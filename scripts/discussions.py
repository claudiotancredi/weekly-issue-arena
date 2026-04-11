"""GitHub Discussion notifications for new and returning contributors."""

import logging

from utils import github_graphql

log = logging.getLogger(__name__)

REPO_OWNER = "claudiotancredi"
REPO_NAME = "weekly-issue-arena"
DISCUSSION_CATEGORY_NAME = "Contributor Spotlights"
ARENA_MILESTONES_CATEGORY = "Arena Milestones"

SITE_BASE = "https://claudiotancredi.github.io/weekly-issue-arena"
ARENA_LEVEL_DISCUSSION_TITLE = "🏛️ Arena Level Tracker"

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


def get_repo_and_named_category_id(
    category_name: str,
) -> tuple[str, str]:
    """Fetch the repository ID and the ID of the named discussion category.

    Raises ``RuntimeError`` if the category does not exist.
    """
    data = github_graphql(
        _REPO_AND_CATEGORY_QUERY,
        {"owner": REPO_OWNER, "name": REPO_NAME},
    )
    repo = data["repository"]
    repo_id = repo["id"]

    for cat in repo["discussionCategories"]["nodes"]:
        if cat["name"] == category_name:
            return repo_id, cat["id"]

    raise RuntimeError(
        f"Discussion category '{category_name}' not found. "
        f"Create it in the repository settings first."
    )


def get_repo_and_category_id() -> tuple[str, str]:
    """Fetch the repository and Contributor Spotlights category IDs."""
    return get_repo_and_named_category_id(DISCUSSION_CATEGORY_NAME)


def get_arena_milestones_category_id() -> tuple[str, str]:
    """Fetch the repository and Arena Milestones category IDs."""
    return get_repo_and_named_category_id(ARENA_MILESTONES_CATEGORY)


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


def _arena_level_thread_body(
    level: int,
    arena_points: int,
    next_threshold: int | None,
    total_issues: int,
) -> str:
    """Body for the persistent Arena Level Tracker thread (first level-up)."""
    next_line = (
        f"**Next level at:** {next_threshold} arena points\n"
        if next_threshold is not None
        else "**Status:** Max level reached. 🏔️\n"
    )
    return (
        f"# 🏛️ Arena Level Tracker\n\n"
        f"This thread tracks every arena-wide milestone. Every contribution "
        f"adds to a shared pool of points — when the pool crosses a "
        f"threshold, the arena levels up and **more issues get unlocked "
        f"for everyone**.\n\n"
        f"## 🎉 The arena just reached **Level {level}**!\n\n"
        f"**Total arena points:** {arena_points}\n"
        f"{next_line}"
        f"**Issues unlocked this week:** {total_issues}\n\n"
        f"Thank you to every contributor who got us here. "
        f"[View the live arena →]({SITE_BASE}/)\n\n"
        f"---\n\n"
        f"*🤖 New milestones will appear as comments below. Subscribe to "
        f"this thread to get notified when the arena levels up.*"
    )


def _arena_level_comment_body(
    from_level: int,
    to_level: int,
    arena_points: int,
    next_threshold: int | None,
    total_issues: int,
    total_issues_prev: int,
) -> str:
    """Body for a level-up comment on the Arena Level Tracker thread."""
    delta = total_issues - total_issues_prev
    if to_level - from_level > 1:
        title = (
            f"## 🚀 Arena leapt from Level {from_level} to Level {to_level}!"
        )
    else:
        title = f"## 🎉 Arena reached Level {to_level}!"

    next_line = (
        f"**Next level at:** {next_threshold} arena points  \n"
        if next_threshold is not None
        else "**Status:** Max level reached. 🏔️  \n"
    )
    return (
        f"{title}\n\n"
        f"**Total arena points:** {arena_points}  \n"
        f"{next_line}"
        f"**Issues this week:** {total_issues} "
        f"(up from {total_issues_prev}, **+{delta}**)\n\n"
        f"The collective effort of every contributor just unlocked "
        f"more issues for everyone. Keep going. 💪\n\n"
        f"[View the live arena →]({SITE_BASE}/)"
    )


def create_arena_level_discussion(
    repo_id: str,
    category_id: str,
    level: int,
    arena_points: int,
    next_threshold: int | None,
    total_issues: int,
) -> str:
    """Create the persistent Arena Level Tracker discussion thread."""
    body = _arena_level_thread_body(
        level, arena_points, next_threshold, total_issues
    )
    data = github_graphql(
        _CREATE_DISCUSSION,
        {
            "input": {
                "repositoryId": repo_id,
                "categoryId": category_id,
                "title": ARENA_LEVEL_DISCUSSION_TITLE,
                "body": body,
            }
        },
    )
    discussion = data["createDiscussion"]["discussion"]
    log.info(
        f"Created Arena Level Tracker discussion #{discussion['number']}: "
        f"{discussion['url']}"
    )
    return discussion["id"]


def add_arena_level_comment(
    discussion_id: str,
    from_level: int,
    to_level: int,
    arena_points: int,
    next_threshold: int | None,
    total_issues: int,
    total_issues_prev: int,
) -> None:
    """Append a level-up comment to the Arena Level Tracker thread."""
    body = _arena_level_comment_body(
        from_level,
        to_level,
        arena_points,
        next_threshold,
        total_issues,
        total_issues_prev,
    )
    github_graphql(
        _ADD_COMMENT,
        {"input": {"discussionId": discussion_id, "body": body}},
    )
    log.info(
        f"Posted arena level-up comment ({from_level} → {to_level}) "
        f"on discussion {discussion_id}"
    )


def announce_arena_level_up(
    milestones: dict,
    from_level: int,
    to_level: int,
    arena_points: int,
    next_threshold: int | None,
    total_issues: int,
    total_issues_prev: int,
) -> dict:
    """Create the tracker thread on first level-up, otherwise comment.

    Returns the (possibly updated) ``milestones`` dict with
    ``discussion_node_id`` populated after thread creation.
    """
    try:
        repo_id, category_id = get_arena_milestones_category_id()
    except RuntimeError as exc:
        log.warning(f"Skipping arena level-up announcement: {exc}")
        return milestones

    discussion_id = milestones.get("discussion_node_id")
    if not discussion_id:
        discussion_id = create_arena_level_discussion(
            repo_id,
            category_id,
            level=to_level,
            arena_points=arena_points,
            next_threshold=next_threshold,
            total_issues=total_issues,
        )
        milestones["discussion_node_id"] = discussion_id
    else:
        add_arena_level_comment(
            discussion_id,
            from_level=from_level,
            to_level=to_level,
            arena_points=arena_points,
            next_threshold=next_threshold,
            total_issues=total_issues,
            total_issues_prev=total_issues_prev,
        )

    return milestones


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
