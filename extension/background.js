// MV3 service worker: receives intercepted Teams notifications, de-duplicates
// them and publishes them to ntfy.

const DEFAULTS = {
  ntfyUrl: "https://ntfy.sh/giovix92_teams_lutech",
  title: "Lutech - Teams",
  tag: "teams",
  token: "",
  enabled: true,
  domFallback: true,
  dedupTtl: 60,
};

const NTFY_MAX_MESSAGE = 4096;
const NTFY_MAX_TITLE = 250;
const LOG_SIZE = 30;
const SEND_RETRIES = 4;

// ---------------------------------------------------------------- settings --
function getConfig() {
  return new Promise((resolve) => chrome.storage.local.get(DEFAULTS, resolve));
}

// --------------------------------------------------------------- dedupe ----
// Kept in chrome.storage.session so suppression survives a service-worker
// restart, which MV3 does aggressively between notifications.
async function isDuplicate(sender, message, ttlSeconds) {
  const key = `${sender}\u0000${message}`;
  const now = Date.now();
  const ttl = Math.max(0, Number(ttlSeconds) || 0) * 1000;
  if (!ttl) return false;

  const { dedup = {} } = await chrome.storage.session.get({ dedup: {} });

  for (const [k, expiry] of Object.entries(dedup)) {
    if (expiry <= now) delete dedup[k];
  }

  const duplicate = dedup[key] !== undefined;
  if (!duplicate) {
    dedup[key] = now + ttl;
    const entries = Object.entries(dedup);
    if (entries.length > 200) {
      entries.sort((a, b) => a[1] - b[1]);
      for (const [k] of entries.slice(0, entries.length - 200)) delete dedup[k];
    }
  }

  await chrome.storage.session.set({ dedup });
  return duplicate;
}

// ------------------------------------------------------- classification ----
function isMeetingCall(text) {
  return (
    text.includes("ha avviato la riunione") ||
    text.includes("started the meeting") ||
    text.includes("started a call") ||
    text.includes("ha avviato la chiamata") ||
    text.includes("incoming call") ||
    text.includes("chiamata in arrivo")
  );
}

function isMention(text) {
  return (
    text.includes(" mentioned ") ||
    text.includes("mentioned you") ||
    text.includes("menzion") ||
    /@\w+/.test(text)
  );
}

function classify(sender, message) {
  const text = `${sender} ${message}`.toLowerCase();
  if (isMeetingCall(text)) return { priority: 5, emoji: "phone" };
  if (isMention(text)) return { priority: 5, emoji: "bell" };
  return { priority: 3, emoji: "speech_balloon" };
}

// ------------------------------------------------------------ ntfy send ----
// Splits "https://ntfy.sh/mytopic" into a server root + topic so we can use
// ntfy's JSON publishing endpoint. JSON keeps UTF-8 intact; the header-based
// API would mangle non-ASCII titles, since headers are ByteStrings.
function parseNtfyUrl(rawUrl) {
  const url = new URL(rawUrl);
  const parts = url.pathname.split("/").filter(Boolean);
  if (!parts.length) throw new Error(`No topic in ntfy URL: ${rawUrl}`);
  const topic = parts.pop();
  const base = `${url.origin}/${parts.join("/")}${parts.length ? "/" : ""}`;
  return { base, topic };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function publish(cfg, sender, message) {
  const { base, topic } = parseNtfyUrl(cfg.ntfyUrl);
  const { priority, emoji } = classify(sender, message);

  const payload = {
    topic,
    title: String(cfg.title || "Teams").slice(0, NTFY_MAX_TITLE),
    message: `[${sender}] ${message}`.slice(0, NTFY_MAX_MESSAGE),
    priority,
    tags: [cfg.tag, emoji].filter(Boolean),
  };

  const headers = { "Content-Type": "application/json" };
  if (cfg.token) headers.Authorization = `Bearer ${cfg.token}`;

  let lastError;
  for (let attempt = 0; attempt < SEND_RETRIES; attempt++) {
    try {
      const res = await fetch(base, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });
      if (res.ok) return;
      lastError = new Error(`ntfy returned ${res.status}: ${(await res.text()).slice(0, 200)}`);
      // 4xx other than rate limiting will not recover by retrying.
      if (res.status >= 400 && res.status < 500 && res.status !== 429) break;
    } catch (e) {
      lastError = e;
    }
    if (attempt < SEND_RETRIES - 1) {
      await sleep(2 ** attempt * 1000 + Math.random() * 500);
    }
  }
  throw lastError || new Error("ntfy publish failed");
}

// ----------------------------------------------------------------- log -----
async function appendLog(entry) {
  const { log = [] } = await chrome.storage.local.get({ log: [] });
  log.unshift({ ...entry, at: Date.now() });
  await chrome.storage.local.set({ log: log.slice(0, LOG_SIZE) });
}

async function flashBadge(text, color) {
  try {
    await chrome.action.setBadgeBackgroundColor({ color });
    await chrome.action.setBadgeText({ text });
    setTimeout(() => chrome.action.setBadgeText({ text: "" }), 3000);
  } catch (_) {
    // Badge is cosmetic only.
  }
}

// -------------------------------------------------------------- pipeline ---
async function handleNotification(payload) {
  const cfg = await getConfig();
  if (!cfg.enabled) return { ok: false, reason: "disabled" };

  const sender = (payload.sender || "Teams").trim() || "Teams";
  const message = (payload.message || "").trim();
  if (!message) return { ok: false, reason: "empty" };

  if (await isDuplicate(sender, message, cfg.dedupTtl)) {
    await appendLog({ status: "duplicate", sender, message, source: payload.source });
    return { ok: false, reason: "duplicate" };
  }

  try {
    await publish(cfg, sender, message);
    await appendLog({ status: "sent", sender, message, source: payload.source });
    await flashBadge("\u2191", "#2e7d32");
    return { ok: true };
  } catch (e) {
    await appendLog({
      status: "error",
      sender,
      message,
      source: payload.source,
      error: String(e && e.message ? e.message : e),
    });
    await flashBadge("!", "#c62828");
    return { ok: false, reason: String(e) };
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg) return false;

  if (msg.type === "teams-notification") {
    handleNotification(msg.payload).then(sendResponse);
    return true;
  }

  if (msg.type === "test") {
    getConfig()
      .then((cfg) => publish(cfg, "Teams \u2192 ntfy", "Test notification \u2014 the bridge is working."))
      .then(() => sendResponse({ ok: true }))
      .catch((e) => sendResponse({ ok: false, reason: String(e && e.message ? e.message : e) }));
    return true;
  }

  return false;
});

chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());

// PWA windows have no extension toolbar, so expose a keyboard shortcut too.
if (chrome.commands) {
  chrome.commands.onCommand.addListener((command) => {
    if (command === "open-settings") chrome.runtime.openOptionsPage();
  });
}

chrome.runtime.onInstalled.addListener(async (details) => {
  const current = await chrome.storage.local.get(Object.keys(DEFAULTS));
  const missing = {};
  for (const [k, v] of Object.entries(DEFAULTS)) {
    if (current[k] === undefined) missing[k] = v;
  }
  if (Object.keys(missing).length) await chrome.storage.local.set(missing);
  if (details.reason === "install") chrome.runtime.openOptionsPage();
});
