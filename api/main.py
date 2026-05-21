"""FastAPI app for Evidence Factory (slice 2: intake modes).

Endpoints:
- POST /api/runs               start a run from paste or URL (JSON body)
- POST /api/runs/upload        start a run from a file upload (multipart)
- POST /api/preview-url        fetch a URL and return a one-line text preview
- GET  /api/runs/{id}/events   Server-Sent Events progress stream
- GET  /api/runs/{id}/zip      download the finished corpus zip
- GET  /healthz                liveness
- GET  /                       serve the built SPA (if ui-app/dist exists)

Jobs are held in process memory — fine for a single-tenant local tool.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .pipeline.attestation import AttestationRequired
from .pipeline.llm_gateway import LLMGateway
from .pipeline.orchestrator import (
    ProgressEvent,
    RunResult,
    RunSettings,
    run_pipeline,
)
from .pipeline.source_intake import IntakeError, ingest_upload, ingest_url

LOG = logging.getLogger("evidence_factory.api")
logging.basicConfig(level=logging.INFO)

UI_DIST = Path(__file__).resolve().parent.parent / "ui-app" / "dist"


class StartRunRequest(BaseModel):
    source_paste: str | None = None
    source_url: str | None = None
    attestation_checked: bool
    operator_label: str | None = None


class PreviewUrlRequest(BaseModel):
    url: str


@dataclass
class _RunState:
    events: list[ProgressEvent] = field(default_factory=list)
    result: RunResult | None = None
    completed: asyncio.Event = field(default_factory=asyncio.Event)
    failed: bool = False
    failure_reason: str | None = None


class _JobStore:
    def __init__(self) -> None:
        self._runs: dict[str, _RunState] = {}
        self._lock = asyncio.Lock()

    async def create(self, run_id: str) -> _RunState:
        async with self._lock:
            state = _RunState()
            self._runs[run_id] = state
            return state

    def get(self, run_id: str) -> _RunState | None:
        return self._runs.get(run_id)


def _make_drive(state: _RunState, source_text: str, attestation_checked: bool) -> None:
    settings = RunSettings()
    gateway = LLMGateway()

    async def drive() -> None:
        try:
            async for event, maybe_result in run_pipeline(
                source_text,
                attestation_checked,
                settings=settings,
                gateway=gateway,
            ):
                state.events.append(event)
                if maybe_result is not None:
                    state.result = maybe_result
                if event.status == "failed":
                    state.failed = True
                    state.failure_reason = json.dumps(event.detail)
        except AttestationRequired as e:
            state.failed = True
            state.failure_reason = str(e)
        except Exception as e:  # pragma: no cover - defensive
            LOG.exception("pipeline crashed")
            state.failed = True
            state.failure_reason = f"internal error: {e}"
        finally:
            state.completed.set()

    asyncio.create_task(drive())


def create_app() -> FastAPI:
    app = FastAPI(title="Evidence Factory")
    store = _JobStore()

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.post("/api/preview-url")
    async def preview_url(req: PreviewUrlRequest) -> dict:
        """Fetch a URL and return the first line of extracted text as a preview."""
        try:
            source = await ingest_url(req.url)
        except IntakeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        first_line = source.body.split("\n", 1)[0][:300]
        return {"preview": first_line, "char_count": source.char_count}

    @app.post("/api/runs")
    async def start_run(req: StartRunRequest) -> dict:
        if req.source_paste and req.source_url:
            raise HTTPException(
                status_code=400, detail="supply either source_paste or source_url, not both"
            )
        if not req.source_paste and not req.source_url:
            raise HTTPException(status_code=400, detail="source_paste or source_url is required")

        if req.source_url:
            try:
                source = await ingest_url(req.source_url)
            except IntakeError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            text = source.body
        else:
            text = req.source_paste or ""
            if not text.strip():
                raise HTTPException(status_code=400, detail="source_paste is empty")

        run_id = _gen_run_id()
        state = await store.create(run_id)
        _make_drive(state, text, req.attestation_checked)
        return {"run_id": run_id}

    @app.post("/api/runs/upload")
    async def start_run_upload(
        file: UploadFile,
        attestation_checked: bool = Form(...),
        operator_label: str | None = Form(None),
    ) -> dict:
        content = await file.read()
        filename = file.filename or "upload.txt"
        try:
            source = ingest_upload(content, filename)
        except IntakeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        run_id = _gen_run_id()
        state = await store.create(run_id)
        _make_drive(state, source.body, attestation_checked)
        return {"run_id": run_id}

    @app.get("/api/runs/{run_id}/events")
    async def stream_events(run_id: str) -> EventSourceResponse:
        state = store.get(run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="unknown run")

        async def gen() -> AsyncIterator[dict]:
            sent = 0
            while True:
                while sent < len(state.events):
                    ev = state.events[sent]
                    sent += 1
                    yield {"event": ev.stage, "data": json.dumps(ev.to_dict())}
                if state.completed.is_set():
                    break
                await asyncio.sleep(0.05)
            yield {"event": "end", "data": json.dumps({"failed": state.failed})}

        return EventSourceResponse(gen())

    @app.get("/api/runs/{run_id}/zip")
    async def download_zip(run_id: str) -> Response:
        state = store.get(run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="unknown run")
        await state.completed.wait()
        if state.failed or state.result is None:
            raise HTTPException(
                status_code=409,
                detail=state.failure_reason or "run did not produce a corpus",
            )
        return Response(
            content=state.result.zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="evidence-factory-{run_id}.zip"',
            },
        )

    if UI_DIST.exists():
        app.mount("/assets", StaticFiles(directory=str(UI_DIST / "assets")), name="assets")

        @app.get("/")
        async def root() -> FileResponse:
            return FileResponse(UI_DIST / "index.html")

    return app


def _gen_run_id() -> str:
    import secrets

    return secrets.token_hex(8)


app = create_app()
