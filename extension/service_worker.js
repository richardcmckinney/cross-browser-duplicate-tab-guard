"use strict";

// CBDTG_EXCEPTIONS_PATCH_V1
importScripts("exceptions.js");

const VERSION = "2.3.1";
const NATIVE_HOST = "systems.venturi.duplicate_tab_guard";
const FALLBACK_WS_URL = "ws://127.0.0.1:__WS_PORT__/__AUTH_TOKEN__";
const BROWSERS = new Set(["brave", "chrome", "chromium"]);
const SNAPSHOT_INTERVAL_MS = 15_000;
const REQUEST_TIMEOUT_MS = 12_000;
const TRANSFER_REQUEST_TIMEOUT_MS = 30_000;
const SESSION_STORAGE_KEY = "pending_duplicate_incidents_v2";
const EXCEPTION_SETTINGS_KEY = CBDTGExceptions.STORAGE_KEY;

let exceptionSettings = CBDTGExceptions.defaultSettings();
let exceptionSettingsPromise = null;
let exceptionReconcileTimer = null;

let nativePort = null;
let nativeReady = false;
let nativeConnecting = false;
let reconnectDelayMs = 500;
let reconnectTimer = null;
let snapshotTimer = null;
let runtimeBrowser = "chromium";
let profileId = null;
let sessionId = crypto.randomUUID();
let incognitoAllowed = false;
let fileSchemeAllowed = false;
let lastNativeError = "";
let transportKind = "none";

const readyWaiters = [];
const pendingRequests = new Map();
const claimCache = new Map();
const claimEpoch = new Map();
const pendingIncidents = new Map();
const fallbackTabs = new Set();
const incidentUiEpoch = new Map();
const TRANSIENT_URLS = new Set([
  "about:blank",
  "about:srcdoc",
  "chrome://newtab/",
  "brave://newtab/",
  "chrome-search://local-ntp/local-ntp.html",
]);

function now() {
  return Date.now();
}

function browserLabel(browser) {
  return ({ brave: "Brave", chrome: "Chrome", chromium: "Chromium" })[browser] || browser;
}

// CBDTG_OWN_EXTENSION_PAGE_EXCLUSION_V1
const OWN_EXTENSION_MANAGER_SCHEMES = new Set(["chrome:", "brave:", "chromium:"]);

function isOwnExtensionUrl(url) {
  return typeof url === "string" && url.startsWith(chrome.runtime.getURL(""));
}

// CBDTG_OWN_EXTENSION_MANAGER_RUNTIME_ID_V2
function containsRuntimeExtensionIdToken(value) {
  let text = String(value || "");
  try {
    text = decodeURIComponent(text);
  } catch (_) {
    // Use the undecoded value when malformed percent-encoding is present.
  }
  const runtimeId = String(chrome.runtime.id || "").toLowerCase();
  if (!runtimeId) return false;
  return text
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .includes(runtimeId);
}

function isOwnExtensionManagementUrl(url) {
  if (typeof url !== "string" || !url) return false;
  try {
    const parsed = new URL(url);
    if (!OWN_EXTENSION_MANAGER_SCHEMES.has(parsed.protocol.toLowerCase())) return false;
    if (parsed.hostname.toLowerCase() !== "extensions") return false;

    for (const [, value] of parsed.searchParams) {
      if (containsRuntimeExtensionIdToken(value)) return true;
    }
    return containsRuntimeExtensionIdToken(parsed.pathname) || containsRuntimeExtensionIdToken(parsed.hash);
  } catch (_) {
    return false;
  }
}

// CBDTG_EXTENSION_MANAGEMENT_SURFACE_EXCLUSION_V1
// Extension-management surfaces are never tracked in any browser, whichever
// extension is shown: every Chromium-family browser's extensions page and its
// sub-pages, the settings route for extensions, and the extension stores.
// Treating them as duplicates raised a dialog or redirected the tab to the
// chooser, which made the extensions page impossible to open twice.
const EXTENSION_MANAGEMENT_SCHEMES = new Set([
  "chrome:",
  "brave:",
  "chromium:",
  "edge:",
  "arc:",
  "opera:",
  "vivaldi:",
]);
const EXTENSION_MANAGEMENT_HOSTS = new Set(["extensions", "extensions-frame", "extensions-internals"]);
const EXTENSION_STORE_PREFIXES = [
  "https://chromewebstore.google.com/",
  "https://chrome.google.com/webstore/",
  "https://microsoftedge.microsoft.com/addons/",
  "https://addons.opera.com/",
];

function isExtensionManagementSurfaceUrl(url) {
  if (typeof url !== "string" || !url) return false;
  let parsed;
  try {
    parsed = new URL(url);
  } catch (_) {
    return false;
  }
  const protocol = parsed.protocol.toLowerCase();
  if (protocol === "https:") {
    const origin = `${protocol}//${parsed.hostname.toLowerCase()}`;
    const path = parsed.pathname.toLowerCase();
    const normalized = `${origin}${path.endsWith("/") ? path : `${path}/`}`;
    return EXTENSION_STORE_PREFIXES.some((prefix) => normalized.startsWith(prefix));
  }
  if (!EXTENSION_MANAGEMENT_SCHEMES.has(protocol)) return false;
  const host = parsed.hostname.toLowerCase();
  if (EXTENSION_MANAGEMENT_HOSTS.has(host)) return true;
  if (host !== "settings") return false;
  const path = parsed.pathname.toLowerCase();
  return path === "/extensions" || path.startsWith("/extensions/");
}
// END CBDTG_EXTENSION_MANAGEMENT_SURFACE_EXCLUSION_V1

function isBuiltInExcludedUrl(url) {
  return isOwnExtensionUrl(url) || isOwnExtensionManagementUrl(url) || isExtensionManagementSurfaceUrl(url);
}

function isTrackableUrl(url) {
  return typeof url === "string" && url.length > 0 && !isBuiltInExcludedUrl(url);
}

function isTransientUrl(url) {
  return TRANSIENT_URLS.has(String(url || ""));
}

function advanceClaimEpoch(tabId) {
  const epoch = (claimEpoch.get(tabId) || 0) + 1;
  claimEpoch.set(tabId, epoch);
  return epoch;
}

async function currentTabForClaim(tabId, expectedUrl, epoch) {
  if (claimEpoch.get(tabId) !== epoch) return null;
  try {
    const tab = await chrome.tabs.get(tabId);
    if (claimEpoch.get(tabId) !== epoch) return null;
    if (Boolean(tab.incognito) !== Boolean(chrome.extension.inIncognitoContext)) return null;
    return tabUrl(tab) === expectedUrl ? tab : null;
  } catch (_) {
    return null;
  }
}

function tabUrl(tab) {
  const url = (tab && (tab.url || tab.pendingUrl)) || "";
  return typeof url === "string" ? url : "";
}

function sanitizeTitle(value) {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, 2048);
}

function randomId() {
  return crypto.randomUUID();
}

async function ensureExceptionSettings(force = false) {
  if (force || !exceptionSettingsPromise) {
    exceptionSettingsPromise = chrome.storage.local.get([EXCEPTION_SETTINGS_KEY])
      .then((stored) => {
        exceptionSettings = CBDTGExceptions.normalizeSettings(stored[EXCEPTION_SETTINGS_KEY]);
        return exceptionSettings;
      })
      .catch(() => {
        exceptionSettings = CBDTGExceptions.defaultSettings();
        return exceptionSettings;
      });
  }
  return exceptionSettingsPromise;
}

function exceptionDecision(url, incognito) {
  return CBDTGExceptions.evaluate(exceptionSettings, {
    url: String(url || ""),
    browser: runtimeBrowser,
    incognito: Boolean(incognito),
  });
}

async function clearIncidentForException(tabId, reason = "exception_rule") {
  const prior = pendingIncidents.get(tabId);
  if (!prior) return;
  const wasFallback = fallbackTabs.has(tabId);
  pendingIncidents.delete(tabId);
  fallbackTabs.delete(tabId);
  incidentUiEpoch.set(tabId, (incidentUiEpoch.get(tabId) || 0) + 1);
  await persistPendingIncidents();
  if (wasFallback && prior.url) {
    chrome.tabs.update(tabId, { url: String(prior.url), active: true }).catch(() => {});
  } else {
    await dismissIncidentUi(tabId);
  }
  sendNativeEvent("cancel_incident", {
    incident_id: prior.incident_id,
    tab_id: String(tabId),
    reason,
  });
}

async function unregisterExcludedTab(tabId, decision) {
  advanceClaimEpoch(tabId);
  claimCache.delete(tabId);
  await clearIncidentForException(tabId, decision && decision.reason ? decision.reason : "exception_rule");
  sendNativeEvent("tab_closed", {
    tab_id: String(tabId),
    reason: decision && decision.reason ? decision.reason : "exception_rule",
  });
}

function scheduleExceptionReconcile() {
  if (exceptionReconcileTimer) clearTimeout(exceptionReconcileTimer);
  exceptionReconcileTimer = setTimeout(() => {
    exceptionReconcileTimer = null;
    reconcileExceptionSettings().catch(() => {});
  }, 80);
}

async function reconcileExceptionSettings() {
  await ensureExceptionSettings(true);
  const tabs = await chrome.tabs.query({});
  const claims = [];
  for (const tab of tabs) {
    if (!Number.isInteger(tab.id)) continue;
    if (Boolean(tab.incognito) !== Boolean(chrome.extension.inIncognitoContext)) continue;
    let url = tabUrl(tab);
    if (isOwnExtensionUrl(url)) url = pendingOriginalUrl(tab.id);
    if (isBuiltInExcludedUrl(url)) {
      await unregisterExcludedTab(tab.id, { reason: "own_extension_page" });
      continue;
    }
    if (!isTrackableUrl(url) || isTransientUrl(url)) continue;
    const decision = exceptionDecision(url, tab.incognito);
    if (decision.excluded) {
      await unregisterExcludedTab(tab.id, decision);
    } else {
      claims.push(claimTab(tab.id, url, "exception_settings_changed", true));
    }
  }
  await Promise.allSettled(claims);
  if (nativeReady) await sendSnapshot();
}

async function detectBrowser() {
  const saved = await chrome.storage.local.get(["browserOverride"]);
  const override = String(saved.browserOverride || "").toLowerCase();
  if (BROWSERS.has(override)) return override;

  try {
    if (navigator.brave && typeof navigator.brave.isBrave === "function" && (await navigator.brave.isBrave())) {
      return "brave";
    }
  } catch (_) {
    // Continue with Chromium client hints.
  }

  const brands = Array.isArray(navigator.userAgentData && navigator.userAgentData.brands)
    ? navigator.userAgentData.brands.map((item) => String(item.brand || ""))
    : [];
  if (brands.some((brand) => /brave/i.test(brand))) return "brave";
  if (brands.some((brand) => /google chrome/i.test(brand))) return "chrome";
  if (brands.some((brand) => /^chromium$/i.test(brand))) return "chromium";

  const ua = String(navigator.userAgent || "");
  if (/brave/i.test(ua)) return "brave";
  if (/chromium/i.test(ua)) return "chromium";
  if (/chrome/i.test(ua)) return "chrome";
  return "chromium";
}

async function ensureIdentity() {
  if (!profileId) {
    const stored = await chrome.storage.local.get(["profileId"]);
    profileId = String(stored.profileId || "");
    if (!profileId) {
      profileId = randomId();
      await chrome.storage.local.set({ profileId });
    }
  }
  runtimeBrowser = await detectBrowser();
  [incognitoAllowed, fileSchemeAllowed] = await Promise.all([
    chrome.extension.isAllowedIncognitoAccess(),
    chrome.extension.isAllowedFileSchemeAccess(),
  ]);
}

function resolveReadyWaiters(error = null) {
  while (readyWaiters.length) {
    const waiter = readyWaiters.shift();
    if (error) waiter.reject(error);
    else waiter.resolve();
  }
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectNative().catch(() => {});
  }, reconnectDelayMs);
  reconnectDelayMs = Math.min(15_000, Math.round(reconnectDelayMs * 1.8));
}

function rejectPendingRequests(message) {
  for (const [requestId, pending] of pendingRequests) {
    clearTimeout(pending.timer);
    pending.reject(new Error(message));
    pendingRequests.delete(requestId);
  }
}

function helloPayload() {
  return {
    type: "hello",
    extension_version: VERSION,
    extension_id: chrome.runtime.id,
    browser: runtimeBrowser,
    profile_id: profileId,
    session_id: sessionId,
    incognito_context: chrome.extension.inIncognitoContext,
    incognito_allowed: incognitoAllowed,
    file_scheme_allowed: fileSchemeAllowed,
  };
}

function clearCurrentTransport(errorMessage, rejectPending = true) {
  lastNativeError = errorMessage || "The local tab coordinator disconnected.";
  nativeReady = false;
  nativePort = null;
  nativeConnecting = false;
  transportKind = "none";
  if (rejectPending) rejectPendingRequests(lastNativeError);
}

async function connectWebSocket() {
  if (nativeReady || nativeConnecting || nativePort) return;
  nativeConnecting = true;
  try {
    await ensureIdentity();
    const socket = new WebSocket(FALLBACK_WS_URL);
    let opened = false;
    const adapter = {
      kind: "websocket",
      postMessage(value) {
        if (socket.readyState !== WebSocket.OPEN) throw new Error("The loopback coordinator is not connected.");
        socket.send(JSON.stringify(value));
      },
      disconnect() {
        try { socket.close(1000, "extension reconnect"); } catch (_) {}
      },
    };
    nativePort = adapter;
    transportKind = "websocket";
    lastNativeError = "";

    const handshakeTimer = setTimeout(() => {
      if (!nativeReady && nativePort === adapter) {
        try { socket.close(); } catch (_) {}
      }
    }, 5_000);

    socket.addEventListener("open", () => {
      opened = true;
      try { adapter.postMessage(helloPayload()); } catch (_) {}
    });
    socket.addEventListener("message", (event) => {
      try {
        handleNativeMessage(JSON.parse(String(event.data || "{}"))).catch(() => {});
      } catch (_) {
        lastNativeError = "The loopback coordinator returned invalid JSON.";
      }
    });
    socket.addEventListener("error", () => {
      lastNativeError = "The loopback coordinator could not be reached.";
    });
    socket.addEventListener("close", () => {
      clearTimeout(handshakeTimer);
      if (nativePort !== adapter) return;
      clearCurrentTransport(
        lastNativeError || (opened ? "The loopback coordinator disconnected." : "The loopback coordinator is not running."),
        nativeReady,
      );
      scheduleReconnect();
    });
  } catch (error) {
    clearCurrentTransport(error && error.message ? error.message : String(error), false);
    scheduleReconnect();
  } finally {
    nativeConnecting = false;
  }
}

async function connectNative() {
  if (nativeReady || nativeConnecting || nativePort) return;
  nativeConnecting = true;
  try {
    await ensureIdentity();
    const port = chrome.runtime.connectNative(NATIVE_HOST);
    nativePort = port;
    transportKind = "native";
    nativeReady = false;
    lastNativeError = "";

    const handshakeTimer = setTimeout(() => {
      if (!nativeReady && nativePort === port) {
        try { port.disconnect(); } catch (_) {}
      }
    }, 5_000);

    port.onMessage.addListener((message) => {
      handleNativeMessage(message).catch(() => {});
    });
    port.onDisconnect.addListener(() => {
      clearTimeout(handshakeTimer);
      if (nativePort !== port) return;
      const error = chrome.runtime.lastError && chrome.runtime.lastError.message;
      const wasReady = nativeReady;
      clearCurrentTransport(error || "The native coordinator disconnected.", wasReady);
      // Native messaging can be unavailable in confined Snap/Flatpak browsers;
      // the installed loopback WebSocket service is the compatibility fallback.
      connectWebSocket().catch(() => scheduleReconnect());
    });

    port.postMessage(helloPayload());
  } catch (error) {
    clearCurrentTransport(error && error.message ? error.message : String(error), false);
    connectWebSocket().catch(() => scheduleReconnect());
  } finally {
    nativeConnecting = false;
  }
}
function waitForNativeReady(timeoutMs = REQUEST_TIMEOUT_MS) {
  if (nativeReady && nativePort) return Promise.resolve();
  connectNative().catch(() => {});
  return new Promise((resolve, reject) => {
    const entry = { resolve, reject };
    readyWaiters.push(entry);
    setTimeout(() => {
      const index = readyWaiters.indexOf(entry);
      if (index >= 0) readyWaiters.splice(index, 1);
      reject(new Error(lastNativeError || "The local tab coordinator is not ready."));
    }, timeoutMs);
  });
}

async function sendNativeRequest(type, payload = {}, timeoutMs = REQUEST_TIMEOUT_MS) {
  await waitForNativeReady(timeoutMs);
  if (!nativePort) throw new Error("The local tab coordinator is unavailable.");
  const requestId = randomId();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pendingRequests.delete(requestId);
      reject(new Error("The local tab coordinator did not respond in time."));
    }, timeoutMs);
    pendingRequests.set(requestId, { resolve, reject, timer });
    try {
      nativePort.postMessage({ type, request_id: requestId, ...payload });
    } catch (error) {
      clearTimeout(timer);
      pendingRequests.delete(requestId);
      reject(error);
    }
  });
}

function sendNativeEvent(type, payload = {}) {
  if (!nativeReady || !nativePort) return;
  try {
    nativePort.postMessage({ type, ...payload });
  } catch (_) {
    // Reconnection logic will refresh the full tab snapshot.
  }
}

async function handleNativeMessage(message) {
  if (!message || typeof message !== "object") return;
  if (message.type === "hello_ack") {
    if (BROWSERS.has(message.browser)) runtimeBrowser = message.browser;
    if (message.transport === "native" || message.transport === "websocket") transportKind = message.transport;
    nativeReady = true;
    reconnectDelayMs = 500;
    resolveReadyWaiters();
    await restorePendingIncidents();
    await sendSnapshot();
    startSnapshotTimer();
    return;
  }

  if (message.request_id && pendingRequests.has(message.request_id)) {
    const pending = pendingRequests.get(message.request_id);
    pendingRequests.delete(message.request_id);
    clearTimeout(pending.timer);
    if (message.ok === false) pending.reject(new Error(message.error || "Coordinator request failed."));
    else pending.resolve(message);
    return;
  }

  if (message.type === "event") {
    await handleCoordinatorEvent(message);
  }
}

function startSnapshotTimer() {
  if (snapshotTimer) clearInterval(snapshotTimer);
  snapshotTimer = setInterval(() => {
    sendSnapshot().catch(() => {});
  }, SNAPSHOT_INTERVAL_MS);
}

async function getPendingStore() {
  try {
    const stored = await chrome.storage.session.get([SESSION_STORAGE_KEY]);
    return stored[SESSION_STORAGE_KEY] && typeof stored[SESSION_STORAGE_KEY] === "object"
      ? stored[SESSION_STORAGE_KEY]
      : {};
  } catch (_) {
    return {};
  }
}

async function persistPendingIncidents() {
  const value = {};
  for (const [tabId, incident] of pendingIncidents) {
    value[String(tabId)] = incident;
  }
  try {
    await chrome.storage.session.set({ [SESSION_STORAGE_KEY]: value });
  } catch (_) {
    // Session persistence is a resilience enhancement, not a correctness dependency.
  }
}

async function restorePendingIncidents() {
  const stored = await getPendingStore();
  pendingIncidents.clear();
  fallbackTabs.clear();
  for (const [rawTabId, incident] of Object.entries(stored)) {
    const tabId = Number(rawTabId);
    if (!Number.isInteger(tabId) || !incident || !incident.incident_id) continue;
    try {
      const tab = await chrome.tabs.get(tabId);
      pendingIncidents.set(tabId, incident);
      if (isOwnExtensionUrl(tabUrl(tab))) fallbackTabs.add(tabId);
      if (isBuiltInExcludedUrl(String(incident.url || ""))) {
        await unregisterExcludedTab(tabId, { reason: "own_extension_page" });
        continue;
      }
    } catch (_) {
      // Tab no longer exists.
    }
  }
  await persistPendingIncidents();
}

function pendingOriginalUrl(tabId) {
  const incident = pendingIncidents.get(tabId);
  return incident && incident.url ? String(incident.url) : "";
}

function tabPayload(tab, forcedUrl = "") {
  const url = forcedUrl || tabUrl(tab);
  return {
    tab_id: String(tab.id),
    window_id: Number(tab.windowId),
    url,
    title: sanitizeTitle(tab.title),
    incognito: Boolean(tab.incognito),
    active: Boolean(tab.active),
    last_active: tab.active ? now() : 0,
  };
}

async function sendSnapshot() {
  if (!nativeReady) return;
  await ensureExceptionSettings();
  const tabs = await chrome.tabs.query({});
  const snapshot = [];
  for (const tab of tabs) {
    if (Boolean(tab.incognito) !== Boolean(chrome.extension.inIncognitoContext)) continue;
    let url = tabUrl(tab);
    if (isOwnExtensionUrl(url)) url = pendingOriginalUrl(tab.id);
    if (!isTrackableUrl(url) || isTransientUrl(url)) continue;
    if (exceptionDecision(url, tab.incognito).excluded) continue;
    snapshot.push(tabPayload(tab, url));
  }
  await sendNativeRequest("snapshot", { tabs: snapshot }, REQUEST_TIMEOUT_MS);
}

async function sendMessageToTab(tabId, message, attempts = 1) {
  let lastError = null;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await chrome.tabs.sendMessage(tabId, message);
    } catch (error) {
      lastError = error;
      if (attempt + 1 < attempts) await new Promise((resolve) => setTimeout(resolve, 120));
    }
  }
  throw lastError || new Error("No content-script receiver is available.");
}

async function showDuplicateDialog(tabId, incident, expectedClaimEpoch = null) {
  if (expectedClaimEpoch !== null) {
    const current = await currentTabForClaim(tabId, String(incident.url || ""), expectedClaimEpoch);
    if (!current) return;
  }
  const epoch = (incidentUiEpoch.get(tabId) || 0) + 1;
  incidentUiEpoch.set(tabId, epoch);
  pendingIncidents.set(tabId, incident);
  await persistPendingIncidents();
  if (expectedClaimEpoch !== null && !(await currentTabForClaim(tabId, String(incident.url || ""), expectedClaimEpoch))) {
    const current = pendingIncidents.get(tabId);
    if (current && current.incident_id === incident.incident_id) {
      pendingIncidents.delete(tabId);
      incidentUiEpoch.set(tabId, epoch + 1);
      await persistPendingIncidents();
    }
    return;
  }
  try {
    await sendMessageToTab(tabId, { type: "show_duplicate_dialog", payload: incident }, 3);
    if (incidentUiEpoch.get(tabId) !== epoch) {
      const current = pendingIncidents.get(tabId);
      if (current) {
        sendMessageToTab(tabId, { type: "show_duplicate_dialog", payload: current }, 1).catch(() => {});
      } else {
        sendMessageToTab(tabId, { type: "dismiss_duplicate_dialog" }, 1).catch(() => {});
      }
      return;
    }
    fallbackTabs.delete(tabId);
  } catch (_) {
    const current = pendingIncidents.get(tabId);
    if (incidentUiEpoch.get(tabId) !== epoch || !current || current.incident_id !== incident.incident_id) {
      return;
    }
    const tab = await matchingTab(tabId, incident.url);
    if (!tab) {
      pendingIncidents.delete(tabId);
      fallbackTabs.delete(tabId);
      incidentUiEpoch.set(tabId, epoch + 1);
      await persistPendingIncidents();
      return;
    }
    fallbackTabs.add(tabId);
    const chooserUrl = chrome.runtime.getURL(`chooser.html#${encodeURIComponent(incident.incident_id)}`);
    await chrome.tabs.update(tabId, { url: chooserUrl, active: true });
  }
}

async function updateIncidentUi(tabId, message, kind = "info", busy = false) {
  if (fallbackTabs.has(tabId)) {
    chrome.runtime.sendMessage({
      type: "chooser_event",
      tab_id: tabId,
      message,
      kind,
      busy,
    }).catch(() => {});
    return;
  }
  sendMessageToTab(tabId, {
    type: "duplicate_status",
    message,
    kind,
    busy,
  }).catch(() => {});
}

async function dismissIncidentUi(tabId) {
  if (!fallbackTabs.has(tabId)) {
    sendMessageToTab(tabId, { type: "dismiss_duplicate_dialog" }).catch(() => {});
  }
}

async function clearIncidentForNavigation(tabId, nextUrl) {
  const prior = pendingIncidents.get(tabId);
  if (!prior || prior.url === nextUrl) return;
  pendingIncidents.delete(tabId);
  fallbackTabs.delete(tabId);
  incidentUiEpoch.set(tabId, (incidentUiEpoch.get(tabId) || 0) + 1);
  await persistPendingIncidents();
  await dismissIncidentUi(tabId);
  sendNativeEvent("cancel_incident", { incident_id: prior.incident_id, tab_id: String(tabId) });
}

async function clearResolvedIncident(tabId) {
  if (!pendingIncidents.has(tabId)) return;
  pendingIncidents.delete(tabId);
  fallbackTabs.delete(tabId);
  incidentUiEpoch.set(tabId, (incidentUiEpoch.get(tabId) || 0) + 1);
  await persistPendingIncidents();
  await dismissIncidentUi(tabId);
}

async function claimTab(tabId, url, source, force = false) {
  if (!Number.isInteger(tabId)) return;
  const normalizedUrl = String(url || "");

  // The extension-owned chooser is a surrogate UI for protected pages that
  // reject content-script injection. Its navigation must never cancel the
  // original duplicate incident or unregister the protected URL.
  if (isOwnExtensionUrl(normalizedUrl)) {
    advanceClaimEpoch(tabId);
    claimCache.delete(tabId);
    return;
  }

  if (isOwnExtensionManagementUrl(normalizedUrl)) {
    await unregisterExcludedTab(tabId, { reason: "own_extension_page" });
    return;
  }

  if (isExtensionManagementSurfaceUrl(normalizedUrl)) {
    await unregisterExcludedTab(tabId, { reason: "extension_management_page" });
    return;
  }

  // New-tab and blank documents are browser transition surfaces rather than
  // meaningful destinations. Never register them: doing so creates false
  // duplicates while a real URL is still committing.
  if (!isTrackableUrl(normalizedUrl) || isTransientUrl(normalizedUrl)) {
    advanceClaimEpoch(tabId);
    claimCache.delete(tabId);
    await clearIncidentForNavigation(tabId, normalizedUrl);
    sendNativeEvent("tab_closed", { tab_id: String(tabId) });
    return;
  }

  const cached = claimCache.get(tabId);
  if (!force && cached && cached.url === normalizedUrl && now() - cached.at < 1200) return;

  const epoch = advanceClaimEpoch(tabId);
  claimCache.set(tabId, { url: normalizedUrl, at: now() });
  const tab = await currentTabForClaim(tabId, normalizedUrl, epoch);
  if (!tab) return;

  await ensureExceptionSettings();
  const decision = exceptionDecision(normalizedUrl, tab.incognito);
  if (decision.excluded) {
    await unregisterExcludedTab(tabId, decision);
    return;
  }

  await clearIncidentForNavigation(tabId, normalizedUrl);
  if (!(await currentTabForClaim(tabId, normalizedUrl, epoch))) return;

  try {
    const response = await sendNativeRequest("claim", {
      tab: tabPayload(tab, normalizedUrl),
      source: String(source || "navigation"),
    });

    // Network/native-messaging responses can arrive after a navigation. A
    // stale response must never replace the alert for the tab's newer URL.
    if (!(await currentTabForClaim(tabId, normalizedUrl, epoch))) return;

    if (response.result === "duplicate" && response.incident) {
      if (String(response.incident.url || "") !== normalizedUrl) return;
      await showDuplicateDialog(tabId, response.incident, epoch);
    } else if (response.result === "transfer_completed") {
      await clearResolvedIncident(tabId);
    } else if (response.result === "unique") {
      await clearResolvedIncident(tabId);
    }
  } catch (error) {
    if (claimEpoch.get(tabId) === epoch) {
      lastNativeError = error && error.message ? error.message : String(error);
    }
  }
}

async function resolveDuplicate(tabId, incidentId, choice) {
  const incident = pendingIncidents.get(tabId);
  if (!incident || incident.incident_id !== incidentId) {
    throw new Error("This duplicate alert is no longer active.");
  }
  if (choice !== "current" && !BROWSERS.has(choice)) {
    throw new Error("Choose Current browser, Brave, Chrome, or Chromium.");
  }
  await updateIncidentUi(tabId, "Applying your selection…", "info", true);
  const response = await sendNativeRequest("resolve", {
    incident_id: incidentId,
    requester_tab_id: String(tabId),
    choice,
  }, TRANSFER_REQUEST_TIMEOUT_MS);
  if (response.pending) {
    await updateIncidentUi(tabId, response.message || `Opening ${browserLabel(choice)}…`, "info", true);
  }
  return response;
}

async function createRequestedTab(message) {
  const url = String(message.url || "");
  const wantsIncognito = Boolean(message.incognito);
  if (!isTrackableUrl(url)) throw new Error("The coordinator supplied an invalid URL.");

  let createdTab = null;
  const windows = await chrome.windows.getAll({ populate: false, windowTypes: ["normal"] });
  const matching = windows.find((win) => Boolean(win.incognito) === wantsIncognito);
  if (matching) {
    createdTab = await chrome.tabs.create({ windowId: matching.id, url, active: true });
    await chrome.windows.update(matching.id, { focused: true });
  } else {
    const createdWindow = await chrome.windows.create({ url, incognito: wantsIncognito, focused: true, type: "normal" });
    if (createdWindow && Array.isArray(createdWindow.tabs) && createdWindow.tabs.length) {
      createdTab = createdWindow.tabs[0];
    }
  }
  // The transfer is finalized by the destination tab's exact-URL claim, not
  // by this acknowledgement. Some headless or policy-managed Chromium builds
  // omit the Window/Tabs object even after successfully creating the window,
  // so an empty tab_id is valid and must never crash the worker.
  sendNativeEvent("created_tab_ack", {
    transfer_id: message.transfer_id,
    tab_id: createdTab && Number.isInteger(createdTab.id) ? String(createdTab.id) : "",
  });
}

async function matchingTab(tabId, expectedUrl = "") {
  const numericId = Number(tabId);
  if (!Number.isInteger(numericId)) return null;
  try {
    const tab = await chrome.tabs.get(numericId);
    let currentUrl = tabUrl(tab);
    if (isOwnExtensionUrl(currentUrl)) currentUrl = pendingOriginalUrl(numericId);
    if (expectedUrl && currentUrl !== expectedUrl) return null;
    return tab;
  } catch (_) {
    return null;
  }
}

async function activateTab(tabId, expectedUrl = "") {
  const numericId = Number(tabId);
  const tab = await matchingTab(numericId, expectedUrl);
  if (!tab) return false;
  try {
    await chrome.tabs.update(numericId, { active: true });
    await chrome.windows.update(tab.windowId, { focused: true });
    return true;
  } catch (_) {
    // The tab may already have been closed by the user.
    return false;
  }
}

async function handleCoordinatorEvent(message) {
  const tabId = Number(message.tab_id);
  switch (message.action) {
    case "close_tab": {
      if (!Number.isInteger(tabId)) return;
      const expectedUrl = String(message.expected_url || "");
      const tab = await matchingTab(tabId, expectedUrl);
      if (!tab) {
        // A user navigation always wins over a stale deduplication decision.
        pendingIncidents.delete(tabId);
        fallbackTabs.delete(tabId);
        incidentUiEpoch.set(tabId, (incidentUiEpoch.get(tabId) || 0) + 1);
        await persistPendingIncidents();
        sendNativeEvent("tab_close_skipped", {
          tab_id: String(tabId),
          incident_id: message.incident_id || "",
          reason: "tab_missing_or_url_changed",
        });
        return;
      }
      await ensureExceptionSettings();
      const closeUrl = expectedUrl || tabUrl(tab);
      const closeDecision = exceptionDecision(closeUrl, tab.incognito);
      if (closeDecision.excluded) {
        await unregisterExcludedTab(tabId, closeDecision);
        sendNativeEvent("tab_close_skipped", {
          tab_id: String(tabId),
          incident_id: message.incident_id || "",
          reason: closeDecision.reason || "exception_rule",
        });
        return;
      }
      pendingIncidents.delete(tabId);
      fallbackTabs.delete(tabId);
      incidentUiEpoch.set(tabId, (incidentUiEpoch.get(tabId) || 0) + 1);
      await persistPendingIncidents();
      try {
        await chrome.tabs.remove(tabId);
        sendNativeEvent("tab_closed_ack", { tab_id: String(tabId), incident_id: message.incident_id || "" });
      } catch (error) {
        sendNativeEvent("tab_close_failed", {
          tab_id: String(tabId),
          incident_id: message.incident_id || "",
          error: error && error.message ? error.message : String(error),
        });
      }
      break;
    }
    case "activate_tab":
      await activateTab(tabId, String(message.expected_url || ""));
      break;
    case "keep_current": {
      if (!Number.isInteger(tabId)) return;
      const incident = pendingIncidents.get(tabId);
      const originalUrl = String(message.url || (incident && incident.url) || "");
      if (fallbackTabs.has(tabId)) {
        pendingIncidents.delete(tabId);
        fallbackTabs.delete(tabId);
        incidentUiEpoch.set(tabId, (incidentUiEpoch.get(tabId) || 0) + 1);
        await persistPendingIncidents();
        if (originalUrl) await chrome.tabs.update(tabId, { url: originalUrl, active: true });
      } else {
        await dismissIncidentUi(tabId);
        pendingIncidents.delete(tabId);
        incidentUiEpoch.set(tabId, (incidentUiEpoch.get(tabId) || 0) + 1);
        await persistPendingIncidents();
        await activateTab(tabId, originalUrl);
      }
      break;
    }
    case "resolution_error":
      if (Number.isInteger(tabId)) await updateIncidentUi(tabId, message.error || "The selected browser could not be opened.", "error", false);
      break;
    case "create_tab":
      try {
        await createRequestedTab(message);
      } catch (error) {
        sendNativeEvent("create_tab_failed", {
          transfer_id: message.transfer_id || "",
          error: error && error.message ? error.message : String(error),
        });
      }
      break;
    case "request_snapshot":
      await sendSnapshot();
      break;
    default:
      break;
  }
}

// CBDTG_NAVIGATION_COMPATIBILITY_V1
function registerWebNavigationEvent(eventName, source) {
  const navigationApi = globalThis.chrome && globalThis.chrome.webNavigation;
  const event = navigationApi && navigationApi[eventName];
  if (!event || typeof event.addListener !== "function") return false;

  event.addListener((details) => {
    if (!details || details.frameId !== 0 || !Number.isInteger(details.tabId)) return;
    const url = typeof details.url === "string" ? details.url : "";
    if (!url) return;
    claimTab(details.tabId, url, source).catch(() => {});
  });
  return true;
}

registerWebNavigationEvent("onCommitted", "committed");
registerWebNavigationEvent("onHistoryStateUpdated", "history_state");
registerWebNavigationEvent("onReferenceFragmentUpdated", "fragment");

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  // changeInfo.url is the compatibility path when a Chromium-family build does
  // not expose chrome.webNavigation. A Content-Disposition download response that never commits as
  // a page does not become a changed tab URL, so download handling is preserved.
  const changedUrl = changeInfo && typeof changeInfo.url === "string" ? changeInfo.url : "";
  if (changedUrl) {
    claimTab(tabId, changedUrl, "tab_url_changed").catch(() => {});
    return;
  }

  // The completed-document pass reconciles restored, discarded, and replaced
  // tabs without intercepting a navigation before it becomes a real page.
  if (changeInfo && changeInfo.status === "complete") {
    const url = tabUrl(tab);
    if (url) claimTab(tabId, url, "complete").catch(() => {});
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  claimCache.delete(tabId);
  claimEpoch.delete(tabId);
  pendingIncidents.delete(tabId);
  fallbackTabs.delete(tabId);
  incidentUiEpoch.delete(tabId);
  persistPendingIncidents().catch(() => {});
  sendNativeEvent("tab_closed", { tab_id: String(tabId) });
});

chrome.tabs.onReplaced.addListener((addedTabId, removedTabId) => {
  claimCache.delete(removedTabId);
  claimEpoch.delete(removedTabId);
  pendingIncidents.delete(removedTabId);
  fallbackTabs.delete(removedTabId);
  incidentUiEpoch.delete(removedTabId);
  persistPendingIncidents().catch(() => {});
  sendNativeEvent("tab_closed", { tab_id: String(removedTabId) });
  chrome.tabs.get(addedTabId).then((tab) => {
    const url = tabUrl(tab);
    if (isTrackableUrl(url)) claimTab(addedTabId, url, "replaced", true).catch(() => {});
  }).catch(() => {});
});

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  try {
    const tab = await chrome.tabs.get(tabId);
    sendNativeEvent("touch_tab", {
      tab_id: String(tabId),
      window_id: Number(tab.windowId),
      active: true,
      at: now(),
    });
  } catch (_) {
    // Ignore activation races.
  }
});

chrome.windows.onFocusChanged.addListener(async (windowId) => {
  if (windowId === chrome.windows.WINDOW_ID_NONE) return;
  try {
    const tabs = await chrome.tabs.query({ windowId, active: true });
    if (tabs.length) {
      sendNativeEvent("touch_tab", {
        tab_id: String(tabs[0].id),
        window_id: Number(windowId),
        active: true,
        at: now(),
      });
    }
  } catch (_) {
    // Ignore window focus races.
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message !== "object") return false;

  if (message.type === "resolve_duplicate") {
    const tabId = sender.tab && sender.tab.id;
    resolveDuplicate(tabId, String(message.incident_id || ""), String(message.choice || ""))
      .then((response) => sendResponse({ ok: true, pending: Boolean(response.pending), message: response.message || "" }))
      .catch((error) => sendResponse({ ok: false, error: error && error.message ? error.message : String(error) }));
    return true;
  }

  if (message.type === "chooser_get") {
    const tabId = sender.tab && sender.tab.id;
    const incident = pendingIncidents.get(tabId);
    sendResponse({ ok: Boolean(incident), tab_id: tabId, incident: incident || null });
    return false;
  }

  if (message.type === "chooser_resolve") {
    const tabId = sender.tab && sender.tab.id;
    resolveDuplicate(tabId, String(message.incident_id || ""), String(message.choice || ""))
      .then((response) => sendResponse({ ok: true, pending: Boolean(response.pending), message: response.message || "" }))
      .catch((error) => sendResponse({ ok: false, error: error && error.message ? error.message : String(error) }));
    return true;
  }

  if (message.type === "get_status") {
    Promise.all([
      chrome.extension.isAllowedIncognitoAccess(),
      chrome.extension.isAllowedFileSchemeAccess(),
      chrome.storage.local.get(["browserOverride", EXCEPTION_SETTINGS_KEY]),
    ]).then(([incAllowed, fileAllowed, settings]) => {
      const exceptionState = CBDTGExceptions.normalizeSettings(settings[EXCEPTION_SETTINGS_KEY]);
      sendResponse({
        ok: true,
        version: VERSION,
        extension_id: chrome.runtime.id,
        browser: runtimeBrowser,
        browser_override: settings.browserOverride || "auto",
        native_connected: nativeReady,
        native_error: lastNativeError,
        transport_kind: transportKind,
        incognito_context: chrome.extension.inIncognitoContext,
        incognito_allowed: incAllowed,
        file_scheme_allowed: fileAllowed,
        pending_incidents: pendingIncidents.size,
        guard_enabled: exceptionState.enabled,
        guard_paused_until: exceptionState.pausedUntil,
        exception_count: exceptionState.rules.filter((rule) => rule.enabled).length,
        exception_total: exceptionState.rules.length,
      });
    }).catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  if (message.type === "get_active_tab_context") {
    chrome.tabs.query({ active: true, lastFocusedWindow: true }).then((tabs) => {
      const tab = tabs[0] || null;
      let url = tab ? tabUrl(tab) : "";
      if (tab && isOwnExtensionUrl(url)) url = pendingOriginalUrl(tab.id);
      sendResponse({
        ok: true,
        url,
        incognito: Boolean(tab && tab.incognito),
        browser: runtimeBrowser,
        trackable: isTrackableUrl(url) && !isTransientUrl(url),
      });
    }).catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  if (message.type === "set_browser_override") {
    const value = String(message.value || "auto").toLowerCase();
    const stored = BROWSERS.has(value) ? value : "";
    chrome.storage.local.set({ browserOverride: stored }).then(() => {
      if (nativePort) nativePort.disconnect();
      runtimeBrowser = stored || runtimeBrowser;
      sessionId = randomId();
      connectNative().catch(() => {});
      sendResponse({ ok: true });
    }).catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  if (message.type === "open_extension_details") {
    const scheme = runtimeBrowser === "brave" ? "brave" : "chrome";
    chrome.tabs.create({ url: `${scheme}://extensions/?id=${chrome.runtime.id}` });
    sendResponse({ ok: true });
    return false;
  }

  if (message.type === "rescan_tabs") {
    reconcileExceptionSettings().then(() => sendResponse({ ok: true })).catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  return false;
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local" || !changes[EXCEPTION_SETTINGS_KEY]) return;
  exceptionSettings = CBDTGExceptions.normalizeSettings(changes[EXCEPTION_SETTINGS_KEY].newValue);
  exceptionSettingsPromise = Promise.resolve(exceptionSettings);
  scheduleExceptionReconcile();
});

chrome.alarms.create("coordinator-health", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm && alarm.name === "coordinator-health" && !nativeReady) connectNative().catch(() => {});
});

chrome.runtime.onInstalled.addListener(() => {
  connectNative().catch(() => {});
});

chrome.runtime.onStartup.addListener(() => {
  connectNative().catch(() => {});
});

ensureExceptionSettings().finally(() => connectNative().catch(() => {}));
