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
    github_get,
    has_linked_pr,
    update_readme_section,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

README_PATH = Path("README.md")
SCORES_PATH = Path(".arena_state/scores.json")
STATE_PATH = Path(".arena_state/issues.json")
CURRENT_ISSUES_PATH = Path(".arena_state/current_issues.json")

POINTS = {"gfi": 1, "bug": 2, "hard": 4}

RANKS = [
    (500, "Mr. Robot"),
    (100, "Bug Slayer"),
    (0, "Hello World Engineer"),
]

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


def update_issue_statuses(readme: str) -> str:
    """Refresh open/closed status emojis in the README tables."""
    if not CURRENT_ISSUES_PATH.exists():
        return readme

    with open(CURRENT_ISSUES_PATH, encoding="utf-8") as f:
        current = json.load(f)

    for issue in current:
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


def get_rank(points: int) -> str:
    """Return the rank name for the given point total."""
    for threshold, name in RANKS:
        if points >= threshold:
            return name
    return "Hello World Engineer"


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
    """Persist player scores to disk.

    Args:
        scores: The scores dictionary containing players,
            credited_issues, and weekly data.
    """
    SCORES_PATH.parent.mkdir(exist_ok=True)
    with open(SCORES_PATH, "w", encoding="utf-8") as f:
        json.dump(scores, f, indent=2)


def save_state(state: dict) -> None:
    """Save the state of the arena (issues history).

    Args:
        state (dict): Issues history information.
    """
    STATE_PATH.parent.mkdir(exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def get_closing_pr(owner: str, repo: str, issue_number: int) -> dict | None:
    """Find the specific PR that closed this issue.

    Strategy:
      1. Look for a 'closed' event in the timeline with a commit_id
         or source PR.
      2. Cross-reference with merged PRs that reference this issue.
      3. Return only the PR whose merge actually triggered the close.
    """
    url = (
        f"https://api.github.com/repos/{owner}/"
        + f"{repo}/issues/{issue_number}/timeline"
    )
    events = []
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

    # Step 1: collect all merged PRs that cross-referenced this issue
    merged_prs = {}  # pr_number -> pr_data
    for event in events:
        if event.get("event") != "cross-referenced":
            continue
        source = event.get("source", {})
        issue_data = source.get("issue", {})
        if not issue_data.get("pull_request"):
            continue
        if issue_data.get("state") != "closed":
            continue
        pr_api_url = issue_data.get("pull_request", {}).get("url")
        if not pr_api_url:
            continue
        try:
            pr_resp = github_get(pr_api_url, timeout=10)
            pr_resp.raise_for_status()
            pr_data = pr_resp.json()
            if pr_data.get("merged"):
                merged_prs[pr_data["number"]] = pr_data
        except Exception:
            continue

    if not merged_prs:
        return None

    # Step 2: if only one merged PR, that's our answer
    if len(merged_prs) == 1:
        pr_data = next(iter(merged_prs.values()))
        return {
            "author": pr_data["user"]["login"],
            "author_avatar": pr_data["user"]["avatar_url"],
            "pr_url": pr_data["html_url"],
            "merged_at": pr_data["merged_at"],
            "created_at": pr_data["created_at"],
        }

    # Step 3: multiple merged PRs — find the one whose merge_commit_sha
    # matches the commit_id in the 'closed' event
    closing_commit = None
    for event in events:
        if event.get("event") == "closed" and event.get("commit_id"):
            closing_commit = event["commit_id"]
            break

    if closing_commit:
        for pr_data in merged_prs.values():
            if pr_data.get("merge_commit_sha") == closing_commit:
                return {
                    "author": pr_data["user"]["login"],
                    "author_avatar": pr_data["user"]["avatar_url"],
                    "pr_url": pr_data["html_url"],
                    "merged_at": pr_data["merged_at"],
                    "created_at": pr_data["created_at"],
                }

    # Step 4: no closing commit match — fall back to the most recently merged
    # PR
    # This handles cases where GitHub doesn't emit a commit_id on the closed
    # event
    most_recent = max(merged_prs.values(), key=lambda p: p["merged_at"])
    log.info(
        "Could not determine closing PR via commit — falling back to "
        + f"most recently merged PR for {owner}/{repo}#{issue_number}"
    )
    return {
        "author": most_recent["user"]["login"],
        "author_avatar": most_recent["user"]["avatar_url"],
        "pr_url": most_recent["html_url"],
        "merged_at": most_recent["merged_at"],
        "created_at": most_recent["created_at"],
    }


def has_pending_pr(
    owner: str, repo: str, issue_number: int, deadline: datetime
) -> bool:
    """Check if a PR referencing this issue was opened before the deadline.

    Uses the timeline API to find cross-referenced PRs.
    Returns True if at least one PR was created before the deadline.
    """
    url = (
        f"https://api.github.com/repos/{owner}/"
        f"{repo}/issues/{issue_number}/timeline"
    )
    events = []
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
        return False

    for event in events:
        if event.get("event") != "cross-referenced":
            continue
        source = event.get("source", {})
        issue_data = source.get("issue", {})
        if not issue_data.get("pull_request"):
            continue
        # Only open PRs count — merged-without-close means contributor
        # didn't use "Fixes #N", so we can't confirm the fix was accepted
        if issue_data.get("state") != "open":
            continue
        created_str = issue_data.get("created_at")
        if not created_str:
            continue
        created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        if created_at <= deadline:
            return True
    return False


def check_issue_still_open(owner: str, repo: str, issue_number: int) -> bool:
    """Return True if the issue is still open (or on API error)."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}"
    try:
        resp = github_get(url, timeout=10)
        resp.raise_for_status()
        return resp.json().get("state") == "open"
    except Exception:
        # Assume open on error
        return True


def process_week(week_id: str, week_data: dict, scores: dict) -> list[dict]:
    """Check a week's issues for merged closing PRs.

    Returns a list of new credit events.
    Resolved issues are marked with ``closed=True`` in-place so they remain
    visible on the website during their week. A separate cleanup step in
    ``main()`` removes them from old weeks once a new week has started.
    Issues with a pending PR are left open and tracked for up to 28 weeks.
    """
    new_credits = []

    for category, issue_list in week_data["issues"].items():
        pts = POINTS[category]

        for issue in issue_list:
            issue_key = f"{issue['owner']}/{issue['repo']}#{issue['number']}"

            # Safety net: already credited — mark closed and skip
            if issue_key in scores.get("credited_issues", []):
                issue["closed"] = True
                continue

            # Compute the 7-day PR deadline from listing date
            listed_at = datetime.fromisoformat(
                issue.get("listed_at", week_data["fetched_at"])
            )
            deadline = listed_at + timedelta(days=7)
            now = datetime.now(timezone.utc)

            # If the issue is still open, check if the PR window has expired
            if check_issue_still_open(
                issue["owner"], issue["repo"], issue["number"]
            ):
                if now > deadline:
                    if has_pending_pr(
                        issue["owner"],
                        issue["repo"],
                        issue["number"],
                        deadline,
                    ):
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
                    issue["has_pr"] = has_linked_pr(
                        issue["owner"],
                        issue["repo"],
                        issue["number"],
                    )
                continue

            # Issue is closed — check if it was closed with a PR
            pr = get_closing_pr(issue["owner"], issue["repo"], issue["number"])
            if not pr:
                log.info(
                    f"No closing PR for closed issue "
                    f"{issue_key} — marking closed"
                )
                issue["closed"] = True
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
                issue["closed"] = True
                continue

            author = pr["author"]

            log.info(f"Crediting {author} {pts} pts for {issue_key}")

            if author not in scores["players"]:
                scores["players"][author] = {
                    "total_points": 0,
                    "avatar_url": pr["author_avatar"],
                    "contributions": [],
                }

            scores["players"][author]["total_points"] += pts
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

            new_credits.append(
                {
                    "author": author,
                    "pts": pts,
                    "issue": issue_key,
                    "week": week_id,
                    "pr_url": pr["pr_url"],
                }
            )

    return new_credits


def build_leaderboard_md(scores: dict) -> str:
    """Render the top-10 all-time leaderboard as Markdown."""
    header = (
        "| Position | Contributor | Points | Rank |\n"
        "|----------|------------|--------|------|\n"
    )
    players = scores.get("players", {})
    if not players:
        return (
            header
            + "| — | *No contributions yet — be the first!*"
            + " | — | — |\n"
        )

    sorted_players = sorted(
        players.items(),
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

    if state:
        # Prune issues older than the 28-week rolling window
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
    all_new_credits = []
    for week_id, week_data in state.items():
        new = process_week(week_id, week_data, scores)
        all_new_credits.extend(new)

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
        return

    save_scores(scores)

    # Recompute arena level + persist milestones (before notifications so
    # the announcement sees up-to-date state).
    milestones, levels_cfg, new_level_ups = update_arena_level(scores)

    # Notify contributors via GitHub Discussions
    if all_new_credits:
        try:
            from discussions import notify_contributors

            scores = notify_contributors(all_new_credits, scores)
            save_scores(scores)  # re-save with discussion_node_ids
        except Exception as exc:
            log.warning(f"Discussion notifications failed: {exc}")

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
                    }
                )
        CURRENT_ISSUES_PATH.write_text(
            json.dumps(refreshed, indent=2), encoding="utf-8"
        )

    # Update README
    readme = README_PATH.read_text(encoding="utf-8")
    readme = update_readme_section(
        readme, "LEADERBOARD", build_leaderboard_md(scores)
    )
    readme = update_readme_section(
        readme, "MERGED-THIS-WEEK", build_merged_this_week_md(scores)
    )
    readme = update_issue_statuses(readme)
    README_PATH.write_text(readme, encoding="utf-8")
    log.info("README leaderboard updated.")
    for c in all_new_credits:
        log.info(
            f"Credited: @{c['author']} +{c['pts']}pt(s) "
            f"for {c['issue']} (listed {c['week']})"
        )


if __name__ == "__main__":
    main()
