// Screenshot harness only: stands in for the browser extension APIs with
// fixed, fictional sample data so the real popup, chooser and in-page dialog
// render exactly as they do in the browser. Never shipped in the extension.
(() => {
  const listeners = [];
  const now = Date.now();
  const store = {
    duplicateTabGuardExceptionSettingsV1: {
      schema: 1, enabled: true, pausedUntil: 0,
      rules: [
        { id: "r1", type: "host", pattern: "mail.example.com", browser: "all", context: "all", enabled: true, note: "Several inbox tabs are fine", createdAt: now - 86400000 },
        { id: "r2", type: "wildcard", pattern: "https://calendar.example.com/*", browser: "all", context: "all", enabled: true, note: "", createdAt: now - 7200000 },
        { id: "r3", type: "exact", pattern: "https://example.com/dashboard", browser: "chrome", context: "regular", enabled: false, note: "", createdAt: now - 3600000 }
      ]
    }
  };
  const incident = {
    incident_id: "sample", url: "", current_browser: "brave",
    existing_summary: "Already open in Chrome", primary_existing_label: "Chrome", primary_existing_browser: "chrome",
    existing_browser_labels: ["Chrome"], existing_browsers: ["chrome"], copy_count: 2, private_copy_count: 0,
    existing_copies: [{ browser: "chrome", browser_label: "Chrome", is_same_browser: false, window_id: 1, incognito: false, title: "Getting started | Example Docs" }]
  };
  window.__sampleIncident = incident;
  window.chrome = {
    runtime: {
      sendMessage: async (message) => {
        switch (message && message.type) {
          case "get_status": return { ok: true, native_connected: true, browser: "brave", incognito_allowed: true, file_scheme_allowed: true, pending_incidents: 0, browser_override: "auto" };
          case "get_active_tab_context": return { ok: true, trackable: true, url: "http://example.com/docs/getting-started/" };
          case "chooser_get": return { ok: true, tab_id: 7, incident: Object.assign({}, incident, { url: "http://example.com/docs/getting-started/" }) };
          default: return { ok: true };
        }
      },
      onMessage: { addListener: (fn) => listeners.push(fn) }
    },
    storage: {
      local: {
        get: async (keys) => { const out = {}; for (const k of [].concat(keys || [])) if (k in store) out[k] = store[k]; return out; },
        set: async (value) => { Object.assign(store, value); }
      },
      onChanged: { addListener: () => {} }
    }
  };
  window.__dispatchToContentScript = (message) => listeners.forEach((fn) => fn(message));
})();
