"use strict";

// Exercises the actual popup DOM and browser API boundaries, without a coordinator or user profile.
const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = path.resolve(__dirname, "..");
const extension = path.join(root, "extension");
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { if (browser) await browser.close(); });

async function popup(t, options = {}) {
  const context = await browser.newContext({ viewport: { width: 390, height: 600 }, colorScheme: options.dark ? "dark" : "light" });
  t.after(() => context.close());
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, [], "No uncaught popup errors"));
  await page.route("https://guard-test.example/**", (route) => {
    const filename = new URL(route.request().url()).pathname.slice(1);
    const type = filename.endsWith(".js") ? "text/javascript" : filename.endsWith(".css") ? "text/css" : "text/html";
    return route.fulfill({ contentType: type, body: fs.readFileSync(path.join(extension, filename)) });
  });
  await page.addInitScript((options) => {
    const key = "duplicateTabGuardExceptionSettingsV1";
    const initialTab = { id: 712345, windowId: 83, active: true, title: options.title || "Project overview", url: "https://acme.example/project", incognito: Boolean(options.private) };
    const state = window.fixture = {
      tabs: options.noTab ? [] : [initialTab],
      calls: [], copied: [], writes: [], deferredContext: [], deferredQuery: [], timers: [], events: {}, statusSequence: options.statusSequence ? options.statusSequence.map((status) => structuredClone(status)) : null,
      stored: { profileId: "test-profile", browserOverride: "", [key]: { schema: 1, enabled: true, pausedUntil: options.paused ? Date.now() + 600000 : 0, rules: [{ id: "rule-one", enabled: true, type: "host", pattern: "acme.example", browser: "all", context: "all", note: "Keep reference pages", createdAt: 1 }] } },
    };
    const event = (name) => ({ addListener(fn) { (state.events[name] ||= []).push(fn); } });
    state.emit = (name, ...args) => (state.events[name] || []).forEach((fn) => fn(...args));
    window.setInterval = (fn) => { state.timers.push(fn); return state.timers.length; };
    window.clearInterval = () => {};
    Object.defineProperty(navigator, "userAgentData", { value: { brands: [{ brand: "Google Chrome" }, { brand: "Chromium" }] } });
    Object.defineProperty(navigator, "clipboard", { value: {
      writeText: async (text) => {
        if (options.clipboard !== "reject") { state.copied.push(text); return; }
        throw new Error("Clipboard denied");
      },
    } });
    document.execCommand = (command) => {
      if (command !== "copy" || options.fallback === false) return false;
      state.copied.push(document.activeElement.value);
      return true;
    };
    window.chrome = {
      runtime: {
        sendMessage: async (message) => {
          state.calls.push(message);
          if (message.type === "get_status") {
            if (options.status === "pending") return new Promise(() => {});
            if (options.status === "reject") throw new Error("Worker unavailable");
            if (state.statusSequence && state.statusSequence.length) return state.statusSequence.shift();
            return { ok: true, browser: "chrome", browser_override: "auto", native_connected: false, incognito_allowed: true, file_scheme_allowed: true, profile_id: "test-profile", session_id: "test-worker-session" };
          }
          if (message.type === "get_active_tab_context") {
            const tab = state.tabs.find((tab) => tab.id === message.tab_id);
            const response = { ok: true, tab_id: tab && tab.id, window_id: tab && tab.windowId, url: tab && tab.url, trackable: Boolean(tab) };
            if (options.context === "defer") return new Promise((resolve) => state.deferredContext.push({ resolve, response }));
            return response;
          }
          return { ok: true };
        },
      },
      tabs: {
        query: async (query) => {
          state.calls.push({ query });
          const tabs = state.tabs.filter((tab) => query.windowId === undefined || tab.windowId === query.windowId);
          if (options.query === "defer") return new Promise((resolve) => state.deferredQuery.push({ resolve, tabs }));
          return tabs;
        },
        onActivated: event("activated"), onUpdated: event("updated"), onRemoved: event("removed"), onDetached: event("detached"),
      },
      storage: {
        local: {
          get: async () => options.settings === "pending" ? new Promise(() => {}) : structuredClone(state.stored),
          set: async (next) => {
            state.writes.push(next);
            if (options.storage === "reject") throw new Error("Storage write failed");
            Object.assign(state.stored, next);
            state.emit("storage", Object.fromEntries(Object.entries(next).map(([name, value]) => [name, { newValue: value }])), "local");
          },
        },
        onChanged: event("storage"),
      },
    };
  }, options);
  await page.goto("https://guard-test.example/popup.html");
  return page;
}

test("ID and title render while both status and settings are unresolved", async (t) => {
  const page = await popup(t, { status: "pending", settings: "pending" });
  await page.waitForFunction(() => document.getElementById("currentTabId").textContent === "712345");
  assert.equal(await page.locator("#currentTitle").textContent(), "Project overview");
  assert.equal(await page.locator("#copyTab").isEnabled(), true);
  assert.deepEqual(await page.evaluate(() => fixture.calls.find((call) => call.query).query), { active: true, currentWindow: true });
  await page.locator("#copyTab").click();
  const copied = await page.evaluate(() => fixture.copied[0]);
  assert.match(copied, /^Tab ID: 712345\nBrowser: Chrome\nWindow ID \(browser API\): 83/m);
});

test("disconnected worker still permits copying private tab reference", async (t) => {
  const page = await popup(t, { status: "reject", private: true });
  await page.waitForFunction(() => document.getElementById("currentTabId").textContent === "712345");
  await page.locator("#copyTab").click();
  assert.match(await page.evaluate(() => fixture.copied[0]), /Context: Private \/ incognito/);
  assert.equal(await page.locator("#copyTabLabel").textContent(), "Copied");
});

test("copy adds scoped Guard identity without mislabelling it as browser session", async (t) => {
  const page = await popup(t);
  await page.waitForFunction(() => document.getElementById("connection").textContent === "Disconnected");
  await page.locator("#copyTab").click();
  const text = await page.evaluate(() => fixture.copied[0]);
  assert.match(text, /Guard profile ID: test-profile/);
  assert.match(text, /Guard session ID: test-worker-session \(extension worker lifetime, not browser session\)/);
  assert.match(text, /browser-scoped, not OS window IDs/);
});

test("activation fixes window scope and discards stale exception replies", async (t) => {
  const page = await popup(t, { context: "defer" });
  await page.waitForFunction(() => fixture.deferredContext.length === 1);
  await page.evaluate(() => {
    fixture.emit("activated", { windowId: 999, tabId: 9999 });
    fixture.tabs = [{ id: 712346, windowId: 83, title: "Second tab", url: "https://acme.example/second", incognito: false }];
    fixture.emit("activated", { windowId: 83, tabId: 712346 });
  });
  await page.waitForFunction(() => fixture.deferredContext.length === 2);
  await page.evaluate(() => {
    fixture.deferredContext[1].resolve(fixture.deferredContext[1].response);
    fixture.deferredContext[0].resolve(fixture.deferredContext[0].response);
  });
  await page.waitForFunction(() => document.getElementById("currentUrl").textContent.endsWith("/second"));
  assert.equal(await page.locator("#currentTabId").textContent(), "712346");
  assert.deepEqual(await page.evaluate(() => fixture.calls.filter((call) => call.query).map((call) => call.query)), [
    { active: true, currentWindow: true }, { active: true, windowId: 83 },
  ]);
  assert.equal(await page.locator("#addExact").isEnabled(), true);
});

test("title updates render safely and removal disables stale copy", async (t) => {
  const page = await popup(t);
  await page.waitForFunction(() => document.getElementById("currentTabId").textContent === "712345");
  const title = "<img src=x onerror=alert(1)> " + "Very long title ".repeat(35);
  await page.evaluate((title) => {
    const tab = fixture.tabs[0];
    tab.title = title;
    fixture.emit("updated", tab.id, { title }, tab);
  }, title);
  assert.equal(await page.locator("#currentTitle").getAttribute("title"), title);
  assert.equal(await page.locator("#currentTitle img").count(), 0);
  assert.equal(await page.evaluate(() => document.body.scrollWidth > 390), false);
  assert.ok(await page.locator("#currentTitle").evaluate((node) => node.getBoundingClientRect().height <= 36));
  await page.evaluate(() => { fixture.tabs = []; fixture.emit("removed", 712345); });
  await page.waitForFunction(() => document.getElementById("copyTab").disabled);
  assert.equal(await page.locator("#currentTabId").textContent(), "—");
});

test("clipboard fallback preserves unfinished import and reports complete failure", async (t) => {
  const page = await popup(t, { clipboard: "reject" });
  await page.waitForFunction(() => !document.getElementById("copyTab").disabled);
  await page.evaluate(() => {
    document.getElementById("importText").value = '{"unfinished":"keep"}';
    document.getElementById("importArea").hidden = false;
  });
  await page.locator("#copyTab").click();
  assert.equal(await page.locator("#importText").inputValue(), '{"unfinished":"keep"}');
  assert.equal(await page.evaluate(() => fixture.copied.length), 1);
  assert.equal(await page.locator(".clipboard-buffer").count(), 0);
  assert.equal(await page.evaluate(() => document.activeElement.id), "copyTab");
  const failure = await popup(t, { clipboard: "reject", fallback: false });
  await failure.waitForFunction(() => !document.getElementById("copyTab").disabled);
  await failure.locator("#copyTab").click();
  assert.match(await failure.locator("#tabCopyStatus").textContent(), /Clipboard unavailable/);
  assert.equal(await failure.locator("#copyTabLabel").textContent(), "Copy ID");
  assert.deepEqual(await failure.evaluate(() => fixture.copied), []);
});

test("countdown and identical storage events preserve rule DOM and focus", async (t) => {
  const page = await popup(t, { paused: true });
  await page.waitForFunction(() => document.querySelector(".rule-toggle"));
  await page.evaluate(() => {
    window.savedRule = document.querySelector(".rule-toggle");
    savedRule.focus();
    fixture.timers.forEach((fn) => fn());
    const key = "duplicateTabGuardExceptionSettingsV1";
    fixture.emit("storage", { [key]: { newValue: fixture.stored[key] } }, "local");
  });
  assert.equal(await page.evaluate(() => savedRule === document.querySelector(".rule-toggle") && document.activeElement === savedRule), true);
  assert.deepEqual(await page.evaluate(() => fixture.writes), []);
});

test("failed guard, pause, resume, rule toggle and delete writes preserve confirmed state", async (t) => {
  for (const control of ["#guardEnabled", "#pauseGuard", "#resumeGuard", ".rule-toggle", ".icon-button.danger"]) {
    const page = await popup(t, { storage: "reject", paused: control === "#resumeGuard" });
    await page.waitForFunction(() => document.querySelector(".rule-toggle"));
    await page.locator(control).click();
    await page.waitForFunction(() => document.getElementById("note").textContent === "Storage write failed");
    assert.equal(await page.locator("#guardEnabled").isChecked(), true, control);
    assert.equal(await page.locator(".rule-toggle").isChecked(), true, control);
    assert.equal(await page.locator(".rule-item").count(), 1, control);
    assert.equal(await page.locator("#resumeGuard").isVisible(), control === "#resumeGuard", control);
  }
});

test("absence of an active tab does not expose a stale reference", async (t) => {
  const page = await popup(t, { noTab: true });
  await page.waitForFunction(() => document.getElementById("currentTitle").textContent === "No active tab available");
  assert.equal(await page.locator("#copyTab").isEnabled(), false);
  assert.equal(await page.locator("#addExact").isEnabled(), false);
});

test("worker resolves the requested tab instead of the last focused window", async () => {
  const source = fs.readFileSync(path.join(extension, "service_worker.js"), "utf8");
  const start = source.indexOf("chrome.runtime.onMessage.addListener(");
  const end = source.indexOf("chrome.storage.onChanged.addListener(", start);
  let listener;
  const calls = [];
  const context = {
    chrome: {
      runtime: { onMessage: { addListener(fn) { listener = fn; } } },
      tabs: {
        get: async (id) => { calls.push(id); return { id, windowId: 83, url: "https://acme.example/exact", incognito: true }; },
        query: async () => { throw new Error("Wrong window lookup"); },
      },
    },
    tabUrl: (tab) => tab.url, isOwnExtensionUrl: () => false, pendingOriginalUrl: () => "",
    isTrackableUrl: () => true, isTransientUrl: () => false, runtimeBrowser: "chrome",
  };
  vm.runInNewContext(source.slice(start, end), context);
  const result = await new Promise((resolve) => assert.equal(listener({ type: "get_active_tab_context", tab_id: 712345 }, {}, resolve), true));
  assert.deepEqual(calls, [712345]);
  assert.equal(result.tab_id, 712345);
  assert.equal(result.window_id, 83);
  assert.equal(result.incognito, true);
});

test("new tab reference adds no permissions or P1 tab-opening features", () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(extension, "manifest.json")));
  assert.deepEqual(manifest.permissions, ["nativeMessaging", "storage", "tabs", "webNavigation", "alarms"]);
  assert.equal(manifest.commands, undefined);
  assert.equal(manifest.chrome_url_overrides, undefined);
  assert.equal(manifest.action.default_popup, "popup.html");
});

test("activation before the initial query resolves invalidates the former active tab", async (t) => {
  const page = await popup(t, { query: "defer" });
  await page.waitForFunction(() => fixture.deferredQuery.length === 1);
  await page.evaluate(() => {
    fixture.tabs = [{ id: 712346, windowId: 83, title: "New active tab", url: "https://acme.example/second" }];
    fixture.emit("activated", { windowId: 83, tabId: 712346 });
  });
  await page.waitForFunction(() => fixture.deferredQuery.length === 2);
  await page.evaluate(() => {
    fixture.deferredQuery[1].resolve(fixture.deferredQuery[1].tabs);
    fixture.deferredQuery[0].resolve(fixture.deferredQuery[0].tabs);
  });
  await page.waitForFunction(() => document.getElementById("currentTabId").textContent === "712346");
  await page.locator("#copyTab").click();
  assert.match(await page.evaluate(() => fixture.copied[0]), /^Tab ID: 712346/);
});

test("light and dark card stay readable, visible and operable by keyboard", async (t) => {
  for (const dark of [false, true]) {
    const page = await popup(t, { dark, title: "Acme workspace | Project overview and planning" });
    await page.waitForFunction(() => document.getElementById("currentTabId").textContent === "712345");
    await page.locator("#copyTab").focus();
    await page.keyboard.press("Enter");
    assert.equal(await page.locator("#copyTabLabel").textContent(), "Copied");
    assert.equal(await page.evaluate(() => document.activeElement.id), "copyTab");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 390);
    const box = await page.locator(".tab-card").boundingBox();
    assert.ok(box.y >= 0 && box.y + box.height < 600);
    if (process.env.GUARD_SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.GUARD_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.GUARD_SCREENSHOT_DIR, dark ? "popup-dark.png" : "popup-light.png") });
    }
  }
});

test("title change, removal and detachment invalidate a pending initial snapshot", async (t) => {
  for (const kind of ["updated", "removed", "detached"]) {
    const page = await popup(t, { query: "defer" });
    await page.waitForFunction(() => fixture.deferredQuery.length === 1);
    await page.evaluate((kind) => {
      if (kind === "updated") {
        fixture.tabs = [{ ...fixture.tabs[0], title: "Updated during lookup" }];
        fixture.emit("updated", 712345, { title: fixture.tabs[0].title }, fixture.tabs[0]);
      } else {
        fixture.tabs = [];
        fixture.emit(kind, 712345, { windowId: 83, oldWindowId: 83 });
      }
    }, kind);
    await page.waitForFunction(() => fixture.deferredQuery.length === 2);
    await page.evaluate(async () => {
      fixture.deferredQuery[1].resolve(fixture.deferredQuery[1].tabs);
      fixture.deferredQuery[0].resolve(fixture.deferredQuery[0].tabs);
      await Promise.resolve();
      await Promise.resolve();
    });
    if (kind === "updated") {
      assert.equal(await page.locator("#currentTitle").textContent(), "Updated during lookup");
    } else {
      assert.equal(await page.locator("#currentTabId").textContent(), "—");
      assert.equal(await page.locator("#copyTab").isEnabled(), false);
    }
  }
});


test("connection status recovers while popup stays open and preserves success notes", async (t) => {
  const page = await popup(t, {
    statusSequence: [
      {
        ok: true,
        browser: "chrome",
        browser_override: "auto",
        native_connected: false,
        native_error: "Native coordinator rejected profile key",
        incognito_allowed: false,
        file_scheme_allowed: false,
        profile_id: "test-profile",
        session_id: "worker-one",
      },
      {
        ok: true,
        browser: "chrome",
        browser_override: "auto",
        native_connected: true,
        native_error: "",
        incognito_allowed: false,
        file_scheme_allowed: false,
        profile_id: "test-profile",
        session_id: "worker-two",
      },
      {
        ok: true,
        browser: "chrome",
        browser_override: "auto",
        native_connected: true,
        native_error: "",
        incognito_allowed: true,
        file_scheme_allowed: true,
        profile_id: "test-profile",
        session_id: "worker-two",
      },
    ],
  });
  await page.waitForFunction(() => document.getElementById("connection").textContent === "Disconnected");
  assert.equal(await page.locator("#note").textContent(), "Native coordinator rejected profile key");

  await page.evaluate(() => fixture.timers[fixture.timers.length - 1]());
  await page.waitForFunction(() => document.getElementById("connection").textContent === "Connected");
  assert.equal(await page.locator("#note").textContent(), "Use Permissions to enable Allow in Incognito/Private and Allow access to file URLs.");

  await page.locator("#addExact").click();
  await page.waitForFunction(() => document.getElementById("note").textContent === "Exact URL excluded.");
  await page.evaluate(() => fixture.timers[fixture.timers.length - 1]());
  await page.waitForTimeout(20);
  assert.equal(await page.locator("#connection").textContent(), "Connected");
  assert.equal(await page.locator("#note").textContent(), "Exact URL excluded.");
});
