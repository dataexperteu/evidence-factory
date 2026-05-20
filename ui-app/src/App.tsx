import { useEffect, useRef, useState } from "react";

type StageEvent = {
  stage: string;
  status: "started" | "complete" | "failed";
  detail: Record<string, unknown>;
};

const STAGES = ["intake", "extract", "events", "emit", "close", "package", "done"] as const;

export function App() {
  const [paste, setPaste] = useState("");
  const [attested, setAttested] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [events, setEvents] = useState<StageEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [streamDone, setStreamDone] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);

  const submitting = runId !== null && !streamDone;
  const canSubmit = paste.trim().length > 0 && attested && !submitting;
  const succeeded = streamDone && events.some((e) => e.stage === "done" && e.status === "complete");

  async function onGenerate() {
    setError(null);
    setEvents([]);
    setStreamDone(false);
    try {
      const resp = await fetch("/api/runs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          source_paste: paste,
          attestation_checked: attested,
        }),
      });
      if (!resp.ok) {
        const body = await resp.text();
        setError(`Failed to start run: ${body}`);
        return;
      }
      const { run_id } = (await resp.json()) as { run_id: string };
      setRunId(run_id);
    } catch (e) {
      setError(`Network error: ${String(e)}`);
    }
  }

  useEffect(() => {
    if (!runId) return;
    const es = new EventSource(`/api/runs/${runId}/events`);
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
    return () => {
      es.close();
    };
  }, [runId]);

  return (
    <div className="app">
      <h1>Evidence Factory</h1>
      <p className="subtitle">
        Paste a source story, attest authorised use, and generate a synthetic
        forensic-style corpus zip. Slice 1: email-only artifacts.
      </p>

      <label htmlFor="paste">Source story</label>
      <textarea
        id="paste"
        data-testid="source-paste"
        placeholder="Paste a public-domain story or a case description here…"
        value={paste}
        onChange={(e) => setPaste(e.target.value)}
        disabled={submitting}
      />

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

      <button
        data-testid="generate"
        onClick={onGenerate}
        disabled={!canSubmit}
      >
        {submitting ? "Generating…" : "Generate"}
      </button>

      {error && <div className="error" data-testid="error">{error}</div>}

      {events.length > 0 && (
        <div className="progress" data-testid="progress">
          {events.map((ev, i) => (
            <div className={`stage ${ev.status}`} key={i} data-testid={`stage-${ev.stage}-${ev.status}`}>
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
