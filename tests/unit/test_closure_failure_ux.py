"""Unit tests for slice-15: closure failure UX.

Verifies:
- _RunState.failure_reason is None by default.
- When a stage emits status="failed", failure_reason is set to the JSON-serialised detail.
- When attestation is refused, failure_reason is set to the error string.
- The SSE end event includes failure_reason in its payload.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest

from api.main import _RunState, create_app
from api.pipeline.attestation import AttestationRequired
from api.pipeline.orchestrator import ProgressEvent, RunResult


# ---------------------------------------------------------------------------
# _RunState defaults
# ---------------------------------------------------------------------------


def test_runstate_failure_reason_defaults_to_none() -> None:
    state = _RunState()
    assert state.failure_reason is None
    assert not state.failed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fake_pipeline_fails(
    source_text: str,
    attestation_checked: bool,
    *,
    settings: Any = None,
    gateway: Any = None,
) -> AsyncIterator[tuple[ProgressEvent, RunResult | None]]:
    yield ProgressEvent(stage="close", status="failed", detail={"error": "rate limit"}), None


async def _fake_pipeline_attestation_error(
    source_text: str,
    attestation_checked: bool,
    *,
    settings: Any = None,
    gateway: Any = None,
) -> AsyncIterator[tuple[ProgressEvent, RunResult | None]]:
    raise AttestationRequired("attestation not checked")
    yield  # make it an async generator


# ---------------------------------------------------------------------------
# _make_drive: failure_reason on stage failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_reason_set_on_stage_failure() -> None:
    from api.main import _make_drive

    state = _RunState()

    with patch("api.main.run_pipeline", side_effect=_fake_pipeline_fails):
        with patch("api.main.LLMGateway"):
            _make_drive(state, "text", attestation_checked=True)
            await asyncio.wait_for(state.completed.wait(), timeout=5.0)

    assert state.failed
    assert state.failure_reason is not None
    parsed = json.loads(state.failure_reason)
    assert parsed["error"] == "rate limit"


@pytest.mark.asyncio
async def test_failure_reason_set_on_attestation_error() -> None:
    from api.main import _make_drive

    state = _RunState()

    with patch("api.main.run_pipeline", side_effect=_fake_pipeline_attestation_error):
        with patch("api.main.LLMGateway"):
            _make_drive(state, "text", attestation_checked=False)
            await asyncio.wait_for(state.completed.wait(), timeout=5.0)

    assert state.failed
    assert state.failure_reason is not None
    assert "attestation" in state.failure_reason.lower()


# ---------------------------------------------------------------------------
# SSE end event: failure_reason is included in the payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_end_event_includes_failure_reason() -> None:
    """The end event data must contain failure_reason when a stage fails."""
    from httpx import AsyncClient, ASGITransport

    app = create_app()

    async def fake_post_run() -> dict:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/runs",
                json={"source_paste": "story text", "attestation_checked": True},
            )
            assert resp.status_code == 200
            return resp.json()

    with patch("api.main.run_pipeline", side_effect=_fake_pipeline_fails):
        with patch("api.main.LLMGateway"):
            run_data = await fake_post_run()
            run_id = run_data["run_id"]

            # Wait for the background drive task to finish.
            app_store = app.state  # not directly accessible; use a small poll
            await asyncio.sleep(0.2)

            end_payload: dict | None = None
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                async with client.stream("GET", f"/api/runs/{run_id}/events") as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            raw = line[len("data:"):].strip()
                            try:
                                data = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            # The end event contains "failed" key
                            if "failed" in data and "failure_reason" in data:
                                end_payload = data
                                break

    assert end_payload is not None, "end event was not received"
    assert end_payload["failed"] is True
    assert end_payload["failure_reason"] is not None
