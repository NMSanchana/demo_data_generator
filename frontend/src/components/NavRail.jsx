import { useState } from "react";
import LoginPanel from "./LoginPanel.jsx";
import { loginSummary } from "../loginStore.js";

export default function NavRail({ page, onNavigate, loginVersion, onLoginChange }) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const summary = loginSummary(); // eslint-disable-line no-unused-vars -- re-evaluated on loginVersion change via key below

  return (
    <>
      <nav className="nav-rail">
        <div className="nav-brand">
          Demo Data Generator
          <span>Domain &amp; geography aware, internal tool</span>
        </div>

        <button
          className={`nav-item ${page === "settings" ? "active" : ""}`}
          onClick={() => onNavigate("settings")}
        >
          <svg className="nav-icon" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="2.4" stroke="currentColor" strokeWidth="1.4" />
            <path
              d="M8 1.6v1.6M8 12.8v1.6M14.4 8h-1.6M3.2 8H1.6M12.4 3.6l-1.1 1.1M4.7 11.3l-1.1 1.1M12.4 12.4l-1.1-1.1M4.7 4.7 3.6 3.6"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
            />
          </svg>
          Settings
        </button>

        <button
          className={`nav-item ${page === "generate" ? "active" : ""}`}
          onClick={() => onNavigate("generate")}
        >
          <svg className="nav-icon" viewBox="0 0 16 16" fill="none">
            <path
              d="M2.5 12.5 6 3.5h4l3.5 9M4 9.5h8"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          Generate data
        </button>

        <div className="nav-spacer" />

        <button
          key={loginVersion}
          className={`nav-item nav-item-login ${drawerOpen ? "active" : ""}`}
          onClick={() => setDrawerOpen(true)}
        >
          <svg className="nav-icon" viewBox="0 0 16 16" fill="none">
            <path
              d="M6 8h7M10.5 5l3 3-3 3M9 2.5H4A1.5 1.5 0 0 0 2.5 4v8A1.5 1.5 0 0 0 4 13.5h5"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span className="nav-item-login-label">
            Login
            <span className={`nav-login-dot ${loginSummary() ? "ok" : "missing"}`} />
          </span>
        </button>

        <div className="nav-footer">v3.0.0</div>
      </nav>

      {drawerOpen && (
        <div className="drawer-overlay" onMouseDown={() => setDrawerOpen(false)}>
          <div className="drawer-panel" onMouseDown={(e) => e.stopPropagation()}>
            <div className="drawer-header">
              <p className="card-section-title" style={{ margin: 0 }}>
                APM Connection
              </p>
              <button className="drawer-close" onClick={() => setDrawerOpen(false)} aria-label="Close">
                ×
              </button>
            </div>
            <LoginPanel onChange={onLoginChange} />
          </div>
        </div>
      )}
    </>
  );
}
