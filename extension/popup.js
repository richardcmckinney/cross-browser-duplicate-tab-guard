"use strict";

const Rules = CBDTGExceptions;
const labels = { brave: "Brave", chrome: "Chrome", chromium: "Chromium", all: "All browsers" };
const contextLabels = { all: "Regular + private", regular: "Regular only", incognito: "Private only" };

const connectionEl = document.getElementById("connection");
const guardStatusEl = document.getElementById("guardStatus");
const browserEl = document.getElementById("browser");
const incognitoEl = document.getElementById("incognito");
const filesEl = document.getElementById("files");
const exceptionCountEl = document.getElementById("exceptionCount");
const pendingEl = document.getElementById("pending");
const overrideEl = document.getElementById("browserOverride");
const noteEl = document.getElementById("note");
const detailsEl = document.getElementById("details");
const rescanEl = document.getElementById("rescan");
const guardEnabledEl = document.getElementById("guardEnabled");
const pauseDurationEl = document.getElementById("pauseDuration");
const pauseGuardEl = document.getElementById("pauseGuard");
const resumeGuardEl = document.getElementById("resumeGuard");
const guardMessageEl = document.getElementById("guardMessage");
const currentTabIdEl = document.getElementById("currentTabId");
const currentTitleEl = document.getElementById("currentTitle");
const tabContextEl = document.getElementById("tabContext");
const copyTabEl = document.getElementById("copyTab");
const copyTabLabelEl = document.getElementById("copyTabLabel");
const tabCopyStatusEl = document.getElementById("tabCopyStatus");
const currentUrlEl = document.getElementById("currentUrl");
const addExactEl = document.getElementById("addExact");
const addHostEl = document.getElementById("addHost");
const summaryCountEl = document.getElementById("summaryCount");
const rulesCountEl = document.getElementById("rulesCount");
const rulesEmptyEl = document.getElementById("rulesEmpty");
const rulesListEl = document.getElementById("rulesList");
const customRuleDetailsEl = document.getElementById("customRuleDetails");
const customRuleSummaryEl = document.getElementById("customRuleSummary");
const ruleFormEl = document.getElementById("ruleForm");
const ruleTypeEl = document.getElementById("ruleType");
const rulePatternEl = document.getElementById("rulePattern");
const ruleBrowserEl = document.getElementById("ruleBrowser");
const ruleContextEl = document.getElementById("ruleContext");
const ruleNoteEl = document.getElementById("ruleNote");
const useCurrentEl = document.getElementById("useCurrent");
const cancelEditEl = document.getElementById("cancelEdit");
const saveRuleEl = document.getElementById("saveRule");
const copyRulesEl = document.getElementById("copyRules");
const showImportEl = document.getElementById("showImport");
const importAreaEl = document.getElementById("importArea");
const importTextEl = document.getElementById("importText");
const importModeEl = document.getElementById("importMode");
const applyImportEl = document.getElementById("applyImport");

let settings = Rules.defaultSettings();
let activeContext = { url: "", browser: "", incognito: false, trackable: false };
let editingRuleId = "";
let statusRefreshTimer = null;
let activeTab = null;
let popupWindowId = null;
let tabRevision = 0;
let settingsRevision = 0;
let statusRevision = 0;
let renderedRules = "";
let savingSettings = false;
let settingsLoaded = false;
let browserIdentity = "";
let guardIdentity = {};
let copyResetTimer = null;
let statusNoteState = "";
let copyingTab = false;

function setNote(message, kind = "", source = "user") {
  noteEl.textContent = String(message || "");
  noteEl.className = `note ${kind}`.trim();
  noteEl.dataset.source = source;
  statusNoteState = source === "status" ? noteEl.textContent : "";
}

function formatRemaining(timestamp) {
  const milliseconds = Math.max(0, Number(timestamp || 0) - Date.now());
  const minutes = Math.ceil(milliseconds / 60000);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"}`;
  const hours = Math.ceil(minutes / 60);
  return `${hours} hour${hours === 1 ? "" : "s"}`;
}

function guardStateText() {
  if (!settings.enabled) return "Disabled";
  if (settings.pausedUntil > Date.now()) return `Paused (${formatRemaining(settings.pausedUntil)})`;
  return "Active";
}

function activeRuleCount() {
  return settings.rules.filter((rule) => rule.enabled).length;
}

async function saveSettings(next, message = "Settings saved.") {
  if (!settingsLoaded) throw new Error("Saved settings are not available yet. Reopen the popup to try again.");
  if (savingSettings) throw new Error("A settings change is still saving. Please try again.");
  const normalized = Rules.normalizeSettings(next);
  savingSettings = true;
  try {
    // Keep the last confirmed state if storage rejects a write.
    await chrome.storage.local.set({ [Rules.STORAGE_KEY]: normalized });
    settings = normalized;
    settingsRevision += 1;
    renderSettings();
    setNote(message, "success");
  } finally {
    savingSettings = false;
  }
}

function reportSaveError(error) {
  renderSettings();
  setNote(error && error.message ? error.message : String(error), "error");
}

function ruleKey(rule) {
  return [rule.type, rule.pattern, rule.browser, rule.context].join("\u0000");
}

async function addOrUpdateRule(candidate, message) {
  const validation = Rules.validateRule(candidate);
  if (!validation.ok) throw new Error(validation.error);
  const rule = validation.rule;
  const rules = settings.rules.map((item) => ({ ...item }));

  if (editingRuleId) {
    const index = rules.findIndex((item) => item.id === editingRuleId);
    if (index < 0) throw new Error("That exception no longer exists.");
    rule.id = editingRuleId;
    rule.createdAt = rules[index].createdAt;
    rules[index] = rule;
  } else {
    const duplicate = rules.find((item) => ruleKey(item) === ruleKey(rule));
    if (duplicate) {
      duplicate.enabled = true;
      if (rule.note) duplicate.note = rule.note;
      await saveSettings({ ...settings, rules }, "That exception already existed and is now enabled.");
      return;
    }
    rules.unshift(rule);
  }

  if (rules.length > Rules.MAX_RULES) throw new Error(`A maximum of ${Rules.MAX_RULES} rules is supported.`);
  await saveSettings({ ...settings, rules }, message || (editingRuleId ? "Exception updated." : "Exception added."));
  resetRuleForm();
}

function resetRuleForm() {
  editingRuleId = "";
  ruleFormEl.reset();
  ruleTypeEl.value = "exact";
  ruleBrowserEl.value = "all";
  ruleContextEl.value = "all";
  cancelEditEl.hidden = true;
  saveRuleEl.textContent = "Add exception";
  customRuleSummaryEl.textContent = "Add a custom rule";
}

function editRule(rule) {
  editingRuleId = rule.id;
  ruleTypeEl.value = rule.type;
  rulePatternEl.value = rule.pattern;
  ruleBrowserEl.value = rule.browser;
  ruleContextEl.value = rule.context;
  ruleNoteEl.value = rule.note || "";
  cancelEditEl.hidden = false;
  saveRuleEl.textContent = "Save changes";
  customRuleSummaryEl.textContent = "Edit exception rule";
  customRuleDetailsEl.open = true;
  rulePatternEl.focus();
}

function badge(text) {
  const element = document.createElement("span");
  element.className = "badge";
  element.textContent = text;
  return element;
}

function renderRules() {
  // Status/countdown updates must not replace rule controls or steal keyboard focus.
  const signature = JSON.stringify(settings.rules);
  if (signature === renderedRules) return;
  renderedRules = signature;
  rulesListEl.replaceChildren();
  rulesEmptyEl.hidden = settings.rules.length > 0;
  rulesCountEl.textContent = String(settings.rules.length);
  summaryCountEl.textContent = String(activeRuleCount());

  for (const rule of settings.rules) {
    const item = document.createElement("article");
    item.className = `rule-item${rule.enabled ? "" : " disabled"}`;

    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.className = "rule-toggle";
    toggle.checked = rule.enabled;
    toggle.title = rule.enabled ? "Disable this rule" : "Enable this rule";
    toggle.setAttribute("aria-label", `${rule.enabled ? "Disable" : "Enable"} ${rule.pattern}`);
    toggle.addEventListener("change", async () => {
      toggle.disabled = true;
      try {
        const rules = settings.rules.map((itemRule) => itemRule.id === rule.id ? { ...itemRule, enabled: toggle.checked } : itemRule);
        await saveSettings({ ...settings, rules }, toggle.checked ? "Exception enabled." : "Exception disabled.");
      } catch (error) {
        toggle.checked = settings.rules.find((itemRule) => itemRule.id === rule.id)?.enabled !== false;
        reportSaveError(error);
      } finally {
        toggle.disabled = false;
      }
    });

    const content = document.createElement("div");
    content.className = "rule-content";
    const pattern = document.createElement("p");
    pattern.className = "rule-pattern";
    pattern.textContent = rule.pattern;
    content.append(pattern);
    if (rule.note) {
      const note = document.createElement("p");
      note.className = "rule-note";
      note.textContent = rule.note;
      content.append(note);
    }
    const meta = document.createElement("div");
    meta.className = "rule-meta";
    meta.append(
      badge(Rules.TYPES[rule.type] || rule.type),
      badge(labels[rule.browser] || rule.browser),
      badge(contextLabels[rule.context] || rule.context),
    );
    content.append(meta);

    const actions = document.createElement("div");
    actions.className = "rule-actions";
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "icon-button";
    edit.textContent = "Edit";
    edit.addEventListener("click", () => editRule(rule));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-button danger";
    remove.textContent = "Delete";
    remove.addEventListener("click", async () => {
      remove.disabled = true;
      try {
        const rules = settings.rules.filter((itemRule) => itemRule.id !== rule.id);
        await saveSettings({ ...settings, rules }, "Exception deleted.");
        if (editingRuleId === rule.id) resetRuleForm();
      } catch (error) {
        reportSaveError(error);
      } finally {
        remove.disabled = false;
      }
    });
    actions.append(edit, remove);
    item.append(toggle, content, actions);
    rulesListEl.append(item);
  }
}

function renderGuardState() {
  if (!settingsLoaded) return;
  const paused = settings.enabled && settings.pausedUntil > Date.now();
  guardEnabledEl.checked = settings.enabled;
  pauseGuardEl.hidden = paused || !settings.enabled;
  resumeGuardEl.hidden = !paused;
  pauseDurationEl.disabled = paused || !settings.enabled;
  guardMessageEl.textContent = !settings.enabled
    ? "The guard is disabled; open tabs remain untouched."
    : paused
      ? `Duplicate handling resumes in ${formatRemaining(settings.pausedUntil)}.`
      : "Rules apply immediately to new and already-open tabs.";
  guardStatusEl.textContent = guardStateText();
}

function renderSettings() {
  renderGuardState();
  exceptionCountEl.textContent = `${activeRuleCount()} active / ${settings.rules.length} total`;
  renderRules();
}

function renderActiveContext() {
  const available = Boolean(activeTab && Number.isInteger(activeTab.id));
  currentTabIdEl.textContent = available ? String(activeTab.id) : "—";
  currentTabIdEl.setAttribute("aria-label", available ? "Current tab ID " + activeTab.id : "Current tab ID unavailable");
  currentTitleEl.textContent = available ? (activeTab.title || "Untitled tab") : "No active tab available";
  currentTitleEl.title = currentTitleEl.textContent;
  copyTabEl.disabled = !available;
  tabContextEl.textContent = available
    ? (labels[browserIdentity] || "Browser identifying…") + " · Window " + activeTab.windowId + " · " + (activeTab.incognito ? "Private" : "Regular")
    : "Tab reference unavailable";
  tabContextEl.title = "Window is the browser API window ID, not the operating-system window ID.";
  currentUrlEl.textContent = activeContext.url || (available ? "This page has no URL." : "The active tab could not be read.");
  currentUrlEl.title = currentUrlEl.textContent;
  addExactEl.disabled = !activeContext.trackable;
  addHostEl.disabled = !activeContext.trackable || !/^https?:\/\//i.test(activeContext.url);
  useCurrentEl.disabled = !activeContext.trackable;
}

function setCopyStatus(message = "Includes browser, window and title.", kind = "") {
  clearTimeout(copyResetTimer);
  tabCopyStatusEl.textContent = message;
  tabCopyStatusEl.className = "copy-status" + (kind ? " " + kind : "");
  copyTabLabelEl.textContent = kind === "success" ? "Copied" : "Copy ID";
}

function acceptActiveTab(tab) {
  const revision = ++tabRevision;
  activeTab = tab && Number.isInteger(tab.id) && tab.id >= 0 ? tab : null;
  if (activeTab && popupWindowId === null) popupWindowId = activeTab.windowId;
  activeContext = { url: activeTab ? (activeTab.pendingUrl || activeTab.url || "") : "", trackable: false };
  setCopyStatus();
  renderActiveContext();
  if (!activeTab) return;
  // Exclusion decisions may need the chooser's original URL. They never gate ID/title rendering.
  const tabId = activeTab.id;
  chrome.runtime.sendMessage({ type: "get_active_tab_context", tab_id: tabId }).then((context) => {
    if (revision !== tabRevision || !context || !context.ok || context.tab_id !== tabId || context.window_id !== popupWindowId) return;
    activeContext = context;
    renderActiveContext();
  }).catch(() => {}); // The tab reference remains usable without a running coordinator/worker.
}

async function refreshActiveTab() {
  const revision = ++tabRevision;
  try {
    const tabs = await chrome.tabs.query(popupWindowId === null
      ? { active: true, currentWindow: true }
      : { active: true, windowId: popupWindowId });
    if (revision !== tabRevision) return;
    acceptActiveTab(tabs[0] || null);
  } catch (_) {
    if (revision === tabRevision) acceptActiveTab(null);
  }
}

function browserFromHints() {
  const brands = (navigator.userAgentData && navigator.userAgentData.brands || []).map((item) => item.brand);
  if (brands.some((brand) => /brave/i.test(brand))) return "brave";
  if (brands.some((brand) => /google chrome/i.test(brand))) return "chrome";
  if (brands.some((brand) => /^chromium$/i.test(brand))) return "chromium";
  if (/brave/i.test(navigator.userAgent)) return "brave";
  if (/chromium/i.test(navigator.userAgent)) return "chromium";
  // Chrome-compatible user agents alone cannot reliably distinguish browser families.
  return "";
}

async function loadSettings() {
  const revision = settingsRevision;
  try {
    const stored = await chrome.storage.local.get([Rules.STORAGE_KEY, "browserOverride", "profileId"]);
    if (revision === settingsRevision) {
      settings = Rules.normalizeSettings(stored[Rules.STORAGE_KEY]);
      settingsLoaded = true;
      renderSettings();
    }
    if (labels[stored.browserOverride] && stored.browserOverride !== "all") browserIdentity = stored.browserOverride;
    if (stored.profileId) guardIdentity.profile_id = stored.profileId;
    if (document.activeElement !== overrideEl && !overrideEl.disabled) {
      overrideEl.value = stored.browserOverride || "auto";
    }
    renderActiveContext();
  } catch (error) {
    setNote("Could not read saved settings: " + error.message, "error");
  }
}

async function refreshStatus() {
  const revision = ++statusRevision;
  try {
    const status = await chrome.runtime.sendMessage({ type: "get_status" });
    if (revision !== statusRevision) return;
    if (!status || !status.ok) throw new Error((status && status.error) || "Status unavailable.");
    connectionEl.textContent = status.native_connected ? "Connected" : "Disconnected";
    connectionEl.className = "pill " + (status.native_connected ? "ok" : "error");
    browserIdentity = status.browser || browserIdentity;
    guardIdentity = {
      profile_id: status.profile_id || guardIdentity.profile_id,
      session_id: status.session_id || guardIdentity.session_id,
    };
    browserEl.textContent = labels[browserIdentity] || "Unknown";
    incognitoEl.textContent = status.incognito_allowed ? "Allowed" : "Enable in permissions";
    filesEl.textContent = status.file_scheme_allowed ? "Allowed" : "Enable in permissions";
    pendingEl.textContent = String(status.pending_incidents || 0);
    if (document.activeElement !== overrideEl && !overrideEl.disabled) {
      overrideEl.value = status.browser_override || "auto";
    }
    renderActiveContext();
    if (!status.native_connected) {
      setNote(status.native_error || "The local tab coordinator is disconnected.", "error", "status");
    } else if (!status.incognito_allowed || !status.file_scheme_allowed) {
      if (!noteEl.textContent || statusNoteState === noteEl.textContent) {
        setNote("Use Permissions to enable Allow in Incognito/Private and Allow access to file URLs.", "", "status");
      }
    } else if (!noteEl.textContent || statusNoteState === noteEl.textContent) {
      setNote("Exact-URL coordination is active. Exception rules are stored locally in this browser profile.", "", "status");
    }
  } catch (error) {
    if (revision !== statusRevision) return;
    connectionEl.textContent = "Unavailable";
    connectionEl.className = "pill error";
    setNote(error && error.message ? error.message : String(error), "error", "status");
  }
}

function refresh() {
  refreshActiveTab();
  refreshStatus();
}

function tabReference() {
  if (!activeTab || !Number.isInteger(activeTab.id)) throw new Error("The active tab is unavailable.");
  const lines = [
    "Tab ID: " + activeTab.id,
    "Browser: " + (labels[browserIdentity] || "Unknown Chromium-based browser"),
    "Window ID (browser API): " + activeTab.windowId,
    "Context: " + (activeTab.incognito ? "Private / incognito" : "Regular"),
    "Title: " + (activeTab.title || "Untitled tab"),
  ];
  if (guardIdentity.profile_id) lines.push("Guard profile ID: " + guardIdentity.profile_id);
  if (guardIdentity.session_id) lines.push("Guard session ID: " + guardIdentity.session_id + " (extension worker lifetime, not browser session)");
  lines.push("Tab/window IDs are browser-scoped, not OS window IDs; recheck after browser restart.");
  return lines.join("\n");
}

async function addCurrent(type) {
  if (!activeContext.trackable) throw new Error("The current page cannot be used as an exception.");
  const pattern = Rules.suggestPattern(activeContext.url, type);
  await addOrUpdateRule({ type, pattern, browser: "all", context: "all", enabled: true }, type === "exact" ? "Exact URL excluded." : "Hostname excluded.");
}

async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
    return;
  } catch (_) {
    // Use a disposable buffer, preserving the import draft, selection and focused control.
    const focused = document.activeElement;
    const selection = window.getSelection();
    const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index).cloneRange()) : [];
    const inputSelection = focused && typeof focused.selectionStart === "number"
      ? [focused.selectionStart, focused.selectionEnd, focused.selectionDirection] : null;
    const buffer = document.createElement("textarea");
    buffer.className = "clipboard-buffer";
    buffer.value = value;
    buffer.setAttribute("aria-hidden", "true");
    document.body.append(buffer);
    let copied = false;
    try {
      buffer.focus({ preventScroll: true });
      buffer.select();
      copied = document.execCommand("copy");
    } finally {
      buffer.remove();
      if (focused && focused.isConnected) focused.focus({ preventScroll: true });
      if (inputSelection) focused.setSelectionRange(...inputSelection);
      if (selection) {
        selection.removeAllRanges();
        for (const range of ranges) selection.addRange(range);
      }
    }
    if (!copied) throw new Error("Clipboard unavailable. Please try copying again.");
  }
}

copyTabEl.addEventListener("click", async () => {
  if (copyingTab) return;
  const revision = tabRevision;
  copyingTab = true;
  copyTabEl.setAttribute("aria-busy", "true");
  try {
    await copyText(tabReference());
    if (revision === tabRevision) {
      setCopyStatus("Tab reference copied.", "success");
      copyResetTimer = setTimeout(() => setCopyStatus(), 2500);
    }
  } catch (error) {
    if (revision === tabRevision) setCopyStatus("Clipboard unavailable. Select the tab ID to copy manually.", "error");
  } finally {
    copyingTab = false;
    copyTabEl.removeAttribute("aria-busy");
    copyTabEl.disabled = !activeTab;
  }
});

overrideEl.addEventListener("change", async () => {
  overrideEl.disabled = true;
  try {
    const result = await chrome.runtime.sendMessage({ type: "set_browser_override", value: overrideEl.value });
    if (!result || !result.ok) throw new Error((result && result.error) || "Could not save browser identity.");
    setNote("Browser identity updated.", "success");
    setTimeout(refresh, 500);
  } catch (error) {
    setNote(error && error.message ? error.message : String(error), "error");
  } finally {
    overrideEl.disabled = false;
  }
});

guardEnabledEl.addEventListener("change", async () => {
  guardEnabledEl.disabled = true;
  try {
    await saveSettings({ ...settings, enabled: guardEnabledEl.checked, pausedUntil: guardEnabledEl.checked ? settings.pausedUntil : 0 }, guardEnabledEl.checked ? "Duplicate guard enabled." : "Duplicate guard disabled.");
  } catch (error) {
    reportSaveError(error);
  } finally {
    guardEnabledEl.disabled = false;
  }
});

pauseGuardEl.addEventListener("click", async () => {
  pauseGuardEl.disabled = true;
  try {
    const minutes = Math.max(1, Number(pauseDurationEl.value) || 60);
    await saveSettings({ ...settings, pausedUntil: Date.now() + minutes * 60000 }, "Duplicate handling paused for " + (minutes < 60 ? minutes + " minutes" : minutes / 60 + " hour" + (minutes === 60 ? "" : "s")) + ".");
  } catch (error) {
    reportSaveError(error);
  } finally {
    pauseGuardEl.disabled = false;
  }
});

resumeGuardEl.addEventListener("click", async () => {
  resumeGuardEl.disabled = true;
  try {
    await saveSettings({ ...settings, pausedUntil: 0 }, "Duplicate handling resumed.");
  } catch (error) {
    reportSaveError(error);
  } finally {
    resumeGuardEl.disabled = false;
  }
});

addExactEl.addEventListener("click", () => addCurrent("exact").catch((error) => setNote(error.message, "error")));
addHostEl.addEventListener("click", () => addCurrent("host").catch((error) => setNote(error.message, "error")));

useCurrentEl.addEventListener("click", () => {
  if (!activeContext.trackable) return;
  rulePatternEl.value = Rules.suggestPattern(activeContext.url, ruleTypeEl.value);
  rulePatternEl.focus();
});

ruleTypeEl.addEventListener("change", () => {
  if (!rulePatternEl.value && activeContext.trackable) {
    rulePatternEl.placeholder = Rules.suggestPattern(activeContext.url, ruleTypeEl.value);
  }
});

ruleFormEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  saveRuleEl.disabled = true;
  try {
    await addOrUpdateRule({
      id: editingRuleId || undefined,
      enabled: true,
      type: ruleTypeEl.value,
      pattern: rulePatternEl.value,
      browser: ruleBrowserEl.value,
      context: ruleContextEl.value,
      note: ruleNoteEl.value,
    });
  } catch (error) {
    setNote(error && error.message ? error.message : String(error), "error");
  } finally {
    saveRuleEl.disabled = false;
  }
});

cancelEditEl.addEventListener("click", resetRuleForm);

copyRulesEl.addEventListener("click", async () => {
  const payload = {
    format: "CrossBrowserDuplicateTabGuard-exceptions",
    version: 1,
    exportedAt: new Date().toISOString(),
    settings,
  };
  try {
    await copyText(JSON.stringify(payload, null, 2));
    setNote("Exception rules copied as JSON.", "success");
  } catch (error) {
    setNote(error.message, "error");
  }
});

showImportEl.addEventListener("click", () => {
  importAreaEl.hidden = !importAreaEl.hidden;
  if (!importAreaEl.hidden) importTextEl.focus();
});

applyImportEl.addEventListener("click", async () => {
  applyImportEl.disabled = true;
  try {
    const parsed = JSON.parse(importTextEl.value);
    const incoming = Array.isArray(parsed)
      ? { ...Rules.defaultSettings(), rules: parsed }
      : parsed && parsed.settings
        ? parsed.settings
        : parsed;
    const normalized = Rules.normalizeSettings(incoming);
    if (!Array.isArray(incoming && incoming.rules)) throw new Error("The imported JSON does not contain a rules array.");

    let next = normalized;
    if (importModeEl.value === "merge") {
      const merged = [...settings.rules];
      const keys = new Set(merged.map(ruleKey));
      for (const rule of normalized.rules) {
        if (!keys.has(ruleKey(rule))) {
          merged.push(rule);
          keys.add(ruleKey(rule));
        }
      }
      next = { ...settings, rules: merged.slice(0, Rules.MAX_RULES) };
    }
    await saveSettings(next, `${normalized.rules.length} imported rule${normalized.rules.length === 1 ? "" : "s"} processed.`);
    importTextEl.value = "";
    importAreaEl.hidden = true;
  } catch (error) {
    setNote(error && error.message ? error.message : String(error), "error");
  } finally {
    applyImportEl.disabled = false;
  }
});

detailsEl.addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "open_extension_details" }).catch((error) => setNote(error.message, "error"));
});

rescanEl.addEventListener("click", async () => {
  rescanEl.disabled = true;
  try {
    const result = await chrome.runtime.sendMessage({ type: "rescan_tabs" });
    if (!result || !result.ok) throw new Error((result && result.error) || "Rescan failed.");
    setNote("Open tabs and exception rules were reconciled.", "success");
  } catch (error) {
    setNote(error && error.message ? error.message : String(error), "error");
  } finally {
    rescanEl.disabled = false;
    setTimeout(refresh, 600);
  }
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local") return;
  if (changes[Rules.STORAGE_KEY]) {
    settingsRevision += 1;
    settings = Rules.normalizeSettings(changes[Rules.STORAGE_KEY].newValue);
    settingsLoaded = true;
    renderSettings();
  }
  if (changes.browserOverride) refreshStatus();
});

chrome.tabs.onActivated.addListener(({ windowId }) => {
  // An activation before the first query resolves invalidates that pending snapshot too.
  if (popupWindowId !== null && windowId !== popupWindowId) return;
  acceptActiveTab(null);
  refreshActiveTab();
});
chrome.tabs.onUpdated.addListener((tabId, changes, tab) => {
  if (!("title" in changes || "url" in changes || "pendingUrl" in changes)) return;
  if (!activeTab) {
    if (tab.active && (popupWindowId === null || tab.windowId === popupWindowId)) refreshActiveTab();
    return;
  }
  if (tabId === activeTab.id && tab.windowId === popupWindowId) acceptActiveTab(tab);
});

function handleTabUnavailable(tabId, windowId) {
  if (activeTab ? tabId !== activeTab.id : popupWindowId !== null && windowId !== popupWindowId) return;
  acceptActiveTab(null);
  refreshActiveTab();
}

chrome.tabs.onRemoved.addListener((tabId, info) => handleTabUnavailable(tabId, info && info.windowId));
chrome.tabs.onDetached.addListener((tabId, info) => handleTabUnavailable(tabId, info && info.oldWindowId));

// Poll the worker while the popup is open so transport recovery is visible without reopening it.
statusRefreshTimer = setInterval(() => {
  renderGuardState();
  refreshStatus();
}, 15000);

window.addEventListener("unload", () => {
  clearInterval(statusRefreshTimer);
  clearTimeout(copyResetTimer);
});
browserIdentity = browserFromHints();
if (navigator.brave && typeof navigator.brave.isBrave === "function") {
  navigator.brave.isBrave().then((isBrave) => {
    if (isBrave && overrideEl.value === "auto") {
      browserIdentity = "brave";
      renderActiveContext();
    }
  }).catch(() => {});
}
loadSettings();
refresh();
