"""Tests for scripts/discussions.py."""

import sys
from unittest.mock import patch

sys.path.insert(0, "scripts")

from discussions import (  # noqa: E402, I001
    _additional_merge_body,
    _expired_body,
    _first_merge_body,
    _issue_closed_body,
    _pr_closed_body,
    _rank_up_body,
    _welcome_body,
    add_additional_merge_comment,
    add_expired_comment,
    add_first_merge_comment,
    add_issue_closed_comment,
    add_pr_closed_comment,
    add_rank_up_comment,
    create_welcome_discussion,
    get_repo_and_category_id,
    process_notification_events,
)


# ── helpers ──────────────────────────────────────────────────


def _make_scores(
    players: dict | None = None,
    credited_issues: list | None = None,
    weekly: dict | None = None,
) -> dict:
    return {
        "players": players or {},
        "credited_issues": credited_issues or [],
        "weekly": weekly or {},
    }


def _make_player(
    total_points: int = 1,
    discussion_node_id: str | None = None,
) -> dict:
    player: dict = {
        "total_points": total_points,
        "avatar_url": "https://avatar.png",
        "contributions": [
            {
                "issue": "org/repo#1",
                "points": 1,
                "pr_url": "https://github.com/org/repo/pull/10",
                "week": "2026-W14",
                "credited_at": "2026-04-09T12:00:00+00:00",
            }
        ],
    }
    if discussion_node_id:
        player["discussion_node_id"] = discussion_node_id
    return player


# ── get_repo_and_category_id ────────────────────────────────


class TestGetRepoAndCategoryId:
    """Tests for get_repo_and_category_id."""

    @patch("discussions.github_graphql")
    def test_returns_ids(self, mock_gql):
        """Returns repo and category IDs when category exists."""
        mock_gql.return_value = {
            "repository": {
                "id": "R_123",
                "discussionCategories": {
                    "nodes": [
                        {"id": "DC_other", "name": "General"},
                        {
                            "id": "DC_spotlights",
                            "name": "Contributor Spotlights",
                        },
                    ]
                },
            }
        }
        repo_id, cat_id = get_repo_and_category_id()
        assert repo_id == "R_123"
        assert cat_id == "DC_spotlights"

    @patch("discussions.github_graphql")
    def test_raises_when_category_missing(self, mock_gql):
        """Raises RuntimeError if category not found."""
        mock_gql.return_value = {
            "repository": {
                "id": "R_123",
                "discussionCategories": {
                    "nodes": [{"id": "DC_other", "name": "General"}]
                },
            }
        }
        try:
            get_repo_and_category_id()
            assert False, "Should have raised"
        except RuntimeError as exc:
            assert "Contributor Spotlights" in str(exc)


# ── body templates ───────────────────────────────────────────


class TestBodyTemplates:
    """Tests for Discussion body content."""

    def test_welcome_body_mentions_pr_and_profile(self):
        """Welcome body references PR URL, issue key, profile embed hint."""
        body = _welcome_body(
            "alice",
            "org/repo#1",
            "https://github.com/org/repo/pull/10",
        )
        assert "@alice" in body
        assert "org/repo#1" in body
        assert "https://github.com/org/repo/pull/10" in body
        assert "github.com/alice/alice" in body
        assert "7 days" in body
        assert "28 weeks" in body

    def test_first_merge_body_has_badge_and_embed_snippet(self):
        """First-merge body contains badge URL and embed markdown."""
        body = _first_merge_body(
            "alice",
            2,
            "Hello World Engineer",
            "org/repo#1",
            "https://github.com/org/repo/pull/10",
        )
        assert "badges/alice.svg" in body
        assert "player/alice/" in body
        assert "Arena Card unlocked" in body
        assert "+2 point(s)" in body
        assert "Hello World Engineer" in body
        assert "github.com/alice/alice" in body

    def test_additional_merge_body_has_points_and_total(self):
        """Additional-merge body shows delta and new total."""
        body = _additional_merge_body(
            4,
            10,
            "Hello World Engineer",
            "org/repo#5",
            "https://github.com/org/repo/pull/50",
        )
        assert "+4 point(s)" in body
        assert "10 pts" in body
        assert "org/repo#5" in body

    def test_rank_up_body_mentions_new_rank(self):
        """Rank-up body includes the new rank and new total, no @mention."""
        body = _rank_up_body("Bug Slayer", 100)
        assert "Bug Slayer" in body
        assert "100 pts" in body
        assert "@" not in body

    def test_first_merge_body_no_mention_in_heading(self):
        """First-merge heading should not @mention — avoids resubscribing."""
        body = _first_merge_body(
            "alice",
            2,
            "Hello World Engineer",
            "org/repo#1",
            "https://github.com/org/repo/pull/10",
        )
        assert "@alice" not in body

    def test_pr_closed_body_has_issue_and_pr(self):
        """PR-closed body references issue key and PR URL."""
        body = _pr_closed_body(
            "org/repo#1", "https://github.com/org/repo/pull/10"
        )
        assert "org/repo#1" in body
        assert "https://github.com/org/repo/pull/10" in body
        assert "closed without" in body.lower()

    def test_issue_closed_body_says_issue_fixed(self):
        """Issue-closed body states the issue is fixed."""
        body = _issue_closed_body("org/repo#1")
        assert "org/repo#1" in body
        assert "fixed" in body.lower()

    def test_expired_body_mentions_28_week_window(self):
        """Expired body mentions the 28-week tracking window."""
        body = _expired_body("org/repo#1")
        assert "org/repo#1" in body
        assert "28-week" in body or "28 weeks" in body


# ── create_welcome_discussion ───────────────────────────────


class TestCreateWelcomeDiscussion:
    """Tests for create_welcome_discussion."""

    @patch("discussions.github_graphql")
    def test_creates_and_returns_id(self, mock_gql):
        """Creates a welcome Discussion and returns node ID."""
        mock_gql.return_value = {
            "createDiscussion": {
                "discussion": {
                    "id": "D_welcome",
                    "number": 7,
                    "url": "https://github.com/…/7",
                }
            }
        }
        node_id = create_welcome_discussion(
            "R_123",
            "DC_cat",
            "alice",
            "org/repo#1",
            "https://github.com/org/repo/pull/10",
        )
        assert node_id == "D_welcome"
        call_vars = mock_gql.call_args[0][1]
        assert call_vars["input"]["repositoryId"] == "R_123"
        assert call_vars["input"]["categoryId"] == "DC_cat"
        assert "@alice" in call_vars["input"]["title"]
        assert "Welcome" in call_vars["input"]["title"]


# ── comment posters ─────────────────────────────────────────


class TestCommentPosters:
    """Tests for the per-event comment posting functions."""

    @patch("discussions.github_graphql")
    def test_first_merge_comment_posts_to_discussion(self, mock_gql):
        """First-merge comment uses the provided discussion ID."""
        mock_gql.return_value = {
            "addDiscussionComment": {"comment": {"id": "C_1"}}
        }
        add_first_merge_comment(
            "D_abc",
            "alice",
            2,
            "Hello World Engineer",
            "org/repo#1",
            "https://github.com/org/repo/pull/10",
        )
        call_vars = mock_gql.call_args[0][1]
        assert call_vars["input"]["discussionId"] == "D_abc"
        assert "Arena Card unlocked" in call_vars["input"]["body"]

    @patch("discussions.github_graphql")
    def test_additional_merge_comment_posts_points(self, mock_gql):
        """Additional-merge comment shows points delta."""
        mock_gql.return_value = {
            "addDiscussionComment": {"comment": {"id": "C_2"}}
        }
        add_additional_merge_comment(
            "D_abc",
            4,
            10,
            "Hello World Engineer",
            "org/repo#5",
            "https://github.com/org/repo/pull/50",
        )
        call_vars = mock_gql.call_args[0][1]
        assert "+4 point(s)" in call_vars["input"]["body"]
        assert "10 pts" in call_vars["input"]["body"]

    @patch("discussions.github_graphql")
    def test_rank_up_comment_posts(self, mock_gql):
        """Rank-up comment references the new rank."""
        mock_gql.return_value = {
            "addDiscussionComment": {"comment": {"id": "C_3"}}
        }
        add_rank_up_comment("D_abc", "alice", "Bug Slayer", 100)
        call_vars = mock_gql.call_args[0][1]
        assert "Bug Slayer" in call_vars["input"]["body"]

    @patch("discussions.github_graphql")
    def test_pr_closed_comment_posts(self, mock_gql):
        """PR-closed comment posts to discussion."""
        mock_gql.return_value = {
            "addDiscussionComment": {"comment": {"id": "C_4"}}
        }
        add_pr_closed_comment(
            "D_abc", "org/repo#1", "https://github.com/org/repo/pull/10"
        )
        call_vars = mock_gql.call_args[0][1]
        assert "org/repo#1" in call_vars["input"]["body"]

    @patch("discussions.github_graphql")
    def test_issue_closed_comment_posts(self, mock_gql):
        """Issue-closed comment posts to discussion."""
        mock_gql.return_value = {
            "addDiscussionComment": {"comment": {"id": "C_5"}}
        }
        add_issue_closed_comment("D_abc", "org/repo#1")
        call_vars = mock_gql.call_args[0][1]
        assert "org/repo#1" in call_vars["input"]["body"]

    @patch("discussions.github_graphql")
    def test_expired_comment_posts(self, mock_gql):
        """Expired comment posts to discussion."""
        mock_gql.return_value = {
            "addDiscussionComment": {"comment": {"id": "C_6"}}
        }
        add_expired_comment("D_abc", "org/repo#1")
        call_vars = mock_gql.call_args[0][1]
        assert "org/repo#1" in call_vars["input"]["body"]


# ── process_notification_events ──────────────────────────────


class TestProcessNotificationEvents:
    """Tests for the process_notification_events orchestrator."""

    def test_no_events_returns_scores_unchanged(self):
        """Empty event list short-circuits without API calls."""
        scores = _make_scores(players={"alice": _make_player()})
        result = process_notification_events([], scores)
        assert result is scores

    @patch("discussions.get_repo_and_category_id")
    def test_category_lookup_failure_returns_scores(self, mock_ids):
        """Category lookup failure skips all event dispatch."""
        mock_ids.side_effect = RuntimeError("not found")
        scores = _make_scores(players={"alice": _make_player()})
        events = [
            {
                "type": "welcome",
                "username": "alice",
                "issue_key": "org/repo#1",
                "pr_url": "https://github.com/org/repo/pull/10",
            }
        ]
        result = process_notification_events(events, scores)
        assert "discussion_node_id" not in result["players"]["alice"]

    @patch("discussions.create_welcome_discussion")
    @patch("discussions.get_repo_and_category_id")
    def test_welcome_event_creates_discussion(self, mock_ids, mock_create):
        """Welcome event creates a Discussion and stores the node ID."""
        mock_ids.return_value = ("R_123", "DC_cat")
        mock_create.return_value = "D_new"

        scores = _make_scores(players={"alice": _make_player()})
        events = [
            {
                "type": "welcome",
                "username": "alice",
                "issue_key": "org/repo#1",
                "pr_url": "https://github.com/org/repo/pull/10",
            }
        ]

        result = process_notification_events(events, scores)
        mock_create.assert_called_once()
        assert result["players"]["alice"]["discussion_node_id"] == "D_new"

    @patch("discussions.create_welcome_discussion")
    @patch("discussions.get_repo_and_category_id")
    def test_welcome_creates_player_entry_if_missing(
        self, mock_ids, mock_create
    ):
        """Welcome event for unknown user creates the player dict."""
        mock_ids.return_value = ("R_123", "DC_cat")
        mock_create.return_value = "D_new"

        scores = _make_scores()
        events = [
            {
                "type": "welcome",
                "username": "newbie",
                "issue_key": "org/repo#1",
                "pr_url": "https://github.com/org/repo/pull/10",
            }
        ]

        result = process_notification_events(events, scores)
        assert result["players"]["newbie"]["discussion_node_id"] == "D_new"

    @patch("discussions.add_first_merge_comment")
    @patch("discussions.get_repo_and_category_id")
    def test_first_merge_event_posts_comment(self, mock_ids, mock_comment):
        """First-merge event posts to the existing discussion."""
        mock_ids.return_value = ("R_123", "DC_cat")
        scores = _make_scores(
            players={"alice": _make_player(discussion_node_id="D_existing")}
        )
        events = [
            {
                "type": "first_merge",
                "username": "alice",
                "points": 2,
                "rank_name": "Hello World Engineer",
                "issue_key": "org/repo#1",
                "pr_url": "https://github.com/org/repo/pull/10",
            }
        ]
        process_notification_events(events, scores)
        mock_comment.assert_called_once()
        assert mock_comment.call_args[0][0] == "D_existing"

    @patch("discussions.add_additional_merge_comment")
    @patch("discussions.get_repo_and_category_id")
    def test_additional_merge_event_posts(self, mock_ids, mock_comment):
        """Additional-merge event forwards args to the comment fn."""
        mock_ids.return_value = ("R_123", "DC_cat")
        scores = _make_scores(
            players={
                "bob": _make_player(
                    total_points=10, discussion_node_id="D_bob"
                )
            }
        )
        events = [
            {
                "type": "additional_merge",
                "username": "bob",
                "points": 4,
                "new_total": 10,
                "rank_name": "Hello World Engineer",
                "issue_key": "org/repo#5",
                "pr_url": "https://github.com/org/repo/pull/50",
            }
        ]
        process_notification_events(events, scores)
        mock_comment.assert_called_once()
        assert mock_comment.call_args[0][0] == "D_bob"

    @patch("discussions.add_rank_up_comment")
    @patch("discussions.get_repo_and_category_id")
    def test_rank_up_event_posts(self, mock_ids, mock_comment):
        """Rank-up event posts to existing discussion."""
        mock_ids.return_value = ("R_123", "DC_cat")
        scores = _make_scores(
            players={
                "alice": _make_player(
                    total_points=100, discussion_node_id="D_abc"
                )
            }
        )
        events = [
            {
                "type": "rank_up",
                "username": "alice",
                "new_rank": "Bug Slayer",
                "new_total": 100,
            }
        ]
        process_notification_events(events, scores)
        mock_comment.assert_called_once_with(
            "D_abc", "alice", "Bug Slayer", 100
        )

    @patch("discussions.add_pr_closed_comment")
    @patch("discussions.get_repo_and_category_id")
    def test_pr_closed_event_posts(self, mock_ids, mock_comment):
        """PR-closed event posts to existing discussion."""
        mock_ids.return_value = ("R_123", "DC_cat")
        scores = _make_scores(
            players={"alice": _make_player(discussion_node_id="D_abc")}
        )
        events = [
            {
                "type": "pr_closed",
                "username": "alice",
                "issue_key": "org/repo#1",
                "pr_url": "https://github.com/org/repo/pull/10",
            }
        ]
        process_notification_events(events, scores)
        mock_comment.assert_called_once_with(
            "D_abc",
            "org/repo#1",
            "https://github.com/org/repo/pull/10",
        )

    @patch("discussions.add_issue_closed_comment")
    @patch("discussions.get_repo_and_category_id")
    def test_issue_closed_event_posts(self, mock_ids, mock_comment):
        """Issue-closed event posts to existing discussion."""
        mock_ids.return_value = ("R_123", "DC_cat")
        scores = _make_scores(
            players={"alice": _make_player(discussion_node_id="D_abc")}
        )
        events = [
            {
                "type": "issue_closed",
                "username": "alice",
                "issue_key": "org/repo#1",
            }
        ]
        process_notification_events(events, scores)
        mock_comment.assert_called_once_with("D_abc", "org/repo#1")

    @patch("discussions.add_expired_comment")
    @patch("discussions.get_repo_and_category_id")
    def test_expired_event_posts(self, mock_ids, mock_comment):
        """Expired event posts to existing discussion."""
        mock_ids.return_value = ("R_123", "DC_cat")
        scores = _make_scores(
            players={"alice": _make_player(discussion_node_id="D_abc")}
        )
        events = [
            {
                "type": "expired",
                "username": "alice",
                "issue_key": "org/repo#1",
            }
        ]
        process_notification_events(events, scores)
        mock_comment.assert_called_once_with("D_abc", "org/repo#1")

    @patch("discussions.add_first_merge_comment")
    @patch("discussions.get_repo_and_category_id")
    def test_event_skipped_when_no_discussion_id(self, mock_ids, mock_comment):
        """Non-welcome event without a discussion_node_id is skipped."""
        mock_ids.return_value = ("R_123", "DC_cat")
        scores = _make_scores(players={"alice": _make_player()})
        events = [
            {
                "type": "first_merge",
                "username": "alice",
                "points": 2,
                "rank_name": "Hello World Engineer",
                "issue_key": "org/repo#1",
                "pr_url": "https://github.com/org/repo/pull/10",
            }
        ]
        process_notification_events(events, scores)
        mock_comment.assert_not_called()

    @patch("discussions.add_first_merge_comment")
    @patch("discussions.create_welcome_discussion")
    @patch("discussions.get_repo_and_category_id")
    def test_per_event_failure_is_isolated(
        self, mock_ids, mock_create, mock_comment
    ):
        """One event's failure doesn't block later events."""
        mock_ids.return_value = ("R_123", "DC_cat")
        mock_create.side_effect = RuntimeError("boom")

        scores = _make_scores(
            players={
                "alice": _make_player(),
                "bob": _make_player(discussion_node_id="D_bob"),
            }
        )
        events = [
            {
                "type": "welcome",
                "username": "alice",
                "issue_key": "org/repo#1",
                "pr_url": "https://github.com/org/repo/pull/10",
            },
            {
                "type": "first_merge",
                "username": "bob",
                "points": 2,
                "rank_name": "Hello World Engineer",
                "issue_key": "org/repo#2",
                "pr_url": "https://github.com/org/repo/pull/11",
            },
        ]

        process_notification_events(events, scores)
        assert "discussion_node_id" not in scores["players"]["alice"]
        mock_comment.assert_called_once()

    @patch("discussions.create_welcome_discussion")
    @patch("discussions.get_repo_and_category_id")
    def test_welcome_initialises_full_player_entry(
        self, mock_ids, mock_create
    ):
        """Welcome for a new user creates a full player dict, not a stub."""
        mock_ids.return_value = ("R_123", "DC_cat")
        mock_create.return_value = "D_new"

        scores = _make_scores()
        events = [
            {
                "type": "welcome",
                "username": "newbie",
                "issue_key": "org/repo#1",
                "pr_url": "https://github.com/org/repo/pull/10",
                "author_avatar": "https://avatar.example.com/n.png",
            }
        ]
        process_notification_events(events, scores)
        player = scores["players"]["newbie"]
        assert player["total_points"] == 0
        assert player["avatar_url"] == "https://avatar.example.com/n.png"
        assert player["contributions"] == []
        assert player["discussion_node_id"] == "D_new"

    @patch("discussions.add_pr_closed_comment")
    @patch("discussions.get_repo_and_category_id")
    def test_pr_closed_success_marks_notified(self, mock_ids, mock_comment):
        """Successful pr_closed dispatch marks pr_closed + pr_closed_issues."""
        mock_ids.return_value = ("R_123", "DC_cat")
        scores = _make_scores(
            players={"alice": _make_player(discussion_node_id="D_abc")}
        )
        events = [
            {
                "type": "pr_closed",
                "username": "alice",
                "issue_key": "org/repo#1",
                "pr_url": "https://github.com/org/repo/pull/10",
            }
        ]
        process_notification_events(events, scores)
        notified = scores["players"]["alice"]["notified"]
        assert "https://github.com/org/repo/pull/10" in notified["pr_closed"]
        assert "org/repo#1" in notified["pr_closed_issues"]

    @patch("discussions.add_pr_closed_comment")
    @patch("discussions.get_repo_and_category_id")
    def test_pr_closed_failure_does_not_mark_notified(
        self, mock_ids, mock_comment
    ):
        """Failed API call leaves notified state untouched → retry next run."""
        mock_ids.return_value = ("R_123", "DC_cat")
        mock_comment.side_effect = RuntimeError("rate limited")
        scores = _make_scores(
            players={"alice": _make_player(discussion_node_id="D_abc")}
        )
        events = [
            {
                "type": "pr_closed",
                "username": "alice",
                "issue_key": "org/repo#1",
                "pr_url": "https://github.com/org/repo/pull/10",
            }
        ]
        process_notification_events(events, scores)
        notified = scores["players"]["alice"].get("notified", {})
        assert "https://github.com/org/repo/pull/10" not in notified.get(
            "pr_closed", []
        )
        assert "org/repo#1" not in notified.get("pr_closed_issues", [])

    @patch("discussions.add_first_merge_comment")
    @patch("discussions.get_repo_and_category_id")
    def test_first_merge_success_flips_flag(self, mock_ids, mock_comment):
        """Successful first_merge dispatch flips notified.first_merge."""
        mock_ids.return_value = ("R_123", "DC_cat")
        scores = _make_scores(
            players={"alice": _make_player(discussion_node_id="D_abc")}
        )
        events = [
            {
                "type": "first_merge",
                "username": "alice",
                "points": 2,
                "rank_name": "Hello World Engineer",
                "issue_key": "org/repo#1",
                "pr_url": "https://github.com/org/repo/pull/10",
            }
        ]
        process_notification_events(events, scores)
        assert scores["players"]["alice"]["notified"]["first_merge"] is True

    @patch("discussions.add_first_merge_comment")
    @patch("discussions.get_repo_and_category_id")
    def test_first_merge_failure_does_not_flip_flag(
        self, mock_ids, mock_comment
    ):
        """Failed first_merge leaves flag unset → retry next run."""
        mock_ids.return_value = ("R_123", "DC_cat")
        mock_comment.side_effect = RuntimeError("boom")
        scores = _make_scores(
            players={"alice": _make_player(discussion_node_id="D_abc")}
        )
        events = [
            {
                "type": "first_merge",
                "username": "alice",
                "points": 2,
                "rank_name": "Hello World Engineer",
                "issue_key": "org/repo#1",
                "pr_url": "https://github.com/org/repo/pull/10",
            }
        ]
        process_notification_events(events, scores)
        assert (
            scores["players"]["alice"].get("notified", {}).get("first_merge")
            is not True
        )

    @patch("discussions.create_welcome_discussion")
    @patch("discussions.add_first_merge_comment")
    @patch("discussions.get_repo_and_category_id")
    def test_welcome_before_first_merge_in_same_run(
        self, mock_ids, mock_comment, mock_create
    ):
        """Welcome sorts before first_merge so the merge comment works."""
        mock_ids.return_value = ("R_123", "DC_cat")
        mock_create.return_value = "D_auto"
        scores = _make_scores()
        events = [
            {
                "type": "first_merge",
                "username": "speedy",
                "points": 2,
                "rank_name": "Hello World Engineer",
                "issue_key": "org/repo#1",
                "pr_url": "https://github.com/org/repo/pull/10",
            },
            {
                "type": "welcome",
                "username": "speedy",
                "issue_key": "org/repo#1",
                "pr_url": "https://github.com/org/repo/pull/10",
                "author_avatar": "https://avatar.example.com/s.png",
            },
        ]
        process_notification_events(events, scores)
        mock_create.assert_called_once()
        mock_comment.assert_called_once()
        # first_merge received the node_id populated by the welcome.
        assert mock_comment.call_args[0][0] == "D_auto"
