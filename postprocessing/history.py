"""Session history persistence for VoiceFlow.

Stores transcription sessions in ``history.json`` at the project root.
Follows the same conventions as ``config.py`` (JSON, UTF-8, silent errors).
"""

import json
import os
import uuid
from datetime import datetime
from typing import Dict, List, Optional

_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "history.json"
)

_MAX_SESSIONS = 50


# ---------------------------------------------------------------------------
# Low-level I/O
# ---------------------------------------------------------------------------

def load_history() -> dict:
    """Load history from disk.  Returns ``{"sessions": []}`` on failure."""
    if os.path.isfile(_HISTORY_PATH):
        try:
            with open(_HISTORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "sessions" in data:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"sessions": []}


def save_history(data: dict) -> None:
    """Persist *data* to ``history.json``."""
    try:
        with open(_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def create_session(engine: str = "", language: str = "") -> dict:
    """Return a new in-memory session dict."""
    return {
        "id": str(uuid.uuid4()),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "ended_at": None,
        "engine": engine,
        "language": language,
        "entries": [],
    }


def add_entry(session: dict, raw_text: str) -> dict:
    """Append a transcription entry to *session* and return it."""
    entry: Dict[str, Optional[str]] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "raw_text": raw_text,
        "polished_text": None,
    }
    session["entries"].append(entry)
    return entry


def update_entry_polished(entry: dict, polished_text: str) -> None:
    """Set the polished text on an existing entry."""
    entry["polished_text"] = polished_text


def finalize_session(session: dict) -> None:
    """Mark the session as ended (set *ended_at*)."""
    session["ended_at"] = datetime.now().isoformat(timespec="seconds")


def save_session_to_history(session: dict) -> None:
    """Prepend *session* to the on-disk history (newest first), capped."""
    data = load_history()
    sessions: List[dict] = data.get("sessions", [])
    sessions.insert(0, session)
    data["sessions"] = sessions[:_MAX_SESSIONS]
    save_history(data)


def delete_session(session_id: str) -> None:
    """Remove a single session by *session_id* from disk."""
    data = load_history()
    data["sessions"] = [
        s for s in data.get("sessions", []) if s.get("id") != session_id
    ]
    save_history(data)


def clear_history() -> None:
    """Wipe all saved sessions."""
    save_history({"sessions": []})


def get_session_preview(session: dict, max_len: int = 80) -> str:
    """Return the first *max_len* characters of combined entry text."""
    parts: list[str] = []
    for entry in session.get("entries", []):
        text = entry.get("polished_text") or entry.get("raw_text") or ""
        if text:
            parts.append(text.strip())
    combined = " ".join(parts)
    if len(combined) > max_len:
        return combined[:max_len] + "..."
    return combined
