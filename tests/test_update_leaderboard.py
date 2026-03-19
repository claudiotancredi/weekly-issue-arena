"""Tests for scripts/update_leaderboard.py."""

import json
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, "scripts")

from update_leaderboard import (  # noqa: E402, I001
    build_leaderboard_md,
    build_weekly_contributors_md,
    get_rank,
    load_scores,
    load_state,
    process_week,
    save_scores,
    save_state,
)


# ── get_rank ─────────────────────────────────────────────────


class TestGetRank:
    """Tests for the get_rank helper."""

    def test_zero_points(self):
        """Zero points yields HW Engineer."""
        assert get_rank(0) == "HW Engineer"

    def test_just_below_bug_slayer(self):
        """99 points is still HW Engineer."""
        assert get_rank(99) == "HW Engineer"

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
    def test_already_credited_issue_skipped(self, mock_open, mock_pr):
        """Already credited issues are skipped entirely."""
        issue = _make_issue()
        key = "org/proj#1"
        scores = _make_scores(credited=[key])
        week = _make_week_data({"gfi": [issue]})

        result = process_week("2026-W11", week, scores)

        assert result == []
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
    @patch("update_leaderboard.check_issue_still_open")
    def test_open_past_window_marked_ineligible(self, mock_open, mock_pr):
        """Open issue past 7-day window is marked ineligible."""
        listed = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        issue = _make_issue(listed_at=listed)
        scores = _make_scores()
        week = _make_week_data({"gfi": [issue]})
        mock_open.return_value = True

        result = process_week("2026-W11", week, scores)

        assert result == []
        assert "org/proj#1" in scores["credited_issues"]
        mock_pr.assert_not_called()

    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_closed_no_pr_skipped(self, mock_open, mock_pr):
        """Closed issue with no closing PR is skipped."""
        issue = _make_issue()
        scores = _make_scores()
        week = _make_week_data({"bug": [issue]})
        mock_open.return_value = False
        mock_pr.return_value = None

        result = process_week("2026-W11", week, scores)

        assert result == []
        assert "org/proj#1" not in scores["credited_issues"]

    @patch("update_leaderboard.get_closing_pr")
    @patch("update_leaderboard.check_issue_still_open")
    def test_closed_pr_opened_too_late(self, mock_open, mock_pr):
        """PR opened after 7-day window marks ineligible."""
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
        assert "org/proj#1" in scores["credited_issues"]

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


# ── build_weekly_contributors_md ─────────────────────────────


class TestBuildWeeklyContributorsMd:
    """Tests for build_weekly_contributors_md."""

    @patch("update_leaderboard.arena_week_id")
    def test_no_contributors(self, mock_week_id):
        """No contributors yields fallback message."""
        mock_week_id.return_value = "2026-W12"
        scores = _make_scores()
        md = build_weekly_contributors_md(scores)
        assert "No contributions tracked" in md

    @patch("update_leaderboard.arena_week_id")
    def test_no_contributors_wrong_week(self, mock_week_id):
        """Contributors in other weeks are not shown."""
        mock_week_id.return_value = "2026-W12"
        scores = _make_scores(weekly={"2026-W10": ["alice"]})
        md = build_weekly_contributors_md(scores)
        assert "No contributions tracked" in md

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
        md = build_weekly_contributors_md(scores)
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
        md = build_weekly_contributors_md(scores)
        anna_pos = md.index("@anna")
        zara_pos = md.index("@zara")
        assert anna_pos < zara_pos
