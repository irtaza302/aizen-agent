import os
import json
import re
from datetime import datetime

from .config import SESSIONS_DIR
from .utils import TokenTracker

def save_session(
    messages: list, name: str | None = None, token_tracker: TokenTracker | None = None
) -> str:
    os.makedirs(SESSIONS_DIR, exist_ok=True)

    if not name:
        name = datetime.now().strftime("session_%Y%m%d_%H%M%S")

    # Sanitize
    name = re.sub(r"[^\w\-]", "_", name)
    filepath = os.path.join(SESSIONS_DIR, f"{name}.json")

    from typing import Any
    session_data: dict[str, Any] = {
        "name": name,
        "saved_at": datetime.now().isoformat(),
        "message_count": len(messages),
        "messages": messages,
    }

    if token_tracker:
        session_data["tokens"] = {
            "input": token_tracker.total_input_tokens,
            "output": token_tracker.total_output_tokens,
        }

    with open(filepath, "w") as f:
        json.dump(session_data, f, indent=2)

    return filepath


def load_session(name: str) -> list | None:
    filepath = os.path.join(SESSIONS_DIR, f"{name}.json")
    if not os.path.exists(filepath):
        filepath = os.path.join(SESSIONS_DIR, name)
        if not os.path.exists(filepath):
            return None

    try:
        with open(filepath, "r") as f:
            data = json.load(f)
        return data.get("messages", [])
    except Exception:
        return None


def list_sessions() -> list:
    if not os.path.exists(SESSIONS_DIR):
        return []

    sessions = []
    for f in sorted(os.listdir(SESSIONS_DIR), reverse=True):
        if f.endswith(".json"):
            try:
                filepath = os.path.join(SESSIONS_DIR, f)
                with open(filepath, "r") as fh:
                    data = json.load(fh)
                sessions.append(
                    {
                        "name": data.get("name", f),
                        "saved_at": data.get("saved_at", "unknown"),
                        "messages": data.get("message_count", 0),
                    }
                )
            except Exception:
                pass
    return sessions
