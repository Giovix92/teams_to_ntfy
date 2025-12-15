import os
import re
import time
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Tuple
import xml.etree.ElementTree as ET

import requests

# ================= CONFIG =================
# Replace YOUR_TOPIC with your actual ntfy topic
NTFY_URL = "https://ntfy.sh/YOUR_TOPIC"

# Replace your title
TITLE = "YOUR_TITLE"

# Set your custom tag, or leave it to "teams"
TAG = "teams"

# Seconds between polling attempts
POLL_SECONDS = 2

# Teams-related constants
TEAMS_ORIGINS = [
    "https://teams.microsoft.com/",
    "teams.microsoft.com",
    "teams.live.com",
]

# Hints that a notification handler is browser-based
BROWSER_HINTS = ["microsoftedge", "msedge", "edge", "chrome"]

# File to store learned Teams handler IDs
LEARN_FILE = Path(__file__).with_name("learned_teams_handlers.json")

# Debugging flags
DEBUG_PRINT_LEARNING = False
DEBUG_PRINT_MATCHES = False
# =========================================

STATE_FILE = Path(__file__).with_name("toast_state.txt")

DB_DIR = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Notifications"
DB_MAIN = DB_DIR / "wpndatabase.db"
DB_WAL = DB_DIR / "wpndatabase.db-wal"
DB_SHM = DB_DIR / "wpndatabase.db-shm"

TMP_DIR = Path(os.environ["TEMP"]) / "toast_ntfy_tmp"
TMP_DIR.mkdir(parents=True, exist_ok=True)

XML_TAG_RE = re.compile(r"<[^>]+>")


# ---------- startup validation ----------
if "YOUR_TOPIC" in NTFY_URL or TITLE == "YOUR_TITLE":
    raise SystemExit("❌ Configure NTFY_URL and TITLE before running.")


def load_last_id() -> int:
    try:
        return int(STATE_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return 0


def save_last_id(nid: int) -> None:
    STATE_FILE.write_text(str(nid), encoding="utf-8")


def load_learned_handlers() -> Dict[str, Any]:
    if not LEARN_FILE.exists():
        return {"handler_ids": []}
    try:
        return json.loads(LEARN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"handler_ids": []}


def save_learned_handlers(handler_ids: List[int]) -> None:
    LEARN_FILE.write_text(
        json.dumps({"handler_ids": sorted(set(handler_ids))}, indent=2),
        encoding="utf-8",
    )


def normalize_text(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x).decode("utf-8", errors="replace")
    return str(x)


def safe_copy(src: Path, dst: Path, retries: int = 12, sleep_s: float = 0.08) -> bool:
    for _ in range(retries):
        try:
            shutil.copyfile(src, dst)
            return True
        except (PermissionError, OSError):
            time.sleep(sleep_s)
    return False


def cleanup_old_snaps(keep: int = 20) -> None:
    snaps = sorted(
        TMP_DIR.glob("wpn_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in snaps[keep:]:
        for ext in ["", "-wal", "-shm"]:
            try:
                Path(str(p) + ext).unlink(missing_ok=True)
            except Exception:
                pass


def copy_db_snapshot() -> Path:
    stamp = int(time.time() * 1000)
    snap = TMP_DIR / f"wpn_{stamp}.db"

    if not safe_copy(DB_MAIN, snap):
        raise OSError("Could not snapshot wpndatabase.db (locked too long)")

    if DB_WAL.exists():
        safe_copy(DB_WAL, Path(str(snap) + "-wal"))
    if DB_SHM.exists():
        safe_copy(DB_SHM, Path(str(snap) + "-shm"))

    cleanup_old_snaps()
    return snap


def table_columns(cur: sqlite3.Cursor, table: str) -> List[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return [r[1] for r in cur.fetchall()]


def build_handler_map(cur: sqlite3.Cursor) -> Dict[int, str]:
    try:
        cols = table_columns(cur, "NotificationHandler")
    except sqlite3.Error:
        return {}

    if not cols:
        return {}

    id_col = "Id" if "Id" in cols else cols[0]
    pick = [c for c in cols if c != id_col]

    cur.execute(f"SELECT {id_col}, {', '.join(pick)} FROM NotificationHandler")
    out: Dict[int, str] = {}

    for row in cur.fetchall():
        try:
            hid = int(row[0])
        except Exception:
            continue
        meta = " | ".join(normalize_text(v) for v in row[1:] if normalize_text(v))
        out[hid] = meta

    return out


def extract_text_nodes(payload_xml: str) -> List[str]:
    if not payload_xml:
        return []

    try:
        root = ET.fromstring(payload_xml.strip())
        texts = [
            t.text.strip()
            for t in root.iter()
            if t.tag.lower().endswith("text") and (t.text or "").strip()
        ]
        if texts:
            return texts
    except Exception:
        pass

    flat = XML_TAG_RE.sub(" ", payload_xml)
    flat = re.sub(r"\s+", " ", flat).strip()
    return [flat] if flat else []


def contains_any(hay: str, needles: List[str]) -> bool:
    h = (hay or "").lower()
    return any(n.lower() in h for n in needles)


def is_browserish(meta: str, payload_xml: str) -> bool:
    return contains_any(meta, BROWSER_HINTS) or contains_any(payload_xml, BROWSER_HINTS)


def looks_like_teams_origin(meta: str, payload_xml: str) -> bool:
    blob = (meta or "") + " " + (payload_xml or "")
    return contains_any(blob, TEAMS_ORIGINS)


def is_mention(message: str) -> bool:
    txt = (message or "").lower()
    return (
        " mentioned " in txt
        or " mentioned you" in txt
        or "menzion" in txt
        or re.search(r"\s@\w+", txt) is not None
    )


def emoji_for(message: str) -> str:
    return "🔔" if is_mention(message) else "💬"


def priority_for(message: str) -> str:
    return "urgent" if is_mention(message) else "default"


def pick_sender_and_message(text_nodes: List[str]) -> Tuple[str, str]:
    if not text_nodes:
        return ("Teams", "(no preview)")

    if len(text_nodes) >= 2:
        sender = text_nodes[0].strip()
        msg = " ".join(t.strip() for t in text_nodes[1:]).strip()
        return (sender or "Teams", msg or "(no preview)")

    flat = text_nodes[0].strip()
    return ("Teams", flat or "(no preview)")


def send_ntfy(sender: str, message: str) -> None:
    body = f"{emoji_for(message)} [{sender}] {message}".strip()
    headers = {
        "Title": TITLE,
        "Priority": priority_for(message),
        "Tags": TAG,
    }
    r = requests.post(NTFY_URL, data=body, headers=headers, timeout=10)
    r.raise_for_status()


def main():
    learned = load_learned_handlers()
    learned_ids = set(int(x) for x in learned.get("handler_ids", []) if str(x).isdigit())

    last_id = load_last_id()
    print("[i] Starting Teams → ntfy bridge")
    print(f"[i] ntfy: {NTFY_URL}")
    print(f"[i] Learned Teams handler IDs: {sorted(learned_ids) if learned_ids else '(none yet)'}")

    while True:
        try:
            snap = copy_db_snapshot()
            con = sqlite3.connect(str(snap))
            cur = con.cursor()

            handler_map = build_handler_map(cur)

            cur.execute(
                """
                SELECT Id, HandlerId, Payload
                FROM Notification
                WHERE Id > ?
                ORDER BY Id ASC
                LIMIT 250
                """,
                (last_id,),
            )
            rows = cur.fetchall()
            con.close()

            if not rows:
                time.sleep(POLL_SECONDS)
                continue

            for nid, hid, payload in rows:
                nid = int(nid)
                hid = int(hid)
                last_id = max(last_id, nid)

                payload_xml = normalize_text(payload)
                meta = handler_map.get(hid, "")

                if hid not in learned_ids and is_browserish(meta, payload_xml) and looks_like_teams_origin(meta, payload_xml):
                    learned_ids.add(hid)
                    save_learned_handlers(list(learned_ids))
                    if DEBUG_PRINT_LEARNING:
                        print(f"[learn] Learned Teams handler id {hid}")

                if hid not in learned_ids:
                    continue

                text_nodes = extract_text_nodes(payload_xml)
                sender, msg = pick_sender_and_message(text_nodes)

                if not msg or msg == "(no preview)":
                    continue

                if DEBUG_PRINT_MATCHES:
                    print(f"[send] {sender}: {msg[:120]}")

                send_ntfy(sender, msg)

            save_last_id(last_id)
            time.sleep(POLL_SECONDS)

        except Exception as e:
            print(f"[!] Error: {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()
