#!/usr/bin/env python3
"""Fetch the weekly arena repo pool from GitHub Search API.

Builds a hybrid pool of exactly 250 repos:
  - 50 anchor repos from config/anchor_repos.yml (always included)
  - ~200 dynamic repos from GitHub Search API queries

The dynamic discovery covers all three issue categories (gfi, bug, hard)
via topic-aware queries plus a "trending newcomers" query that catches
recently-exploded repos. Noise (awesome-lists, tutorials, skill packs)
is filtered post-fetch.

Output: .arena_state/repo_pool.json

Usage::

    python scripts/fetch_repos.py [--dry-run]

Environment variables:
    GITHUB_TOKEN  — required for higher rate limits.
"""

import argparse
import json
import logging
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from utils import arena_week_id, github_get

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

ANCHOR_PATH = Path("config/anchor_repos.yml")
POOL_PATH = Path(".arena_state/repo_pool.json")

POOL_TARGET = 250
ANCHOR_TARGET = 50
DYNAMIC_TARGET = POOL_TARGET - ANCHOR_TARGET  # 200
PAGES_PER_QUERY = 2  # 100 results per page → 200 max per query
PER_PAGE = 100

SEARCH_API_URL = "https://api.github.com/search/repositories"


# ── Search query templates ──────────────────────────────────────────


def build_queries(month_ago: str, six_months_ago: str) -> list[dict]:
    """Return the 12 search queries with date placeholders filled in.

    Each query is tagged with a ``bucket`` name used downstream to
    partition the pool into 'trending' vs 'dynamic' repos. The trending
    newcomers query is the only source of the 'trending' bucket; all
    other queries contribute to 'dynamic'.
    """
    return [
        # GFI-focused (gfi category)
        {
            "q": (
                f"good-first-issues:>3 pushed:>={month_ago} "
                f"stars:>1000 archived:false"
            ),
            "sort": "stars",
            "bucket": "dynamic",
        },
        {
            "q": (
                f"topic:machine-learning good-first-issues:>1 "
                f"pushed:>={month_ago} stars:>100 archived:false"
            ),
            "sort": "updated",
            "bucket": "dynamic",
        },
        {
            "q": (
                f"topic:deep-learning good-first-issues:>0 "
                f"pushed:>={month_ago} stars:>200 archived:false"
            ),
            "sort": "updated",
            "bucket": "dynamic",
        },
        {
            "q": (
                f"topic:data-science good-first-issues:>0 "
                f"pushed:>={month_ago} stars:>200 archived:false"
            ),
            "sort": "updated",
            "bucket": "dynamic",
        },
        {
            "q": (
                f"topic:geospatial good-first-issues:>0 "
                f"pushed:>={month_ago} stars:>50 archived:false"
            ),
            "sort": "updated",
            "bucket": "dynamic",
        },
        {
            "q": (
                f"language:python good-first-issues:>3 "
                f"pushed:>={month_ago} stars:>500 archived:false"
            ),
            "sort": "updated",
            "bucket": "dynamic",
        },
        # Help-wanted-focused (bug + hard categories)
        {
            "q": (
                f"help-wanted-issues:>10 pushed:>={month_ago} "
                f"stars:>2000 archived:false"
            ),
            "sort": "updated",
            "bucket": "dynamic",
        },
        {
            "q": (
                f"topic:llm help-wanted-issues:>3 "
                f"pushed:>={month_ago} stars:>500 archived:false"
            ),
            "sort": "stars",
            "bucket": "dynamic",
        },
        {
            "q": (
                f"topic:machine-learning help-wanted-issues:>3 "
                f"pushed:>={month_ago} stars:>500 archived:false"
            ),
            "sort": "stars",
            "bucket": "dynamic",
        },
        {
            "q": (
                f"topic:nlp help-wanted-issues:>2 "
                f"pushed:>={month_ago} stars:>200 archived:false"
            ),
            "sort": "updated",
            "bucket": "dynamic",
        },
        {
            "q": (
                f"topic:computer-vision help-wanted-issues:>2 "
                f"pushed:>={month_ago} stars:>200 archived:false"
            ),
            "sort": "updated",
            "bucket": "dynamic",
        },
        # Trending newcomers (catches openclaw-style explosions)
        {
            "q": (
                f"created:>={six_months_ago} stars:>5000 "
                f"archived:false fork:false"
            ),
            "sort": "stars",
            "bucket": "trending",
        },
    ]


# ── Noise filtering ─────────────────────────────────────────────────

NOISE_TOPICS = {
    # Awesome-list style
    "awesome",
    "awesome-list",
    "awesome-lists",
    "curated-list",
    "list",
    "lists",
    # Learning material
    "tutorial",
    "tutorials",
    "cheatsheet",
    "cheatsheets",
    "learning-resources",
    "roadmap",
    "roadmaps",
    "interview",
    "interview-questions",
    "books",
    "free",
    # AI-skill packs (very common in recent trending)
    "skills",
    "skill",
    "claude-skills",
    "agent-skills",
    "ai-skills",
    "claude-code-skills",
}

NOISE_NAME_PATTERNS = [
    r"^awesome[-_]",
    r"^awesome$",
    r"[-_]tutorials?$",
    r"[-_]roadmaps?$",
    r"[-_]cheatsheets?$",
    r"[-_]books?$",
    r"[-_]examples?$",
    r"[-_]resources?$",
    r"[-_]skills?$",
    r"^oh[-_]my[-_]",
    r"[-_]lists?$",
    r"^learn[-_]",
]

# Real software projects accumulate many issues; awesome-lists rarely do.
MIN_OPEN_ISSUES = 20


def is_noise(repo: dict) -> bool:
    """Return True if a repo looks like a list/tutorial/skill-pack."""
    topics = set(repo.get("topics", []) or [])
    if topics & NOISE_TOPICS:
        return True
    name = (repo.get("name") or "").lower()
    if any(re.search(p, name) for p in NOISE_NAME_PATTERNS):
        return True
    if not repo.get("has_issues", True):
        return True
    if repo.get("open_issues_count", 0) < MIN_OPEN_ISSUES:
        return True
    return False


# ── File I/O ────────────────────────────────────────────────────────


def load_anchor_repos() -> list[dict]:
    """Load the curated anchor repo list from config/anchor_repos.yml."""
    with open(ANCHOR_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("repos", [])


def save_repo_pool(pool: dict) -> None:
    """Write pool to .arena_state/repo_pool.json."""
    POOL_PATH.parent.mkdir(exist_ok=True)
    with open(POOL_PATH, "w", encoding="utf-8") as f:
        json.dump(pool, f, indent=2)
    log.info(f"Pool saved to {POOL_PATH}")


# ── Search ──────────────────────────────────────────────────────────


def search_repos(
    query: str, sort: str, max_pages: int = PAGES_PER_QUERY
) -> list[dict]:
    """Search GitHub repos. Returns a list of normalized repo dicts.

    Each result includes: owner, repo, stars, topics, has_issues,
    open_issues_count, name. The Search API returns these fields by
    default — no preview header needed.
    """
    results = []
    for page in range(1, max_pages + 1):
        params = {
            "q": query,
            "sort": sort,
            "order": "desc",
            "per_page": PER_PAGE,
            "page": page,
        }
        try:
            resp = github_get(SEARCH_API_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            log.warning(f"Search failed (page {page}): {exc}")
            break

        items = payload.get("items", [])
        if not items:
            break

        for item in items:
            owner_login = (item.get("owner") or {}).get("login")
            if not owner_login:
                continue
            results.append(
                {
                    "owner": owner_login,
                    "repo": item.get("name"),
                    "name": item.get("name"),
                    "stars": item.get("stargazers_count", 0),
                    "topics": item.get("topics", []) or [],
                    "has_issues": item.get("has_issues", True),
                    "open_issues_count": item.get("open_issues_count", 0),
                }
            )

        # Stop early if fewer than per_page items returned
        if len(items) < PER_PAGE:
            break

    return results


# ── Pool building ───────────────────────────────────────────────────


def fetch_dynamic_candidates(queries: list[dict]) -> dict[str, dict]:
    """Run all queries, dedupe, count query matches, filter noise.

    Returns a dict keyed by 'owner/repo' with merged repo info plus:
      - ``query_matches``: number of queries the repo appeared in
      - ``buckets``: set of query-bucket names the repo matched
        (e.g. {"dynamic"} or {"dynamic", "trending"})
    """
    candidates: dict[str, dict] = {}
    for i, q in enumerate(queries, 1):
        log.info(f"Query {i}/{len(queries)}: {q['q'][:80]}...")
        results = search_repos(q["q"], q["sort"])
        log.info(f"  → {len(results)} results")

        bucket = q.get("bucket", "dynamic")
        for repo in results:
            if is_noise(repo):
                continue
            key = f"{repo['owner']}/{repo['repo']}"
            if key in candidates:
                candidates[key]["query_matches"] += 1
                candidates[key]["buckets"].add(bucket)
            else:
                entry = dict(repo)
                entry["query_matches"] = 1
                entry["buckets"] = {bucket}
                candidates[key] = entry
    return candidates


def sample_dynamic(
    candidates: dict[str, dict],
    anchor_keys: set[str],
    rng: random.Random,
) -> list[dict]:
    """Remove anchor repos from candidates and shuffle uniformly.

    The caller decides how many to take from the front. The shuffle is
    driven by ``rng`` so runs with the same seed are reproducible and
    different seeds produce different week-to-week rotations.
    """
    filtered = [c for k, c in candidates.items() if k not in anchor_keys]
    rng.shuffle(filtered)
    return filtered


def _resolve_bucket(candidate: dict) -> str:
    """Resolve a dynamic candidate's final bucket name.

    Priority: ``trending`` if the repo matched the trending-newcomers
    query, otherwise ``dynamic``. Anchor repos are handled separately
    in ``build_pool`` and always take precedence over both.
    """
    buckets = candidate.get("buckets") or set()
    if "trending" in buckets:
        return "trending"
    return "dynamic"


def build_pool(anchor: list[dict], dynamic_sampled: list[dict]) -> dict:
    """Combine anchor + sampled dynamic to reach POOL_TARGET total.

    Each repo entry is tagged with ``source`` (one of
    ``anchor``/``trending``/``dynamic``). Anchor always wins when a
    repo could be classified multiple ways. The returned dict exposes
    per-bucket counts to simplify downstream consumption.
    """
    anchor_entries = [
        {"owner": r["owner"], "repo": r["repo"], "source": "anchor"}
        for r in anchor
    ]

    slots_for_dynamic = POOL_TARGET - len(anchor_entries)
    selected_dynamic = dynamic_sampled[:slots_for_dynamic]

    dynamic_entries = [
        {
            "owner": r["owner"],
            "repo": r["repo"],
            "source": _resolve_bucket(r),
        }
        for r in selected_dynamic
    ]

    repos = anchor_entries + dynamic_entries

    if len(dynamic_entries) < slots_for_dynamic:
        log.warning(
            f"Dynamic pool short: got {len(dynamic_entries)}, "
            f"wanted {slots_for_dynamic}. Total pool: {len(repos)}."
        )

    trending_count = sum(
        1 for r in dynamic_entries if r["source"] == "trending"
    )
    dynamic_only_count = len(dynamic_entries) - trending_count

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "anchor_count": len(anchor_entries),
        "trending_count": trending_count,
        "dynamic_count": dynamic_only_count,
        "total_count": len(repos),
        "repos": repos,
    }


# ── Entry point ─────────────────────────────────────────────────────


def main():
    """Fetch repos and write the weekly pool.

    If --dry-run is passed, the pool is built and summarized but
    nothing is written to disk.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print pool summary without writing to disk",
    )
    args = parser.parse_args()

    log.info("Loading anchor repos...")
    anchor = load_anchor_repos()
    log.info(f"  → {len(anchor)} anchor repos")
    anchor_keys = {f"{r['owner']}/{r['repo']}" for r in anchor}

    now = datetime.now(timezone.utc)
    month_ago = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    six_months_ago = (now - timedelta(days=183)).strftime("%Y-%m-%d")
    queries = build_queries(month_ago, six_months_ago)

    log.info(f"Running {len(queries)} search queries...")
    candidates = fetch_dynamic_candidates(queries)
    log.info(f"  → {len(candidates)} unique non-noise candidates")

    rng = random.Random(arena_week_id())
    dynamic_sampled = sample_dynamic(candidates, anchor_keys, rng)
    log.info(f"  → {len(dynamic_sampled)} candidates after removing anchors")

    pool = build_pool(anchor, dynamic_sampled)
    log.info(
        f"Pool built: {pool['total_count']} repos "
        f"({pool['anchor_count']} anchor + "
        f"{pool['trending_count']} trending + "
        f"{pool['dynamic_count']} dynamic)"
    )

    if args.dry_run:
        log.info("Dry run — skipping write.")
        print("\n=== ANCHOR (first 10) ===")
        for r in pool["repos"][:10]:
            print(f"  [{r['source']}] {r['owner']}/{r['repo']}")
        print("\n=== TRENDING ===")
        trending = [r for r in pool["repos"] if r["source"] == "trending"]
        for r in trending:
            print(f"  [{r['source']}] {r['owner']}/{r['repo']}")
        print("\n=== DYNAMIC (first 20) ===")
        dyn = [r for r in pool["repos"] if r["source"] == "dynamic"]
        for r in dyn[:20]:
            print(f"  [{r['source']}] {r['owner']}/{r['repo']}")
        print(
            f"\n=== TOTAL: {pool['total_count']} "
            f"(anchor={pool['anchor_count']}, "
            f"trending={pool['trending_count']}, "
            f"dynamic={pool['dynamic_count']}) ==="
        )
        return

    save_repo_pool(pool)


if __name__ == "__main__":
    main()
