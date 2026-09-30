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

function setNote(message, kind = "") {
  noteEl.textContent = String(message || "");
  noteEl.className = `note ${kind}`.trim();
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
  settings = Rules.normalizeSettings(next);
  await chrome.storage.local.set({ [Rules.STORAGE_KEY]: settings });
  renderSettings();
  setNote(message, "success");
}

function ruleKey(rule) {
  return [rule.type, rule.pattern, rule.browser, rule.context].join("\u0000");
}

async function addOrUpdateRule(candidate, message) {
  const validation = Rules.validateRule(candidate);
  if (!validation.ok) throw new Error(validation.error);
  const rule = validation.rule;
  const rules = [...settings.rules];

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
      const rules = settings.rules.map((itemRule) => itemRule.id === rule.id ? { ...itemRule, enabled: toggle.checked } : itemRule);
      await saveSettings({ ...settings, rules }, toggle.checked ? "Exception enabled." : "Exception disabled.");
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
      const rules = settings.rules.filter((itemRule) => itemRule.id !== rule.id);
      if (editingRuleId === rule.id) resetRuleForm();
      await saveSettings({ ...settings, rules }, "Exception deleted.");
    });
    actions.append(edit, remove);
    item.append(toggle, content, actions);
    rulesListEl.append(item);
  }
}

function renderSettings() {
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
  exceptionCountEl.textContent = `${activeRuleCount()} active / ${settings.rules.length} total`;
  renderRules();
}

function renderActiveContext() {
  currentUrlEl.textContent = activeContext.trackable ? activeContext.url : "This page cannot be added as an exception.";
  addExactEl.disabled = !activeContext.trackable;
  addHostEl.disabled = !activeContext.trackable || !/^https?:\/\//i.test(activeContext.url);
  useCurrentEl.disabled = !activeContext.trackable;
}

async function refresh() {
  try {
    const [status, stored, current] = await Promise.all([
      chrome.runtime.sendMessage({ type: "get_status" }),
      chrome.storage.local.get([Rules.STORAGE_KEY]),
      chrome.runtime.sendMessage({ type: "get_active_tab_context" }),
    ]);
    if (!status || !status.ok) throw new Error((status && status.error) || "Status unavailable.");

    settings = Rules.normalizeSettings(stored[Rules.STORAGE_KEY]);
    activeContext = current && current.ok ? current : activeContext;
    connectionEl.textContent = status.native_connected ? "Connected" : "Disconnected";
    connectionEl.className = `pill ${status.native_connected ? "ok" : "error"}`;
    browserEl.textContent = labels[status.browser] || status.browser || "Unknown";
    incognitoEl.textContent = status.incognito_allowed ? "Allowed" : "Enable in permissions";
    filesEl.textContent = status.file_scheme_allowed ? "Allowed" : "Enable in permissions";
    pendingEl.textContent = String(status.pending_incidents || 0);
    overrideEl.value = status.browser_override || "auto";
    renderSettings();
    renderActiveContext();

    if (!status.incognito_allowed || !status.file_scheme_allowed) {
      setNote("Use Permissions to enable Allow in Incognito/Private and Allow access to file URLs. Browsers require those toggles to be set by the user.");
    } else if (!status.native_connected) {
      setNote(status.native_error || "Run the one-file installer again to repair the native coordinator.", "error");
    } else if (!noteEl.textContent || noteEl.classList.contains("error")) {
      setNote("Exact-URL coordination is active. Exception rules are stored locally in this browser profile.");
    }
  } catch (error) {
    connectionEl.textContent = "Error";
    connectionEl.className = "pill error";
    setNote(error && error.message ? error.message : String(error), "error");
  }
}

async function addCurrent(type) {
  if (!activeContext.trackable) throw new Error("The current page cannot be used as an exception.");
  const pattern = Rules.suggestPattern(activeContext.url, type);
  await addOrUpdateRule({ type, pattern, browser: "all", context: "all", enabled: true }, type === "exact" ? "Exact URL excluded." : "Hostname excluded.");
}

async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
  } catch (_) {
    importAreaEl.hidden = false;
    importTextEl.value = value;
    importTextEl.focus();
    importTextEl.select();
    if (!document.execCommand("copy")) throw new Error("Could not copy automatically. The JSON is selected for manual copying.");
  }
}

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
  try {
    await saveSettings({ ...settings, enabled: guardEnabledEl.checked, pausedUntil: guardEnabledEl.checked ? settings.pausedUntil : 0 }, guardEnabledEl.checked ? "Duplicate guard enabled." : "Duplicate guard disabled.");
  } catch (error) {
    setNote(error.message, "error");
  }
});

pauseGuardEl.addEventListener("click", async () => {
  const minutes = Math.max(1, Number(pauseDurationEl.value) || 60);
  await saveSettings({ ...settings, pausedUntil: Date.now() + minutes * 60000 }, `Duplicate handling paused for ${minutes < 60 ? `${minutes} minutes` : `${minutes / 60} hour${minutes === 60 ? "" : "s"}`}.`);
});

resumeGuardEl.addEventListener("click", async () => {
  await saveSettings({ ...settings, pausedUntil: 0 }, "Duplicate handling resumed.");
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
  chrome.runtime.sendMessage({ type: "open_extension_details" });
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
  if (areaName !== "local" || !changes[Rules.STORAGE_KEY]) return;
  settings = Rules.normalizeSettings(changes[Rules.STORAGE_KEY].newValue);
  renderSettings();
});

statusRefreshTimer = setInterval(() => {
  if (settings.pausedUntil > 0 && settings.pausedUntil <= Date.now()) {
    settings = { ...settings, pausedUntil: 0 };
    chrome.storage.local.set({ [Rules.STORAGE_KEY]: settings }).catch(() => {});
  }
  renderSettings();
}, 15000);

window.addEventListener("unload", () => clearInterval(statusRefreshTimer));
refresh();
