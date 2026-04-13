#!/usr/bin/env python3
"""One-shot: fill `language` on entries in current_issues.json and issues.json.

Useful after first deploy of the language feature, before the next
weekly fetch has run. Reuses the persistent language cache so repeated
invocations only hit GitHub for repos it has never seen.
"""

import json
import logging
from pathlib import Path

from fetch_issues import (
    LANGUAGES_PATH,
    fetch_repo_language,
    load_language_cache,
    save_language_cache,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

CURRENT = Path(".arena_state/current_issues.json")
WEEKLY = Path(".arena_state/issues.json")


def resolve(owner: str, repo: str, cache: dict) -> str | None:
    """Return the language for owner/repo, hitting GitHub if not cached."""
    key = f"{owner}/{repo}"
    if key in cache:
        return cache[key]
    lang = fetch_repo_language(owner, repo)
    cache[key] = lang
    return lang


def backfill_current(cache: dict) -> bool:
    """Stamp `language` on every entry in current_issues.json."""
    if not CURRENT.exists():
        return False
    data = json.loads(CURRENT.read_text(encoding="utf-8"))
    changed = False
    for entry in data:
        if entry.get("language"):
            continue
        entry["language"] = resolve(entry["owner"], entry["repo"], cache)
        changed = True
    if changed:
        CURRENT.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log.info(f"Updated {CURRENT}")
    return changed


def backfill_weekly(cache: dict) -> bool:
    """Stamp `language` on every issue in issues.json across all weeks."""
    if not WEEKLY.exists():
        return False
    data = json.loads(WEEKLY.read_text(encoding="utf-8"))
    changed = False
    for week in data.values():
        for cat_issues in week.get("issues", {}).values():
            for iss in cat_issues:
                if iss.get("language"):
                    continue
                iss["language"] = resolve(iss["owner"], iss["repo"], cache)
                changed = True
    if changed:
        WEEKLY.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log.info(f"Updated {WEEKLY}")
    return changed


def main() -> None:
    """Run the full backfill across current_issues.json and issues.json."""
    cache = load_language_cache()
    log.info(f"Loaded {len(cache)} cached languages from {LANGUAGES_PATH}")
    backfill_current(cache)
    backfill_weekly(cache)
    save_language_cache(cache)
    log.info(f"Cache now has {len(cache)} entries.")


if __name__ == "__main__":
    main()
