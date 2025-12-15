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
   - Forwards them to **ntfy** through a cURL call

Each machine learns its own Teams notification handler automatically.

---

## Requirements

- Windows 10 or Windows 11
- Python 3.9 or newer
- Teams Web notifications enabled
- One Python dependency:

```bash
python -m pip install --user requests
```

---

## Quick Start

1. Clone the repository:

```bash
git clone https://github.com/Giovix92/teams_to_ntfy.git
cd teams_to_ntfy
```

2. Create a new account via ntfy.sh website and create a new topic/argument.

3. Edit the configuration in teams_to_ntfy.py:

```python
NTFY_URL = "https://ntfy.sh/YOUR_TOPIC"
TITLE = "YOUR_TITLE"
TAG = "teams"
```

4. Run the script:

```bash
python teams_to_ntfy.py
```

5. Ask someone to send you a DM/message.

---

## ntfy Setup (Phone)

1. Install the ntfy app (Android or iOS)
2. Subscribe to your chosen topic:
3. Notifications will appear immediately.

---

## Notification Format

The notification content and urgency are automatically determined.

- Messages containing mentions (@, mentioned, menzion*) are sent as high priority
- A visual indicator is added:
    - 💬 normal message
    - 🔔 mention

---

## Automatic Handler Learning

On first execution:

- The script waits for a Teams Web notification
- Detects the corresponding Windows notification handler
- Stores it locally in: `learned_teams_handlers.json`
- All future Teams notifications are forwarded using this learned handler.

This avoids hard-coded IDs and works across different machines and Windows builds.

---

## Debugging

To enable debug output, set:

DEBUG_PRINT_LEARNING = True
DEBUG_PRINT_MATCHES = True

This prints:

- When a Teams handler is detected
- Which notifications are forwarded

---

## Background Execution

The script:

- Cleans up old database snapshots automatically
- Persists the last processed notification
- Is safe to run continuously in the background

Generated files:

- toast_state.txt
- learned_teams_handlers.json
- %TEMP%/toast_ntfy_tmp/

---

## Limitations

- Requires Teams Web to produce Windows notifications
- Does not capture silent or badge-only updates
- Notifications must not be blocked by Focus Assist

---

## Security and Privacy
- No authentication tokens
- No access to Microsoft APIs
- No message storage
- Only message previews are forwarded
- Entirely local and reversible

---

## License

MIT License. Just don't blame me if your boss finds out. :)