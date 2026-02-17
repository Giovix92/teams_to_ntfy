import os
import re
import time
import json
import shutil
import hashlib
import sqlite3
import logging
import atexit
import random
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple
import xml.etree.ElementTree as ET

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ================= CONFIG =================
# Your ntfy topic URL
NTFY_URL = "https://ntfy.sh/YOUR_TOPIC"

# Notification title
TITLE = "YOUR_TITLE"

# Plain-text tag shown below the notification (not an emoji short code)
TAG = "teams"

# Seconds between polling attempts (backs off up to POLL_SECONDS_MAX when quiet)
POLL_SECONDS = 2
POLL_SECONDS_MAX = 10

# Optional Bearer token for private ntfy topics — leave empty for public topics
NTFY_TOKEN = ""

# On startup, skip notifications older than this many seconds to avoid a backlog
# burst being sent to ntfy all at once. Set to 0 to disable.
STARTUP_SKIP_OLDER_THAN = 300  # 5 minutes

# Handler ID blocklist — rows from these handler IDs are skipped immediately
# without even checking their content.
# Add handler IDs here for things that are definitively not Teams messages:
#   - ntfy echo-back toasts (Chrome toasting your own notifications back to you)
#   - Edge badge-counter updates
# The script learns Teams handler IDs automatically; this list is for known noise.
# Populate it from a DEBUG_DUMP run if you see unwanted handler IDs in the log.
BLOCKLIST_HANDLER_IDS: Set[int] = {
    280,   # Chrome — ntfy echo-back toasts
    384,   # Edge badge counter
}

# Teams-related constants
TEAMS_ORIGINS = [
    "https://teams.microsoft.com/",
    "teams.microsoft.com",
    "teams.live.com",
]

# Hints that a notification handler is browser-based (Edge running Teams web)
BROWSER_HINTS = ["microsoftedge", "msedge", "edge", "chrome"]

# File to store learned Teams handler IDs
LEARN_FILE = Path(__file__).with_name("learned_teams_handlers.json")

# How many recent (sender+message) hashes to keep for duplicate suppression
DEDUP_CACHE_SIZE = 200

# How long (seconds) to suppress an identical sender+message pair
DEDUP_TTL = 60

# How many consecutive errors before the script gives up and exits
MAX_CONSECUTIVE_ERRORS = 20

# ntfy documented field length limits
_NTFY_MAX_MESSAGE = 4096
_NTFY_MAX_TITLE = 250

# Logging level — override via LOG_LEVEL env var, e.g. LOG_LEVEL=DEBUG
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
# =========================================

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

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
    raise SystemExit("Configure NTFY_URL and TITLE before running.")


# ---------- clean up temp dir on exit ----------
def _cleanup_tmp() -> None:
    try:
        shutil.rmtree(TMP_DIR, ignore_errors=True)
        log.debug("Cleaned up temp dir on exit.")
    except Exception:
        pass


atexit.register(_cleanup_tmp)


# ---------- requests session ----------
_session: requests.Session | None = None


def _make_session() -> requests.Session:
    s = requests.Session()
    # urllib3-level retry for transient TCP/SSL failures before they reach our
    # application retry loop. Does not auto-retry on any HTTP status codes.
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=[],
        allowed_methods=["POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    if NTFY_TOKEN:
        s.headers.update({"Authorization": f"Bearer {NTFY_TOKEN}"})
    return s


def get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = _make_session()
    return _session


def reset_session() -> None:
    """Close and discard the current session so the next call to get_session()
    opens a completely fresh TCP+TLS connection."""
    global _session
    if _session is not None:
        try:
            _session.close()
        except Exception:
            pass
        _session = None


# ---------- state helpers (atomic writes) ----------
def load_last_id() -> int:
    try:
        return int(STATE_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return 0


def save_last_id(nid: int) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(str(nid), encoding="utf-8")
    tmp.replace(STATE_FILE)


def load_learned_handlers() -> Dict[str, Any]:
    if not LEARN_FILE.exists():
        return {"handler_ids": []}
    try:
        return json.loads(LEARN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"handler_ids": []}


def save_learned_handlers(handler_ids: List[int]) -> None:
    tmp = LEARN_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"handler_ids": sorted(set(handler_ids))}, indent=2),
        encoding="utf-8",
    )
    tmp.replace(LEARN_FILE)


# ---------- deduplication cache ----------
# Maps sha256(sender+message) -> expiry timestamp (monotonic)
_dedup_cache: Dict[str, float] = {}


def _dedup_key(sender: str, message: str) -> str:
    return hashlib.sha256(f"{sender}\x00{message}".encode("utf-8")).hexdigest()


def is_duplicate(sender: str, message: str) -> bool:
    key = _dedup_key(sender, message)
    now = time.monotonic()

    # Evict expired entries
    for k in [k for k, exp in _dedup_cache.items() if exp <= now]:
        del _dedup_cache[k]

    # Trim to size limit (oldest expiry first)
    while len(_dedup_cache) >= DEDUP_CACHE_SIZE:
        del _dedup_cache[min(_dedup_cache, key=lambda k: _dedup_cache[k])]

    if key in _dedup_cache:
        return True

    _dedup_cache[key] = now + DEDUP_TTL
    return False


# ---------- DB helpers ----------
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


# ---------- XML / text helpers ----------
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
        or "menzion" in txt  # Italian locale: "menzionato"
        or re.search(r"\s@\w+", txt) is not None
    )


def priority_for(message: str) -> str:
    return "urgent" if is_mention(message) else "default"


def tags_for(message: str) -> str:
    # Comma-separated Tags header value.
    # ntfy converts recognised short codes to emoji prepended to the title:
    #   "bell"           -> 🔔  (mention)
    #   "speech_balloon" -> 💬  (regular message)
    emoji_tag = "bell" if is_mention(message) else "speech_balloon"
    return f"{TAG},{emoji_tag}"


def pick_sender_and_message(text_nodes: List[str]) -> Tuple[str, str]:
    if not text_nodes:
        return ("Teams", "(no preview)")

    if len(text_nodes) >= 2:
        sender = text_nodes[0].strip()
        msg = " ".join(t.strip() for t in text_nodes[1:]).strip()
        return (sender or "Teams", msg or "(no preview)")

    flat = text_nodes[0].strip()
    return ("Teams", flat or "(no preview)")


# ---------- ntfy sender ----------
def send_ntfy(sender: str, message: str, retries: int = 4) -> None:
    body = f"[{sender}] {message}"[:_NTFY_MAX_MESSAGE].encode("utf-8")
    headers = {
        "Title":    TITLE[:_NTFY_MAX_TITLE],
        "Priority": priority_for(message),
        "Tags":     tags_for(message),
    }

    for attempt in range(retries):
        try:
            r = get_session().post(NTFY_URL, data=body, headers=headers, timeout=10)
            if not r.ok:
                log.error("ntfy returned %d: %s", r.status_code, r.text[:300])
            r.raise_for_status()
            log.info("Sent: [%s] %s", sender, message[:80])
            return

        except requests.exceptions.SSLError:
            # Server dropped the connection (stale keep-alive or rate limiting).
            # Reset so the next attempt gets a fresh socket.
            # NOTE: this is a known ntfy.sh behaviour when requests arrive too
            # quickly. The script will recover automatically on the next attempt.
            reset_session()
            if attempt == retries - 1:
                raise
            wait = (3 ** attempt) + random.uniform(1, 3)
            log.warning(
                "send_ntfy attempt %d SSL error, resetting session — retrying in %.1fs",
                attempt + 1, wait,
            )
            time.sleep(wait)

        except requests.HTTPError:
            if attempt == retries - 1:
                raise
            wait = (2 ** attempt) + random.uniform(0, 1)
            log.warning("send_ntfy attempt %d HTTP error — retrying in %.1fs", attempt + 1, wait)
            time.sleep(wait)

        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = (2 ** attempt) + random.uniform(0, 1)
            log.warning("send_ntfy attempt %d failed: %s — retrying in %.1fs", attempt + 1, e, wait)
            time.sleep(wait)


# ---------- main loop ----------
def main() -> None:
    cleanup_old_snaps(keep=0)

    learned = load_learned_handlers()
    learned_ids: Set[int] = set(
        int(x) for x in learned.get("handler_ids", []) if str(x).isdigit()
    )

    last_id = load_last_id()
    consecutive_errors = 0
    quiet_streak = 0

    startup_cutoff = time.time() - STARTUP_SKIP_OLDER_THAN if STARTUP_SKIP_OLDER_THAN > 0 else 0
    startup_drain = True

    log.info("Starting Teams -> ntfy bridge")
    log.info("ntfy URL: %s", NTFY_URL)
    if startup_cutoff:
        log.info(
            "Startup: skipping notifications older than %ds to avoid backlog burst",
            STARTUP_SKIP_OLDER_THAN,
        )
    log.info(
        "Learned Teams handler IDs: %s",
        sorted(learned_ids) if learned_ids else "(none yet)",
    )
    if BLOCKLIST_HANDLER_IDS:
        log.info("Blocklisted handler IDs (noise): %s", sorted(BLOCKLIST_HANDLER_IDS))
    log.info(
        "Known limitation: if the Teams chat is the active foreground window, "
        "Teams suppresses the Windows toast entirely and the message won't be forwarded."
    )

    while True:
        try:
            snap = copy_db_snapshot()
            con = sqlite3.connect(str(snap))
            cur = con.cursor()

            handler_map = build_handler_map(cur)

            cur.execute(
                """
                SELECT Id, HandlerId, Payload, ArrivalTime
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
                quiet_streak += 1
                sleep_s = min(POLL_SECONDS * (1 + quiet_streak // 5), POLL_SECONDS_MAX)
                time.sleep(sleep_s)
                consecutive_errors = 0
                if startup_drain:
                    startup_drain = False
                    log.info("Startup drain complete — forwarding live notifications.")
                continue

            quiet_streak = 0
            consecutive_errors = 0

            for nid, hid, payload, arrival_time in rows:
                nid = int(nid)
                hid = int(hid)

                # Advance cursor before anything else so a crash mid-batch
                # doesn't replay already-processed rows on the next run
                if nid > last_id:
                    last_id = nid
                    save_last_id(last_id)

                # Skip known-noisy handlers immediately
                if hid in BLOCKLIST_HANDLER_IDS:
                    log.debug("Skipping blocklisted handler %d (id=%d)", hid, nid)
                    continue

                payload_xml = normalize_text(payload)
                meta = handler_map.get(hid, "")

                # Learn new Teams handler IDs
                if (
                    hid not in learned_ids
                    and is_browserish(meta, payload_xml)
                    and looks_like_teams_origin(meta, payload_xml)
                ):
                    learned_ids.add(hid)
                    save_learned_handlers(list(learned_ids))
                    log.info("Learned new Teams handler id: %d | %s", hid, meta[:120])

                if hid not in learned_ids:
                    continue

                # During startup drain, skip notifications older than the cutoff
                if startup_drain and startup_cutoff > 0 and arrival_time:
                    try:
                        # ArrivalTime is Windows FILETIME: 100-ns ticks since 1601-01-01
                        unix_ts = (int(arrival_time) - 116444736000000000) / 10000000
                        if unix_ts < startup_cutoff:
                            log.debug("Startup: skipping stale notification id=%d", nid)
                            continue
                    except Exception:
                        pass  # unparseable timestamp — send it anyway

                text_nodes = extract_text_nodes(payload_xml)
                sender, msg = pick_sender_and_message(text_nodes)

                if not msg or msg == "(no preview)":
                    continue

                if is_duplicate(sender, msg):
                    log.debug("Suppressed duplicate: [%s] %s", sender, msg[:80])
                    continue

                send_ntfy(sender, msg)

            if startup_drain:
                startup_drain = False
                log.info("Startup drain complete — forwarding live notifications.")

            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            log.info("Interrupted by user, exiting.")
            break

        except Exception as e:
            consecutive_errors += 1
            log.error("Error (%d/%d): %s", consecutive_errors, MAX_CONSECUTIVE_ERRORS, e)

            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                log.critical(
                    "Reached %d consecutive errors — giving up. "
                    "Check ntfy connectivity and DB access.",
                    MAX_CONSECUTIVE_ERRORS,
                )
                raise SystemExit(1)

            # Exponential backoff, capped at 30s
            wait = min(2 ** min(consecutive_errors, 5), 30)
            time.sleep(wait)


if __name__ == "__main__":
    main()