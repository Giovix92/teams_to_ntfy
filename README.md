# Teams → ntfy Bridge

Forward Microsoft Teams notifications to your phone **without installing Teams on it**.  
Designed for **locked-down corporate environments**.

---

## Two ways to run it

| | **Browser extension** | **Python script** |
|---|---|---|
| How it works | Hooks the Notification API inside Teams Web | Polls the Windows notification database |
| Misses notifications if a Teams origin isn't recognised | ❌ No — runs inside the page | ⚠️ Only if its handler can't be learned |
| Fires when Teams is the foreground window | ✅ Yes | ❌ No (Windows never raises a toast) |
| Needs a process running | No — lives in the browser | Yes |
| Needs Teams open | Yes | Yes (see below) |
| Setup | Load unpacked in Edge/Chrome | `python teams_to_ntfy.py` |

Both publish to the same ntfy topic, so pick one — running both will double every
message (the extension and the script de-duplicate independently).

### Teams has to be open either way

Teams Web only notifies you while it is running. Unless it has registered a
**Web Push** subscription, closing the tab/PWA means Teams raises no
notification at all — so there is nothing for *either* tool to forward.

You can check whether your browser holds a push subscription for Teams:

```powershell
# Edge
$p = "$env:LOCALAPPDATA\Microsoft\Edge\User Data\Default\EdgePushStorageWithWinRt"
Select-String -Path "$p\*" -Pattern 'teams' -Encoding default
```

No matches means no push subscription, so Teams must stay open regardless of
which option you choose. (Only if Teams *does* use push does the Python script
gain an edge, since push notifications come from the service worker — a context
extensions cannot observe.)

**Recommendation:** since Teams has to stay open anyway, the **extension** is
the better default. It needs no background process, cannot be broken by a Teams
domain migration, and is the only option that still works when the Teams window
is in the foreground.

---

## Option A — Browser extension

The extension wraps `new Notification(...)` and
`ServiceWorkerRegistration.showNotification(...)` inside the Teams Web page
*before* Teams boots, so it sees notifications at the moment they are raised —
no database, no polling, no handler learning. Nothing is intercepted or
blocked: the original call is always forwarded, so your normal desktop toast
still appears.

### Install

1. Open `edge://extensions` (or `chrome://extensions`)
2. Enable **Developer mode**
3. Click **Load unpacked** and select the `extension/` folder of this repo
4. The options page opens automatically — fill in your ntfy topic URL and click
   **Send test notification** to confirm the link works

Settings can be reopened any time by clicking the extension's toolbar icon.

### Supported domains

The content scripts are injected on both the current and the upcoming Teams Web
origins, so the migration to `teams.cloud.microsoft` needs no action:

```
https://teams.microsoft.com/*      https://*.teams.microsoft.com/*
https://teams.cloud.microsoft/*    https://m365.cloud.microsoft/*
https://teams.live.com/*           https://*.teams.live.com/*
```

Unlike the Python script, origins never have to be "learned" — the hook runs
inside the page itself, so a future origin only needs adding to `matches` in
`manifest.json`.

### Does it work in the installed PWA?

**Yes.** Chromium injects content scripts into installed PWA windows exactly as
it does into tabs — a PWA is a normal renderer without the browser chrome. The
Teams PWA runs at `https://teams.microsoft.com/v2/?clientType=pwa`, which the
`matches` patterns cover.

Two PWA-specific notes:

- There is no extension toolbar in a PWA window. Open the settings with
  **Ctrl+Shift+Y**, or from `edge://extensions`.
- **The one real limit:** if Teams ever raises a notification from its *service
  worker* (Web Push), no extension can see it — service workers are an isolated
  context extensions are deliberately forbidden from touching. This only
  applies if Teams holds a push subscription; see
  *[Teams has to be open either way](#teams-has-to-be-open-either-way)*. Without
  one, closing Teams produces no notification at all, so nothing is lost.

In practice Teams raises notifications from the page while the client is
running, which is the path the hook covers. To confirm on your own machine,
leave Teams open, have someone message you, and check the activity log on the
options page — if the message is listed, you are on the intercepted path.

### Settings

| Setting | Default | Description |
|---------|---------|-------------|
| ntfy topic URL | `https://ntfy.sh/giovix92_teams_lutech` | Full URL including the topic. Self-hosted servers prompt for host access on save. |
| Notification title | `Lutech - Teams` | Title shown on your phone |
| Tag | `teams` | Plain-text tag; an emoji tag is added automatically |
| Access token | *(empty)* | Bearer token for protected topics |
| Duplicate suppression | `60` | Seconds to suppress an identical sender+message pair |
| Forward notifications | on | Master on/off switch |
| Watch in-app toasts | on | DOM fallback, see below |

The options page also shows a live log of the last 30 events (sent, duplicate,
error) — useful for confirming notifications are being picked up.

### Teams notification style

Teams Web can render notifications two ways (**Settings → Notifications and
activity → Notification style**):

- **Browser** — Teams calls the Notification API. This is the path the extension
  hooks directly and the most reliable one. **Recommended.**
- **Teams built-in** — Teams draws its own toast in the page and never calls the
  Notification API. The extension falls back to a `MutationObserver` on the
  toast container, enabled by the *Watch in-app toasts* setting. This works but
  depends on Teams' DOM structure, so prefer **Browser**.

### Privacy

The extension talks to exactly two things: the Teams tab it is injected into,
and your ntfy topic. It reads only the title and body of notifications Teams
raises, stores nothing but your settings and a short local activity log, and
has no access to the rest of your browsing.

---

## Option B — Python script

### Overview

In many organizations, Microsoft Teams cannot be installed on personal devices due to device compliance or MDM requirements.

This project provides a **local, no-admin solution** that forwards Teams notifications to your phone using **Windows notifications** and **ntfy**.

**Key properties**
- No admin rights required
- No Microsoft Graph or API tokens
- No Teams mobile app
- No system configuration changes
- Works for you and your colleagues automatically (if you all use the same topic, not suggested)

---

### How It Works

1. Microsoft Teams is used via **Teams Web** (`https://teams.microsoft.com` or the
   newer `https://teams.cloud.microsoft`) in Edge or Chrome
2. Teams generates **Windows toast notifications**
3. Windows stores those notifications locally
4. This script:
   - Reads the notification database (snapshot-based)
   - Automatically identifies which notifications belong to Teams
   - Extracts sender and message preview
   - Forwards them to **ntfy**

Each machine learns its own Teams notification handler automatically.

Each poll copies `wpndatabase.db` and its `-wal` sidecar, but deliberately
**not** the `-shm` shared-memory index: that file belongs to the live writer
process and SQLite's documentation calls copying it unsafe. Without it SQLite
recovers the WAL itself, which is always correct.

---

### Requirements

- Windows 10 or Windows 11
- Python 3.10 or newer
- Teams Web notifications enabled
- Python dependencies:

```bash
python -m pip install --user requests urllib3
```

---

### Quick Start

1. Clone the repository:

```bash
git clone https://github.com/Giovix92/teams_to_ntfy.git
cd teams_to_ntfy
```

2. Create an account on [ntfy.sh](https://ntfy.sh) and choose a topic name.

3. Edit the configuration block at the top of `teams_to_ntfy.py`:

```python
NTFY_URL = "https://ntfy.sh/YOUR_TOPIC"   # your ntfy topic URL
TITLE    = "YOUR_TITLE"                    # notification title on your phone
TAG      = "teams"                         # tag shown below notifications
```

4. Run the script:

```bash
python teams_to_ntfy.py
```

5. Ask someone to send you a message and watch the notification arrive on your phone.

---

### ntfy Setup (Phone)

1. Install the [ntfy app](https://ntfy.sh) (Android or iOS)
2. Subscribe to the topic you configured above
3. Notifications will appear immediately

---

### Notification Format

Message urgency and appearance are set automatically:

| Condition | Priority | Tag |
|-----------|----------|-----|
| Regular message | default | 💬 (`speech_balloon`) |
| Mention (@, "mentioned", "menzionato") | urgent | 🔔 (`bell`) |

The sender name is prepended to the message body: `[Sender Name] message text`.

---

### Configuration Reference

All options are at the top of the script.

| Variable | Default | Description |
|----------|---------|-------------|
| `NTFY_URL` | — | Full ntfy topic URL |
| `TITLE` | — | Notification title |
| `TAG` | `"teams"` | Plain-text tag shown below notification |
| `POLL_SECONDS` | `2` | Base polling interval |
| `POLL_SECONDS_MAX` | `10` | Max polling interval during quiet periods |
| `NTFY_TOKEN` | `""` | Bearer token for private ntfy topics |
| `STARTUP_SKIP_OLDER_THAN` | `300` | Skip notifications older than N seconds on startup (0 = disabled) |
| `BLOCKLIST_HANDLER_IDS` | `{280, 384}` | Handler IDs to ignore immediately (see below) |
| `DEDUP_TTL` | `60` | Seconds to suppress identical sender+message pairs |
| `MAX_CONSECUTIVE_ERRORS` | `20` | Exit after this many unrecovered consecutive errors |
| `LOG_LEVEL` | `"INFO"` | Log verbosity; override via `LOG_LEVEL=DEBUG` env var |

---

### Automatic Handler Learning

On first execution the script waits for a Teams Web notification, detects the
corresponding Windows notification handler ID, and stores it in:

```
learned_teams_handlers.json
```

All future Teams notifications are forwarded using this learned handler. This
avoids hard-coded IDs and works across different machines and Windows builds.

#### One handler per origin — a common cause of dropped messages

Windows creates a **separate handler ID per site origin**. Teams Web on the old
and new domains therefore looks like two different apps:

```
[281] Microsoft.MicrosoftEdge.Stable_…!https://teams.microsoft.com/v2/?clientType=pwa
[498] Microsoft.MicrosoftEdge.Stable_…!https://teams.cloud.microsoft/
```

A handler is only learned if its metadata matches `TEAMS_ORIGINS` (or the
`TEAMS_HOST_RE` catch-all for `teams*` hosts on Microsoft domains). Anything
unrecognised is skipped **and** the cursor advances past it, so those
notifications are lost for good.

This is what made notifications go missing when Microsoft started migrating
Teams to `teams.cloud.microsoft`: with both tabs open, delivery alternated
between a learned handler and an unlearned one. Both domains are now matched.

If a browser handler cannot be recognised, the script logs a warning once:

```
[WARNING] Unrecognised browser notification handler 498 — its notifications are
          being skipped. If this is Teams on a new domain, add that origin to
          TEAMS_ORIGINS. Handler: Microsoft.MicrosoftEdge.Stable_…!https://…
```

To inspect the handlers on your machine, run with `LOG_LEVEL=DEBUG` and watch
for that warning, then add the origin to `TEAMS_ORIGINS`.

---

### Handler Blocklist

Some Windows notification handlers produce noise that is never relevant:

- **280** — Chrome echoing your own ntfy notifications back to the desktop
- **384** — Edge favicon badge counter updates

These are listed in `BLOCKLIST_HANDLER_IDS` and are skipped immediately. If you
see other unwanted handler IDs in the log, add them to this set.

---

### Background Execution

The script is safe to run continuously. It:

- Polls the notification database via snapshots
- Backs off polling automatically during quiet periods (up to 10s)
- Persists state across restarts via `toast_state.txt`
- Cleans up temporary snapshot files on exit
- Exits cleanly after 20 consecutive unrecovered errors

Generated files:

```
toast_state.txt               # last processed notification ID
learned_teams_handlers.json   # learned Teams handler IDs
%TEMP%\toast_ntfy_tmp\        # temporary DB snapshots (auto-cleaned)
```

---

### Debugging

Set the `LOG_LEVEL` environment variable before running:

```bash
# Windows CMD
set LOG_LEVEL=DEBUG
python teams_to_ntfy.py

# PowerShell
$env:LOG_LEVEL = "DEBUG"
python teams_to_ntfy.py
```

Debug output includes every notification sent, duplicates suppressed, and
blocklisted handlers skipped.

---

### Limitations

These apply to the **Python script only** — the browser extension is not
affected by any of them, since it never involves Windows toasts.

- **Foreground window suppression**: if the Teams chat is the active foreground
  window, Teams does not fire a Windows toast at all. The message will not be
  forwarded. This is a Teams/Windows behaviour and cannot be worked around from
  this script.
- **Per-origin handlers**: a Teams origin whose handler is not recognised is
  skipped permanently (see *Automatic Handler Learning* above).
- Requires Teams Web to produce Windows toast notifications (not badge-only)
- Notifications blocked by Focus Assist will not appear in the database

---

## Security and Privacy

- No authentication tokens (unless you configure `NTFY_TOKEN` for a private topic)
- No access to Microsoft APIs
- No message storage — only the preview text visible in the toast is forwarded
- Entirely local and reversible: delete the three generated files to reset

---

## License

MIT License. Just don't blame me if your boss finds out. :)
