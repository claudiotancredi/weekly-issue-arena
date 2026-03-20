#!/usr/bin/env python3
"""Fetch open issues from configured repos and update the README tables.

Usage::

    python scripts/fetch_issues.py

Environment variables:
    GITHUB_TOKEN  — required for higher rate limits (5 000 req/hr vs 60).
"""

import argparse
import json
import logging
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from utils import (
    arena_week_id,
    arena_week_start,
    github_get,
    update_readme_section,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

CONFIG_PATH = Path("config/repos.yml")
README_PATH = Path("README.md")
STATE_PATH = Path(".arena_state/issues.json")


def load_configured_repos() -> dict:
    """Loads the info about the configured repos for the Arena issues.

    Returns:
        dict: Information about the configured repos.
    """
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_issues_for_repo(
    owner: str, repo: str, labels: list[str], limit: int
) -> list[dict]:
    """Fetch open issues from a repo matching any of the given labels."""
    collected = []
    seen_ids: set[int] = set()
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
            resp = github_get(url, params=params)
            resp.raise_for_status()
            issues = resp.json()
            # Exclude pull requests and duplicates from other labels
            for i in issues:
                if "pull_request" not in i and i["id"] not in seen_ids:
                    seen_ids.add(i["id"])
                    collected.append(i)
            log.info(f"  {owner}/{repo} [{label}]: {len(issues)} issues")
        except Exception as e:
            log.warning(f"  Failed {owner}/{repo} [{label}]: {e}")
    return collected[:limit]


def enforce_repo_diversity(
    issues: list[dict], max_per_repo: int = 2
) -> list[dict]:
    """Cap the number of issues per repository to ensure diversity."""
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
    seen_globally: set[str] = set()  # dedup across categories
    listed_at = arena_week_start().isoformat()  # pinned to Friday 17:00:00 UTC
    for repo_cfg in repos:
        owner = repo_cfg["owner"]
        repo = repo_cfg["repo"]
        log.info(f"Fetching from {owner}/{repo}...")

        for category in ["hard", "bug", "gfi"]:
            labels = label_mappings[category]
            issues = get_issues_for_repo(owner, repo, labels, limit=5)
            for issue in issues:
                url_parts = issue["html_url"].split(
                    "/"
                )  # https://github.com/OWNER/REPO/issues/N
                owner_actual = url_parts[3]
                repo_actual = url_parts[4]
                issue_key = f"{owner_actual}/{repo_actual}#{issue['number']}"
                if issue_key in seen_globally:
                    continue
                seen_globally.add(issue_key)
                results[category].append(
                    {
                        "number": issue["number"],
                        "title": issue["title"],
                        "url": issue["html_url"],
                        "owner": owner_actual,
                        "repo": repo_actual,
                        "repo_url": f"https://github.com/{owner_actual}/{repo_actual}",
                        "created_at": issue["created_at"],
                        "updated_at": issue["updated_at"],
                        "author": issue["user"]["login"],
                        "listed_at": listed_at,
                    }
                )

    # Seed RNG with the week ID for reproducible results
    random.seed(arena_week_id())

    # Shuffle and cap to configured limits
    for category in results:
        random.shuffle(results[category])
        results[category] = results[category][
            : limits[category] * 2
        ]  # fetch extra buffer
        results[category] = enforce_repo_diversity(results[category])
        results[category] = results[category][: limits[category]]  # final cap

    return results


def truncate_title(title: str, max_len: int = 60) -> str:
    """Shorten a title with an ellipsis if it exceeds *max_len*.

    Also escapes pipe characters so titles don't break Markdown tables.
    """
    title = title.replace("|", "\\|")
    return title if len(title) <= max_len else title[: max_len - 3] + "..."


def build_issue_table(issues: list[dict]) -> str:
    """Build a Markdown table of issues for the README."""
    header = (
        "| # | Title | Repository | Status |\n"
        "|---|-------|------------|--------|\n"
    )
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


def save_state(issues: dict[str, list[dict]], week_id: str) -> None:
    """Persist fetched issues so the leaderboard script can track PRs."""
    STATE_PATH.parent.mkdir(exist_ok=True)

    # Load existing state or create new
    state = {}
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)

    state[week_id] = {
        "fetched_at": arena_week_start().isoformat(),
        "issues": issues,
    }

    # Prune weeks older than 28 weeks (6+ months)
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=28)
    state = {
        k: v
        for k, v in state.items()
        if datetime.fromisoformat(v["fetched_at"]) > cutoff
    }

    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    log.info(f"State saved to {STATE_PATH}")


def save_current_issues(issues: dict[str, list[dict]]) -> None:
    """Write the current week's issues for status-tracking."""
    current = []
    for category, issue_list in issues.items():
        for issue in issue_list:
            current.append(
                {
                    "owner": issue["owner"],
                    "repo": issue["repo"],
                    "number": issue["number"],
                    "category": category,
                }
            )
    path = Path(".arena_state/current_issues.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)


def main():
    """Main function for fetching new issues.

    If dry run flag is enabled, new issues are only printed and not
    stored in README.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run: fetch and print issues without writing anything",
    )
    args = parser.parse_args()

    log.info("Loading the information about the configured repos...")
    config = load_configured_repos()
    log.info("Fetching issues from configured repos...")
    issues = fetch_all_issues(config)

    total = sum(len(v) for v in issues.values())
    log.info(
        f"Total issues fetched: {total} "
        f"(GFI: {len(issues['gfi'])}, "
        f"Bug: {len(issues['bug'])}, "
        f"Hard: {len(issues['hard'])})"
    )

    if args.dry_run:
        log.info("Dry run — skipping README and state writes.")
        for category, issue_list in issues.items():
            print(f"\n=== {category.upper()} ({len(issue_list)}) ===")
            for issue in issue_list:
                print(
                    f"  [{issue['owner']}/"
                    f"{issue['repo']}"
                    f"#{issue['number']}] "
                    f"{issue['title'][:80]}"
                )
        return

    readme = README_PATH.read_text()
    readme = update_readme_section(
        readme, "ISSUES:GFI", build_issue_table(issues["gfi"])
    )
    readme = update_readme_section(
        readme, "ISSUES:BUGS", build_issue_table(issues["bug"])
    )
    readme = update_readme_section(
        readme, "ISSUES:HARD", build_issue_table(issues["hard"])
    )
    README_PATH.write_text(readme)
    log.info("README updated.")

    week_id = arena_week_id()
    save_state(issues, week_id)
    save_current_issues(issues)


if __name__ == "__main__":
    main()
