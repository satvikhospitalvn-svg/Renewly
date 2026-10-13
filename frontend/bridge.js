// bridge.js - connects the Renewly frontend to the desktop engine (main.py).
//
// Loaded as <script type="module"> by the page that renewly_bridge.py serves. It is NOT used
// by the Electron build, where `window.renewlyBridge` simply does not exist and renderer.js
// keeps working from localStorage alone.
//
// Flow: read the per-launch token from the URL -> hydrate localStorage from the engine ->
// start the app (renderer.js, served as /app.js) -> forward changes back to the engine.

const KEYS = { plans: "renewly.plans.v1", settings: "renewly.settings.v1" };
const TOKEN_KEY = "renewly.bridge.token";
const POLL_MS = 15000;

const safe = {
  get(store, key) { try { return store.getItem(key); } catch { return null; } },
  set(store, key, value) { try { store.setItem(key, value); } catch { /* storage unavailable */ } },
};

// ---- token (arrives once in the URL, then lives only in this tab's sessionStorage) ----
const params = new URLSearchParams(location.search);
let token = params.get("t") || safe.get(sessionStorage, TOKEN_KEY) || "";
if (params.has("t")) {
  safe.set(sessionStorage, TOKEN_KEY, token);
  history.replaceState(null, "", location.pathname); // keep the token out of the address bar
}

async function api(method, path, body) {
  const headers = { "X-Renewly-Token": token };
  const init = { method, headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const response = await fetch(path, init);
  let data = null;
  try { data = await response.json(); } catch { /* empty body */ }
  if (!response.ok) throw new Error((data && data.error) || `Request failed (${response.status})`);
  return data;
}

function reportError(error) {
  const message = error instanceof Error ? error.message : String(error);
  window.dispatchEvent(new CustomEvent("renewly-bridge-error", { detail: message }));
}

// ---- state sync ----------------------------------------------------------------------
let pendingWrites = 0;
let lastHash = "";
const listeners = new Set();

function writeLocal(state) {
  safe.set(localStorage, KEYS.plans, JSON.stringify(state.plans || []));
  safe.set(localStorage, KEYS.settings, JSON.stringify(state.settings || {}));
  if (window.renewlyBridge) {
    window.renewlyBridge.info = { user: state.user || {}, cloud: Boolean(state.cloud), version: state.version || "", meta: state.meta || {} };
  }
}

const hashOf = (state) => JSON.stringify([state.plans, state.settings, state.user, state.cloud]);

async function refresh() {
  if (pendingWrites > 0 || document.hidden) return;
  try {
    const state = await api("GET", "/bridge/state");
    const hash = hashOf(state);
    if (hash === lastHash) return;
    lastHash = hash;
    writeLocal(state);
    listeners.forEach((fn) => { try { fn(state); } catch (e) { console.error(e); } });
  } catch { /* engine briefly unavailable (or app signed out); try again on the next tick */ }
}

async function write(action) {
  pendingWrites += 1;
  try {
    await action();
    try { lastHash = hashOf(await api("GET", "/bridge/state")); } catch { /* ignore */ }
  } catch (error) {
    reportError(error);
  } finally {
    pendingWrites -= 1;
  }
}

// ---- public API used by renderer.js --------------------------------------------------
window.renewlyWindow = {
  minimize: () => api("POST", "/bridge/window/minimize", {}).catch(reportError),
  toggleMaximize: () => api("POST", "/bridge/window/toggle-maximize", {}).catch(reportError),
  close: () => api("POST", "/bridge/window/close", {}).catch(reportError),
};

window.renewlyBridge = {
  available: true,
  info: { user: {}, cloud: false, version: "", meta: {} },
  upsertPlan: (plan) => write(() => api("PUT", `/bridge/plans/${encodeURIComponent(plan.id)}`, plan)),
  deletePlan: (id) => write(() => api("DELETE", `/bridge/plans/${encodeURIComponent(id)}`)),
  saveSettings: (settings) => write(() => api("PUT", "/bridge/settings", settings)),
  // Open one of the original desktop windows: settings, profile, password, logout,
  // notifications, recycle, export, web-push-portal.
  openDesktop: (name) => api("POST", `/bridge/desktop/${encodeURIComponent(name)}`, {}),
  testPush: () => api("POST", "/bridge/push/test", {}),
  onExternalChange: (fn) => { listeners.add(fn); return () => listeners.delete(fn); },
};

// ---- start ---------------------------------------------------------------------------
function showFatal(message) {
  document.body.innerHTML = "";
  const box = document.createElement("div");
  box.style.cssText = "font:16px system-ui,sans-serif;max-width:32em;margin:12vh auto;padding:0 20px;line-height:1.5";
  box.textContent = message;
  document.body.append(box);
}

try {
  const state = await api("GET", "/bridge/state");
  lastHash = hashOf(state);
  writeLocal(state);
} catch (error) {
  delete window.renewlyBridge;
  showFatal("Renewly could not reach the desktop app. Keep the Renewly app running and open it from there. ("
    + (error && error.message ? error.message : error) + ")");
  throw error;
}

await import("/app.js");

setInterval(refresh, POLL_MS);
window.addEventListener("focus", refresh);
document.addEventListener("visibilitychange", refresh);
