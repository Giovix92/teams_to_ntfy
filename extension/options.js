const DEFAULTS = {
  ntfyUrl: "https://ntfy.sh/giovix92_teams_lutech",
  title: "Lutech - Teams",
  tag: "teams",
  token: "",
  enabled: true,
  domFallback: true,
  dedupTtl: 60,
};

const $ = (id) => document.getElementById(id);
const statusEl = $("status");

let statusTimer = null;

function setStatus(text, kind = "") {
  statusEl.textContent = text;
  statusEl.className = kind;
  clearTimeout(statusTimer);
  if (text) statusTimer = setTimeout(() => setStatus(""), 4000);
}

function load() {
  chrome.storage.local.get(DEFAULTS, (cfg) => {
    $("ntfyUrl").value = cfg.ntfyUrl;
    $("title").value = cfg.title;
    $("tag").value = cfg.tag;
    $("token").value = cfg.token;
    $("dedupTtl").value = cfg.dedupTtl;
    $("enabled").checked = cfg.enabled;
    $("domFallback").checked = cfg.domFallback;
  });
}

// Self-hosted ntfy servers are not covered by the manifest's host_permissions,
// so ask for access on demand before the first publish attempt.
function ensureHostPermission(rawUrl) {
  return new Promise((resolve, reject) => {
    let origin;
    try {
      origin = `${new URL(rawUrl).origin}/*`;
    } catch (_) {
      reject(new Error("That is not a valid URL."));
      return;
    }
    chrome.permissions.contains({ origins: [origin] }, (has) => {
      if (has) {
        resolve();
        return;
      }
      chrome.permissions.request({ origins: [origin] }, (granted) => {
        if (granted) resolve();
        else reject(new Error(`Permission to reach ${origin} was denied.`));
      });
    });
  });
}

function readForm() {
  const rawUrl = $("ntfyUrl").value.trim();
  let url;
  try {
    url = new URL(rawUrl);
  } catch (_) {
    throw new Error("Enter a valid ntfy topic URL.");
  }
  if (!url.pathname.split("/").filter(Boolean).length) {
    throw new Error("The URL must include a topic, e.g. https://ntfy.sh/my_topic");
  }
  return {
    ntfyUrl: rawUrl,
    title: $("title").value.trim() || "Teams",
    tag: $("tag").value.trim(),
    token: $("token").value.trim(),
    dedupTtl: Math.max(0, Number($("dedupTtl").value) || 0),
    enabled: $("enabled").checked,
    domFallback: $("domFallback").checked,
  };
}

async function save() {
  let cfg;
  try {
    cfg = readForm();
  } catch (e) {
    setStatus(e.message, "err");
    return null;
  }
  try {
    await ensureHostPermission(cfg.ntfyUrl);
  } catch (e) {
    setStatus(e.message, "err");
    return null;
  }
  await chrome.storage.local.set(cfg);
  setStatus("Saved.", "ok");
  return cfg;
}

$("save").addEventListener("click", save);

$("test").addEventListener("click", async () => {
  const cfg = await save();
  if (!cfg) return;
  setStatus("Sending…");
  chrome.runtime.sendMessage({ type: "test" }, (res) => {
    if (chrome.runtime.lastError) {
      setStatus(chrome.runtime.lastError.message, "err");
    } else if (res && res.ok) {
      setStatus("Test notification sent.", "ok");
    } else {
      setStatus(`Failed: ${(res && res.reason) || "unknown error"}`, "err");
    }
  });
});

$("clearLog").addEventListener("click", () => chrome.storage.local.set({ log: [] }));

// ------------------------------------------------------------------- log ---
function renderLog(entries) {
  const box = $("log");
  box.textContent = "";

  if (!entries.length) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "Nothing yet.";
    box.append(p);
    return;
  }

  for (const e of entries) {
    const row = document.createElement("div");
    row.className = `row ${e.status}`;

    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = e.status;

    const text = document.createElement("span");
    text.className = "text";
    text.textContent = e.status === "error"
      ? `[${e.sender}] ${e.message} — ${e.error}`
      : `[${e.sender}] ${e.message}`;

    const time = document.createElement("span");
    time.className = "time";
    time.textContent = new Date(e.at).toLocaleTimeString();

    row.append(badge, text, time);
    box.append(row);
  }
}

function refreshLog() {
  chrome.storage.local.get({ log: [] }, ({ log }) => renderLog(log));
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.log) renderLog(changes.log.newValue || []);
});

load();
refreshLog();
