#!/usr/bin/env python3
"""
update_leaderboard.py
---------------------
Checks tracked issues to see if any PRs have been merged that close them.
Awards points and updates the leaderboard + weekly contributors sections in README.

Usage:
    python scripts/update_leaderboard.py

Environment variables:
    GITHUB_TOKEN  — required
"""

import os
import re
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
README_PATH = Path("README.md")
STATE_PATH = Path(".arena_state/issues.json")
SCORES_PATH = Path(".arena_state/scores.json")
CURRENT_ISSUES_PATH = Path(".arena_state/current_issues.json")

HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"Bearer {GITHUB_TOKEN}"

POINTS = {"gfi": 1, "bug": 2, "hard": 4}

RANKS = [
    (500, "Mr. Robot", "🤖"),
    (100, "Bug Slayer", "🐛"),
    (0,   "HW Engineer", "🔧"),
]

def check_issue_status(owner: str, repo: str, number: int) -> str:
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        state = resp.json().get("state")
        return "🟢 Open" if state == "open" else "🔴 Closed"
    except requests.HTTPError:
        return "🟢 Open"  # assume open on error

def update_issue_statuses(readme: str) -> str:
    if not CURRENT_ISSUES_PATH.exists():
        return readme
    
    with open(CURRENT_ISSUES_PATH) as f:
        current = json.load(f)
    
    for issue in current:
        status = check_issue_status(issue["owner"], issue["repo"], issue["number"])
        issue_url = f"https://github.com/{issue['owner']}/{issue['repo']}/issues/{issue['number']}"
        # Replace whichever status emoji is currently next to this issue URL
        readme = re.sub(
            rf"(\[.*?\]\({re.escape(issue_url)}\).*?\| )(?:🟢 Open|🔴 Closed)",
            rf"\g<1>{status}",
            readme
        )
    
    return readme

def get_rank(points: int) -> tuple[str, str]:
    for threshold, name, emoji in RANKS:
        if points >= threshold:
            return name, emoji
    return "HW Engineer", "🔧"


def load_state() -> dict:
    if STATE_PATH.exists():
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}


def load_scores() -> dict:
    if SCORES_PATH.exists():
        with open(SCORES_PATH) as f:
            return json.load(f)
    return {"players": {}, "credited_issues": [], "weekly": {}}


def save_scores(scores: dict) -> None:
    SCORES_PATH.parent.mkdir(exist_ok=True)
    with open(SCORES_PATH, "w") as f:
        json.dump(scores, f, indent=2)


def get_closing_pr(owner: str, repo: str, issue_number: int) -> dict | None:
    """
    Check if an issue has been closed by a PR.
    Uses the GitHub timeline API to find cross-references and closes events.
    Returns the PR data if found, else None.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/timeline"
    try:
        resp = requests.get(url, headers={**HEADERS, "Accept": "application/vnd.github.mockingbird-preview+json"}, timeout=15)
        resp.raise_for_status()
        events = resp.json()
    except requests.HTTPError as e:
        log.warning(f"Timeline fetch failed for {owner}/{repo}#{issue_number}: {e}")
        return None

    for event in events:
        if event.get("event") == "closed" and event.get("commit_id"):
            # Closed via commit — try to attribute to a PR
            pass
        if event.get("event") == "cross-referenced":
            source = event.get("source", {})
            issue_data = source.get("issue", {})
            pr_url = issue_data.get("pull_request", {}).get("html_url")
            if pr_url and issue_data.get("state") == "closed":
                # Verify the PR was merged (not just closed)
                pr_api_url = issue_data.get("pull_request", {}).get("url")
                if pr_api_url:
                    pr_resp = requests.get(pr_api_url, headers=HEADERS, timeout=10)
                    if pr_resp.ok:
                        pr_data = pr_resp.json()
                        if pr_data.get("merged"):
                            return {
                                "author": pr_data["user"]["login"],
                                "author_avatar": pr_data["user"]["avatar_url"],
                                "pr_url": pr_data["html_url"],
                                "merged_at": pr_data["merged_at"],
                                "created_at": pr_data["created_at"],
                            }
    return None


def check_issue_still_open(owner: str, repo: str, issue_number: int) -> bool:
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        return resp.json().get("state") == "open"
    except requests.HTTPError:
        return True  # Assume open on error


def process_week(week_id: str, week_data: dict, scores: dict, issue_author_map: dict) -> list[dict]:
    """
    For a given week's issues, check if any have been closed by a merged PR.
    Returns list of new credit events.
    """
    new_credits = []
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=28)
    fetched_at = datetime.fromisoformat(week_data["fetched_at"])

    if fetched_at < cutoff:
        log.info(f"Week {week_id} is older than 28 weeks, skipping.")
        return new_credits

    for category, issue_list in week_data["issues"].items():
        pts = POINTS[category]
        for issue in issue_list:
            issue_key = f"{issue['owner']}/{issue['repo']}#{issue['number']}"

            if issue_key in scores.get("credited_issues", []):
                continue  # Already credited

            if check_issue_still_open(issue["owner"], issue["repo"], issue["number"]):
                continue  # Still open

            pr = get_closing_pr(issue["owner"], issue["repo"], issue["number"])
            if not pr:
                continue

            # Enforce: PR must have been opened within 7 days of the issue being listed
            listed_at = datetime.fromisoformat(issue.get("listed_at", week_data["fetched_at"]))
            pr_created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
            if pr_created_at > listed_at + timedelta(days=7):
                log.info(f"Skipping {issue_key}: PR opened too late ({pr_created_at} > {listed_at + timedelta(days=7)})")
                continue

            author = pr["author"]
            # Don't credit the issue author
            issue_author = issue_author_map.get(issue_key)
            if issue_author and issue_author == author:
                log.info(f"Skipping {issue_key}: closed by issue author {author}")
                scores["credited_issues"].append(issue_key)
                continue

            log.info(f"Crediting {author} {pts} pts for {issue_key}")
            
            if author not in scores["players"]:
                scores["players"][author] = {
                    "total_points": 0,
                    "avatar_url": pr["author_avatar"],
                    "contributions": [],
                }

            scores["players"][author]["total_points"] += pts
            scores["players"][author]["avatar_url"] = pr["author_avatar"]
            scores["players"][author]["contributions"].append({
                "issue": issue_key,
                "points": pts,
                "pr_url": pr["pr_url"],
                "week": week_id,
                "credited_at": datetime.now(timezone.utc).isoformat(),
            })
            scores["credited_issues"].append(issue_key)

            # Track weekly contributors
            current_week = datetime.now(timezone.utc).strftime("%Y-W%W")
            if current_week not in scores["weekly"]:
                scores["weekly"][current_week] = []
            if author not in scores["weekly"][current_week]:
                scores["weekly"][current_week].append(author)

            new_credits.append({"author": author, "pts": pts, "issue": issue_key})

    return new_credits


def build_leaderboard_md(scores: dict) -> str:
    header = "| Position | Contributor | Points | Rank |\n|----------|------------|--------|------|\n"
    players = scores.get("players", {})
    if not players:
        return header + "| — | *No contributions yet — be the first!* | — | — |\n"

    sorted_players = sorted(players.items(), key=lambda x: (-x[1]["total_points"], x[0]))
    lines = []
    for i, (username, data) in enumerate(sorted_players[:10], 1):
        pts = data["total_points"]
        rank_name, rank_emoji = get_rank(pts)
        avatar = data.get("avatar_url", "")
        profile_url = f"https://github.com/{username}"
        avatar_html = f'<a href="{profile_url}"><img src="{avatar}" width="40" height="40" style="border-radius:50%"/></a>'
        lines.append(f"| {i} | {avatar_html} [@{username}]({profile_url}) | {pts} | {rank_emoji} {rank_name} |")
    return header + "\n".join(lines) + "\n"


def build_weekly_contributors_md(scores: dict) -> str:
    current_week = datetime.now(timezone.utc).strftime("%Y-W%W")
    contributors = scores.get("weekly", {}).get(current_week, [])
    if not contributors:
        return "*No contributions tracked yet for this week.*\n"

    players = scores.get("players", {})
    avatars = []
    for username in sorted(contributors):
        avatar = players.get(username, {}).get("avatar_url", "")
        profile_url = f"https://github.com/{username}"
        avatars.append(f'<a href="{profile_url}"><img src="{avatar}" width="48" height="48" style="border-radius:50%;margin:2px" title="@{username}"/></a>')
    return " ".join(avatars) + "\n"


def update_readme_section(content: str, tag: str, new_body: str) -> str:
    pattern = rf"(<!-- {tag}:START -->).*?(<!-- {tag}:END -->)"
    replacement = rf"\1\n{new_body}\2"
    updated, count = re.subn(pattern, replacement, content, flags=re.DOTALL)
    if count == 0:
        log.warning(f"Marker {tag} not found in README")
    return updated


def main():
    state = load_state()
    scores = load_scores()

    if not state:
        log.info("No tracked issues found. Run fetch_issues.py first.")
        return

    issue_author_map = {}
    for week_data in state.values():
        for category, issue_list in week_data["issues"].items():
            for issue in issue_list:
                key = f"{issue['owner']}/{issue['repo']}#{issue['number']}"
                issue_author_map[key] = issue.get("author", "")

    all_new_credits = []
    for week_id, week_data in state.items():
        new = process_week(week_id, week_data, scores, issue_author_map)
        all_new_credits.extend(new)

    if all_new_credits:
        log.info(f"Awarded points in {len(all_new_credits)} new contributions.")
    else:
        log.info("No new contributions to credit this run.")

    save_scores(scores)

    # Update README
    readme = README_PATH.read_text()
    readme = update_readme_section(readme, "LEADERBOARD", build_leaderboard_md(scores))
    readme = update_readme_section(readme, "WEEKLY", build_weekly_contributors_md(scores))
    readme = update_issue_statuses(readme)
    README_PATH.write_text(readme)
    log.info("README leaderboard updated.")


if __name__ == "__main__":
    main()
