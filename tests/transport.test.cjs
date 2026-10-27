"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const workerPath = path.join(__dirname, "..", "extension", "service_worker.js");
const workerSource = fs.readFileSync(workerPath, "utf8");
const transportStart = workerSource.indexOf("function resolveReadyWaiters");
const transportEnd = workerSource.indexOf("function startSnapshotTimer", transportStart);
assert.ok(transportStart >= 0 && transportEnd > transportStart);

class Scheduler {
  constructor() {
    this.nextId = 1;
    this.timeouts = new Map();
    this.intervals = new Map();
  }

  setTimeout(callback, delay) {
    const id = this.nextId++;
    this.timeouts.set(id, { callback, delay: Number(delay) || 0 });
    return id;
  }

  clearTimeout(id) {
    this.timeouts.delete(id);
  }

  setInterval(callback, delay) {
    const id = this.nextId++;
    this.intervals.set(id, { callback, delay: Number(delay) || 0 });
    return id;
  }

  clearInterval(id) {
    this.intervals.delete(id);
  }

  tick(milliseconds) {
    for (const [id, timer] of Array.from(this.timeouts)) {
      if (timer.delay <= milliseconds) {
        this.timeouts.delete(id);
        timer.callback();
      }
    }
  }

  pending() {
    return this.timeouts.size + this.intervals.size;
  }
}

class FakePort {
  constructor() {
    this.sent = [];
    this.disconnectCount = 0;
    this.messageListeners = [];
    this.disconnectListeners = [];
    this.onMessage = { addListener: (listener) => this.messageListeners.push(listener) };
    this.onDisconnect = { addListener: (listener) => this.disconnectListeners.push(listener) };
  }

  postMessage(message) {
    this.sent.push(message);
  }

  disconnect() {
    this.disconnectCount += 1;
    // Chrome does not invoke the caller's onDisconnect listener here.
  }

  emitMessage(message) {
    for (const listener of this.messageListeners) listener(message);
  }
}

class FakeSocket {
  constructor() {
    this.readyState = 1;
    this.listeners = {};
    this.sent = [];
  }

  addEventListener(name, listener) {
    (this.listeners[name] ||= []).push(listener);
  }

  send(value) {
    this.sent.push(value);
  }

  close() {
    for (const listener of this.listeners.close || []) listener();
  }
}

function makeHarness() {
  const scheduler = new Scheduler();
  const ports = [];
  const context = {
    setTimeout: scheduler.setTimeout.bind(scheduler),
    clearTimeout: scheduler.clearTimeout.bind(scheduler),
    setInterval: scheduler.setInterval.bind(scheduler),
    clearInterval: scheduler.clearInterval.bind(scheduler),
    crypto: { randomUUID: () => "request-" + Math.random().toString(16).slice(2) },
    WebSocket: FakeSocket,
    chrome: {
      runtime: {
        id: "test-extension",
        lastError: null,
        connectNative: () => {
          const port = new FakePort();
          ports.push(port);
          return port;
        },
      },
      extension: {
        inIncognitoContext: false,
      },
    },
  };
  const prelude = [
    "const VERSION = \"test\";",
    "const BROWSERS = new Set([\"brave\", \"chrome\", \"chromium\"]);",
    "const NATIVE_HOST = \"systems.venturi.duplicate_tab_guard\";",
    "const FALLBACK_WS_URL = \"ws://127.0.0.1/test\";",
    "const REQUEST_TIMEOUT_MS = 12000;",
    "let nativePort = null;",
    "let nativeReady = false;",
    "let nativeConnecting = false;",
    "let reconnectDelayMs = 500;",
    "let reconnectTimer = null;",
    "let snapshotTimer = null;",
    "let runtimeBrowser = \"chromium\";",
    "let profileId = \"profile\";",
    "let sessionId = \"session\";",
    "let incognitoAllowed = true;",
    "let fileSchemeAllowed = true;",
    "let lastNativeError = \"\";",
    "let transportKind = \"none\";",
    "let transportGeneration = 0;",
    "let connectionAttempt = 0;",
    "const transportState = new WeakMap();",
    "const readyWaiters = [];",
    "const pendingRequests = new Map();",
    "function ensureIdentity() { return Promise.resolve(); }",
    "function randomId() { return crypto.randomUUID(); }",
    "function restorePendingIncidents() { return Promise.resolve(); }",
    "function sendSnapshot() { return Promise.resolve(); }",
    "function handleCoordinatorEvent() { return Promise.resolve(); }",
    "function helloPayload() { return { type: \"hello\", extension_version: \"test\", extension_id: chrome.runtime.id, browser: runtimeBrowser, profile_id: profileId, session_id: sessionId }; }",
    "function startSnapshotTimer() { snapshotTimer = setInterval(() => {}, 15000); }",
  ].join("\n");
  vm.runInNewContext(prelude + "\n" + workerSource.slice(transportStart, transportEnd), context);
  return { context, scheduler, ports };
}

async function settle() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function state(harness, expression) {
  return vm.runInNewContext(expression, harness.context);
}

test("native handshake settles and clears both handshake and waiter timers", async () => {
  const harness = makeHarness();
  harness.context.connectNative();
  await settle();
  const port = harness.ports[0];
  assert.ok(port, "native port should be created");
  const waiter = harness.context.waitForNativeReady(5000);
  port.emitMessage({ type: "hello_ack", transport: "native" });
  await settle();
  assert.equal(state(harness, "nativeReady"), true, "hello ack should ready the current port");
  await waiter;
  await settle();
  assert.equal(state(harness, "nativeReady"), true);
  assert.equal(harness.scheduler.pending(), 1, "only the snapshot interval remains");
  harness.scheduler.tick(6000);
  assert.equal(port.disconnectCount, 0);
});

test("fatal coordinator error rejects ready waiters with the real message and resets the port", async () => {
  const harness = makeHarness();
  harness.context.connectNative();
  await settle();
  const port = harness.ports[0];
  const waiting = harness.context.waitForNativeReady(5000);
  port.emitMessage({ type: "fatal", error: "profile key rejected by coordinator" });
  await settle();
  await assert.rejects(waiting, /profile key rejected by coordinator/);
  await settle();
  assert.equal(port.disconnectCount, 1);
  assert.equal(state(harness, "nativePort").kind, "websocket");
  assert.equal(state(harness, "lastNativeError"), "profile key rejected by coordinator");
  assert.equal(state(harness, "nativeReady"), false);
});

test("fatal coordinator error rejects in-flight requests and keeps the message", async () => {
  const harness = makeHarness();
  harness.context.connectNative();
  await settle();
  const port = harness.ports[0];
  port.emitMessage({ type: "hello_ack", transport: "native" });
  await settle();
  const request = harness.context.sendNativeRequest("probe", {});
  await settle();
  port.emitMessage({ type: "fatal", message: "coordinator database is unavailable" });
  await settle();
  await assert.rejects(request, /coordinator database is unavailable/);
  assert.equal(state(harness, "lastNativeError"), "coordinator database is unavailable");
});

test("stale native callbacks cannot ready a newer connection", async () => {
  const harness = makeHarness();
  harness.context.connectNative();
  await settle();
  const first = harness.ports[0];
  harness.context.clearCurrentTransport("first connection closed", false, false);
  harness.context.connectNative();
  await settle();
  const second = harness.ports[1];
  first.emitMessage({ type: "hello_ack", transport: "native" });
  await settle();
  assert.equal(state(harness, "nativeReady"), false);
  second.emitMessage({ type: "hello_ack", transport: "native" });
  await settle();
  assert.equal(state(harness, "nativeReady"), true);
  assert.equal(state(harness, "nativePort"), second);
});

test("native handshake timeout explicitly resets the caller-owned port and starts fallback", async () => {
  const harness = makeHarness();
  harness.context.connectNative();
  await settle();
  const port = harness.ports[0];
  harness.scheduler.tick(5001);
  await settle();
  assert.equal(port.disconnectCount, 1);
  assert.equal(state(harness, "nativePort").kind, "websocket");
  assert.equal(state(harness, "nativeReady"), false);
});


test("native disconnect before handshake clears the current port and retries fallback", async () => {
  const harness = makeHarness();
  harness.context.connectNative();
  await settle();
  const port = harness.ports[0];
  port.disconnectListeners[0]();
  await settle();
  assert.equal(state(harness, "nativeReady"), false);
  assert.equal(state(harness, "nativePort").kind, "websocket");
});
