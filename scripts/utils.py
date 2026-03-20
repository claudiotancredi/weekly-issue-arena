"""Shared utilities for Weekly Issue Arena scripts."""

import logging
import os
import re
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"Bearer {GITHUB_TOKEN}"


def github_get(url: str, **kwargs) -> requests.Response:
    """Make a GET request to the GitHub API with rate-limit awareness.

    Logs a warning when remaining requests drop below 100 and
    raises ``SystemExit`` when the limit is exhausted.
    """
    kwargs.setdefault("timeout", 15)
    resp = requests.get(url, headers=HEADERS, **kwargs)

    remaining = resp.headers.get("X-RateLimit-Remaining")
    if remaining is not None:
        remaining = int(remaining)
        if remaining < 100:
            reset_ts = int(resp.headers.get("X-RateLimit-Reset", 0))
            reset_at = datetime.fromtimestamp(
                reset_ts, tz=timezone.utc
            ).isoformat()
            log.warning(
                f"GitHub API rate limit low: "
                f"{remaining} remaining. Resets at {reset_at}"
            )
        if resp.status_code == 403 and remaining == 0:
            log.error("GitHub API rate limit exhausted.")
            raise SystemExit(1)

    return resp


def arena_week_id(dt: datetime | None = None) -> str:
    """Return the arena week identifier for a given datetime.

    Arena weeks run Friday 17:00 UTC to the following Friday
    16:59 UTC.  The returned string uses the ISO year and week
    number of the Friday that starts the arena week.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)

    # Friday = weekday 4
    days_since_friday = (dt.weekday() - 4) % 7
    friday_start = dt - timedelta(days=days_since_friday)

    # If we are on Friday but before 17:00 UTC, the current
    # arena week hasn't started yet — use the previous Friday.
    if days_since_friday == 0 and dt.hour < 17:
        friday_start -= timedelta(weeks=1)

    return friday_start.strftime("%G-W%V")


def arena_week_start(dt: datetime | None = None) -> datetime:
    """Return the exact start of the current arena week (Friday 17:00:00 UTC).

    This is the canonical timestamp for ``listed_at`` so that the 7-day
    PR deadline is always deterministic regardless of cron drift.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)

    days_since_friday = (dt.weekday() - 4) % 7
    friday_start = dt - timedelta(days=days_since_friday)

    if days_since_friday == 0 and dt.hour < 17:
        friday_start -= timedelta(weeks=1)

    return friday_start.replace(hour=17, minute=0, second=0, microsecond=0)


def update_readme_section(content: str, tag: str, new_body: str) -> str:
    """Replace content between matching START/END markers."""
    pattern = rf"(<!-- {tag}:START -->).*?(<!-- {tag}:END -->)"
    replacement = rf"\1\n{new_body}\2"
    updated, count = re.subn(pattern, replacement, content, flags=re.DOTALL)
    if count == 0:
        log.warning(f"Marker {tag} not found in README")
    return updated
