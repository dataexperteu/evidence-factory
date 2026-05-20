import { useEffect, useRef, useState } from "react";

type IntakeMode = "paste" | "url" | "upload";

type StageEvent = {
  stage: string;
  status: "started" | "complete" | "failed";
  detail: Record<string, unknown>;
};

const STAGES = ["intake", "extract", "events", "emit", "close", "package", "done"] as const;

export function App() {
  const [mode, setMode] = useState<IntakeMode>("paste");

  // Paste mode state
  const [paste, setPaste] = useState("");

  // URL mode state
  const [url, setUrl] = useState("");
  const [urlPreview, setUrlPreview] = useState<string | null>(null);
  const [urlPreviewLoading, setUrlPreviewLoading] = useState(false);
  const [urlPreviewError, setUrlPreviewError] = useState<string | null>(null);

  // Upload mode state
  const [uploadFile, setUploadFile] = useState<File | null>(null);

  // Shared state
  const [attested, setAttested] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [events, setEvents] = useState<StageEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [streamDone, setStreamDone] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);

  const submitting = runId !== null && !streamDone;
  const succeeded =
    streamDone && events.some((e) => e.stage === "done" && e.status === "complete");

  const modeReady =
    (mode === "paste" && paste.trim().length > 0) ||
    (mode === "url" && urlPreview !== null) ||
    (mode === "upload" && uploadFile !== null);

  const canSubmit = modeReady && attested && !submitting;

  function resetRun() {
    setRunId(null);
    setEvents([]);
    setError(null);
    setStreamDone(false);
    sourceRef.current?.close();
    sourceRef.current = null;
  }

  function switchMode(next: IntakeMode) {
    resetRun();
    setMode(next);
  }

  async function fetchUrlPreview() {
    if (!url.trim()) return;
    setUrlPreviewLoading(true);
    setUrlPreviewError(null);
    setUrlPreview(null);
    try {
      const resp = await fetch("/api/preview-url", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ url: url.trim() }),
      });
      if (!resp.ok) {
        const body = await resp.text();
        setUrlPreviewError(`Could not fetch URL: ${body}`);
        return;
      }
      const data = (await resp.json()) as { preview: string; char_count: number };
      setUrlPreview(data.preview);
    } catch (e) {
      setUrlPreviewError(`Network error: ${String(e)}`);
    } finally {
      setUrlPreviewLoading(false);
    }
  }

  function startSse(id: string) {
    const es = new EventSource(`/api/runs/${id}/events`);
    sourceRef.current = es;
    const handleStage = (ev: MessageEvent) => {
      try {
        const parsed = JSON.parse(ev.data) as StageEvent;
        setEvents((prev) => [...prev, parsed]);
      } catch {
        /* ignore malformed frames */
      }
    };
    STAGES.forEach((stage) => es.addEventListener(stage, handleStage));
    es.addEventListener("end", () => {
      setStreamDone(true);
      es.close();
    });
    es.onerror = () => {
      setStreamDone(true);
      es.close();
    };
  }

  async function onGenerate() {
    resetRun();
    setError(null);

    let resp: Response;
    try {
      if (mode === "paste") {
        resp = await fetch("/api/runs", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ source_paste: paste, attestation_checked: attested }),
        });
      } else if (mode === "url") {
        resp = await fetch("/api/runs", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ source_url: url.trim(), attestation_checked: attested }),
        });
      } else {
        const fd = new FormData();
        fd.append("file", uploadFile!);
        fd.append("attestation_checked", String(attested));
        resp = await fetch("/api/runs/upload", { method: "POST", body: fd });
      }
    } catch (e) {
      setError(`Network error: ${String(e)}`);
      return;
    }

    if (!resp.ok) {
      const body = await resp.text();
      setError(`Failed to start run: ${body}`);
      return;
    }

    const { run_id } = (await resp.json()) as { run_id: string };
    setRunId(run_id);
    startSse(run_id);
  }

  useEffect(() => {
    return () => {
      sourceRef.current?.close();
    };
  }, []);

  return (
    <div className="app">
      <h1>Evidence Factory</h1>
      <p className="subtitle">
        Provide a source story, attest authorised use, and generate a synthetic
        forensic-style corpus zip.
      </p>

      {/* Mode selector */}
      <div className="mode-tabs" role="tablist" aria-label="Intake mode">
        {(["paste", "url", "upload"] as IntakeMode[]).map((m) => (
          <button
            key={m}
            role="tab"
            aria-selected={mode === m}
            data-testid={`tab-${m}`}
            className={`tab${mode === m ? " active" : ""}`}
            onClick={() => switchMode(m)}
            disabled={submitting}
          >
            {m === "paste" ? "Paste" : m === "url" ? "URL" : "Upload"}
          </button>
        ))}
      </div>

      {/* Paste panel */}
      {mode === "paste" && (
        <div data-testid="panel-paste">
          <label htmlFor="paste">Source story</label>
          <textarea
            id="paste"
            data-testid="source-paste"
            placeholder="Paste a public-domain story or a case description here…"
            value={paste}
            onChange={(e) => setPaste(e.target.value)}
            disabled={submitting}
          />
        </div>
      )}

      {/* URL panel */}
      {mode === "url" && (
        <div data-testid="panel-url">
          <label htmlFor="source-url">Article URL (http/https)</label>
          <div className="row url-row">
            <input
              id="source-url"
              data-testid="source-url"
              type="url"
              placeholder="https://example.com/article"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                setUrlPreview(null);
                setUrlPreviewError(null);
              }}
              disabled={submitting}
            />
            <button
              data-testid="fetch-preview"
              onClick={fetchUrlPreview}
              disabled={!url.trim() || urlPreviewLoading || submitting}
            >
              {urlPreviewLoading ? "Fetching…" : "Preview"}
            </button>
          </div>
          {urlPreviewError && (
            <div className="error" data-testid="url-preview-error">
              {urlPreviewError}
            </div>
          )}
          {urlPreview !== null && (
            <div className="url-preview" data-testid="url-preview">
              <strong>Preview:</strong> {urlPreview}
            </div>
          )}
        </div>
      )}

      {/* Upload panel */}
      {mode === "upload" && (
        <div data-testid="panel-upload">
          <label htmlFor="file-upload">Upload source file (.txt or .md)</label>
          <input
            id="file-upload"
            data-testid="file-upload"
            type="file"
            accept=".txt,.md"
            disabled={submitting}
            onChange={(e) => {
              const f = e.target.files?.[0] ?? null;
              setUploadFile(f);
            }}
          />
          {uploadFile && (
            <div className="upload-info" data-testid="upload-info">
              {uploadFile.name} ({uploadFile.size.toLocaleString()} bytes)
            </div>
          )}
        </div>
      )}

      {/* Attestation + Generate */}
      <div className="row">
        <input
          id="attest"
          data-testid="attestation"
          type="checkbox"
          checked={attested}
          onChange={(e) => setAttested(e.target.checked)}
          disabled={submitting}
        />
        <label htmlFor="attest">
          I attest that I am authorised to use this source for synthetic-evidence generation.
        </label>
      </div>

      <button data-testid="generate" onClick={onGenerate} disabled={!canSubmit}>
        {submitting ? "Generating…" : "Generate"}
      </button>

      {error && (
        <div className="error" data-testid="error">
          {error}
        </div>
      )}

      {events.length > 0 && (
        <div className="progress" data-testid="progress">
          {events.map((ev, i) => (
            <div
              className={`stage ${ev.status}`}
              key={i}
              data-testid={`stage-${ev.stage}-${ev.status}`}
            >
              <span>{ev.stage}</span>
              <span>{ev.status}</span>
            </div>
          ))}
        </div>
      )}

      {succeeded && runId && (
        <a
          className="download"
          data-testid="download"
          href={`/api/runs/${runId}/zip`}
          download={`evidence-factory-${runId}.zip`}
        >
          Download corpus zip
        </a>
      )}
    </div>
  );
}
