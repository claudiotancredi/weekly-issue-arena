#!/usr/bin/env python3
"""Check tracked issues for merged PRs and update the leaderboard.

Awards points and updates the leaderboard + weekly contributors
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

from utils import (
    arena_week_id,
    github_get,
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


def check_issue_status(owner: str, repo: str, number: int) -> str:
    """Return a status emoji string for the given issue."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    try:
        resp = github_get(url, timeout=10)
        resp.raise_for_status()
        state = resp.json().get("state")
        return "🟢 Open" if state == "open" else "🔴 Closed"
    except Exception:
        return "🟢 Open"  # assume open on error


def update_issue_statuses(readme: str) -> str:
    """Refresh open/closed status emojis in the README tables."""
    if not CURRENT_ISSUES_PATH.exists():
        return readme

    with open(CURRENT_ISSUES_PATH, encoding="utf-8") as f:
        current = json.load(f)

    for issue in current:
        status = check_issue_status(
            issue["owner"], issue["repo"], issue["number"]
        )
        issue_url = (
            f"https://github.com/{issue['owner']}/"
            + f"{issue['repo']}/issues/{issue['number']}"
        )
        # Replace whichever status emoji is currently next to this issue URL
        readme = re.sub(
            rf"(\[.*?\]\({re.escape(issue_url)}\).*?\| )(?:🟢 Open|🔴 Closed)",
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
    Resolved issues (credited or expired) are removed from week_data in-place
    so issues.json stays lean.
    """
    new_credits = []

    for category, issue_list in week_data["issues"].items():
        pts = POINTS[category]
        to_remove = []

        for issue in issue_list:
            issue_key = f"{issue['owner']}/{issue['repo']}#{issue['number']}"

            # Safety net: already credited — remove stale entry and skip
            if issue_key in scores.get("credited_issues", []):
                to_remove.append(issue)
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
                    log.info(
                        f"Issue {issue_key} still open past "
                        f"7-day window — removing from tracking"
                    )
                    to_remove.append(issue)
                continue

            # Issue is closed — check if it was closed with a PR
            pr = get_closing_pr(issue["owner"], issue["repo"], issue["number"])
            if not pr:
                log.info(
                    f"No closing PR for closed issue "
                    f"{issue_key} — skipping for now"
                )
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
                to_remove.append(issue)
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
            to_remove.append(issue)

            # Track weekly contributors
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
                }
            )

        for issue in to_remove:
            issue_list.remove(issue)

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


def build_weekly_contributors_md(scores: dict) -> str:
    """Render small avatar chips for this week's contributors."""
    current_week = arena_week_id()
    week_contribs = scores.get("weekly", {}).get(current_week, [])
    if not week_contribs:
        return "*No contributions tracked yet for this week.*\n"

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

    save_state(state)

    # Update README
    readme = README_PATH.read_text(encoding="utf-8")
    readme = update_readme_section(
        readme, "LEADERBOARD", build_leaderboard_md(scores)
    )
    readme = update_readme_section(
        readme, "WEEKLY", build_weekly_contributors_md(scores)
    )
    readme = update_issue_statuses(readme)
    README_PATH.write_text(readme, encoding="utf-8")
    log.info("README leaderboard updated.")
    if all_new_credits:
        print("NEW_CREDITS:" + json.dumps(all_new_credits))


if __name__ == "__main__":
    main()
