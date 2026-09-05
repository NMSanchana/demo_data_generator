import { useState } from "react";
import { AppProvider, useApp } from "./AppContext";
import NavRail from "./components/NavRail";
import SettingsPage from "./components/SettingsPage";
import GeneratePage from "./components/GeneratePage";

function Shell() {
  const [page, setPage] = useState("generate");
  const { loginVersion, bumpLoginVersion } = useApp();

  return (
    <div className="app-shell">
      <NavRail page={page} onNavigate={setPage} loginVersion={loginVersion} onLoginChange={bumpLoginVersion} />
      <div className="main-column">
        <div className="content">{page === "settings" ? <SettingsPage /> : <GeneratePage />}</div>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <AppProvider>
      <Shell />
    </AppProvider>
  );
}
