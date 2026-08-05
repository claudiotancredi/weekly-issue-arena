"""Tests for scripts/preferences.py."""

import json
import sys
from pathlib import Path

sys.path.insert(0, "scripts")

from preferences import (  # noqa: E402, I001
    empty_preferences,
    find_user,
    get_user,
    is_issue_processed,
    is_member,
    load_preferences,
    mark_issue_processed,
    member_usernames,
    remove_user,
    save_preferences,
    upsert_user,
    wants_discussion,
    wants_leaderboard,
)


def _doc(**users) -> dict:
    """Build a preferences doc from ``login=(leaderboard, discussion)``."""
    return {
        "version": 1,
        "users": {
            login: {
                "leaderboard": flags[0],
                "discussion": flags[1],
                "notifications_ack": True,
                "joined_at": "2026-08-01T10:00:00+00:00",
                "updated_at": "2026-08-01T10:00:00+00:00",
            }
            for login, flags in users.items()
        },
        "processed_issues": [],
    }


# ── loading ──────────────────────────────────────────────────


class TestLoadPreferences:
    """Loading must fail closed: unreadable state means nobody is in."""

    def test_missing_file_returns_empty(self, tmp_path):
        """A repo with no preferences file has no participants."""
        prefs = load_preferences(tmp_path / "nope.json")
        assert prefs["users"] == {}
        assert prefs["processed_issues"] == []

    def test_malformed_json_returns_empty(self, tmp_path):
        """Corrupt state must not be read as blanket consent."""
        path = tmp_path / "preferences.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_preferences(path)["users"] == {}

    def test_non_object_json_returns_empty(self, tmp_path):
        """A JSON list where an object is expected is treated as empty."""
        path = tmp_path / "preferences.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        assert load_preferences(path)["users"] == {}

    def test_missing_keys_are_backfilled(self, tmp_path):
        """A partial document still loads with usable defaults."""
        path = tmp_path / "preferences.json"
        path.write_text(json.dumps({"users": {}}), encoding="utf-8")
        prefs = load_preferences(path)
        assert prefs["processed_issues"] == []
        assert prefs["version"] == 1

    def test_wrong_types_are_replaced(self, tmp_path):
        """Nonsense types for users/processed_issues are discarded."""
        path = tmp_path / "preferences.json"
        path.write_text(
            json.dumps({"users": "everyone", "processed_issues": 3}),
            encoding="utf-8",
        )
        prefs = load_preferences(path)
        assert prefs["users"] == {}
        assert prefs["processed_issues"] == []

    def test_roundtrip(self, tmp_path):
        """Saving then loading preserves the entries."""
        path = tmp_path / "preferences.json"
        prefs = _doc(alice=(True, True))
        save_preferences(prefs, path)
        loaded = load_preferences(path)
        assert loaded["users"]["alice"]["discussion"] is True
        assert "updated_at" in loaded

    def test_save_creates_parent_directory(self, tmp_path):
        """First write in a fresh checkout must not fail on a missing dir."""
        path = Path(tmp_path) / "nested" / "preferences.json"
        save_preferences(empty_preferences(), path)
        assert path.exists()


# ── queries ──────────────────────────────────────────────────


class TestQueries:
    """Lookups drive every consent decision in the arena."""

    def test_member_and_non_member(self):
        """Only logins present in the document are participants."""
        prefs = _doc(alice=(True, True))
        assert is_member(prefs, "alice")
        assert not is_member(prefs, "stranger")

    def test_lookup_is_case_insensitive(self):
        """GitHub logins are case-insensitive; consent follows suit."""
        prefs = _doc(AlIcE=(True, True))
        assert is_member(prefs, "alice")
        assert wants_discussion(prefs, "ALICE")
        assert find_user(prefs, "alice")[0] == "AlIcE"

    def test_empty_username_is_never_a_member(self):
        """A blank author must not match anything."""
        prefs = _doc(alice=(True, True))
        assert not is_member(prefs, "")
        assert get_user(prefs, "   ") is None

    def test_discussion_flag_is_independent(self):
        """Leaderboard consent does not imply discussion consent."""
        prefs = _doc(quiet=(True, False))
        assert wants_leaderboard(prefs, "quiet")
        assert not wants_discussion(prefs, "quiet")

    def test_non_member_wants_nothing(self):
        """Absent users consent to nothing."""
        prefs = _doc()
        assert not wants_leaderboard(prefs, "ghost")
        assert not wants_discussion(prefs, "ghost")

    def test_member_usernames_preserves_casing(self):
        """The stored casing is what gets displayed."""
        prefs = _doc(AlVaro=(True, True), bob=(True, False))
        assert member_usernames(prefs) == {"AlVaro", "bob"}

    def test_malformed_entry_is_ignored(self):
        """A non-dict entry must not crash or grant consent."""
        prefs = {"users": {"weird": "yes please"}}
        assert not is_member(prefs, "weird")
        assert member_usernames(prefs) == set()


# ── mutations ────────────────────────────────────────────────


class TestUpsertUser:
    """Joining and re-joining."""

    def test_creates_entry(self):
        """A first submission records all three answers."""
        prefs = empty_preferences()
        upsert_user(
            prefs,
            "alice",
            leaderboard=True,
            discussion=True,
            notifications_ack=True,
            source_issue=42,
        )
        entry = prefs["users"]["alice"]
        assert entry["leaderboard"] is True
        assert entry["discussion"] is True
        assert entry["source_issue"] == 42
        assert entry["joined_at"] == entry["updated_at"]

    def test_update_preserves_joined_at(self):
        """Re-submitting to change a choice is not a new join."""
        prefs = _doc(alice=(True, True))
        original_join = prefs["users"]["alice"]["joined_at"]
        upsert_user(
            prefs,
            "alice",
            leaderboard=True,
            discussion=False,
            notifications_ack=True,
        )
        entry = prefs["users"]["alice"]
        assert entry["joined_at"] == original_join
        assert entry["updated_at"] != original_join
        assert entry["discussion"] is False

    def test_update_rewrites_key_to_latest_casing(self):
        """Only one entry survives, under the newest login casing."""
        prefs = _doc(alice=(True, True))
        upsert_user(
            prefs,
            "AlIcE",
            leaderboard=True,
            discussion=True,
            notifications_ack=True,
        )
        assert list(prefs["users"]) == ["AlIcE"]


class TestRemoveUser:
    """Leaving."""

    def test_removes_entry(self):
        """Removal deletes the participant outright."""
        prefs = _doc(alice=(True, True))
        assert remove_user(prefs, "alice") is True
        assert prefs["users"] == {}

    def test_removal_is_case_insensitive(self):
        """A leave form from AlIcE removes alice."""
        prefs = _doc(alice=(True, True))
        assert remove_user(prefs, "AlIcE") is True
        assert prefs["users"] == {}

    def test_removing_unknown_user_is_a_noop(self):
        """Leaving without having joined changes nothing."""
        prefs = _doc(alice=(True, True))
        assert remove_user(prefs, "ghost") is False
        assert "alice" in prefs["users"]


class TestProcessedIssues:
    """The idempotency ledger for consent forms."""

    def test_mark_and_check(self):
        """A processed issue is remembered."""
        prefs = empty_preferences()
        assert not is_issue_processed(prefs, 7)
        mark_issue_processed(prefs, 7)
        assert is_issue_processed(prefs, 7)

    def test_marking_twice_does_not_duplicate(self):
        """Re-running the sweep must not grow the ledger."""
        prefs = empty_preferences()
        mark_issue_processed(prefs, 7)
        mark_issue_processed(prefs, 7)
        assert prefs["processed_issues"] == [7]
