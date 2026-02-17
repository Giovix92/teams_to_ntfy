# Teams → ntfy Bridge

Forward Microsoft Teams notifications to your phone **without installing Teams on it**.  
Designed for **locked-down corporate environments**.

---

## Overview

In many organizations, Microsoft Teams cannot be installed on personal devices due to device compliance or MDM requirements.

This project provides a **local, no-admin solution** that forwards Teams notifications to your phone using **Windows notifications** and **ntfy**.

**Key properties**
- No admin rights required
- No Microsoft Graph or API tokens
- No Teams mobile app
- No system configuration changes
- Works for you and your colleagues automatically (if you all use the same topic, not suggested)

---

## How It Works

1. Microsoft Teams is used via **Teams Web** (`https://teams.microsoft.com`) in Edge or Chrome
2. Teams generates **Windows toast notifications**
3. Windows stores those notifications locally
4. This script:
   - Reads the notification database (read-only, snapshot-based)
   - Automatically identifies which notifications belong to Teams
   - Extracts sender and message preview
   - Forwards them to **ntfy**

Each machine learns its own Teams notification handler automatically.

---

## Requirements

- Windows 10 or Windows 11
- Python 3.10 or newer
- Teams Web notifications enabled
- Python dependencies:

```bash
python -m pip install --user requests urllib3
```

---

## Quick Start

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

## ntfy Setup (Phone)

1. Install the [ntfy app](https://ntfy.sh) (Android or iOS)
2. Subscribe to the topic you configured above
3. Notifications will appear immediately

---

## Notification Format

Message urgency and appearance are set automatically:

| Condition | Priority | Tag |
|-----------|----------|-----|
| Regular message | default | 💬 (`speech_balloon`) |
| Mention (@, "mentioned", "menzionato") | urgent | 🔔 (`bell`) |

The sender name is prepended to the message body: `[Sender Name] message text`.

---

## Configuration Reference

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

## Automatic Handler Learning

On first execution the script waits for a Teams Web notification, detects the
corresponding Windows notification handler ID, and stores it in:

```
learned_teams_handlers.json
```

All future Teams notifications are forwarded using this learned handler. This
avoids hard-coded IDs and works across different machines and Windows builds.

---

## Handler Blocklist

Some Windows notification handlers produce noise that is never relevant:

- **280** — Chrome echoing your own ntfy notifications back to the desktop
- **384** — Edge favicon badge counter updates

These are listed in `BLOCKLIST_HANDLER_IDS` and are skipped immediately. If you
see other unwanted handler IDs in the log, add them to this set.

---

## Background Execution

The script is safe to run continuously. It:

- Polls the notification database via read-only snapshots
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

## Debugging

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

## Limitations

- **Foreground window suppression**: if the Teams chat is the active foreground
  window, Teams does not fire a Windows toast at all. The message will not be
  forwarded. This is a Teams/Windows behaviour and cannot be worked around from
  this script.
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