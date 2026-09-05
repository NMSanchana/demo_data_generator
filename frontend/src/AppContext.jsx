import { createContext, useContext, useState } from "react";
import { getLogin } from "./loginStore.js";

const AppContext = createContext(null);

export function AppProvider({ children }) {
  // loginVersion is bumped whenever the Login drawer saves/clears, so any
  // component that reads loginStore's current value (which lives outside
  // React state, in localStorage) knows to re-render.
  const [loginVersion, setLoginVersion] = useState(0);

  const bumpLoginVersion = () => setLoginVersion((v) => v + 1);

  return (
    <AppContext.Provider
      value={{
        login: getLogin(),
        loginVersion,
        bumpLoginVersion,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used within AppProvider");
  return ctx;
}
