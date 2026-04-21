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
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from arena_level import (
    current_level_from_state,
    effective_limits,
    load_levels_config,
)
from utils import (
    arena_week_id,
    arena_week_start,
    atomic_write_json,
    atomic_write_text,
    github_get,
    has_linked_pr,
    update_readme_section,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

CONFIG_PATH = Path("config/repos.yml")
ANCHOR_PATH = Path("config/anchor_repos.yml")
POOL_PATH = Path(".arena_state/repo_pool.json")
README_PATH = Path("README.md")
STATE_PATH = Path(".arena_state/issues.json")


def load_configured_repos() -> dict:
    """Load label_mappings, level-derived limits, and the weekly repo list.

    Three-level fallback chain for the repo list:
      1. ``.arena_state/repo_pool.json`` (weekly dynamic pool, preferred)
      2. ``config/anchor_repos.yml`` (curated anchor list, fallback)
      3. empty list (logged as error)

    ``label_mappings`` comes from ``config/repos.yml``. ``limits`` is
    computed from the current arena level (``config/arena_levels.json``
    plus ``.arena_state/milestones.json``) so collective progress unlocks
    more issues per category over time.

    Returns:
        dict: Configuration with ``repos``, ``label_mappings``, ``limits``,
        and ``arena_level``.
    """
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    levels_cfg = load_levels_config()
    level = current_level_from_state()
    config["limits"] = effective_limits(level, levels_cfg)
    config["arena_level"] = level
    log.info(
        f"Arena level {level} → limits: "
        f"gfi={config['limits']['gfi']}, "
        f"bug={config['limits']['bug']}, "
        f"hard={config['limits']['hard']}"
    )

    if POOL_PATH.exists():
        with open(POOL_PATH, encoding="utf-8") as f:
            pool = json.load(f)
        config["repos"] = pool["repos"]
        trending_count = pool.get("trending_count", 0)
        log.info(
            f"Loaded {pool['total_count']} repos from pool "
            f"({pool['anchor_count']} anchor + "
            f"{trending_count} trending + "
            f"{pool['dynamic_count']} dynamic)."
        )
    elif ANCHOR_PATH.exists():
        with open(ANCHOR_PATH, encoding="utf-8") as f:
            anchor = yaml.safe_load(f)
        config["repos"] = [
            {"owner": r["owner"], "repo": r["repo"]}
            for r in anchor.get("repos", [])
        ]
        log.warning(
            f"No dynamic pool found — falling back to anchor repos "
            f"({len(config['repos'])})."
        )
    else:
        log.error("No repo source available — pool is empty.")
        config["repos"] = []

    return config


def fetch_repo_language(owner: str, repo: str) -> str | None:
    """Fetch the dominant language of a repo via GitHub REST."""
    url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        resp = github_get(url)
        resp.raise_for_status()
        return resp.json().get("language")
    except Exception as exc:
        log.warning(f"  Language fetch failed for {owner}/{repo}: {exc}")
        return None


def build_language_map(repos: list[dict]) -> dict[str, str | None]:
    """Return a {owner/repo: language} map for all repos.

    Uses ``language`` from pool entries when present, otherwise hits
    GitHub once per repo. No on-disk cache — kept simple at the cost
    of ~250 extra API calls per weekly run.
    """
    result: dict[str, str | None] = {}
    for r in repos:
        key = f"{r['owner']}/{r['repo']}"
        lang = r.get("language")
        if lang:
            result[key] = lang
        else:
            result[key] = fetch_repo_language(r["owner"], r["repo"])
    return result


def get_issues_for_repo(
    owner: str,
    repo: str,
    labels: list[str],
    limit: int,
    cutoff_weeks: int = 40,
) -> list[dict]:
    """Fetch open issues from a repo matching any of the given labels."""
    collected = []
    seen_ids: set[int] = set()
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=cutoff_weeks)
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


def select_category_issues(
    issues: list[dict],
    limit: int,
    rng: random.Random,
    max_per_repo: int = 2,
) -> list[dict]:
    """Select up to ``limit`` issues for one category at random.

    The shuffle is driven by ``rng`` so runs with the same seed are
    reproducible within a week. Constraints enforced during selection:
      - At most ``max_per_repo`` issues from any single repo.
      - Issues with an already-linked PR are skipped (lazy API check).
    """
    pool = list(issues)
    rng.shuffle(pool)

    selected: list[dict] = []
    repo_counts: dict[str, int] = {}

    for issue in pool:
        if len(selected) >= limit:
            break
        key = f"{issue['owner']}/{issue['repo']}"
        if repo_counts.get(key, 0) >= max_per_repo:
            continue
        if has_linked_pr(issue["owner"], issue["repo"], issue["number"]):
            continue
        selected.append(issue)
        repo_counts[key] = repo_counts.get(key, 0) + 1

    return selected


def fetch_all_issues(config: dict) -> dict[str, list[dict]]:
    """Fetch GFI, bug, and hard issues from all configured repos."""
    repos = config["repos"]
    label_mappings = config["label_mappings"]
    limits = config["limits"]

    results: dict[str, list[dict]] = {"gfi": [], "bug": [], "hard": []}
    seen_globally: set[str] = set()  # dedup across categories
    listed_at = arena_week_start().isoformat()  # pinned to Friday 17:00:00 UTC
    language_map = build_language_map(repos)
    for repo_cfg in repos:
        owner = repo_cfg["owner"]
        repo = repo_cfg["repo"]
        log.info(f"Fetching from {owner}/{repo}...")

        for category in ["hard", "bug", "gfi"]:
            labels = label_mappings[category]
            cutoff = 80 if category == "hard" else 40
            issues = get_issues_for_repo(
                owner,
                repo,
                labels,
                limit=5,
                cutoff_weeks=cutoff,
            )
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
                        "language": language_map.get(
                            f"{owner_actual}/{repo_actual}"
                        ),
                    }
                )

    # Week-seeded local RNG — reproducible within the week, rotates weekly.
    rng = random.Random(arena_week_id())

    selected: dict[str, list[dict]] = {}
    for category in results:
        cat_limit = limits[category]
        picked = select_category_issues(results[category], cat_limit, rng)
        selected[category] = picked
        log.info(f"{category}: selected {len(picked)}/{cat_limit}")

    return selected


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


SUMMARY_PATTERNS = {
    "gfi": re.compile(
        r"(<summary>\s*🙂 Good First Issues )\(\d+\)(</summary>)"
    ),
    "bug": re.compile(r"(<summary>\s*🪲 Bug Fixes )\(\d+\)(</summary>)"),
    "hard": re.compile(r"(<summary>\s*😓 Hard Issues )\(\d+\)(</summary>)"),
}


def update_summary_counts(readme: str, issues: dict[str, list[dict]]) -> str:
    """Rewrite the collapsible-section counts to match actual issue totals."""
    for category, pattern in SUMMARY_PATTERNS.items():
        count = len(issues.get(category, []))
        readme = pattern.sub(rf"\g<1>({count})\2", readme)
    return readme


def save_state(issues: dict[str, list[dict]], week_id: str) -> None:
    """Persist fetched issues so the leaderboard script can track PRs."""
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
        if datetime.fromisoformat(v["fetched_at"]) >= cutoff
    }

    atomic_write_json(STATE_PATH, state)
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
                    "language": issue.get("language"),
                }
            )
    path = Path(".arena_state/current_issues.json")
    atomic_write_json(path, current)


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
    readme = update_summary_counts(readme, issues)
    atomic_write_text(README_PATH, readme)
    log.info("README updated.")

    week_id = arena_week_id()
    save_state(issues, week_id)
    save_current_issues(issues)


if __name__ == "__main__":
    main()
