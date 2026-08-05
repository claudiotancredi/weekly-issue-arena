"""Tests for opt-in gating across the leaderboard pipeline.

Consent is the load-bearing property of the arena: a contributor who never
submitted the *Join the Arena* form must cost nothing (no API request), earn
nothing, appear nowhere, and be mentioned nowhere. These tests pin that.
"""

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, "scripts")

from update_leaderboard import (  # noqa: E402, I001
    _collect_welcome_events,
    _stub_qualifies,
    build_leaderboard_md,
    build_merged_this_week_md,
    gather_issue_prs,
    process_week,
    update_arena_level,
)
from utils import arena_week_id  # noqa: E402, I001


# ── helpers ──────────────────────────────────────────────────

PR_API_URL = "https://api.github.com/repos/org/proj/pulls/10"


def _prefs(*usernames, discussion=True):
    """Preferences doc granting arena consent to *usernames*."""
    return {
        "version": 1,
        "users": {
            u: {
                "leaderboard": True,
                "discussion": discussion,
                "notifications_ack": True,
            }
            for u in usernames
        },
        "processed_issues": [],
    }


def _timeline_response(events):
    return type(
        "R",
        (),
        {
            "raise_for_status": lambda self: None,
            "json": lambda self: events,
            "links": {},
        },
    )()


def _cross_ref_event(
    author="outsider",
    state="open",
    body="Fixes #1",
    created_at=None,
    api_url=PR_API_URL,
):
    """A timeline cross-reference carrying an inline PR payload.

    GitHub embeds enough of the PR here (author, state, body, timestamps)
    that the arena can judge a non-participant's claim without ever
    fetching the PR itself.
    """
    if created_at is None:
        created_at = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat()
    return {
        "event": "cross-referenced",
        "source": {
            "issue": {
                "pull_request": {"url": api_url},
                "user": {"login": author},
                "state": state,
                "title": "Fix the thing",
                "body": body,
                "created_at": created_at,
                "html_url": "https://github.com/org/proj/pull/10",
            }
        },
    }


def _pr_payload(author="member"):
    now = datetime.now(timezone.utc)
    return {
        "number": 10,
        "user": {
            "login": author,
            "avatar_url": "https://avatar.example.com/a.png",
        },
        "html_url": "https://github.com/org/proj/pull/10",
        "state": "open",
        "merged": False,
        "merged_at": None,
        "created_at": (now - timedelta(hours=2)).isoformat(),
        "merge_commit_sha": None,
        "title": "Fix the thing",
        "body": "Fixes #1",
    }


def _make_issue(number=1):
    return {
        "owner": "org",
        "repo": "proj",
        "number": number,
        "listed_at": (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat(),
        "title": f"Issue #{number}",
        "url": f"https://github.com/org/proj/issues/{number}",
    }


def _make_week(issues):
    return {
        "fetched_at": (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat(),
        "issues": {"gfi": issues, "bug": [], "hard": []},
    }


def _make_scores(players=None, weekly=None):
    return {
        "players": players or {},
        "credited_issues": [],
        "weekly": weekly or {},
    }


def _gathered_pr(author="member", **overrides):
    now = datetime.now(timezone.utc)
    pr = {
        "number": 10,
        "author": author,
        "author_avatar": "https://avatar.example.com/a.png",
        "pr_url": "https://github.com/org/proj/pull/10",
        "state": "open",
        "merged": False,
        "merged_at": None,
        "created_at": (now - timedelta(hours=2)).isoformat(),
        "merge_commit_sha": None,
        "has_closing_keyword": True,
        "within_deadline": True,
        "body": "Fixes #1",
    }
    pr.update(overrides)
    return pr


def _bundle(prs=None, state="open", closing_commit=None, external=False):
    return {
        "prs": prs or [],
        "state": state,
        "closing_commit": closing_commit,
        "external_pending": external,
    }


# ── request budget ───────────────────────────────────────────


class TestGatherIssuePrsSkipsNonMembers:
    """Non-participants must not cost the CI a single request."""

    @patch("update_leaderboard.github_get")
    def test_non_member_pr_is_never_fetched(self, mock_get):
        """The PR endpoint is not called for someone who never opted in."""
        mock_get.return_value = _timeline_response(
            [_cross_ref_event(author="outsider")]
        )
        deadline = datetime.now(timezone.utc) + timedelta(days=7)

        bundle = gather_issue_prs("org", "proj", 1, deadline, members=set())

        assert bundle["prs"] == []
        called_urls = [call.args[0] for call in mock_get.call_args_list]
        assert all("timeline" in url for url in called_urls)
        assert PR_API_URL not in called_urls

    @patch("update_leaderboard.github_get")
    def test_member_pr_is_fetched(self, mock_get):
        """A participant's PR is fetched exactly as before."""

        def side_effect(url, **kwargs):
            if "timeline" in url:
                return _timeline_response([_cross_ref_event(author="member")])
            return type(
                "R",
                (),
                {
                    "raise_for_status": lambda self: None,
                    "json": lambda self: _pr_payload("member"),
                    "links": {},
                },
            )()

        mock_get.side_effect = side_effect
        deadline = datetime.now(timezone.utc) + timedelta(days=7)

        bundle = gather_issue_prs(
            "org", "proj", 1, deadline, members={"member"}
        )

        assert [pr["author"] for pr in bundle["prs"]] == ["member"]
        assert bundle["external_pending"] is False

    @patch("update_leaderboard.github_get")
    def test_membership_check_is_case_insensitive(self, mock_get):
        """A login stored as 'Member' still matches the PR author."""

        def side_effect(url, **kwargs):
            if "timeline" in url:
                return _timeline_response([_cross_ref_event(author="MemBer")])
            return type(
                "R",
                (),
                {
                    "raise_for_status": lambda self: None,
                    "json": lambda self: _pr_payload("MemBer"),
                    "links": {},
                },
            )()

        mock_get.side_effect = side_effect
        deadline = datetime.now(timezone.utc) + timedelta(days=7)

        bundle = gather_issue_prs(
            "org", "proj", 1, deadline, members={"member"}
        )

        assert len(bundle["prs"]) == 1

    @patch("update_leaderboard.github_get")
    def test_non_member_open_pr_sets_external_pending(self, mock_get):
        """The issue still reads as claimed, from timeline data alone."""
        mock_get.return_value = _timeline_response(
            [_cross_ref_event(author="outsider", state="open")]
        )
        deadline = datetime.now(timezone.utc) + timedelta(days=7)

        bundle = gather_issue_prs("org", "proj", 1, deadline, members=set())

        assert bundle["external_pending"] is True

    @patch("update_leaderboard.github_get")
    def test_non_member_pr_without_keyword_is_not_a_claim(self, mock_get):
        """A PR that would not auto-close the issue does not claim it."""
        mock_get.return_value = _timeline_response(
            [_cross_ref_event(author="outsider", body="Related to #1")]
        )
        deadline = datetime.now(timezone.utc) + timedelta(days=7)

        bundle = gather_issue_prs("org", "proj", 1, deadline, members=set())

        assert bundle["external_pending"] is False

    @patch("update_leaderboard.github_get")
    def test_non_member_closed_pr_is_not_a_claim(self, mock_get):
        """A closed PR leaves the issue open for someone else."""
        mock_get.return_value = _timeline_response(
            [_cross_ref_event(author="outsider", state="closed")]
        )
        deadline = datetime.now(timezone.utc) + timedelta(days=7)

        bundle = gather_issue_prs("org", "proj", 1, deadline, members=set())

        assert bundle["external_pending"] is False

    @patch("update_leaderboard.github_get")
    def test_members_none_disables_the_filter(self, mock_get):
        """Local inspection mode still fetches everything."""

        def side_effect(url, **kwargs):
            if "timeline" in url:
                return _timeline_response(
                    [_cross_ref_event(author="outsider")]
                )
            return type(
                "R",
                (),
                {
                    "raise_for_status": lambda self: None,
                    "json": lambda self: _pr_payload("outsider"),
                    "links": {},
                },
            )()

        mock_get.side_effect = side_effect
        deadline = datetime.now(timezone.utc) + timedelta(days=7)

        bundle = gather_issue_prs("org", "proj", 1, deadline, members=None)

        assert len(bundle["prs"]) == 1


class TestStubQualifies:
    """Judging a claim from the timeline payload only."""

    def test_open_keyworded_in_window_qualifies(self):
        """The same three conditions as a fully fetched PR."""
        stub = _cross_ref_event()["source"]["issue"]
        deadline = datetime.now(timezone.utc) + timedelta(days=7)
        assert _stub_qualifies(stub, "org", "proj", 1, deadline) is True

    def test_pr_opened_after_deadline_does_not_qualify(self):
        """A late PR never claims the issue."""
        stub = _cross_ref_event()["source"]["issue"]
        deadline = datetime.now(timezone.utc) - timedelta(days=1)
        assert _stub_qualifies(stub, "org", "proj", 1, deadline) is False

    def test_keyword_in_title_counts(self):
        """GitHub honours closing keywords in the title too."""
        stub = _cross_ref_event(body="no keyword here")["source"]["issue"]
        stub["title"] = "Closes #1"
        deadline = datetime.now(timezone.utc) + timedelta(days=7)
        assert _stub_qualifies(stub, "org", "proj", 1, deadline) is True

    def test_missing_created_at_does_not_qualify(self):
        """An incomplete payload is not read as a claim."""
        stub = _cross_ref_event()["source"]["issue"]
        del stub["created_at"]
        deadline = datetime.now(timezone.utc) + timedelta(days=7)
        assert _stub_qualifies(stub, "org", "proj", 1, deadline) is False

    def test_unparseable_created_at_does_not_qualify(self):
        """A malformed timestamp must not raise."""
        stub = _cross_ref_event()["source"]["issue"]
        stub["created_at"] = "not-a-date"
        deadline = datetime.now(timezone.utc) + timedelta(days=7)
        assert _stub_qualifies(stub, "org", "proj", 1, deadline) is False


# ── status display ───────────────────────────────────────────


class TestExternalPendingStatus:
    """An outsider's claim shows on the board without naming them."""

    @patch("update_leaderboard.gather_issue_prs")
    def test_external_pending_marks_issue_as_claimed(self, mock_gather):
        """has_pr is set so nobody duplicates the work."""
        mock_gather.return_value = _bundle(external=True)
        issue = _make_issue()
        week = _make_week([issue])

        _, events, status = process_week(
            "2026-W31", week, _make_scores(), set(), _prefs(), set()
        )

        assert issue["has_pr"] is True
        assert status["org/proj#1"]["has_pr"] is True
        assert events == []

    @patch("update_leaderboard.gather_issue_prs")
    def test_external_pending_keeps_issue_past_deadline(self, mock_gather):
        """A claimed issue is not dropped at the 7-day mark."""
        mock_gather.return_value = _bundle(external=True)
        issue = _make_issue()
        issue["listed_at"] = (
            datetime.now(timezone.utc) - timedelta(days=10)
        ).isoformat()
        week = _make_week([issue])

        process_week("2026-W31", week, _make_scores(), set(), _prefs(), set())

        assert issue.get("closed") is not True

    @patch("update_leaderboard.gather_issue_prs")
    def test_no_claim_leaves_issue_open(self, mock_gather):
        """No PRs at all means the issue stays free."""
        mock_gather.return_value = _bundle(external=False)
        issue = _make_issue()
        week = _make_week([issue])

        _, _, status = process_week(
            "2026-W31", week, _make_scores(), set(), _prefs(), set()
        )

        assert status["org/proj#1"]["has_pr"] is False


# ── discussion consent ───────────────────────────────────────


class TestWelcomeConsent:
    """A thread is only ever opened for someone who asked for one."""

    def test_member_with_discussion_gets_welcome(self):
        """The opted-in case still works."""
        events = _collect_welcome_events(
            [_gathered_pr(author="alice")],
            _make_scores(),
            "org/proj#1",
            set(),
            _prefs("alice"),
        )
        assert [e["username"] for e in events] == ["alice"]

    def test_member_without_discussion_gets_no_welcome(self):
        """Leaderboard-only participants are never mentioned."""
        events = _collect_welcome_events(
            [_gathered_pr(author="quiet")],
            _make_scores(),
            "org/proj#1",
            set(),
            _prefs("quiet", discussion=False),
        )
        assert events == []

    def test_non_member_gets_no_welcome(self):
        """Belt and braces: even if a stray PR reaches the collector."""
        events = _collect_welcome_events(
            [_gathered_pr(author="outsider")],
            _make_scores(),
            "org/proj#1",
            set(),
            _prefs("alice"),
        )
        assert events == []

    def test_prefs_none_preserves_legacy_behaviour(self):
        """Omitting prefs keeps the collector usable for local runs."""
        events = _collect_welcome_events(
            [_gathered_pr(author="anyone")],
            _make_scores(),
            "org/proj#1",
            set(),
            None,
        )
        assert len(events) == 1


class TestCreditWithoutDiscussion:
    """Points without mentions, for participants who declined the thread."""

    @patch("update_leaderboard.gather_issue_prs")
    def test_credit_emitted_without_welcome(self, mock_gather):
        """A quiet participant is credited but never welcomed."""
        merged_pr = _gathered_pr(
            author="quiet", state="closed", merged=True, merge_commit_sha="abc"
        )
        mock_gather.return_value = _bundle(
            prs=[merged_pr], state="closed", closing_commit="abc"
        )
        week = _make_week([_make_issue()])
        scores = _make_scores()

        credits, events, _ = process_week(
            "2026-W31",
            week,
            scores,
            set(),
            _prefs("quiet", discussion=False),
            {"quiet"},
        )

        assert credits[0]["author"] == "quiet"
        assert scores["players"]["quiet"]["total_points"] == 1
        assert [e["type"] for e in events if e["type"] == "welcome"] == []

    @patch("update_leaderboard.gather_issue_prs")
    def test_welcome_fallback_still_fires_with_consent(self, mock_gather):
        """A never-seen-open PR still welcomes a consenting participant."""
        merged_pr = _gathered_pr(
            author="alice", state="closed", merged=True, merge_commit_sha="abc"
        )
        mock_gather.return_value = _bundle(
            prs=[merged_pr], state="closed", closing_commit="abc"
        )
        week = _make_week([_make_issue()])

        _, events, _ = process_week(
            "2026-W31",
            week,
            _make_scores(),
            set(),
            _prefs("alice"),
            {"alice"},
        )

        assert events[0]["type"] == "welcome"


# ── public rendering ─────────────────────────────────────────


class TestLeaderboardRendering:
    """Only consenting participants reach the README."""

    def test_listed_participant_is_rendered(self):
        """The normal case."""
        scores = _make_scores(
            players={
                "alice": {
                    "total_points": 4,
                    "avatar_url": "https://a.png",
                    "contributions": [],
                }
            }
        )
        assert "@alice" in build_leaderboard_md(scores, _prefs("alice"))

    def test_unlisted_player_is_omitted(self):
        """A stale row from someone who left never renders."""
        scores = _make_scores(
            players={
                "gone": {
                    "total_points": 4,
                    "avatar_url": "https://a.png",
                    "contributions": [],
                }
            }
        )
        md = build_leaderboard_md(scores, _prefs())
        assert "@gone" not in md
        assert "No contributions yet" in md

    def test_prefs_none_renders_everyone(self):
        """Omitting prefs keeps the renderer usable standalone."""
        scores = _make_scores(
            players={
                "alice": {
                    "total_points": 4,
                    "avatar_url": "https://a.png",
                    "contributions": [],
                }
            }
        )
        assert "@alice" in build_leaderboard_md(scores)

    def test_merged_this_week_filters_unlisted(self):
        """The weekly avatar strip respects consent too."""
        week = arena_week_id()
        scores = _make_scores(
            players={
                "alice": {
                    "total_points": 1,
                    "avatar_url": "https://a.png",
                    "contributions": [],
                },
                "gone": {
                    "total_points": 1,
                    "avatar_url": "https://g.png",
                    "contributions": [],
                },
            },
            weekly={week: ["alice", "gone"]},
        )

        md = build_merged_this_week_md(scores, _prefs("alice"))

        assert "@alice" in md
        assert "@gone" not in md

    def test_merged_this_week_empty_after_filtering(self):
        """Filtering everyone out reads as an empty week, not a crash."""
        week = arena_week_id()
        scores = _make_scores(
            players={
                "gone": {
                    "total_points": 1,
                    "avatar_url": "https://g.png",
                    "contributions": [],
                }
            },
            weekly={week: ["gone"]},
        )
        assert "No merged contributions" in build_merged_this_week_md(
            scores, _prefs()
        )


# ── arena level ──────────────────────────────────────────────


class TestArenaLevelRatchet:
    """Someone leaving must not claw back unlocked issues."""

    @patch("update_leaderboard.load_milestones")
    @patch("update_leaderboard.load_levels_config")
    def test_level_does_not_drop_when_points_are_erased(
        self, mock_cfg, mock_milestones
    ):
        """Points can now leave the pool; the level does not follow."""
        mock_cfg.return_value = {
            "baseline": {"gfi": 20, "bug": 14, "hard": 10},
            "levels": [
                {"level": 0, "threshold": 0, "bonus": {}},
                {"level": 1, "threshold": 25, "bonus": {"gfi": 1}},
            ],
        }
        mock_milestones.return_value = {
            "current_level": 1,
            "current_arena_points": 30,
            "history": [],
        }
        scores = _make_scores(
            players={"alice": {"total_points": 5, "contributions": []}}
        )

        milestones, _, level_ups = update_arena_level(scores)

        assert milestones["current_level"] == 1
        assert milestones["current_arena_points"] == 5
        assert level_ups == []

    @patch("update_leaderboard.load_milestones")
    @patch("update_leaderboard.load_levels_config")
    def test_level_still_rises(self, mock_cfg, mock_milestones):
        """The ratchet does not block ordinary progress."""
        mock_cfg.return_value = {
            "baseline": {"gfi": 20, "bug": 14, "hard": 10},
            "levels": [
                {"level": 0, "threshold": 0, "bonus": {}},
                {"level": 1, "threshold": 25, "bonus": {"gfi": 1}},
            ],
        }
        mock_milestones.return_value = {
            "current_level": 0,
            "current_arena_points": 0,
            "history": [],
        }
        scores = _make_scores(
            players={"alice": {"total_points": 30, "contributions": []}}
        )

        milestones, _, level_ups = update_arena_level(scores)

        assert milestones["current_level"] == 1
        assert [lv["level"] for lv in level_ups] == [1]
