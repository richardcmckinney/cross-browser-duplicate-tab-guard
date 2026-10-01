#!/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/bin/python3.14
"""One-file installer and runtime for Cross-Browser Duplicate Tab Guard.

Supported deployment targets: macOS and Linux.
Supported browsers: Brave, Google Chrome, and Chromium.

The same source file installs a Manifest V3 extension, registers itself as a
native-messaging host, and runs an in-memory coordinator. A loopback WebSocket
transport is installed as a fallback for confined browser packages that cannot
launch native messaging hosts.
"""

from __future__ import annotations

import argparse
import base64
import errno
import fcntl
import hashlib
import http.client
import io
import json
import os
import platform
import secrets
import shlex
import shutil
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Union
from urllib.parse import urlsplit

APP_NAME = "Cross-Browser Duplicate Tab Guard"
APP_SLUG = "cross-browser-duplicate-tab-guard"
APP_VERSION = "2.3.0"
HOST_NAME = "systems.venturi.duplicate_tab_guard"
EXTENSION_ID = "ealdapdhgkonfamgboejhjaniepdjihk"
EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}/"
DEFAULT_WS_PORT = 49473
MAX_MESSAGE_BYTES = 16 * 1024 * 1024
MAX_URL_CHARS = 131_072
MAX_TITLE_CHARS = 2_048
MAX_TABS_PER_SNAPSHOT = 5_000
OFFLINE_GRACE_SECONDS = 10.0
TAB_STALE_SECONDS = 70.0
TRANSFER_TIMEOUT_SECONDS = 25.0
INCIDENT_TTL_SECONDS = 600.0
DAEMON_IDLE_SECONDS = 25.0
SUPPORTED_BROWSERS = {"brave", "chrome", "chromium"}

PUBLIC_KEY_B64 = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAuiXZIY3iosKFylijfVyg"
    "Td2esQ0a6DTNkYhXeqc/atLRzLnihPQpYu7nAJVd0tN+3xaotBnQyK846tIbZce"
    "/HMotXjMCWgWLyFqNLfcSWJnHQo1KLY7GDhXJ1OhMGDPGmDsADdff2qP+G6iZ0j"
    "T86AiXnSxmusr6uHB9tWFKcOO2hrm01WE+KmzOpC3NOlyQg2EOjTXmJp6JVq1jO"
    "JlSPjZ2Myq/jzW5BWZibeB7xErCnxaNT6JDtzW1C0NYdjVejozUCIuslb587mlh"
    "LG0tcbGhTZ7tI5Iq/e3h/5Q1xkr/eBe7fAuuJFBEr9XT3kYbABJyvA05Ofiyd7p"
    "zwNCvKwIDAQAB"
)

EXTENSION_FILES: dict[str, str] = {'chooser.css': ':root { color-scheme: light dark; font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }\n* { box-sizing: border-box; }\nbody { margin: 0; min-height: 100vh; background: #eef1f5; color: #171b24; }\n.shell { min-height: 100vh; display: grid; place-items: center; padding: 28px; }\n.card { width: min(620px, 100%); padding: 30px; border: 1px solid #d9dee6; border-radius: 20px; background: #fff; box-shadow: 0 28px 90px rgba(0,0,0,.16); }\n.eyebrow { margin: 0 0 8px; color: #606a7b; font-size: 11px; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }\nh1 { margin: 0; font-size: 26px; line-height: 1.2; letter-spacing: -.025em; }\n.description { margin: 12px 0 0; color: #4f5969; line-height: 1.6; }\n.url { margin: 18px 0; padding: 13px 14px; max-height: 140px; overflow: auto; overflow-wrap: anywhere; border: 1px solid #e0e4ea; border-radius: 11px; background: #f7f8fa; font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }\nlabel { display: block; margin-bottom: 7px; font-size: 12px; font-weight: 750; }\nselect { width: 100%; min-height: 44px; padding: 9px 12px; border: 1px solid #cbd2dc; border-radius: 10px; background: #fff; color: inherit; font: inherit; }\nselect:focus { outline: 3px solid rgba(80,101,232,.18); border-color: #5065e8; }\n.meta, .status { margin: 10px 0 0; min-height: 20px; color: #667184; font-size: 12px; line-height: 1.45; }\n.status.error { color: #b42318; }\n.status.success { color: #027a48; }\n.actions { display: flex; justify-content: flex-end; margin-top: 18px; }\nbutton { min-height: 43px; padding: 10px 17px; border: 0; border-radius: 10px; background: #202633; color: #fff; font: inherit; font-weight: 750; cursor: pointer; }\nbutton:disabled, select:disabled { opacity: .58; cursor: wait; }\n.existing-banner {\n  margin: 16px 0 0;\n  padding: 13px 15px;\n  border: 1px solid #d0d7de;\n  border-radius: 12px;\n  background: #f6f8fa;\n}\n.banner-header {\n  font-size: 11px;\n  font-weight: 750;\n  text-transform: uppercase;\n  letter-spacing: .08em;\n  color: #57606a;\n  margin-bottom: 6px;\n}\n.badge-list {\n  display: flex;\n  flex-wrap: wrap;\n  gap: 6px;\n  margin-bottom: 6px;\n}\n.browser-pill {\n  display: inline-flex;\n  align-items: center;\n  padding: 3px 9px;\n  border-radius: 999px;\n  font-size: 12px;\n  font-weight: 750;\n  line-height: 1.25;\n}\n.browser-pill.brave { background: #ffeae5; color: #b42318; border: 1px solid #fecdca; }\n.browser-pill.chrome { background: #e8f1ff; color: #175cd3; border: 1px solid #b2ddff; }\n.browser-pill.chromium { background: #e0f2fe; color: #026aa2; border: 1px solid #b9e6fe; }\n.browser-pill.other { background: #eef1f5; color: #344054; border: 1px solid #d0d5dd; }\n.banner-summary {\n  margin: 0;\n  font-size: 13px;\n  font-weight: 650;\n  line-height: 1.45;\n  color: #1f242e;\n}\n.copies-list {\n  margin-top: 8px;\n  padding: 8px 10px;\n  background: #f0f2f5;\n  border-radius: 8px;\n  font-size: 11px;\n  line-height: 1.5;\n  color: #475467;\n  max-height: 90px;\n  overflow: auto;\n}\n.copy-item {\n  margin-top: 2px;\n  overflow-wrap: anywhere;\n}\n.copy-item:first-child { margin-top: 0; }\n@media (prefers-color-scheme: dark) {\n  body { background: #0f1115; color: #f3f5f8; }\n  .card { background: #181b21; border-color: #343a44; }\n  .description, .meta, .status, .eyebrow { color: #aab4c3; }\n  .url { background: #22262e; border-color: #373e49; }\n  select { background: #22262e; border-color: #3e4652; }\n  button { background: #f0f2f5; color: #171a20; }\n  .existing-banner { background: #1c2128; border-color: #30363d; }\n  .banner-header { color: #8b949e; }\n  .browser-pill.brave { background: #3c1618; color: #f97066; border-color: #7a271a; }\n  .browser-pill.chrome { background: #102a4c; color: #84caff; border-color: #1849a9; }\n  .browser-pill.chromium { background: #082f49; color: #7dd3fc; border-color: #075985; }\n  .browser-pill.other { background: #21262d; color: #c9d1d9; border-color: #30363d; }\n  .banner-summary { color: #f0f6fc; }\n  .copies-list { background: #20242c; color: #9da7b4; }\n}\n', 'chooser.html': '<!doctype html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1">\n  <title>Duplicate tab detected</title>\n  <link rel="stylesheet" href="chooser.css">\n</head>\n<body>\n  <main class="shell">\n    <section class="card" role="alertdialog" aria-labelledby="title" aria-describedby="description">\n      <p class="eyebrow">Duplicate tab detected</p>\n      <h1 id="title">This exact URL is already open</h1>\n      <div id="banner" class="existing-banner" style="display: none;">\n        <div class="banner-header">Pre-existing tab location</div>\n        <div id="badge-list" class="badge-list"></div>\n        <p id="banner-summary" class="banner-summary"></p>\n      </div>\n      <p id="description" class="description">Choose which browser should retain the single live copy. Every other exact-URL copy will be closed.</p>\n      <div id="url" class="url"></div>\n      <label for="choice">Browser to retain</label>\n      <select id="choice">\n        <option value="current">Current browser — this tab</option>\n        <option value="brave">Brave</option>\n        <option value="chrome">Chrome</option>\n        <option value="chromium">Chromium</option>\n      </select>\n      <div id="meta" class="meta"></div>\n      <p id="status" class="status" aria-live="polite"></p>\n      <div class="actions">\n        <button id="confirm" type="button">Keep selected copy</button>\n      </div>\n    </section>\n  </main>\n  <script src="chooser.js"></script>\n</body>\n</html>\n', 'chooser.js': '"use strict";\n\nconst labels = { brave: "Brave", chrome: "Chrome", chromium: "Chromium" };\nlet tabId = null;\nlet incident = null;\n\nconst urlEl = document.getElementById("url");\nconst choiceEl = document.getElementById("choice");\nconst metaEl = document.getElementById("meta");\nconst statusEl = document.getElementById("status");\nconst confirmEl = document.getElementById("confirm");\n\nfunction setBusy(value) {\n  confirmEl.disabled = Boolean(value);\n  choiceEl.disabled = Boolean(value);\n}\n\nfunction setStatus(message, kind = "") {\n  statusEl.textContent = String(message || "");\n  statusEl.className = `status ${kind}`.trim();\n}\n\nconst bannerEl = document.getElementById("banner");\nconst badgeListEl = document.getElementById("badge-list");\nconst bannerSummaryEl = document.getElementById("banner-summary");\nconst descEl = document.getElementById("description");\n\nasync function load() {\n  const response = await chrome.runtime.sendMessage({ type: "chooser_get" });\n  if (!response || !response.ok || !response.incident) {\n    throw new Error("This duplicate alert is no longer active. You may close this tab.");\n  }\n  tabId = response.tab_id;\n  incident = response.incident;\n  urlEl.textContent = incident.url || "";\n  const titleEl = document.getElementById("title");\n  if (titleEl && incident.existing_summary) {\n    titleEl.textContent = incident.existing_summary;\n  }\n  const eyebrowEl = document.querySelector(".eyebrow");\n  if (eyebrowEl && incident.primary_existing_label) {\n    eyebrowEl.textContent = `Duplicate tab detected • Pre-existing in ${incident.primary_existing_label}`;\n  }\n\n  if (incident.existing_summary) {\n    if (bannerEl && badgeListEl && bannerSummaryEl) {\n      badgeListEl.innerHTML = "";\n      const bLabels = incident.existing_browser_labels && incident.existing_browser_labels.length\n        ? incident.existing_browser_labels\n        : (incident.existing_browsers || []).map((b) => labels[b] || b);\n      for (const bName of (bLabels.length ? bLabels : [labels[incident.primary_existing_browser] || "Chromium"])) {\n        const pill = document.createElement("span");\n        pill.className = `browser-pill ${bName.toLowerCase()}`;\n        pill.textContent = bName;\n        badgeListEl.append(pill);\n      }\n      bannerSummaryEl.textContent = incident.existing_summary;\n      bannerEl.style.display = "block";\n    }\n    if (descEl) {\n      descEl.textContent = `${incident.existing_summary}. Choose which browser should retain the single live copy. Every other exact-URL copy will be closed.`;\n    }\n  }\n\n  const existingCopies = Array.isArray(incident.existing_copies) ? incident.existing_copies : [];\n  const hasSameBrowserExisting = existingCopies.some((c) => c.is_same_browser);\n  const currentLabel = labels[incident.current_browser] || "this browser";\n  const currentText = `Current browser — ${currentLabel} (this tab${hasSameBrowserExisting ? "; pre-existing copy also open here" : ""})`;\n\n  choiceEl.innerHTML = "";\n  const baseOptions = [\n    ["current", currentText],\n    ["brave", "Brave"],\n    ["chrome", "Chrome"],\n    ["chromium", "Chromium"],\n  ];\n\n  for (const [value, baseText] of baseOptions) {\n    const opt = document.createElement("option");\n    opt.value = value;\n    let text = baseText;\n    if (value === "current") {\n      text = currentText;\n    } else {\n      const matching = existingCopies.filter((c) => c.browser === value && !c.is_same_browser);\n      if (matching.length > 0) {\n        const priv = matching.some((c) => c.incognito);\n        text = `${baseText} (pre-existing tab${priv ? " — private" : ""})`;\n      } else if (value === incident.current_browser) {\n        if (hasSameBrowserExisting) {\n          text = `${baseText} (pre-existing copy in another window)`;\n        }\n      } else {\n        text = `${baseText} (transfer tab here)`;\n      }\n    }\n    opt.textContent = text;\n    choiceEl.append(opt);\n  }\n\n  const count = Number(incident.copy_count || 2);\n  const privateCount = Number(incident.private_copy_count || 0);\n  metaEl.innerHTML = "";\n  const metaText = document.createElement("p");\n  metaText.textContent = `${count} live copies found${privateCount ? `; ${privateCount} in private/incognito context` : ""}.${incident.existing_summary ? ` • ${incident.existing_summary}` : ""}`;\n  metaEl.append(metaText);\n\n  if (existingCopies.length > 0) {\n    const copiesList = document.createElement("div");\n    copiesList.className = "copies-list";\n    for (const copy of existingCopies) {\n      const item = document.createElement("div");\n      item.className = "copy-item";\n      const bLabel = copy.browser_label || labels[copy.browser] || copy.browser;\n      const winInfo = copy.window_id > 0 ? ` Window ${copy.window_id}` : "";\n      const privInfo = copy.incognito ? " (Private)" : "";\n      const sameInfo = copy.is_same_browser ? " [same browser]" : " [pre-existing]";\n      const titleInfo = copy.title ? ` • "${copy.title}"` : "";\n      item.textContent = `• ${bLabel}${sameInfo}${winInfo}${privInfo}${titleInfo}`;\n      copiesList.append(item);\n    }\n    metaEl.append(copiesList);\n  }\n}\n\nconfirmEl.addEventListener("click", async () => {\n  if (!incident) return;\n  setBusy(true);\n  setStatus("Applying your selection…");\n  try {\n    const response = await chrome.runtime.sendMessage({\n      type: "chooser_resolve",\n      incident_id: incident.incident_id,\n      choice: choiceEl.value,\n    });\n    if (!response || !response.ok) throw new Error((response && response.error) || "The duplicate could not be resolved.");\n    setStatus(response.pending ? (response.message || "Opening the selected browser…") : "Selection applied.", response.pending ? "" : "success");\n  } catch (error) {\n    setBusy(false);\n    setStatus(error && error.message ? error.message : String(error), "error");\n  }\n});\n\nchrome.runtime.onMessage.addListener((message) => {\n  if (!message || message.type !== "chooser_event" || message.tab_id !== tabId) return;\n  setBusy(Boolean(message.busy));\n  setStatus(message.message || "", message.kind || "");\n});\n\nload().catch((error) => {\n  setBusy(true);\n  setStatus(error && error.message ? error.message : String(error), "error");\n});\n', 'content.js': '(() => {\n  "use strict";\n\n  const HOST_ID = "__cross_browser_duplicate_tab_guard__";\n  const BROWSER_LABELS = Object.freeze({\n    brave: "Brave",\n    chrome: "Chrome",\n    chromium: "Chromium",\n  });\n\n  let state = null;\n  let host = null;\n  let shadow = null;\n\n  function label(browser) {\n    return BROWSER_LABELS[browser] || "Current browser";\n  }\n\n  function removeDialog() {\n    if (host && host.isConnected) host.remove();\n    host = null;\n    shadow = null;\n    state = null;\n  }\n\n  function setStatus(message, kind = "info") {\n    if (!shadow) return;\n    const status = shadow.querySelector("[data-role=\'status\']");\n    if (!status) return;\n    status.textContent = String(message || "");\n    status.dataset.kind = kind;\n  }\n\n  function setBusy(busy) {\n    if (!shadow) return;\n    const button = shadow.querySelector("button[data-role=\'confirm\']");\n    const select = shadow.querySelector("select[data-role=\'choice\']");\n    if (button) button.disabled = Boolean(busy);\n    if (select) select.disabled = Boolean(busy);\n  }\n\n  function render(payload) {\n    removeDialog();\n    state = payload;\n\n    host = document.createElement("div");\n    host.id = HOST_ID;\n    host.setAttribute("data-extension-owned", "true");\n    host.style.setProperty("all", "initial", "important");\n    host.style.setProperty("position", "fixed", "important");\n    host.style.setProperty("inset", "0", "important");\n    host.style.setProperty("z-index", "2147483647", "important");\n\n    shadow = host.attachShadow({ mode: "closed" });\n    const style = document.createElement("style");\n    style.textContent = `\n      :host { all: initial; }\n      *, *::before, *::after { box-sizing: border-box; }\n      .backdrop {\n        position: fixed;\n        inset: 0;\n        display: grid;\n        place-items: center;\n        padding: 24px;\n        background: rgba(9, 12, 18, 0.56);\n        backdrop-filter: blur(8px);\n        font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;\n        color: #171b24;\n      }\n      .dialog {\n        width: min(560px, 100%);\n        border: 1px solid rgba(23, 27, 36, 0.12);\n        border-radius: 18px;\n        background: #ffffff;\n        box-shadow: 0 28px 90px rgba(0, 0, 0, 0.28);\n        overflow: hidden;\n      }\n      .header { padding: 24px 24px 15px; }\n      .eyebrow {\n        margin: 0 0 8px;\n        font-size: 11px;\n        font-weight: 750;\n        letter-spacing: 0.12em;\n        text-transform: uppercase;\n        color: #5f6878;\n      }\n      h1 {\n        margin: 0;\n        font-size: 22px;\n        line-height: 1.25;\n        font-weight: 760;\n        letter-spacing: -0.02em;\n      }\n      .body { padding: 0 24px 24px; }\n      p { margin: 0; font-size: 14px; line-height: 1.55; color: #4b5565; }\n      .url {\n        margin: 16px 0;\n        padding: 12px 13px;\n        border: 1px solid #e1e5eb;\n        border-radius: 10px;\n        background: #f7f8fa;\n        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;\n        font-size: 12px;\n        line-height: 1.45;\n        color: #252b36;\n        overflow-wrap: anywhere;\n        max-height: 112px;\n        overflow: auto;\n      }\n      label {\n        display: block;\n        margin: 0 0 7px;\n        font-size: 12px;\n        font-weight: 700;\n        color: #343b48;\n      }\n      select {\n        width: 100%;\n        min-height: 43px;\n        border: 1px solid #cfd5dd;\n        border-radius: 10px;\n        padding: 9px 38px 9px 12px;\n        background: #fff;\n        color: #1c222d;\n        font: inherit;\n        font-size: 14px;\n        outline: none;\n      }\n      select:focus { border-color: #5065e8; box-shadow: 0 0 0 3px rgba(80, 101, 232, 0.15); }\n      .meta { margin-top: 10px; font-size: 12px; color: #697386; }\n      .status {\n        min-height: 20px;\n        margin-top: 13px;\n        font-size: 12px;\n        line-height: 1.45;\n        color: #5a6474;\n      }\n      .status[data-kind="error"] { color: #b42318; }\n      .status[data-kind="success"] { color: #027a48; }\n      .actions {\n        display: flex;\n        justify-content: flex-end;\n        gap: 10px;\n        padding: 16px 24px;\n        border-top: 1px solid #e7e9ed;\n        background: #fafbfc;\n      }\n      button {\n        appearance: none;\n        min-height: 42px;\n        border: 0;\n        border-radius: 10px;\n        padding: 10px 16px;\n        background: #202633;\n        color: #fff;\n        font: inherit;\n        font-size: 14px;\n        font-weight: 700;\n        cursor: pointer;\n      }\n      button:hover:not(:disabled) { background: #0f131b; }\n      button:focus-visible { outline: 3px solid rgba(80, 101, 232, 0.25); outline-offset: 2px; }\n      button:disabled, select:disabled { opacity: 0.58; cursor: wait; }\n      .existing-banner {\n        margin: 14px 0 0;\n        padding: 12px 14px;\n        border: 1px solid #d0d7de;\n        border-radius: 12px;\n        background: #f6f8fa;\n      }\n      .banner-header {\n        font-size: 11px;\n        font-weight: 750;\n        text-transform: uppercase;\n        letter-spacing: 0.08em;\n        color: #57606a;\n        margin-bottom: 6px;\n      }\n      .badge-list {\n        display: flex;\n        flex-wrap: wrap;\n        gap: 6px;\n        margin-bottom: 6px;\n      }\n      .browser-pill {\n        display: inline-flex;\n        align-items: center;\n        padding: 3px 9px;\n        border-radius: 999px;\n        font-size: 12px;\n        font-weight: 750;\n        line-height: 1.25;\n      }\n      .browser-pill.brave { background: #ffeae5; color: #b42318; border: 1px solid #fecdca; }\n      .browser-pill.chrome { background: #e8f1ff; color: #175cd3; border: 1px solid #b2ddff; }\n      .browser-pill.chromium { background: #e0f2fe; color: #026aa2; border: 1px solid #b9e6fe; }\n      .browser-pill.other { background: #eef1f5; color: #344054; border: 1px solid #d0d5dd; }\n      .banner-summary {\n        margin: 0;\n        font-size: 13px;\n        font-weight: 650;\n        line-height: 1.45;\n        color: #1f242e;\n      }\n      .copies-list {\n        margin-top: 8px;\n        padding: 8px 10px;\n        background: #f0f2f5;\n        border-radius: 8px;\n        font-size: 11px;\n        line-height: 1.5;\n        color: #475467;\n        max-height: 85px;\n        overflow: auto;\n      }\n      .copy-item {\n        margin-top: 2px;\n        overflow-wrap: anywhere;\n      }\n      .copy-item:first-child { margin-top: 0; }\n      @media (prefers-color-scheme: dark) {\n        .dialog { background: #171a20; border-color: #343943; color: #f5f7fa; }\n        p, .meta, .status { color: #aeb7c5; }\n        .eyebrow { color: #9ca6b5; }\n        .url { background: #20242c; border-color: #343a45; color: #edf1f7; }\n        label { color: #d9dee7; }\n        select { background: #20242c; border-color: #3d4450; color: #f4f6f8; }\n        .actions { background: #14171c; border-color: #303640; }\n        button { background: #eef1f5; color: #161a21; }\n        button:hover:not(:disabled) { background: #ffffff; }\n        .existing-banner { background: #1c2128; border-color: #30363d; }\n        .banner-header { color: #8b949e; }\n        .browser-pill.brave { background: #3c1618; color: #f97066; border-color: #7a271a; }\n        .browser-pill.chrome { background: #102a4c; color: #84caff; border-color: #1849a9; }\n        .browser-pill.chromium { background: #082f49; color: #7dd3fc; border-color: #075985; }\n        .browser-pill.other { background: #21262d; color: #c9d1d9; border-color: #30363d; }\n        .banner-summary { color: #f0f6fc; }\n        .copies-list { background: #20242c; color: #9da7b4; }\n      }\n    `;\n\n    const backdrop = document.createElement("div");\n    backdrop.className = "backdrop";\n    backdrop.setAttribute("role", "presentation");\n\n    const dialog = document.createElement("section");\n    dialog.className = "dialog";\n    dialog.setAttribute("role", "alertdialog");\n    dialog.setAttribute("aria-modal", "true");\n    dialog.setAttribute("aria-labelledby", "cbdtg-title");\n    dialog.setAttribute("aria-describedby", "cbdtg-description");\n\n    const header = document.createElement("div");\n    header.className = "header";\n    const eyebrow = document.createElement("p");\n    eyebrow.className = "eyebrow";\n    const existingLabel = payload.primary_existing_label || (payload.existing_browser_labels && payload.existing_browser_labels[0]) || "";\n    eyebrow.textContent = existingLabel ? `Duplicate tab detected • Pre-existing in ${existingLabel}` : "Duplicate tab detected";\n    const title = document.createElement("h1");\n    title.id = "cbdtg-title";\n    title.textContent = payload.existing_summary || "This exact URL is already open";\n    header.append(eyebrow, title);\n\n    if (payload.existing_summary) {\n      const banner = document.createElement("div");\n      banner.className = "existing-banner";\n      const bannerHeader = document.createElement("div");\n      bannerHeader.className = "banner-header";\n      bannerHeader.textContent = "Pre-existing tab location";\n      const badgeList = document.createElement("div");\n      badgeList.className = "badge-list";\n      const bLabels = payload.existing_browser_labels && payload.existing_browser_labels.length\n        ? payload.existing_browser_labels\n        : (payload.existing_browsers || []).map((b) => label(b));\n      for (const bName of (bLabels.length ? bLabels : [label(payload.primary_existing_browser || "chromium")])) {\n        const pill = document.createElement("span");\n        pill.className = `browser-pill ${bName.toLowerCase()}`;\n        pill.textContent = bName;\n        badgeList.append(pill);\n      }\n      const summary = document.createElement("p");\n      summary.className = "banner-summary";\n      summary.textContent = payload.existing_summary;\n      banner.append(bannerHeader, badgeList, summary);\n      header.append(banner);\n    }\n\n    const body = document.createElement("div");\n    body.className = "body";\n    const description = document.createElement("p");\n    description.id = "cbdtg-description";\n    if (payload.existing_summary) {\n      description.textContent = `${payload.existing_summary}. Choose which browser should retain the single live copy. Every other exact-URL copy will be closed.`;\n    } else {\n      description.textContent = "Choose which browser should retain the single live copy. Every other exact-URL copy will be closed.";\n    }\n    const url = document.createElement("div");\n    url.className = "url";\n    url.textContent = String(payload.url || location.href);\n\n    const labelEl = document.createElement("label");\n    labelEl.htmlFor = "cbdtg-choice";\n    labelEl.textContent = "Browser to retain";\n    const select = document.createElement("select");\n    select.id = "cbdtg-choice";\n    select.dataset.role = "choice";\n\n    const baseOptions = [\n      ["current", `Current browser — ${label(payload.current_browser)} (this tab)`],\n      ["brave", "Brave"],\n      ["chrome", "Chrome"],\n      ["chromium", "Chromium"],\n    ];\n    const existingCopies = Array.isArray(payload.existing_copies) ? payload.existing_copies : [];\n    const hasSameBrowserExisting = existingCopies.some((c) => c.is_same_browser);\n\n    for (const [value, baseText] of baseOptions) {\n      const option = document.createElement("option");\n      option.value = value;\n      let text = baseText;\n      if (value === "current") {\n        text = `Current browser — ${label(payload.current_browser)} (this tab${hasSameBrowserExisting ? "; pre-existing copy also open here" : ""})`;\n      } else {\n        const matching = existingCopies.filter((c) => c.browser === value && !c.is_same_browser);\n        if (matching.length > 0) {\n          const priv = matching.some((c) => c.incognito);\n          text = `${baseText} (pre-existing tab${priv ? " — private" : ""})`;\n        } else if (value === payload.current_browser) {\n          if (hasSameBrowserExisting) {\n            text = `${baseText} (pre-existing copy in another window)`;\n          }\n        } else {\n          text = `${baseText} (transfer tab here)`;\n        }\n      }\n      option.textContent = text;\n      select.append(option);\n    }\n\n    const count = Number(payload.copy_count || 2);\n    const privateCount = Number(payload.private_copy_count || 0);\n    const meta = document.createElement("div");\n    meta.className = "meta";\n    let metaText = `${count} live copies found${privateCount ? `; ${privateCount} in private/incognito context` : ""}.`;\n    if (payload.existing_summary) {\n      metaText += ` • ${payload.existing_summary}`;\n    }\n    meta.textContent = metaText;\n\n    if (existingCopies.length > 0) {\n      const copiesList = document.createElement("div");\n      copiesList.className = "copies-list";\n      for (const copy of existingCopies) {\n        const item = document.createElement("div");\n        item.className = "copy-item";\n        const bLabel = copy.browser_label || label(copy.browser);\n        const winInfo = copy.window_id > 0 ? ` Window ${copy.window_id}` : "";\n        const privInfo = copy.incognito ? " (Private)" : "";\n        const sameInfo = copy.is_same_browser ? " [same browser]" : " [pre-existing]";\n        const titleInfo = copy.title ? ` • "${copy.title}"` : "";\n        item.textContent = `• ${bLabel}${sameInfo}${winInfo}${privInfo}${titleInfo}`;\n        copiesList.append(item);\n      }\n      meta.append(copiesList);\n    }\n    const status = document.createElement("div");\n    status.className = "status";\n    status.dataset.role = "status";\n    status.setAttribute("aria-live", "polite");\n\n    body.append(description, url, labelEl, select, meta, status);\n\n    const actions = document.createElement("div");\n    actions.className = "actions";\n    const confirm = document.createElement("button");\n    confirm.type = "button";\n    confirm.dataset.role = "confirm";\n    confirm.textContent = "Keep selected copy";\n    confirm.addEventListener("click", async () => {\n      setBusy(true);\n      setStatus("Applying your selection…");\n      try {\n        const activeIncident = state;\n        if (!activeIncident || !activeIncident.incident_id || String(activeIncident.url || location.href) !== location.href) {\n          throw new Error("This duplicate alert is no longer active.");\n        }\n        const response = await chrome.runtime.sendMessage({\n          type: "resolve_duplicate",\n          incident_id: activeIncident.incident_id,\n          choice: select.value,\n        });\n        if (!response || !response.ok) {\n          throw new Error((response && response.error) || "The duplicate could not be resolved.");\n        }\n        if (response.pending) {\n          setStatus(response.message || "Opening the selected browser…", "info");\n        } else {\n          setStatus("Selection applied.", "success");\n        }\n      } catch (error) {\n        setBusy(false);\n        setStatus(error && error.message ? error.message : String(error), "error");\n      }\n    });\n    actions.append(confirm);\n\n    dialog.append(header, body, actions);\n    backdrop.append(dialog);\n    shadow.append(style, backdrop);\n\n    const root = document.documentElement || document.body;\n    if (root) {\n      root.append(host);\n    } else {\n      const pendingHost = host;\n      document.addEventListener("DOMContentLoaded", () => {\n        const destination = document.documentElement || document.body;\n        if (destination && pendingHost === host && !pendingHost.isConnected) destination.append(pendingHost);\n      }, { once: true });\n    }\n\n    queueMicrotask(() => {\n      if (select.isConnected) select.focus();\n    });\n  }\n\n  chrome.runtime.onMessage.addListener((message) => {\n    if (!message || typeof message !== "object") return;\n    if (message.type === "show_duplicate_dialog") {\n      const payload = message.payload || {};\n      if (payload.url && String(payload.url) !== location.href) return;\n      render(payload);\n      return;\n    }\n    if (message.type === "duplicate_status") {\n      setBusy(Boolean(message.busy));\n      setStatus(message.message || "", message.kind || "info");\n      return;\n    }\n    if (message.type === "dismiss_duplicate_dialog") {\n      removeDialog();\n    }\n  });\n})();\n', 'exceptions.js': '"use strict";\n\n(() => {\n  const STORAGE_KEY = "duplicateTabGuardExceptionSettingsV1";\n  const SCHEMA_VERSION = 1;\n  const MAX_RULES = 500;\n  const MAX_PATTERN_LENGTH = 4096;\n  const MAX_NOTE_LENGTH = 160;\n\n  const TYPES = Object.freeze({\n    exact: "Exact URL",\n    host: "Hostname",\n    domain: "Domain + subdomains",\n    wildcard: "Wildcard URL",\n    regex: "Regular expression",\n  });\n  const BROWSER_SCOPES = new Set(["all", "brave", "chrome", "chromium"]);\n  const CONTEXT_SCOPES = new Set(["all", "regular", "incognito"]);\n\n  function makeId() {\n    try {\n      return crypto.randomUUID();\n    } catch (_) {\n      return `rule-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;\n    }\n  }\n\n  function defaultSettings() {\n    return {\n      schema: SCHEMA_VERSION,\n      enabled: true,\n      pausedUntil: 0,\n      rules: [],\n    };\n  }\n\n  function normalizeType(value) {\n    const type = String(value || "exact").toLowerCase();\n    return Object.prototype.hasOwnProperty.call(TYPES, type) ? type : "exact";\n  }\n\n  function normalizeBrowserScope(value) {\n    const scope = String(value || "all").toLowerCase();\n    return BROWSER_SCOPES.has(scope) ? scope : "all";\n  }\n\n  function normalizeContextScope(value) {\n    const scope = String(value || "all").toLowerCase();\n    return CONTEXT_SCOPES.has(scope) ? scope : "all";\n  }\n\n  function normalizeRule(value = {}) {\n    const raw = value && typeof value === "object" ? value : {};\n    return {\n      id: String(raw.id || makeId()).slice(0, 128),\n      enabled: raw.enabled !== false,\n      type: normalizeType(raw.type),\n      pattern: String(raw.pattern || "").trim().slice(0, MAX_PATTERN_LENGTH),\n      browser: normalizeBrowserScope(raw.browser),\n      context: normalizeContextScope(raw.context),\n      note: String(raw.note || "").replace(/\\s+/g, " ").trim().slice(0, MAX_NOTE_LENGTH),\n      createdAt: Number.isFinite(Number(raw.createdAt)) ? Number(raw.createdAt) : Date.now(),\n    };\n  }\n\n  function normalizeSettings(value) {\n    const raw = value && typeof value === "object" ? value : {};\n    const rules = Array.isArray(raw.rules)\n      ? raw.rules.slice(0, MAX_RULES).map(normalizeRule).filter((rule) => rule.pattern)\n      : [];\n    return {\n      schema: SCHEMA_VERSION,\n      enabled: raw.enabled !== false,\n      pausedUntil: Math.max(0, Number(raw.pausedUntil) || 0),\n      rules,\n    };\n  }\n\n  function parseUrl(value) {\n    try {\n      return new URL(String(value || ""));\n    } catch (_) {\n      return null;\n    }\n  }\n\n  function hostFromPattern(pattern, includePort) {\n    let value = String(pattern || "").trim().toLowerCase();\n    if (!value) return "";\n    value = value.replace(/^\\*\\./, "");\n    const parsed = parseUrl(value.includes("://") ? value : `https://${value}`);\n    if (parsed) return (includePort ? parsed.host : parsed.hostname).toLowerCase();\n    value = value.split(/[/?#]/, 1)[0];\n    return includePort ? value : value.replace(/:\\d+$/, "");\n  }\n\n  function escapeRegex(value) {\n    return String(value).replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&");\n  }\n\n  function wildcardRegex(pattern) {\n    const source = String(pattern || "")\n      .split("*").map((part) => part.split("?").map(escapeRegex).join(".")).join(".*");\n    return new RegExp(`^${source}$`);\n  }\n\n    const regexCache = new Map();\n  function getCachedRegex(key, factory) {\n    let re = regexCache.get(key);\n    if (!re) {\n      re = factory();\n      if (regexCache.size > 2000) regexCache.clear();\n      regexCache.set(key, re);\n    }\n    return re;\n  }\n\n  function regexFromPattern(pattern) {\n    const value = String(pattern || "");\n    if (value.startsWith("/")) {\n      const lastSlash = value.lastIndexOf("/");\n      if (lastSlash > 0) {\n        const body = value.slice(1, lastSlash);\n        const flags = value.slice(lastSlash + 1).replace(/[gy]/g, "");\n        if (!/^[dimsuv]*$/.test(flags) || new Set(flags).size !== flags.length) {\n          throw new Error("Unsupported or duplicate regular-expression flags.");\n        }\n        return new RegExp(body, flags);\n      }\n    }\n    return new RegExp(value);\n  }\n\n  function validateRule(value) {\n    const rule = normalizeRule(value);\n    if (!rule.pattern) return { ok: false, error: "Enter a URL, hostname, wildcard, or regular expression." };\n    if (rule.pattern.length > MAX_PATTERN_LENGTH) return { ok: false, error: `Patterns are limited to ${MAX_PATTERN_LENGTH} characters.` };\n\n    if (rule.type === "host" && !hostFromPattern(rule.pattern, true)) {\n      return { ok: false, error: "Enter a valid hostname, optionally including a port." };\n    }\n    if (rule.type === "domain" && !hostFromPattern(rule.pattern, false)) {\n      return { ok: false, error: "Enter a valid domain name." };\n    }\n    try {\n      if (rule.type === "wildcard") wildcardRegex(rule.pattern);\n      if (rule.type === "regex") regexFromPattern(rule.pattern);\n    } catch (error) {\n      return { ok: false, error: error && error.message ? error.message : "The pattern is invalid." };\n    }\n    return { ok: true, rule };\n  }\n\n  function ruleMatches(value, context = {}) {\n    const rule = normalizeRule(value);\n    if (!rule.enabled || !rule.pattern) return false;\n\n    const browser = String(context.browser || "").toLowerCase();\n    if (rule.browser !== "all" && rule.browser !== browser) return false;\n\n    const incognito = Boolean(context.incognito);\n    if (rule.context === "regular" && incognito) return false;\n    if (rule.context === "incognito" && !incognito) return false;\n\n    const url = String(context.url || "");\n    if (!url) return false;\n\n    if (rule.type === "exact") return url === rule.pattern;\n\n    const parsed = parseUrl(url);\n    if (rule.type === "host") {\n      if (!parsed) return false;\n      const target = hostFromPattern(rule.pattern, true);\n      const actual = target.includes(":") ? parsed.host.toLowerCase() : parsed.hostname.toLowerCase();\n      return actual === target;\n    }\n    if (rule.type === "domain") {\n      if (!parsed) return false;\n      const target = hostFromPattern(rule.pattern, false);\n      const actual = parsed.hostname.toLowerCase();\n      return actual === target || actual.endsWith(`.${target}`);\n    }\n    try {\n      if (rule.type === "wildcard") {\n        return getCachedRegex("w:" + rule.pattern, () => wildcardRegex(rule.pattern)).test(url);\n      }\n      if (rule.type === "regex") {\n        return getCachedRegex("r:" + rule.pattern, () => regexFromPattern(rule.pattern)).test(url);\n      }\n    } catch (_) {\n      return false;\n    }\n    return false;\n  }\n\n  function evaluate(value, context = {}, at = Date.now()) {\n    const settings = normalizeSettings(value);\n    if (!settings.enabled) return { excluded: true, reason: "guard_disabled", rule: null };\n    if (settings.pausedUntil > at) return { excluded: true, reason: "guard_paused", rule: null };\n\n    for (const rule of settings.rules) {\n      if (ruleMatches(rule, context)) return { excluded: true, reason: "exception_rule", rule };\n    }\n    return { excluded: false, reason: "", rule: null };\n  }\n\n  function suggestPattern(url, type) {\n    const normalizedType = normalizeType(type);\n    const text = String(url || "");\n    const parsed = parseUrl(text);\n    if (normalizedType === "exact") return text;\n    if (normalizedType === "host") return parsed ? parsed.host : "";\n    if (normalizedType === "domain") return parsed ? parsed.hostname : "";\n    if (normalizedType === "wildcard") {\n      if (!parsed) return text;\n      if (parsed.protocol === "file:") return `${parsed.protocol}//${parsed.pathname.replace(/[^/]*$/, "*")}`;\n      return `${parsed.protocol}//${parsed.host}/*`;\n    }\n    return text ? `^${escapeRegex(text)}$` : "";\n  }\n\n  globalThis.CBDTGExceptions = Object.freeze({\n    STORAGE_KEY,\n    SCHEMA_VERSION,\n    MAX_RULES,\n    TYPES,\n    defaultSettings,\n    normalizeRule,\n    normalizeSettings,\n    validateRule,\n    ruleMatches,\n    evaluate,\n    suggestPattern,\n  });\n})();\n', 'manifest.json': '{\n  "manifest_version": 3,\n  "name": "Cross-Browser Duplicate Tab Guard",\n  "version": "2.3.0",\n  "description": "Prevents exact-URL duplicate tabs across Brave, Chrome, and Chromium, identifying pre-existing duplicate browser locations, with editable exceptions, runtime-ID-aware own-page exclusions, and navigation API compatibility fallbacks.",\n  "minimum_chrome_version": "116",\n  "key": "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAuiXZIY3iosKFylijfVygTd2esQ0a6DTNkYhXeqc/atLRzLnihPQpYu7nAJVd0tN+3xaotBnQyK846tIbZce/HMotXjMCWgWLyFqNLfcSWJnHQo1KLY7GDhXJ1OhMGDPGmDsADdff2qP+G6iZ0jT86AiXnSxmusr6uHB9tWFKcOO2hrm01WE+KmzOpC3NOlyQg2EOjTXmJp6JVq1jOJlSPjZ2Myq/jzW5BWZibeB7xErCnxaNT6JDtzW1C0NYdjVejozUCIuslb587mlhLG0tcbGhTZ7tI5Iq/e3h/5Q1xkr/eBe7fAuuJFBEr9XT3kYbABJyvA05Ofiyd7pzwNCvKwIDAQAB",\n  "incognito": "split",\n  "permissions": [\n    "nativeMessaging",\n    "storage",\n    "tabs",\n    "webNavigation",\n    "alarms"\n  ],\n  "host_permissions": [\n    "<all_urls>"\n  ],\n  "background": {\n    "service_worker": "service_worker.js"\n  },\n  "content_scripts": [\n    {\n      "matches": [\n        "<all_urls>"\n      ],\n      "js": [\n        "content.js"\n      ],\n      "run_at": "document_start",\n      "all_frames": false,\n      "match_about_blank": true,\n      "match_origin_as_fallback": true\n    }\n  ],\n  "action": {\n    "default_title": "Duplicate Tab Guard status",\n    "default_popup": "popup.html"\n  },\n  "content_security_policy": {\n    "extension_pages": "script-src \'self\'; object-src \'self\'; connect-src \'self\' ws://127.0.0.1:* http://127.0.0.1:*;"\n  }\n}\n', 'popup.css': ':root { color-scheme: light dark; font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }\n* { box-sizing: border-box; }\nbody { margin: 0; width: 390px; max-height: 600px; overflow-y: auto; color: #171b24; background: #fff; }\nmain { padding: 18px; }\n.heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }\n.eyebrow { margin: 0 0 3px; color: #667184; font-size: 9px; font-weight: 800; letter-spacing: .13em; text-transform: uppercase; }\nh1 { margin: 0; font-size: 18px; letter-spacing: -.02em; }\n.pill { padding: 5px 8px; border-radius: 999px; background: #eef1f5; color: #566172; font-size: 10px; font-weight: 750; white-space: nowrap; }\n.pill.ok { background: #e7f6ee; color: #027a48; }\n.pill.error { background: #fff0ed; color: #b42318; }\ndl { margin: 17px 0; border: 1px solid #e2e6ec; border-radius: 12px; overflow: hidden; }\ndl div { display: flex; justify-content: space-between; gap: 16px; padding: 9px 12px; border-bottom: 1px solid #e9ecf1; }\ndl div:last-child { border-bottom: 0; }\ndt { color: #697386; font-size: 11px; }\ndd { margin: 0; font-size: 11px; font-weight: 700; text-align: right; }\nlabel { display: block; margin: 0 0 6px; font-size: 11px; font-weight: 700; }\nselect, input, textarea { width: 100%; min-height: 38px; padding: 7px 9px; border: 1px solid #cfd5dd; border-radius: 9px; background: #fff; color: inherit; font: inherit; font-size: 12px; }\ntextarea { min-height: 96px; resize: vertical; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 10px; line-height: 1.45; }\nselect:focus, input:focus, textarea:focus, button:focus-visible, summary:focus-visible { outline: 3px solid rgba(80,101,232,.18); outline-offset: 1px; border-color: #5065e8; }\n.panel { margin-top: 14px; border: 1px solid #e2e6ec; border-radius: 12px; overflow: hidden; }\n.panel > summary { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 11px 12px; cursor: pointer; font-size: 12px; font-weight: 800; list-style: none; }\n.panel > summary::-webkit-details-marker, .subpanel > summary::-webkit-details-marker { display: none; }\n.panel > summary::after, .subpanel > summary::after { content: "+"; color: #697386; font-size: 15px; font-weight: 500; }\n.panel[open] > summary::after, .subpanel[open] > summary::after { content: "−"; }\n.summary-count { margin-left: auto; padding: 2px 6px; border-radius: 999px; background: #eef1f5; color: #596476; font-size: 9px; }\n.panel-body { padding: 0 12px 12px; border-top: 1px solid #e9ecf1; }\n.guard-row { padding: 12px 0; border-bottom: 1px solid #edf0f4; }\n.switch-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin: 0; }\n.switch-row span { display: grid; gap: 2px; }\n.switch-row strong { font-size: 11px; }\n.switch-row small { color: #737e8f; font-size: 9px; font-weight: 500; }\n.switch-row input { appearance: none; width: 36px; min-width: 36px; height: 21px; min-height: 21px; padding: 0; border: 0; border-radius: 999px; background: #c8ced7; cursor: pointer; position: relative; }\n.switch-row input::after { content: ""; position: absolute; width: 17px; height: 17px; top: 2px; left: 2px; border-radius: 50%; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.25); transition: transform .15s ease; }\n.switch-row input:checked { background: #202633; }\n.switch-row input:checked::after { transform: translateX(15px); }\n.pause-row { display: grid; grid-template-columns: 1fr auto auto; gap: 7px; margin-top: 10px; }\n.compact { min-height: 34px; padding: 6px 9px; }\n.microcopy { margin: 8px 0 0; color: #697386; font-size: 9px; line-height: 1.45; }\n.current-card { margin: 12px 0; padding: 11px; border: 1px solid #e5e8ed; border-radius: 10px; background: #f8f9fb; }\n.mini-label { margin: 0 0 4px; color: #697386; font-size: 9px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }\n.current-url { margin: 0; max-height: 34px; overflow: hidden; overflow-wrap: anywhere; color: #343b48; font: 10px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }\n.quick-actions { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 9px; }\n.quick-actions button { flex: 1 1 auto; }\n.subpanel { margin-top: 10px; border: 1px solid #e5e8ed; border-radius: 10px; overflow: hidden; }\n.subpanel > summary { display: flex; align-items: center; justify-content: space-between; padding: 9px 10px; cursor: pointer; list-style: none; color: #394150; font-size: 10px; font-weight: 800; }\n.subpanel form, .subpanel > p, .subpanel > .quick-actions, .subpanel > #importArea { margin: 0; padding: 10px; border-top: 1px solid #edf0f4; }\n.subpanel > p + .quick-actions { padding-top: 0; border-top: 0; }\n.field-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }\n.field-grid + label, label + .input-action, .input-action + .field-grid, .field-grid + .microcopy { margin-top: 9px; }\n.optional { color: #8690a0; font-weight: 500; }\n.input-action { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 7px; align-items: end; }\n.input-action button { white-space: nowrap; }\n.form-actions { display: flex; justify-content: flex-end; gap: 7px; margin-top: 10px; }\n.rules-heading { display: flex; justify-content: space-between; margin: 13px 1px 7px; color: #596476; font-size: 9px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; }\n.rules-list { display: grid; gap: 7px; }\n.empty-state { margin: 0; padding: 12px; border: 1px dashed #d8dde5; border-radius: 9px; color: #778192; font-size: 10px; line-height: 1.45; text-align: center; }\n.rule-item { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 8px; align-items: start; padding: 9px; border: 1px solid #e3e7ed; border-radius: 9px; background: #fff; }\n.rule-item.disabled { opacity: .58; }\n.rule-toggle { width: 15px; height: 15px; min-height: 15px; margin: 2px 0 0; }\n.rule-content { min-width: 0; }\n.rule-pattern { margin: 0; overflow-wrap: anywhere; font: 10px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }\n.rule-note { margin: 4px 0 0; color: #667184; font-size: 9px; line-height: 1.35; }\n.rule-meta { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }\n.badge { padding: 2px 5px; border-radius: 999px; background: #eef1f5; color: #667184; font-size: 8px; font-weight: 750; }\n.rule-actions { display: flex; gap: 3px; }\n.icon-button { min-height: 27px; padding: 4px 6px; background: transparent; color: #596476; font-size: 9px; }\n.icon-button:hover { background: #eef1f5; }\n.icon-button.danger { color: #b42318; }\n.transfer-panel { margin-top: 10px; }\n.import-actions { margin-top: 8px; }\n.note { min-height: 34px; margin: 10px 0 0; color: #697386; font-size: 10px; line-height: 1.45; }\n.note.success { color: #027a48; }\n.note.error { color: #b42318; }\n.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }\nbutton { min-height: 37px; padding: 8px 11px; border: 0; border-radius: 9px; background: #202633; color: #fff; font: inherit; font-size: 11px; font-weight: 750; cursor: pointer; }\nbutton.secondary { background: #eef1f5; color: #252b36; }\nbutton:disabled { opacity: .48; cursor: not-allowed; }\ncode { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }\n[hidden] { display: none !important; }\n@media (prefers-color-scheme: dark) {\n  body { background: #171a20; color: #f3f5f8; }\n  .pill, button.secondary, .summary-count, .badge { background: #292e37; color: #c3cad5; }\n  dl, .panel, .subpanel, .current-card, .rule-item { border-color: #343a45; }\n  dl div, .panel-body, .guard-row, .subpanel form, .subpanel > p, .subpanel > .quick-actions, .subpanel > #importArea { border-color: #303641; }\n  dt, .note, .eyebrow, .microcopy, .mini-label, .rules-heading, .switch-row small, .rule-note { color: #a6b0bf; }\n  select, input, textarea { background: #22262e; border-color: #3e4652; }\n  .current-card { background: #20242b; }\n  .current-url, .subpanel > summary { color: #dce1e9; }\n  .rule-item { background: #1d2128; }\n  .empty-state { border-color: #3a414d; color: #9ca6b5; }\n  .switch-row input { background: #4a5260; }\n  .switch-row input:checked { background: #f0f2f5; }\n  button { background: #f0f2f5; color: #171a20; }\n  .icon-button { background: transparent; color: #aeb7c5; }\n  .icon-button:hover { background: #292e37; }\n}\n', 'popup.html': '<!doctype html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1">\n  <title>Duplicate Tab Guard</title>\n  <link rel="stylesheet" href="popup.css">\n</head>\n<body>\n  <main>\n    <div class="heading">\n      <div>\n        <p class="eyebrow">Cross-browser</p>\n        <h1>Duplicate Tab Guard</h1>\n      </div>\n      <span id="connection" class="pill">Checking…</span>\n    </div>\n\n    <dl>\n      <div><dt>Guard</dt><dd id="guardStatus">—</dd></div>\n      <div><dt>Browser</dt><dd id="browser">—</dd></div>\n      <div><dt>Incognito / Private</dt><dd id="incognito">—</dd></div>\n      <div><dt>File URLs</dt><dd id="files">—</dd></div>\n      <div><dt>Exceptions</dt><dd id="exceptionCount">—</dd></div>\n      <div><dt>Pending alerts</dt><dd id="pending">—</dd></div>\n    </dl>\n\n    <label for="browserOverride">Browser identity override</label>\n    <select id="browserOverride">\n      <option value="auto">Automatic</option>\n      <option value="brave">Brave</option>\n      <option value="chrome">Chrome</option>\n      <option value="chromium">Chromium</option>\n    </select>\n\n    <details id="exceptionsPanel" class="panel" open>\n      <summary>\n        <span>Exceptions &amp; behavior</span>\n        <span id="summaryCount" class="summary-count">0</span>\n      </summary>\n      <div class="panel-body">\n        <div class="guard-row">\n          <label class="switch-row" for="guardEnabled">\n            <span>\n              <strong>Duplicate guard enabled</strong>\n              <small>Disable without removing any rules.</small>\n            </span>\n            <input id="guardEnabled" type="checkbox" role="switch">\n          </label>\n          <div class="pause-row">\n            <select id="pauseDuration" aria-label="Pause duration">\n              <option value="15">15 minutes</option>\n              <option value="60" selected>1 hour</option>\n              <option value="1440">24 hours</option>\n            </select>\n            <button id="pauseGuard" type="button" class="secondary compact">Pause</button>\n            <button id="resumeGuard" type="button" class="secondary compact" hidden>Resume</button>\n          </div>\n          <p id="guardMessage" class="microcopy"></p>\n        </div>\n\n        <div class="current-card">\n          <p class="mini-label">Current page</p>\n          <p id="currentUrl" class="current-url">Reading active tab…</p>\n          <div class="quick-actions">\n            <button id="addExact" type="button" class="secondary">Exclude exact URL</button>\n            <button id="addHost" type="button" class="secondary">Exclude hostname</button>\n          </div>\n        </div>\n\n        <details id="customRuleDetails" class="subpanel">\n          <summary id="customRuleSummary">Add a custom rule</summary>\n          <form id="ruleForm">\n            <div class="field-grid">\n              <div>\n                <label for="ruleType">Match type</label>\n                <select id="ruleType">\n                  <option value="exact">Exact URL</option>\n                  <option value="host">Hostname</option>\n                  <option value="domain">Domain + subdomains</option>\n                  <option value="wildcard">Wildcard URL</option>\n                  <option value="regex">Regular expression</option>\n                </select>\n              </div>\n              <div>\n                <label for="ruleBrowser">Browser scope</label>\n                <select id="ruleBrowser">\n                  <option value="all">All browsers</option>\n                  <option value="brave">Brave only</option>\n                  <option value="chrome">Chrome only</option>\n                  <option value="chromium">Chromium only</option>\n                </select>\n              </div>\n            </div>\n\n            <label for="rulePattern">Pattern</label>\n            <div class="input-action">\n              <input id="rulePattern" type="text" autocomplete="off" spellcheck="false" placeholder="https://example.com/path">\n              <button id="useCurrent" type="button" class="secondary compact">Use current</button>\n            </div>\n\n            <div class="field-grid">\n              <div>\n                <label for="ruleContext">Window scope</label>\n                <select id="ruleContext">\n                  <option value="all">Regular + private</option>\n                  <option value="regular">Regular only</option>\n                  <option value="incognito">Private / incognito only</option>\n                </select>\n              </div>\n              <div>\n                <label for="ruleNote">Description <span class="optional">optional</span></label>\n                <input id="ruleNote" type="text" maxlength="160" placeholder="Why this is excluded">\n              </div>\n            </div>\n\n            <p class="microcopy">Wildcard rules use <code>*</code> for any text and <code>?</code> for one character. Regex rules may use JavaScript literal form such as <code>/pattern/i</code>.</p>\n            <div class="form-actions">\n              <button id="cancelEdit" type="button" class="secondary" hidden>Cancel</button>\n              <button id="saveRule" type="submit">Add exception</button>\n            </div>\n          </form>\n        </details>\n\n        <div class="rules-heading">\n          <span>Saved rules</span>\n          <span id="rulesCount">0</span>\n        </div>\n        <p id="rulesEmpty" class="empty-state">No exceptions yet. Use the current-page buttons or add a custom rule.</p>\n        <div id="rulesList" class="rules-list"></div>\n\n        <details class="subpanel transfer-panel">\n          <summary>Import / export rules</summary>\n          <p class="microcopy">Copy the same rule set into Brave, Chrome, or Chromium to keep browser profiles aligned.</p>\n          <div class="quick-actions">\n            <button id="copyRules" type="button" class="secondary">Copy JSON</button>\n            <button id="showImport" type="button" class="secondary">Import JSON</button>\n          </div>\n          <div id="importArea" hidden>\n            <label for="importText">Paste exported JSON</label>\n            <textarea id="importText" rows="5" spellcheck="false"></textarea>\n            <div class="input-action import-actions">\n              <select id="importMode" aria-label="Import mode">\n                <option value="merge">Merge with existing</option>\n                <option value="replace">Replace existing</option>\n              </select>\n              <button id="applyImport" type="button">Apply import</button>\n            </div>\n          </div>\n        </details>\n      </div>\n    </details>\n\n    <p id="note" class="note" aria-live="polite"></p>\n    <div class="actions">\n      <button id="details" type="button" class="secondary">Permissions</button>\n      <button id="rescan" type="button">Rescan tabs</button>\n    </div>\n  </main>\n  <script src="exceptions.js"></script>\n  <script src="popup.js"></script>\n</body>\n</html>\n', 'popup.js': '"use strict";\n\nconst Rules = CBDTGExceptions;\nconst labels = { brave: "Brave", chrome: "Chrome", chromium: "Chromium", all: "All browsers" };\nconst contextLabels = { all: "Regular + private", regular: "Regular only", incognito: "Private only" };\n\nconst connectionEl = document.getElementById("connection");\nconst guardStatusEl = document.getElementById("guardStatus");\nconst browserEl = document.getElementById("browser");\nconst incognitoEl = document.getElementById("incognito");\nconst filesEl = document.getElementById("files");\nconst exceptionCountEl = document.getElementById("exceptionCount");\nconst pendingEl = document.getElementById("pending");\nconst overrideEl = document.getElementById("browserOverride");\nconst noteEl = document.getElementById("note");\nconst detailsEl = document.getElementById("details");\nconst rescanEl = document.getElementById("rescan");\nconst guardEnabledEl = document.getElementById("guardEnabled");\nconst pauseDurationEl = document.getElementById("pauseDuration");\nconst pauseGuardEl = document.getElementById("pauseGuard");\nconst resumeGuardEl = document.getElementById("resumeGuard");\nconst guardMessageEl = document.getElementById("guardMessage");\nconst currentUrlEl = document.getElementById("currentUrl");\nconst addExactEl = document.getElementById("addExact");\nconst addHostEl = document.getElementById("addHost");\nconst summaryCountEl = document.getElementById("summaryCount");\nconst rulesCountEl = document.getElementById("rulesCount");\nconst rulesEmptyEl = document.getElementById("rulesEmpty");\nconst rulesListEl = document.getElementById("rulesList");\nconst customRuleDetailsEl = document.getElementById("customRuleDetails");\nconst customRuleSummaryEl = document.getElementById("customRuleSummary");\nconst ruleFormEl = document.getElementById("ruleForm");\nconst ruleTypeEl = document.getElementById("ruleType");\nconst rulePatternEl = document.getElementById("rulePattern");\nconst ruleBrowserEl = document.getElementById("ruleBrowser");\nconst ruleContextEl = document.getElementById("ruleContext");\nconst ruleNoteEl = document.getElementById("ruleNote");\nconst useCurrentEl = document.getElementById("useCurrent");\nconst cancelEditEl = document.getElementById("cancelEdit");\nconst saveRuleEl = document.getElementById("saveRule");\nconst copyRulesEl = document.getElementById("copyRules");\nconst showImportEl = document.getElementById("showImport");\nconst importAreaEl = document.getElementById("importArea");\nconst importTextEl = document.getElementById("importText");\nconst importModeEl = document.getElementById("importMode");\nconst applyImportEl = document.getElementById("applyImport");\n\nlet settings = Rules.defaultSettings();\nlet activeContext = { url: "", browser: "", incognito: false, trackable: false };\nlet editingRuleId = "";\nlet statusRefreshTimer = null;\n\nfunction setNote(message, kind = "") {\n  noteEl.textContent = String(message || "");\n  noteEl.className = `note ${kind}`.trim();\n}\n\nfunction formatRemaining(timestamp) {\n  const milliseconds = Math.max(0, Number(timestamp || 0) - Date.now());\n  const minutes = Math.ceil(milliseconds / 60000);\n  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"}`;\n  const hours = Math.ceil(minutes / 60);\n  return `${hours} hour${hours === 1 ? "" : "s"}`;\n}\n\nfunction guardStateText() {\n  if (!settings.enabled) return "Disabled";\n  if (settings.pausedUntil > Date.now()) return `Paused (${formatRemaining(settings.pausedUntil)})`;\n  return "Active";\n}\n\nfunction activeRuleCount() {\n  return settings.rules.filter((rule) => rule.enabled).length;\n}\n\nasync function saveSettings(next, message = "Settings saved.") {\n  settings = Rules.normalizeSettings(next);\n  await chrome.storage.local.set({ [Rules.STORAGE_KEY]: settings });\n  renderSettings();\n  setNote(message, "success");\n}\n\nfunction ruleKey(rule) {\n  return [rule.type, rule.pattern, rule.browser, rule.context].join("\\u0000");\n}\n\nasync function addOrUpdateRule(candidate, message) {\n  const validation = Rules.validateRule(candidate);\n  if (!validation.ok) throw new Error(validation.error);\n  const rule = validation.rule;\n  const rules = [...settings.rules];\n\n  if (editingRuleId) {\n    const index = rules.findIndex((item) => item.id === editingRuleId);\n    if (index < 0) throw new Error("That exception no longer exists.");\n    rule.id = editingRuleId;\n    rule.createdAt = rules[index].createdAt;\n    rules[index] = rule;\n  } else {\n    const duplicate = rules.find((item) => ruleKey(item) === ruleKey(rule));\n    if (duplicate) {\n      duplicate.enabled = true;\n      if (rule.note) duplicate.note = rule.note;\n      await saveSettings({ ...settings, rules }, "That exception already existed and is now enabled.");\n      return;\n    }\n    rules.unshift(rule);\n  }\n\n  if (rules.length > Rules.MAX_RULES) throw new Error(`A maximum of ${Rules.MAX_RULES} rules is supported.`);\n  await saveSettings({ ...settings, rules }, message || (editingRuleId ? "Exception updated." : "Exception added."));\n  resetRuleForm();\n}\n\nfunction resetRuleForm() {\n  editingRuleId = "";\n  ruleFormEl.reset();\n  ruleTypeEl.value = "exact";\n  ruleBrowserEl.value = "all";\n  ruleContextEl.value = "all";\n  cancelEditEl.hidden = true;\n  saveRuleEl.textContent = "Add exception";\n  customRuleSummaryEl.textContent = "Add a custom rule";\n}\n\nfunction editRule(rule) {\n  editingRuleId = rule.id;\n  ruleTypeEl.value = rule.type;\n  rulePatternEl.value = rule.pattern;\n  ruleBrowserEl.value = rule.browser;\n  ruleContextEl.value = rule.context;\n  ruleNoteEl.value = rule.note || "";\n  cancelEditEl.hidden = false;\n  saveRuleEl.textContent = "Save changes";\n  customRuleSummaryEl.textContent = "Edit exception rule";\n  customRuleDetailsEl.open = true;\n  rulePatternEl.focus();\n}\n\nfunction badge(text) {\n  const element = document.createElement("span");\n  element.className = "badge";\n  element.textContent = text;\n  return element;\n}\n\nfunction renderRules() {\n  rulesListEl.replaceChildren();\n  rulesEmptyEl.hidden = settings.rules.length > 0;\n  rulesCountEl.textContent = String(settings.rules.length);\n  summaryCountEl.textContent = String(activeRuleCount());\n\n  for (const rule of settings.rules) {\n    const item = document.createElement("article");\n    item.className = `rule-item${rule.enabled ? "" : " disabled"}`;\n\n    const toggle = document.createElement("input");\n    toggle.type = "checkbox";\n    toggle.className = "rule-toggle";\n    toggle.checked = rule.enabled;\n    toggle.title = rule.enabled ? "Disable this rule" : "Enable this rule";\n    toggle.setAttribute("aria-label", `${rule.enabled ? "Disable" : "Enable"} ${rule.pattern}`);\n    toggle.addEventListener("change", async () => {\n      const rules = settings.rules.map((itemRule) => itemRule.id === rule.id ? { ...itemRule, enabled: toggle.checked } : itemRule);\n      await saveSettings({ ...settings, rules }, toggle.checked ? "Exception enabled." : "Exception disabled.");\n    });\n\n    const content = document.createElement("div");\n    content.className = "rule-content";\n    const pattern = document.createElement("p");\n    pattern.className = "rule-pattern";\n    pattern.textContent = rule.pattern;\n    content.append(pattern);\n    if (rule.note) {\n      const note = document.createElement("p");\n      note.className = "rule-note";\n      note.textContent = rule.note;\n      content.append(note);\n    }\n    const meta = document.createElement("div");\n    meta.className = "rule-meta";\n    meta.append(\n      badge(Rules.TYPES[rule.type] || rule.type),\n      badge(labels[rule.browser] || rule.browser),\n      badge(contextLabels[rule.context] || rule.context),\n    );\n    content.append(meta);\n\n    const actions = document.createElement("div");\n    actions.className = "rule-actions";\n    const edit = document.createElement("button");\n    edit.type = "button";\n    edit.className = "icon-button";\n    edit.textContent = "Edit";\n    edit.addEventListener("click", () => editRule(rule));\n    const remove = document.createElement("button");\n    remove.type = "button";\n    remove.className = "icon-button danger";\n    remove.textContent = "Delete";\n    remove.addEventListener("click", async () => {\n      const rules = settings.rules.filter((itemRule) => itemRule.id !== rule.id);\n      if (editingRuleId === rule.id) resetRuleForm();\n      await saveSettings({ ...settings, rules }, "Exception deleted.");\n    });\n    actions.append(edit, remove);\n    item.append(toggle, content, actions);\n    rulesListEl.append(item);\n  }\n}\n\nfunction renderSettings() {\n  const paused = settings.enabled && settings.pausedUntil > Date.now();\n  guardEnabledEl.checked = settings.enabled;\n  pauseGuardEl.hidden = paused || !settings.enabled;\n  resumeGuardEl.hidden = !paused;\n  pauseDurationEl.disabled = paused || !settings.enabled;\n  guardMessageEl.textContent = !settings.enabled\n    ? "The guard is disabled; open tabs remain untouched."\n    : paused\n      ? `Duplicate handling resumes in ${formatRemaining(settings.pausedUntil)}.`\n      : "Rules apply immediately to new and already-open tabs.";\n  guardStatusEl.textContent = guardStateText();\n  exceptionCountEl.textContent = `${activeRuleCount()} active / ${settings.rules.length} total`;\n  renderRules();\n}\n\nfunction renderActiveContext() {\n  currentUrlEl.textContent = activeContext.trackable ? activeContext.url : "This page cannot be added as an exception.";\n  addExactEl.disabled = !activeContext.trackable;\n  addHostEl.disabled = !activeContext.trackable || !/^https?:\\/\\//i.test(activeContext.url);\n  useCurrentEl.disabled = !activeContext.trackable;\n}\n\nasync function refresh() {\n  try {\n    const [status, stored, current] = await Promise.all([\n      chrome.runtime.sendMessage({ type: "get_status" }),\n      chrome.storage.local.get([Rules.STORAGE_KEY]),\n      chrome.runtime.sendMessage({ type: "get_active_tab_context" }),\n    ]);\n    if (!status || !status.ok) throw new Error((status && status.error) || "Status unavailable.");\n\n    settings = Rules.normalizeSettings(stored[Rules.STORAGE_KEY]);\n    activeContext = current && current.ok ? current : activeContext;\n    connectionEl.textContent = status.native_connected ? "Connected" : "Disconnected";\n    connectionEl.className = `pill ${status.native_connected ? "ok" : "error"}`;\n    browserEl.textContent = labels[status.browser] || status.browser || "Unknown";\n    incognitoEl.textContent = status.incognito_allowed ? "Allowed" : "Enable in permissions";\n    filesEl.textContent = status.file_scheme_allowed ? "Allowed" : "Enable in permissions";\n    pendingEl.textContent = String(status.pending_incidents || 0);\n    overrideEl.value = status.browser_override || "auto";\n    renderSettings();\n    renderActiveContext();\n\n    if (!status.incognito_allowed || !status.file_scheme_allowed) {\n      setNote("Use Permissions to enable Allow in Incognito/Private and Allow access to file URLs. Browsers require those toggles to be set by the user.");\n    } else if (!status.native_connected) {\n      setNote(status.native_error || "Run the one-file installer again to repair the native coordinator.", "error");\n    } else if (!noteEl.textContent || noteEl.classList.contains("error")) {\n      setNote("Exact-URL coordination is active. Exception rules are stored locally in this browser profile.");\n    }\n  } catch (error) {\n    connectionEl.textContent = "Error";\n    connectionEl.className = "pill error";\n    setNote(error && error.message ? error.message : String(error), "error");\n  }\n}\n\nasync function addCurrent(type) {\n  if (!activeContext.trackable) throw new Error("The current page cannot be used as an exception.");\n  const pattern = Rules.suggestPattern(activeContext.url, type);\n  await addOrUpdateRule({ type, pattern, browser: "all", context: "all", enabled: true }, type === "exact" ? "Exact URL excluded." : "Hostname excluded.");\n}\n\nasync function copyText(value) {\n  try {\n    await navigator.clipboard.writeText(value);\n  } catch (_) {\n    importAreaEl.hidden = false;\n    importTextEl.value = value;\n    importTextEl.focus();\n    importTextEl.select();\n    if (!document.execCommand("copy")) throw new Error("Could not copy automatically. The JSON is selected for manual copying.");\n  }\n}\n\noverrideEl.addEventListener("change", async () => {\n  overrideEl.disabled = true;\n  try {\n    const result = await chrome.runtime.sendMessage({ type: "set_browser_override", value: overrideEl.value });\n    if (!result || !result.ok) throw new Error((result && result.error) || "Could not save browser identity.");\n    setNote("Browser identity updated.", "success");\n    setTimeout(refresh, 500);\n  } catch (error) {\n    setNote(error && error.message ? error.message : String(error), "error");\n  } finally {\n    overrideEl.disabled = false;\n  }\n});\n\nguardEnabledEl.addEventListener("change", async () => {\n  try {\n    await saveSettings({ ...settings, enabled: guardEnabledEl.checked, pausedUntil: guardEnabledEl.checked ? settings.pausedUntil : 0 }, guardEnabledEl.checked ? "Duplicate guard enabled." : "Duplicate guard disabled.");\n  } catch (error) {\n    setNote(error.message, "error");\n  }\n});\n\npauseGuardEl.addEventListener("click", async () => {\n  const minutes = Math.max(1, Number(pauseDurationEl.value) || 60);\n  await saveSettings({ ...settings, pausedUntil: Date.now() + minutes * 60000 }, `Duplicate handling paused for ${minutes < 60 ? `${minutes} minutes` : `${minutes / 60} hour${minutes === 60 ? "" : "s"}`}.`);\n});\n\nresumeGuardEl.addEventListener("click", async () => {\n  await saveSettings({ ...settings, pausedUntil: 0 }, "Duplicate handling resumed.");\n});\n\naddExactEl.addEventListener("click", () => addCurrent("exact").catch((error) => setNote(error.message, "error")));\naddHostEl.addEventListener("click", () => addCurrent("host").catch((error) => setNote(error.message, "error")));\n\nuseCurrentEl.addEventListener("click", () => {\n  if (!activeContext.trackable) return;\n  rulePatternEl.value = Rules.suggestPattern(activeContext.url, ruleTypeEl.value);\n  rulePatternEl.focus();\n});\n\nruleTypeEl.addEventListener("change", () => {\n  if (!rulePatternEl.value && activeContext.trackable) {\n    rulePatternEl.placeholder = Rules.suggestPattern(activeContext.url, ruleTypeEl.value);\n  }\n});\n\nruleFormEl.addEventListener("submit", async (event) => {\n  event.preventDefault();\n  saveRuleEl.disabled = true;\n  try {\n    await addOrUpdateRule({\n      id: editingRuleId || undefined,\n      enabled: true,\n      type: ruleTypeEl.value,\n      pattern: rulePatternEl.value,\n      browser: ruleBrowserEl.value,\n      context: ruleContextEl.value,\n      note: ruleNoteEl.value,\n    });\n  } catch (error) {\n    setNote(error && error.message ? error.message : String(error), "error");\n  } finally {\n    saveRuleEl.disabled = false;\n  }\n});\n\ncancelEditEl.addEventListener("click", resetRuleForm);\n\ncopyRulesEl.addEventListener("click", async () => {\n  const payload = {\n    format: "CrossBrowserDuplicateTabGuard-exceptions",\n    version: 1,\n    exportedAt: new Date().toISOString(),\n    settings,\n  };\n  try {\n    await copyText(JSON.stringify(payload, null, 2));\n    setNote("Exception rules copied as JSON.", "success");\n  } catch (error) {\n    setNote(error.message, "error");\n  }\n});\n\nshowImportEl.addEventListener("click", () => {\n  importAreaEl.hidden = !importAreaEl.hidden;\n  if (!importAreaEl.hidden) importTextEl.focus();\n});\n\napplyImportEl.addEventListener("click", async () => {\n  applyImportEl.disabled = true;\n  try {\n    const parsed = JSON.parse(importTextEl.value);\n    const incoming = Array.isArray(parsed)\n      ? { ...Rules.defaultSettings(), rules: parsed }\n      : parsed && parsed.settings\n        ? parsed.settings\n        : parsed;\n    const normalized = Rules.normalizeSettings(incoming);\n    if (!Array.isArray(incoming && incoming.rules)) throw new Error("The imported JSON does not contain a rules array.");\n\n    let next = normalized;\n    if (importModeEl.value === "merge") {\n      const merged = [...settings.rules];\n      const keys = new Set(merged.map(ruleKey));\n      for (const rule of normalized.rules) {\n        if (!keys.has(ruleKey(rule))) {\n          merged.push(rule);\n          keys.add(ruleKey(rule));\n        }\n      }\n      next = { ...settings, rules: merged.slice(0, Rules.MAX_RULES) };\n    }\n    await saveSettings(next, `${normalized.rules.length} imported rule${normalized.rules.length === 1 ? "" : "s"} processed.`);\n    importTextEl.value = "";\n    importAreaEl.hidden = true;\n  } catch (error) {\n    setNote(error && error.message ? error.message : String(error), "error");\n  } finally {\n    applyImportEl.disabled = false;\n  }\n});\n\ndetailsEl.addEventListener("click", () => {\n  chrome.runtime.sendMessage({ type: "open_extension_details" });\n});\n\nrescanEl.addEventListener("click", async () => {\n  rescanEl.disabled = true;\n  try {\n    const result = await chrome.runtime.sendMessage({ type: "rescan_tabs" });\n    if (!result || !result.ok) throw new Error((result && result.error) || "Rescan failed.");\n    setNote("Open tabs and exception rules were reconciled.", "success");\n  } catch (error) {\n    setNote(error && error.message ? error.message : String(error), "error");\n  } finally {\n    rescanEl.disabled = false;\n    setTimeout(refresh, 600);\n  }\n});\n\nchrome.storage.onChanged.addListener((changes, areaName) => {\n  if (areaName !== "local" || !changes[Rules.STORAGE_KEY]) return;\n  settings = Rules.normalizeSettings(changes[Rules.STORAGE_KEY].newValue);\n  renderSettings();\n});\n\nstatusRefreshTimer = setInterval(() => {\n  if (settings.pausedUntil > 0 && settings.pausedUntil <= Date.now()) {\n    settings = { ...settings, pausedUntil: 0 };\n    chrome.storage.local.set({ [Rules.STORAGE_KEY]: settings }).catch(() => {});\n  }\n  renderSettings();\n}, 15000);\n\nwindow.addEventListener("unload", () => clearInterval(statusRefreshTimer));\nrefresh();\n', 'service_worker.js': '"use strict";\n\n// CBDTG_EXCEPTIONS_PATCH_V1\nimportScripts("exceptions.js");\n\nconst VERSION = "2.3.0";\nconst NATIVE_HOST = "systems.venturi.duplicate_tab_guard";\nconst FALLBACK_WS_URL = "ws://127.0.0.1:__WS_PORT__/__AUTH_TOKEN__";\nconst BROWSERS = new Set(["brave", "chrome", "chromium"]);\nconst SNAPSHOT_INTERVAL_MS = 15_000;\nconst REQUEST_TIMEOUT_MS = 12_000;\nconst TRANSFER_REQUEST_TIMEOUT_MS = 30_000;\nconst SESSION_STORAGE_KEY = "pending_duplicate_incidents_v2";\nconst EXCEPTION_SETTINGS_KEY = CBDTGExceptions.STORAGE_KEY;\n\nlet exceptionSettings = CBDTGExceptions.defaultSettings();\nlet exceptionSettingsPromise = null;\nlet exceptionReconcileTimer = null;\n\nlet nativePort = null;\nlet nativeReady = false;\nlet nativeConnecting = false;\nlet reconnectDelayMs = 500;\nlet reconnectTimer = null;\nlet snapshotTimer = null;\nlet runtimeBrowser = "chromium";\nlet profileId = null;\nlet sessionId = crypto.randomUUID();\nlet incognitoAllowed = false;\nlet fileSchemeAllowed = false;\nlet lastNativeError = "";\nlet transportKind = "none";\n\nconst readyWaiters = [];\nconst pendingRequests = new Map();\nconst claimCache = new Map();\nconst claimEpoch = new Map();\nconst pendingIncidents = new Map();\nconst fallbackTabs = new Set();\nconst incidentUiEpoch = new Map();\nconst TRANSIENT_URLS = new Set([\n  "about:blank",\n  "about:srcdoc",\n  "chrome://newtab/",\n  "brave://newtab/",\n  "chrome-search://local-ntp/local-ntp.html",\n]);\n\nfunction now() {\n  return Date.now();\n}\n\nfunction browserLabel(browser) {\n  return ({ brave: "Brave", chrome: "Chrome", chromium: "Chromium" })[browser] || browser;\n}\n\n// CBDTG_OWN_EXTENSION_PAGE_EXCLUSION_V1\nconst OWN_EXTENSION_MANAGER_SCHEMES = new Set(["chrome:", "brave:", "chromium:"]);\n\nfunction isOwnExtensionUrl(url) {\n  return typeof url === "string" && url.startsWith(chrome.runtime.getURL(""));\n}\n\n// CBDTG_OWN_EXTENSION_MANAGER_RUNTIME_ID_V2\nfunction containsRuntimeExtensionIdToken(value) {\n  let text = String(value || "");\n  try {\n    text = decodeURIComponent(text);\n  } catch (_) {\n    // Use the undecoded value when malformed percent-encoding is present.\n  }\n  const runtimeId = String(chrome.runtime.id || "").toLowerCase();\n  if (!runtimeId) return false;\n  return text\n    .toLowerCase()\n    .split(/[^a-z0-9]+/)\n    .includes(runtimeId);\n}\n\nfunction isOwnExtensionManagementUrl(url) {\n  if (typeof url !== "string" || !url) return false;\n  try {\n    const parsed = new URL(url);\n    if (!OWN_EXTENSION_MANAGER_SCHEMES.has(parsed.protocol.toLowerCase())) return false;\n    if (parsed.hostname.toLowerCase() !== "extensions") return false;\n\n    for (const [, value] of parsed.searchParams) {\n      if (containsRuntimeExtensionIdToken(value)) return true;\n    }\n    return containsRuntimeExtensionIdToken(parsed.pathname) || containsRuntimeExtensionIdToken(parsed.hash);\n  } catch (_) {\n    return false;\n  }\n}\n\nfunction isBuiltInExcludedUrl(url) {\n  return isOwnExtensionUrl(url) || isOwnExtensionManagementUrl(url);\n}\n\nfunction isTrackableUrl(url) {\n  return typeof url === "string" && url.length > 0 && !isBuiltInExcludedUrl(url);\n}\n\nfunction isTransientUrl(url) {\n  return TRANSIENT_URLS.has(String(url || ""));\n}\n\nfunction advanceClaimEpoch(tabId) {\n  const epoch = (claimEpoch.get(tabId) || 0) + 1;\n  claimEpoch.set(tabId, epoch);\n  return epoch;\n}\n\nasync function currentTabForClaim(tabId, expectedUrl, epoch) {\n  if (claimEpoch.get(tabId) !== epoch) return null;\n  try {\n    const tab = await chrome.tabs.get(tabId);\n    if (claimEpoch.get(tabId) !== epoch) return null;\n    if (Boolean(tab.incognito) !== Boolean(chrome.extension.inIncognitoContext)) return null;\n    return tabUrl(tab) === expectedUrl ? tab : null;\n  } catch (_) {\n    return null;\n  }\n}\n\nfunction tabUrl(tab) {\n  const url = (tab && (tab.url || tab.pendingUrl)) || "";\n  return typeof url === "string" ? url : "";\n}\n\nfunction sanitizeTitle(value) {\n  return String(value || "").replace(/\\s+/g, " ").trim().slice(0, 2048);\n}\n\nfunction randomId() {\n  return crypto.randomUUID();\n}\n\nasync function ensureExceptionSettings(force = false) {\n  if (force || !exceptionSettingsPromise) {\n    exceptionSettingsPromise = chrome.storage.local.get([EXCEPTION_SETTINGS_KEY])\n      .then((stored) => {\n        exceptionSettings = CBDTGExceptions.normalizeSettings(stored[EXCEPTION_SETTINGS_KEY]);\n        return exceptionSettings;\n      })\n      .catch(() => {\n        exceptionSettings = CBDTGExceptions.defaultSettings();\n        return exceptionSettings;\n      });\n  }\n  return exceptionSettingsPromise;\n}\n\nfunction exceptionDecision(url, incognito) {\n  return CBDTGExceptions.evaluate(exceptionSettings, {\n    url: String(url || ""),\n    browser: runtimeBrowser,\n    incognito: Boolean(incognito),\n  });\n}\n\nasync function clearIncidentForException(tabId, reason = "exception_rule") {\n  const prior = pendingIncidents.get(tabId);\n  if (!prior) return;\n  const wasFallback = fallbackTabs.has(tabId);\n  pendingIncidents.delete(tabId);\n  fallbackTabs.delete(tabId);\n  incidentUiEpoch.set(tabId, (incidentUiEpoch.get(tabId) || 0) + 1);\n  await persistPendingIncidents();\n  if (wasFallback && prior.url) {\n    chrome.tabs.update(tabId, { url: String(prior.url), active: true }).catch(() => {});\n  } else {\n    await dismissIncidentUi(tabId);\n  }\n  sendNativeEvent("cancel_incident", {\n    incident_id: prior.incident_id,\n    tab_id: String(tabId),\n    reason,\n  });\n}\n\nasync function unregisterExcludedTab(tabId, decision) {\n  advanceClaimEpoch(tabId);\n  claimCache.delete(tabId);\n  await clearIncidentForException(tabId, decision && decision.reason ? decision.reason : "exception_rule");\n  sendNativeEvent("tab_closed", {\n    tab_id: String(tabId),\n    reason: decision && decision.reason ? decision.reason : "exception_rule",\n  });\n}\n\nfunction scheduleExceptionReconcile() {\n  if (exceptionReconcileTimer) clearTimeout(exceptionReconcileTimer);\n  exceptionReconcileTimer = setTimeout(() => {\n    exceptionReconcileTimer = null;\n    reconcileExceptionSettings().catch(() => {});\n  }, 80);\n}\n\nasync function reconcileExceptionSettings() {\n  await ensureExceptionSettings(true);\n  const tabs = await chrome.tabs.query({});\n  const claims = [];\n  for (const tab of tabs) {\n    if (!Number.isInteger(tab.id)) continue;\n    if (Boolean(tab.incognito) !== Boolean(chrome.extension.inIncognitoContext)) continue;\n    let url = tabUrl(tab);\n    if (isOwnExtensionUrl(url)) url = pendingOriginalUrl(tab.id);\n    if (isBuiltInExcludedUrl(url)) {\n      await unregisterExcludedTab(tab.id, { reason: "own_extension_page" });\n      continue;\n    }\n    if (!isTrackableUrl(url) || isTransientUrl(url)) continue;\n    const decision = exceptionDecision(url, tab.incognito);\n    if (decision.excluded) {\n      await unregisterExcludedTab(tab.id, decision);\n    } else {\n      claims.push(claimTab(tab.id, url, "exception_settings_changed", true));\n    }\n  }\n  await Promise.allSettled(claims);\n  if (nativeReady) await sendSnapshot();\n}\n\nasync function detectBrowser() {\n  const saved = await chrome.storage.local.get(["browserOverride"]);\n  const override = String(saved.browserOverride || "").toLowerCase();\n  if (BROWSERS.has(override)) return override;\n\n  try {\n    if (navigator.brave && typeof navigator.brave.isBrave === "function" && (await navigator.brave.isBrave())) {\n      return "brave";\n    }\n  } catch (_) {\n    // Continue with Chromium client hints.\n  }\n\n  const brands = Array.isArray(navigator.userAgentData && navigator.userAgentData.brands)\n    ? navigator.userAgentData.brands.map((item) => String(item.brand || ""))\n    : [];\n  if (brands.some((brand) => /brave/i.test(brand))) return "brave";\n  if (brands.some((brand) => /google chrome/i.test(brand))) return "chrome";\n  if (brands.some((brand) => /^chromium$/i.test(brand))) return "chromium";\n\n  const ua = String(navigator.userAgent || "");\n  if (/brave/i.test(ua)) return "brave";\n  if (/chromium/i.test(ua)) return "chromium";\n  if (/chrome/i.test(ua)) return "chrome";\n  return "chromium";\n}\n\nasync function ensureIdentity() {\n  if (!profileId) {\n    const stored = await chrome.storage.local.get(["profileId"]);\n    profileId = String(stored.profileId || "");\n    if (!profileId) {\n      profileId = randomId();\n      await chrome.storage.local.set({ profileId });\n    }\n  }\n  runtimeBrowser = await detectBrowser();\n  [incognitoAllowed, fileSchemeAllowed] = await Promise.all([\n    chrome.extension.isAllowedIncognitoAccess(),\n    chrome.extension.isAllowedFileSchemeAccess(),\n  ]);\n}\n\nfunction resolveReadyWaiters(error = null) {\n  while (readyWaiters.length) {\n    const waiter = readyWaiters.shift();\n    if (error) waiter.reject(error);\n    else waiter.resolve();\n  }\n}\n\nfunction scheduleReconnect() {\n  if (reconnectTimer) return;\n  reconnectTimer = setTimeout(() => {\n    reconnectTimer = null;\n    connectNative().catch(() => {});\n  }, reconnectDelayMs);\n  reconnectDelayMs = Math.min(15_000, Math.round(reconnectDelayMs * 1.8));\n}\n\nfunction rejectPendingRequests(message) {\n  for (const [requestId, pending] of pendingRequests) {\n    clearTimeout(pending.timer);\n    pending.reject(new Error(message));\n    pendingRequests.delete(requestId);\n  }\n}\n\nfunction helloPayload() {\n  return {\n    type: "hello",\n    extension_version: VERSION,\n    extension_id: chrome.runtime.id,\n    browser: runtimeBrowser,\n    profile_id: profileId,\n    session_id: sessionId,\n    incognito_context: chrome.extension.inIncognitoContext,\n    incognito_allowed: incognitoAllowed,\n    file_scheme_allowed: fileSchemeAllowed,\n  };\n}\n\nfunction clearCurrentTransport(errorMessage, rejectPending = true) {\n  lastNativeError = errorMessage || "The local tab coordinator disconnected.";\n  nativeReady = false;\n  nativePort = null;\n  nativeConnecting = false;\n  transportKind = "none";\n  if (rejectPending) rejectPendingRequests(lastNativeError);\n}\n\nasync function connectWebSocket() {\n  if (nativeReady || nativeConnecting || nativePort) return;\n  nativeConnecting = true;\n  try {\n    await ensureIdentity();\n    const socket = new WebSocket(FALLBACK_WS_URL);\n    let opened = false;\n    const adapter = {\n      kind: "websocket",\n      postMessage(value) {\n        if (socket.readyState !== WebSocket.OPEN) throw new Error("The loopback coordinator is not connected.");\n        socket.send(JSON.stringify(value));\n      },\n      disconnect() {\n        try { socket.close(1000, "extension reconnect"); } catch (_) {}\n      },\n    };\n    nativePort = adapter;\n    transportKind = "websocket";\n    lastNativeError = "";\n\n    const handshakeTimer = setTimeout(() => {\n      if (!nativeReady && nativePort === adapter) {\n        try { socket.close(); } catch (_) {}\n      }\n    }, 5_000);\n\n    socket.addEventListener("open", () => {\n      opened = true;\n      try { adapter.postMessage(helloPayload()); } catch (_) {}\n    });\n    socket.addEventListener("message", (event) => {\n      try {\n        handleNativeMessage(JSON.parse(String(event.data || "{}"))).catch(() => {});\n      } catch (_) {\n        lastNativeError = "The loopback coordinator returned invalid JSON.";\n      }\n    });\n    socket.addEventListener("error", () => {\n      lastNativeError = "The loopback coordinator could not be reached.";\n    });\n    socket.addEventListener("close", () => {\n      clearTimeout(handshakeTimer);\n      if (nativePort !== adapter) return;\n      clearCurrentTransport(\n        lastNativeError || (opened ? "The loopback coordinator disconnected." : "The loopback coordinator is not running."),\n        nativeReady,\n      );\n      scheduleReconnect();\n    });\n  } catch (error) {\n    clearCurrentTransport(error && error.message ? error.message : String(error), false);\n    scheduleReconnect();\n  } finally {\n    nativeConnecting = false;\n  }\n}\n\nasync function connectNative() {\n  if (nativeReady || nativeConnecting || nativePort) return;\n  nativeConnecting = true;\n  try {\n    await ensureIdentity();\n    const port = chrome.runtime.connectNative(NATIVE_HOST);\n    nativePort = port;\n    transportKind = "native";\n    nativeReady = false;\n    lastNativeError = "";\n\n    const handshakeTimer = setTimeout(() => {\n      if (!nativeReady && nativePort === port) {\n        try { port.disconnect(); } catch (_) {}\n      }\n    }, 5_000);\n\n    port.onMessage.addListener((message) => {\n      handleNativeMessage(message).catch(() => {});\n    });\n    port.onDisconnect.addListener(() => {\n      clearTimeout(handshakeTimer);\n      if (nativePort !== port) return;\n      const error = chrome.runtime.lastError && chrome.runtime.lastError.message;\n      const wasReady = nativeReady;\n      clearCurrentTransport(error || "The native coordinator disconnected.", wasReady);\n      // Native messaging can be unavailable in confined Snap/Flatpak browsers;\n      // the installed loopback WebSocket service is the compatibility fallback.\n      connectWebSocket().catch(() => scheduleReconnect());\n    });\n\n    port.postMessage(helloPayload());\n  } catch (error) {\n    clearCurrentTransport(error && error.message ? error.message : String(error), false);\n    connectWebSocket().catch(() => scheduleReconnect());\n  } finally {\n    nativeConnecting = false;\n  }\n}\nfunction waitForNativeReady(timeoutMs = REQUEST_TIMEOUT_MS) {\n  if (nativeReady && nativePort) return Promise.resolve();\n  connectNative().catch(() => {});\n  return new Promise((resolve, reject) => {\n    const entry = { resolve, reject };\n    readyWaiters.push(entry);\n    setTimeout(() => {\n      const index = readyWaiters.indexOf(entry);\n      if (index >= 0) readyWaiters.splice(index, 1);\n      reject(new Error(lastNativeError || "The local tab coordinator is not ready."));\n    }, timeoutMs);\n  });\n}\n\nasync function sendNativeRequest(type, payload = {}, timeoutMs = REQUEST_TIMEOUT_MS) {\n  await waitForNativeReady(timeoutMs);\n  if (!nativePort) throw new Error("The local tab coordinator is unavailable.");\n  const requestId = randomId();\n  return new Promise((resolve, reject) => {\n    const timer = setTimeout(() => {\n      pendingRequests.delete(requestId);\n      reject(new Error("The local tab coordinator did not respond in time."));\n    }, timeoutMs);\n    pendingRequests.set(requestId, { resolve, reject, timer });\n    try {\n      nativePort.postMessage({ type, request_id: requestId, ...payload });\n    } catch (error) {\n      clearTimeout(timer);\n      pendingRequests.delete(requestId);\n      reject(error);\n    }\n  });\n}\n\nfunction sendNativeEvent(type, payload = {}) {\n  if (!nativeReady || !nativePort) return;\n  try {\n    nativePort.postMessage({ type, ...payload });\n  } catch (_) {\n    // Reconnection logic will refresh the full tab snapshot.\n  }\n}\n\nasync function handleNativeMessage(message) {\n  if (!message || typeof message !== "object") return;\n  if (message.type === "hello_ack") {\n    if (BROWSERS.has(message.browser)) runtimeBrowser = message.browser;\n    if (message.transport === "native" || message.transport === "websocket") transportKind = message.transport;\n    nativeReady = true;\n    reconnectDelayMs = 500;\n    resolveReadyWaiters();\n    await restorePendingIncidents();\n    await sendSnapshot();\n    startSnapshotTimer();\n    return;\n  }\n\n  if (message.request_id && pendingRequests.has(message.request_id)) {\n    const pending = pendingRequests.get(message.request_id);\n    pendingRequests.delete(message.request_id);\n    clearTimeout(pending.timer);\n    if (message.ok === false) pending.reject(new Error(message.error || "Coordinator request failed."));\n    else pending.resolve(message);\n    return;\n  }\n\n  if (message.type === "event") {\n    await handleCoordinatorEvent(message);\n  }\n}\n\nfunction startSnapshotTimer() {\n  if (snapshotTimer) clearInterval(snapshotTimer);\n  snapshotTimer = setInterval(() => {\n    sendSnapshot().catch(() => {});\n  }, SNAPSHOT_INTERVAL_MS);\n}\n\nasync function getPendingStore() {\n  try {\n    const stored = await chrome.storage.session.get([SESSION_STORAGE_KEY]);\n    return stored[SESSION_STORAGE_KEY] && typeof stored[SESSION_STORAGE_KEY] === "object"\n      ? stored[SESSION_STORAGE_KEY]\n      : {};\n  } catch (_) {\n    return {};\n  }\n}\n\nasync function persistPendingIncidents() {\n  const value = {};\n  for (const [tabId, incident] of pendingIncidents) {\n    value[String(tabId)] = incident;\n  }\n  try {\n    await chrome.storage.session.set({ [SESSION_STORAGE_KEY]: value });\n  } catch (_) {\n    // Session persistence is a resilience enhancement, not a correctness dependency.\n  }\n}\n\nasync function restorePendingIncidents() {\n  const stored = await getPendingStore();\n  pendingIncidents.clear();\n  fallbackTabs.clear();\n  for (const [rawTabId, incident] of Object.entries(stored)) {\n    const tabId = Number(rawTabId);\n    if (!Number.isInteger(tabId) || !incident || !incident.incident_id) continue;\n    try {\n      const tab = await chrome.tabs.get(tabId);\n      pendingIncidents.set(tabId, incident);\n      if (isOwnExtensionUrl(tabUrl(tab))) fallbackTabs.add(tabId);\n      if (isBuiltInExcludedUrl(String(incident.url || ""))) {\n        await unregisterExcludedTab(tabId, { reason: "own_extension_page" });\n        continue;\n      }\n    } catch (_) {\n      // Tab no longer exists.\n    }\n  }\n  await persistPendingIncidents();\n}\n\nfunction pendingOriginalUrl(tabId) {\n  const incident = pendingIncidents.get(tabId);\n  return incident && incident.url ? String(incident.url) : "";\n}\n\nfunction tabPayload(tab, forcedUrl = "") {\n  const url = forcedUrl || tabUrl(tab);\n  return {\n    tab_id: String(tab.id),\n    window_id: Number(tab.windowId),\n    url,\n    title: sanitizeTitle(tab.title),\n    incognito: Boolean(tab.incognito),\n    active: Boolean(tab.active),\n    last_active: tab.active ? now() : 0,\n  };\n}\n\nasync function sendSnapshot() {\n  if (!nativeReady) return;\n  await ensureExceptionSettings();\n  const tabs = await chrome.tabs.query({});\n  const snapshot = [];\n  for (const tab of tabs) {\n    if (Boolean(tab.incognito) !== Boolean(chrome.extension.inIncognitoContext)) continue;\n    let url = tabUrl(tab);\n    if (isOwnExtensionUrl(url)) url = pendingOriginalUrl(tab.id);\n    if (!isTrackableUrl(url) || isTransientUrl(url)) continue;\n    if (exceptionDecision(url, tab.incognito).excluded) continue;\n    snapshot.push(tabPayload(tab, url));\n  }\n  await sendNativeRequest("snapshot", { tabs: snapshot }, REQUEST_TIMEOUT_MS);\n}\n\nasync function sendMessageToTab(tabId, message, attempts = 1) {\n  let lastError = null;\n  for (let attempt = 0; attempt < attempts; attempt += 1) {\n    try {\n      return await chrome.tabs.sendMessage(tabId, message);\n    } catch (error) {\n      lastError = error;\n      if (attempt + 1 < attempts) await new Promise((resolve) => setTimeout(resolve, 120));\n    }\n  }\n  throw lastError || new Error("No content-script receiver is available.");\n}\n\nasync function showDuplicateDialog(tabId, incident, expectedClaimEpoch = null) {\n  if (expectedClaimEpoch !== null) {\n    const current = await currentTabForClaim(tabId, String(incident.url || ""), expectedClaimEpoch);\n    if (!current) return;\n  }\n  const epoch = (incidentUiEpoch.get(tabId) || 0) + 1;\n  incidentUiEpoch.set(tabId, epoch);\n  pendingIncidents.set(tabId, incident);\n  await persistPendingIncidents();\n  if (expectedClaimEpoch !== null && !(await currentTabForClaim(tabId, String(incident.url || ""), expectedClaimEpoch))) {\n    const current = pendingIncidents.get(tabId);\n    if (current && current.incident_id === incident.incident_id) {\n      pendingIncidents.delete(tabId);\n      incidentUiEpoch.set(tabId, epoch + 1);\n      await persistPendingIncidents();\n    }\n    return;\n  }\n  try {\n    await sendMessageToTab(tabId, { type: "show_duplicate_dialog", payload: incident }, 3);\n    if (incidentUiEpoch.get(tabId) !== epoch) {\n      const current = pendingIncidents.get(tabId);\n      if (current) {\n        sendMessageToTab(tabId, { type: "show_duplicate_dialog", payload: current }, 1).catch(() => {});\n      } else {\n        sendMessageToTab(tabId, { type: "dismiss_duplicate_dialog" }, 1).catch(() => {});\n      }\n      return;\n    }\n    fallbackTabs.delete(tabId);\n  } catch (_) {\n    const current = pendingIncidents.get(tabId);\n    if (incidentUiEpoch.get(tabId) !== epoch || !current || current.incident_id !== incident.incident_id) {\n      return;\n    }\n    const tab = await matchingTab(tabId, incident.url);\n    if (!tab) {\n      pendingIncidents.delete(tabId);\n      fallbackTabs.delete(tabId);\n      incidentUiEpoch.set(tabId, epoch + 1);\n      await persistPendingIncidents();\n      return;\n    }\n    fallbackTabs.add(tabId);\n    const chooserUrl = chrome.runtime.getURL(`chooser.html#${encodeURIComponent(incident.incident_id)}`);\n    await chrome.tabs.update(tabId, { url: chooserUrl, active: true });\n  }\n}\n\nasync function updateIncidentUi(tabId, message, kind = "info", busy = false) {\n  if (fallbackTabs.has(tabId)) {\n    chrome.runtime.sendMessage({\n      type: "chooser_event",\n      tab_id: tabId,\n      message,\n      kind,\n      busy,\n    }).catch(() => {});\n    return;\n  }\n  sendMessageToTab(tabId, {\n    type: "duplicate_status",\n    message,\n    kind,\n    busy,\n  }).catch(() => {});\n}\n\nasync function dismissIncidentUi(tabId) {\n  if (!fallbackTabs.has(tabId)) {\n    sendMessageToTab(tabId, { type: "dismiss_duplicate_dialog" }).catch(() => {});\n  }\n}\n\nasync function clearIncidentForNavigation(tabId, nextUrl) {\n  const prior = pendingIncidents.get(tabId);\n  if (!prior || prior.url === nextUrl) return;\n  pendingIncidents.delete(tabId);\n  fallbackTabs.delete(tabId);\n  incidentUiEpoch.set(tabId, (incidentUiEpoch.get(tabId) || 0) + 1);\n  await persistPendingIncidents();\n  await dismissIncidentUi(tabId);\n  sendNativeEvent("cancel_incident", { incident_id: prior.incident_id, tab_id: String(tabId) });\n}\n\nasync function clearResolvedIncident(tabId) {\n  if (!pendingIncidents.has(tabId)) return;\n  pendingIncidents.delete(tabId);\n  fallbackTabs.delete(tabId);\n  incidentUiEpoch.set(tabId, (incidentUiEpoch.get(tabId) || 0) + 1);\n  await persistPendingIncidents();\n  await dismissIncidentUi(tabId);\n}\n\nasync function claimTab(tabId, url, source, force = false) {\n  if (!Number.isInteger(tabId)) return;\n  const normalizedUrl = String(url || "");\n\n  // The extension-owned chooser is a surrogate UI for protected pages that\n  // reject content-script injection. Its navigation must never cancel the\n  // original duplicate incident or unregister the protected URL.\n  if (isOwnExtensionUrl(normalizedUrl)) {\n    advanceClaimEpoch(tabId);\n    claimCache.delete(tabId);\n    return;\n  }\n\n  if (isOwnExtensionManagementUrl(normalizedUrl)) {\n    await unregisterExcludedTab(tabId, { reason: "own_extension_page" });\n    return;\n  }\n\n  // New-tab and blank documents are browser transition surfaces rather than\n  // meaningful destinations. Never register them: doing so creates false\n  // duplicates while a real URL is still committing.\n  if (!isTrackableUrl(normalizedUrl) || isTransientUrl(normalizedUrl)) {\n    advanceClaimEpoch(tabId);\n    claimCache.delete(tabId);\n    await clearIncidentForNavigation(tabId, normalizedUrl);\n    sendNativeEvent("tab_closed", { tab_id: String(tabId) });\n    return;\n  }\n\n  const cached = claimCache.get(tabId);\n  if (!force && cached && cached.url === normalizedUrl && now() - cached.at < 1200) return;\n\n  const epoch = advanceClaimEpoch(tabId);\n  claimCache.set(tabId, { url: normalizedUrl, at: now() });\n  const tab = await currentTabForClaim(tabId, normalizedUrl, epoch);\n  if (!tab) return;\n\n  await ensureExceptionSettings();\n  const decision = exceptionDecision(normalizedUrl, tab.incognito);\n  if (decision.excluded) {\n    await unregisterExcludedTab(tabId, decision);\n    return;\n  }\n\n  await clearIncidentForNavigation(tabId, normalizedUrl);\n  if (!(await currentTabForClaim(tabId, normalizedUrl, epoch))) return;\n\n  try {\n    const response = await sendNativeRequest("claim", {\n      tab: tabPayload(tab, normalizedUrl),\n      source: String(source || "navigation"),\n    });\n\n    // Network/native-messaging responses can arrive after a navigation. A\n    // stale response must never replace the alert for the tab\'s newer URL.\n    if (!(await currentTabForClaim(tabId, normalizedUrl, epoch))) return;\n\n    if (response.result === "duplicate" && response.incident) {\n      if (String(response.incident.url || "") !== normalizedUrl) return;\n      await showDuplicateDialog(tabId, response.incident, epoch);\n    } else if (response.result === "transfer_completed") {\n      await clearResolvedIncident(tabId);\n    } else if (response.result === "unique") {\n      await clearResolvedIncident(tabId);\n    }\n  } catch (error) {\n    if (claimEpoch.get(tabId) === epoch) {\n      lastNativeError = error && error.message ? error.message : String(error);\n    }\n  }\n}\n\nasync function resolveDuplicate(tabId, incidentId, choice) {\n  const incident = pendingIncidents.get(tabId);\n  if (!incident || incident.incident_id !== incidentId) {\n    throw new Error("This duplicate alert is no longer active.");\n  }\n  if (choice !== "current" && !BROWSERS.has(choice)) {\n    throw new Error("Choose Current browser, Brave, Chrome, or Chromium.");\n  }\n  await updateIncidentUi(tabId, "Applying your selection…", "info", true);\n  const response = await sendNativeRequest("resolve", {\n    incident_id: incidentId,\n    requester_tab_id: String(tabId),\n    choice,\n  }, TRANSFER_REQUEST_TIMEOUT_MS);\n  if (response.pending) {\n    await updateIncidentUi(tabId, response.message || `Opening ${browserLabel(choice)}…`, "info", true);\n  }\n  return response;\n}\n\nasync function createRequestedTab(message) {\n  const url = String(message.url || "");\n  const wantsIncognito = Boolean(message.incognito);\n  if (!isTrackableUrl(url)) throw new Error("The coordinator supplied an invalid URL.");\n\n  let createdTab = null;\n  const windows = await chrome.windows.getAll({ populate: false, windowTypes: ["normal"] });\n  const matching = windows.find((win) => Boolean(win.incognito) === wantsIncognito);\n  if (matching) {\n    createdTab = await chrome.tabs.create({ windowId: matching.id, url, active: true });\n    await chrome.windows.update(matching.id, { focused: true });\n  } else {\n    const createdWindow = await chrome.windows.create({ url, incognito: wantsIncognito, focused: true, type: "normal" });\n    if (createdWindow && Array.isArray(createdWindow.tabs) && createdWindow.tabs.length) {\n      createdTab = createdWindow.tabs[0];\n    }\n  }\n  // The transfer is finalized by the destination tab\'s exact-URL claim, not\n  // by this acknowledgement. Some headless or policy-managed Chromium builds\n  // omit the Window/Tabs object even after successfully creating the window,\n  // so an empty tab_id is valid and must never crash the worker.\n  sendNativeEvent("created_tab_ack", {\n    transfer_id: message.transfer_id,\n    tab_id: createdTab && Number.isInteger(createdTab.id) ? String(createdTab.id) : "",\n  });\n}\n\nasync function matchingTab(tabId, expectedUrl = "") {\n  const numericId = Number(tabId);\n  if (!Number.isInteger(numericId)) return null;\n  try {\n    const tab = await chrome.tabs.get(numericId);\n    let currentUrl = tabUrl(tab);\n    if (isOwnExtensionUrl(currentUrl)) currentUrl = pendingOriginalUrl(numericId);\n    if (expectedUrl && currentUrl !== expectedUrl) return null;\n    return tab;\n  } catch (_) {\n    return null;\n  }\n}\n\nasync function activateTab(tabId, expectedUrl = "") {\n  const numericId = Number(tabId);\n  const tab = await matchingTab(numericId, expectedUrl);\n  if (!tab) return false;\n  try {\n    await chrome.tabs.update(numericId, { active: true });\n    await chrome.windows.update(tab.windowId, { focused: true });\n    return true;\n  } catch (_) {\n    // The tab may already have been closed by the user.\n    return false;\n  }\n}\n\nasync function handleCoordinatorEvent(message) {\n  const tabId = Number(message.tab_id);\n  switch (message.action) {\n    case "close_tab": {\n      if (!Number.isInteger(tabId)) return;\n      const expectedUrl = String(message.expected_url || "");\n      const tab = await matchingTab(tabId, expectedUrl);\n      if (!tab) {\n        // A user navigation always wins over a stale deduplication decision.\n        pendingIncidents.delete(tabId);\n        fallbackTabs.delete(tabId);\n        incidentUiEpoch.set(tabId, (incidentUiEpoch.get(tabId) || 0) + 1);\n        await persistPendingIncidents();\n        sendNativeEvent("tab_close_skipped", {\n          tab_id: String(tabId),\n          incident_id: message.incident_id || "",\n          reason: "tab_missing_or_url_changed",\n        });\n        return;\n      }\n      await ensureExceptionSettings();\n      const closeUrl = expectedUrl || tabUrl(tab);\n      const closeDecision = exceptionDecision(closeUrl, tab.incognito);\n      if (closeDecision.excluded) {\n        await unregisterExcludedTab(tabId, closeDecision);\n        sendNativeEvent("tab_close_skipped", {\n          tab_id: String(tabId),\n          incident_id: message.incident_id || "",\n          reason: closeDecision.reason || "exception_rule",\n        });\n        return;\n      }\n      pendingIncidents.delete(tabId);\n      fallbackTabs.delete(tabId);\n      incidentUiEpoch.set(tabId, (incidentUiEpoch.get(tabId) || 0) + 1);\n      await persistPendingIncidents();\n      try {\n        await chrome.tabs.remove(tabId);\n        sendNativeEvent("tab_closed_ack", { tab_id: String(tabId), incident_id: message.incident_id || "" });\n      } catch (error) {\n        sendNativeEvent("tab_close_failed", {\n          tab_id: String(tabId),\n          incident_id: message.incident_id || "",\n          error: error && error.message ? error.message : String(error),\n        });\n      }\n      break;\n    }\n    case "activate_tab":\n      await activateTab(tabId, String(message.expected_url || ""));\n      break;\n    case "keep_current": {\n      if (!Number.isInteger(tabId)) return;\n      const incident = pendingIncidents.get(tabId);\n      const originalUrl = String(message.url || (incident && incident.url) || "");\n      if (fallbackTabs.has(tabId)) {\n        pendingIncidents.delete(tabId);\n        fallbackTabs.delete(tabId);\n        incidentUiEpoch.set(tabId, (incidentUiEpoch.get(tabId) || 0) + 1);\n        await persistPendingIncidents();\n        if (originalUrl) await chrome.tabs.update(tabId, { url: originalUrl, active: true });\n      } else {\n        await dismissIncidentUi(tabId);\n        pendingIncidents.delete(tabId);\n        incidentUiEpoch.set(tabId, (incidentUiEpoch.get(tabId) || 0) + 1);\n        await persistPendingIncidents();\n        await activateTab(tabId, originalUrl);\n      }\n      break;\n    }\n    case "resolution_error":\n      if (Number.isInteger(tabId)) await updateIncidentUi(tabId, message.error || "The selected browser could not be opened.", "error", false);\n      break;\n    case "create_tab":\n      try {\n        await createRequestedTab(message);\n      } catch (error) {\n        sendNativeEvent("create_tab_failed", {\n          transfer_id: message.transfer_id || "",\n          error: error && error.message ? error.message : String(error),\n        });\n      }\n      break;\n    case "request_snapshot":\n      await sendSnapshot();\n      break;\n    default:\n      break;\n  }\n}\n\n// CBDTG_NAVIGATION_COMPATIBILITY_V1\nfunction registerWebNavigationEvent(eventName, source) {\n  const navigationApi = globalThis.chrome && globalThis.chrome.webNavigation;\n  const event = navigationApi && navigationApi[eventName];\n  if (!event || typeof event.addListener !== "function") return false;\n\n  event.addListener((details) => {\n    if (!details || details.frameId !== 0 || !Number.isInteger(details.tabId)) return;\n    const url = typeof details.url === "string" ? details.url : "";\n    if (!url) return;\n    claimTab(details.tabId, url, source).catch(() => {});\n  });\n  return true;\n}\n\nregisterWebNavigationEvent("onCommitted", "committed");\nregisterWebNavigationEvent("onHistoryStateUpdated", "history_state");\nregisterWebNavigationEvent("onReferenceFragmentUpdated", "fragment");\n\nchrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {\n  // changeInfo.url is the compatibility path when a Chromium-family build does\n  // not expose chrome.webNavigation. A Content-Disposition download response that never commits as\n  // a page does not become a changed tab URL, so download handling is preserved.\n  const changedUrl = changeInfo && typeof changeInfo.url === "string" ? changeInfo.url : "";\n  if (changedUrl) {\n    claimTab(tabId, changedUrl, "tab_url_changed").catch(() => {});\n    return;\n  }\n\n  // The completed-document pass reconciles restored, discarded, and replaced\n  // tabs without intercepting a navigation before it becomes a real page.\n  if (changeInfo && changeInfo.status === "complete") {\n    const url = tabUrl(tab);\n    if (url) claimTab(tabId, url, "complete").catch(() => {});\n  }\n});\n\nchrome.tabs.onRemoved.addListener((tabId) => {\n  claimCache.delete(tabId);\n  claimEpoch.delete(tabId);\n  pendingIncidents.delete(tabId);\n  fallbackTabs.delete(tabId);\n  incidentUiEpoch.delete(tabId);\n  persistPendingIncidents().catch(() => {});\n  sendNativeEvent("tab_closed", { tab_id: String(tabId) });\n});\n\nchrome.tabs.onReplaced.addListener((addedTabId, removedTabId) => {\n  claimCache.delete(removedTabId);\n  claimEpoch.delete(removedTabId);\n  pendingIncidents.delete(removedTabId);\n  fallbackTabs.delete(removedTabId);\n  incidentUiEpoch.delete(removedTabId);\n  persistPendingIncidents().catch(() => {});\n  sendNativeEvent("tab_closed", { tab_id: String(removedTabId) });\n  chrome.tabs.get(addedTabId).then((tab) => {\n    const url = tabUrl(tab);\n    if (isTrackableUrl(url)) claimTab(addedTabId, url, "replaced", true).catch(() => {});\n  }).catch(() => {});\n});\n\nchrome.tabs.onActivated.addListener(async ({ tabId }) => {\n  try {\n    const tab = await chrome.tabs.get(tabId);\n    sendNativeEvent("touch_tab", {\n      tab_id: String(tabId),\n      window_id: Number(tab.windowId),\n      active: true,\n      at: now(),\n    });\n  } catch (_) {\n    // Ignore activation races.\n  }\n});\n\nchrome.windows.onFocusChanged.addListener(async (windowId) => {\n  if (windowId === chrome.windows.WINDOW_ID_NONE) return;\n  try {\n    const tabs = await chrome.tabs.query({ windowId, active: true });\n    if (tabs.length) {\n      sendNativeEvent("touch_tab", {\n        tab_id: String(tabs[0].id),\n        window_id: Number(windowId),\n        active: true,\n        at: now(),\n      });\n    }\n  } catch (_) {\n    // Ignore window focus races.\n  }\n});\n\nchrome.runtime.onMessage.addListener((message, sender, sendResponse) => {\n  if (!message || typeof message !== "object") return false;\n\n  if (message.type === "resolve_duplicate") {\n    const tabId = sender.tab && sender.tab.id;\n    resolveDuplicate(tabId, String(message.incident_id || ""), String(message.choice || ""))\n      .then((response) => sendResponse({ ok: true, pending: Boolean(response.pending), message: response.message || "" }))\n      .catch((error) => sendResponse({ ok: false, error: error && error.message ? error.message : String(error) }));\n    return true;\n  }\n\n  if (message.type === "chooser_get") {\n    const tabId = sender.tab && sender.tab.id;\n    const incident = pendingIncidents.get(tabId);\n    sendResponse({ ok: Boolean(incident), tab_id: tabId, incident: incident || null });\n    return false;\n  }\n\n  if (message.type === "chooser_resolve") {\n    const tabId = sender.tab && sender.tab.id;\n    resolveDuplicate(tabId, String(message.incident_id || ""), String(message.choice || ""))\n      .then((response) => sendResponse({ ok: true, pending: Boolean(response.pending), message: response.message || "" }))\n      .catch((error) => sendResponse({ ok: false, error: error && error.message ? error.message : String(error) }));\n    return true;\n  }\n\n  if (message.type === "get_status") {\n    Promise.all([\n      chrome.extension.isAllowedIncognitoAccess(),\n      chrome.extension.isAllowedFileSchemeAccess(),\n      chrome.storage.local.get(["browserOverride", EXCEPTION_SETTINGS_KEY]),\n    ]).then(([incAllowed, fileAllowed, settings]) => {\n      const exceptionState = CBDTGExceptions.normalizeSettings(settings[EXCEPTION_SETTINGS_KEY]);\n      sendResponse({\n        ok: true,\n        version: VERSION,\n        extension_id: chrome.runtime.id,\n        browser: runtimeBrowser,\n        browser_override: settings.browserOverride || "auto",\n        native_connected: nativeReady,\n        native_error: lastNativeError,\n        transport_kind: transportKind,\n        incognito_context: chrome.extension.inIncognitoContext,\n        incognito_allowed: incAllowed,\n        file_scheme_allowed: fileAllowed,\n        pending_incidents: pendingIncidents.size,\n        guard_enabled: exceptionState.enabled,\n        guard_paused_until: exceptionState.pausedUntil,\n        exception_count: exceptionState.rules.filter((rule) => rule.enabled).length,\n        exception_total: exceptionState.rules.length,\n      });\n    }).catch((error) => sendResponse({ ok: false, error: error.message }));\n    return true;\n  }\n\n  if (message.type === "get_active_tab_context") {\n    chrome.tabs.query({ active: true, lastFocusedWindow: true }).then((tabs) => {\n      const tab = tabs[0] || null;\n      let url = tab ? tabUrl(tab) : "";\n      if (tab && isOwnExtensionUrl(url)) url = pendingOriginalUrl(tab.id);\n      sendResponse({\n        ok: true,\n        url,\n        incognito: Boolean(tab && tab.incognito),\n        browser: runtimeBrowser,\n        trackable: isTrackableUrl(url) && !isTransientUrl(url),\n      });\n    }).catch((error) => sendResponse({ ok: false, error: error.message }));\n    return true;\n  }\n\n  if (message.type === "set_browser_override") {\n    const value = String(message.value || "auto").toLowerCase();\n    const stored = BROWSERS.has(value) ? value : "";\n    chrome.storage.local.set({ browserOverride: stored }).then(() => {\n      if (nativePort) nativePort.disconnect();\n      runtimeBrowser = stored || runtimeBrowser;\n      sessionId = randomId();\n      connectNative().catch(() => {});\n      sendResponse({ ok: true });\n    }).catch((error) => sendResponse({ ok: false, error: error.message }));\n    return true;\n  }\n\n  if (message.type === "open_extension_details") {\n    const scheme = runtimeBrowser === "brave" ? "brave" : "chrome";\n    chrome.tabs.create({ url: `${scheme}://extensions/?id=${chrome.runtime.id}` });\n    sendResponse({ ok: true });\n    return false;\n  }\n\n  if (message.type === "rescan_tabs") {\n    reconcileExceptionSettings().then(() => sendResponse({ ok: true })).catch((error) => sendResponse({ ok: false, error: error.message }));\n    return true;\n  }\n\n  return false;\n});\n\nchrome.storage.onChanged.addListener((changes, areaName) => {\n  if (areaName !== "local" || !changes[EXCEPTION_SETTINGS_KEY]) return;\n  exceptionSettings = CBDTGExceptions.normalizeSettings(changes[EXCEPTION_SETTINGS_KEY].newValue);\n  exceptionSettingsPromise = Promise.resolve(exceptionSettings);\n  scheduleExceptionReconcile();\n});\n\nchrome.alarms.create("coordinator-health", { periodInMinutes: 1 });\nchrome.alarms.onAlarm.addListener((alarm) => {\n  if (alarm && alarm.name === "coordinator-health" && !nativeReady) connectNative().catch(() => {});\n});\n\nchrome.runtime.onInstalled.addListener(() => {\n  connectNative().catch(() => {});\n});\n\nchrome.runtime.onStartup.addListener(() => {\n  connectNative().catch(() => {});\n});\n\nensureExceptionSettings().finally(() => connectNative().catch(() => {}));\n'}

class GuardError(Exception):
    """Expected operational error."""


def supported_platform() -> str:
    value = platform.system().lower()
    if value not in {"darwin", "linux"}:
        raise GuardError("This installer supports macOS and Linux only.")
    return value


def home_dir() -> Path:
    return Path.home().expanduser().resolve()


def app_dir() -> Path:
    if supported_platform() == "darwin":
        return home_dir() / "Library" / "Application Support" / "CrossBrowserDuplicateTabGuard"
    root = Path(os.environ.get("XDG_DATA_HOME", home_dir() / ".local" / "share"))
    return root / APP_SLUG


def installed_script_path() -> Path:
    return app_dir() / "cross_browser_duplicate_tab_guard.py"


def extension_dir() -> Path:
    return app_dir() / "extension"


def config_path() -> Path:
    return app_dir() / "config.json"


def log_path() -> Path:
    if supported_platform() == "darwin":
        return home_dir() / "Library" / "Logs" / "CrossBrowserDuplicateTabGuard.log"
    root = Path(os.environ.get("XDG_STATE_HOME", home_dir() / ".local" / "state"))
    return root / APP_SLUG / "coordinator.log"


def runtime_dir() -> Path:
    if supported_platform() == "darwin":
        root = home_dir() / "Library" / "Application Support" / "CrossBrowserDuplicateTabGuard" / "run"
        try:
            ensure_private_dir(root)
            return root
        except OSError:
            fallback = Path(f"/tmp/cbdtg-{os.getuid()}")
            ensure_private_dir(fallback)
            return fallback
    if supported_platform() == "linux" and os.environ.get("XDG_RUNTIME_DIR"):
        root = Path(os.environ["XDG_RUNTIME_DIR"])
        try:
            if root.exists() and root.stat().st_uid == os.getuid():
                target = root / "cbdtg"
                ensure_private_dir(target)
                return target
        except OSError:
            pass
    fallback = Path(f"/tmp/cbdtg-{os.getuid()}")
    ensure_private_dir(fallback)
    return fallback


def socket_path() -> Path:
    return runtime_dir() / "coordinator.sock"


def daemon_lock_path() -> Path:
    return runtime_dir() / "daemon.lock"


def daemon_pid_path() -> Path:
    return runtime_dir() / "daemon.pid"


def startup_lock_path() -> Path:
    return runtime_dir() / "startup.lock"


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def atomic_write(path: Path, data: Union[str, bytes], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    temp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    with open(temp, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp, mode)
    os.replace(temp, path)


def log_runtime(message: str) -> None:
    """Log operational state without recording URLs or page titles."""
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}\n")
    except OSError:
        pass


def extension_id_from_key(public_key_b64: str) -> str:
    digest = hashlib.sha256(base64.b64decode(public_key_b64)).digest()[:16]
    return "".join(chr(ord("a") + (byte >> 4)) + chr(ord("a") + (byte & 15)) for byte in digest)


def choose_ws_port(preferred: int = DEFAULT_WS_PORT) -> int:
    for port in [preferred, *range(49474, 49540)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise GuardError("No free loopback port was available for the coordinator.")


def load_config(create: bool = False) -> dict[str, Any]:
    path = config_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            token = str(data.get("token", ""))
            port = int(data.get("ws_port", DEFAULT_WS_PORT))
            if len(token) >= 32 and 1024 <= port <= 65535:
                return {"token": token, "ws_port": port}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    if not create:
        raise GuardError("The coordinator is not installed or its configuration is invalid.")
    data = {"token": secrets.token_urlsafe(36), "ws_port": choose_ws_port()}
    atomic_write(path, json.dumps(data, indent=2) + "\n", 0o600)
    return data


def render_extension_files(config: dict[str, Any]) -> dict[str, str]:
    rendered: dict[str, str] = {}
    local_ext = Path(__file__).resolve().parent / "extension"
    if local_ext.is_dir():
        for root, dirs, files in os.walk(local_ext):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for file in files:
                if file.startswith(".") or file.endswith(("~", ".bak", ".tmp", ".png", ".jpg", ".jpeg", ".ico")):
                    continue
                p = Path(root) / file
                rel = str(p.relative_to(local_ext))
                try:
                    text = p.read_text(encoding="utf-8")
                    rendered[rel] = (
                        text.replace("__AUTH_TOKEN__", str(config["token"]))
                        .replace("__WS_PORT__", str(config["ws_port"]))
                    )
                except Exception:
                    pass
        if rendered:
            return rendered
    for name, content in EXTENSION_FILES.items():
        rendered[name] = (
            content.replace("__AUTH_TOKEN__", str(config["token"]))
            .replace("__WS_PORT__", str(config["ws_port"]))
        )
    return rendered


def native_manifest_dirs() -> dict[str, list[Path]]:
    home = home_dir()
    if supported_platform() == "darwin":
        return {
            "chrome": [home / "Library/Application Support/Google/Chrome/NativeMessagingHosts"],
            "chromium": [home / "Library/Application Support/Chromium/NativeMessagingHosts"],
            "brave": [home / "Library/Application Support/BraveSoftware/Brave-Browser/NativeMessagingHosts"],
        }

    config_home = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return {
        "chrome": [config_home / "google-chrome/NativeMessagingHosts"],
        "chromium": [
            config_home / "chromium/NativeMessagingHosts",
            home / "snap/chromium/common/chromium/NativeMessagingHosts",
            home / ".var/app/org.chromium.Chromium/config/chromium/NativeMessagingHosts",
        ],
        "brave": [
            config_home / "BraveSoftware/Brave-Browser/NativeMessagingHosts",
            home / "snap/brave/common/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts",
            home / ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser/NativeMessagingHosts",
        ],
    }


def native_manifest_payload() -> str:
    payload = {
        "name": HOST_NAME,
        "description": "Local in-memory coordinator for Cross-Browser Duplicate Tab Guard",
        "path": str(installed_script_path()),
        "type": "stdio",
        "allowed_origins": [EXTENSION_ORIGIN],
    }
    return json.dumps(payload, indent=2) + "\n"


def install_native_manifests() -> list[Path]:
    written: list[Path] = []
    payload = native_manifest_payload()
    for directories in native_manifest_dirs().values():
        for directory in directories:
            try:
                path = directory / f"{HOST_NAME}.json"
                atomic_write(path, payload, 0o644)
                written.append(path)
            except OSError:
                # Alternative sandbox-specific paths are best effort. Standard
                # paths are also installed and WebSocket is the fallback.
                continue
    return written


def external_extension_dirs() -> dict[str, list[Path]]:
    home = home_dir()
    if supported_platform() == "darwin":
        return {
            "chrome": [home / "Library/Application Support/Google/Chrome/External Extensions"],
            "chromium": [home / "Library/Application Support/Chromium/External Extensions"],
            "brave": [home / "Library/Application Support/BraveSoftware/Brave-Browser/External Extensions"],
        }

    config_home = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return {
        "chrome": [config_home / "google-chrome/External Extensions"],
        "chromium": [
            config_home / "chromium/External Extensions",
            home / ".var/app/org.chromium.Chromium/config/chromium/External Extensions",
        ],
        "brave": [
            config_home / "BraveSoftware/Brave-Browser/External Extensions",
            home / ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser/External Extensions",
        ],
    }


def install_external_extensions() -> list[Path]:
    written: list[Path] = []
    payload = json.dumps({"external_directory": str(extension_dir())}, indent=2) + "\n"
    for directories in external_extension_dirs().values():
        for directory in directories:
            try:
                path = directory / f"{EXTENSION_ID}.json"
                atomic_write(path, payload, 0o644)
                written.append(path)
            except OSError:
                continue
    return written


def launch_agent_path() -> Path:
    return home_dir() / "Library" / "LaunchAgents" / "systems.venturi.duplicate-tab-guard.plist"


def systemd_unit_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", home_dir() / ".config")) / "systemd/user/cross-browser-duplicate-tab-guard.service"


def xdg_autostart_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", home_dir() / ".config")) / "autostart/cross-browser-duplicate-tab-guard.desktop"


def xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def desktop_exec_quote(value: str) -> str:
    # Desktop Entry Exec values are not shell commands. Double-quote and
    # escape the characters defined specially by the freedesktop grammar.
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


def install_autostart() -> str:
    script = installed_script_path()
    if supported_platform() == "darwin":
        plist = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>systems.venturi.duplicate-tab-guard</string>
  <key>ProgramArguments</key>
  <array>
    <string>{xml_escape(str(script))}</string>
    <string>daemon</string>
    <string>--persistent</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>{xml_escape(str(log_path()))}</string>
  <key>StandardErrorPath</key><string>{xml_escape(str(log_path()))}</string>
</dict>
</plist>
'''
        path = launch_agent_path()
        atomic_write(path, plist, 0o644)
        uid = str(os.getuid())
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        completed = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if completed.returncode != 0:
            subprocess.run(["launchctl", "load", "-w", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "launchd"

    unit = f'''[Unit]
Description=Cross-Browser Duplicate Tab Guard coordinator
After=graphical-session.target

[Service]
Type=simple
ExecStart={shlex.quote(str(script))} daemon --persistent
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
'''
    atomic_write(systemd_unit_path(), unit, 0o644)

    # Prefer a user systemd service. Install the XDG autostart entry only when
    # systemd-user is unavailable, preventing two persistent coordinators from
    # being launched at login.
    stop_running_daemon()
    systemctl = shutil.which("systemctl")
    if systemctl:
        reload_result = subprocess.run([systemctl, "--user", "daemon-reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        enable_result = subprocess.run([systemctl, "--user", "enable", systemd_unit_path().name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        restart_result = subprocess.run([systemctl, "--user", "restart", systemd_unit_path().name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if reload_result.returncode == 0 and enable_result.returncode == 0 and restart_result.returncode == 0:
            xdg_autostart_path().unlink(missing_ok=True)
            return "systemd-user"
        # A partially successful enable must not coexist with the XDG fallback
        # at the next login, or two coordinators would race for the same socket.
        subprocess.run(
            [systemctl, "--user", "disable", "--now", systemd_unit_path().name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    desktop = f'''[Desktop Entry]
Type=Application
Name=Cross-Browser Duplicate Tab Guard
Exec={desktop_exec_quote(str(script))} daemon --persistent
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
'''
    atomic_write(xdg_autostart_path(), desktop, 0o644)
    stop_running_daemon()
    start_daemon_process(persistent=True)
    return "xdg-autostart"


def stop_autostart() -> None:
    if supported_platform() == "darwin":
        path = launch_agent_path()
        uid = str(os.getuid())
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["launchctl", "unload", "-w", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        path.unlink(missing_ok=True)
        return
    systemctl = shutil.which("systemctl")
    if systemctl:
        subprocess.run([systemctl, "--user", "disable", "--now", systemd_unit_path().name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([systemctl, "--user", "daemon-reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    systemd_unit_path().unlink(missing_ok=True)
    xdg_autostart_path().unlink(missing_ok=True)


class BrowserLauncher:
    MAC_APPS = {"brave": "Brave Browser", "chrome": "Google Chrome", "chromium": "Chromium"}
    LINUX_COMMANDS: dict[str, tuple[tuple[str, ...], ...]] = {
        "brave": (
            ("brave-browser",), ("brave-browser-stable",), ("brave",),
            ("/snap/bin/brave",), ("flatpak", "run", "com.brave.Browser"),
        ),
        "chrome": (("google-chrome",), ("google-chrome-stable",), ("chrome",)),
        "chromium": (
            ("chromium",), ("chromium-browser",), ("/snap/bin/chromium",),
            ("flatpak", "run", "org.chromium.Chromium"),
        ),
    }

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run

    @staticmethod
    def _openable(url: str) -> bool:
        try:
            return urlsplit(url).scheme in {"http", "https", "file"}
        except ValueError:
            return False

    @staticmethod
    def resolve_linux_prefix(prefix: tuple[str, ...]) -> Optional[list[str]]:
        first = prefix[0]
        if os.path.isabs(first):
            path = Path(first)
            if not path.is_file() or not os.access(path, os.X_OK):
                return None
            executable = str(path)
        else:
            executable = shutil.which(first)
            if not executable:
                return None

        # A Flatpak executable alone does not establish that the requested
        # browser application is installed. Avoid false-positive diagnostics
        # and transfer attempts that immediately terminate with "not found".
        if first == "flatpak" and len(prefix) >= 3 and prefix[1] == "run":
            try:
                check = subprocess.run(
                    [executable, "info", prefix[2]],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired):
                return None
            if check.returncode != 0:
                return None

        return [executable, *prefix[1:]]

    def command(self, browser: str, url: str, incognito: bool) -> list[str]:
        if browser not in SUPPORTED_BROWSERS:
            raise GuardError(f"Unsupported browser: {browser}")
        if not self._openable(url):
            raise GuardError("That URL scheme cannot be launched in a different browser.")
        if supported_platform() == "darwin":
            app = self.MAC_APPS[browser]
            open_bin = shutil.which("open") or "/usr/bin/open"
            if not self.dry_run:
                check = subprocess.run([open_bin, "-Ra", app], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if check.returncode != 0:
                    raise GuardError(f"{app} is not installed.")
            if incognito:
                # -n is necessary when the app already has a regular process;
                # otherwise macOS may ignore the new --incognito arguments.
                return [open_bin, "-n", "-a", app, "--args", "--incognito", "--new-window", url]
            return [open_bin, "-a", app, url]

        for prefix in self.LINUX_COMMANDS[browser]:
            command = self.resolve_linux_prefix(prefix)
            if not command:
                continue
            if incognito:
                command.extend(["--incognito", "--new-window", url])
            else:
                command.extend(["--new-tab", url])
            return command
        if self.dry_run:
            fallback = self.LINUX_COMMANDS[browser][0][0]
            if incognito:
                return [fallback, "--incognito", "--new-window", url]
            return [fallback, "--new-tab", url]
        raise GuardError(f"No executable was found for {browser}.")

    def open(self, browser: str, url: str, incognito: bool) -> tuple[bool, str]:
        try:
            command = self.command(browser, url, incognito)
            if self.dry_run:
                return True, "dry-run"
            if supported_platform() == "darwin":
                completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=10)
                if completed.returncode != 0:
                    return False, completed.stderr.strip() or f"Could not open {browser}."
            else:
                subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
            return True, "launched"
        except (GuardError, OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)


@dataclass
class ClientSession:
    id: str
    send_callback: Callable[[dict[str, Any]], bool]
    transport: str
    browser: str
    profile_id: str
    incognito: bool
    incognito_allowed: bool
    file_scheme_allowed: bool
    context_key: str
    connected_at: float
    last_seen: float
    active: bool = True

    def send(self, payload: dict[str, Any]) -> bool:
        if not self.active:
            return False
        try:
            return bool(self.send_callback(payload))
        except Exception:
            return False


@dataclass
class ContextState:
    key: str
    browser: str
    profile_id: str
    incognito: bool
    session_id: Optional[str]
    offline_since: Optional[float] = None


def _browser_label(browser: str) -> str:
    return {"brave": "Brave", "chrome": "Chrome", "chromium": "Chromium"}.get(str(browser or "").lower(), str(browser or "").title())


def _format_existing_summary(initiator_browser: str, existing_records: list[TabRecord]) -> str:
    if not existing_records:
        return ""
    other_browser_records = [r for r in existing_records if r.browser != initiator_browser]
    same_browser_records = [r for r in existing_records if r.browser == initiator_browser]

    parts: list[str] = []
    if other_browser_records:
        other_browsers = list(dict.fromkeys(r.browser for r in other_browser_records))
        labels = [_browser_label(b) for b in other_browsers]
        if len(labels) == 1:
            browser_str = labels[0]
        elif len(labels) == 2:
            browser_str = f"{labels[0]} and {labels[1]}"
        else:
            browser_str = ", ".join(labels[:-1]) + f", and {labels[-1]}"
        parts.append(browser_str)

    if same_browser_records:
        cur_label = _browser_label(initiator_browser)
        parts.append(f"another {cur_label} window")

    if not parts:
        return ""
    joined = " and ".join(parts)
    return f"Already open in {joined}"


@dataclass
class TabRecord:
    context_key: str
    tab_id: str
    owner_session_id: str
    browser: str
    incognito: bool
    window_id: int
    url: str
    title: str
    active: bool
    first_seen: float
    last_seen: float
    last_active: float

    @property
    def key(self) -> tuple[str, str]:
        return (self.context_key, self.tab_id)


@dataclass
class Incident:
    id: str
    url: str
    initiator_key: tuple[str, str]
    created_at: float
    status: str = "pending"
    selected: Optional[str] = None
    transfer_id: Optional[str] = None


@dataclass
class Transfer:
    id: str
    incident_id: str
    target_browser: str
    url: str
    incognito: bool
    created_at: float
    expires_at: float


class Coordinator:
    def __init__(self, launcher: Optional[BrowserLauncher] = None) -> None:
        self.lock = threading.RLock()
        self.launcher = launcher or BrowserLauncher()
        self.clients: dict[str, ClientSession] = {}
        self.contexts: dict[str, ContextState] = {}
        self.tabs: dict[tuple[str, str], TabRecord] = {}
        self.incidents: dict[str, Incident] = {}
        self.transfers: dict[str, Transfer] = {}
        self.last_activity = time.time()

    @staticmethod
    def _clean_string(value: Any, maximum: int) -> str:
        return str(value or "")[:maximum]

    def register_client(self, sender: Callable[[dict[str, Any]], bool], message: dict[str, Any], transport: str) -> Optional[ClientSession]:
        extension_id = self._clean_string(message.get("extension_id"), 64)
        if extension_id != EXTENSION_ID:
            sender({"type": "fatal", "error": "Unrecognized extension identity."})
            return None
        supplied = self._clean_string(message.get("browser"), 32).lower()
        native = self._clean_string(message.get("_native_browser"), 32).lower()
        browser = native if native in SUPPORTED_BROWSERS else supplied
        if browser not in SUPPORTED_BROWSERS:
            browser = "chromium"
        profile_id = self._clean_string(message.get("profile_id"), 128) or str(uuid.uuid4())
        incognito = bool(message.get("incognito_context"))
        context_key = f"{browser}:{profile_id}:{'private' if incognito else 'regular'}"
        now_value = time.time()
        session = ClientSession(
            id=str(uuid.uuid4()),
            send_callback=sender,
            transport=transport,
            browser=browser,
            profile_id=profile_id,
            incognito=incognito,
            incognito_allowed=bool(message.get("incognito_allowed")),
            file_scheme_allowed=bool(message.get("file_scheme_allowed")),
            context_key=context_key,
            connected_at=now_value,
            last_seen=now_value,
        )
        with self.lock:
            old_context = self.contexts.get(context_key)
            if old_context and old_context.session_id in self.clients:
                self.clients[old_context.session_id].active = False
            self.clients[session.id] = session
            self.contexts[context_key] = ContextState(context_key, browser, profile_id, incognito, session.id, None)
            for record in self.tabs.values():
                if record.context_key == context_key:
                    record.owner_session_id = session.id
            self.last_activity = now_value
        session.send({"type": "hello_ack", "browser": browser, "transport": transport, "version": APP_VERSION})
        return session

    def disconnect(self, session: Optional[ClientSession]) -> None:
        if not session:
            return
        now_value = time.time()
        with self.lock:
            session.active = False
            self.clients.pop(session.id, None)
            context = self.contexts.get(session.context_key)
            if context and context.session_id == session.id:
                context.session_id = None
                context.offline_since = now_value
            self.last_activity = now_value

    def _reply(self, session: ClientSession, request: dict[str, Any], ok: bool = True, **payload: Any) -> None:
        request_id = request.get("request_id")
        if request_id:
            session.send({"type": "response", "request_id": request_id, "ok": ok, **payload})

    def _context_live_locked(self, context_key: str, now_value: Optional[float] = None) -> bool:
        now_value = now_value or time.time()
        context = self.contexts.get(context_key)
        if not context:
            return False
        if context.session_id and context.session_id in self.clients and self.clients[context.session_id].active:
            return True
        return context.offline_since is not None and now_value - context.offline_since <= OFFLINE_GRACE_SECONDS

    def _session_for_context_locked(self, context_key: str) -> Optional[ClientSession]:
        context = self.contexts.get(context_key)
        if not context or not context.session_id:
            return None
        session = self.clients.get(context.session_id)
        return session if session and session.active else None

    def _upsert_tab_locked(self, session: ClientSession, payload: dict[str, Any]) -> TabRecord:
        now_value = time.time()
        tab_id = self._clean_string(payload.get("tab_id"), 80)
        url = self._clean_string(payload.get("url"), MAX_URL_CHARS)
        if not tab_id or not url:
            raise GuardError("A tab id and URL are required.")
        key = (session.context_key, tab_id)
        existing = self.tabs.get(key)
        active = bool(payload.get("active"))
        if active:
            for record in self.tabs.values():
                if record.context_key == session.context_key:
                    record.active = False
        record = TabRecord(
            context_key=session.context_key,
            tab_id=tab_id,
            owner_session_id=session.id,
            browser=session.browser,
            incognito=bool(payload.get("incognito", session.incognito)),
            window_id=int(payload.get("window_id", -1)),
            url=url,
            title=self._clean_string(payload.get("title"), MAX_TITLE_CHARS),
            active=active,
            first_seen=existing.first_seen if existing else now_value,
            last_seen=now_value,
            last_active=now_value if active else (existing.last_active if existing else 0.0),
        )
        self.tabs[key] = record
        return record

    def _live_records_locked(self, url: str, exclude: Optional[tuple[str, str]] = None) -> list[TabRecord]:
        now_value = time.time()
        return [
            record for key, record in self.tabs.items()
            if key != exclude
            and record.url == url
            and now_value - record.last_seen <= TAB_STALE_SECONDS
            and self._context_live_locked(record.context_key, now_value)
        ]

    def _incident_payload_locked(self, incident: Incident) -> dict[str, Any]:
        records = self._live_records_locked(incident.url)
        initiator = self.tabs.get(incident.initiator_key)
        if initiator and all(record.key != initiator.key for record in records):
            records.append(initiator)
        by_browser = {browser: 0 for browser in SUPPORTED_BROWSERS}
        for record in records:
            by_browser[record.browser] = by_browser.get(record.browser, 0) + 1
        current_browser = initiator.browser if initiator else "chromium"
        existing_records = [r for r in records if r.key != incident.initiator_key]
        existing_browsers = list(dict.fromkeys(r.browser for r in existing_records))
        existing_browser_labels = [_browser_label(b) for b in existing_browsers]
        primary_existing_browser = existing_browsers[0] if existing_browsers else ""
        primary_existing_label = _browser_label(primary_existing_browser) if primary_existing_browser else ""
        existing_summary = _format_existing_summary(current_browser, existing_records)
        existing_copies = [
            {
                "browser": r.browser,
                "browser_label": _browser_label(r.browser),
                "tab_id": r.tab_id,
                "window_id": r.window_id,
                "incognito": bool(r.incognito),
                "title": r.title or "",
                "is_same_browser": bool(initiator and r.browser == initiator.browser),
            }
            for r in existing_records
        ]
        return {
            "incident_id": incident.id,
            "url": incident.url,
            "current_browser": current_browser,
            "copy_count": len(records),
            "private_copy_count": sum(1 for record in records if record.incognito),
            "copies_by_browser": by_browser,
            "existing_browsers": existing_browsers,
            "existing_browser_labels": existing_browser_labels,
            "primary_existing_browser": primary_existing_browser,
            "primary_existing_label": primary_existing_label,
            "existing_summary": existing_summary,
            "existing_copies": existing_copies,
        }

    def _send_actions(self, actions: Iterable[tuple[ClientSession, dict[str, Any]]]) -> None:
        for session, payload in actions:
            session.send({"type": "event", **payload})

    def _finalize_locked(self, incident: Incident, keep_key: tuple[str, str]) -> list[tuple[ClientSession, dict[str, Any]]]:
        keep = self.tabs.get(keep_key)
        if not keep:
            raise GuardError("The selected copy is no longer open.")
        actions: list[tuple[ClientSession, dict[str, Any]]] = []
        copies = self._live_records_locked(incident.url)
        if all(record.key != keep_key for record in copies):
            copies.append(keep)
        for record in copies:
            owner = self._session_for_context_locked(record.context_key)
            if record.key == keep_key:
                record.active = True
                record.last_active = time.time()
                if owner:
                    if record.key == incident.initiator_key:
                        actions.append((owner, {"action": "keep_current", "tab_id": record.tab_id, "incident_id": incident.id, "url": incident.url}))
                    else:
                        actions.append((owner, {"action": "activate_tab", "tab_id": record.tab_id, "incident_id": incident.id, "expected_url": incident.url}))
                continue
            self.tabs.pop(record.key, None)
            if owner:
                actions.append((owner, {"action": "close_tab", "tab_id": record.tab_id, "incident_id": incident.id, "expected_url": incident.url}))
        incident.status = "resolved"
        return actions

    def _choose_target_session_locked(self, browser: str, wants_incognito: bool) -> Optional[ClientSession]:
        candidates = [session for session in self.clients.values() if session.active and session.browser == browser]
        if wants_incognito:
            # Manifest V3 split mode gives private windows a separate extension
            # process. Never ask a regular-context worker to manufacture a
            # private window: some Chromium builds return a null Window object,
            # and doing so can cross the privacy boundary. If no live private
            # context exists, launch the browser with --incognito instead and
            # let that private extension context claim the transferred tab.
            exact = [session for session in candidates if session.incognito]
            return max(exact, key=lambda item: item.last_seen) if exact else None
        regular = [session for session in candidates if not session.incognito]
        return max(regular, key=lambda item: item.last_seen) if regular else None

    def _complete_transfer_locked(self, transfer: Transfer, keep_key: tuple[str, str]) -> list[tuple[ClientSession, dict[str, Any]]]:
        incident = self.incidents.get(transfer.incident_id)
        if not incident:
            self.transfers.pop(transfer.id, None)
            return []
        actions = self._finalize_locked(incident, keep_key)
        self.transfers.pop(transfer.id, None)
        incident.transfer_id = None
        return actions

    def handle(self, session: ClientSession, message: dict[str, Any]) -> None:
        session.last_seen = time.time()
        self.last_activity = session.last_seen
        context = self.contexts.get(session.context_key)
        if not context or context.session_id != session.id:
            self._reply(session, message, False, error="This browser connection was superseded by a newer one.")
            return
        kind = str(message.get("type", ""))
        try:
            if kind == "snapshot":
                self._handle_snapshot(session, message)
            elif kind == "claim":
                self._handle_claim(session, message)
            elif kind == "resolve":
                self._handle_resolve(session, message)
            elif kind == "tab_closed":
                self._handle_tab_closed(session, message)
            elif kind == "touch_tab":
                self._handle_touch(session, message)
            elif kind == "cancel_incident":
                self._handle_cancel(session, message)
            elif kind == "status":
                self._handle_status(session, message)
            elif kind == "create_tab_failed":
                self._handle_create_failed(session, message)
            elif kind in {"heartbeat", "created_tab_ack", "tab_closed_ack", "tab_close_failed", "tab_close_skipped"}:
                self._reply(session, message, True)
            else:
                self._reply(session, message, False, error=f"Unknown coordinator message: {kind}")
        except GuardError as exc:
            self._reply(session, message, False, error=str(exc))
        except Exception as exc:
            log_runtime(f"coordinator handler error: {type(exc).__name__}")
            self._reply(session, message, False, error="The local coordinator encountered an internal error.")

    def _handle_snapshot(self, session: ClientSession, message: dict[str, Any]) -> None:
        values = message.get("tabs", [])
        if not isinstance(values, list) or len(values) > MAX_TABS_PER_SNAPSHOT:
            raise GuardError("The tab snapshot is invalid or too large.")
        with self.lock:
            seen: set[tuple[str, str]] = set()
            for payload in values:
                if not isinstance(payload, dict):
                    continue
                record = self._upsert_tab_locked(session, payload)
                seen.add(record.key)
            for key in [key for key in self.tabs if key[0] == session.context_key and key not in seen]:
                self.tabs.pop(key, None)
        self._reply(session, message, True, result="snapshot_accepted")

    def _handle_claim(self, session: ClientSession, message: dict[str, Any]) -> None:
        payload = message.get("tab")
        if not isinstance(payload, dict):
            raise GuardError("A tab payload is required.")
        actions: list[tuple[ClientSession, dict[str, Any]]] = []
        with self.lock:
            record = self._upsert_tab_locked(session, payload)
            matching_transfer = next((
                transfer for transfer in self.transfers.values()
                if transfer.target_browser == session.browser
                and transfer.url == record.url
                and transfer.incognito == record.incognito
                and transfer.expires_at >= time.time()
            ), None)
            if matching_transfer:
                actions = self._complete_transfer_locked(matching_transfer, record.key)
                result = {"result": "transfer_completed", "transfer_id": matching_transfer.id}
            else:
                duplicates = self._live_records_locked(record.url, exclude=record.key)
                if not duplicates:
                    result = {"result": "unique"}
                else:
                    incident = next((
                        item for item in self.incidents.values()
                        if item.status == "pending" and item.initiator_key == record.key and item.url == record.url
                    ), None)
                    if not incident:
                        incident = Incident(str(uuid.uuid4()), record.url, record.key, time.time())
                        self.incidents[incident.id] = incident
                    result = {"result": "duplicate", "incident": self._incident_payload_locked(incident)}
        self._reply(session, message, True, **result)
        self._send_actions(actions)

    def _handle_resolve(self, session: ClientSession, message: dict[str, Any]) -> None:
        incident_id = self._clean_string(message.get("incident_id"), 80)
        requester_tab_id = self._clean_string(message.get("requester_tab_id"), 80)
        choice = self._clean_string(message.get("choice"), 32).lower()
        launch: Optional[tuple[str, str, bool, str]] = None
        actions: list[tuple[ClientSession, dict[str, Any]]] = []
        with self.lock:
            incident = self.incidents.get(incident_id)
            if not incident or incident.status in {"resolved", "cancelled"}:
                raise GuardError("This duplicate alert is no longer active.")
            if incident.initiator_key != (session.context_key, requester_tab_id):
                raise GuardError("This browser tab does not own the duplicate alert.")
            if incident.status == "transferring":
                self._reply(session, message, True, pending=True, message="The selected browser is still opening…")
                return
            copies = self._live_records_locked(incident.url)
            initiator = self.tabs.get(incident.initiator_key)
            if not initiator:
                raise GuardError("The initiating tab is no longer open.")
            if choice == "current":
                actions = self._finalize_locked(incident, incident.initiator_key)
                response = {"pending": False, "result": "resolved"}
            elif choice in SUPPORTED_BROWSERS:
                candidates = [record for record in copies if record.browser == choice]
                other_candidates = [record for record in candidates if record.key != incident.initiator_key]
                pool = other_candidates or candidates
                if pool:
                    keep = max(pool, key=lambda record: (record.active, record.last_active, -record.first_seen))
                    actions = self._finalize_locked(incident, keep.key)
                    response = {"pending": False, "result": "resolved"}
                else:
                    transfer = Transfer(
                        id=str(uuid.uuid4()),
                        incident_id=incident.id,
                        target_browser=choice,
                        url=incident.url,
                        incognito=initiator.incognito,
                        created_at=time.time(),
                        expires_at=time.time() + TRANSFER_TIMEOUT_SECONDS,
                    )
                    self.transfers[transfer.id] = transfer
                    incident.status = "transferring"
                    incident.selected = choice
                    incident.transfer_id = transfer.id
                    target = self._choose_target_session_locked(choice, initiator.incognito)
                    if target:
                        actions.append((target, {
                            "action": "create_tab", "transfer_id": transfer.id,
                            "url": incident.url, "incognito": initiator.incognito,
                        }))
                    else:
                        launch = (choice, incident.url, initiator.incognito, transfer.id)
                    response = {"pending": True, "message": f"Opening {choice.title()}…", "transfer_id": transfer.id}
            else:
                raise GuardError("Choose Current browser, Brave, Chrome, or Chromium.")

        if launch:
            ok, detail = self.launcher.open(launch[0], launch[1], launch[2])
            if not ok:
                with self.lock:
                    transfer = self.transfers.pop(launch[3], None)
                    if transfer:
                        incident = self.incidents.get(transfer.incident_id)
                        if incident:
                            incident.status = "pending"
                            incident.transfer_id = None
                self._reply(session, message, False, error=detail)
                return
        self._reply(session, message, True, **response)
        self._send_actions(actions)

    def _handle_tab_closed(self, session: ClientSession, message: dict[str, Any]) -> None:
        tab_id = self._clean_string(message.get("tab_id"), 80)
        key = (session.context_key, tab_id)
        with self.lock:
            self.tabs.pop(key, None)
            for incident in self.incidents.values():
                if incident.initiator_key == key and incident.status != "resolved":
                    incident.status = "cancelled"
        self._reply(session, message, True)

    def _handle_touch(self, session: ClientSession, message: dict[str, Any]) -> None:
        tab_id = self._clean_string(message.get("tab_id"), 80)
        key = (session.context_key, tab_id)
        with self.lock:
            record = self.tabs.get(key)
            if record:
                for item in self.tabs.values():
                    if item.context_key == session.context_key:
                        item.active = False
                record.active = True
                record.last_active = time.time()
                record.last_seen = time.time()
        self._reply(session, message, True)

    def _handle_cancel(self, session: ClientSession, message: dict[str, Any]) -> None:
        incident_id = self._clean_string(message.get("incident_id"), 80)
        with self.lock:
            incident = self.incidents.get(incident_id)
            if incident and incident.initiator_key[0] == session.context_key and incident.status != "resolved":
                incident.status = "cancelled"
        self._reply(session, message, True)

    def _handle_status(self, session: ClientSession, message: dict[str, Any]) -> None:
        with self.lock:
            contexts = [context for context in self.contexts.values() if self._context_live_locked(context.key)]
            by_browser = {browser: 0 for browser in SUPPORTED_BROWSERS}
            for context in contexts:
                by_browser[context.browser] = by_browser.get(context.browser, 0) + 1
            self._reply(
                session,
                message,
                True,
                result="status",
                active_contexts=len(contexts),
                private_contexts=sum(1 for context in contexts if context.incognito),
                contexts_by_browser=by_browser,
                tracked_tabs=len(self.tabs),
                pending_incidents=sum(1 for incident in self.incidents.values() if incident.status == "pending"),
                transferring_incidents=sum(1 for incident in self.incidents.values() if incident.status == "transferring"),
            )

    def _handle_create_failed(self, session: ClientSession, message: dict[str, Any]) -> None:
        transfer_id = self._clean_string(message.get("transfer_id"), 80)
        error = self._clean_string(message.get("error"), 500) or "The browser could not create the selected tab."
        action: Optional[tuple[ClientSession, dict[str, Any]]] = None
        with self.lock:
            transfer = self.transfers.pop(transfer_id, None)
            if transfer:
                incident = self.incidents.get(transfer.incident_id)
                if incident:
                    incident.status = "pending"
                    incident.transfer_id = None
                    owner = self._session_for_context_locked(incident.initiator_key[0])
                    if owner:
                        action = (owner, {"action": "resolution_error", "tab_id": incident.initiator_key[1], "incident_id": incident.id, "error": error})
        if action:
            self._send_actions([action])
        self._reply(session, message, True)

    def housekeeping(self) -> list[tuple[ClientSession, dict[str, Any]]]:
        now_value = time.time()
        actions: list[tuple[ClientSession, dict[str, Any]]] = []
        with self.lock:
            for context_key, context in list(self.contexts.items()):
                if context.session_id is None and context.offline_since and now_value - context.offline_since > OFFLINE_GRACE_SECONDS:
                    for key in [key for key in self.tabs if key[0] == context_key]:
                        self.tabs.pop(key, None)
                    self.contexts.pop(context_key, None)
            for key, record in list(self.tabs.items()):
                if now_value - record.last_seen > TAB_STALE_SECONDS:
                    self.tabs.pop(key, None)
            for transfer_id, transfer in list(self.transfers.items()):
                if transfer.expires_at < now_value:
                    self.transfers.pop(transfer_id, None)
                    incident = self.incidents.get(transfer.incident_id)
                    if incident and incident.status == "transferring":
                        incident.status = "pending"
                        incident.transfer_id = None
                        owner = self._session_for_context_locked(incident.initiator_key[0])
                        if owner:
                            actions.append((owner, {
                                "action": "resolution_error", "tab_id": incident.initiator_key[1],
                                "incident_id": incident.id,
                                "error": "The selected browser did not register the new tab in time. Existing tabs were left open.",
                            }))
            for incident_id, incident in list(self.incidents.items()):
                if now_value - incident.created_at > INCIDENT_TTL_SECONDS:
                    self.incidents.pop(incident_id, None)
        return actions

    def active_client_count(self) -> int:
        with self.lock:
            return sum(1 for session in self.clients.values() if session.active)


class CoordinatorHandlerMixin:
    coordinator: Coordinator
    session: Optional[ClientSession]
    transport_name: str

    def process_message(self, message: dict[str, Any]) -> bool:
        if self.session is None:
            if message.get("type") != "hello":
                self.send_json({"type": "fatal", "error": "A hello message is required first."})
                return False
            self.session = self.server.coordinator.register_client(self.send_json, message, self.transport_name)  # type: ignore[attr-defined]
            return self.session is not None
        self.server.coordinator.handle(self.session, message)  # type: ignore[attr-defined]
        return True


class ThreadingUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class UnixCoordinatorHandler(CoordinatorHandlerMixin, socketserver.StreamRequestHandler):
    transport_name = "native"

    def setup(self) -> None:
        super().setup()
        self.session = None
        self.send_lock = threading.Lock()

    def send_json(self, payload: dict[str, Any]) -> bool:
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
        if len(data) > MAX_MESSAGE_BYTES:
            return False
        try:
            with self.send_lock:
                self.wfile.write(data)
                self.wfile.flush()
            return True
        except OSError:
            return False

    def handle(self) -> None:
        while True:
            line = self.rfile.readline(MAX_MESSAGE_BYTES + 1)
            if not line or len(line) > MAX_MESSAGE_BYTES:
                break
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                break
            if not isinstance(message, dict) or not self.process_message(message):
                break

    def finish(self) -> None:
        try:
            self.server.coordinator.disconnect(self.session)  # type: ignore[attr-defined]
        finally:
            super().finish()


class ThreadingWebSocketServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


class WebSocketCoordinatorHandler(CoordinatorHandlerMixin, socketserver.BaseRequestHandler):
    transport_name = "websocket"

    def setup(self) -> None:
        self.session = None
        self.send_lock = threading.Lock()
        self.buffer = bytearray()

    def _recv_exact(self, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            chunk = self.request.recv(size - len(result))
            if not chunk:
                raise EOFError
            result.extend(chunk)
        return bytes(result)

    def _read_headers(self) -> tuple[str, dict[str, str]]:
        data = bytearray()
        while b"\r\n\r\n" not in data:
            chunk = self.request.recv(4096)
            if not chunk:
                raise EOFError
            data.extend(chunk)
            if len(data) > 32_768:
                raise GuardError("WebSocket headers are too large.")
        head, remainder = data.split(b"\r\n\r\n", 1)
        self.buffer.extend(remainder)
        lines = head.decode("latin-1").split("\r\n")
        request_line = lines[0]
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        return request_line, headers

    def _buffered_exact(self, size: int) -> bytes:
        while len(self.buffer) < size:
            chunk = self.request.recv(max(4096, size - len(self.buffer)))
            if not chunk:
                raise EOFError
            self.buffer.extend(chunk)
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def _send_frame(self, opcode: int, payload: bytes = b"") -> bool:
        length = len(payload)
        if length < 126:
            header = bytes([0x80 | opcode, length])
        elif length <= 0xFFFF:
            header = bytes([0x80 | opcode, 126]) + struct.pack("!H", length)
        else:
            header = bytes([0x80 | opcode, 127]) + struct.pack("!Q", length)
        try:
            with self.send_lock:
                self.request.sendall(header + payload)
            return True
        except OSError:
            return False

    def send_json(self, payload: dict[str, Any]) -> bool:
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(data) > MAX_MESSAGE_BYTES:
            return False
        return self._send_frame(0x1, data)

    def _read_frame(self) -> Optional[str]:
        first, second = self._buffered_exact(2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._buffered_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._buffered_exact(8))[0]
        if length > MAX_MESSAGE_BYTES or not masked or not fin:
            raise GuardError("Unsupported WebSocket frame.")
        mask = self._buffered_exact(4)
        payload = bytearray(self._buffered_exact(length))
        for index in range(length):
            payload[index] ^= mask[index % 4]
        if opcode == 0x8:
            return None
        if opcode == 0x9:
            self._send_frame(0xA, bytes(payload))
            return ""
        if opcode != 0x1:
            return ""
        return payload.decode("utf-8")

    def handle(self) -> None:
        config = self.server.config  # type: ignore[attr-defined]
        try:
            request_line, headers = self._read_headers()
            parts = request_line.split()
            expected_path = f"/{config['token']}"
            if len(parts) < 2 or parts[0] != "GET" or parts[1].split("?", 1)[0] != expected_path:
                self.request.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                return
            origin = headers.get("origin", "")
            if origin and origin.rstrip("/") != EXTENSION_ORIGIN.rstrip("/"):
                self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                return
            key = headers.get("sec-websocket-key", "")
            if headers.get("upgrade", "").lower() != "websocket" or not key:
                self.request.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                return
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")
            response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode("ascii")
            self.request.sendall(response)
            while True:
                text = self._read_frame()
                if text is None:
                    break
                if not text:
                    continue
                message = json.loads(text)
                if not isinstance(message, dict) or not self.process_message(message):
                    break
        except (EOFError, OSError, UnicodeDecodeError, json.JSONDecodeError, GuardError):
            pass
        finally:
            self.server.coordinator.disconnect(self.session)  # type: ignore[attr-defined]


def detect_parent_browser() -> str:
    chunks: list[str] = []
    pid = os.getppid()
    try:
        if supported_platform() == "linux":
            for _ in range(6):
                if pid <= 1:
                    break
                proc = Path(f"/proc/{pid}")
                try:
                    chunks.append(os.readlink(proc / "exe"))
                except OSError:
                    pass
                try:
                    chunks.append((proc / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "ignore"))
                    status = (proc / "status").read_text(encoding="utf-8", errors="ignore")
                    parent_line = next((line for line in status.splitlines() if line.startswith("PPid:")), "")
                    pid = int(parent_line.split()[1]) if parent_line else 1
                except (OSError, ValueError, IndexError):
                    break
        else:
            for _ in range(6):
                completed = subprocess.run(["ps", "-p", str(pid), "-o", "ppid=", "-o", "command="], capture_output=True, text=True, timeout=2)
                line = completed.stdout.strip()
                if not line:
                    break
                parts = line.split(None, 1)
                pid = int(parts[0])
                if len(parts) > 1:
                    chunks.append(parts[1])
    except Exception:
        pass
    joined = " ".join(chunks).lower()
    if "brave" in joined:
        return "brave"
    if "chromium" in joined:
        return "chromium"
    if "google chrome" in joined or "google-chrome" in joined or "/chrome" in joined:
        return "chrome"
    return ""


def read_native_message(stream: io.BufferedReader) -> Optional[dict[str, Any]]:
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise EOFError
    length = struct.unpack("=I", header)[0]
    if length > 64 * 1024 * 1024:
        raise GuardError("Native message is too large.")
    data = stream.read(length)
    if len(data) != length:
        raise EOFError
    message = json.loads(data.decode("utf-8"))
    if not isinstance(message, dict):
        raise GuardError("Native message must be a JSON object.")
    return message


def write_native_message(stream: io.BufferedWriter, payload: dict[str, Any], lock: threading.Lock) -> None:
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(data) > 1024 * 1024:
        raise GuardError("Native response is too large.")
    with lock:
        stream.write(struct.pack("=I", len(data)))
        stream.write(data)
        stream.flush()


def daemon_is_reachable() -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            sock.connect(str(socket_path()))
        return True
    except OSError:
        return False


def _process_is_our_daemon(pid: int) -> bool:
    if pid <= 1 or pid == os.getpid():
        return False
    try:
        if supported_platform() == "linux":
            command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "ignore")
        else:
            completed = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=2)
            command = completed.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return "cross_browser_duplicate_tab_guard.py" in command and "daemon" in command


def stop_running_daemon(timeout: float = 5.0) -> None:
    path = daemon_pid_path()
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return
    if not _process_is_our_daemon(pid):
        path.unlink(missing_ok=True)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        path.unlink(missing_ok=True)
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    path.unlink(missing_ok=True)


def start_daemon_process(persistent: bool = False) -> None:
    ensure_private_dir(runtime_dir())
    script = installed_script_path() if installed_script_path().exists() else Path(__file__).resolve()
    command = [str(script), "daemon"]
    if persistent:
        command.append("--persistent")
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log, "ab", buffering=0)
    try:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=handle, stderr=handle, start_new_session=True, close_fds=True)
    finally:
        handle.close()


def ensure_daemon() -> None:
    if daemon_is_reachable():
        return
    ensure_private_dir(runtime_dir())
    with open(startup_lock_path(), "a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if daemon_is_reachable():
            return
        start_daemon_process(persistent=False)
        deadline = time.time() + 7.0
        while time.time() < deadline:
            if daemon_is_reachable():
                return
            time.sleep(0.08)
    raise GuardError("The local coordinator could not be started.")


def native_host_main(origin: str = "") -> int:
    try:
        ensure_daemon()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(str(socket_path()))
    except Exception as exc:
        log_runtime(f"native host startup failed: {type(exc).__name__}")
        return 1

    output_lock = threading.Lock()
    stop = threading.Event()

    def daemon_reader() -> None:
        buffer = bytearray()
        try:
            while not stop.is_set():
                chunk = sock.recv(8192)
                if not chunk:
                    break
                buffer.extend(chunk)
                while b"\n" in buffer:
                    line, _, remainder = buffer.partition(b"\n")
                    buffer[:] = remainder
                    if not line:
                        continue
                    message = json.loads(line.decode("utf-8"))
                    if isinstance(message, dict):
                        write_native_message(sys.stdout.buffer, message, output_lock)
        except Exception:
            pass
        finally:
            stop.set()
            try:
                sys.stdin.close()
            except OSError:
                pass

    thread = threading.Thread(target=daemon_reader, name="daemon-to-browser", daemon=True)
    thread.start()
    native_browser = detect_parent_browser()
    try:
        while not stop.is_set():
            message = read_native_message(sys.stdin.buffer)
            if message is None:
                break
            if message.get("type") == "hello":
                message["_native_browser"] = native_browser
                message["_origin"] = origin
            data = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
            sock.sendall(data)
    except (EOFError, OSError, GuardError, json.JSONDecodeError):
        pass
    finally:
        stop.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()
    return 0


def daemon_main(persistent: bool = False) -> int:
    config = load_config(create=False)
    ensure_private_dir(runtime_dir())
    lock_file = open(daemon_lock_path(), "a+b")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            return 0
        raise

    sock_path = socket_path()
    if sock_path.exists():
        try:
            sock_path.unlink()
        except OSError:
            pass
    coordinator = Coordinator()
    unix_server = ThreadingUnixServer(str(sock_path), UnixCoordinatorHandler)
    unix_server.coordinator = coordinator  # type: ignore[attr-defined]
    os.chmod(sock_path, 0o600)
    ws_server: Optional[ThreadingWebSocketServer]
    try:
        ws_server = ThreadingWebSocketServer(("127.0.0.1", int(config["ws_port"])), WebSocketCoordinatorHandler)
        ws_server.coordinator = coordinator  # type: ignore[attr-defined]
        ws_server.config = config  # type: ignore[attr-defined]
    except OSError as exc:
        ws_server = None
        log_runtime(f"WebSocket fallback unavailable: {type(exc).__name__}")

    atomic_write(daemon_pid_path(), f"{os.getpid()}\n", 0o600)
    stopped = threading.Event()

    def serve(server: socketserver.BaseServer) -> None:
        try:
            server.serve_forever(poll_interval=0.25)
        finally:
            stopped.set()

    unix_thread = threading.Thread(target=serve, args=(unix_server,), daemon=True, name="unix-coordinator")
    unix_thread.start()
    if ws_server is not None:
        ws_thread = threading.Thread(target=serve, args=(ws_server,), daemon=True, name="websocket-coordinator")
        ws_thread.start()
    log_runtime(f"coordinator started version={APP_VERSION} persistent={persistent}")

    def stop_handler(_signum: int, _frame: Any) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    try:
        while not stopped.wait(1.0):
            coordinator._send_actions(coordinator.housekeeping())
            if not persistent and coordinator.active_client_count() == 0 and time.time() - coordinator.last_activity > DAEMON_IDLE_SECONDS:
                break
    finally:
        unix_server.shutdown()
        unix_server.server_close()
        if ws_server is not None:
            ws_server.shutdown()
            ws_server.server_close()
        sock_path.unlink(missing_ok=True)
        try:
            if daemon_pid_path().read_text(encoding="utf-8").strip() == str(os.getpid()):
                daemon_pid_path().unlink(missing_ok=True)
        except OSError:
            pass
        log_runtime("coordinator stopped")
        lock_file.close()
    return 0


def current_source_bytes() -> bytes:
    data = Path(__file__).resolve().read_bytes()
    interpreter = str(Path(sys.executable).resolve())
    lines = data.splitlines(keepends=True)
    if lines:
        lines[0] = f"#!{interpreter}\n".encode("utf-8")
    return b"".join(lines)


def install(open_pages: bool = False) -> int:
    if sys.version_info < (3, 9):
        raise GuardError("Python 3.9 or newer is required.")
    if extension_id_from_key(PUBLIC_KEY_B64) != EXTENSION_ID:
        raise GuardError("The embedded extension key does not match the expected extension ID.")
    root = app_dir()
    ensure_private_dir(root)
    config = load_config(create=True)
    atomic_write(installed_script_path(), current_source_bytes(), 0o755)
    for relative, content in render_extension_files(config).items():
        atomic_write(extension_dir() / relative, content, 0o644)
    local_ext = Path(__file__).resolve().parent / "extension"
    if local_ext.is_dir():
        for root_dir, dirs, files in os.walk(local_ext):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for file in files:
                if file.endswith((".png", ".svg", ".ico", ".jpg")):
                    src_file = Path(root_dir) / file
                    rel = src_file.relative_to(local_ext)
                    dest_file = extension_dir() / rel
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dest_file)
    manifests = install_native_manifests()
    external_exts = install_external_extensions()
    autostart = install_autostart()
    deadline = time.time() + 7.0
    while time.time() < deadline and not daemon_is_reachable():
        time.sleep(0.08)
    if not daemon_is_reachable():
        start_daemon_process(persistent=True)
    if open_pages:
        open_installation_pages()
    print(f"{APP_NAME} {APP_VERSION} installed.")
    print(f"\nExtension folder:\n  {extension_dir()}")
    print(f"\nPinned extension ID:\n  {EXTENSION_ID}")
    print(f"\nNative host manifests written: {len(manifests)}")
    print(f"External extension manifests written: {len(external_exts)}")
    print(f"Autostart: {autostart}")
    print("\nComplete these browser-controlled steps in Brave, Chrome, and Chromium:")
    print("  1. Open the browser's Extensions page and enable Developer mode.")
    print("  2. Choose Load unpacked and select the extension folder above.")
    print("  3. Open this extension's Details page.")
    print("  4. Enable Allow in Incognito/Private.")
    print("  5. Enable Allow access to file URLs for maximum coverage.")
    print("\nThe same extension folder is used in every browser and on both supported operating systems.")
    return 0


def browser_commands() -> dict[str, list[str]]:
    if supported_platform() == "darwin":
        return {
            "brave": ["open", "-a", "Brave Browser", "brave://extensions"],
            "chrome": ["open", "-a", "Google Chrome", "chrome://extensions"],
            "chromium": ["open", "-a", "Chromium", "chrome://extensions"],
        }
    result: dict[str, list[str]] = {}
    launcher = BrowserLauncher()
    for browser, url in {"brave": "brave://extensions", "chrome": "chrome://extensions", "chromium": "chrome://extensions"}.items():
        for prefix in launcher.LINUX_COMMANDS[browser]:
            command = launcher.resolve_linux_prefix(prefix)
            if command:
                result[browser] = [*command, url]
                break
    return result


def open_installation_pages() -> None:
    try:
        if supported_platform() == "darwin":
            subprocess.Popen(["open", str(extension_dir())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            opener = shutil.which("xdg-open")
            if opener:
                subprocess.Popen([opener, str(extension_dir())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass
    for command in browser_commands().values():
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=(supported_platform() == "linux"))
        except OSError:
            continue


def uninstall() -> int:
    stop_autostart()
    stop_running_daemon()
    for directories in native_manifest_dirs().values():
        for directory in directories:
            (directory / f"{HOST_NAME}.json").unlink(missing_ok=True)
    try:
        shutil.rmtree(app_dir())
    except FileNotFoundError:
        pass
    print(f"{APP_NAME} was removed. Remove the unpacked extension from each browser's Extensions page.")
    return 0


def is_tcp_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def find_browser_installations() -> dict[str, str]:
    found: dict[str, str] = {}
    if supported_platform() == "darwin":
        for browser, app in BrowserLauncher.MAC_APPS.items():
            for root in [Path("/Applications"), home_dir() / "Applications"]:
                candidate = root / f"{app}.app"
                if candidate.exists():
                    found[browser] = str(candidate)
                    break
        return found
    launcher = BrowserLauncher()
    for browser, candidates in launcher.LINUX_COMMANDS.items():
        for prefix in candidates:
            command = launcher.resolve_linux_prefix(prefix)
            if command:
                found[browser] = " ".join(command)
                break
    return found


def doctor() -> int:
    print(f"{APP_NAME} doctor")
    print(f"  Version: {APP_VERSION}")
    print(f"  Platform: {platform.platform()}")
    print(f"  Python: {sys.version.split()[0]} ({sys.executable})")
    print(f"  Extension ID: {EXTENSION_ID}")
    print(f"  Installed script: {installed_script_path()} [{'OK' if installed_script_path().exists() else 'MISSING'}]")
    print(f"  Extension folder: {extension_dir()} [{'OK' if (extension_dir() / 'manifest.json').exists() else 'MISSING'}]")
    try:
        config = load_config(create=False)
        print(f"  WebSocket fallback: 127.0.0.1:{config['ws_port']} [{'LISTENING' if is_tcp_listening(config['ws_port']) else 'NOT LISTENING'}]")
    except GuardError as exc:
        print(f"  Configuration: {exc}")
    print(f"  Native socket: {socket_path()} [{'READY' if daemon_is_reachable() else 'NOT READY'}]")
    print("  Browser installations:")
    found = find_browser_installations()
    for browser in ["brave", "chrome", "chromium"]:
        print(f"    {browser.title()}: {found.get(browser, 'not detected')}")
    manifest_count = sum((directory / f"{HOST_NAME}.json").exists() for values in native_manifest_dirs().values() for directory in values)
    print(f"  Native host manifests present: {manifest_count}")
    if any("snap" in value or "flatpak" in value for value in found.values()):
        print("  Note: A confined browser was detected; it will use the loopback WebSocket fallback if native messaging is blocked.")
    print("\nBrowser-controlled permissions cannot be toggled from a script. Check the extension popup in each browser to confirm Incognito/Private and file-URL access are Allowed.")
    return 0


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def __call__(self, payload: dict[str, Any]) -> bool:
        self.messages.append(payload)
        return True

    def response(self, request_id: str) -> dict[str, Any]:
        return next(message for message in reversed(self.messages) if message.get("request_id") == request_id)


def self_test() -> int:
    failures: list[str] = []
    passed: list[str] = []

    def run_check(name: str, callback: Callable[[], None]) -> None:
        try:
            callback()
            passed.append(name)
        except Exception as exc:
            import traceback
            failures.append(f"{name}: {exc}\n{traceback.format_exc()}")

    def extension_validation() -> None:
        assert extension_id_from_key(PUBLIC_KEY_B64) == EXTENSION_ID
        manifest = json.loads(EXTENSION_FILES["manifest.json"])
        assert manifest["manifest_version"] == 3
        assert manifest["version"] == APP_VERSION
        assert manifest["incognito"] == "split"
        assert manifest["background"] == {"service_worker": "service_worker.js"}
        assert set(manifest["permissions"]) >= {"nativeMessaging", "storage", "tabs", "webNavigation", "alarms"}
        assert "downloads" not in manifest["permissions"]
        assert "<all_urls>" in manifest["host_permissions"]
        assert manifest["content_scripts"][0]["matches"] == ["<all_urls>"]
        assert manifest["content_scripts"][0]["run_at"] == "document_start"
        assert EXTENSION_FILES["manifest.json"].count(PUBLIC_KEY_B64) == 1

        worker = EXTENSION_FILES["service_worker.js"]
        content = EXTENSION_FILES["content.js"]
        chooser = EXTENSION_FILES["chooser.js"]
        popup = EXTENSION_FILES["popup.js"]
        assert f'const VERSION = "{APP_VERSION}"' in worker
        assert "registerWebNavigationEvent" in worker
        assert '"onCommitted"' in worker
        assert '"onHistoryStateUpdated"' in worker
        assert '"onReferenceFragmentUpdated"' in worker
        assert "webNavigation.onBeforeNavigate" not in worker
        assert "Content-Disposition" in worker
        assert "claimEpoch" in worker and "currentTabForClaim" in worker
        assert "TRANSIENT_URLS" in worker and "isTransientUrl(url)" in worker
        own_guard = worker.index("if (isOwnExtensionUrl(normalizedUrl))")
        transient_guard = worker.index("if (!isTrackableUrl(normalizedUrl) || isTransientUrl(normalizedUrl))")
        assert own_guard < transient_guard
        own_block = worker[own_guard:transient_guard]
        assert "clearIncidentForNavigation" not in own_block
        assert "sendNativeEvent" not in own_block
        assert 'sendNativeEvent("tab_closed"' in worker
        assert "chrome.downloads" not in worker + content + chooser + popup
        assert "preventDefault()" not in content
        assert "__AUTH_TOKEN__" in worker and "__WS_PORT__" in worker
        for value in ['["current",', '["brave", "Brave"]', '["chrome", "Chrome"]', '["chromium", "Chromium"]']:
            assert value in content
        assert "activeIncident = state" in content
        assert "navigator.brave" in worker
        assert "google chrome" in worker and "Chromium" in worker

    run_check("embedded Manifest V3, permissions, UI, and race guards", extension_validation)

    def javascript_syntax() -> None:
        node = shutil.which("node")
        if not node:
            return
        with tempfile.TemporaryDirectory() as temp:
            config = {"token": "x" * 48, "ws_port": DEFAULT_WS_PORT}
            for name, content in render_extension_files(config).items():
                path = Path(temp) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                if name.endswith(".js"):
                    completed = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
                    if completed.returncode != 0:
                        raise AssertionError(f"{name}: {completed.stderr.strip()}")

    run_check("embedded JavaScript syntax", javascript_syntax)

    def native_paths_and_launch_commands() -> None:
        from unittest import mock

        with mock.patch.object(platform, "system", return_value="Darwin"), \
             mock.patch.object(Path, "home", return_value=Path("/Users/guard-test")), \
             mock.patch.object(shutil, "which", side_effect=lambda name: "/usr/bin/open" if name == "open" else None):
            mac_home = Path("/Users/guard-test").resolve()
            paths = native_manifest_dirs()
            assert paths["chrome"] == [mac_home / "Library/Application Support/Google/Chrome/NativeMessagingHosts"]
            assert paths["chromium"] == [mac_home / "Library/Application Support/Chromium/NativeMessagingHosts"]
            assert paths["brave"] == [mac_home / "Library/Application Support/BraveSoftware/Brave-Browser/NativeMessagingHosts"]
            ext_paths = external_extension_dirs()
            assert ext_paths["chrome"] == [mac_home / "Library/Application Support/Google/Chrome/External Extensions"]
            assert ext_paths["chromium"] == [mac_home / "Library/Application Support/Chromium/External Extensions"]
            assert ext_paths["brave"] == [mac_home / "Library/Application Support/BraveSoftware/Brave-Browser/External Extensions"]
            mac = BrowserLauncher(dry_run=True)
            assert mac.command("brave", "https://example.test/a?x=1&y=2", True) == [
                "/usr/bin/open", "-n", "-a", "Brave Browser", "--args", "--incognito", "--new-window",
                "https://example.test/a?x=1&y=2"
            ]
            assert mac.command("chrome", "file:///tmp/example.txt", False) == [
                "/usr/bin/open", "-a", "Google Chrome", "file:///tmp/example.txt"
            ]

        executables = {
            "brave-browser": "/usr/bin/brave-browser",
            "google-chrome": "/usr/bin/google-chrome",
            "chromium": "/usr/bin/chromium",
        }
        with mock.patch.object(platform, "system", return_value="Linux"), \
             mock.patch.object(Path, "home", return_value=Path("/home/guard-test")), \
             mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/home/guard-test/.config"}, clear=False), \
             mock.patch.object(shutil, "which", side_effect=lambda name: executables.get(name)):
            paths = native_manifest_dirs()
            assert paths["chrome"][0] == Path("/home/guard-test/.config/google-chrome/NativeMessagingHosts")
            assert paths["chromium"][0] == Path("/home/guard-test/.config/chromium/NativeMessagingHosts")
            assert any("snap/chromium" in str(path) for path in paths["chromium"])
            assert paths["brave"][0] == Path("/home/guard-test/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts")
            assert any("com.brave.Browser" in str(path) for path in paths["brave"])
            ext_paths = external_extension_dirs()
            assert ext_paths["chrome"][0] == Path("/home/guard-test/.config/google-chrome/External Extensions")
            assert ext_paths["chromium"][0] == Path("/home/guard-test/.config/chromium/External Extensions")
            assert any(".var/app/com.brave.Browser" in str(path) for path in ext_paths["brave"])
            linux = BrowserLauncher(dry_run=False)
            assert BrowserLauncher.resolve_linux_prefix(("/definitely/missing/browser",)) is None
            url = "https://example.test/path?x=1&literal=;$(no-shell)"
            assert linux.command("brave", url, True) == ["/usr/bin/brave-browser", "--incognito", "--new-window", url]
            assert linux.command("chrome", url, False) == ["/usr/bin/google-chrome", "--new-tab", url]
            assert linux.command("chromium", url, True) == ["/usr/bin/chromium", "--incognito", "--new-window", url]

        assert desktop_exec_quote("/home/Test User/$guard`/runner.py") == '"/home/Test User/\\$guard\\`/runner.py"'

        manifest = json.loads(native_manifest_payload())
        assert manifest["name"] == HOST_NAME
        assert manifest["allowed_origins"] == [EXTENSION_ORIGIN]
        assert Path(manifest["path"]).is_absolute()

    run_check("macOS/Linux browser distinctions and native-host paths", native_paths_and_launch_commands)

    def autostart_artifacts() -> None:
        from types import SimpleNamespace
        from unittest import mock

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mac_home = root / "Mac & User"
            with mock.patch.object(platform, "system", return_value="Darwin"), \
                 mock.patch.object(Path, "home", return_value=mac_home), \
                 mock.patch.object(os, "getuid", return_value=501), \
                 mock.patch.object(subprocess, "run", return_value=SimpleNamespace(returncode=0)):
                assert install_autostart() == "launchd"
                plist_path = launch_agent_path()
                plist = plist_path.read_text(encoding="utf-8")
                assert plist_path.exists()
                assert "systems.venturi.duplicate-tab-guard" in plist
                assert "Mac &amp; User" in plist
                assert "daemon" in plist and "--persistent" in plist

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            linux_home = root / "Linux User"
            config_home = root / "Linux Config"
            data_home = root / "Linux Data $guard`"
            state_home = root / "Linux State"
            env = {
                "XDG_CONFIG_HOME": str(config_home),
                "XDG_DATA_HOME": str(data_home),
                "XDG_STATE_HOME": str(state_home),
            }
            with mock.patch.object(platform, "system", return_value="Linux"), \
                 mock.patch.object(Path, "home", return_value=linux_home), \
                 mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(shutil, "which", return_value=None), \
                 mock.patch(__name__ + ".stop_running_daemon"), \
                 mock.patch(__name__ + ".start_daemon_process"):
                assert install_autostart() == "xdg-autostart"
                desktop = xdg_autostart_path().read_text(encoding="utf-8")
                unit = systemd_unit_path().read_text(encoding="utf-8")
                expected_script = desktop_exec_quote(str(installed_script_path()))
                assert f"Exec={expected_script} daemon --persistent" in desktop
                assert "NoDisplay=true" in desktop
                assert "Restart=on-failure" in unit

    run_check("macOS launchd and Linux autostart artifact generation", autostart_artifacts)

    def register(
        coordinator: Coordinator,
        sender: FakeSender,
        browser: str,
        profile: str,
        incognito: bool,
        transport: str = "websocket",
    ) -> ClientSession:
        session = coordinator.register_client(sender, {
            "type": "hello",
            "extension_id": EXTENSION_ID,
            "browser": browser,
            "profile_id": profile,
            "incognito_context": incognito,
            "incognito_allowed": True,
            "file_scheme_allowed": True,
        }, transport)
        assert session is not None
        return session

    def tab(tab_id: str, url: str, incognito: bool, active: bool = True, window_id: int = 1) -> dict[str, Any]:
        return {
            "tab_id": tab_id,
            "window_id": window_id,
            "url": url,
            "title": f"Tab {tab_id}",
            "incognito": incognito,
            "active": active,
        }

    def exact_url_and_current_browser_flow() -> None:
        coordinator = Coordinator(BrowserLauncher(dry_run=True))
        chrome_sender = FakeSender()
        brave_sender = FakeSender()
        chrome = register(coordinator, chrome_sender, "chrome", "chrome-profile", False)
        brave = register(coordinator, brave_sender, "brave", "brave-profile", True, "native")
        exact = "https://example.test/a?x=1&x=2#z"
        coordinator.handle(chrome, {"type": "snapshot", "request_id": "s1", "tabs": [tab("1", exact, False)]})
        coordinator.handle(brave, {"type": "claim", "request_id": "c1", "tab": tab("2", exact, True, window_id=2)})
        duplicate = brave_sender.response("c1")
        assert duplicate["result"] == "duplicate"
        assert duplicate["incident"]["private_copy_count"] == 1
        assert duplicate["incident"]["copy_count"] == 2
        assert duplicate["incident"]["existing_browsers"] == ["chrome"]
        assert duplicate["incident"]["existing_browser_labels"] == ["Chrome"]
        assert duplicate["incident"]["primary_existing_browser"] == "chrome"
        assert duplicate["incident"]["primary_existing_label"] == "Chrome"
        assert duplicate["incident"]["existing_summary"] == "Already open in Chrome"
        assert len(duplicate["incident"]["existing_copies"]) == 1
        assert duplicate["incident"]["existing_copies"][0]["browser"] == "chrome"
        assert duplicate["incident"]["existing_copies"][0]["is_same_browser"] is False
        incident_id = duplicate["incident"]["incident_id"]
        coordinator.handle(brave, {
            "type": "resolve", "request_id": "r1", "incident_id": incident_id,
            "requester_tab_id": "2", "choice": "current",
        })
        assert brave_sender.response("r1")["result"] == "resolved"
        assert any(message.get("action") == "close_tab" and message.get("tab_id") == "1" for message in chrome_sender.messages)
        assert any(message.get("action") == "keep_current" and message.get("tab_id") == "2" for message in brave_sender.messages)

        # Same-browser duplicate summary test
        url_same = "https://example.test/same-browser"
        coordinator.handle(chrome, {"type": "snapshot", "request_id": "s_same", "tabs": [tab("s1", url_same, False, window_id=1)]})
        coordinator.handle(chrome, {"type": "claim", "request_id": "c_same", "tab": tab("s2", url_same, False, window_id=2)})
        dup_same = chrome_sender.response("c_same")
        assert dup_same["result"] == "duplicate"
        assert dup_same["incident"]["existing_summary"] == "Already open in another Chrome window"
        assert dup_same["incident"]["existing_copies"][0]["is_same_browser"] is True

        # Multi-browser duplicate summary test
        url_multi = "https://example.test/multi"
        coordinator.handle(chrome, {"type": "snapshot", "request_id": "s_m1", "tabs": [tab("m1", url_multi, False, window_id=1)]})
        coordinator.handle(brave, {"type": "claim", "request_id": "s_m2", "tab": tab("m2", url_multi, False, window_id=1)})
        dup_m2 = brave_sender.response("s_m2")
        assert dup_m2["result"] == "duplicate"
        assert dup_m2["incident"]["existing_summary"] == "Already open in Chrome"

        chromium_sender = FakeSender()
        chromium = register(coordinator, chromium_sender, "chromium", "chromium-profile", False)
        coordinator.handle(chromium, {"type": "claim", "request_id": "c_m3", "tab": tab("m3", url_multi, False, window_id=1)})
        dup_m3 = chromium_sender.response("c_m3")
        assert dup_m3["result"] == "duplicate"
        assert dup_m3["incident"]["existing_summary"] == "Already open in Chrome and Brave"

    run_check("regular/private exact-URL duplicate and Current-browser resolution", exact_url_and_current_browser_flow)

    def literal_url_semantics() -> None:
        coordinator = Coordinator(BrowserLauncher(dry_run=True))
        sender_a, sender_b = FakeSender(), FakeSender()
        a = register(coordinator, sender_a, "chrome", "a", False)
        b = register(coordinator, sender_b, "chromium", "b", False)
        base = "https://example.test/p?a=1&b=2#one"
        coordinator.handle(a, {"type": "snapshot", "request_id": "s", "tabs": [tab("1", base, False)]})
        variants = [
            "https://example.test/p?b=2&a=1#one",
            "https://example.test/p?a=1&b=2#two",
            "https://example.test/p?a=1&b=2",
            "https://EXAMPLE.test/p?a=1&b=2#one",
        ]
        for index, value in enumerate(variants, 2):
            request_id = f"v{index}"
            coordinator.handle(b, {"type": "claim", "request_id": request_id, "tab": tab(str(index), value, False)})
            assert sender_b.response(request_id)["result"] == "unique"
        coordinator.handle(b, {"type": "claim", "request_id": "exact", "tab": tab("99", base, False)})
        assert sender_b.response("exact")["result"] == "duplicate"

    run_check("literal URL comparison including query order and fragments", literal_url_semantics)

    def selected_existing_browser_flow() -> None:
        coordinator = Coordinator(BrowserLauncher(dry_run=True))
        brave_sender, chrome_sender = FakeSender(), FakeSender()
        brave = register(coordinator, brave_sender, "brave", "brave", False)
        chrome = register(coordinator, chrome_sender, "chrome", "chrome", False)
        url = "https://example.test/existing"
        coordinator.handle(brave, {"type": "snapshot", "request_id": "s", "tabs": [tab("b", url, False)]})
        coordinator.handle(chrome, {"type": "claim", "request_id": "c", "tab": tab("c", url, False)})
        incident = chrome_sender.response("c")["incident"]
        coordinator.handle(chrome, {
            "type": "resolve", "request_id": "r", "incident_id": incident["incident_id"],
            "requester_tab_id": "c", "choice": "brave",
        })
        assert chrome_sender.response("r")["result"] == "resolved"
        assert any(item.get("action") == "activate_tab" and item.get("tab_id") == "b" for item in brave_sender.messages)
        assert any(item.get("action") == "close_tab" and item.get("tab_id") == "c" for item in chrome_sender.messages)

    run_check("selected existing Brave/Chrome/Chromium copy is retained", selected_existing_browser_flow)

    class RecordingLauncher:
        def __init__(self, fail: bool = False) -> None:
            self.fail = fail
            self.calls: list[tuple[str, str, bool]] = []

        def open(self, browser: str, url: str, incognito: bool) -> tuple[bool, str]:
            self.calls.append((browser, url, incognito))
            return (False, "simulated launch failure") if self.fail else (True, "launched")

    def transfer_to_absent_private_browser() -> None:
        launcher = RecordingLauncher()
        coordinator = Coordinator(launcher)  # type: ignore[arg-type]
        chrome_sender = FakeSender()
        chrome = register(coordinator, chrome_sender, "chrome", "chrome-private", True)
        url = "https://example.test/private-transfer"
        coordinator.handle(chrome, {"type": "snapshot", "request_id": "s", "tabs": [tab("1", url, True)]})
        coordinator.handle(chrome, {"type": "claim", "request_id": "c", "tab": tab("2", url, True)})
        incident = chrome_sender.response("c")["incident"]
        coordinator.handle(chrome, {
            "type": "resolve", "request_id": "r", "incident_id": incident["incident_id"],
            "requester_tab_id": "2", "choice": "brave",
        })
        response = chrome_sender.response("r")
        assert response["pending"] is True
        assert launcher.calls == [("brave", url, True)]
        assert not any(item.get("action") == "close_tab" for item in chrome_sender.messages)

        brave_sender = FakeSender()
        brave = register(coordinator, brave_sender, "brave", "brave-private", True)
        coordinator.handle(brave, {"type": "claim", "request_id": "arrival", "tab": tab("9", url, True)})
        assert brave_sender.response("arrival")["result"] == "transfer_completed"
        closed = {item.get("tab_id") for item in chrome_sender.messages if item.get("action") == "close_tab"}
        assert closed == {"1", "2"}
        assert any(item.get("action") == "activate_tab" and item.get("tab_id") == "9" for item in brave_sender.messages)

    run_check("privacy-preserving transfer to an absent selected browser", transfer_to_absent_private_browser)

    def failed_launch_keeps_every_copy() -> None:
        launcher = RecordingLauncher(fail=True)
        coordinator = Coordinator(launcher)  # type: ignore[arg-type]
        sender = FakeSender()
        session = register(coordinator, sender, "chrome", "chrome", False)
        url = "https://example.test/launch-failure"
        coordinator.handle(session, {"type": "snapshot", "request_id": "s", "tabs": [tab("1", url, False)]})
        coordinator.handle(session, {"type": "claim", "request_id": "c", "tab": tab("2", url, False)})
        incident = sender.response("c")["incident"]
        coordinator.handle(session, {
            "type": "resolve", "request_id": "r", "incident_id": incident["incident_id"],
            "requester_tab_id": "2", "choice": "chromium",
        })
        response = sender.response("r")
        assert response["ok"] is False
        assert "simulated launch failure" in response["error"]
        assert len([record for record in coordinator.tabs.values() if record.url == url]) == 2
        assert not any(item.get("action") == "close_tab" for item in sender.messages)

    run_check("failed cross-browser launch leaves all existing tabs open", failed_launch_keeps_every_copy)

    def native_framing() -> None:
        payload = {"type": "hello", "n": 7, "unicode": "✓"}
        raw = io.BytesIO()
        write_native_message(raw, payload, threading.Lock())
        raw.seek(0)
        assert read_native_message(raw) == payload

    run_check("native messaging framing", native_framing)

    if failures:
        print("Self-test FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Self-test passed:")
    for name in passed:
        print(f"  - {name}")
    return 0


def pack_extension(output_dir: str = "dist") -> int:
    """Package the extension for Chrome Web Store distribution."""
    local_ext = Path(__file__).resolve().parent / "extension"
    src_dir = local_ext if local_ext.is_dir() else extension_dir()

    manifest_path = src_dir / "manifest.json"
    if not manifest_path.exists():
        raise GuardError(f"manifest.json not found in {src_dir}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise GuardError(f"Failed to parse manifest.json: {e}")

    version = manifest.get("version", "0.0.0")
    name = manifest.get("name", "extension")

    if manifest.get("manifest_version") != 3:
        raise GuardError("Chrome Web Store requires manifest_version 3")

    icons = manifest.get("icons", {})
    for req_size in ("16", "48", "128"):
        icon_rel = icons.get(req_size)
        if not icon_rel:
            print(f"Warning: icon size {req_size} is not declared in manifest.json icons")
        elif not (src_dir / icon_rel).exists():
            raise GuardError(f"Icon file referenced in manifest does not exist: {src_dir / icon_rel}")

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    zip_name = f"cross-browser-duplicate-tab-guard-v{version}.zip"
    zip_dest = out_path / zip_name

    ignore_patterns = {".DS_Store", "Thumbs.db", "__pycache__", ".git", ".gitignore"}

    file_count = 0
    with zipfile.ZipFile(zip_dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for root_dir, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d not in ignore_patterns and not d.startswith((".", "_"))]
            for file in sorted(files):
                if file in ignore_patterns or file.endswith(("~", ".bak", ".tmp", ".swp")):
                    continue
                file_path = Path(root_dir) / file
                arcname = file_path.relative_to(src_dir)
                zf.write(file_path, arcname)
                file_count += 1

    size_bytes = zip_dest.stat().st_size
    size_kb = size_bytes / 1024.0
    print(f"Successfully packaged {name} v{version}:")
    print(f"  Archive: {zip_dest}")
    print(f"  Files:   {file_count}")
    print(f"  Size:    {size_kb:.1f} KB ({size_bytes} bytes)")
    print("Ready for Chrome Web Store Developer Dashboard upload.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    install_parser = sub.add_parser("install", help="Install or update the extension and coordinator")
    install_parser.add_argument("--open", action="store_true", help="Open browser extension pages and the extension folder")
    sub.add_parser("uninstall", help="Remove the coordinator and native host registrations")
    sub.add_parser("doctor", help="Check installation paths, browsers, and coordinator connectivity")
    sub.add_parser("self-test", help="Run embedded validation tests")
    pack_parser = sub.add_parser("pack-extension", help="Package extension for Chrome Web Store distribution")
    pack_parser.add_argument("--output-dir", default="dist", help="Output directory for zip archive (default: dist)")
    daemon_parser = sub.add_parser("daemon", help=argparse.SUPPRESS)
    daemon_parser.add_argument("--persistent", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Chromium invokes a native host with the extension origin as argv[1].
    if argv and argv[0].startswith("chrome-extension://"):
        return native_host_main(argv[0])
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            return install(args.open)
        if args.command == "uninstall":
            return uninstall()
        if args.command == "doctor":
            return doctor()
        if args.command == "self-test":
            return self_test()
        if args.command == "pack-extension":
            return pack_extension(args.output_dir)
        if args.command == "daemon":
            return daemon_main(args.persistent)
        parser.print_help()
        return 0
    except GuardError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
