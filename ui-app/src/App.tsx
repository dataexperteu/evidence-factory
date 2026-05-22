import { useEffect, useRef, useState } from "react";

type IntakeMode = "paste" | "url" | "upload";
type DifficultyPreset = "easy" | "medium" | "hard";

type Dials = {
  owners_per_proposition: number;
  min_owner_distinct: number;
  fragmentation_factor: number;
  dominance_margin: number;
  target_artifact_count: number;
  red_herring_count: number;
  noise_count: number;
};

const PRESET_DIALS: Record<DifficultyPreset, Dials> = {
  easy: {
    owners_per_proposition: 2,
    min_owner_distinct: 2,
    fragmentation_factor: 2,
    dominance_margin: 0.5,
    target_artifact_count: 100,
    red_herring_count: 1,
    noise_count: 80,
  },
  medium: {
    owners_per_proposition: 3,
    min_owner_distinct: 3,
    fragmentation_factor: 3,
    dominance_margin: 0.3,
    target_artifact_count: 400,
    red_herring_count: 3,
    noise_count: 350,
  },
  hard: {
    owners_per_proposition: 5,
    min_owner_distinct: 5,
    fragmentation_factor: 5,
    dominance_margin: 0.15,
    target_artifact_count: 1000,
    red_herring_count: 6,
    noise_count: 950,
  },
};

function dialMatchesPreset(dials: Dials): DifficultyPreset | null {
  for (const [preset, values] of Object.entries(PRESET_DIALS) as [DifficultyPreset, Dials][]) {
    if (
      dials.owners_per_proposition === values.owners_per_proposition &&
      dials.min_owner_distinct === values.min_owner_distinct &&
      dials.fragmentation_factor === values.fragmentation_factor &&
      Math.abs(dials.dominance_margin - values.dominance_margin) < 1e-9 &&
      dials.target_artifact_count === values.target_artifact_count &&
      dials.red_herring_count === values.red_herring_count &&
      dials.noise_count === values.noise_count
    ) {
      return preset;
    }
  }
  return null;
}

type StageEvent = {
  stage: string;
  status: "started" | "complete" | "failed";
  detail: Record<string, unknown>;
};

type ClosureGap = {
  proposition_id: string;
  required: number;
  observed: number;
  distinct_owners: string[];
};

type RedHerringGap = {
  proposition_id: string;
  support_signal: number;
  breaker_signal: number;
  required_breaker_signal: number;
  reason: string;
};

type DominanceGap = {
  alternative_id: string;
  truth_support: number;
  alternative_support: number;
  required_max_support: number;
  margin_shortfall: number;
  reason: string;
};

type CloseDetail = {
  gaps?: ClosureGap[];
  red_herring_gaps?: RedHerringGap[];
  dominance_gaps?: DominanceGap[];
};

const STAGES = ["intake", "extract", "events", "emit", "critique", "close", "package", "done"] as const;

function FailureDetail({ ev }: { ev: StageEvent }) {
  if (ev.stage === "close") {
    const d = ev.detail as CloseDetail;
    const items: string[] = [];
    for (const g of d.gaps ?? []) {
      items.push(
        `Proposition ${g.proposition_id}: needs ${g.required} corroborating owner(s), found ${g.observed}`,
      );
    }
    for (const g of d.red_herring_gaps ?? []) {
      items.push(`Red-herring ${g.proposition_id}: ${g.reason}`);
    }
    for (const g of d.dominance_gaps ?? []) {
      items.push(`Dominance gap for ${g.alternative_id}: ${g.reason}`);
    }
    if (items.length === 0) {
      return (
        <div className="stage-detail" data-testid="failure-detail">
          Closure verification failed.
        </div>
      );
    }
    return (
      <ul className="stage-detail" data-testid="failure-detail">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    );
  }
  const msg = (ev.detail as { error?: string }).error ?? "Stage failed.";
  return (
    <div className="stage-detail" data-testid="failure-detail">
      {msg}
    </div>
  );
}

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

  // Difficulty dials (default = medium preset)
  const [dials, setDials] = useState<Dials>({ ...PRESET_DIALS.medium });

  // Shared state
  const [attested, setAttested] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [events, setEvents] = useState<StageEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [streamDone, setStreamDone] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);
  const failedStageRef = useRef<boolean>(false);

  const submitting = runId !== null && !streamDone;
  const succeeded =
    streamDone && events.some((e) => e.stage === "done" && e.status === "complete");

  const failedEvent = events.find((e) => e.status === "failed") ?? null;
  const hasFailed = streamDone && failedEvent !== null;

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
    failedStageRef.current = false;
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
        if (parsed.status === "failed") {
          failedStageRef.current = true;
        }
      } catch {
        /* ignore malformed frames */
      }
    };
    STAGES.forEach((stage) => es.addEventListener(stage, handleStage));
    es.addEventListener("end", (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data) as { failed?: boolean; failure_reason?: string };
        if (data.failed && data.failure_reason && !failedStageRef.current) {
          setError(data.failure_reason);
        }
      } catch {
        /* ignore */
      }
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

    const activePreset = dialMatchesPreset(dials);

    let resp: Response;
    try {
      if (mode === "paste") {
        resp = await fetch("/api/runs", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            source_paste: paste,
            attestation_checked: attested,
            difficulty: activePreset ?? "medium",
            ...dials,
          }),
        });
      } else if (mode === "url") {
        resp = await fetch("/api/runs", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            source_url: url.trim(),
            attestation_checked: attested,
            difficulty: activePreset ?? "medium",
            ...dials,
          }),
        });
      } else {
        const fd = new FormData();
        fd.append("file", uploadFile!);
        fd.append("attestation_checked", String(attested));
        fd.append("difficulty", activePreset ?? "medium");
        fd.append("owners_per_proposition", String(dials.owners_per_proposition));
        fd.append("min_owner_distinct", String(dials.min_owner_distinct));
        fd.append("fragmentation_factor", String(dials.fragmentation_factor));
        fd.append("dominance_margin", String(dials.dominance_margin));
        fd.append("target_artifact_count", String(dials.target_artifact_count));
        fd.append("red_herring_count", String(dials.red_herring_count));
        fd.append("noise_count", String(dials.noise_count));
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

      {/* Difficulty preset selector */}
      <div className="difficulty-section">
        <div className="preset-selector" role="group" aria-label="Difficulty preset">
          {(["easy", "medium", "hard"] as DifficultyPreset[]).map((preset) => {
            const active = dialMatchesPreset(dials) === preset;
            return (
              <button
                key={preset}
                data-testid={`preset-${preset}`}
                className={`preset-btn${active ? " active" : ""}`}
                aria-pressed={active}
                disabled={submitting}
                onClick={() => setDials({ ...PRESET_DIALS[preset] })}
              >
                {preset.charAt(0).toUpperCase() + preset.slice(1)}
              </button>
            );
          })}
          {dialMatchesPreset(dials) === null && (
            <span className="preset-custom" data-testid="preset-custom">
              Custom
            </span>
          )}
        </div>

        <details data-testid="advanced-dials">
          <summary data-testid="advanced-dials-summary">Advanced settings</summary>
          <div className="dials-grid">
            <label>
              Corroborators per proposition
              <input
                type="number"
                data-testid="dial-owners-per-proposition"
                min={1}
                max={6}
                value={dials.owners_per_proposition}
                disabled={submitting}
                onChange={(e) =>
                  setDials((d) => ({ ...d, owners_per_proposition: Number(e.target.value) }))
                }
              />
            </label>
            <label>
              Closure lower bound
              <input
                type="number"
                data-testid="dial-min-owner-distinct"
                min={1}
                max={6}
                value={dials.min_owner_distinct}
                disabled={submitting}
                onChange={(e) =>
                  setDials((d) => ({ ...d, min_owner_distinct: Number(e.target.value) }))
                }
              />
            </label>
            <label>
              Fragmentation factor
              <input
                type="number"
                data-testid="dial-fragmentation-factor"
                min={1}
                max={10}
                value={dials.fragmentation_factor}
                disabled={submitting}
                onChange={(e) =>
                  setDials((d) => ({ ...d, fragmentation_factor: Number(e.target.value) }))
                }
              />
            </label>
            <label>
              Dominance margin
              <input
                type="number"
                data-testid="dial-dominance-margin"
                min={0.05}
                max={1.0}
                step={0.05}
                value={dials.dominance_margin}
                disabled={submitting}
                onChange={(e) =>
                  setDials((d) => ({ ...d, dominance_margin: Number(e.target.value) }))
                }
              />
            </label>
            <label>
              Target artifact count
              <input
                type="number"
                data-testid="dial-target-artifact-count"
                min={10}
                max={2000}
                value={dials.target_artifact_count}
                disabled={submitting}
                onChange={(e) =>
                  setDials((d) => ({ ...d, target_artifact_count: Number(e.target.value) }))
                }
              />
            </label>
            <label>
              Red-herring count
              <input
                type="number"
                data-testid="dial-red-herring-count"
                min={0}
                max={20}
                value={dials.red_herring_count}
                disabled={submitting}
                onChange={(e) =>
                  setDials((d) => ({ ...d, red_herring_count: Number(e.target.value) }))
                }
              />
            </label>
            <label>
              Noise count
              <input
                type="number"
                data-testid="dial-noise-count"
                min={0}
                max={2000}
                value={dials.noise_count}
                disabled={submitting}
                onChange={(e) =>
                  setDials((d) => ({ ...d, noise_count: Number(e.target.value) }))
                }
              />
            </label>
          </div>
        </details>
      </div>

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
            <div key={i}>
              <div
                className={`stage ${ev.status}`}
                data-testid={`stage-${ev.stage}-${ev.status}`}
              >
                <span>{ev.stage}</span>
                <span>{ev.status}</span>
              </div>
              {ev.status === "failed" && <FailureDetail ev={ev} />}
            </div>
          ))}
        </div>
      )}

      {hasFailed && (
        <div className="failure-recovery" data-testid="failure-recovery">
          <p className="failure-hint">
            Tip: reduce difficulty or try again — transient errors sometimes resolve on retry.
          </p>
          <button data-testid="retry" onClick={onGenerate} disabled={submitting}>
            Retry
          </button>
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
