"""Tests for scripts/update_leaderboard.py."""

import json
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

sys.path.insert(0, "scripts")

from update_leaderboard import (  # noqa: E402, I001
    build_leaderboard_md,
    build_merged_this_week_md,
    get_rank,
    has_pending_pr,
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


# ── has_pending_pr ───────────────────────────────────────────


class TestHasPendingPr:
    """Tests for the has_pending_pr function."""

    @patch("update_leaderboard.github_get")
    def test_open_pr_within_deadline(self, mock_get):
        """An open PR created before the deadline returns True."""
        deadline = datetime(2026, 3, 20, 17, 0, tzinfo=timezone.utc)
        resp = mock_get.return_value
        resp.raise_for_status.return_value = None
        resp.json.return_value = [
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "state": "open",
                        "pull_request": {"merged_at": None},
                        "created_at": "2026-03-18T10:00:00Z",
                    }
                },
            }
        ]
        resp.links = {}
        assert has_pending_pr("org", "repo", 1, deadline) is True

    @patch("update_leaderboard.github_get")
    def test_pr_created_after_deadline(self, mock_get):
        """A PR created after the deadline returns False."""
        deadline = datetime(2026, 3, 20, 17, 0, tzinfo=timezone.utc)
        resp = mock_get.return_value
        resp.raise_for_status.return_value = None
        resp.json.return_value = [
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "state": "open",
                        "pull_request": {"merged_at": None},
                        "created_at": "2026-03-25T10:00:00Z",
                    }
                },
            }
        ]
        resp.links = {}
        assert has_pending_pr("org", "repo", 1, deadline) is False

    @patch("update_leaderboard.github_get")
    def test_closed_rejected_pr_ignored(self, mock_get):
        """A PR closed without merge (rejected) is ignored."""
        deadline = datetime(2026, 3, 20, 17, 0, tzinfo=timezone.utc)
        resp = mock_get.return_value
        resp.raise_for_status.return_value = None
        resp.json.return_value = [
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "state": "closed",
                        "pull_request": {"merged_at": None},
                        "created_at": "2026-03-18T10:00:00Z",
                    }
                },
            }
        ]
        resp.links = {}
        assert has_pending_pr("org", "repo", 1, deadline) is False

    @patch("update_leaderboard.github_get")
    def test_merged_pr_without_close_ignored(self, mock_get):
        """A merged PR that didn't auto-close the issue is ignored."""
        deadline = datetime(2026, 3, 20, 17, 0, tzinfo=timezone.utc)
        resp = mock_get.return_value
        resp.raise_for_status.return_value = None
        resp.json.return_value = [
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "state": "closed",
                        "pull_request": {
                            "merged_at": "2026-03-22T12:00:00Z",
                        },
                        "created_at": "2026-03-18T10:00:00Z",
                    }
                },
            }
        ]
        resp.links = {}
        assert has_pending_pr("org", "repo", 1, deadline) is False

    @patch("update_leaderboard.github_get")
    def test_non_pr_cross_reference_ignored(self, mock_get):
        """A cross-reference to a plain issue (not a PR) is ignored."""
        deadline = datetime(2026, 3, 20, 17, 0, tzinfo=timezone.utc)
        resp = mock_get.return_value
        resp.raise_for_status.return_value = None
        resp.json.return_value = [
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "state": "open",
                        "created_at": "2026-03-18T10:00:00Z",
                    }
                },
            }
        ]
        resp.links = {}
        assert has_pending_pr("org", "repo", 1, deadline) is False

    @patch("update_leaderboard.github_get")
    def test_api_error_returns_false(self, mock_get):
        """API failure returns False (safe default)."""
        deadline = datetime(2026, 3, 20, 17, 0, tzinfo=timezone.utc)
        mock_get.side_effect = Exception("network error")
        assert has_pending_pr("org", "repo", 1, deadline) is False

    @patch("update_leaderboard.github_get")
    def test_empty_timeline_returns_false(self, mock_get):
        """Empty timeline (no events) returns False."""
        deadline = datetime(2026, 3, 20, 17, 0, tzinfo=timezone.utc)
        resp = mock_get.return_value
        resp.raise_for_status.return_value = None
        resp.json.return_value = []
        resp.links = {}
        assert has_pending_pr("org", "repo", 1, deadline) is False

    @patch("update_leaderboard.github_get")
    def test_pr_created_exactly_at_deadline(self, mock_get):
        """PR created exactly at the deadline counts (boundary <=)."""
        deadline = datetime(2026, 3, 20, 17, 0, 0, tzinfo=timezone.utc)
        resp = mock_get.return_value
        resp.raise_for_status.return_value = None
        resp.json.return_value = [
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "state": "open",
                        "pull_request": {"merged_at": None},
                        "created_at": "2026-03-20T17:00:00Z",  # == deadline
                    }
                },
            }
        ]
        resp.links = {}
        assert has_pending_pr("org", "repo", 1, deadline) is True

    @patch("update_leaderboard.github_get")
    def test_pr_created_one_second_after_deadline(self, mock_get):
        """PR created one second after the deadline does not count."""
        deadline = datetime(2026, 3, 20, 17, 0, 0, tzinfo=timezone.utc)
        resp = mock_get.return_value
        resp.raise_for_status.return_value = None
        resp.json.return_value = [
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "state": "open",
                        "pull_request": {"merged_at": None},
                        "created_at": "2026-03-20T17:00:01Z",  # 1s after
                    }
                },
            }
        ]
        resp.links = {}
        assert has_pending_pr("org", "repo", 1, deadline) is False

    @patch("update_leaderboard.github_get")
    def test_multiple_prs_one_valid_returns_true(self, mock_get):
        """Multiple PRs — one rejected + one open within deadline → True."""
        deadline = datetime(2026, 3, 20, 17, 0, tzinfo=timezone.utc)
        resp = mock_get.return_value
        resp.raise_for_status.return_value = None
        resp.json.return_value = [
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "state": "closed",  # rejected
                        "pull_request": {"merged_at": None},
                        "created_at": "2026-03-15T10:00:00Z",
                    }
                },
            },
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "state": "open",  # still open
                        "pull_request": {"merged_at": None},
                        "created_at": "2026-03-17T10:00:00Z",
                    }
                },
            },
        ]
        resp.links = {}
        assert has_pending_pr("org", "repo", 1, deadline) is True

    @patch("update_leaderboard.github_get")
    def test_multiple_prs_all_rejected_returns_false(self, mock_get):
        """Multiple PRs all rejected (closed without merge) → False."""
        deadline = datetime(2026, 3, 20, 17, 0, tzinfo=timezone.utc)
        resp = mock_get.return_value
        resp.raise_for_status.return_value = None
        resp.json.return_value = [
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "state": "closed",
                        "pull_request": {"merged_at": None},
                        "created_at": "2026-03-14T10:00:00Z",
                    }
                },
            },
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "state": "closed",
                        "pull_request": {"merged_at": None},
                        "created_at": "2026-03-16T10:00:00Z",
                    }
                },
            },
        ]
        resp.links = {}
        assert has_pending_pr("org", "repo", 1, deadline) is False

    @patch("update_leaderboard.github_get")
    def test_paginated_timeline_pr_on_second_page(self, mock_get):
        """PR found on the second page of a paginated timeline → True."""
        deadline = datetime(2026, 3, 20, 17, 0, tzinfo=timezone.utc)

        page1 = Mock()
        page1.raise_for_status.return_value = None
        page1.json.return_value = [{"event": "labeled"}]
        page1.links = {"next": {"url": "https://api.github.com/page2"}}

        page2 = Mock()
        page2.raise_for_status.return_value = None
        page2.json.return_value = [
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "state": "open",
                        "pull_request": {"merged_at": None},
                        "created_at": "2026-03-18T10:00:00Z",
                    }
                },
            }
        ]
        page2.links = {}

        mock_get.side_effect = [page1, page2]
        assert has_pending_pr("org", "repo", 1, deadline) is True

    @patch("update_leaderboard.github_get")
    def test_non_cross_referenced_events_ignored(self, mock_get):
        """Non-cross-referenced events (labeled, assigned, etc.) ignored."""
        deadline = datetime(2026, 3, 20, 17, 0, tzinfo=timezone.utc)
        resp = mock_get.return_value
        resp.raise_for_status.return_value = None
        resp.json.return_value = [
            {"event": "labeled", "label": {"name": "bug"}},
            {"event": "assigned", "assignee": {"login": "alice"}},
            {"event": "commented", "body": "PR incoming!"},
        ]
        resp.links = {}
        assert has_pending_pr("org", "repo", 1, deadline) is False


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


class TestProcessWeek:
    """Tests for the process_week function."""

    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_already_credited_issue_gets_closed_flag(self, mock_open, mock_pr):
        """Already credited issues are marked closed=True without API calls."""
        issue = _make_issue()
        key = "org/proj#1"
        scores = _make_scores(credited=[key])
        week = _make_week_data({"gfi": [issue]})

        result = process_week("2026-W11", week, scores)

        assert result == []
        assert len(week["issues"]["gfi"]) == 1
        assert week["issues"]["gfi"][0]["closed"] is True
        mock_open.assert_not_called()
        mock_pr.assert_not_called()

    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_open_within_window_skipped(self, mock_open, mock_pr):
        """Open issue within 7-day window is skipped."""
        listed = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue]})
        mock_open.return_value = True

        result = process_week("2026-W11", week, scores)

        assert result == []
        assert "org/proj#1" not in scores["credited_issues"]
        mock_pr.assert_not_called()

    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.has_pending_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_open_past_window_no_pr_gets_closed_flag(
        self, mock_open, mock_pending, mock_pr
    ):
        """Open issue past 7-day window with no PR is marked closed=True."""
        listed = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue]})
        mock_open.return_value = True
        mock_pending.return_value = False

        result = process_week("2026-W11", week, scores)

        assert result == []
        assert len(week["issues"]["gfi"]) == 1
        assert week["issues"]["gfi"][0].get("closed") is True
        assert "org/proj#1" not in scores["credited_issues"]
        mock_pr.assert_not_called()

    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.has_pending_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_open_past_window_with_pr_kept(
        self, mock_open, mock_pending, mock_pr
    ):
        """Open issue past 7-day window with a pending PR is kept."""
        listed = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue]})
        mock_open.return_value = True
        mock_pending.return_value = True

        result = process_week("2026-W11", week, scores)

        assert result == []
        assert len(week["issues"]["gfi"]) == 1
        assert "org/proj#1" not in scores["credited_issues"]
        mock_pr.assert_not_called()

    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_closed_no_pr_gets_closed_flag(self, mock_open, mock_pr):
        """Closed issue with no closing PR is marked closed=True."""
        issue = _make_issue()
        scores = _make_scores()
        week = _make_week_data({"bug": [issue]})
        mock_open.return_value = False
        mock_pr.return_value = None

        result = process_week("2026-W11", week, scores)

        assert result == []
        assert "org/proj#1" not in scores["credited_issues"]
        assert len(week["issues"]["bug"]) == 1
        assert week["issues"]["bug"][0].get("closed") is True

    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_closed_pr_opened_too_late_gets_closed_flag(
        self, mock_open, mock_pr
    ):
        """PR opened after 7-day window marks issue closed=True."""
        listed = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"hard": [issue]})
        mock_open.return_value = False

        late_created = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        mock_pr.return_value = _make_pr(created_at=late_created)

        result = process_week("2026-W11", week, scores)

        assert result == []
        assert len(week["issues"]["hard"]) == 1
        assert week["issues"]["hard"][0].get("closed") is True
        assert "org/proj#1" not in scores["credited_issues"]

    @patch("update_leaderboard.arena_week_id")
    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_valid_pr_awards_points(self, mock_open, mock_pr, mock_week_id):
        """Valid PR awards points and creates player entry."""
        listed = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"bug": [issue]})
        mock_open.return_value = False

        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat()
        mock_pr.return_value = _make_pr(
            author="hero",
            avatar="https://avatar.example.com/hero.png",
            created_at=pr_created,
        )
        mock_week_id.return_value = "2026-W12"

        result = process_week("2026-W11", week, scores)

        assert len(result) == 1
        credit = result[0]
        assert credit["author"] == "hero"
        assert credit["pts"] == 2
        assert credit["issue"] == "org/proj#1"
        assert credit["week"] == "2026-W11"

        player = scores["players"]["hero"]
        assert player["total_points"] == 2
        assert player["avatar_url"] == ("https://avatar.example.com/hero.png")
        assert len(player["contributions"]) == 1
        contrib = player["contributions"][0]
        assert contrib["week"] == "2026-W11"
        assert contrib["points"] == 2

        assert "org/proj#1" in scores["credited_issues"]
        assert len(week["issues"]["bug"]) == 1
        assert week["issues"]["bug"][0].get("closed") is True
        assert "hero" in scores["weekly"]["2026-W12"]

    @patch("update_leaderboard.arena_week_id")
    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_valid_pr_updates_existing_player(
        self, mock_open, mock_pr, mock_week_id
    ):
        """Valid PR updates an existing player's points."""
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
        }
        scores = _make_scores(players={"hero": existing_player})
        week = _make_week_data({"hard": [issue]})
        mock_open.return_value = False

        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        mock_pr.return_value = _make_pr(
            author="hero",
            avatar="https://new.png",
            created_at=pr_created,
        )
        mock_week_id.return_value = "2026-W12"

        result = process_week("2026-W11", week, scores)

        assert len(result) == 1
        player = scores["players"]["hero"]
        assert player["total_points"] == 7
        assert player["avatar_url"] == "https://new.png"
        assert len(player["contributions"]) == 2

    @patch("update_leaderboard.arena_week_id")
    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_multiple_categories_processed(
        self, mock_open, mock_pr, mock_week_id
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
        mock_open.return_value = False

        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        mock_pr.side_effect = [
            _make_pr(
                author="alice",
                created_at=pr_created,
            ),
            _make_pr(
                author="bob",
                created_at=pr_created,
            ),
        ]
        mock_week_id.return_value = "2026-W12"

        result = process_week("2026-W11", week, scores)

        assert len(result) == 2
        authors = {c["author"] for c in result}
        assert authors == {"alice", "bob"}

        assert result[0]["pts"] == 1  # gfi
        assert result[1]["pts"] == 2  # bug

    @patch("update_leaderboard.has_pending_pr")
    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_has_pending_pr_not_called_within_window(
        self, mock_open, mock_pr, mock_pending
    ):
        """has_pending_pr is not called when issue is within 7-day window."""
        listed = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue]})
        mock_open.return_value = True

        process_week("2026-W11", week, scores)

        mock_pending.assert_not_called()

    @patch("update_leaderboard.has_pending_pr")
    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_has_pending_pr_not_called_when_issue_closed(
        self, mock_open, mock_pr, mock_pending
    ):
        """has_pending_pr is not called when the issue is already closed."""
        listed = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue]})
        mock_open.return_value = False
        mock_pr.return_value = None

        process_week("2026-W11", week, scores)

        mock_pending.assert_not_called()

    @patch("update_leaderboard.has_pending_pr")
    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_open_past_window_merged_without_close_gets_closed_flag(
        self, mock_open, mock_closing_pr, mock_pending
    ):
        """Open past window with merged-without-close PR is marked closed."""
        listed = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue]})
        mock_open.return_value = True
        mock_pending.return_value = (
            False  # has_pending_pr: merged PR doesn't count
        )

        process_week("2026-W11", week, scores)

        assert len(week["issues"]["gfi"]) == 1
        assert week["issues"]["gfi"][0].get("closed") is True
        assert "org/proj#1" not in scores["credited_issues"]

    @patch("update_leaderboard.arena_week_id")
    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.has_pending_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_pending_pr_tracked_then_eventually_credited(
        self, mock_open, mock_pending, mock_pr, mock_week_id
    ):
        """Issue kept via pending PR on run 1 is credited on run 2."""
        listed = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"bug": [issue]})
        mock_week_id.return_value = "2026-W12"

        # Run 1: issue open past window but has a pending PR — kept
        mock_open.return_value = True
        mock_pending.return_value = True

        result1 = process_week("2026-W11", week, scores)

        assert result1 == []
        assert len(week["issues"]["bug"]) == 1  # still tracked

        # Run 2: issue is now closed, PR was opened within window — credited
        mock_open.return_value = False
        pr_created = (
            datetime.now(timezone.utc) - timedelta(days=18)
        ).isoformat()  # within 7-day window from listing
        mock_pr.return_value = _make_pr(author="hero", created_at=pr_created)

        result2 = process_week("2026-W11", week, scores)

        assert len(result2) == 1
        assert result2[0]["author"] == "hero"
        assert result2[0]["pts"] == 2
        assert len(week["issues"]["bug"]) == 1
        assert week["issues"]["bug"][0].get("closed") is True
        assert "org/proj#1" in scores["credited_issues"]

    @patch("update_leaderboard.has_pending_pr")
    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_missing_listed_at_falls_back_to_fetched_at(
        self, mock_open, mock_pr, mock_pending
    ):
        """Issue without listed_at uses fetched_at to compute the deadline."""
        # fetched_at is 10 days ago → deadline 3 days ago → past window
        fetched_at = (
            datetime.now(timezone.utc) - timedelta(days=10)
        ).isoformat()
        issue = {
            "owner": "org",
            "repo": "proj",
            "number": 1,
            # no listed_at
        }
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue]}, fetched_at=fetched_at)
        mock_open.return_value = True
        mock_pending.return_value = False

        process_week("2026-W11", week, scores)

        assert len(week["issues"]["gfi"]) == 1
        assert week["issues"]["gfi"][0].get("closed") is True
        mock_pending.assert_called_once()

    @patch("update_leaderboard.has_pending_pr")
    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_two_issues_one_with_pr_one_without(
        self, mock_open, mock_pr, mock_pending
    ):
        """Pending-PR issue stays open; issue without PR gets closed=True."""
        listed = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        issue_a = _make_issue(number=1, listed_at=listed)
        issue_b = _make_issue(number=2, listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue_a, issue_b]})
        mock_open.return_value = True
        mock_pending.side_effect = [
            True,
            False,
        ]  # issue_a kept open, issue_b marked closed

        process_week("2026-W11", week, scores)

        remaining = week["issues"]["gfi"]
        assert len(remaining) == 2
        assert remaining[0]["number"] == 1
        assert not remaining[0].get("closed")
        assert remaining[1]["number"] == 2
        assert remaining[1].get("closed") is True

    @patch("update_leaderboard.has_pending_pr")
    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_deadline_exactly_now_not_expired(
        self, mock_open, mock_pr, mock_pending
    ):
        """Deadline in the future is not expired (strict > check)."""
        # listed_at = now - 7 days + 1 min → deadline = now + 1 min
        listed = (
            datetime.now(timezone.utc)
            - timedelta(days=7)
            + timedelta(minutes=1)
        ).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue]})
        mock_open.return_value = True

        process_week("2026-W11", week, scores)

        # has_pending_pr must NOT have been called — within window
        mock_pending.assert_not_called()
        assert len(week["issues"]["gfi"]) == 1

    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.has_pending_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_expired_issue_gets_closed_flag(
        self, mock_open, mock_pending, mock_pr
    ):
        """Open issue past 7-day window with no PR gets closed=True flag."""
        listed = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue]})
        mock_open.return_value = True
        mock_pending.return_value = False

        process_week("2026-W11", week, scores)

        assert len(week["issues"]["gfi"]) == 1
        assert week["issues"]["gfi"][0].get("closed") is True
        assert "org/proj#1" not in scores["credited_issues"]

    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_late_pr_issue_gets_closed_flag(self, mock_open, mock_pr):
        """Issue with a PR opened after the 7-day window gets closed=True."""
        listed = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"bug": [issue]})
        mock_open.return_value = False
        late_created = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        mock_pr.return_value = _make_pr(created_at=late_created)

        process_week("2026-W11", week, scores)

        assert len(week["issues"]["bug"]) == 1
        assert week["issues"]["bug"][0].get("closed") is True
        assert "org/proj#1" not in scores["credited_issues"]

    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_no_closing_pr_gets_closed_flag(self, mock_open, mock_pr):
        """Issue closed on GitHub with no qualifying PR gets closed=True."""
        issue = _make_issue()
        scores = _make_scores()
        week = _make_week_data({"hard": [issue]})
        mock_open.return_value = False
        mock_pr.return_value = None

        process_week("2026-W11", week, scores)

        assert len(week["issues"]["hard"]) == 1
        assert week["issues"]["hard"][0].get("closed") is True
        assert "org/proj#1" not in scores["credited_issues"]

    @patch("update_leaderboard.arena_week_id")
    def test_old_week_closed_issues_purged(self, mock_week_id):
        """Cleanup step removes closed=True issues from non-current weeks."""
        mock_week_id.return_value = "2026-W12"

        # W11: one closed issue, one open (pending PR)
        closed_issue = _make_issue(number=1)
        closed_issue["closed"] = True
        open_issue = _make_issue(number=2)

        state = {
            "2026-W11": {
                "fetched_at": "2026-03-13T17:00:00+00:00",
                "issues": {
                    "gfi": [closed_issue, open_issue],
                    "bug": [],
                    "hard": [],
                },
            }
        }

        # Apply the same cleanup logic used in main()
        current_week = mock_week_id.return_value
        for week_id, week_data in state.items():
            if week_id == current_week:
                continue
            for cat_issues in week_data["issues"].values():
                cat_issues[:] = [i for i in cat_issues if not i.get("closed")]

        remaining = state["2026-W11"]["issues"]["gfi"]
        assert len(remaining) == 1
        assert remaining[0]["number"] == 2
        assert not remaining[0].get("closed")

    @patch("update_leaderboard.arena_week_id")
    def test_current_week_closed_issues_not_purged(self, mock_week_id):
        """Cleanup step keeps closed=True issues in the current week."""
        mock_week_id.return_value = "2026-W12"

        closed_issue = _make_issue(number=1)
        closed_issue["closed"] = True

        state = {
            "2026-W12": {
                "fetched_at": "2026-03-20T17:00:00+00:00",
                "issues": {"gfi": [closed_issue], "bug": [], "hard": []},
            }
        }

        # Apply the same cleanup logic used in main()
        current_week = mock_week_id.return_value
        for week_id, week_data in state.items():
            if week_id == current_week:
                continue
            for cat_issues in week_data["issues"].values():
                cat_issues[:] = [i for i in cat_issues if not i.get("closed")]

        remaining = state["2026-W12"]["issues"]["gfi"]
        assert len(remaining) == 1
        assert remaining[0].get("closed") is True


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
