import os
import json
import re
import sqlite3
from datetime import datetime

from .config import SESSIONS_DIR
from .utils import TokenTracker
from .logging_config import logger


# ─── Singleton DB connection ────────────────────────────────────────────────────

_db_connection: sqlite3.Connection | None = None
_db_path_cached: str | None = None


def _get_db() -> sqlite3.Connection:
    """Return a singleton SQLite connection, creating the schema if needed.
    
    Automatically reconnects if SESSIONS_DIR has changed (e.g. during testing).
    """
    global _db_connection, _db_path_cached

    os.makedirs(SESSIONS_DIR, exist_ok=True)
    db_path = os.path.join(SESSIONS_DIR, "aizen.db")

    # Reconnect if the path changed (supports monkeypatch in tests)
    if _db_connection is not None and _db_path_cached == db_path:
        return _db_connection

    if _db_connection is not None:
        try:
            _db_connection.close()
        except Exception:
            pass

    _db_connection = sqlite3.connect(db_path)
    _db_path_cached = db_path
    _db_connection.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            name TEXT PRIMARY KEY,
            saved_at TEXT,
            message_count INTEGER,
            messages TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER
        )
    ''')
    _db_connection.commit()
    return _db_connection


def _migrate_legacy_sessions():
    """Migrate old .json files into the SQLite DB once."""
    if not os.path.exists(SESSIONS_DIR):
        return
        
    conn = _get_db()
    migrated_any = False
    for f in os.listdir(SESSIONS_DIR):
        if f.endswith(".json"):
            filepath = os.path.join(SESSIONS_DIR, f)
            try:
                with open(filepath, "r") as fh:
                    data = json.load(fh)
                    name = data.get("name", f[:-5])
                    msgs = data.get("messages", [])
                    saved_at = data.get("saved_at", datetime.now().isoformat())
                    
                    conn.execute(
                        "INSERT OR IGNORE INTO sessions (name, saved_at, message_count, messages, input_tokens, output_tokens) VALUES (?, ?, ?, ?, ?, ?)",
                        (name, saved_at, len(msgs), json.dumps(msgs), 0, 0)
                    )
                # Mark as migrated
                os.rename(filepath, filepath + ".migrated")
                migrated_any = True
            except Exception as e:
                logger.debug("Failed to migrate session file %s: %s", filepath, e)
                
    if migrated_any:
        conn.commit()

# Run migration on import
_migrate_legacy_sessions()

def save_session(
    messages: list, name: str | None = None, token_tracker: TokenTracker | None = None
) -> str:
    if not name:
        name = datetime.now().strftime("session_%Y%m%d_%H%M%S")

    # Sanitize
    name = re.sub(r"[^\w\-]", "_", name)

    input_toks = token_tracker.input_tokens if token_tracker else 0
    output_toks = token_tracker.output_tokens if token_tracker else 0
    saved_at = datetime.now().isoformat()
    
    conn = _get_db()
    conn.execute(
        "REPLACE INTO sessions (name, saved_at, message_count, messages, input_tokens, output_tokens) VALUES (?, ?, ?, ?, ?, ?)",
        (name, saved_at, len(messages), json.dumps(messages), input_toks, output_toks)
    )
    conn.commit()
    return f"sqlite://{name}"


def load_session(name: str) -> list | None:
    # If the user passed the legacy filename by accident
    if name.endswith(".json"):
        name = name[:-5]
        
    conn = _get_db()
    cur = conn.execute("SELECT messages FROM sessions WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None
    return None


def list_sessions() -> list:
    if not os.path.exists(SESSIONS_DIR):
        return []
        
    conn = _get_db()
    cur = conn.execute("SELECT name, saved_at, message_count FROM sessions ORDER BY saved_at DESC")
    sessions = []
    for row in cur.fetchall():
        sessions.append({
            "name": row[0],
            "saved_at": row[1],
            "messages": row[2]
        })
    return sessions
