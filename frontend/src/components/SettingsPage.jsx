import { useState } from "react";
import { getSettings, saveSettings } from "../api";

const initialFlags = {
  use_domain: true,
  use_subdomain: true,
  use_geography: true,
};

export default function SettingsPage() {
  const [module, setModule] = useState("");
  const [screen, setScreen] = useState("");
  const [flags, setFlags] = useState(initialFlags);

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState(null); // { type: "success" | "error" | "info", text }

  const toggleFlag = (key) =>
    setFlags((prev) => ({ ...prev, [key]: !prev[key] }));

  async function handleLoad() {
    if (!module.trim()) {
      setBanner({ type: "error", text: "Module is required to load settings." });
      return;
    }
    setLoading(true);
    setBanner(null);
    try {
      const result = await getSettings(module.trim(), screen.trim() || null);
      setFlags({
        use_domain: result.use_domain,
        use_subdomain: result.use_subdomain,
        use_geography: result.use_geography,
      });
      setBanner({
        type: "info",
        text: result.is_default
          ? "No settings saved yet for this module/screen — showing all-enabled defaults."
          : result.screen
          ? `Loaded screen-specific settings for "${result.screen}".`
          : "Loaded the module-level default settings.",
      });
    } catch (err) {
      setBanner({ type: "error", text: err.message });
    } finally {
      setLoading(false);
    }
  }

  async function handleSave() {
    if (!module.trim()) {
      setBanner({ type: "error", text: "Module is required to save settings." });
      return;
    }
    setSaving(true);
    setBanner(null);
    try {
      const result = await saveSettings({
        module: module.trim(),
        screen: screen.trim() || null,
        ...flags,
      });
      setBanner({
        type: "success",
        text: result.screen
          ? `Saved settings for "${result.module}" / "${result.screen}".`
          : `Saved the module-level default for "${result.module}" — applies to every screen unless a screen-specific override is saved.`,
      });
    } catch (err) {
      setBanner({ type: "error", text: err.message });
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <div className="page-header">
        <h1>Settings</h1>
        <p>
          One-time, module-based settings. Pick which components a screen's
          demo data should use. Leave screen blank to set the default for
          every screen under a module — a screen-specific save later
          overrides just that screen.
        </p>
      </div>

      <div className="panel">
        <div className="field-row">
          <div className="field">
            <label htmlFor="settings-module">Module</label>
            <input
              id="settings-module"
              type="text"
              placeholder="e.g. Skill Management"
              value={module}
              onChange={(e) => setModule(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="settings-screen">Screen (optional)</label>
            <input
              id="settings-screen"
              type="text"
              placeholder="e.g. Skill Domain"
              value={screen}
              onChange={(e) => setScreen(e.target.value)}
            />
            <span className="hint">Blank = module-level default</span>
          </div>
        </div>

        <div className="checkbox-grid">
          <SettingRow
            tint="green"
            icon={
              <svg viewBox="0 0 20 20" fill="none">
                <path
                  d="M4 16V9M10 16V4M16 16v-6"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                />
              </svg>
            }
            label="Domain"
            desc="Use the business domain (e.g. Agriculture & Farming) as generation context."
            checked={flags.use_domain}
            onChange={() => toggleFlag("use_domain")}
          />
          <SettingRow
            tint="pink"
            icon={
              <svg viewBox="0 0 20 20" fill="none">
                <circle cx="10" cy="4.5" r="2" stroke="currentColor" strokeWidth="1.6" />
                <circle cx="5" cy="15" r="2" stroke="currentColor" strokeWidth="1.6" />
                <circle cx="15" cy="15" r="2" stroke="currentColor" strokeWidth="1.6" />
                <path
                  d="M10 6.5v3M10 9.5 5 13M10 9.5l5 3.5"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                />
              </svg>
            }
            label="Subdomain"
            desc="Use the optional subdomain as generation context, when supplied."
            checked={flags.use_subdomain}
            onChange={() => toggleFlag("use_subdomain")}
          />
          <SettingRow
            tint="orange"
            icon={
              <svg viewBox="0 0 20 20" fill="none">
                <path
                  d="M10 18s6-5.2 6-9.6A6 6 0 1 0 4 8.4C4 12.8 10 18 10 18Z"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinejoin="round"
                />
                <circle cx="10" cy="8.2" r="2" stroke="currentColor" strokeWidth="1.6" />
              </svg>
            }
            label="Geography"
            desc="Use region and location as generation context."
            checked={flags.use_geography}
            onChange={() => toggleFlag("use_geography")}
          />
        </div>

        <div className="button-row">
          <button className="btn btn-secondary" onClick={handleLoad} disabled={loading}>
            {loading ? "Loading…" : "Load current settings"}
          </button>
          <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save settings"}
          </button>
        </div>

        {banner && <div className={`status-banner ${banner.type}`}>{banner.text}</div>}
      </div>
    </>
  );
}

function SettingRow({ tint, icon, label, desc, checked, onChange }) {
  return (
    <label className={`setting-row setting-row-${tint}`}>
      <span className="setting-row-icon">{icon}</span>
      <span className="setting-row-text">
        <span className="setting-row-label">{label}</span>
        <span className="setting-row-desc">{desc}</span>
      </span>
      <span className="toggle-switch">
        <input type="checkbox" checked={checked} onChange={onChange} />
        <span className="toggle-track">
          <span className="toggle-thumb" />
        </span>
      </span>
    </label>
  );
}