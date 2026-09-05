import { useState } from "react";
import { saveRow } from "../api";
import { getLogin, hasLogin } from "../loginStore.js";
import { useApp } from "../AppContext";
import LoginRequiredModal from "./LoginRequiredModal.jsx";

const ROW_STATE = { IDLE: "idle", SAVING: "saving", OK: "ok", ERROR: "error" };

/**
 * One screen's generation result: header with status, an editable table
 * of the generated rows (the only output surface — there is no export),
 * and a per-row Save-to-APM flow with ✓/✗ status per row.
 *
 * `result` shape (matches ScreenResult in backend/models.py):
 *   { status, message, screen, resolved_path, fields, rows, row_count,
 *     apm_ready, apm_disabled_reason }
 */
export default function ResultPanel({ module, result }) {
  const { bumpLoginVersion } = useApp();
  const [rows, setRows] = useState(result.rows || []);
  const [rowStatus, setRowStatus] = useState({});
  const [savingAll, setSavingAll] = useState(false);
  const [loginModalOpen, setLoginModalOpen] = useState(false);

  if (result.status === "error") {
    return (
      <div className="result-panel">
        <div className="result-header">
          <h2>
            {module}
            {result.screen ? ` / ${result.screen}` : ""}
          </h2>
        </div>
        <div className="status-banner error" style={{ margin: "0 24px 20px" }}>
          {result.message || "Generation failed for this screen."}
        </div>
      </div>
    );
  }

  const fields = result.fields || [];
  const columns = fields.length > 0 ? fields.map((f) => f.field_name) : rows[0] ? Object.keys(rows[0]) : [];

  function statusFor(rowIndex) {
    return rowStatus[rowIndex]?.state || ROW_STATE.IDLE;
  }

  function handleCellChange(rowIndex, col, value) {
    setRows((prev) => {
      const next = [...prev];
      next[rowIndex] = { ...next[rowIndex], [col]: value };
      return next;
    });
  }

  async function saveOneRow(rowIndex) {
    const row = rows[rowIndex];
    setRowStatus((s) => ({ ...s, [rowIndex]: { state: ROW_STATE.SAVING } }));
    try {
      const res = await saveRow(getLogin(), { module, screen: result.screen, row });
      setRowStatus((s) => ({
        ...s,
        [rowIndex]: res.ok ? { state: ROW_STATE.OK } : { state: ROW_STATE.ERROR, error: res.error || "Save failed" },
      }));
    } catch (e) {
      setRowStatus((s) => ({ ...s, [rowIndex]: { state: ROW_STATE.ERROR, error: e.message } }));
    }
  }

  async function handleSaveAll() {
    if (!hasLogin()) {
      setLoginModalOpen(true);
      return;
    }
    setSavingAll(true);
    for (let i = 0; i < rows.length; i++) {
      await saveOneRow(i);
    }
    setSavingAll(false);
  }

  const savedCount = rows.filter((_, i) => statusFor(i) === ROW_STATE.OK).length;
  const failedCount = rows.filter((_, i) => statusFor(i) === ROW_STATE.ERROR).length;

  return (
    <div className="result-panel">
      <div className="result-header">
        <div>
          <h2>
            {module}
            {result.screen ? ` / ${result.screen}` : ""}
          </h2>
          <div className="result-meta">
            {result.row_count} row{result.row_count === 1 ? "" : "s"} generated
            {result.resolved_path ? ` — resolved from ${result.resolved_path}` : ""}
          </div>
          {!result.apm_ready && (
            <div className="result-meta result-meta-warning">
              Save disabled: {result.apm_disabled_reason || "APM schema could not be resolved for this screen."}
            </div>
          )}
        </div>
        <span className="apm-screen-actions">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleSaveAll}
            disabled={savingAll || !result.apm_ready || rows.length === 0}
            title={!result.apm_ready ? result.apm_disabled_reason : undefined}
          >
            {savingAll ? "Saving to APM…" : "Save all to APM"}
          </button>
          {(savedCount > 0 || failedCount > 0) && (
            <span className="apm-sync-summary">
              {savedCount > 0 && <span className="apm-sync-ok">{savedCount} saved</span>}
              {failedCount > 0 && <span className="apm-sync-fail">{failedCount} failed</span>}
            </span>
          )}
        </span>
      </div>

      {rows.length > 0 ? (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th className="apm-status-col">Sync</th>
                {columns.map((col) => (
                  <th key={col}>{col}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => {
                const st = statusFor(rowIndex);
                const entry = rowStatus[rowIndex];
                return (
                  <tr key={rowIndex}>
                    <td className="apm-status-col" title={entry?.error || ""}>
                      {st === ROW_STATE.SAVING && <span className="apm-row-status spinner" />}
                      {st === ROW_STATE.OK && (
                        <button
                          type="button"
                          className="apm-row-status apm-tick"
                          title="Saved"
                          onClick={() => saveOneRow(rowIndex)}
                        >
                          ✓
                        </button>
                      )}
                      {st === ROW_STATE.ERROR && (
                        <button
                          type="button"
                          className="apm-row-status apm-cross"
                          title={entry?.error ? `${entry.error} — click to retry` : "Failed — click to retry"}
                          onClick={() => saveOneRow(rowIndex)}
                        >
                          ✗
                        </button>
                      )}
                      {st === ROW_STATE.IDLE && (
                        <button
                          type="button"
                          className="apm-row-status apm-idle"
                          title={result.apm_ready ? "Save this row" : result.apm_disabled_reason}
                          disabled={!result.apm_ready}
                          onClick={() => (hasLogin() ? saveOneRow(rowIndex) : setLoginModalOpen(true))}
                        >
                          ↑
                        </button>
                      )}
                    </td>
                    {columns.map((col) => {
                      const value = row[col];
                      if (typeof value === "boolean") {
                        return (
                          <td key={col}>
                            <input type="checkbox" checked={value} onChange={(e) => handleCellChange(rowIndex, col, e.target.checked)} />
                          </td>
                        );
                      }
                      return (
                        <td key={col}>
                          <input
                            type="text"
                            value={value === null || value === undefined ? "" : value}
                            onChange={(e) => handleCellChange(rowIndex, col, e.target.value)}
                          />
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div style={{ padding: "16px 24px", color: "var(--color-ink-muted)" }}>No rows returned.</div>
      )}

      {loginModalOpen && (
        <LoginRequiredModal
          onSaved={() => {
            setLoginModalOpen(false);
            bumpLoginVersion();
            handleSaveAll();
          }}
          onCancel={() => setLoginModalOpen(false)}
        />
      )}
    </div>
  );
}
