"""Tests for scripts/update_leaderboard.py."""

import json
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, "scripts")

from utils import get_rank  # noqa: E402, I001
from update_leaderboard import (  # noqa: E402, I001
    build_leaderboard_md,
    build_merged_this_week_md,
    check_issue_status,
    gather_issue_prs,
    load_scores,
    load_state,
    process_week,
    save_scores,
    save_state,
    update_arena_level,
    update_issue_statuses,
)
import arena_level  # noqa: E402, I001


# ── get_rank ─────────────────────────────────────────────────


class TestGetRank:
    """Tests for the get_rank helper."""

    def test_zero_points(self):
        """Zero points yields Hello World Engineer."""
        assert get_rank(0) == "Hello World Engineer"

    def test_just_below_bug_slayer(self):
        """99 points is still Hello World Engineer."""
        assert get_rank(99) == "Hello World Engineer"

    def test_bug_slayer_threshold(self):
        """100 points reaches Bug Slayer."""
        assert get_rank(100) == "Bug Slayer"

    def test_bug_slayer_upper_bound(self):
        """499 points is still Bug Slayer."""
        assert get_rank(499) == "Bug Slayer"

    def test_mr_robot_threshold(self):
        """500 points reaches Mr. Robot."""
        assert get_rank(500) == "Mr. Robot"

    def test_mr_robot_high_score(self):
        """1000 points is still Mr. Robot."""
        assert get_rank(1000) == "Mr. Robot"


# ── load_state / load_scores ─────────────────────────────────


class TestLoadState:
    """Tests for the load_state function."""

    def test_missing_file_returns_empty_dict(self, tmp_path, monkeypatch):
        """Missing issues.json returns an empty dict."""
        fake = tmp_path / "missing" / "issues.json"
        monkeypatch.setattr("update_leaderboard.STATE_PATH", fake)
        assert load_state() == {}

    def test_existing_file_returns_parsed_json(self, tmp_path, monkeypatch):
        """Existing issues.json is loaded and parsed."""
        fake = tmp_path / "issues.json"
        data = {"2026-W11": {"fetched_at": "x"}}
        fake.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr("update_leaderboard.STATE_PATH", fake)
        assert load_state() == data


class TestLoadScores:
    """Tests for the load_scores function."""

    def test_missing_file_returns_default(self, tmp_path, monkeypatch):
        """Missing scores.json returns default structure."""
        fake = tmp_path / "missing" / "scores.json"
        monkeypatch.setattr("update_leaderboard.SCORES_PATH", fake)
        result = load_scores()
        assert result == {
            "players": {},
            "credited_issues": [],
            "weekly": {},
        }

    def test_existing_file_returns_parsed_json(self, tmp_path, monkeypatch):
        """Existing scores.json is loaded and parsed."""
        fake = tmp_path / "scores.json"
        data = {
            "players": {"alice": {"total_points": 5}},
            "credited_issues": ["a/b#1"],
            "weekly": {},
        }
        fake.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr("update_leaderboard.SCORES_PATH", fake)
        assert load_scores() == data


# ── save_scores / save_state ─────────────────────────────────


class TestSaveScores:
    """Tests for the save_scores function."""

    def test_creates_parent_dir(self, tmp_path, monkeypatch):
        """Parent directory is created if it does not exist."""
        fake = tmp_path / "new_dir" / "scores.json"
        monkeypatch.setattr("update_leaderboard.SCORES_PATH", fake)
        save_scores({"players": {}, "credited_issues": []})
        assert fake.parent.exists()

    def test_writes_valid_json(self, tmp_path, monkeypatch):
        """Output file contains valid JSON matching input."""
        fake = tmp_path / "scores.json"
        monkeypatch.setattr("update_leaderboard.SCORES_PATH", fake)
        data = {
            "players": {"bob": {"total_points": 3}},
            "credited_issues": ["x/y#2"],
            "weekly": {},
        }
        save_scores(data)
        loaded = json.loads(fake.read_text(encoding="utf-8"))
        assert loaded == data


class TestGatherIssuePrs:
    """Tests for gather_issue_prs network-failure handling."""

    @patch("update_leaderboard.github_get")
    def test_pr_fetch_failure_returns_none(self, mock_get):
        """PR sub-fetch failure bails (returns None).

        Otherwise the issue's qualifying PR is invisible to the rest of
        the run and the issue may be false-closed past its 7-day window.
        """
        timeline_response = type(
            "R",
            (),
            {
                "raise_for_status": lambda self: None,
                "json": lambda self: [
                    {
                        "event": "cross-referenced",
                        "source": {
                            "issue": {
                                "pull_request": {
                                    "url": (
                                        "https://api.github.com/repos/"
                                        "org/proj/pulls/10"
                                    ),
                                }
                            }
                        },
                    }
                ],
                "links": {},
            },
        )()

        def side_effect(url, **kwargs):
            if "timeline" in url:
                return timeline_response
            raise RuntimeError("simulated PR fetch failure")

        mock_get.side_effect = side_effect
        deadline = datetime.now(timezone.utc) + timedelta(days=7)
        assert gather_issue_prs("org", "proj", 1, deadline) is None


class TestSaveState:
    """Tests for the save_state function."""

    def test_creates_parent_dir(self, tmp_path, monkeypatch):
        """Parent directory is created if it does not exist."""
        fake = tmp_path / "sub" / "issues.json"
        monkeypatch.setattr("update_leaderboard.STATE_PATH", fake)
        save_state({"week": {}})
        assert fake.parent.exists()

    def test_writes_valid_json(self, tmp_path, monkeypatch):
        """Output file contains valid JSON matching input."""
        fake = tmp_path / "issues.json"
        monkeypatch.setattr("update_leaderboard.STATE_PATH", fake)
        data = {"2026-W11": {"fetched_at": "ts"}}
        save_state(data)
        loaded = json.loads(fake.read_text(encoding="utf-8"))
        assert loaded == data


# ── process_week ─────────────────────────────────────────────


def _make_issue(
    owner="org",
    repo="proj",
    number=1,
    listed_at=None,
):
    """Build a minimal issue dict for testing."""
    if listed_at is None:
        listed_at = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
    return {
        "owner": owner,
        "repo": repo,
        "number": number,
        "listed_at": listed_at,
        "title": f"Issue #{number}",
        "url": (f"https://github.com/{owner}/{repo}/issues/{number}"),
    }


def _make_week_data(issues_by_cat=None, fetched_at=None):
    """Build a minimal week_data dict for testing."""
    if fetched_at is None:
        fetched_at = (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat()
    if issues_by_cat is None:
        issues_by_cat = {"gfi": [], "bug": [], "hard": []}
    return {
        "fetched_at": fetched_at,
        "issues": issues_by_cat,
    }


def _make_scores(players=None, credited=None, weekly=None):
    """Build a minimal scores dict for testing."""
    return {
        "players": players or {},
        "credited_issues": credited or [],
        "weekly": weekly or {},
    }


def _make_pr(
    author="contributor",
    avatar="https://avatar.example.com/a.png",
    pr_url="https://github.com/org/proj/pull/10",
    merged_at=None,
    created_at=None,
):
    """Build a minimal PR dict for testing."""
    now = datetime.now(timezone.utc)
    if merged_at is None:
        merged_at = now.isoformat()
    if created_at is None:
        created_at = (now - timedelta(hours=2)).isoformat()
    return {
        "author": author,
        "author_avatar": avatar,
        "pr_url": pr_url,
        "merged_at": merged_at,
        "created_at": created_at,
    }


def _bundle(prs=None, state="open", closing_commit=None):
    """Build the dict ``gather_issue_prs`` returns in the new architecture."""
    return {
        "prs": prs or [],
        "state": state,
        "closing_commit": closing_commit,
    }


def _make_gathered_pr(
    author="contributor",
    author_avatar="https://avatar.example.com/a.png",
    pr_url="https://github.com/org/proj/pull/10",
    state="open",
    merged=False,
    merged_at=None,
    created_at=None,
    has_closing_keyword=True,
    within_deadline=True,
    merge_commit_sha=None,
    number=10,
):
    """Build a PR dict matching gather_issue_prs output."""
    now = datetime.now(timezone.utc)
    if created_at is None:
        created_at = (now - timedelta(hours=2)).isoformat()
    return {
        "number": number,
        "author": author,
        "author_avatar": author_avatar,
        "pr_url": pr_url,
        "state": state,
        "merged": merged,
        "merged_at": merged_at,
        "created_at": created_at,
        "merge_commit_sha": merge_commit_sha,
        "has_closing_keyword": has_closing_keyword,
        "within_deadline": within_deadline,
        "body": "Closes #1" if has_closing_keyword else "",
    }


class TestProcessWeek:
    """Tests for the process_week function (new architecture)."""

    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open")
    def test_already_credited_issue_gets_closed_flag(
        self, mock_open, mock_gather
    ):
        """Already credited issues are marked closed without API calls."""
        issue = _make_issue()
        key = "org/proj#1"
        scores = _make_scores(credited=[key])
        week = _make_week_data({"gfi": [issue]})

        credits, events, _ = process_week("2026-W11", week, scores)

        assert credits == []
        assert events == []
        assert week["issues"]["gfi"][0]["closed"] is True
        mock_open.assert_not_called()
        mock_gather.assert_not_called()

    @patch("update_leaderboard.gather_issue_prs", return_value=_bundle())
    @patch("update_leaderboard.check_issue_still_open", return_value=True)
    def test_open_within_window_skipped(self, mock_open, mock_gather):
        """Open issue within 7-day window is skipped (no credit)."""
        listed = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue]})

        credits, events, _ = process_week("2026-W11", week, scores)

        assert credits == []
        assert "org/proj#1" not in scores["credited_issues"]

    @patch("update_leaderboard.gather_issue_prs", return_value=_bundle())
    @patch("update_leaderboard.check_issue_still_open", return_value=True)
    def test_open_past_window_no_pr_gets_closed_flag(
        self, mock_open, mock_gather
    ):
        """Open issue past 7-day window with no PR is marked closed."""
        listed = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue]})

        credits, events, _ = process_week("2026-W11", week, scores)

        assert credits == []
        assert week["issues"]["gfi"][0].get("closed") is True
        assert "org/proj#1" not in scores["credited_issues"]

    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open", return_value=True)
    def test_open_past_window_with_pending_pr_kept(
        self, mock_open, mock_gather
    ):
        """Open issue past window with a qualifying pending PR is kept."""
        listed = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue]})
        mock_gather.return_value = [
            _make_gathered_pr(state="open", merged=False)
        ]

        credits, events, _ = process_week("2026-W11", week, scores)

        assert credits == []
        assert issue.get("has_pr") is True
        assert not issue.get("closed")

    @patch(
        "update_leaderboard.gather_issue_prs",
        return_value=_bundle(state="closed"),
    )
    @patch("update_leaderboard.check_issue_still_open", return_value=False)
    def test_closed_no_pr_gets_closed_flag(self, mock_open, mock_gather):
        """Closed issue with no qualifying PR is marked closed."""
        issue = _make_issue()
        scores = _make_scores()
        week = _make_week_data({"bug": [issue]})

        credits, events, _ = process_week("2026-W11", week, scores)

        assert credits == []
        assert issue.get("closed") is True
        assert "org/proj#1" not in scores["credited_issues"]

    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open", return_value=False)
    def test_closed_pr_opened_too_late_gets_closed_flag(
        self, mock_open, mock_gather
    ):
        """PR opened after the 7-day window does not credit."""
        listed = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"hard": [issue]})

        late_created = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        mock_gather.return_value = [
            _make_gathered_pr(
                state="closed",
                merged=True,
                merged_at=datetime.now(timezone.utc).isoformat(),
                created_at=late_created,
                within_deadline=False,
            )
        ]

        credits, events, _ = process_week("2026-W11", week, scores)

        assert credits == []
        assert issue.get("closed") is True
        assert "org/proj#1" not in scores["credited_issues"]

    @patch("update_leaderboard.arena_week_id", return_value="2026-W12")
    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open", return_value=False)
    def test_valid_pr_awards_points(
        self, mock_open, mock_gather, mock_week_id
    ):
        """Valid merged PR credits author and creates player entry."""
        listed = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"bug": [issue]})

        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat()
        mock_gather.return_value = [
            _make_gathered_pr(
                author="hero",
                author_avatar="https://avatar.example.com/hero.png",
                state="closed",
                merged=True,
                merged_at=datetime.now(timezone.utc).isoformat(),
                created_at=pr_created,
            )
        ]

        credits, events, _ = process_week("2026-W11", week, scores)

        assert len(credits) == 1
        credit = credits[0]
        assert credit["author"] == "hero"
        assert credit["pts"] == 2
        assert credit["issue"] == "org/proj#1"
        assert credit["week"] == "2026-W11"

        player = scores["players"]["hero"]
        assert player["total_points"] == 2
        assert len(player["contributions"]) == 1
        assert "org/proj#1" in scores["credited_issues"]
        assert issue.get("closed") is True
        assert "hero" in scores["weekly"]["2026-W12"]

        event_types = [e["type"] for e in events]
        assert "first_merge" in event_types

    @patch("update_leaderboard.arena_week_id", return_value="2026-W12")
    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open", return_value=False)
    def test_additional_merge_event_for_returning_contributor(
        self, mock_open, mock_gather, mock_week_id
    ):
        """Returning contributor (already flagged) gets additional_merge."""
        listed = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        issue = _make_issue(number=5, listed_at=listed)
        existing_player = {
            "total_points": 3,
            "avatar_url": "https://old.png",
            "contributions": [
                {
                    "issue": "x/y#1",
                    "points": 1,
                    "pr_url": "https://pr",
                    "week": "2026-W10",
                    "credited_at": "ts",
                }
            ],
            "discussion_node_id": "D_existing",
            "notified": {"first_merge": True},
        }
        scores = _make_scores(players={"hero": existing_player})
        week = _make_week_data({"hard": [issue]})
        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        mock_gather.return_value = [
            _make_gathered_pr(
                author="hero",
                author_avatar="https://new.png",
                state="closed",
                merged=True,
                merged_at=datetime.now(timezone.utc).isoformat(),
                created_at=pr_created,
            )
        ]

        credits, events, _ = process_week("2026-W11", week, scores)

        assert len(credits) == 1
        player = scores["players"]["hero"]
        assert player["total_points"] == 7
        event_types = [e["type"] for e in events]
        assert "additional_merge" in event_types
        assert "first_merge" not in event_types

    @patch("update_leaderboard.arena_week_id", return_value="2026-W12")
    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open", return_value=False)
    def test_multiple_categories_processed(
        self, mock_open, mock_gather, mock_week_id
    ):
        """Issues from multiple categories are all processed."""
        listed = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        gfi_issue = _make_issue(number=1, listed_at=listed)
        bug_issue = _make_issue(number=2, listed_at=listed)
        scores = _make_scores()
        week = _make_week_data(
            {
                "gfi": [gfi_issue],
                "bug": [bug_issue],
                "hard": [],
            }
        )
        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        now_iso = datetime.now(timezone.utc).isoformat()
        mock_gather.side_effect = [
            [
                _make_gathered_pr(
                    author="alice",
                    state="closed",
                    merged=True,
                    created_at=pr_created,
                    merged_at=now_iso,
                )
            ],
            [
                _make_gathered_pr(
                    author="bob",
                    state="closed",
                    merged=True,
                    created_at=pr_created,
                    merged_at=now_iso,
                )
            ],
        ]

        credits, events, _ = process_week("2026-W11", week, scores)

        assert len(credits) == 2
        authors = {c["author"] for c in credits}
        assert authors == {"alice", "bob"}
        pts_by_author = {c["author"]: c["pts"] for c in credits}
        assert pts_by_author == {"alice": 1, "bob": 2}

    @patch("update_leaderboard.arena_week_id", return_value="2026-W12")
    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open")
    def test_pending_pr_tracked_then_credited(
        self, mock_open, mock_gather, mock_week_id
    ):
        """Issue kept via pending PR on run 1 is credited on run 2."""
        listed = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"bug": [issue]})

        mock_open.return_value = True
        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=18)
        ).isoformat()
        mock_gather.return_value = [
            _make_gathered_pr(
                author="hero",
                state="open",
                merged=False,
                created_at=pr_created,
            )
        ]
        credits1, _, _ = process_week("2026-W11", week, scores)
        assert credits1 == []

        mock_open.return_value = False
        mock_gather.return_value = [
            _make_gathered_pr(
                author="hero",
                state="closed",
                merged=True,
                merged_at=datetime.now(timezone.utc).isoformat(),
                created_at=pr_created,
            )
        ]
        credits2, events2, _ = process_week("2026-W11", week, scores)
        assert len(credits2) == 1
        assert credits2[0]["author"] == "hero"
        assert credits2[0]["pts"] == 2
        assert "org/proj#1" in scores["credited_issues"]

    @patch("update_leaderboard.gather_issue_prs", return_value=_bundle())
    @patch("update_leaderboard.check_issue_still_open", return_value=True)
    def test_missing_listed_at_falls_back_to_fetched_at(
        self, mock_open, mock_gather
    ):
        """Issue without listed_at uses fetched_at to compute deadline."""
        fetched_at = (
            datetime.now(timezone.utc) - timedelta(days=10)
        ).isoformat()
        issue = {
            "owner": "org",
            "repo": "proj",
            "number": 1,
        }
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue]}, fetched_at=fetched_at)

        process_week("2026-W11", week, scores)

        assert issue.get("closed") is True

    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open", return_value=True)
    def test_two_issues_one_with_pr_one_without(self, mock_open, mock_gather):
        """Pending-PR issue stays open; issue without PR gets closed."""
        listed = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        issue_a = _make_issue(number=1, listed_at=listed)
        issue_b = _make_issue(number=2, listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue_a, issue_b]})
        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=9)
        ).isoformat()
        mock_gather.side_effect = [
            [
                _make_gathered_pr(
                    state="open", merged=False, created_at=pr_created
                )
            ],
            [],
        ]

        process_week("2026-W11", week, scores)

        assert issue_a.get("has_pr") is True
        assert not issue_a.get("closed")
        assert issue_b.get("closed") is True

    @patch("update_leaderboard.gather_issue_prs", return_value=_bundle())
    @patch("update_leaderboard.check_issue_still_open", return_value=True)
    def test_deadline_exactly_now_not_expired(self, mock_open, mock_gather):
        """Deadline still in the future is not expired."""
        listed = (
            datetime.now(timezone.utc)
            - timedelta(days=7)
            + timedelta(minutes=1)
        ).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue]})

        process_week("2026-W11", week, scores)

        assert not issue.get("closed")

    @patch("update_leaderboard.arena_week_id", return_value="2026-W12")
    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open", return_value=False)
    def test_rank_up_event_emitted(self, mock_open, mock_gather, mock_week_id):
        """Crossing a rank threshold emits a rank_up event."""
        listed = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores(
            players={
                "hero": {
                    "total_points": 99,
                    "avatar_url": "https://h.png",
                    "contributions": [],
                    "discussion_node_id": "D_1",
                    "notified": {"first_merge": True},
                }
            }
        )
        week = _make_week_data({"bug": [issue]})
        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat()
        mock_gather.return_value = [
            _make_gathered_pr(
                author="hero",
                state="closed",
                merged=True,
                merged_at=datetime.now(timezone.utc).isoformat(),
                created_at=pr_created,
            )
        ]

        credits, events, _ = process_week("2026-W11", week, scores)

        assert len(credits) == 1
        rank_up = [e for e in events if e["type"] == "rank_up"]
        assert len(rank_up) == 1
        assert rank_up[0]["new_rank"] == "Bug Slayer"

    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open", return_value=True)
    def test_welcome_event_for_new_pr_author(self, mock_open, mock_gather):
        """First qualifying PR by new author produces a welcome event."""
        listed = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue]})
        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        mock_gather.return_value = [
            _make_gathered_pr(
                author="newcomer",
                state="open",
                merged=False,
                created_at=pr_created,
            )
        ]

        credits, events, _ = process_week("2026-W11", week, scores)

        welcomes = [e for e in events if e["type"] == "welcome"]
        assert len(welcomes) == 1
        assert welcomes[0]["username"] == "newcomer"

    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open", return_value=True)
    def test_no_welcome_if_already_welcomed(self, mock_open, mock_gather):
        """Existing welcomed user does not get a duplicate welcome."""
        listed = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores(
            players={
                "veteran": {
                    "total_points": 1,
                    "avatar_url": "https://v.png",
                    "contributions": [],
                    "discussion_node_id": "D_known",
                }
            }
        )
        week = _make_week_data({"gfi": [issue]})
        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        mock_gather.return_value = [
            _make_gathered_pr(
                author="veteran",
                state="open",
                merged=False,
                created_at=pr_created,
            )
        ]

        credits, events, _ = process_week("2026-W11", week, scores)

        welcomes = [e for e in events if e["type"] == "welcome"]
        assert welcomes == []

    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open", return_value=True)
    def test_pr_closed_event_for_welcomed_user(self, mock_open, mock_gather):
        """Closed-unmerged PR by welcomed user emits pr_closed event."""
        listed = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores(
            players={
                "alice": {
                    "total_points": 1,
                    "avatar_url": "https://a.png",
                    "contributions": [],
                    "discussion_node_id": "D_a",
                }
            }
        )
        week = _make_week_data({"gfi": [issue]})
        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        mock_gather.return_value = [
            _make_gathered_pr(
                author="alice",
                state="closed",
                merged=False,
                created_at=pr_created,
            )
        ]

        credits, events, _ = process_week("2026-W11", week, scores)

        closed = [e for e in events if e["type"] == "pr_closed"]
        assert len(closed) == 1
        assert closed[0]["username"] == "alice"

        # Simulate dispatcher marking the event notified on success.
        scores["players"]["alice"].setdefault("notified", {}).setdefault(
            "pr_closed", []
        ).append(closed[0]["pr_url"])

        credits2, events2, _ = process_week("2026-W11", week, scores)
        assert [e for e in events2 if e["type"] == "pr_closed"] == []

    @patch("update_leaderboard.arena_week_id", return_value="2026-W12")
    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open", return_value=False)
    def test_issue_closed_event_for_losing_welcomed_user(
        self, mock_open, mock_gather, mock_week_id
    ):
        """Losing PR author (with welcome thread) gets issue_closed event."""
        listed = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores(
            players={
                "loser": {
                    "total_points": 1,
                    "avatar_url": "https://l.png",
                    "contributions": [],
                    "discussion_node_id": "D_l",
                }
            }
        )
        week = _make_week_data({"bug": [issue]})
        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat()
        mock_gather.return_value = [
            _make_gathered_pr(
                author="winner",
                state="closed",
                merged=True,
                merged_at=datetime.now(timezone.utc).isoformat(),
                created_at=pr_created,
            ),
            _make_gathered_pr(
                author="loser",
                state="open",
                merged=False,
                pr_url="https://github.com/org/proj/pull/11",
                created_at=pr_created,
            ),
        ]

        credits, events, _ = process_week("2026-W11", week, scores)

        assert len(credits) == 1
        assert credits[0]["author"] == "winner"
        closed_evs = [e for e in events if e["type"] == "issue_closed"]
        assert len(closed_evs) == 1
        assert closed_evs[0]["username"] == "loser"

    @patch("update_leaderboard.arena_week_id", return_value="2026-W12")
    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open")
    def test_issue_closed_suppressed_if_pr_closed_already_sent(
        self, mock_open, mock_gather, mock_week_id
    ):
        """User who already got pr_closed for an issue skips issue_closed."""
        listed = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores(
            players={
                "loser": {
                    "total_points": 1,
                    "avatar_url": "https://l.png",
                    "contributions": [],
                    "discussion_node_id": "D_l",
                }
            }
        )
        week = _make_week_data({"bug": [issue]})
        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat()

        # Run 1: loser's PR is closed-unmerged while issue still open
        mock_open.return_value = True
        mock_gather.return_value = [
            _make_gathered_pr(
                author="loser",
                state="closed",
                merged=False,
                pr_url="https://github.com/org/proj/pull/11",
                created_at=pr_created,
            )
        ]
        _, events1, _ = process_week("2026-W11", week, scores)
        assert any(
            e["type"] == "pr_closed" and e["username"] == "loser"
            for e in events1
        )
        # Simulate dispatcher marking pr_closed + pr_closed_issues on success.
        loser_notified = scores["players"]["loser"].setdefault("notified", {})
        loser_notified.setdefault("pr_closed", []).append(
            "https://github.com/org/proj/pull/11"
        )
        loser_notified.setdefault("pr_closed_issues", []).append("org/proj#1")

        # Run 2: a winning PR merges and closes the issue
        mock_open.return_value = False
        mock_gather.return_value = [
            _make_gathered_pr(
                author="winner",
                state="closed",
                merged=True,
                merged_at=datetime.now(timezone.utc).isoformat(),
                pr_url="https://github.com/org/proj/pull/10",
                created_at=pr_created,
            ),
            _make_gathered_pr(
                author="loser",
                state="closed",
                merged=False,
                pr_url="https://github.com/org/proj/pull/11",
                created_at=pr_created,
            ),
        ]
        issue["closed"] = False  # reset from run 1
        _, events2, _ = process_week("2026-W11", week, scores)

        closed_evs = [
            e
            for e in events2
            if e["type"] == "issue_closed" and e["username"] == "loser"
        ]
        assert closed_evs == []

    @patch("update_leaderboard.arena_week_id", return_value="2026-W12")
    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open", return_value=False)
    def test_merged_pr_without_keyword_gets_no_credit(
        self, mock_open, mock_gather, mock_week_id
    ):
        """Merged PR lacking a closing keyword does not award points."""
        listed = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"bug": [issue]})
        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat()
        mock_gather.return_value = [
            _make_gathered_pr(
                author="drive-by",
                state="closed",
                merged=True,
                merged_at=datetime.now(timezone.utc).isoformat(),
                created_at=pr_created,
                has_closing_keyword=False,
            )
        ]

        credits, events, _ = process_week("2026-W11", week, scores)

        assert credits == []
        assert "org/proj#1" not in scores["credited_issues"]
        assert issue.get("closed") is True
        assert [e for e in events if e["type"] == "first_merge"] == []

    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open", return_value=True)
    def test_closed_unmerged_pr_does_not_welcome(self, mock_open, mock_gather):
        """A closed-unmerged PR must not retro-welcome its author."""
        listed = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue]})
        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        mock_gather.return_value = [
            _make_gathered_pr(
                author="driveby",
                state="closed",
                merged=False,
                created_at=pr_created,
            )
        ]

        credits, events, _ = process_week("2026-W11", week, scores)

        welcomes = [e for e in events if e["type"] == "welcome"]
        assert welcomes == []
        # Also no pr_closed (no prior welcome thread to comment on)
        assert [e for e in events if e["type"] == "pr_closed"] == []

    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open", return_value=True)
    def test_shared_welcomed_in_run_prevents_duplicate_across_weeks(
        self, mock_open, mock_gather
    ):
        """welcomed_in_run shared across weeks dedupes same-user welcome."""
        listed = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        issue_a = _make_issue(number=1, listed_at=listed)
        issue_b = _make_issue(number=2, listed_at=listed)
        scores = _make_scores()
        week_a = _make_week_data({"gfi": [issue_a]})
        week_b = _make_week_data({"gfi": [issue_b]})
        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        mock_gather.side_effect = [
            [
                _make_gathered_pr(
                    author="newcomer",
                    state="open",
                    merged=False,
                    pr_url="https://github.com/org/proj/pull/10",
                    created_at=pr_created,
                )
            ],
            [
                _make_gathered_pr(
                    author="newcomer",
                    state="open",
                    merged=False,
                    pr_url="https://github.com/org/proj/pull/11",
                    created_at=pr_created,
                )
            ],
        ]
        shared: set = set()
        _, ev_a, _ = process_week("2026-W10", week_a, scores, shared)
        _, ev_b, _ = process_week("2026-W11", week_b, scores, shared)

        welcomes = [
            e
            for e in (ev_a + ev_b)
            if e["type"] == "welcome" and e["username"] == "newcomer"
        ]
        assert len(welcomes) == 1

    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open", return_value=True)
    def test_same_run_welcome_allows_pr_closed(self, mock_open, mock_gather):
        """A user welcomed this run still receives pr_closed in same run."""
        listed = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        issue_a = _make_issue(number=1, listed_at=listed)
        issue_b = _make_issue(number=2, listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue_a, issue_b]})
        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        # Issue A: user opens a fresh PR (welcome).
        # Issue B: user already has a closed-unmerged PR (pr_closed).
        mock_gather.side_effect = [
            [
                _make_gathered_pr(
                    author="user",
                    state="open",
                    merged=False,
                    pr_url="https://github.com/org/proj/pull/10",
                    created_at=pr_created,
                )
            ],
            [
                _make_gathered_pr(
                    author="user",
                    state="closed",
                    merged=False,
                    pr_url="https://github.com/org/proj/pull/11",
                    created_at=pr_created,
                )
            ],
        ]

        _, events, _ = process_week("2026-W11", week, scores)

        types = [e["type"] for e in events]
        assert "welcome" in types
        assert "pr_closed" in types

    @patch("update_leaderboard.arena_week_id", return_value="2026-W12")
    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open", return_value=False)
    def test_ambiguous_multiple_merged_prs_no_credit(
        self, mock_open, mock_gather, mock_week_id
    ):
        """Multiple merged PRs with no commit-id match → no credit."""
        listed = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"bug": [issue]})
        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat()
        now_iso = datetime.now(timezone.utc).isoformat()
        # Two merged PRs, neither matching a closing commit — find_closing_pr
        # returns None to avoid misattribution.
        mock_gather.return_value = [
            _make_gathered_pr(
                author="a",
                state="closed",
                merged=True,
                merged_at=now_iso,
                pr_url="https://github.com/org/proj/pull/10",
                created_at=pr_created,
                merge_commit_sha="deadbeef",
            ),
            _make_gathered_pr(
                author="b",
                state="closed",
                merged=True,
                merged_at=now_iso,
                pr_url="https://github.com/org/proj/pull/11",
                created_at=pr_created,
                merge_commit_sha="cafebabe",
            ),
        ]

        with patch("update_leaderboard.github_get") as mock_github_get:
            # timeline returns no closed event with commit_id → None
            resp = type("R", (), {})()
            resp.json = lambda: []
            resp.raise_for_status = lambda: None
            resp.links = {}
            mock_github_get.return_value = resp

            credits, events, _ = process_week("2026-W11", week, scores)

        assert credits == []
        assert "org/proj#1" not in scores["credited_issues"]
        assert issue.get("closed") is True

    @patch("update_leaderboard.arena_week_id", return_value="2026-W12")
    @patch("update_leaderboard.gather_issue_prs")
    @patch("update_leaderboard.check_issue_still_open", return_value=False)
    def test_auto_welcome_when_merged_without_prior_welcome(
        self, mock_open, mock_gather, mock_week_id
    ):
        """Merged PR from a user never seen open → auto-welcome emitted."""
        listed = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"bug": [issue]})
        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat()
        mock_gather.return_value = [
            _make_gathered_pr(
                author="speedy",
                state="closed",
                merged=True,
                merged_at=datetime.now(timezone.utc).isoformat(),
                created_at=pr_created,
            )
        ]

        credits, events, _ = process_week("2026-W11", week, scores)

        types = [e["type"] for e in events]
        assert "welcome" in types
        assert "first_merge" in types
        # Welcome must come before first_merge in the emitted order for
        # the same user so the dispatcher creates the thread first.
        w_idx = next(
            i
            for i, e in enumerate(events)
            if e["type"] == "welcome" and e["username"] == "speedy"
        )
        m_idx = next(
            i
            for i, e in enumerate(events)
            if e["type"] == "first_merge" and e["username"] == "speedy"
        )
        assert w_idx < m_idx
        welcome_ev = events[w_idx]
        assert welcome_ev["pr_url"] == ("https://github.com/org/proj/pull/10")
        assert welcome_ev["issue_key"] == "org/proj#1"

    @patch("update_leaderboard.gather_issue_prs", return_value=None)
    @patch("update_leaderboard.check_issue_still_open", return_value=True)
    def test_timeline_fetch_failure_preserves_state(
        self, mock_open, mock_gather
    ):
        """gather_issue_prs returning None → process_week skips the issue."""
        listed = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue]})

        credits, events, _ = process_week("2026-W11", week, scores)

        assert credits == []
        assert events == []
        # Must not mark closed on a timeline glitch.
        assert not issue.get("closed")
        assert not issue.get("has_pr")


# ── build_leaderboard_md ─────────────────────────────────────


class TestBuildLeaderboardMd:
    """Tests for the build_leaderboard_md function."""

    def test_empty_players(self):
        """Empty players dict shows placeholder message."""
        scores = _make_scores()
        md = build_leaderboard_md(scores)
        assert "No contributions yet" in md
        assert "Position" in md

    def test_single_player(self):
        """Single player appears in the leaderboard."""
        scores = _make_scores(
            players={
                "alice": {
                    "total_points": 5,
                    "avatar_url": "https://a.png",
                    "contributions": [],
                },
            }
        )
        md = build_leaderboard_md(scores)
        assert "@alice" in md
        assert "| 1 |" in md

    def test_sorted_by_points_desc(self):
        """Players are sorted by points descending."""
        scores = _make_scores(
            players={
                "low": {
                    "total_points": 1,
                    "avatar_url": "https://l.png",
                    "contributions": [],
                },
                "high": {
                    "total_points": 10,
                    "avatar_url": "https://h.png",
                    "contributions": [],
                },
            }
        )
        md = build_leaderboard_md(scores)
        high_pos = md.index("@high")
        low_pos = md.index("@low")
        assert high_pos < low_pos

    def test_alphabetical_tiebreaker(self):
        """Same points are sorted alphabetically."""
        scores = _make_scores(
            players={
                "zara": {
                    "total_points": 5,
                    "avatar_url": "https://z.png",
                    "contributions": [],
                },
                "anna": {
                    "total_points": 5,
                    "avatar_url": "https://a.png",
                    "contributions": [],
                },
            }
        )
        md = build_leaderboard_md(scores)
        anna_pos = md.index("@anna")
        zara_pos = md.index("@zara")
        assert anna_pos < zara_pos

    def test_top_10_limit(self):
        """Only the top 10 players are shown."""
        players = {}
        for i in range(15):
            name = f"player{i:02d}"
            players[name] = {
                "total_points": 100 - i,
                "avatar_url": f"https://{name}.png",
                "contributions": [],
            }
        scores = _make_scores(players=players)
        md = build_leaderboard_md(scores)

        for i in range(10):
            name = f"player{i:02d}"
            assert f"@{name}" in md

        for i in range(10, 15):
            name = f"player{i:02d}"
            assert f"@{name}" not in md

    def test_rank_images_present(self):
        """Rank badge images appear for each player."""
        scores = _make_scores(
            players={
                "newbie": {
                    "total_points": 1,
                    "avatar_url": "https://n.png",
                    "contributions": [],
                },
            }
        )
        md = build_leaderboard_md(scores)
        assert "hwengineer.png" in md

    def test_zero_point_players_excluded(self):
        """Welcome-only (0-point) players don't appear on leaderboard."""
        scores = _make_scores(
            players={
                "alice": {
                    "total_points": 5,
                    "avatar_url": "https://a.png",
                    "contributions": [],
                },
                "welcomed_only": {
                    "total_points": 0,
                    "avatar_url": "https://w.png",
                    "contributions": [],
                    "discussion_node_id": "D_abc",
                },
            }
        )
        md = build_leaderboard_md(scores)
        assert "@alice" in md
        assert "@welcomed_only" not in md

    def test_all_zero_point_players_shows_placeholder(self):
        """Only 0-point players → placeholder row, not blank leaderboard."""
        scores = _make_scores(
            players={
                "welcomed_only": {
                    "total_points": 0,
                    "avatar_url": "https://w.png",
                    "contributions": [],
                    "discussion_node_id": "D_abc",
                },
            }
        )
        md = build_leaderboard_md(scores)
        assert "No contributions yet" in md
        assert "@welcomed_only" not in md


# ── build_merged_this_week_md ─────────────────────────────


class TestBuildMergedThisWeekMd:
    """Tests for build_merged_this_week_md."""

    @patch("update_leaderboard.arena_week_id")
    def test_no_contributors(self, mock_week_id):
        """No contributors yields fallback message."""
        mock_week_id.return_value = "2026-W12"
        scores = _make_scores()
        md = build_merged_this_week_md(scores)
        assert "No merged contributions" in md

    @patch("update_leaderboard.arena_week_id")
    def test_no_contributors_wrong_week(self, mock_week_id):
        """Contributors in other weeks are not shown."""
        mock_week_id.return_value = "2026-W12"
        scores = _make_scores(weekly={"2026-W10": ["alice"]})
        md = build_merged_this_week_md(scores)
        assert "No merged contributions" in md

    @patch("update_leaderboard.arena_week_id")
    def test_contributors_shown(self, mock_week_id):
        """Current week contributors appear as avatars."""
        mock_week_id.return_value = "2026-W12"
        scores = _make_scores(
            players={
                "alice": {
                    "total_points": 3,
                    "avatar_url": "https://alice.png",
                    "contributions": [],
                },
            },
            weekly={"2026-W12": ["alice"]},
        )
        md = build_merged_this_week_md(scores)
        assert "@alice" in md
        assert "https://alice.png" in md

    @patch("update_leaderboard.arena_week_id")
    def test_contributors_sorted_alphabetically(self, mock_week_id):
        """Weekly contributors are sorted by username."""
        mock_week_id.return_value = "2026-W12"
        scores = _make_scores(
            players={
                "zara": {
                    "total_points": 1,
                    "avatar_url": "https://z.png",
                    "contributions": [],
                },
                "anna": {
                    "total_points": 1,
                    "avatar_url": "https://a.png",
                    "contributions": [],
                },
            },
            weekly={"2026-W12": ["zara", "anna"]},
        )
        md = build_merged_this_week_md(scores)
        anna_pos = md.index("@anna")
        zara_pos = md.index("@zara")
        assert anna_pos < zara_pos


# ── update_issue_statuses ─────────────────────────────────────


class TestUpdateIssueStatuses:
    """Tests for the update_issue_statuses function."""

    @patch("update_leaderboard.check_issue_status")
    def test_replaces_open_with_closed(
        self, mock_status, tmp_path, monkeypatch
    ):
        """Status emoji is updated from open to closed."""
        current = [{"owner": "org", "repo": "proj", "number": 1}]
        f = tmp_path / "current_issues.json"
        f.write_text(json.dumps(current), encoding="utf-8")
        monkeypatch.setattr("update_leaderboard.CURRENT_ISSUES_PATH", f)
        mock_status.return_value = "🔴 Closed"

        url = "https://github.com/org/proj/issues/1"
        readme = f"| [Title]({url}) | repo | 🟢 Open |"
        result = update_issue_statuses(readme)

        assert "🔴 Closed" in result
        assert "🟢 Open" not in result

    @patch("update_leaderboard.check_issue_status")
    def test_replaces_closed_with_open(
        self, mock_status, tmp_path, monkeypatch
    ):
        """Status emoji is updated from closed to open."""
        current = [{"owner": "org", "repo": "proj", "number": 2}]
        f = tmp_path / "current_issues.json"
        f.write_text(json.dumps(current), encoding="utf-8")
        monkeypatch.setattr("update_leaderboard.CURRENT_ISSUES_PATH", f)
        mock_status.return_value = "🟢 Open"

        url = "https://github.com/org/proj/issues/2"
        readme = f"| [Title]({url}) | repo | 🔴 Closed |"
        result = update_issue_statuses(readme)

        assert "🟢 Open" in result
        assert "🔴 Closed" not in result

    def test_missing_current_issues_file_returns_readme_unchanged(
        self, tmp_path, monkeypatch
    ):
        """Missing current_issues.json returns readme unchanged."""
        monkeypatch.setattr(
            "update_leaderboard.CURRENT_ISSUES_PATH",
            tmp_path / "missing.json",
        )
        readme = "# README unchanged"
        assert update_issue_statuses(readme) == readme

    @patch("update_leaderboard.check_issue_status")
    def test_unrelated_issue_url_not_modified(
        self, mock_status, tmp_path, monkeypatch
    ):
        """Lines not matching the issue URL are not modified."""
        current = [{"owner": "org", "repo": "proj", "number": 3}]
        f = tmp_path / "current_issues.json"
        f.write_text(json.dumps(current), encoding="utf-8")
        monkeypatch.setattr("update_leaderboard.CURRENT_ISSUES_PATH", f)
        mock_status.return_value = "🔴 Closed"

        readme = (
            "| [Other](https://github.com/x/y/issues/99) | repo | 🟢 Open |"
        )
        result = update_issue_statuses(readme)

        assert result == readme  # no change — wrong issue URL

    @patch("update_leaderboard.check_issue_status")
    def test_multiple_issues_all_updated(
        self, mock_status, tmp_path, monkeypatch
    ):
        """All issues in the list have their status updated."""
        current = [
            {"owner": "org", "repo": "proj", "number": 1},
            {"owner": "org", "repo": "proj", "number": 2},
        ]
        f = tmp_path / "current_issues.json"
        f.write_text(json.dumps(current), encoding="utf-8")
        monkeypatch.setattr("update_leaderboard.CURRENT_ISSUES_PATH", f)
        mock_status.side_effect = ["🔴 Closed", "🟢 Open"]

        url1 = "https://github.com/org/proj/issues/1"
        url2 = "https://github.com/org/proj/issues/2"
        readme = (
            f"| [T1]({url1}) | r | 🟢 Open |\n| [T2]({url2}) | r | 🟢 Open |"
        )
        result = update_issue_statuses(readme)

        assert f"[T1]({url1}) | r | 🔴 Closed" in result
        assert f"[T2]({url2}) | r | 🟢 Open" in result

    @patch("update_leaderboard.check_issue_status")
    def test_has_pr_passed_to_check_issue_status(
        self, mock_status, tmp_path, monkeypatch
    ):
        """The has_pr field is forwarded to check_issue_status."""
        current = [
            {"owner": "org", "repo": "proj", "number": 5, "has_pr": True},
        ]
        f = tmp_path / "current_issues.json"
        f.write_text(json.dumps(current), encoding="utf-8")
        monkeypatch.setattr("update_leaderboard.CURRENT_ISSUES_PATH", f)
        mock_status.return_value = "🟡 PR Proposed"

        url = "https://github.com/org/proj/issues/5"
        readme = f"| [Title]({url}) | repo | 🟢 Open |"
        result = update_issue_statuses(readme)

        mock_status.assert_called_once_with("org", "proj", 5, has_pr=True)
        assert "🟡 PR Proposed" in result
        assert "🟢 Open" not in result

    @patch("update_leaderboard.check_issue_status")
    def test_pr_proposed_replaced_with_closed(
        self, mock_status, tmp_path, monkeypatch
    ):
        """PR Proposed status is replaced when issue is closed."""
        current = [
            {"owner": "org", "repo": "proj", "number": 6, "has_pr": True},
        ]
        f = tmp_path / "current_issues.json"
        f.write_text(json.dumps(current), encoding="utf-8")
        monkeypatch.setattr("update_leaderboard.CURRENT_ISSUES_PATH", f)
        mock_status.return_value = "🔴 Closed"

        url = "https://github.com/org/proj/issues/6"
        readme = f"| [Title]({url}) | repo | 🟡 PR Proposed |"
        result = update_issue_statuses(readme)

        assert "🔴 Closed" in result
        assert "🟡 PR Proposed" not in result


# ── check_issue_status PR Proposed ───────────────────────────


class TestCheckIssueStatusPrProposed:
    """Tests for the has_pr parameter of check_issue_status."""

    @patch("update_leaderboard.github_get")
    def test_open_with_has_pr_returns_pr_proposed(self, mock_get):
        """Open issue with has_pr=True returns PR Proposed."""
        mock_get.return_value.json.return_value = {"state": "open"}
        mock_get.return_value.raise_for_status = lambda: None
        result = check_issue_status("o", "r", 1, has_pr=True)
        assert result == "🟡 PR Proposed"

    @patch("update_leaderboard.github_get")
    def test_closed_overrides_has_pr(self, mock_get):
        """Closed issue returns Closed even with has_pr=True."""
        mock_get.return_value.json.return_value = {"state": "closed"}
        mock_get.return_value.raise_for_status = lambda: None
        result = check_issue_status("o", "r", 1, has_pr=True)
        assert result == "🔴 Closed"

    @patch("update_leaderboard.github_get")
    def test_open_without_has_pr_returns_open(self, mock_get):
        """Open issue without has_pr returns Open."""
        mock_get.return_value.json.return_value = {"state": "open"}
        mock_get.return_value.raise_for_status = lambda: None
        result = check_issue_status("o", "r", 1, has_pr=False)
        assert result == "🟢 Open"


# ── update_arena_level ────────────────────────────────────────


class TestUpdateArenaLevel:
    """Tests for level detection and milestones write-back."""

    @staticmethod
    def _stub_config_and_milestones(monkeypatch, tmp_path, milestones=None):
        """Point arena_level helpers at a tmp milestones file + stub config."""
        config = {
            "version": 1,
            "baseline": {"gfi": 20, "bug": 14, "hard": 10},
            "levels": [
                {
                    "level": 0,
                    "threshold": 0,
                    "bonus": {"gfi": 0, "bug": 0, "hard": 0},
                },
                {
                    "level": 1,
                    "threshold": 25,
                    "bonus": {"gfi": 1, "bug": 1, "hard": 1},
                },
                {
                    "level": 2,
                    "threshold": 75,
                    "bonus": {"gfi": 2, "bug": 2, "hard": 2},
                },
                {
                    "level": 3,
                    "threshold": 150,
                    "bonus": {"gfi": 3, "bug": 3, "hard": 3},
                },
            ],
        }
        path = tmp_path / "milestones.json"
        if milestones is not None:
            path.write_text(json.dumps(milestones), encoding="utf-8")

        def _load_milestones():
            return arena_level.load_milestones(path)

        monkeypatch.setattr(
            "update_leaderboard.load_milestones", _load_milestones
        )
        monkeypatch.setattr(
            "update_leaderboard.load_levels_config", lambda: config
        )
        return path

    def test_no_level_up_when_below_threshold(self, tmp_path, monkeypatch):
        """Below the next threshold leaves the level untouched."""
        path = self._stub_config_and_milestones(
            monkeypatch,
            tmp_path,
            milestones={
                "current_level": 0,
                "current_arena_points": 10,
                "history": [],
            },
        )
        scores = {"players": {"alice": {"total_points": 24}}}
        milestones, _, level_ups = update_arena_level(scores)
        assert milestones["current_level"] == 0
        assert milestones["current_arena_points"] == 24
        assert level_ups == []
        # File untouched until save_milestones is called explicitly.
        assert path.exists()

    def test_single_level_up_appends_history(self, tmp_path, monkeypatch):
        """Crossing one threshold appends a single history entry."""
        self._stub_config_and_milestones(
            monkeypatch,
            tmp_path,
            milestones={
                "current_level": 0,
                "current_arena_points": 24,
                "history": [],
            },
        )
        scores = {
            "players": {
                "alice": {"total_points": 12},
                "bob": {"total_points": 13},
            }
        }
        milestones, _, level_ups = update_arena_level(scores)
        assert milestones["current_level"] == 1
        assert milestones["current_arena_points"] == 25
        assert len(level_ups) == 1
        assert level_ups[0]["level"] == 1
        assert level_ups[0]["threshold"] == 25
        assert level_ups[0]["announced"] is False
        assert milestones["history"][-1]["level"] == 1

    def test_multi_level_jump_records_each(self, tmp_path, monkeypatch):
        """Crossing multiple thresholds in one run records all of them."""
        self._stub_config_and_milestones(
            monkeypatch,
            tmp_path,
            milestones={
                "current_level": 0,
                "current_arena_points": 0,
                "history": [],
            },
        )
        scores = {"players": {"alice": {"total_points": 160}}}
        milestones, _, level_ups = update_arena_level(scores)
        assert milestones["current_level"] == 3
        assert [lu["level"] for lu in level_ups] == [1, 2, 3]
        assert len(milestones["history"]) == 3

    def test_no_level_up_at_max(self, tmp_path, monkeypatch):
        """Already at max level → no new history entries."""
        self._stub_config_and_milestones(
            monkeypatch,
            tmp_path,
            milestones={
                "current_level": 3,
                "current_arena_points": 200,
                "history": [],
            },
        )
        scores = {"players": {"alice": {"total_points": 500}}}
        milestones, _, level_ups = update_arena_level(scores)
        assert milestones["current_level"] == 3
        assert level_ups == []

    def test_first_run_with_no_milestones_file(self, tmp_path, monkeypatch):
        """No milestones.json on disk → starts at level 0 then climbs."""
        self._stub_config_and_milestones(monkeypatch, tmp_path)
        scores = {"players": {"alice": {"total_points": 80}}}
        milestones, _, level_ups = update_arena_level(scores)
        assert milestones["current_level"] == 2
        assert [lu["level"] for lu in level_ups] == [1, 2]
