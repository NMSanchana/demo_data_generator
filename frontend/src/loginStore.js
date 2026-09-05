const KEY = "apm_login_json";

export function getLogin() {
  return localStorage.getItem(KEY) || "";
}

export function setLogin(value) {
  localStorage.setItem(KEY, value);
}

export function hasLogin() {
  return !!getLogin().trim();
}

export function clearLogin() {
  localStorage.removeItem(KEY);
}

export function loginSummary() {
  const raw = getLogin();
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return { userName: parsed.UserName || parsed.UserCode || "Configured", ouName: parsed.OuName || null };
  } catch {
    return { userName: "Configured", ouName: null };
  }
}
