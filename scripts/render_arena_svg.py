"""Render assets/arena_level.svg from the current arena state.

The SVG is committed alongside ``.arena_state/milestones.json`` and
embedded in the README via a static ``<img>`` tag. GitHub renders
committed SVGs inline, so the bar updates automatically every time the
hourly leaderboard job re-renders this file.
"""

import logging
from pathlib import Path

from arena_level import (
    effective_limits,
    get_level_entry,
    get_next_level_entry,
    load_levels_config,
    load_milestones,
    total_issues_at_level,
)
from utils import atomic_write_text

log = logging.getLogger(__name__)

SVG_PATH = Path("assets/arena_level.svg")

# Colors mirror the site's tailwind palette so the README and the live
# site feel like one product.
_BG = "#12121a"
_BG_INNER = "#0a0a0f"
_BORDER = "#2a2a3e"
_TRACK = "#1f1f2e"
_ACCENT = "#6366f1"
_ACCENT_2 = "#8b5cf6"
_TEXT = "#e4e4e7"
_TEXT_MUTED = "#a1a1aa"
_TEXT_DIM = "#71717a"

_FONT = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', "
    "Helvetica, Arial, sans-serif"
)

_WIDTH = 720
_HEIGHT = 180
_PAD = 28
_BAR_X = _PAD
_BAR_Y = 116
_BAR_W = _WIDTH - 2 * _PAD
_BAR_H = 14
_BAR_RADIUS = 7


def _build_svg(
    level: int,
    arena_points: int,
    next_threshold: int | None,
    threshold: int,
    total_issues: int,
    total_issues_next: int | None,
    is_max: bool,
) -> str:
    if is_max or next_threshold is None:
        progress = 1.0
        progress_label = f"{arena_points} pts • MAX LEVEL"
        next_label = f"{total_issues} issues per week"
    else:
        span = max(next_threshold - threshold, 1)
        progress = max(0.0, min(1.0, (arena_points - threshold) / span))
        progress_label = (
            f"{arena_points} / {next_threshold} pts → Level {level + 1}"
        )
        delta = (total_issues_next or total_issues) - total_issues
        next_label = f"+{delta} issues at Level {level + 1}"

    fill_w = max(int(_BAR_W * progress), 0)

    # Wrapper avoids zero-width fill rect (would still be valid SVG, but
    # cleaner to omit when there is no progress yet).
    fill_rect = ""
    if fill_w > 0:
        fill_rect = (
            f'<rect x="{_BAR_X}" y="{_BAR_Y}" width="{fill_w}" '
            f'height="{_BAR_H}" rx="{_BAR_RADIUS}" ry="{_BAR_RADIUS}" '
            f'fill="url(#barGrad)"/>'
        )

    # Tick marks at every level threshold so visitors can see the curve.
    tick_marks = ""
    if not is_max and next_threshold is not None:
        # Draw the next-level mark only — keep the bar uncluttered.
        tick_x = _BAR_X + _BAR_W - 2
        tick_marks = (
            f'<rect x="{tick_x}" y="{_BAR_Y - 3}" width="2" '
            f'height="{_BAR_H + 6}" rx="1" fill="{_ACCENT_2}"/>'
        )

    label_style = (
        f".label {{ font-family: {_FONT}; font-weight: 600; "
        f"letter-spacing: 1.6px; text-transform: uppercase; }}"
    )
    lines = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{_WIDTH}" height="{_HEIGHT}" '
            f'viewBox="0 0 {_WIDTH} {_HEIGHT}" role="img" '
            f'aria-label="Arena Level {level} — '
            f'{arena_points} arena points">'
        ),
        "  <defs>",
        '    <linearGradient id="barGrad" x1="0" y1="0" x2="1" y2="0">',
        f'      <stop offset="0%" stop-color="{_ACCENT}"/>',
        f'      <stop offset="100%" stop-color="{_ACCENT_2}"/>',
        "    </linearGradient>",
        '    <linearGradient id="bgGrad" x1="0" y1="0" x2="0" y2="1">',
        f'      <stop offset="0%" stop-color="{_BG}"/>',
        f'      <stop offset="100%" stop-color="{_BG_INNER}"/>',
        "    </linearGradient>",
        "    <style>",
        f"      {label_style}",
        f"      .level-num {{ font-family: {_FONT}; font-weight: 800; }}",
        f"      .level-tag {{ font-family: {_FONT}; font-weight: 700; }}",
        f"      .stat {{ font-family: {_FONT}; font-weight: 600; }}",
        f"      .progress {{ font-family: {_FONT}; font-weight: 500; }}",
        f"      .delta {{ font-family: {_FONT}; font-weight: 600; }}",
        "    </style>",
        "  </defs>",
        (
            f'  <rect x="1" y="1" width="{_WIDTH - 2}" '
            f'height="{_HEIGHT - 2}" rx="14" ry="14" '
            f'fill="url(#bgGrad)" stroke="{_BORDER}" '
            f'stroke-width="1.5"/>'
        ),
        (
            f'  <text x="{_PAD}" y="42" class="label" font-size="11" '
            f'fill="{_TEXT_DIM}">ARENA LEVEL</text>'
        ),
        (
            f'  <text x="{_WIDTH - _PAD}" y="42" text-anchor="end" '
            f'class="label" font-size="11" fill="{_TEXT_MUTED}">'
            f"{total_issues} ISSUES / WEEK</text>"
        ),
        (
            f'  <text x="{_PAD}" y="92" class="level-num" '
            f'font-size="56" fill="{_ACCENT}">{level}</text>'
        ),
        (
            f'  <text x="{_PAD + 78}" y="74" class="level-tag" '
            f'font-size="14" fill="{_TEXT_MUTED}">LEVEL</text>'
        ),
        (
            f'  <text x="{_PAD + 78}" y="94" class="stat" '
            f'font-size="16" fill="{_TEXT}">Weekly Issue Arena</text>'
        ),
        (
            f'  <rect x="{_BAR_X}" y="{_BAR_Y}" width="{_BAR_W}" '
            f'height="{_BAR_H}" rx="{_BAR_RADIUS}" '
            f'ry="{_BAR_RADIUS}" fill="{_TRACK}"/>'
        ),
        f"  {fill_rect}",
        f"  {tick_marks}",
        (
            f'  <text x="{_PAD}" y="158" class="progress" '
            f'font-size="12" fill="{_TEXT_MUTED}">'
            f"{progress_label}</text>"
        ),
        (
            f'  <text x="{_WIDTH - _PAD}" y="158" text-anchor="end" '
            f'class="delta" font-size="12" fill="{_ACCENT}">'
            f"{next_label}</text>"
        ),
        "</svg>",
        "",
    ]
    return "\n".join(lines)


def render_arena_svg(
    milestones: dict | None = None,
    config: dict | None = None,
    out_path: Path = SVG_PATH,
) -> Path:
    """Render the arena level SVG to ``assets/arena_level.svg``.

    If ``milestones`` or ``config`` are not supplied, they are loaded
    from disk. Returns the output path.
    """
    if config is None:
        config = load_levels_config()
    if milestones is None:
        milestones = load_milestones()

    level = int(milestones.get("current_level", 0))
    arena_points = int(milestones.get("current_arena_points", 0))

    entry = get_level_entry(level, config)
    next_entry = get_next_level_entry(level, config)
    total_issues = sum(effective_limits(level, config).values())
    total_issues_next = (
        total_issues_at_level(level + 1, config) if next_entry else None
    )

    svg = _build_svg(
        level=level,
        arena_points=arena_points,
        next_threshold=next_entry["threshold"] if next_entry else None,
        threshold=entry["threshold"],
        total_issues=total_issues,
        total_issues_next=total_issues_next,
        is_max=next_entry is None,
    )

    out_path.parent.mkdir(exist_ok=True)
    atomic_write_text(out_path, svg)
    log.info(
        f"Rendered {out_path} (level={level}, "
        f"points={arena_points}, issues={total_issues})"
    )
    return out_path


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s: %(message)s"
    )
    render_arena_svg()
