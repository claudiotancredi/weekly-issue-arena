"""Tests for scripts/sync_preferences.py."""

import sys
from pathlib import Path
from unittest.mock import patch

import yaml

sys.path.insert(0, "scripts")

from preferences import (  # noqa: E402, I001
    empty_preferences,
    get_user,
    is_issue_processed,
)
from sync_preferences import (  # noqa: E402, I001
    JOIN_DISCUSSION_HEADING,
    JOIN_LEADERBOARD_HEADING,
    JOIN_NOTIFICATIONS_HEADING,
    LEAVE_HEADING,
    checkbox_state,
    classify_form,
    clear_player_discussion,
    join_comment,
    leave_comment,
    parse_join,
    parse_leave,
    player_discussion_id,
    process_issue,
    purge_player,
    split_sections,
)


# ── fixtures ─────────────────────────────────────────────────

# Bodies below mirror exactly what GitHub renders from the issue forms in
# .github/ISSUE_TEMPLATE/. The section headings come from each field's
# `label`, so these strings and those YAML labels must stay in sync — that
# is what these tests pin.

JOIN_BODY_FULL = """### Public leaderboard

- [X] Show my GitHub username on the public leaderboard and statistics

### Personal arena update discussion

- [X] Create my personal arena update discussion

### Notifications

- [X] I understand that GitHub will subscribe me to any arena Discussion \
that mentions me, and that I can stop those notifications at any time with \
the "Unsubscribe" button in the thread's sidebar

### Anything else? (optional)

_No response_"""

JOIN_BODY_NO_DISCUSSION = """### Public leaderboard

- [X] Show my GitHub username on the public leaderboard and statistics

### Personal arena update discussion

- [ ] Create my personal arena update discussion

### Notifications

- [X] I understand that GitHub will subscribe me to any arena Discussion \
that mentions me

### Anything else? (optional)

_No response_"""

JOIN_BODY_NO_LEADERBOARD = """### Public leaderboard

- [ ] Show my GitHub username on the public leaderboard and statistics

### Personal arena update discussion

- [X] Create my personal arena update discussion

### Notifications

- [X] I understand"""

LEAVE_BODY = """### Confirm removal

- [X] Remove my username, my points and my arena Discussion thread from the \
Weekly Issue Arena

### Anything we should improve? (optional)

_No response_"""

LEAVE_BODY_UNCONFIRMED = """### Confirm removal

- [ ] Remove my username, my points and my arena Discussion thread from the \
Weekly Issue Arena"""

BUG_REPORT_BODY = """**Describe the bug**
The leaderboard shows the wrong total.

**Steps to reproduce**
- [x] I checked the FAQ first
"""


def _issue(number: int, author: str, body: str) -> dict:
    return {"number": number, "user": {"login": author}, "body": body}


def _scores(players: dict | None = None, weekly: dict | None = None) -> dict:
    return {
        "players": players or {},
        "credited_issues": [],
        "weekly": weekly or {},
    }


def _player(points: int = 3, node_id: str | None = None) -> dict:
    player = {
        "total_points": points,
        "avatar_url": "https://avatar.png",
        "contributions": [
            {
                "issue": "org/repo#1",
                "points": points,
                "pr_url": "https://github.com/org/repo/pull/1",
                "week": "2026-W31",
                "credited_at": "2026-08-01T10:00:00+00:00",
            }
        ],
    }
    if node_id:
        player["discussion_node_id"] = node_id
    return player


# ── body parsing ─────────────────────────────────────────────


class TestSplitSections:
    """Splitting the rendered issue-form body into answers."""

    def test_splits_on_headings(self):
        """Each `###` heading opens a section."""
        sections = split_sections(JOIN_BODY_FULL)
        assert "public leaderboard" in sections
        assert "personal arena update discussion" in sections
        assert "notifications" in sections

    def test_headings_are_lowercased(self):
        """Lookups are case-insensitive by construction."""
        sections = split_sections("### MiXeD Case\n\n- [X] yes")
        assert "mixed case" in sections

    def test_empty_body_yields_nothing(self):
        """A blank body has no sections and no answers."""
        assert split_sections("") == {}
        assert split_sections(None) == {}


class TestCheckboxState:
    """Reading a single checkbox answer."""

    def test_ticked(self):
        """`- [X]` reads as consent."""
        sections = split_sections(JOIN_BODY_FULL)
        assert checkbox_state(sections, "Public leaderboard") is True

    def test_unticked(self):
        """`- [ ]` reads as refusal, not as absence."""
        sections = split_sections(JOIN_BODY_NO_DISCUSSION)
        assert (
            checkbox_state(sections, "Personal arena update discussion")
            is False
        )

    def test_lowercase_x_counts(self):
        """GitHub renders `x` or `X` depending on client."""
        sections = split_sections("### Public leaderboard\n\n- [x] yes")
        assert checkbox_state(sections, "Public leaderboard") is True

    def test_missing_section_is_none(self):
        """A section that was never rendered is unknown, not false."""
        sections = split_sections(JOIN_BODY_FULL)
        assert checkbox_state(sections, "Nope") is None


class TestClassifyForm:
    """Telling the two consent forms apart from ordinary issues."""

    def test_join_form(self):
        """The leaderboard heading identifies a join form."""
        assert classify_form(JOIN_BODY_FULL) == "join"

    def test_leave_form(self):
        """The confirmation heading identifies a leave form."""
        assert classify_form(LEAVE_BODY) == "leave"

    def test_ordinary_issue_is_not_a_form(self):
        """A bug report with a checkbox is not consent."""
        assert classify_form(BUG_REPORT_BODY) is None

    def test_empty_body_is_not_a_form(self):
        """An empty issue body classifies as nothing."""
        assert classify_form("") is None


class TestParseJoin:
    """Turning a join body into stored choices."""

    def test_full_consent(self):
        """Both boxes ticked means both permissions granted."""
        choices = parse_join(JOIN_BODY_FULL)
        assert choices == {
            "leaderboard": True,
            "discussion": True,
            "notifications_ack": True,
        }

    def test_leaderboard_only(self):
        """Declining the thread still joins the arena."""
        choices = parse_join(JOIN_BODY_NO_DISCUSSION)
        assert choices["leaderboard"] is True
        assert choices["discussion"] is False

    def test_missing_leaderboard_consent_returns_none(self):
        """The required box is required; a hand-edited body records nothing."""
        assert parse_join(JOIN_BODY_NO_LEADERBOARD) is None


class TestParseLeave:
    """Reading the leave confirmation."""

    def test_confirmed(self):
        """A ticked confirmation authorises removal."""
        assert parse_leave(LEAVE_BODY) is True

    def test_unconfirmed(self):
        """An unticked confirmation authorises nothing."""
        assert parse_leave(LEAVE_BODY_UNCONFIRMED) is False


# ── scores purge ─────────────────────────────────────────────


class TestPurgePlayer:
    """Erasing a departing participant from scores.json."""

    def test_removes_player_and_returns_node_id(self):
        """The player row goes, and the thread ID comes back for deletion."""
        scores = _scores(players={"alice": _player(node_id="D_alice")})
        removed, node_id = purge_player(scores, "alice")
        assert removed is True
        assert node_id == "D_alice"
        assert scores["players"] == {}

    def test_removes_from_weekly_lists(self):
        """A departed player is not still 'merged this week'."""
        scores = _scores(
            players={"alice": _player(), "bob": _player()},
            weekly={"2026-W31": ["alice", "bob"], "2026-W30": ["alice"]},
        )
        purge_player(scores, "alice")
        assert scores["weekly"]["2026-W31"] == ["bob"]
        assert "2026-W30" not in scores["weekly"]

    def test_is_case_insensitive(self):
        """Login casing must not let a row survive removal."""
        scores = _scores(
            players={"AlIcE": _player()}, weekly={"2026-W31": ["AlIcE"]}
        )
        removed, _ = purge_player(scores, "alice")
        assert removed is True
        assert scores["players"] == {}
        assert scores["weekly"] == {}

    def test_credited_issues_are_kept(self):
        """Issue keys carry no identity and guard against double credit."""
        scores = _scores(players={"alice": _player()})
        scores["credited_issues"] = ["org/repo#1"]
        purge_player(scores, "alice")
        assert scores["credited_issues"] == ["org/repo#1"]

    def test_unknown_player_is_a_noop(self):
        """Leaving before ever scoring is not an error."""
        scores = _scores(players={"alice": _player()})
        removed, node_id = purge_player(scores, "ghost")
        assert removed is False
        assert node_id is None
        assert "alice" in scores["players"]


class TestDiscussionHelpers:
    """Reading and clearing the stored thread ID."""

    def test_player_discussion_id(self):
        """The node ID is found case-insensitively."""
        scores = _scores(players={"Alice": _player(node_id="D_1")})
        assert player_discussion_id(scores, "alice") == "D_1"

    def test_player_discussion_id_missing(self):
        """No thread means no ID."""
        scores = _scores(players={"alice": _player()})
        assert player_discussion_id(scores, "alice") is None

    def test_clear_player_discussion(self):
        """Clearing forgets the thread without touching the points."""
        scores = _scores(players={"alice": _player(node_id="D_1")})
        clear_player_discussion(scores, "alice")
        assert "discussion_node_id" not in scores["players"]["alice"]
        assert scores["players"]["alice"]["total_points"] == 3


# ── process_issue ────────────────────────────────────────────


class TestProcessJoin:
    """Applying a join form."""

    @patch("sync_preferences.comment_and_close")
    def test_records_preferences_and_closes(self, mock_close):
        """A valid form stores consent and closes the issue."""
        prefs = empty_preferences()
        scores = _scores()

        action = process_issue(
            _issue(42, "alice", JOIN_BODY_FULL), prefs, scores
        )

        assert action == "join"
        entry = get_user(prefs, "alice")
        assert entry["leaderboard"] is True
        assert entry["discussion"] is True
        assert entry["source_issue"] == 42
        assert is_issue_processed(prefs, 42)
        mock_close.assert_called_once()
        assert mock_close.call_args[0][0] == 42

    @patch("sync_preferences.comment_and_close")
    def test_author_is_the_subject_not_the_body(self, mock_close):
        """Nobody can opt somebody else in by typing their name."""
        body = JOIN_BODY_FULL + "\n\nPlease also add @victim to the arena."
        prefs = empty_preferences()

        process_issue(_issue(43, "alice", body), prefs, _scores())

        assert get_user(prefs, "alice") is not None
        assert get_user(prefs, "victim") is None

    @patch("sync_preferences.comment_and_close")
    def test_discussion_declined_is_stored(self, mock_close):
        """The optional box being unticked is recorded as a refusal."""
        prefs = empty_preferences()

        process_issue(
            _issue(44, "quiet", JOIN_BODY_NO_DISCUSSION), prefs, _scores()
        )

        assert get_user(prefs, "quiet")["discussion"] is False

    @patch("sync_preferences.delete_thread", return_value=True)
    @patch("sync_preferences.comment_and_close")
    def test_withdrawing_discussion_consent_deletes_thread(
        self, mock_close, mock_delete
    ):
        """Unticking the box takes down the thread it created."""
        prefs = empty_preferences()
        prefs["users"]["alice"] = {
            "leaderboard": True,
            "discussion": True,
            "notifications_ack": True,
        }
        scores = _scores(players={"alice": _player(node_id="D_alice")})

        process_issue(
            _issue(45, "alice", JOIN_BODY_NO_DISCUSSION), prefs, scores
        )

        mock_delete.assert_called_once_with("D_alice")
        assert "discussion_node_id" not in scores["players"]["alice"]

    @patch("sync_preferences.delete_thread")
    @patch("sync_preferences.comment_and_close")
    def test_keeping_discussion_consent_keeps_thread(
        self, mock_close, mock_delete
    ):
        """Re-submitting without changing the box leaves the thread alone."""
        prefs = empty_preferences()
        prefs["users"]["alice"] = {
            "leaderboard": True,
            "discussion": True,
            "notifications_ack": True,
        }
        scores = _scores(players={"alice": _player(node_id="D_alice")})

        process_issue(_issue(46, "alice", JOIN_BODY_FULL), prefs, scores)

        mock_delete.assert_not_called()
        assert scores["players"]["alice"]["discussion_node_id"] == "D_alice"

    @patch("sync_preferences.comment_and_close")
    def test_form_without_required_consent_records_nothing(self, mock_close):
        """An unticked required box stores no consent but still closes."""
        prefs = empty_preferences()

        action = process_issue(
            _issue(47, "alice", JOIN_BODY_NO_LEADERBOARD), prefs, _scores()
        )

        assert action == "join-invalid"
        assert get_user(prefs, "alice") is None
        assert is_issue_processed(prefs, 47)
        mock_close.assert_called_once()

    @patch("sync_preferences.comment_and_close")
    def test_already_processed_issue_is_skipped(self, mock_close):
        """An edit to a settled form must not re-run it."""
        prefs = empty_preferences()
        prefs["processed_issues"] = [42]

        action = process_issue(
            _issue(42, "alice", JOIN_BODY_FULL), prefs, _scores()
        )

        assert action is None
        mock_close.assert_not_called()

    @patch("sync_preferences.comment_and_close")
    def test_ordinary_issue_is_ignored(self, mock_close):
        """Bug reports pass through untouched — and stay open."""
        prefs = empty_preferences()

        action = process_issue(
            _issue(48, "alice", BUG_REPORT_BODY), prefs, _scores()
        )

        assert action is None
        assert prefs["users"] == {}
        mock_close.assert_not_called()

    @patch("sync_preferences.comment_and_close")
    def test_dry_run_writes_nothing(self, mock_close):
        """--dry-run reports without storing or closing."""
        prefs = empty_preferences()

        action = process_issue(
            _issue(49, "alice", JOIN_BODY_FULL),
            prefs,
            _scores(),
            dry_run=True,
        )

        assert action == "join"
        assert prefs["users"] == {}
        assert not is_issue_processed(prefs, 49)
        mock_close.assert_not_called()

    @patch("sync_preferences.comment_and_close")
    def test_authorless_issue_is_skipped(self, mock_close):
        """A payload with no author has nobody to record consent for."""
        prefs = empty_preferences()
        issue = {"number": 50, "user": None, "body": JOIN_BODY_FULL}

        assert process_issue(issue, prefs, _scores()) is None
        assert prefs["users"] == {}


class TestProcessLeave:
    """Applying a leave form."""

    @patch("sync_preferences.delete_thread", return_value=True)
    @patch("sync_preferences.comment_and_close")
    def test_purges_everything(self, mock_close, mock_delete):
        """Leaving erases consent, points, weekly rows and the thread."""
        prefs = empty_preferences()
        prefs["users"]["alice"] = {"leaderboard": True, "discussion": True}
        scores = _scores(
            players={"alice": _player(node_id="D_alice")},
            weekly={"2026-W31": ["alice"]},
        )

        action = process_issue(_issue(60, "alice", LEAVE_BODY), prefs, scores)

        assert action == "leave"
        assert prefs["users"] == {}
        assert scores["players"] == {}
        assert scores["weekly"] == {}
        mock_delete.assert_called_once_with("D_alice")
        mock_close.assert_called_once()

    @patch("sync_preferences.delete_thread")
    @patch("sync_preferences.comment_and_close")
    def test_leaving_without_joining_is_graceful(
        self, mock_close, mock_delete
    ):
        """Someone who never joined can still submit the form."""
        prefs = empty_preferences()

        action = process_issue(
            _issue(61, "ghost", LEAVE_BODY), prefs, _scores()
        )

        assert action == "leave"
        mock_delete.assert_not_called()
        mock_close.assert_called_once()

    @patch("sync_preferences.delete_thread", side_effect=AssertionError)
    @patch("sync_preferences.comment_and_close")
    def test_unconfirmed_leave_removes_nothing(self, mock_close, mock_delete):
        """Without the confirmation box, nothing is deleted."""
        prefs = empty_preferences()
        prefs["users"]["alice"] = {"leaderboard": True, "discussion": True}
        scores = _scores(players={"alice": _player(node_id="D_alice")})

        action = process_issue(
            _issue(62, "alice", LEAVE_BODY_UNCONFIRMED), prefs, scores
        )

        assert action == "leave-invalid"
        assert "alice" in prefs["users"]
        assert "alice" in scores["players"]

    @patch("sync_preferences.delete_thread", return_value=False)
    @patch("sync_preferences.comment_and_close")
    def test_thread_delete_failure_still_removes_consent(
        self, mock_close, mock_delete
    ):
        """A failed delete must still remove the user from the arena."""
        prefs = empty_preferences()
        prefs["users"]["alice"] = {"leaderboard": True, "discussion": True}
        scores = _scores(players={"alice": _player(node_id="D_alice")})

        process_issue(_issue(63, "alice", LEAVE_BODY), prefs, scores)

        assert prefs["users"] == {}
        assert scores["players"] == {}

    @patch("sync_preferences.delete_thread")
    @patch("sync_preferences.comment_and_close")
    def test_dry_run_leaves_state_intact(self, mock_close, mock_delete):
        """--dry-run never deletes a thread."""
        prefs = empty_preferences()
        prefs["users"]["alice"] = {"leaderboard": True, "discussion": True}
        scores = _scores(players={"alice": _player(node_id="D_alice")})

        process_issue(
            _issue(64, "alice", LEAVE_BODY), prefs, scores, dry_run=True
        )

        assert "alice" in prefs["users"]
        assert "alice" in scores["players"]
        mock_delete.assert_not_called()
        mock_close.assert_not_called()


# ── comment bodies ───────────────────────────────────────────


class TestComments:
    """What the bot says back."""

    def test_join_comment_states_both_choices(self):
        """The confirmation spells out what was actually stored."""
        body = join_comment(
            "alice",
            {"leaderboard": True, "discussion": True},
            updated=False,
        )
        assert "@alice" in body
        assert "recorded" in body
        assert "leave_the_arena" in body

    def test_join_comment_reflects_declined_discussion(self):
        """Declining the thread is stated plainly, not glossed over."""
        body = join_comment(
            "alice",
            {"leaderboard": True, "discussion": False},
            updated=True,
        )
        assert "updated" in body
        assert "without ever being mentioned" in body

    def test_leave_comment_lists_what_was_erased(self):
        """A departing user is told exactly what happened."""
        body = leave_comment("alice", was_member=True, purged=True)
        assert "@alice" in body
        assert "erased" in body
        assert "join_the_arena" in body

    def test_leave_comment_for_non_member(self):
        """Nothing on record is said honestly, not faked."""
        body = leave_comment("ghost", was_member=False, purged=False)
        assert "nothing on record" in body


# ── form / parser coupling ───────────────────────────────────


class TestFormLabelsMatchParser:
    """The YAML forms and the parser constants must not drift apart.

    GitHub renders each checkbox field's ``label`` as the ``###`` heading
    in the issue body, and the parser finds answers by that heading. A
    reworded label with no matching constant would silently stop
    recording consent, so the two are pinned to each other here.
    """

    @staticmethod
    def _checkbox_labels(template: str) -> list[str]:
        doc = yaml.safe_load(
            Path(f".github/ISSUE_TEMPLATE/{template}").read_text(
                encoding="utf-8"
            )
        )
        return [
            block["attributes"]["label"]
            for block in doc["body"]
            if block["type"] == "checkboxes"
        ]

    @staticmethod
    def _required_flags(template: str) -> dict[str, bool]:
        doc = yaml.safe_load(
            Path(f".github/ISSUE_TEMPLATE/{template}").read_text(
                encoding="utf-8"
            )
        )
        return {
            block["attributes"]["label"]: block["attributes"]["options"][
                0
            ].get("required", False)
            for block in doc["body"]
            if block["type"] == "checkboxes"
        }

    def test_join_form_headings(self):
        """Each join heading the parser looks for exists in the form."""
        assert self._checkbox_labels("join_the_arena.yml") == [
            JOIN_LEADERBOARD_HEADING,
            JOIN_DISCUSSION_HEADING,
            JOIN_NOTIFICATIONS_HEADING,
        ]

    def test_leave_form_heading(self):
        """The leave confirmation heading matches the parser."""
        assert self._checkbox_labels("leave_the_arena.yml") == [LEAVE_HEADING]

    def test_leaderboard_consent_is_required_in_the_form(self):
        """parse_join rejects a body without it, so GitHub must enforce it."""
        flags = self._required_flags("join_the_arena.yml")
        assert flags[JOIN_LEADERBOARD_HEADING] is True
        assert flags[JOIN_NOTIFICATIONS_HEADING] is True

    def test_discussion_consent_is_optional_in_the_form(self):
        """Points without mentions must remain a reachable choice."""
        flags = self._required_flags("join_the_arena.yml")
        assert flags[JOIN_DISCUSSION_HEADING] is False

    def test_leave_confirmation_is_required(self):
        """Nobody is removed without ticking the box."""
        flags = self._required_flags("leave_the_arena.yml")
        assert flags[LEAVE_HEADING] is True
