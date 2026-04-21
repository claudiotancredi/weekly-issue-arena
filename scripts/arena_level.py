"""Arena level logic — collective points unlock more weekly issues.

The arena's level is a pure function of the sum of every player's
``total_points`` in ``scores.json``. Each level above 0 adds a bonus
to the per-category issue limits. Thresholds and bonuses live in
``config/arena_levels.json`` so Python (issue selection) and the
TypeScript site (UI) read the exact same numbers.
"""

import json
import logging
from pathlib import Path

from utils import atomic_write_json

log = logging.getLogger(__name__)

CONFIG_PATH = Path("config/arena_levels.json")
MILESTONES_PATH = Path(".arena_state/milestones.json")

# Hard fallback if config is missing or malformed — keeps fetch alive.
_FALLBACK_BASELINE = {"gfi": 20, "bug": 14, "hard": 10}
_FALLBACK_LEVELS = [
    {"level": 0, "threshold": 0, "bonus": {"gfi": 0, "bug": 0, "hard": 0}}
]


def load_levels_config(path: Path = CONFIG_PATH) -> dict:
    """Load arena levels config or fall back to a level-0-only schema."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if "baseline" not in data or "levels" not in data:
            raise ValueError("missing baseline/levels keys")
        return data
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log.warning(
            f"Could not load {path}: {exc} — using level 0 baseline only."
        )
        return {
            "version": 1,
            "baseline": dict(_FALLBACK_BASELINE),
            "levels": list(_FALLBACK_LEVELS),
        }


def compute_arena_points(scores: dict) -> int:
    """Sum total_points across all players in scores.json."""
    return sum(
        int(p.get("total_points", 0))
        for p in scores.get("players", {}).values()
    )


def compute_arena_level(arena_points: int, config: dict) -> int:
    """Highest level whose threshold the arena has crossed."""
    levels = config.get("levels", _FALLBACK_LEVELS)
    return max(
        (lv["level"] for lv in levels if arena_points >= lv["threshold"]),
        default=0,
    )


def get_level_entry(level: int, config: dict) -> dict:
    """Return the full {level, threshold, bonus} dict for a given level."""
    levels = config.get("levels", _FALLBACK_LEVELS)
    for lv in levels:
        if lv["level"] == level:
            return lv
    # Clamp to highest level if requested level is above max.
    return max(levels, key=lambda lv: lv["level"])


def get_next_level_entry(level: int, config: dict) -> dict | None:
    """Return the entry for ``level + 1`` or None if at max."""
    levels = config.get("levels", _FALLBACK_LEVELS)
    next_levels = [lv for lv in levels if lv["level"] == level + 1]
    return next_levels[0] if next_levels else None


def effective_limits(level: int, config: dict) -> dict[str, int]:
    """Per-category issue limits for the given arena level.

    Baseline + bonus for that level. Never returns less than baseline.
    """
    baseline = config.get("baseline", _FALLBACK_BASELINE)
    entry = get_level_entry(level, config)
    bonus = entry.get("bonus", {})
    return {
        cat: int(baseline.get(cat, 0)) + int(bonus.get(cat, 0))
        for cat in baseline
    }


def total_issues_at_level(level: int, config: dict) -> int:
    """Sum of effective per-category limits for a given level."""
    return sum(effective_limits(level, config).values())


def load_milestones(path: Path = MILESTONES_PATH) -> dict:
    """Load milestones state or return a fresh level-0 default."""
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning(f"Could not read {path}: {exc} — starting fresh.")
    return {
        "current_level": 0,
        "current_arena_points": 0,
        "discussion_node_id": None,
        "history": [],
    }


def save_milestones(milestones: dict, path: Path = MILESTONES_PATH) -> None:
    """Persist milestones state atomically."""
    atomic_write_json(path, milestones)


def current_level_from_state() -> int:
    """Read the persisted current level (defaults to 0 on first run)."""
    return int(load_milestones().get("current_level", 0))
