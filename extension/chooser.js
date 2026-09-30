"use strict";

const labels = { brave: "Brave", chrome: "Chrome", chromium: "Chromium" };
let tabId = null;
let incident = null;

const urlEl = document.getElementById("url");
const choiceEl = document.getElementById("choice");
const metaEl = document.getElementById("meta");
const statusEl = document.getElementById("status");
const confirmEl = document.getElementById("confirm");

function setBusy(value) {
  confirmEl.disabled = Boolean(value);
  choiceEl.disabled = Boolean(value);
}

function setStatus(message, kind = "") {
  statusEl.textContent = String(message || "");
  statusEl.className = `status ${kind}`.trim();
}

const bannerEl = document.getElementById("banner");
const badgeListEl = document.getElementById("badge-list");
const bannerSummaryEl = document.getElementById("banner-summary");
const descEl = document.getElementById("description");

async function load() {
  const response = await chrome.runtime.sendMessage({ type: "chooser_get" });
  if (!response || !response.ok || !response.incident) {
    throw new Error("This duplicate alert is no longer active. You may close this tab.");
  }
  tabId = response.tab_id;
  incident = response.incident;
  urlEl.textContent = incident.url || "";
  const titleEl = document.getElementById("title");
  if (titleEl && incident.existing_summary) {
    titleEl.textContent = incident.existing_summary;
  }
  const eyebrowEl = document.querySelector(".eyebrow");
  if (eyebrowEl && incident.primary_existing_label) {
    eyebrowEl.textContent = `Duplicate tab detected • Pre-existing in ${incident.primary_existing_label}`;
  }

  if (incident.existing_summary) {
    if (bannerEl && badgeListEl && bannerSummaryEl) {
      badgeListEl.innerHTML = "";
      const bLabels = incident.existing_browser_labels && incident.existing_browser_labels.length
        ? incident.existing_browser_labels
        : (incident.existing_browsers || []).map((b) => labels[b] || b);
      for (const bName of (bLabels.length ? bLabels : [labels[incident.primary_existing_browser] || "Chromium"])) {
        const pill = document.createElement("span");
        pill.className = `browser-pill ${bName.toLowerCase()}`;
        pill.textContent = bName;
        badgeListEl.append(pill);
      }
      bannerSummaryEl.textContent = incident.existing_summary;
      bannerEl.style.display = "block";
    }
    if (descEl) {
      descEl.textContent = `${incident.existing_summary}. Choose which browser should retain the single live copy. Every other exact-URL copy will be closed.`;
    }
  }

  const existingCopies = Array.isArray(incident.existing_copies) ? incident.existing_copies : [];
  const hasSameBrowserExisting = existingCopies.some((c) => c.is_same_browser);
  const currentLabel = labels[incident.current_browser] || "this browser";
  const currentText = `Current browser — ${currentLabel} (this tab${hasSameBrowserExisting ? "; pre-existing copy also open here" : ""})`;

  choiceEl.innerHTML = "";
  const baseOptions = [
    ["current", currentText],
    ["brave", "Brave"],
    ["chrome", "Chrome"],
    ["chromium", "Chromium"],
  ];

  for (const [value, baseText] of baseOptions) {
    const opt = document.createElement("option");
    opt.value = value;
    let text = baseText;
    if (value === "current") {
      text = currentText;
    } else {
      const matching = existingCopies.filter((c) => c.browser === value && !c.is_same_browser);
      if (matching.length > 0) {
        const priv = matching.some((c) => c.incognito);
        text = `${baseText} (pre-existing tab${priv ? " — private" : ""})`;
      } else if (value === incident.current_browser) {
        if (hasSameBrowserExisting) {
          text = `${baseText} (pre-existing copy in another window)`;
        }
      } else {
        text = `${baseText} (transfer tab here)`;
      }
    }
    opt.textContent = text;
    choiceEl.append(opt);
  }

  const count = Number(incident.copy_count || 2);
  const privateCount = Number(incident.private_copy_count || 0);
  metaEl.innerHTML = "";
  const metaText = document.createElement("p");
  metaText.textContent = `${count} live copies found${privateCount ? `; ${privateCount} in private/incognito context` : ""}.${incident.existing_summary ? ` • ${incident.existing_summary}` : ""}`;
  metaEl.append(metaText);

  if (existingCopies.length > 0) {
    const copiesList = document.createElement("div");
    copiesList.className = "copies-list";
    for (const copy of existingCopies) {
      const item = document.createElement("div");
      item.className = "copy-item";
      const bLabel = copy.browser_label || labels[copy.browser] || copy.browser;
      const winInfo = copy.window_id > 0 ? ` Window ${copy.window_id}` : "";
      const privInfo = copy.incognito ? " (Private)" : "";
      const sameInfo = copy.is_same_browser ? " [same browser]" : " [pre-existing]";
      const titleInfo = copy.title ? ` • "${copy.title}"` : "";
      item.textContent = `• ${bLabel}${sameInfo}${winInfo}${privInfo}${titleInfo}`;
      copiesList.append(item);
    }
    metaEl.append(copiesList);
  }
}

confirmEl.addEventListener("click", async () => {
  if (!incident) return;
  setBusy(true);
  setStatus("Applying your selection…");
  try {
    const response = await chrome.runtime.sendMessage({
      type: "chooser_resolve",
      incident_id: incident.incident_id,
      choice: choiceEl.value,
    });
    if (!response || !response.ok) throw new Error((response && response.error) || "The duplicate could not be resolved.");
    setStatus(response.pending ? (response.message || "Opening the selected browser…") : "Selection applied.", response.pending ? "" : "success");
  } catch (error) {
    setBusy(false);
    setStatus(error && error.message ? error.message : String(error), "error");
  }
});

chrome.runtime.onMessage.addListener((message) => {
  if (!message || message.type !== "chooser_event" || message.tab_id !== tabId) return;
  setBusy(Boolean(message.busy));
  setStatus(message.message || "", message.kind || "");
});

load().catch((error) => {
  setBusy(true);
  setStatus(error && error.message ? error.message : String(error), "error");
});
