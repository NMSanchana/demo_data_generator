import { useState } from "react";
import { getLogin, setLogin, clearLogin, loginSummary } from "../loginStore.js";

export default function LoginPanel({ onChange }) {
  const [draft, setDraft] = useState(getLogin());
  const [error, setError] = useState(null);
  const summary = loginSummary();

  function handleSave() {
    try {
      JSON.parse(draft);
    } catch {
      setError("That doesn't look like valid JSON. Paste the full Login object exactly as copied.");
      return;
    }
    setLogin(draft);
    setError(null);
    onChange?.();
  }

  function handleClear() {
    clearLogin();
    setDraft("");
    setError(null);
    onChange?.();
  }

  return (
    <div className="login-panel">
      {summary ? (
        <p className="apm-status apm-status-ok">
          <span className="apm-status-dot apm-status-dot-ok" />
          Login configured{summary.userName ? ` — ${summary.userName}` : ""}
          {summary.ouName ? ` (${summary.ouName})` : ""}
        </p>
      ) : (
        <p className="apm-status apm-status-missing">
          <span className="apm-status-dot apm-status-dot-missing" />
          Login not set — required before saving to APM
        </p>
      )}

      <p className="field-hint" style={{ marginTop: 0 }}>
        Paste the full <code>Login</code> JSON object here once. It's stored only in this
        browser and sent as the <code>Login</code> header on every save.
      </p>
      <textarea
        className="apm-login-textarea"
        placeholder='{"UserCode": "E573", "DatabaseName": "YourDatabase", ...}'
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={7}
      />
      {error && <div className="error-banner">{error}</div>}
      <div className="actions-row">
        <button type="button" className="btn-primary" onClick={handleSave}>
          Save Login
        </button>
        {summary && (
          <button type="button" className="btn-secondary" onClick={handleClear}>
            Clear
          </button>
        )}
      </div>
    </div>
  );
}
