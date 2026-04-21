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


# ── Body templates ───────────────────────────────────────────


def _welcome_body(username: str, issue_key: str, pr_url: str) -> str:
    """Welcome body posted when a qualifying first PR is opened."""
    return (
        f"# \U0001f44b Welcome to the Weekly Issue Arena, @{username}!\n\n"
        f"We just spotted your PR [{issue_key}]({pr_url}). "
        f"Thanks for jumping in! \U0001f680\n\n"
        f"## \U0001f6e0\ufe0f How it works from here\n\n"
        f"1. The arena bot watches this PR. **When it merges and closes "
        f"the issue**, you get credited with points automatically "
        f"(usually within ~1 hour).\n"
        f"2. **You unlock your Arena Card on your first ever merged "
        f"contribution** — whether it's this issue or another arena "
        f"issue you tackle in the future. The card shows your rank and "
        f"updates every time you earn more points.\n"
        f"3. Keep an eye on this thread: every future merge, rank-up, "
        f"or status change will appear here as a comment. "
        f"GitHub will notify you automatically.\n\n"
        f"## \U0001f4cc A few things to know\n\n"
        f"- You have **7 days** from the issue's listing to open a PR "
        f"that references it with a closing keyword "
        f"(`Fixes #N`, `Closes #N`, `Resolves #N`).\n"
        f"- After that, you have up to **28 weeks** for the PR to "
        f"merge. If it doesn't merge in that window, the issue stops "
        f"being tracked.\n"
        f"- If the issue gets closed without your PR winning (another "
        f"PR merged first, or the maintainers closed it), don't "
        f"worry — you can pick up any other arena issue and still "
        f"unlock your card there.\n\n"
        f"## \U0001f3af Once you unlock your Arena Card\n\n"
        f"You'll get a comment here with a ready-to-paste markdown "
        f"snippet. **Embed it in your GitHub profile README** "
        f"(`github.com/{username}/{username}`) so visitors see your "
        f"arena rank and points right on your profile.\n\n"
        f"---\n\n"
        f"*\U0001f916 Automatic updates will be posted here as "
        f"comments. To stop receiving notifications, click "
        f'"Unsubscribe" in the sidebar.*'
    )


def _first_merge_body(
    username: str,
    points: int,
    rank_name: str,
    issue_key: str,
    pr_url: str,
) -> str:
    """First-merge body: unlocks the Arena Card."""
    badge = _badge_url(username)
    profile = _profile_url(username)
    return (
        f"# \U0001f3c6 Arena Card unlocked!\n\n"
        f"Your PR merged and closed [{issue_key}]({pr_url}). "
        f"**+{points} point(s)** awarded \U0001f389\n\n"
        f"\U0001f396\ufe0f **Current rank:** {rank_name} | "
        f"**Total points:** {points}\n\n"
        f"## \U0001f4e3 Embed your Arena Card\n\n"
        f"Paste this into your GitHub profile README "
        f"(`github.com/{username}/{username}`) so your rank and "
        f"points are visible from your profile:\n\n"
        f"```markdown\n"
        f"[![Weekly Issue Arena]({badge})]({profile})\n"
        f"```\n\n"
        f"The card updates automatically as you earn more points.\n\n"
        f"[\U0001f449 View your full profile]({profile})"
    )


def _additional_merge_body(
    points: int,
    new_total: int,
    rank_name: str,
    issue_key: str,
    pr_url: str,
) -> str:
    """Subsequent-merge body: plain point update."""
    return (
        f"\U0001f4e5 **+{points} point(s)** for "
        f"[{issue_key}]({pr_url})\n\n"
        f"\U0001f396\ufe0f New total: **{new_total} pts** | "
        f"Rank: **{rank_name}**"
    )


def _rank_up_body(new_rank: str, new_total: int) -> str:
    """Rank-up body: congrats when crossing a rank threshold."""
    return (
        f"## \U0001f31f Rank up!\n\n"
        f"You just crossed a threshold — new rank: "
        f"**{new_rank}** ({new_total} pts).\n\n"
        f"Your Arena Card reflects the new rank automatically. "
        f"Keep going. \U0001f4aa"
    )


def _pr_closed_body(issue_key: str, pr_url: str) -> str:
    """PR-closed-unmerged body: encouragement to keep going."""
    return (
        f"## \U0001f494 PR closed without merging\n\n"
        f"Your PR for [{issue_key}]({pr_url}) was closed without "
        f"being merged. It happens — scope changes, or the "
        f"maintainers going in a different direction.\n\n"
        f"If you're still within the 7-day PR-opening window and "
        f"the issue is still being tracked, you can try again with "
        f"a new PR on the same issue. Otherwise, pick any other "
        f"arena issue and go again — every attempt sharpens your "
        f"instincts. \U0001f4aa"
    )


def _issue_closed_body(issue_key: str) -> str:
    """Issue-closed body: issue is fixed (by someone else or just closed)."""
    return (
        f"## \U0001f3c1 {issue_key} closed\n\n"
        f"The issue is fixed — so your PR for it won't be the one "
        f"that counts this round. No points this time, but the "
        f"effort is real and the practice sticks.\n\n"
        f"Jump on another arena issue whenever you're ready. "
        f"\U0001f680"
    )


def _expired_body(issue_key: str) -> str:
    """28-week expiry body: tracking window closed."""
    return (
        f"## \U0001f570\ufe0f Tracking window closed for {issue_key}\n\n"
        f"The 28-week window for this issue has elapsed and it's no "
        f"longer tracked by the arena. If your PR still merges later, "
        f"congrats on the upstream contribution — it just won't be "
        f"credited here.\n\n"
        f"Plenty of fresh arena issues are waiting. \U0001f3af"
    )


# ── Public API ───────────────────────────────────────────────


def create_welcome_discussion(
    repo_id: str,
    category_id: str,
    username: str,
    issue_key: str,
    pr_url: str,
) -> str:
    """Create a welcome Discussion when a contributor's first PR opens.

    Returns the Discussion node ID.
    """
    body = _welcome_body(username, issue_key, pr_url)
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
        f"Created welcome Discussion #{discussion['number']} "
        f"for @{username}: {discussion['url']}"
    )
    return discussion["id"]


def add_first_merge_comment(
    discussion_id: str,
    username: str,
    points: int,
    rank_name: str,
    issue_key: str,
    pr_url: str,
) -> None:
    """Post the first-merge Arena Card unlock comment."""
    body = _first_merge_body(username, points, rank_name, issue_key, pr_url)
    github_graphql(
        _ADD_COMMENT,
        {"input": {"discussionId": discussion_id, "body": body}},
    )
    log.info(f"Posted first-merge comment for @{username} on {discussion_id}")


def add_additional_merge_comment(
    discussion_id: str,
    points: int,
    new_total: int,
    rank_name: str,
    issue_key: str,
    pr_url: str,
) -> None:
    """Post a plain points update on an existing contributor Discussion."""
    body = _additional_merge_body(
        points, new_total, rank_name, issue_key, pr_url
    )
    github_graphql(
        _ADD_COMMENT,
        {"input": {"discussionId": discussion_id, "body": body}},
    )
    log.info(f"Posted additional-merge comment on {discussion_id}")


def add_rank_up_comment(
    discussion_id: str,
    username: str,
    new_rank: str,
    new_total: int,
) -> None:
    """Post a rank-up congratulations comment."""
    body = _rank_up_body(new_rank, new_total)
    github_graphql(
        _ADD_COMMENT,
        {"input": {"discussionId": discussion_id, "body": body}},
    )
    log.info(
        f"Posted rank-up comment for @{username} "
        f"(→ {new_rank}) on {discussion_id}"
    )


def add_pr_closed_comment(
    discussion_id: str,
    issue_key: str,
    pr_url: str,
) -> None:
    """Post the PR-closed-without-merging encouragement comment."""
    body = _pr_closed_body(issue_key, pr_url)
    github_graphql(
        _ADD_COMMENT,
        {"input": {"discussionId": discussion_id, "body": body}},
    )
    log.info(f"Posted pr-closed comment on {discussion_id} for {issue_key}")


def add_issue_closed_comment(
    discussion_id: str,
    issue_key: str,
) -> None:
    """Post the issue-closed-without-winning comment."""
    body = _issue_closed_body(issue_key)
    github_graphql(
        _ADD_COMMENT,
        {"input": {"discussionId": discussion_id, "body": body}},
    )
    log.info(f"Posted issue-closed comment on {discussion_id} for {issue_key}")


def add_expired_comment(
    discussion_id: str,
    issue_key: str,
) -> None:
    """Post the 28-week expiry comment."""
    body = _expired_body(issue_key)
    github_graphql(
        _ADD_COMMENT,
        {"input": {"discussionId": discussion_id, "body": body}},
    )
    log.info(f"Posted expired comment on {discussion_id} for {issue_key}")


# ── Arena Level Tracker (unchanged) ──────────────────────────


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


def _notified_bucket(scores: dict, username: str, key: str) -> list:
    """Return the persisted per-player dedup list, creating it if needed."""
    player = scores["players"].setdefault(username, {})
    return player.setdefault("notified", {}).setdefault(key, [])


def _is_already_notified(scores: dict, event: dict) -> bool:
    """Return True if this event type + target was already dispatched."""
    etype = event["type"]
    username = event["username"]
    player = scores["players"].get(username) or {}
    notified = player.get("notified", {})

    if etype == "first_merge":
        return bool(notified.get("first_merge"))
    if etype == "rank_up":
        return event["new_rank"] in notified.get("ranks_crossed", [])
    if etype == "pr_closed":
        return event["pr_url"] in notified.get("pr_closed", [])
    if etype == "issue_closed":
        return event["issue_key"] in notified.get("issue_closed", [])
    if etype == "expired":
        return event["issue_key"] in notified.get("expired", [])
    # welcome, additional_merge: handled by other means
    return False


def _mark_notified(scores: dict, event: dict) -> None:
    """Persist the fact that this event was successfully dispatched."""
    etype = event["type"]
    username = event["username"]

    if etype == "first_merge":
        scores["players"][username].setdefault("notified", {})[
            "first_merge"
        ] = True
    elif etype == "rank_up":
        bucket = _notified_bucket(scores, username, "ranks_crossed")
        if event["new_rank"] not in bucket:
            bucket.append(event["new_rank"])
    elif etype == "pr_closed":
        bucket = _notified_bucket(scores, username, "pr_closed")
        if event["pr_url"] not in bucket:
            bucket.append(event["pr_url"])
        # Used by issue_closed collector to suppress a later duplicate.
        issues_bucket = _notified_bucket(scores, username, "pr_closed_issues")
        if event["issue_key"] not in issues_bucket:
            issues_bucket.append(event["issue_key"])
    elif etype == "issue_closed":
        bucket = _notified_bucket(scores, username, "issue_closed")
        if event["issue_key"] not in bucket:
            bucket.append(event["issue_key"])
    elif etype == "expired":
        bucket = _notified_bucket(scores, username, "expired")
        if event["issue_key"] not in bucket:
            bucket.append(event["issue_key"])


def process_notification_events(events: list[dict], scores: dict) -> dict:
    """Dispatch a list of notification events to Discussion API calls.

    Each event is a dict with a ``type`` plus type-specific fields.
    Supported types:
      - ``welcome``: username, issue_key, pr_url, author_avatar
      - ``first_merge``: username, points, rank_name, issue_key, pr_url
      - ``additional_merge``: username, points, new_total, rank_name,
        issue_key, pr_url
      - ``rank_up``: username, new_rank, new_total
      - ``pr_closed``: username, issue_key, pr_url
      - ``issue_closed``: username, issue_key
      - ``expired``: username, issue_key

    Dedup state under ``scores['players'][u]['notified']`` is updated
    ONLY after the Discussion API call succeeds — a failed event will
    be retried on the next run. Caller persists ``scores``. Returns it.
    """
    if not events:
        return scores

    # Welcome events must run before any comment events for the same user,
    # since the discussion_node_id is only populated by the welcome call.
    events = sorted(
        events, key=lambda e: 0 if e.get("type") == "welcome" else 1
    )

    try:
        repo_id, category_id = get_repo_and_category_id()
    except RuntimeError as exc:
        log.warning(f"Skipping Discussion notifications: {exc}")
        return scores

    dispatched_in_run: set = set()

    for event in events:
        etype = event.get("type")
        username = event["username"]

        if _is_already_notified(scores, event):
            continue

        # Intra-run dedup: skip if an identical event already fired this
        # run (can happen if the same issue appears in multiple weeks).
        run_key = (
            etype,
            username,
            event.get("pr_url")
            or event.get("issue_key")
            or event.get("new_rank"),
        )
        if run_key in dispatched_in_run:
            continue

        player = scores["players"].get(username)

        try:
            if etype == "welcome":
                node_id = create_welcome_discussion(
                    repo_id,
                    category_id,
                    username,
                    event["issue_key"],
                    event["pr_url"],
                )
                # Initialise the full player entry so build_leaderboard_md
                # and other readers don't hit KeyError on total_points for
                # a welcome-only user.
                entry = scores["players"].setdefault(
                    username,
                    {
                        "total_points": 0,
                        "avatar_url": event.get("author_avatar", ""),
                        "contributions": [],
                    },
                )
                entry.setdefault("total_points", 0)
                entry.setdefault("contributions", [])
                if not entry.get("avatar_url"):
                    entry["avatar_url"] = event.get("author_avatar", "")
                entry["discussion_node_id"] = node_id
                dispatched_in_run.add(run_key)
                continue

            discussion_id = (
                player.get("discussion_node_id") if player else None
            )
            if not discussion_id:
                log.warning(
                    f"Cannot post {etype} for @{username}: "
                    f"no discussion_node_id"
                )
                continue

            if etype == "first_merge":
                add_first_merge_comment(
                    discussion_id,
                    username,
                    event["points"],
                    event["rank_name"],
                    event["issue_key"],
                    event["pr_url"],
                )
            elif etype == "additional_merge":
                add_additional_merge_comment(
                    discussion_id,
                    event["points"],
                    event["new_total"],
                    event["rank_name"],
                    event["issue_key"],
                    event["pr_url"],
                )
            elif etype == "rank_up":
                add_rank_up_comment(
                    discussion_id,
                    username,
                    event["new_rank"],
                    event["new_total"],
                )
            elif etype == "pr_closed":
                add_pr_closed_comment(
                    discussion_id, event["issue_key"], event["pr_url"]
                )
            elif etype == "issue_closed":
                add_issue_closed_comment(discussion_id, event["issue_key"])
            elif etype == "expired":
                add_expired_comment(discussion_id, event["issue_key"])
            else:
                log.warning(f"Unknown event type {etype!r} — skipping")
                continue

            _mark_notified(scores, event)
            dispatched_in_run.add(run_key)
        except Exception as exc:
            log.warning(
                f"Discussion event {etype} failed for @{username}: {exc}"
            )

    return scores


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
