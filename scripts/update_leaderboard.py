#!/usr/bin/env python3
"""Check tracked issues for merged PRs and update the leaderboard.

Awards points and updates the leaderboard + merged-this-week section
sections in README.

Usage::

    python scripts/update_leaderboard.py

Environment variables:
    GITHUB_TOKEN  — required.
"""

import argparse
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from arena_level import (
    compute_arena_level,
    compute_arena_points,
    get_level_entry,
    get_next_level_entry,
    load_levels_config,
    load_milestones,
    save_milestones,
    total_issues_at_level,
)
from utils import (
    arena_week_id,
    atomic_write_json,
    atomic_write_text,
    get_rank,
    github_get,
    pr_has_closing_keyword,
    update_readme_section,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

README_PATH = Path("README.md")
SCORES_PATH = Path(".arena_state/scores.json")
STATE_PATH = Path(".arena_state/issues.json")
CURRENT_ISSUES_PATH = Path(".arena_state/current_issues.json")

POINTS = {"gfi": 1, "bug": 2, "hard": 4}

RANK_IMAGES = {
    "Mr. Robot": "assets/mrrobot.png",
    "Bug Slayer": "assets/bugslayer.png",
    "Hello World Engineer": "assets/hwengineer.png",
}


def check_issue_status(
    owner: str,
    repo: str,
    number: int,
    has_pr: bool = False,
) -> str:
    """Return a status emoji string for the given issue."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    try:
        resp = github_get(url, timeout=10)
        resp.raise_for_status()
        state = resp.json().get("state")
        if state != "open":
            return "🔴 Closed"
        return "🟡 PR Proposed" if has_pr else "🟢 Open"
    except Exception:
        return "🟢 Open"


def _status_emoji(state: str, has_pr: bool) -> str:
    if state != "open":
        return "🔴 Closed"
    return "🟡 PR Proposed" if has_pr else "🟢 Open"


def update_issue_statuses(
    readme: str, status_map: dict[str, dict] | None = None
) -> str:
    """Refresh open/closed status emojis in the README tables.

    ``status_map`` maps ``"owner/repo#N"`` → ``{"state", "has_pr"}`` and is
    populated by ``process_week``. When an issue is present in the map we
    use it directly — that same timeline fetch already told us the state,
    so a separate GET would just duplicate the request. Issues missing
    from the map (e.g. left over from a prior run) fall back to
    ``check_issue_status`` to avoid leaving stale emojis.
    """
    if not CURRENT_ISSUES_PATH.exists():
        return readme

    with open(CURRENT_ISSUES_PATH, encoding="utf-8") as f:
        current = json.load(f)

    status_map = status_map or {}
    for issue in current:
        key = f"{issue['owner']}/{issue['repo']}#{issue['number']}"
        entry = status_map.get(key)
        if entry is not None:
            status = _status_emoji(entry["state"], entry.get("has_pr", False))
        else:
            status = check_issue_status(
                issue["owner"],
                issue["repo"],
                issue["number"],
                has_pr=issue.get("has_pr", False),
            )
        issue_url = (
            f"https://github.com/{issue['owner']}/"
            + f"{issue['repo']}/issues/{issue['number']}"
        )
        readme = re.sub(
            rf"(\[.*?\]\({re.escape(issue_url)}\).*?\| )"
            rf"(?:🟢 Open|🟡 PR Proposed|🔴 Closed)",
            rf"\g<1>{status}",
            readme,
        )

    return readme


def load_state() -> dict:
    """Load the state of the arena (issues history).

    Returns an empty dict in case the issues.json file
    does not exist.

    Returns:
        dict: Issues history information.
    """
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_scores() -> dict:
    """Load the players scores.

    Returns a dict with predefined keys in case the
    scores.json file does not exist.

    Returns:
        dict: Players scores information.
    """
    if SCORES_PATH.exists():
        with open(SCORES_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"players": {}, "credited_issues": [], "weekly": {}}


def save_scores(scores: dict) -> None:
    """Persist player scores to disk atomically.

    Args:
        scores: The scores dictionary containing players,
            credited_issues, and weekly data.
    """
    atomic_write_json(SCORES_PATH, scores)


def save_state(state: dict) -> None:
    """Save the state of the arena (issues history) atomically.

    Args:
        state (dict): Issues history information.
    """
    atomic_write_json(STATE_PATH, state)


def gather_issue_prs(
    owner: str, repo: str, issue_number: int, deadline: datetime
) -> dict | None:
    """Scan the timeline once and return everything we need for an issue.

    Returns a dict with:
      * ``prs`` — list of PR dicts, each containing ``number``, ``author``,
        ``author_avatar``, ``pr_url``, ``state``, ``merged``, ``merged_at``,
        ``created_at``, ``merge_commit_sha``, ``has_closing_keyword``,
        ``within_deadline``, ``body``.
      * ``state`` — ``"open"`` or ``"closed"``, derived from the latest
        ``closed``/``reopened`` event in the timeline. Avoids a separate
        ``GET /issues/{n}`` round-trip per issue per run.
      * ``closing_commit`` — the ``commit_id`` from the first ``closed``
        event with one, or ``None``. Lets ``find_closing_pr`` disambiguate
        multi-merged-PR issues without refetching the timeline.

    Returns ``None`` on network/API errors so callers can distinguish
    "no data" (None) from "nothing to report" and avoid false-closing an
    issue based on a transient glitch.
    """
    url = (
        f"https://api.github.com/repos/{owner}/"
        f"{repo}/issues/{issue_number}/timeline"
    )
    events: list[dict] = []
    try:
        while url:
            resp = github_get(url)
            resp.raise_for_status()
            events.extend(resp.json())
            url = resp.links.get("next", {}).get("url")
    except Exception as e:
        log.warning(
            f"Timeline fetch failed for {owner}/{repo}#{issue_number}: {e}"
        )
        return None

    # Derive issue state from the most recent close/reopen event. Missing
    # events or a purely-open history ⇒ open.
    state = "open"
    last_state_change_at: str | None = None
    closing_commit: str | None = None
    for event in events:
        et = event.get("event")
        if et in ("closed", "reopened"):
            ts = event.get("created_at") or ""
            if ts >= (last_state_change_at or ""):
                last_state_change_at = ts
                state = "closed" if et == "closed" else "open"
        if (
            et == "closed"
            and event.get("commit_id")
            and closing_commit is None
        ):
            closing_commit = event["commit_id"]

    pr_api_urls: dict[str, str] = {}
    for event in events:
        if event.get("event") != "cross-referenced":
            continue
        source = event.get("source", {})
        issue_data = source.get("issue", {})
        if not issue_data.get("pull_request"):
            continue
        api_url = issue_data.get("pull_request", {}).get("url")
        if api_url:
            pr_api_urls[api_url] = api_url

    prs: list[dict] = []
    for api_url in pr_api_urls:
        try:
            pr_resp = github_get(api_url, timeout=10)
            pr_resp.raise_for_status()
            pr = pr_resp.json()
        except Exception as e:
            log.warning(
                f"PR fetch failed for {owner}/{repo}#{issue_number} "
                f"({api_url}): {e}"
            )
            return None

        created_at_str = pr.get("created_at")
        if not created_at_str:
            continue
        created_at = datetime.fromisoformat(
            created_at_str.replace("Z", "+00:00")
        )
        body = pr.get("body") or ""
        title = pr.get("title") or ""
        # GitHub's auto-close honors keywords in both title and body, so
        # arena credit must mirror that. Keyword in either field qualifies.
        has_kw = pr_has_closing_keyword(
            f"{title}\n{body}", owner, repo, issue_number
        )
        prs.append(
            {
                "number": pr.get("number"),
                "author": pr["user"]["login"],
                "author_avatar": pr["user"]["avatar_url"],
                "pr_url": pr["html_url"],
                "state": pr.get("state"),
                "merged": bool(pr.get("merged")),
                "merged_at": pr.get("merged_at"),
                "created_at": created_at_str,
                "merge_commit_sha": pr.get("merge_commit_sha"),
                "has_closing_keyword": has_kw,
                "within_deadline": created_at <= deadline,
                "body": body,
            }
        )

    prs.sort(key=lambda p: p["created_at"])
    return {"prs": prs, "state": state, "closing_commit": closing_commit}


def find_closing_pr(
    owner: str,
    repo: str,
    issue_number: int,
    prs: list[dict],
    closing_commit: str | None,
) -> dict | None:
    """Select the merged PR that actually closed the issue, if any.

    With a single merged PR, returns it directly. With multiple merged PRs,
    uses ``closing_commit`` (extracted from the timeline by the caller) to
    match a PR's ``merge_commit_sha``. If no match is possible (commit_id
    missing or doesn't match any PR's merge commit), returns ``None``
    rather than guessing — avoiding misattribution. The issue will be
    marked closed without credit.
    """
    merged = [p for p in prs if p["merged"]]
    if not merged:
        return None
    if len(merged) == 1:
        return merged[0]

    if closing_commit:
        for pr in merged:
            if pr.get("merge_commit_sha") == closing_commit:
                return pr

    log.warning(
        f"Ambiguous closing PR for {owner}/{repo}#{issue_number}: "
        f"{len(merged)} merged PRs, no commit-id match — skipping credit "
        f"to avoid misattribution."
    )
    return None


def check_issue_still_open(owner: str, repo: str, issue_number: int) -> bool:
    """Return True if the issue is still open (or on API error).

    Kept as a utility for manual inspection and legacy call sites.
    ``process_week`` no longer calls this — it derives issue state from
    the timeline events already fetched by :func:`gather_issue_prs`.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}"
    try:
        resp = github_get(url, timeout=10)
        resp.raise_for_status()
        return resp.json().get("state") == "open"
    except Exception:
        return True


def _player_has_welcome(
    scores: dict,
    username: str,
    welcomed_in_run: set | None = None,
) -> bool:
    """Has this player already been welcomed.

    Considers a user welcomed if either:
      - their scores entry already has a ``discussion_node_id`` from a prior
        run, or
      - a welcome event has been queued for them this run (tracked in
        ``welcomed_in_run``). Without this, any pr_closed / issue_closed
        event emitted in the same run as the welcome would be skipped,
        leaving the user silently without the expected comment.
    """
    player = scores["players"].get(username)
    if player and player.get("discussion_node_id"):
        return True
    if welcomed_in_run and username in welcomed_in_run:
        return True
    return False


def _notified_set(scores: dict, username: str, key: str) -> list:
    """Return the read-only view of a player's dedup list.

    Returns an empty list for unknown players or unset keys. Collectors
    must treat the return value as read-only: the dispatcher in
    ``discussions.process_notification_events`` is the sole writer of
    notification dedup state so that failed API calls can be retried.
    """
    player = scores["players"].get(username)
    if not player:
        return []
    notified = player.get("notified")
    if not notified:
        return []
    return notified.get(key, [])


def _has_first_merge(scores: dict, username: str) -> bool:
    """Return True if the player already received a first_merge comment."""
    player = scores["players"].get(username)
    if not player:
        return False
    return bool(player.get("notified", {}).get("first_merge"))


def _collect_welcome_events(
    prs: list[dict],
    scores: dict,
    issue_key: str,
    welcomed_in_run: set,
) -> list[dict]:
    """Qualifying first-PR-open events deduped within this run.

    Requires an OPEN, non-merged PR with a closing keyword opened within
    the 7-day window. Already-closed or merged PRs do not retro-trigger a
    welcome here — a merged PR that was never seen open is welcomed as a
    fallback from the credit path instead. Closed-unmerged PRs are out.
    Deduped by ``welcomed_in_run`` (shared across the full run) and by
    ``discussion_node_id`` from prior runs.
    """
    events = []
    for pr in prs:
        if not pr["has_closing_keyword"] or not pr["within_deadline"]:
            continue
        if pr["state"] != "open" or pr["merged"]:
            continue
        author = pr["author"]
        if _player_has_welcome(scores, author, welcomed_in_run):
            continue
        welcomed_in_run.add(author)
        events.append(
            {
                "type": "welcome",
                "username": author,
                "issue_key": issue_key,
                "pr_url": pr["pr_url"],
                "author_avatar": pr.get("author_avatar", ""),
            }
        )
    return events


def _collect_pr_closed_events(
    prs: list[dict],
    scores: dict,
    issue_key: str,
    welcomed_in_run: set | None = None,
) -> list[dict]:
    """Closed-unmerged PR events for users with a welcome thread.

    Emits candidate events; the dispatcher marks them notified only on
    success so that transient API failures are retried next run.
    """
    events = []
    for pr in prs:
        if pr["state"] != "closed" or pr["merged"]:
            continue
        if not pr["has_closing_keyword"]:
            continue
        author = pr["author"]
        if not _player_has_welcome(scores, author, welcomed_in_run):
            continue
        if pr["pr_url"] in _notified_set(scores, author, "pr_closed"):
            continue
        events.append(
            {
                "type": "pr_closed",
                "username": author,
                "issue_key": issue_key,
                "pr_url": pr["pr_url"],
            }
        )
    return events


def _collect_issue_closed_events(
    prs: list[dict],
    scores: dict,
    issue_key: str,
    exclude_author: str | None = None,
    welcomed_in_run: set | None = None,
) -> list[dict]:
    """Issue-closed events for PR authors whose PR didn't win.

    Suppressed for users who already received a ``pr_closed`` notification
    for the same issue — they've already been told their PR is done.
    """
    events = []
    seen_in_run: set = set()
    for pr in prs:
        if not pr["has_closing_keyword"]:
            continue
        author = pr["author"]
        if author == exclude_author:
            continue
        if author in seen_in_run:
            continue
        if not _player_has_welcome(scores, author, welcomed_in_run):
            continue
        if issue_key in _notified_set(scores, author, "pr_closed_issues"):
            continue
        if issue_key in _notified_set(scores, author, "issue_closed"):
            continue
        seen_in_run.add(author)
        events.append(
            {
                "type": "issue_closed",
                "username": author,
                "issue_key": issue_key,
            }
        )
    return events


def process_week(
    week_id: str,
    week_data: dict,
    scores: dict,
    welcomed_in_run: set | None = None,
) -> tuple[list[dict], list[dict], dict]:
    """Check a week's issues for merged closing PRs.

    Returns ``(new_credits, new_events, status_map)`` where ``status_map``
    maps ``"owner/repo#N"`` → ``{"state": "open"|"closed", "has_pr": bool}``
    for every issue inspected. The status map lets ``main()`` update the
    README without re-pinging each issue via a separate GitHub request.

    Resolved issues are marked with ``closed=True`` in-place so they remain
    visible on the website during their week. A separate cleanup step in
    ``main()`` removes them from old weeks once a new week has started.
    Issues with a pending PR are left open and tracked for up to 28 weeks.

    ``welcomed_in_run`` is a shared set of usernames welcomed during this
    run. Pass the same set across every ``process_week`` call and through
    ``_collect_expiry_events`` so that a user welcomed in one week doesn't
    get silently re-welcomed from another week.
    """
    new_credits: list[dict] = []
    new_events: list[dict] = []
    status_map: dict[str, dict] = {}
    if welcomed_in_run is None:
        welcomed_in_run = set()

    for category, issue_list in week_data["issues"].items():
        pts = POINTS[category]

        for issue in issue_list:
            issue_key = f"{issue['owner']}/{issue['repo']}#{issue['number']}"

            # Safety net: already credited — mark closed and skip
            if issue_key in scores.get("credited_issues", []):
                issue["closed"] = True
                status_map[issue_key] = {"state": "closed", "has_pr": False}
                continue

            # Compute the 7-day PR deadline from listing date
            listed_at = datetime.fromisoformat(
                issue.get("listed_at", week_data["fetched_at"])
            )
            deadline = listed_at + timedelta(days=7)
            now = datetime.now(timezone.utc)

            bundle = gather_issue_prs(
                issue["owner"], issue["repo"], issue["number"], deadline
            )
            if bundle is None:
                # Timeline fetch failed for this issue: preserve state and
                # retry next run rather than risk a false close.
                log.info(
                    f"Skipping {issue_key} this run: "
                    f"timeline fetch unavailable"
                )
                continue
            # Back-compat: legacy callers / older tests may mock
            # ``gather_issue_prs`` with a raw PR list. Fall back to a
            # separate issue-state probe in that shape so neither path
            # silently breaks. Production code always sees the dict shape.
            if isinstance(bundle, list):
                prs = bundle
                issue_state = (
                    "open"
                    if check_issue_still_open(
                        issue["owner"], issue["repo"], issue["number"]
                    )
                    else "closed"
                )
                closing_commit = None
            else:
                prs = bundle["prs"]
                issue_state = bundle["state"]
                closing_commit = bundle["closing_commit"]

            # Welcome any new qualifying PR author
            new_events.extend(
                _collect_welcome_events(
                    prs, scores, issue_key, welcomed_in_run
                )
            )
            # Don't-give-up comments for closed-unmerged PRs
            new_events.extend(
                _collect_pr_closed_events(
                    prs, scores, issue_key, welcomed_in_run
                )
            )

            # If the issue is still open, check if the PR window has expired
            if issue_state == "open":
                qualifying_pending = any(
                    pr["state"] == "open"
                    and pr["has_closing_keyword"]
                    and pr["within_deadline"]
                    for pr in prs
                )
                if now > deadline:
                    if qualifying_pending:
                        log.info(
                            f"Issue {issue_key} has a pending PR "
                            f"— keeping in tracking"
                        )
                        issue["has_pr"] = True
                    else:
                        log.info(
                            f"Issue {issue_key} still open past "
                            f"7-day window with no PR — marking closed"
                        )
                        issue["closed"] = True
                else:
                    issue["has_pr"] = qualifying_pending
                status_map[issue_key] = {
                    "state": "closed" if issue.get("closed") else "open",
                    "has_pr": bool(issue.get("has_pr")),
                }
                continue

            # Issue is closed — find the winning PR, if any
            pr = find_closing_pr(
                issue["owner"],
                issue["repo"],
                issue["number"],
                prs,
                closing_commit,
            )
            if not pr:
                log.info(
                    f"No closing PR for closed issue "
                    f"{issue_key} — marking closed"
                )
                # Notify other welcomed contributors their PR didn't win
                new_events.extend(
                    _collect_issue_closed_events(
                        prs,
                        scores,
                        issue_key,
                        welcomed_in_run=welcomed_in_run,
                    )
                )
                issue["closed"] = True
                status_map[issue_key] = {"state": "closed", "has_pr": False}
                continue

            # Enforce: PR must have been opened within 7 days of listing
            pr_created_at = datetime.fromisoformat(
                pr["created_at"].replace("Z", "+00:00")
            )
            if pr_created_at > deadline:
                log.info(
                    f"Skipping {issue_key}: PR opened too late "
                    f"({pr_created_at} > {deadline})"
                )
                new_events.extend(
                    _collect_issue_closed_events(
                        prs,
                        scores,
                        issue_key,
                        welcomed_in_run=welcomed_in_run,
                    )
                )
                issue["closed"] = True
                status_map[issue_key] = {"state": "closed", "has_pr": False}
                continue

            # Require closing keyword for credit. A merged PR without
            # "fixes/closes/resolves #N" in title or body didn't actually
            # auto-close the issue — a maintainer may have closed it
            # manually against this PR's merge commit, but the link is
            # implicit. Award no points; notify other welcomed authors.
            if not pr["has_closing_keyword"]:
                log.info(
                    f"Skipping {issue_key}: winning PR #{pr['number']} "
                    f"lacks closing keyword — marking closed without credit"
                )
                new_events.extend(
                    _collect_issue_closed_events(
                        prs,
                        scores,
                        issue_key,
                        welcomed_in_run=welcomed_in_run,
                    )
                )
                issue["closed"] = True
                status_map[issue_key] = {"state": "closed", "has_pr": False}
                continue

            author = pr["author"]

            log.info(f"Crediting {author} {pts} pts for {issue_key}")

            # Auto-welcome fallback: if we never saw this author's PR in
            # the open state (e.g. opened + merged between hourly runs),
            # no welcome event was emitted. Emit one now so the dispatcher
            # creates the Discussion thread before the first_merge comment.
            if not _player_has_welcome(scores, author, welcomed_in_run):
                welcomed_in_run.add(author)
                new_events.append(
                    {
                        "type": "welcome",
                        "username": author,
                        "issue_key": issue_key,
                        "pr_url": pr["pr_url"],
                        "author_avatar": pr["author_avatar"],
                    }
                )

            if author not in scores["players"]:
                scores["players"][author] = {
                    "total_points": 0,
                    "avatar_url": pr["author_avatar"],
                    "contributions": [],
                }
            # Ensure required fields exist even if a welcome-only entry
            # was previously created (pre-fix state on disk).
            scores["players"][author].setdefault("total_points", 0)
            scores["players"][author].setdefault("contributions", [])

            old_total = scores["players"][author]["total_points"]
            new_total = old_total + pts
            new_rank = get_rank(new_total)

            scores["players"][author]["total_points"] = new_total
            scores["players"][author]["avatar_url"] = pr["author_avatar"]
            scores["players"][author]["contributions"].append(
                {
                    "issue": issue_key,
                    "points": pts,
                    "pr_url": pr["pr_url"],
                    "week": week_id,
                    "credited_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            # Keep in credited_issues as a safety net against re-crediting
            # if the issue somehow reappears in a future fetch
            scores["credited_issues"].append(issue_key)
            issue["closed"] = True

            # Track merges for the current arena week
            current_week = arena_week_id()
            if current_week not in scores["weekly"]:
                scores["weekly"][current_week] = []
            if author not in scores["weekly"][current_week]:
                scores["weekly"][current_week].append(author)

            # First merge vs additional merge. The flag is flipped by the
            # dispatcher on success, so we check it here to route the event.
            if _has_first_merge(scores, author):
                new_events.append(
                    {
                        "type": "additional_merge",
                        "username": author,
                        "points": pts,
                        "new_total": new_total,
                        "rank_name": new_rank,
                        "issue_key": issue_key,
                        "pr_url": pr["pr_url"],
                    }
                )
            else:
                new_events.append(
                    {
                        "type": "first_merge",
                        "username": author,
                        "points": pts,
                        "rank_name": new_rank,
                        "issue_key": issue_key,
                        "pr_url": pr["pr_url"],
                    }
                )

            # Rank-up: emit whenever the current rank isn't yet marked as
            # announced. The dispatcher marks it after success, so a failed
            # rank_up is retried on the next credit.
            ranks_crossed = (
                scores["players"][author]
                .get("notified", {})
                .get("ranks_crossed", [])
            )
            if new_rank != "Hello World Engineer" and (
                new_rank not in ranks_crossed
            ):
                new_events.append(
                    {
                        "type": "rank_up",
                        "username": author,
                        "new_rank": new_rank,
                        "new_total": new_total,
                    }
                )

            # Other PR authors on this issue: issue_closed (didn't win)
            new_events.extend(
                _collect_issue_closed_events(
                    prs,
                    scores,
                    issue_key,
                    exclude_author=author,
                    welcomed_in_run=welcomed_in_run,
                )
            )

            new_credits.append(
                {
                    "author": author,
                    "pts": pts,
                    "issue": issue_key,
                    "week": week_id,
                    "pr_url": pr["pr_url"],
                }
            )
            status_map[issue_key] = {"state": "closed", "has_pr": False}

    return new_credits, new_events, status_map


def _collect_expiry_events(
    state: dict,
    scores: dict,
    welcomed_in_run: set | None = None,
) -> list[dict]:
    """Emit expired events for pending-PR contributors on weeks being dropped.

    Called before pruning. A week is expiring if its ``fetched_at`` is older
    than the 28-week cutoff. Any issue in that week still flagged
    ``has_pr=True`` and not ``closed`` represents a pending PR whose author
    will hear nothing more from the arena about this issue.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=28)
    events: list[dict] = []

    for week_id, week_data in state.items():
        fetched_at = datetime.fromisoformat(week_data["fetched_at"])
        if fetched_at >= cutoff:
            continue

        for cat_issues in week_data["issues"].values():
            for issue in cat_issues:
                if not issue.get("has_pr") or issue.get("closed"):
                    continue
                issue_key = (
                    f"{issue['owner']}/{issue['repo']}#{issue['number']}"
                )
                # Scan one more time to find pending PR authors
                listed_at = datetime.fromisoformat(
                    issue.get("listed_at", week_data["fetched_at"])
                )
                deadline = listed_at + timedelta(days=7)
                bundle = gather_issue_prs(
                    issue["owner"],
                    issue["repo"],
                    issue["number"],
                    deadline,
                )
                if bundle is None:
                    continue
                # Legacy list shape still accepted — see process_week.
                prs_for_expiry = (
                    bundle if isinstance(bundle, list) else bundle["prs"]
                )
                seen_in_issue: set = set()
                for pr in prs_for_expiry:
                    if pr["state"] != "open" or not pr["has_closing_keyword"]:
                        continue
                    if not pr["within_deadline"]:
                        continue
                    author = pr["author"]
                    if author in seen_in_issue:
                        continue
                    seen_in_issue.add(author)
                    if not _player_has_welcome(
                        scores, author, welcomed_in_run
                    ):
                        continue
                    if issue_key in _notified_set(scores, author, "expired"):
                        continue
                    events.append(
                        {
                            "type": "expired",
                            "username": author,
                            "issue_key": issue_key,
                        }
                    )
        log.info(f"Week {week_id} expiring (fetched_at={fetched_at})")

    return events


def build_leaderboard_md(scores: dict) -> str:
    """Render the top-10 all-time leaderboard as Markdown."""
    header = (
        "| Position | Contributor | Points | Rank |\n"
        "|----------|------------|--------|------|\n"
    )
    players = scores.get("players", {})
    ranked = {u: d for u, d in players.items() if d.get("total_points", 0) > 0}
    if not ranked:
        return (
            header
            + "| — | *No contributions yet — be the first!*"
            + " | — | — |\n"
        )

    sorted_players = sorted(
        ranked.items(),
        key=lambda x: (-x[1]["total_points"], x[0].lower()),
    )
    lines = []
    for i, (username, data) in enumerate(sorted_players[:10], 1):
        pts = data["total_points"]
        rank_name = get_rank(pts)
        avatar = data.get("avatar_url", "")
        profile = f"https://github.com/{username}"
        avatar_html = (
            f'<a href="{profile}">'
            f'<img src="{avatar}" width="64" height="64"'
            f' style="border-radius:50%;"/></a>'
        )
        rank_img = (
            f'<img src="{RANK_IMAGES[rank_name]}" width="64" height="64"/>'
        )
        contributor = (
            f'<div align="center">{avatar_html}<br/>'
            f"[@{username}]({profile})</div>"
        )
        rank_cell = f'<div align="center">{rank_img}</div>'
        lines.append(f"| {i} | {contributor} | {pts} | {rank_cell} |")
    return header + "\n".join(lines) + "\n"


def build_merged_this_week_md(scores: dict) -> str:
    """Render avatar chips for contributors whose PRs merged this week."""
    current_week = arena_week_id()
    week_contribs = scores.get("weekly", {}).get(current_week, [])
    if not week_contribs:
        return "*No merged contributions yet this week.*\n"

    players = scores.get("players", {})
    avatars = []
    for username in sorted(week_contribs):
        avatar = players.get(username, {}).get("avatar_url", "")
        profile = f"https://github.com/{username}"
        avatars.append(
            f'<a href="{profile}">'
            f'<img src="{avatar}" width="48" height="48"'
            f' style="border-radius:50%;"'
            f' title="@{username}"/></a>'
        )
    return " ".join(avatars) + "\n"


LEVEL_UP_RATE_LIMIT_HOURS = 6


def _last_announcement_time(milestones: dict) -> datetime | None:
    """Return the timestamp of the most recently announced level-up."""
    history = milestones.get("history", [])
    announced = [h for h in history if h.get("announced")]
    if not announced:
        return None
    last = max(announced, key=lambda h: h.get("reached_at", ""))
    try:
        return datetime.fromisoformat(
            last["reached_at"].replace("Z", "+00:00")
        )
    except (KeyError, ValueError):
        return None


def update_arena_level(scores: dict) -> tuple[dict, dict, list[dict]]:
    """Recompute arena level, persist milestones, return level-up events.

    Returns:
        (milestones, levels_config, new_level_ups)
        ``new_level_ups`` is the list of history entries that crossed
        a threshold during this run.
    """
    levels_cfg = load_levels_config()
    milestones = load_milestones()

    arena_points = compute_arena_points(scores)
    new_level = compute_arena_level(arena_points, levels_cfg)
    old_level = int(milestones.get("current_level", 0))

    new_level_ups: list[dict] = []
    if new_level > old_level:
        for lv in range(old_level + 1, new_level + 1):
            entry = get_level_entry(lv, levels_cfg)
            new_level_ups.append(
                {
                    "level": lv,
                    "reached_at": datetime.now(timezone.utc).isoformat(),
                    "arena_points_at_reach": arena_points,
                    "threshold": entry["threshold"],
                    "announced": False,
                }
            )
        milestones.setdefault("history", []).extend(new_level_ups)
        log.info(
            f"Arena leveled up: {old_level} → {new_level} "
            f"({len(new_level_ups)} threshold(s) crossed)"
        )

    milestones["current_level"] = new_level
    milestones["current_arena_points"] = arena_points

    return milestones, levels_cfg, new_level_ups


def main():
    """Main function for updating the leaderboard."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check issues and report without writing files",
    )
    args = parser.parse_args()

    # Load arena state (issues history)
    state = load_state()
    # Load players scores
    scores = load_scores()

    # Single welcome-dedup set shared across expiry + every week so a
    # user welcomed in one week isn't silently re-welcomed in another.
    welcomed_in_run: set = set()

    # Collect expiry events BEFORE pruning
    expiry_events: list[dict] = []
    if state:
        expiry_events = _collect_expiry_events(state, scores, welcomed_in_run)
        cutoff_date = datetime.now(timezone.utc) - timedelta(weeks=28)
        state = {
            k: v
            for k, v in state.items()
            if datetime.fromisoformat(v["fetched_at"]) >= cutoff_date
        }
        if not args.dry_run:
            save_state(state)

    if not state:
        log.info("No tracked issues found. Run fetch_issues.py first.")
        return

    # Process each week in the rolling window
    all_new_credits: list[dict] = []
    all_new_events: list[dict] = list(expiry_events)
    all_status: dict[str, dict] = {}
    for week_id, week_data in state.items():
        credits, events, status = process_week(
            week_id, week_data, scores, welcomed_in_run
        )
        all_new_credits.extend(credits)
        all_new_events.extend(events)
        all_status.update(status)

    # Purge closed issues from weeks that are no longer the current week.
    # Issues with a pending PR (no closed flag) survive and keep being tracked.
    current_week = arena_week_id()
    for week_id, week_data in state.items():
        if week_id == current_week:
            continue
        for cat_issues in week_data["issues"].values():
            cat_issues[:] = [i for i in cat_issues if not i.get("closed")]

    # Drop weeks whose issue lists are all empty
    state = {k: v for k, v in state.items() if any(v["issues"].values())}

    if all_new_credits:
        log.info(
            f"Awarded points in {len(all_new_credits)} new contributions."
        )
    else:
        log.info("No new contributions to credit this run.")

    if args.dry_run:
        log.info("Dry run — skipping file writes.")
        for c in all_new_credits:
            print(
                f"  @{c['author']} +{c['pts']}pt(s) "
                f"for {c['issue']} (listed {c['week']})"
            )
        for e in all_new_events:
            print(f"  event: {e}")
        return

    # Recompute arena level + persist milestones (before notifications so
    # the announcement sees up-to-date state).
    milestones, levels_cfg, new_level_ups = update_arena_level(scores)

    # Fire Discussion notifications for all collected events. The dispatcher
    # mutates `scores` in place (discussion_node_id, notified buckets) and
    # survives per-event failures — see discussions dispatcher for details.
    if all_new_events:
        try:
            from discussions import process_notification_events

            scores = process_notification_events(all_new_events, scores)
        except Exception as exc:
            log.warning(f"Discussion notifications failed: {exc}")

    # Persist scores AFTER the dispatcher has stamped discussion_node_id and
    # notified flags. Saving earlier would leak an intermediate state where a
    # successfully-created Discussion is not linked to its player — a crash
    # mid-run would then create a duplicate welcome thread next time.
    save_scores(scores)

    # Announce arena level-ups in the dedicated Arena Milestones discussion.
    if new_level_ups:
        last_announced = _last_announcement_time(milestones)
        now = datetime.now(timezone.utc)
        rate_gated = last_announced is not None and (
            now - last_announced
        ) < timedelta(hours=LEVEL_UP_RATE_LIMIT_HOURS)
        if rate_gated:
            log.info(
                f"Skipping level-up announcement: last fired at "
                f"{last_announced.isoformat()} (rate gate "
                f"{LEVEL_UP_RATE_LIMIT_HOURS}h)."
            )
        else:
            try:
                from discussions import announce_arena_level_up

                top_level = max(lv["level"] for lv in new_level_ups)
                from_level = min(lv["level"] for lv in new_level_ups) - 1
                next_entry = get_next_level_entry(top_level, levels_cfg)
                milestones = announce_arena_level_up(
                    milestones=milestones,
                    from_level=from_level,
                    to_level=top_level,
                    arena_points=milestones["current_arena_points"],
                    next_threshold=(
                        next_entry["threshold"] if next_entry else None
                    ),
                    total_issues=total_issues_at_level(top_level, levels_cfg),
                    total_issues_prev=total_issues_at_level(
                        from_level, levels_cfg
                    ),
                )
                # Mark every newly crossed level as announced.
                for entry in milestones.get("history", []):
                    if entry.get("level") in {
                        lv["level"] for lv in new_level_ups
                    }:
                        entry["announced"] = True
            except Exception as exc:
                log.warning(f"Arena level-up announcement failed: {exc}")

    save_milestones(milestones)

    # Render the README arena-level SVG from the freshest state.
    try:
        from render_arena_svg import render_arena_svg

        render_arena_svg(milestones, levels_cfg)
    except Exception as exc:
        log.warning(f"Arena SVG render failed: {exc}")

    save_state(state)

    # Rebuild current_issues.json with has_pr flags from process_week.
    current_week = arena_week_id()
    if current_week in state:
        refreshed = []
        for cat, items in state[current_week]["issues"].items():
            for iss in items:
                refreshed.append(
                    {
                        "owner": iss["owner"],
                        "repo": iss["repo"],
                        "number": iss["number"],
                        "category": cat,
                        "has_pr": iss.get("has_pr", False),
                        "language": iss.get("language"),
                    }
                )
        atomic_write_json(CURRENT_ISSUES_PATH, refreshed)

    # Update README
    readme = README_PATH.read_text(encoding="utf-8")
    readme = update_readme_section(
        readme, "LEADERBOARD", build_leaderboard_md(scores)
    )
    readme = update_readme_section(
        readme, "MERGED-THIS-WEEK", build_merged_this_week_md(scores)
    )
    readme = update_issue_statuses(readme, all_status)
    atomic_write_text(README_PATH, readme)
    log.info("README leaderboard updated.")
    for c in all_new_credits:
        log.info(
            f"Credited: @{c['author']} +{c['pts']}pt(s) "
            f"for {c['issue']} (listed {c['week']})"
        )


if __name__ == "__main__":
    main()
