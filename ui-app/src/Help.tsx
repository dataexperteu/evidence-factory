import { useState } from "react";

export function Help() {
  const [visible, setVisible] = useState(
    () => localStorage.getItem("help-dismissed") !== "true"
  );

  if (!visible) return null;

  function dismiss() {
    setVisible(false);
    localStorage.setItem("help-dismissed", "true");
  }

  return (
    <div data-testid="help-panel" className="help-banner">
      <button
        data-testid="help-dismiss"
        className="help-dismiss-btn"
        aria-label="Dismiss help"
        onClick={dismiss}
      >
        ×
      </button>
      <div className="help-content">
        <p>
          Evidence Factory generates a <strong>synthetic forensic evidence corpus</strong> from a
          narrative story. It produces realistic-looking artifacts (documents, messages, media, logs)
          that together support a true account while embedding red herrings, noise, and controlled
          difficulty parameters — useful for training investigators or testing forensic analysis tools.
        </p>
        <p>
          <strong>Input:</strong> Provide a story or narrative (paste plain text, supply a URL, or
          upload a <code>.txt</code>/<code>.md</code> file). Choose a{" "}
          <strong>difficulty preset</strong> (Easy / Medium / Hard) to control corpus size, red
          herring count, and evidence corroboration. Advanced dials let you fine-tune corroborators,
          fragmentation factor, dominance margin, artifact count, red herring count, and noise count.
        </p>
        <p>
          <strong>Output</strong> — a downloadable <code>corpus.zip</code> containing:
        </p>
        <ul>
          <li>Per-artifact files in native formats (PDF, XLSX, JPEG, SMS thread JSON, log file, etc.)</li>
          <li>
            A <code>MANIFEST.txt</code> with a synthetic-evidence disclaimer
          </li>
          <li>Provenance metadata embedded in each artifact</li>
        </ul>
      </div>
    </div>
  );
}
