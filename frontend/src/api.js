/**
 * Thin fetch wrapper around the FastAPI backend.
 *
 * The backend base URL is fixed at build time (VITE_API_BASE_URL) and is
 * NOT user-configurable anywhere in the UI — there is deliberately no
 * "Backend URL" setting. Every call attaches the "Login" header when one
 * is set; the backend forwards this straight through to the APM save
 * services, which use it to route to the correct database.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

function buildHeaders(login, extra = {}) {
  const headers = { "Content-Type": "application/json", ...extra };
  if (login) headers["Login"] = login;
  return headers;
}

async function parseErrorMessage(res) {
  try {
    const data = await res.json();
    if (data.detail) {
      if (Array.isArray(data.detail)) {
        return data.detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
      }
      return data.detail;
    }
    return res.statusText || "Request failed";
  } catch {
    return res.statusText || "Request failed";
  }
}

export async function getDomains() {
  const res = await fetch(`${BASE_URL}/meta/domains`);
  if (!res.ok) throw new Error(await parseErrorMessage(res));
  return res.json();
}

export async function getSettings(module, screen) {
  const params = new URLSearchParams({ module });
  if (screen) params.set("screen", screen);

  const res = await fetch(`${BASE_URL}/settings?${params.toString()}`, {
    method: "GET",
    headers: buildHeaders(),
  });
  if (!res.ok) throw new Error(await parseErrorMessage(res));
  return res.json();
}

export async function saveSettings(payload) {
  const res = await fetch(`${BASE_URL}/settings`, {
    method: "POST",
    headers: buildHeaders(),
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await parseErrorMessage(res));
  return res.json();
}

export async function generateData(login, payload) {
  const res = await fetch(`${BASE_URL}/generate`, {
    method: "POST",
    headers: buildHeaders(login),
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await parseErrorMessage(res));
  return res.json();
}

export async function saveRow(login, payload) {
  const res = await fetch(`${BASE_URL}/save-row`, {
    method: "POST",
    headers: buildHeaders(login),
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await parseErrorMessage(res));
  return res.json();
}

export function hasLogin(login) {
  if (!login) return false;
  try {
    const parsed = JSON.parse(login);
    return parsed && typeof parsed === "object" && Object.keys(parsed).length > 0;
  } catch {
    return false;
  }
}