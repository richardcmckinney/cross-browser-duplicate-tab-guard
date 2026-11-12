"use strict";

// Real dialog DOM + real worker message handler; browser/coordinator APIs are isolated.
const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const extension = path.join(__dirname, "..", "extension");
const workerSource = fs.readFileSync(path.join(extension, "service_worker.js"), "utf8");
const settingsKey = "duplicateTabGuardExceptionSettingsV1";
const incident = { incident_id: "duplicate-one", url: "https://guard-test.example/article", current_browser: "chrome", copy_count: 2 };
const initialSettings = {
  schema: 1, enabled: true, pausedUntil: 0,
  rules: [{ id: "keep", enabled: true, type: "host", pattern: "reference.example", browser: "all", context: "all", note: "Keep reference", createdAt: 1 }],
};
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { if (browser) await browser.close(); });

function worker(options = {}) {
  const state = {
    clock: 2000000000000, events: {}, writes: [], nativeEvents: [], tabUpdates: [], removed: [], alarms: new Map(), claims: [],
    stored: { [settingsKey]: structuredClone(initialSettings) },
    tab: { id: 71, windowId: 4, url: options.chooser ? "chrome-extension://test-id/chooser.html#duplicate-one" : incident.url, incognito: false },
  };
  const event = (name) => ({ addListener(fn) { (state.events[name] ||= []).push(fn); } });
  const context = vm.createContext({
    console, URL, Map, Set, WeakMap, Promise,
    Date: class extends Date { static now() { return state.clock; } },
    crypto: { randomUUID: () => "test-session" },
    setTimeout: () => 1, clearTimeout: () => {}, setInterval: () => 1, clearInterval: () => {},
    state, initialIncident: structuredClone(incident),
    chrome: {
      runtime: { id: "test-id", getURL: (s) => "chrome-extension://test-id/" + s, onMessage: event("message"), onInstalled: event("installed"), onStartup: event("startup"), sendMessage: async () => {} },
      extension: { inIncognitoContext: false },
      tabs: {
        onUpdated: event("updated"), onRemoved: event("removed"), onReplaced: event("replaced"), onActivated: event("activated"),
        get: async () => structuredClone(state.tab),
        query: async () => [structuredClone(state.tab)],
        update: async (id, update) => { state.tabUpdates.push({ id, ...update }); Object.assign(state.tab, update); return state.tab; },
        remove: async (id) => state.removed.push(id),
        sendMessage: async () => ({}),
      },
      windows: { onFocusChanged: event("focus"), WINDOW_ID_NONE: -1 },
      storage: {
        onChanged: event("storage"),
        local: {
          get: async () => { if (options.readFailure) throw new Error("Storage unavailable"); return structuredClone(state.stored); },
          set: async (value) => {
            if (options.writeFailure) throw new Error("Storage write failed");
            state.writes.push(structuredClone(value));
            Object.assign(state.stored, structuredClone(value));
            for (const fn of state.events.storage || []) fn({ [settingsKey]: { newValue: value[settingsKey] } }, "local");
          },
        },
        session: { get: async () => ({}), set: async () => {} },
      },
      alarms: {
        create: async (name, value) => state.alarms.set(name, value),
        clear: async (name) => state.alarms.delete(name),
        onAlarm: event("alarm"),
      },
    },
  });
  context.importScripts = (name) => vm.runInContext(fs.readFileSync(path.join(extension, name), "utf8"), context);
  // Transport startup is covered by transport.test.cjs; leave all production handlers intact.
  vm.runInContext(workerSource.slice(0, workerSource.lastIndexOf("\nensureExceptionSettings().")), context);
  vm.runInContext("pendingIncidents.set(71, initialIncident); sendNativeEvent = (type, payload) => state.nativeEvents.push({ type, ...payload });", context);
  if (options.chooser) vm.runInContext("fallbackTabs.add(71)", context);
  state.request = (message, sender = { tab: { id: 71 } }) => new Promise((resolve) => {
    for (const fn of state.events.message) {
      const pending = fn(message, sender, resolve);
      if (pending || message.type === "chooser_get") return;
    }
    resolve({ ok: false, error: "Unknown message" });
  });
  state.context = context;
  return state;
}

async function dialog(t, kind, options = {}) {
  const state = worker({ ...options, chooser: kind === "chooser" });
  const context = await browser.newContext({ viewport: { width: 390, height: options.height || 850 }, colorScheme: options.dark ? "dark" : "light" });
  t.after(() => context.close());
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  await page.exposeFunction("workerRequest", (message) => state.request(message));
  await page.route("https://guard-test.example/**", (route) => {
    const name = new URL(route.request().url()).pathname.slice(1);
    if (name === "article") return route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Article</title><p>Reference page</p>" });
    const type = name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : "text/html";
    return route.fulfill({ contentType: type, body: fs.readFileSync(path.join(extension, name)) });
  });
  await page.addInitScript(() => {
    const listeners = [];
    window.chrome = { runtime: { sendMessage: (m) => window.workerRequest(m), onMessage: { addListener(fn) { listeners.push(fn); } } } };
    window.deliver = (m) => listeners.forEach((fn) => fn(m));
    // Permit automation to locate the otherwise closed extension-owned shadow root.
    const attach = Element.prototype.attachShadow;
    Element.prototype.attachShadow = function (options) { return attach.call(this, { ...options, mode: "open" }); };
  });
  await page.goto("https://guard-test.example/" + (kind === "chooser" ? "chooser.html" : "article"));
  if (kind === "overlay") {
    await page.addScriptTag({ url: "https://guard-test.example/content.js" });
    await page.evaluate((payload) => window.deliver({ type: "show_duplicate_dialog", payload }), incident);
  }
  const select = page.locator(kind === "chooser" ? "#pause-duration" : "#cbdtg-pause-duration");
  const pause = page.locator(kind === "chooser" ? "#pause" : "[data-role=pause]");
  await select.waitFor();
  return { page, state, select, pause };
}

for (const kind of ["overlay", "chooser"]) {
  for (const minutes of [15, 60, 1440]) {
    test(kind + " Pause persists " + minutes + " minutes and retains both tabs", async (t) => {
      const { page, state, select, pause } = await dialog(t, kind, { dark: minutes === 1440 });
      const choices = await select.locator("option").evaluateAll((nodes) => nodes.map((n) => ({ value: n.value, text: n.textContent })));
      const toolbar = fs.readFileSync(path.join(extension, "popup.html"), "utf8").match(/<select id="pauseDuration"[\s\S]*?<\/select>/)[0];
      assert.deepEqual(choices.map((c) => c.value), [...toolbar.matchAll(/value="(\d+)"/g)].map((m) => m[1]));
      await select.selectOption(String(minutes));
      await pause.click();
      if (kind === "chooser") await page.locator("#status.success").waitFor();
      else await page.locator("#__cross_browser_duplicate_tab_guard__").waitFor({ state: "detached" });
      assert.deepEqual(state.stored[settingsKey], { ...initialSettings, pausedUntil: state.clock + minutes * 60000 });
      assert.equal(state.alarms.get("guard-pause-expiry").when, state.clock + minutes * 60000);
      assert.deepEqual(state.removed, []);
      assert.equal(state.nativeEvents.some((event) => event.type === "cancel_incident" && event.reason === "guard_paused"), true);
      assert.equal(state.nativeEvents.some((event) => event.type === "resolve"), false);
      assert.equal(vm.runInContext("pendingIncidents.size", state.context), 0);
      if (kind === "chooser") {
        assert.equal(state.tab.url, incident.url);
        assert.equal(state.tabUpdates.length, 1);
      } else {
        assert.equal(await page.locator("#__cross_browser_duplicate_tab_guard__").count(), 0);
      }
      assert.equal(vm.runInContext("exceptionDecision(initialIncident.url, false).reason", state.context), "guard_paused");
      state.clock += minutes * 60000;
      assert.equal(vm.runInContext("exceptionDecision(initialIncident.url, false).excluded", state.context), false);
    });
  }

  test(kind + " failed storage write keeps dialog and permits retry", async (t) => {
    const { page, state, pause } = await dialog(t, kind, { writeFailure: true });
    await pause.click();
    const status = page.locator(kind === "chooser" ? "#status" : "[data-role=status]");
    await status.filter({ hasText: "Storage write failed" }).waitFor();
    assert.equal(await pause.isEnabled(), true);
    assert.deepEqual(state.stored[settingsKey], initialSettings);
    assert.equal(vm.runInContext("pendingIncidents.size", state.context), 1);
    assert.deepEqual(state.tabUpdates, []);
    assert.deepEqual(state.nativeEvents, []);
  });
}

test("worker rejects stale incidents, changed tabs, unsupported durations, and failed reads without writes", async () => {
  for (const [options, message, changedUrl] of [
    [{}, { incident_id: "stale", minutes: 60 }],
    [{}, { incident_id: incident.incident_id, minutes: 60 }, "https://different.example/"],
    [{}, { incident_id: incident.incident_id, minutes: -1 }],
    [{}, { incident_id: incident.incident_id, minutes: 1 }],
    [{}, { incident_id: incident.incident_id, minutes: "60" }],
    [{ readFailure: true }, { incident_id: incident.incident_id, minutes: 60 }],
  ]) {
    const state = worker(options);
    if (changedUrl) state.tab.url = changedUrl;
    const result = await state.request({ type: "pause_duplicate", ...message });
    assert.equal(result.ok, false);
    assert.deepEqual(state.writes, []);
    assert.deepEqual(state.nativeEvents, []);
  }
  const state = worker();
  assert.equal((await state.request({ type: "pause_duplicate", incident_id: incident.incident_id, minutes: 60 }, {})).ok, false);
});

test("expiry alarm reconciles existing tabs and pause schedule is restorable", async () => {
  const state = worker();
  await state.request({ type: "pause_duplicate", incident_id: incident.incident_id, minutes: 15 });
  state.alarms.clear();
  vm.runInContext("schedulePauseExpiry()", state.context);
  assert.equal(state.alarms.get("guard-pause-expiry").when, state.clock + 15 * 60000);
  vm.runInContext("claimTab = async (...args) => state.claims.push(args)", state.context);
  state.clock += 15 * 60000;
  for (const fn of state.events.alarm) fn({ name: "guard-pause-expiry" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(state.claims.length, 1);
  assert.deepEqual(Array.from(state.claims[0]), [71, incident.url, "exception_settings_changed", true]);
  state.stored[settingsKey].pausedUntil = 0;
  for (const fn of state.events.storage) fn({ [settingsKey]: { newValue: state.stored[settingsKey] } }, "local");
  assert.equal(state.alarms.has("guard-pause-expiry"), false);
});

test("pause controls remain reachable in short viewports", async (t) => {
  for (const kind of ["overlay", "chooser"]) {
    for (const dark of [false, true]) {
      const { page, pause, state } = await dialog(t, kind, { height: 420, dark });
      if (kind === "overlay") {
        const bounds = await page.locator(".dialog").boundingBox();
        assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= 420, "Dialog stays inside viewport");
      }
      await pause.scrollIntoViewIfNeeded();
      if (process.env.PAUSE_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.PAUSE_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.PAUSE_SCREENSHOT_DIR, kind + (dark ? "-dark" : "-light") + ".jpg"), type: "jpeg", quality: 70 });
      }
      await pause.click();
      assert.equal(state.stored[settingsKey].pausedUntil, state.clock + 60 * 60000);
    }
  }
});


