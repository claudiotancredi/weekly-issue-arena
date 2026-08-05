"""Opt-in preferences for arena participation.

The arena is consent-first: a contributor exists in the arena only after
they submit the *Join the Arena* issue form. This module owns
``.arena_state/preferences.json``, the single source of truth for who
opted in and what they agreed to.

Schema::

    {
      "version": 1,
      "updated_at": "2026-08-05T10:00:00+00:00",
      "users": {
        "octocat": {
          "leaderboard": true,
          "discussion": true,
          "notifications_ack": true,
          "joined_at": "2026-08-05T10:00:00+00:00",
          "updated_at": "2026-08-05T10:00:00+00:00",
          "source_issue": 42
        }
      },
      "processed_issues": [42]
    }

``leaderboard`` is required by the form, so every entry in ``users`` is a
consenting participant. ``discussion`` is opt-in on top: a member with
``discussion=false`` earns points but is never mentioned anywhere in
discussions and receives no notifications.

Lookups are case-insensitive because GitHub logins are, while the stored
key preserves the login's canonical casing for display.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from utils import atomic_write_json

log = logging.getLogger(__name__)

PREFERENCES_PATH = Path(".arena_state/preferences.json")

SCHEMA_VERSION = 1


def empty_preferences() -> dict:
    """Return a fresh, empty preferences document."""
    return {"version": SCHEMA_VERSION, "users": {}, "processed_issues": []}


def load_preferences(path: Path = PREFERENCES_PATH) -> dict:
    """Load preferences, returning an empty document when absent or broken.

    A missing or unreadable file means "nobody has opted in", which is the
    safe default: no file, no participants, no notifications.
    """
    if not Path(path).exists():
        return empty_preferences()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(f"Could not read {path}: {exc} — treating as empty.")
        return empty_preferences()

    if not isinstance(data, dict):
        log.warning(f"{path} is not an object — treating as empty.")
        return empty_preferences()

    data.setdefault("version", SCHEMA_VERSION)
    if not isinstance(data.get("users"), dict):
        data["users"] = {}
    if not isinstance(data.get("processed_issues"), list):
        data["processed_issues"] = []
    return data


def save_preferences(prefs: dict, path: Path = PREFERENCES_PATH) -> None:
    """Persist preferences atomically, stamping ``updated_at``."""
    prefs["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(path, prefs)


def _norm(username: str) -> str:
    return (username or "").strip().lower()


def find_user(prefs: dict, username: str) -> tuple[str, dict] | None:
    """Return ``(stored_key, entry)`` for *username*, case-insensitively."""
    target = _norm(username)
    if not target:
        return None
    for key, entry in prefs.get("users", {}).items():
        if _norm(key) == target and isinstance(entry, dict):
            return key, entry
    return None


def get_user(prefs: dict, username: str) -> dict | None:
    """Return the stored preference entry for *username*, or ``None``."""
    found = find_user(prefs, username)
    return found[1] if found else None


def is_member(prefs: dict, username: str) -> bool:
    """Return True if *username* opted into the arena."""
    return get_user(prefs, username) is not None


def wants_leaderboard(prefs: dict, username: str) -> bool:
    """Return True if *username* consented to public listing."""
    entry = get_user(prefs, username)
    return bool(entry and entry.get("leaderboard"))


def wants_discussion(prefs: dict, username: str) -> bool:
    """Return True if *username* asked for a personal Discussion thread."""
    entry = get_user(prefs, username)
    return bool(entry and entry.get("discussion"))


def member_usernames(prefs: dict) -> set[str]:
    """Return the canonical logins of every opted-in participant."""
    return {
        key
        for key, entry in prefs.get("users", {}).items()
        if isinstance(entry, dict)
    }


def upsert_user(
    prefs: dict,
    username: str,
    *,
    leaderboard: bool,
    discussion: bool,
    notifications_ack: bool,
    source_issue: int | None = None,
) -> dict:
    """Create or update a participant entry, returning the stored entry.

    ``joined_at`` is preserved across updates so re-submitting the form to
    tweak a choice does not look like a brand-new join. The stored key is
    rewritten to the login casing of the latest submission.
    """
    now = datetime.now(timezone.utc).isoformat()
    found = find_user(prefs, username)

    joined_at = now
    if found:
        old_key, old_entry = found
        joined_at = old_entry.get("joined_at", now)
        prefs["users"].pop(old_key, None)

    entry = {
        "leaderboard": bool(leaderboard),
        "discussion": bool(discussion),
        "notifications_ack": bool(notifications_ack),
        "joined_at": joined_at,
        "updated_at": now,
    }
    if source_issue is not None:
        entry["source_issue"] = source_issue

    prefs.setdefault("users", {})[username] = entry
    return entry


def remove_user(prefs: dict, username: str) -> bool:
    """Delete a participant entry. Returns True if one was removed."""
    found = find_user(prefs, username)
    if not found:
        return False
    prefs["users"].pop(found[0], None)
    return True


def is_issue_processed(prefs: dict, issue_number: int) -> bool:
    """Return True if this consent-form issue was already applied."""
    return issue_number in prefs.get("processed_issues", [])


def mark_issue_processed(prefs: dict, issue_number: int) -> None:
    """Record that a consent-form issue has been applied."""
    processed = prefs.setdefault("processed_issues", [])
    if issue_number not in processed:
        processed.append(issue_number)
