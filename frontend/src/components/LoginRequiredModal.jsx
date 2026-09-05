import { useState } from "react";
import { setLogin } from "../loginStore.js";

export default function LoginRequiredModal({ onSaved, onCancel }) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState(null);

  function handleSave() {
    try {
      JSON.parse(draft);
    } catch {
      setError("That doesn't look like valid JSON. Paste the full Login object exactly as copied.");
      return;
    }
    setLogin(draft);
    onSaved();
  }

  return (
    <div className="modal-overlay" onMouseDown={onCancel}>
      <div className="modal-box" onMouseDown={(e) => e.stopPropagation()}>
        <p className="card-section-title">Login required</p>
        <p className="field-hint" style={{ marginTop: 0 }}>
          No APM Login is configured yet. Paste it below to save this row — it'll be
          remembered for every save after this.
        </p>
        <textarea
          className="apm-login-textarea"
          placeholder='{"UserCode": "E573", "DatabaseName": "YourDatabase", ...}'
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={7}
          autoFocus
        />
        {error && <div className="error-banner">{error}</div>}
        <div className="actions-row">
          <button type="button" className="btn-primary" onClick={handleSave}>
            Save &amp; Continue
          </button>
          <button type="button" className="btn-secondary" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
