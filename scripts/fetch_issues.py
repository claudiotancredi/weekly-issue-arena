#!/usr/bin/env python3
"""
fetch_issues.py
---------------
Fetches open issues from configured repos and updates the README issue tables.

Usage:
    python scripts/fetch_issues.py

Environment variables:
    GITHUB_TOKEN  — required for higher rate limits (5000 req/hr vs 60)
"""

import os
import re
import json
import random
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
CONFIG_PATH = Path("config/repos.yml")
README_PATH = Path("README.md")
STATE_PATH = Path(".arena_state/issues.json")

HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"Bearer {GITHUB_TOKEN}"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_issues_for_repo(owner: str, repo: str, labels: list[str], limit: int) -> list[dict]:
    """Fetch open issues from a repo matching any of the given labels."""
    collected = []
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=104)  # 2 years
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    for label in labels:
        if len(collected) >= limit:
            break
        url = f"https://api.github.com/repos/{owner}/{repo}/issues"
        params = {
            "state": "open",
            "labels": label,
            "per_page": min(limit - len(collected), 30),
            "sort": "updated",
            "direction": "desc",
            "since": cutoff_str,
        }
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
            resp.raise_for_status()
            issues = resp.json()
            # Exclude pull requests (GitHub returns PRs in issues endpoint)
            issues = [i for i in issues if "pull_request" not in i]
            collected.extend(issues)
            log.info(f"  {owner}/{repo} [{label}]: {len(issues)} issues")
        except requests.HTTPError as e:
            log.warning(f"  Failed {owner}/{repo} [{label}]: {e}")
    return collected[:limit]

def enforce_repo_diversity(issues: list[dict], max_per_repo: int = 2) -> list[dict]:
    counts = {}
    result = []
    for issue in issues:
        key = f"{issue['owner']}/{issue['repo']}"
        if counts.get(key, 0) < max_per_repo:
            result.append(issue)
            counts[key] = counts.get(key, 0) + 1
    return result


def fetch_all_issues(config: dict) -> dict[str, list[dict]]:
    """Fetch GFI, bug, and hard issues from all configured repos."""
    repos = config["repos"]
    label_mappings = config["label_mappings"]
    limits = config["limits"]

    results = {"gfi": [], "bug": [], "hard": []}

    for repo_cfg in repos:
        owner = repo_cfg["owner"]
        repo = repo_cfg["repo"]
        log.info(f"Fetching from {owner}/{repo}...")

        for category in ["gfi", "bug", "hard"]:
            labels = label_mappings[category]
            issues = get_issues_for_repo(owner, repo, labels, limit=5)
            for issue in issues:
                results[category].append({
                    "number": issue["number"],
                    "title": issue["title"],
                    "url": issue["html_url"],
                    "owner": owner,
                    "repo": repo,
                    "repo_url": f"https://github.com/{owner}/{repo}",
                    "created_at": issue["created_at"],
                    "updated_at": issue["updated_at"],
                    "author": issue["user"]["login"],
                    "listed_at": datetime.now(timezone.utc).isoformat(),
                })

    # Shuffle and cap to configured limits
    for category in results:
        random.shuffle(results[category])
        results[category] = results[category][:limits[category] * 2]  # fetch extra buffer
        results[category] = enforce_repo_diversity(results[category])
        results[category] = results[category][:limits[category]]  # final cap

    return results


def truncate_title(title: str, max_len: int = 60) -> str:
    return title if len(title) <= max_len else title[:max_len - 3] + "..."


def build_issue_table(issues: list[dict]) -> str:
    header = "| # | Title | Repository | Status |\n|---|-------|------------|--------|\n"
    if not issues:
        return header + "| — | *No issues found this week* | — | — |\n"
    lines = []
    for i, issue in enumerate(issues, 1):
        title = truncate_title(issue["title"])
        repo_name = f"{issue['owner']}/{issue['repo']}"
        lines.append(
            f"| {i} | [{title}]({issue['url']}) "
            f"| [{repo_name}]({issue['repo_url']}) | 🟢 Open |"
        )
    return header + "\n".join(lines) + "\n"


def update_readme_section(content: str, tag: str, new_body: str) -> str:
    """Replace content between <!-- TAG:START --> and <!-- TAG:END --> markers."""
    pattern = rf"(<!-- {tag}:START -->).*?(<!-- {tag}:END -->)"
    replacement = rf"\1\n{new_body}\2"
    updated, count = re.subn(pattern, replacement, content, flags=re.DOTALL)
    if count == 0:
        log.warning(f"Marker {tag} not found in README")
    return updated


def save_state(issues: dict[str, list[dict]], week_id: str) -> None:
    """Persist fetched issues so the leaderboard script can track PRs."""
    STATE_PATH.parent.mkdir(exist_ok=True)
    
    # Load existing state or create new
    state = {}
    if STATE_PATH.exists():
        with open(STATE_PATH) as f:
            state = json.load(f)

    state[week_id] = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "issues": issues,
    }

    # Prune weeks older than 28 weeks (6+ months)
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=28)
    state = {
        k: v for k, v in state.items()
        if datetime.fromisoformat(v["fetched_at"]) > cutoff
    }

    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
    log.info(f"State saved to {STATE_PATH}")


def save_current_issues(issues: dict[str, list[dict]]) -> None:
    current = []
    for category, issue_list in issues.items():
        for issue in issue_list:
            current.append({
                "owner": issue["owner"],
                "repo": issue["repo"],
                "number": issue["number"],
                "category": category,
            })
    path = Path(".arena_state/current_issues.json")
    with open(path, "w") as f:
        json.dump(current, f, indent=2)


def main():
    config = load_config()
    log.info("Fetching issues from configured repos...")
    issues = fetch_all_issues(config)

    total = sum(len(v) for v in issues.values())
    log.info(f"Total issues fetched: {total} (GFI: {len(issues['gfi'])}, Bug: {len(issues['bug'])}, Hard: {len(issues['hard'])})")

    # Update README
    readme = README_PATH.read_text()
    readme = update_readme_section(readme, "ISSUES:GFI", build_issue_table(issues["gfi"]))
    readme = update_readme_section(readme, "ISSUES:BUGS", build_issue_table(issues["bug"]))
    readme = update_readme_section(readme, "ISSUES:HARD", build_issue_table(issues["hard"]))
    README_PATH.write_text(readme)
    log.info("README updated.")

    # Save state for leaderboard tracking
    week_id = datetime.now(timezone.utc).strftime("%Y-W%W")
    save_state(issues, week_id)
    save_current_issues(issues)


if __name__ == "__main__":
    main()
