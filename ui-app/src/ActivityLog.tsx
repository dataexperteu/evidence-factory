import { useState } from "react";

export type LogEntry = {
  id: number;
  timestamp: string;
  icon: "✓" | "✗" | "ℹ";
  message: string;
};

type Props = {
  entries: LogEntry[];
  onClear: () => void;
};

export function ActivityLog({ entries, onClear }: Props) {
  const [open, setOpen] = useState(false);
  const hasError = entries.some((e) => e.icon === "✗");

  return (
    <div className="activity-log" data-testid="activity-log">
      <button
        className={`activity-log-toggle${hasError ? " has-error" : ""}`}
        data-testid="activity-log-toggle"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        Log
        {entries.length > 0 && (
          <span className="activity-log-badge" data-testid="activity-log-badge">
            {entries.length}
          </span>
        )}
      </button>

      {open && (
        <div className="activity-log-panel" data-testid="activity-log-panel">
          <div className="activity-log-header">
            <span className="activity-log-title">Activity Log</span>
            <div className="activity-log-header-actions">
              <button
                className="activity-log-clear"
                data-testid="activity-log-clear"
                onClick={onClear}
              >
                Clear log
              </button>
              <button
                className="activity-log-close"
                data-testid="activity-log-close"
                aria-label="Close activity log"
                onClick={() => setOpen(false)}
              >
                ✕
              </button>
            </div>
          </div>
          <div className="activity-log-body">
            {entries.length === 0 ? (
              <p className="activity-log-empty" data-testid="activity-log-empty">
                No entries yet.
              </p>
            ) : (
              <ul className="activity-log-list" data-testid="activity-log-list">
                {entries.map((entry) => (
                  <li
                    key={entry.id}
                    className={`log-entry log-entry-${
                      entry.icon === "✓" ? "success" : entry.icon === "✗" ? "error" : "info"
                    }`}
                    data-testid="activity-log-entry"
                  >
                    <span className="log-timestamp">{entry.timestamp}</span>
                    <span className="log-icon" aria-hidden="true">
                      {entry.icon}
                    </span>
                    <span className="log-message">{entry.message}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
