"""Tests for scripts/discussions.py."""

import sys
from unittest.mock import patch

sys.path.insert(0, "scripts")

from discussions import (  # noqa: E402, I001
    add_contribution_comment,
    create_contributor_discussion,
    get_repo_and_category_id,
    notify_contributors,
    _welcome_body,
    _update_body,
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


# ── create_contributor_discussion ────────────────────────────


class TestCreateContributorDiscussion:
    """Tests for create_contributor_discussion."""

    @patch("discussions.github_graphql")
    def test_creates_and_returns_id(self, mock_gql):
        """Creates a Discussion and returns its node ID."""
        mock_gql.return_value = {
            "createDiscussion": {
                "discussion": {
                    "id": "D_abc",
                    "number": 42,
                    "url": "https://github.com/…/42",
                }
            }
        }
        node_id = create_contributor_discussion(
            "R_123",
            "DC_cat",
            "alice",
            2,
            "Hello World Engineer",
            "org/repo#1",
            "https://github.com/org/repo/pull/10",
        )
        assert node_id == "D_abc"
        call_vars = mock_gql.call_args[0][1]
        assert call_vars["input"]["repositoryId"] == "R_123"
        assert "@alice" in call_vars["input"]["title"]


# ── add_contribution_comment ─────────────────────────────────


class TestAddContributionComment:
    """Tests for add_contribution_comment."""

    @patch("discussions.github_graphql")
    def test_posts_comment(self, mock_gql):
        """Posts a comment with correct discussion ID."""
        mock_gql.return_value = {
            "addDiscussionComment": {"comment": {"id": "C_1"}}
        }
        add_contribution_comment(
            "D_abc",
            "alice",
            2,
            5,
            "Hello World Engineer",
            "org/repo#2",
            "https://github.com/org/repo/pull/20",
        )
        call_vars = mock_gql.call_args[0][1]
        assert call_vars["input"]["discussionId"] == "D_abc"
        assert "+2 point(s)" in call_vars["input"]["body"]


# ── notify_contributors ─────────────────────────────────────


class TestNotifyContributors:
    """Tests for the notify_contributors orchestrator."""

    @patch("discussions.create_contributor_discussion")
    @patch("discussions.get_repo_and_category_id")
    def test_creates_discussion_for_new_contributor(
        self, mock_ids, mock_create
    ):
        """New contributor (no discussion_node_id) gets a Discussion."""
        mock_ids.return_value = ("R_123", "DC_cat")
        mock_create.return_value = "D_new"

        scores = _make_scores(players={"alice": _make_player(total_points=2)})
        credits = [
            {
                "author": "alice",
                "pts": 2,
                "issue": "org/repo#1",
                "week": "2026-W14",
            }
        ]

        result = notify_contributors(credits, scores)
        mock_create.assert_called_once()
        assert result["players"]["alice"]["discussion_node_id"] == "D_new"

    @patch("discussions.add_contribution_comment")
    @patch("discussions.get_repo_and_category_id")
    def test_adds_comment_for_returning_contributor(
        self, mock_ids, mock_comment
    ):
        """Returning contributor (has discussion_node_id) gets a comment."""
        mock_ids.return_value = ("R_123", "DC_cat")

        scores = _make_scores(
            players={
                "bob": _make_player(
                    total_points=5,
                    discussion_node_id="D_existing",
                )
            }
        )
        credits = [
            {
                "author": "bob",
                "pts": 2,
                "issue": "org/repo#3",
                "week": "2026-W14",
            }
        ]

        notify_contributors(credits, scores)
        mock_comment.assert_called_once()
        call_args = mock_comment.call_args
        assert call_args[0][0] == "D_existing"

    @patch("discussions.get_repo_and_category_id")
    def test_category_not_found_returns_scores_unchanged(self, mock_ids):
        """If category lookup fails, scores are returned unchanged."""
        mock_ids.side_effect = RuntimeError("not found")
        scores = _make_scores(players={"alice": _make_player()})
        credits = [
            {
                "author": "alice",
                "pts": 1,
                "issue": "org/repo#1",
                "week": "2026-W14",
            }
        ]
        result = notify_contributors(credits, scores)
        assert "discussion_node_id" not in result["players"]["alice"]

    @patch("discussions.create_contributor_discussion")
    @patch("discussions.get_repo_and_category_id")
    def test_per_contributor_failure_is_isolated(self, mock_ids, mock_create):
        """One contributor's failure doesn't block others."""
        mock_ids.return_value = ("R_123", "DC_cat")
        mock_create.side_effect = [
            RuntimeError("API error"),
            "D_bob",
        ]

        scores = _make_scores(
            players={
                "alice": _make_player(total_points=1),
                "bob": _make_player(total_points=2),
            }
        )
        credits = [
            {
                "author": "alice",
                "pts": 1,
                "issue": "org/repo#1",
                "week": "2026-W14",
            },
            {
                "author": "bob",
                "pts": 2,
                "issue": "org/repo#2",
                "week": "2026-W14",
            },
        ]

        result = notify_contributors(credits, scores)
        assert "discussion_node_id" not in result["players"]["alice"]
        assert result["players"]["bob"]["discussion_node_id"] == "D_bob"


# ── message templates ────────────────────────────────────────


class TestMessageTemplates:
    """Tests for Discussion body content."""

    def test_welcome_body_contains_badge_and_profile(self):
        """Welcome message includes badge URL and profile link."""
        body = _welcome_body(
            "alice",
            2,
            "Hello World Engineer",
            "org/repo#1",
            "https://github.com/org/repo/pull/10",
        )
        assert "badges/alice.svg" in body
        assert "player/alice/" in body
        assert "@alice" in body
        assert "2 point(s)" in body
        assert "Unsubscribe" in body

    def test_update_body_contains_points(self):
        """Update comment includes points and new total."""
        body = _update_body(
            "bob",
            4,
            10,
            "Hello World Engineer",
            "org/repo#5",
            "https://github.com/org/repo/pull/50",
        )
        assert "+4 point(s)" in body
        assert "10 pts" in body
