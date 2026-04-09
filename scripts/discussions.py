"""GitHub Discussion notifications for new and returning contributors."""

import logging

from utils import github_graphql

log = logging.getLogger(__name__)

REPO_OWNER = "claudiotancredi"
REPO_NAME = "weekly-issue-arena"
DISCUSSION_CATEGORY_NAME = "Contributor Spotlights"

SITE_BASE = "https://claudiotancredi.github.io/weekly-issue-arena"

# ── GraphQL queries / mutations ──────────────────────────────

_REPO_AND_CATEGORY_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    id
    discussionCategories(first: 25) {
      nodes { id name }
    }
  }
}
"""

_CREATE_DISCUSSION = """
mutation($input: CreateDiscussionInput!) {
  createDiscussion(input: $input) {
    discussion { id number url }
  }
}
"""

_ADD_COMMENT = """
mutation($input: AddDiscussionCommentInput!) {
  addDiscussionComment(input: $input) {
    comment { id }
  }
}
"""


# ── Helpers ──────────────────────────────────────────────────


def get_repo_and_category_id() -> tuple[str, str]:
    """Fetch the repository and Contributor Spotlights category IDs.

    Raises ``RuntimeError`` if the category does not exist.
    """
    data = github_graphql(
        _REPO_AND_CATEGORY_QUERY,
        {"owner": REPO_OWNER, "name": REPO_NAME},
    )
    repo = data["repository"]
    repo_id = repo["id"]

    for cat in repo["discussionCategories"]["nodes"]:
        if cat["name"] == DISCUSSION_CATEGORY_NAME:
            return repo_id, cat["id"]

    raise RuntimeError(
        f"Discussion category '{DISCUSSION_CATEGORY_NAME}' not found. "
        f"Create it in the repository settings first."
    )


def _badge_url(username: str) -> str:
    return f"{SITE_BASE}/badges/{username}.svg"


def _profile_url(username: str) -> str:
    return f"{SITE_BASE}/player/{username}/"


def _welcome_body(
    username: str,
    points: int,
    rank_name: str,
    issue_key: str,
    pr_url: str,
) -> str:
    badge = _badge_url(username)
    profile = _profile_url(username)
    return (
        f"# \u2694\ufe0f Welcome to the Arena, @{username}!\n\n"
        f"You just earned **{points} point(s)** for closing "
        f"[{issue_key}]({pr_url})! \U0001f389\n\n"
        f"\U0001f3c5 **Current rank:** {rank_name} | "
        f"**Total points:** {points}\n\n"
        f"## \U0001f4e3 Your Arena Badge\n\n"
        f"Add this to your GitHub profile README:\n\n"
        f"```markdown\n"
        f"[![Weekly Issue Arena]({badge})]({profile})\n"
        f"```\n\n"
        f"[\U0001f449 View your full profile]({profile})\n\n"
        f"---\n\n"
        f"*\U0001f916 When you earn more points, an update will be "
        f"posted here automatically by the arena bot. GitHub will "
        f"notify you of each update. To stop receiving notifications, "
        f'click "Unsubscribe" in the sidebar.*'
    )


def _update_body(
    username: str,
    points: int,
    new_total: int,
    rank_name: str,
    issue_key: str,
    pr_url: str,
) -> str:
    return (
        f"\U0001f4e5 **+{points} point(s)** for "
        f"[{issue_key}]({pr_url})\n\n"
        f"\U0001f3c5 New total: **{new_total} pts** | "
        f"Rank: **{rank_name}**"
    )


# ── Public API ───────────────────────────────────────────────


def create_contributor_discussion(
    repo_id: str,
    category_id: str,
    username: str,
    points: int,
    rank_name: str,
    issue_key: str,
    pr_url: str,
) -> str:
    """Create a welcome Discussion for a first-time contributor.

    Returns the Discussion node ID.
    """
    body = _welcome_body(username, points, rank_name, issue_key, pr_url)
    data = github_graphql(
        _CREATE_DISCUSSION,
        {
            "input": {
                "repositoryId": repo_id,
                "categoryId": category_id,
                "title": f"Welcome @{username} to the Weekly Issue Arena!",
                "body": body,
            }
        },
    )
    discussion = data["createDiscussion"]["discussion"]
    log.info(
        f"Created Discussion #{discussion['number']} "
        f"for @{username}: {discussion['url']}"
    )
    return discussion["id"]


def add_contribution_comment(
    discussion_id: str,
    username: str,
    points: int,
    new_total: int,
    rank_name: str,
    issue_key: str,
    pr_url: str,
) -> None:
    """Post an update comment on an existing contributor Discussion."""
    body = _update_body(
        username, points, new_total, rank_name, issue_key, pr_url
    )
    github_graphql(
        _ADD_COMMENT,
        {"input": {"discussionId": discussion_id, "body": body}},
    )
    log.info(f"Posted update comment for @{username} on {discussion_id}")


def notify_contributors(new_credits: list[dict], scores: dict) -> dict:
    """Create or update Discussions for all newly credited contributors.

    *new_credits* is the list returned by ``process_week()``, each
    dict containing ``author``, ``pts``, ``issue``, and ``week``.

    Returns the (possibly updated) *scores* dict with
    ``discussion_node_id`` fields populated for new contributors.
    """
    try:
        repo_id, category_id = get_repo_and_category_id()
    except RuntimeError as exc:
        log.warning(f"Skipping Discussion notifications: {exc}")
        return scores

    # Import here to avoid circular dependency
    from update_leaderboard import get_rank

    for credit in new_credits:
        author = credit["author"]
        pts = credit["pts"]
        issue_key = credit["issue"]
        pr_url = scores["players"][author]["contributions"][-1]["pr_url"]
        total = scores["players"][author]["total_points"]
        rank_name = get_rank(total)
        discussion_id = scores["players"][author].get("discussion_node_id")

        try:
            if not discussion_id:
                node_id = create_contributor_discussion(
                    repo_id,
                    category_id,
                    author,
                    pts,
                    rank_name,
                    issue_key,
                    pr_url,
                )
                scores["players"][author]["discussion_node_id"] = node_id
            else:
                add_contribution_comment(
                    discussion_id,
                    author,
                    pts,
                    total,
                    rank_name,
                    issue_key,
                    pr_url,
                )
        except Exception as exc:
            log.warning(f"Discussion notification failed for @{author}: {exc}")

    return scores
