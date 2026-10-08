// Runs in the ISOLATED world. Two jobs:
//   1. Relay the MAIN-world Notification API hook to the service worker.
//   2. Fallback: watch the DOM for Teams' own in-app toasts, which is what
//      Teams renders when "Notification style" is set to "Teams built-in"
//      (in that mode no browser Notification is ever constructed).
(() => {
  "use strict";

  const seenNodes = new WeakSet();

  function send(payload) {
    try {
      chrome.runtime.sendMessage({ type: "teams-notification", payload }, () => {
        // Swallow "Extension context invalidated" / no-receiver errors.
        void chrome.runtime.lastError;
      });
    } catch (_) {
      // Extension was reloaded while the page stayed open.
    }
  }

  // ---- 1. Notification API hook relay ---------------------------------------
  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const d = event.data;
    if (!d || d.__teamsToNtfy !== true) return;

    const title = (d.title || "").trim();
    const body = (d.body || "").trim();
    if (!title && !body) return;

    send({
      source: d.source || "Notification",
      sender: title || "Teams",
      message: body || title,
    });
  });

  // ---- 2. In-app toast fallback ---------------------------------------------
  const TOAST_SELECTORS = [
    '[data-tid="toast-container"]',
    '[data-tid="toast-notification"]',
    '[data-tid="notification-toast"]',
    '[class*="toastContainer"]',
    ".fui-Toast",
  ];

  function extract(node) {
    const raw = (node.innerText || node.textContent || "").trim();
    if (!raw) return null;

    const lines = raw
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
      // Drop action-button labels that Teams renders inside the toast.
      .filter((l) => !/^(reply|rispondi|dismiss|ignora|chiudi|close|mark as read|segna come letto)$/i.test(l));

    if (!lines.length) return null;
    if (lines.length === 1) return { sender: "Teams", message: lines[0] };
    return { sender: lines[0], message: lines.slice(1).join(" ") };
  }

  function consider(node) {
    if (!(node instanceof Element) || seenNodes.has(node)) return;
    if (!TOAST_SELECTORS.some((sel) => node.matches(sel))) return;

    seenNodes.add(node);
    // Teams mounts the toast shell first and fills the text a tick later.
    setTimeout(() => {
      const parsed = extract(node);
      if (!parsed || parsed.message.length < 2) return;
      send({ source: "dom", ...parsed });
    }, 350);
  }

  function scan(root) {
    if (!(root instanceof Element)) return;
    consider(root);
    for (const sel of TOAST_SELECTORS) {
      root.querySelectorAll(sel).forEach(consider);
    }
  }

  let observer = null;

  function startObserver() {
    if (observer || !document.body) return;
    observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        m.addedNodes.forEach(scan);
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  function stopObserver() {
    if (!observer) return;
    observer.disconnect();
    observer = null;
  }

  function applySetting() {
    chrome.storage.local.get({ domFallback: true }, (cfg) => {
      if (chrome.runtime.lastError) return;
      if (cfg.domFallback) startObserver();
      else stopObserver();
    });
  }

  if (document.body) applySetting();
  else document.addEventListener("DOMContentLoaded", applySetting, { once: true });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.domFallback) applySetting();
  });
})();
