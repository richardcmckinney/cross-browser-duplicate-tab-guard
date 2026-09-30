(() => {
  "use strict";

  const HOST_ID = "__cross_browser_duplicate_tab_guard__";
  const BROWSER_LABELS = Object.freeze({
    brave: "Brave",
    chrome: "Chrome",
    chromium: "Chromium",
  });

  let state = null;
  let host = null;
  let shadow = null;

  function label(browser) {
    return BROWSER_LABELS[browser] || "Current browser";
  }

  function removeDialog() {
    if (host && host.isConnected) host.remove();
    host = null;
    shadow = null;
    state = null;
  }

  function setStatus(message, kind = "info") {
    if (!shadow) return;
    const status = shadow.querySelector("[data-role='status']");
    if (!status) return;
    status.textContent = String(message || "");
    status.dataset.kind = kind;
  }

  function setBusy(busy) {
    if (!shadow) return;
    const button = shadow.querySelector("button[data-role='confirm']");
    const select = shadow.querySelector("select[data-role='choice']");
    if (button) button.disabled = Boolean(busy);
    if (select) select.disabled = Boolean(busy);
  }

  function render(payload) {
    removeDialog();
    state = payload;

    host = document.createElement("div");
    host.id = HOST_ID;
    host.setAttribute("data-extension-owned", "true");
    host.style.setProperty("all", "initial", "important");
    host.style.setProperty("position", "fixed", "important");
    host.style.setProperty("inset", "0", "important");
    host.style.setProperty("z-index", "2147483647", "important");

    shadow = host.attachShadow({ mode: "closed" });
    const style = document.createElement("style");
    style.textContent = `
      :host { all: initial; }
      *, *::before, *::after { box-sizing: border-box; }
      .backdrop {
        position: fixed;
        inset: 0;
        display: grid;
        place-items: center;
        padding: 24px;
        background: rgba(9, 12, 18, 0.56);
        backdrop-filter: blur(8px);
        font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        color: #171b24;
      }
      .dialog {
        width: min(560px, 100%);
        border: 1px solid rgba(23, 27, 36, 0.12);
        border-radius: 18px;
        background: #ffffff;
        box-shadow: 0 28px 90px rgba(0, 0, 0, 0.28);
        overflow: hidden;
      }
      .header { padding: 24px 24px 15px; }
      .eyebrow {
        margin: 0 0 8px;
        font-size: 11px;
        font-weight: 750;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: #5f6878;
      }
      h1 {
        margin: 0;
        font-size: 22px;
        line-height: 1.25;
        font-weight: 760;
        letter-spacing: -0.02em;
      }
      .body { padding: 0 24px 24px; }
      p { margin: 0; font-size: 14px; line-height: 1.55; color: #4b5565; }
      .url {
        margin: 16px 0;
        padding: 12px 13px;
        border: 1px solid #e1e5eb;
        border-radius: 10px;
        background: #f7f8fa;
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        font-size: 12px;
        line-height: 1.45;
        color: #252b36;
        overflow-wrap: anywhere;
        max-height: 112px;
        overflow: auto;
      }
      label {
        display: block;
        margin: 0 0 7px;
        font-size: 12px;
        font-weight: 700;
        color: #343b48;
      }
      select {
        width: 100%;
        min-height: 43px;
        border: 1px solid #cfd5dd;
        border-radius: 10px;
        padding: 9px 38px 9px 12px;
        background: #fff;
        color: #1c222d;
        font: inherit;
        font-size: 14px;
        outline: none;
      }
      select:focus { border-color: #5065e8; box-shadow: 0 0 0 3px rgba(80, 101, 232, 0.15); }
      .meta { margin-top: 10px; font-size: 12px; color: #697386; }
      .status {
        min-height: 20px;
        margin-top: 13px;
        font-size: 12px;
        line-height: 1.45;
        color: #5a6474;
      }
      .status[data-kind="error"] { color: #b42318; }
      .status[data-kind="success"] { color: #027a48; }
      .actions {
        display: flex;
        justify-content: flex-end;
        gap: 10px;
        padding: 16px 24px;
        border-top: 1px solid #e7e9ed;
        background: #fafbfc;
      }
      button {
        appearance: none;
        min-height: 42px;
        border: 0;
        border-radius: 10px;
        padding: 10px 16px;
        background: #202633;
        color: #fff;
        font: inherit;
        font-size: 14px;
        font-weight: 700;
        cursor: pointer;
      }
      button:hover:not(:disabled) { background: #0f131b; }
      button:focus-visible { outline: 3px solid rgba(80, 101, 232, 0.25); outline-offset: 2px; }
      button:disabled, select:disabled { opacity: 0.58; cursor: wait; }
      .existing-banner {
        margin: 14px 0 0;
        padding: 12px 14px;
        border: 1px solid #d0d7de;
        border-radius: 12px;
        background: #f6f8fa;
      }
      .banner-header {
        font-size: 11px;
        font-weight: 750;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #57606a;
        margin-bottom: 6px;
      }
      .badge-list {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin-bottom: 6px;
      }
      .browser-pill {
        display: inline-flex;
        align-items: center;
        padding: 3px 9px;
        border-radius: 999px;
        font-size: 12px;
        font-weight: 750;
        line-height: 1.25;
      }
      .browser-pill.brave { background: #ffeae5; color: #b42318; border: 1px solid #fecdca; }
      .browser-pill.chrome { background: #e8f1ff; color: #175cd3; border: 1px solid #b2ddff; }
      .browser-pill.chromium { background: #e0f2fe; color: #026aa2; border: 1px solid #b9e6fe; }
      .browser-pill.other { background: #eef1f5; color: #344054; border: 1px solid #d0d5dd; }
      .banner-summary {
        margin: 0;
        font-size: 13px;
        font-weight: 650;
        line-height: 1.45;
        color: #1f242e;
      }
      .copies-list {
        margin-top: 8px;
        padding: 8px 10px;
        background: #f0f2f5;
        border-radius: 8px;
        font-size: 11px;
        line-height: 1.5;
        color: #475467;
        max-height: 85px;
        overflow: auto;
      }
      .copy-item {
        margin-top: 2px;
        overflow-wrap: anywhere;
      }
      .copy-item:first-child { margin-top: 0; }
      @media (prefers-color-scheme: dark) {
        .dialog { background: #171a20; border-color: #343943; color: #f5f7fa; }
        p, .meta, .status { color: #aeb7c5; }
        .eyebrow { color: #9ca6b5; }
        .url { background: #20242c; border-color: #343a45; color: #edf1f7; }
        label { color: #d9dee7; }
        select { background: #20242c; border-color: #3d4450; color: #f4f6f8; }
        .actions { background: #14171c; border-color: #303640; }
        button { background: #eef1f5; color: #161a21; }
        button:hover:not(:disabled) { background: #ffffff; }
        .existing-banner { background: #1c2128; border-color: #30363d; }
        .banner-header { color: #8b949e; }
        .browser-pill.brave { background: #3c1618; color: #f97066; border-color: #7a271a; }
        .browser-pill.chrome { background: #102a4c; color: #84caff; border-color: #1849a9; }
        .browser-pill.chromium { background: #082f49; color: #7dd3fc; border-color: #075985; }
        .browser-pill.other { background: #21262d; color: #c9d1d9; border-color: #30363d; }
        .banner-summary { color: #f0f6fc; }
        .copies-list { background: #20242c; color: #9da7b4; }
      }
    `;

    const backdrop = document.createElement("div");
    backdrop.className = "backdrop";
    backdrop.setAttribute("role", "presentation");

    const dialog = document.createElement("section");
    dialog.className = "dialog";
    dialog.setAttribute("role", "alertdialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "cbdtg-title");
    dialog.setAttribute("aria-describedby", "cbdtg-description");

    const header = document.createElement("div");
    header.className = "header";
    const eyebrow = document.createElement("p");
    eyebrow.className = "eyebrow";
    const existingLabel = payload.primary_existing_label || (payload.existing_browser_labels && payload.existing_browser_labels[0]) || "";
    eyebrow.textContent = existingLabel ? `Duplicate tab detected • Pre-existing in ${existingLabel}` : "Duplicate tab detected";
    const title = document.createElement("h1");
    title.id = "cbdtg-title";
    title.textContent = payload.existing_summary || "This exact URL is already open";
    header.append(eyebrow, title);

    if (payload.existing_summary) {
      const banner = document.createElement("div");
      banner.className = "existing-banner";
      const bannerHeader = document.createElement("div");
      bannerHeader.className = "banner-header";
      bannerHeader.textContent = "Pre-existing tab location";
      const badgeList = document.createElement("div");
      badgeList.className = "badge-list";
      const bLabels = payload.existing_browser_labels && payload.existing_browser_labels.length
        ? payload.existing_browser_labels
        : (payload.existing_browsers || []).map((b) => label(b));
      for (const bName of (bLabels.length ? bLabels : [label(payload.primary_existing_browser || "chromium")])) {
        const pill = document.createElement("span");
        pill.className = `browser-pill ${bName.toLowerCase()}`;
        pill.textContent = bName;
        badgeList.append(pill);
      }
      const summary = document.createElement("p");
      summary.className = "banner-summary";
      summary.textContent = payload.existing_summary;
      banner.append(bannerHeader, badgeList, summary);
      header.append(banner);
    }

    const body = document.createElement("div");
    body.className = "body";
    const description = document.createElement("p");
    description.id = "cbdtg-description";
    if (payload.existing_summary) {
      description.textContent = `${payload.existing_summary}. Choose which browser should retain the single live copy. Every other exact-URL copy will be closed.`;
    } else {
      description.textContent = "Choose which browser should retain the single live copy. Every other exact-URL copy will be closed.";
    }
    const url = document.createElement("div");
    url.className = "url";
    url.textContent = String(payload.url || location.href);

    const labelEl = document.createElement("label");
    labelEl.htmlFor = "cbdtg-choice";
    labelEl.textContent = "Browser to retain";
    const select = document.createElement("select");
    select.id = "cbdtg-choice";
    select.dataset.role = "choice";

    const baseOptions = [
      ["current", `Current browser — ${label(payload.current_browser)} (this tab)`],
      ["brave", "Brave"],
      ["chrome", "Chrome"],
      ["chromium", "Chromium"],
    ];
    const existingCopies = Array.isArray(payload.existing_copies) ? payload.existing_copies : [];
    const hasSameBrowserExisting = existingCopies.some((c) => c.is_same_browser);

    for (const [value, baseText] of baseOptions) {
      const option = document.createElement("option");
      option.value = value;
      let text = baseText;
      if (value === "current") {
        text = `Current browser — ${label(payload.current_browser)} (this tab${hasSameBrowserExisting ? "; pre-existing copy also open here" : ""})`;
      } else {
        const matching = existingCopies.filter((c) => c.browser === value && !c.is_same_browser);
        if (matching.length > 0) {
          const priv = matching.some((c) => c.incognito);
          text = `${baseText} (pre-existing tab${priv ? " — private" : ""})`;
        } else if (value === payload.current_browser) {
          if (hasSameBrowserExisting) {
            text = `${baseText} (pre-existing copy in another window)`;
          }
        } else {
          text = `${baseText} (transfer tab here)`;
        }
      }
      option.textContent = text;
      select.append(option);
    }

    const count = Number(payload.copy_count || 2);
    const privateCount = Number(payload.private_copy_count || 0);
    const meta = document.createElement("div");
    meta.className = "meta";
    let metaText = `${count} live copies found${privateCount ? `; ${privateCount} in private/incognito context` : ""}.`;
    if (payload.existing_summary) {
      metaText += ` • ${payload.existing_summary}`;
    }
    meta.textContent = metaText;

    if (existingCopies.length > 0) {
      const copiesList = document.createElement("div");
      copiesList.className = "copies-list";
      for (const copy of existingCopies) {
        const item = document.createElement("div");
        item.className = "copy-item";
        const bLabel = copy.browser_label || label(copy.browser);
        const winInfo = copy.window_id > 0 ? ` Window ${copy.window_id}` : "";
        const privInfo = copy.incognito ? " (Private)" : "";
        const sameInfo = copy.is_same_browser ? " [same browser]" : " [pre-existing]";
        const titleInfo = copy.title ? ` • "${copy.title}"` : "";
        item.textContent = `• ${bLabel}${sameInfo}${winInfo}${privInfo}${titleInfo}`;
        copiesList.append(item);
      }
      meta.append(copiesList);
    }
    const status = document.createElement("div");
    status.className = "status";
    status.dataset.role = "status";
    status.setAttribute("aria-live", "polite");

    body.append(description, url, labelEl, select, meta, status);

    const actions = document.createElement("div");
    actions.className = "actions";
    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.dataset.role = "confirm";
    confirm.textContent = "Keep selected copy";
    confirm.addEventListener("click", async () => {
      setBusy(true);
      setStatus("Applying your selection…");
      try {
        const activeIncident = state;
        if (!activeIncident || !activeIncident.incident_id || String(activeIncident.url || location.href) !== location.href) {
          throw new Error("This duplicate alert is no longer active.");
        }
        const response = await chrome.runtime.sendMessage({
          type: "resolve_duplicate",
          incident_id: activeIncident.incident_id,
          choice: select.value,
        });
        if (!response || !response.ok) {
          throw new Error((response && response.error) || "The duplicate could not be resolved.");
        }
        if (response.pending) {
          setStatus(response.message || "Opening the selected browser…", "info");
        } else {
          setStatus("Selection applied.", "success");
        }
      } catch (error) {
        setBusy(false);
        setStatus(error && error.message ? error.message : String(error), "error");
      }
    });
    actions.append(confirm);

    dialog.append(header, body, actions);
    backdrop.append(dialog);
    shadow.append(style, backdrop);

    const root = document.documentElement || document.body;
    if (root) {
      root.append(host);
    } else {
      const pendingHost = host;
      document.addEventListener("DOMContentLoaded", () => {
        const destination = document.documentElement || document.body;
        if (destination && pendingHost === host && !pendingHost.isConnected) destination.append(pendingHost);
      }, { once: true });
    }

    queueMicrotask(() => {
      if (select.isConnected) select.focus();
    });
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (!message || typeof message !== "object") return;
    if (message.type === "show_duplicate_dialog") {
      const payload = message.payload || {};
      if (payload.url && String(payload.url) !== location.href) return;
      render(payload);
      return;
    }
    if (message.type === "duplicate_status") {
      setBusy(Boolean(message.busy));
      setStatus(message.message || "", message.kind || "info");
      return;
    }
    if (message.type === "dismiss_duplicate_dialog") {
      removeDialog();
    }
  });
})();
