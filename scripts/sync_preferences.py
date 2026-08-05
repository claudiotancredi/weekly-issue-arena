#!/usr/bin/env python3
"""Apply *Join the Arena* / *Leave the Arena* issue forms to preferences.

Reads consent-form issues, stores the submitter's choices in
``.arena_state/preferences.json``, then closes the issue with a summary
comment. Every other job in the arena reads that file to decide whether a
user may be tracked, listed, or mentioned.

The account whose preferences change is always the **issue author**. The
body is only read for the checkbox answers, so nobody can opt somebody
else in (or out) by typing a username.

Usage::

    python scripts/sync_preferences.py                 # sweep open issues
    python scripts/sync_preferences.py --issue 42      # one issue
    python scripts/sync_preferences.py --dry-run       # report only

Environment variables:
    GITHUB_TOKEN       — required to comment and close.
    GITHUB_REPOSITORY  — "owner/name"; falls back to the arena repo.
"""

import argparse
import json
import logging
import os
import re
from pathlib import Path

from preferences import (
    get_user,
    is_issue_processed,
    load_preferences,
    mark_issue_processed,
    remove_user,
    save_preferences,
    upsert_user,
)
from utils import atomic_write_json, github_get, github_patch, github_post

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

SCORES_PATH = Path(".arena_state/scores.json")

DEFAULT_REPO = "claudiotancredi/weekly-issue-arena"

# Section headings, as GitHub renders them from the issue-form field
# labels. Changing a label in .github/ISSUE_TEMPLATE/*.yml means changing
# the matching constant here — the tests pin both together.
JOIN_LEADERBOARD_HEADING = "Public leaderboard"
JOIN_DISCUSSION_HEADING = "Personal arena update discussion"
JOIN_NOTIFICATIONS_HEADING = "Notifications"
LEAVE_HEADING = "Confirm removal"

LEAVE_FORM_LINK = (
    "https://github.com/claudiotancredi/weekly-issue-arena/"
    "issues/new?template=leave_the_arena.yml"
)
JOIN_FORM_LINK = (
    "https://github.com/claudiotancredi/weekly-issue-arena/"
    "issues/new?template=join_the_arena.yml"
)

_HEADING_RE = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
_CHECKED_RE = re.compile(r"^\s*-\s*\[[xX]\]", re.MULTILINE)
_UNCHECKED_RE = re.compile(r"^\s*-\s*\[\s*\]", re.MULTILINE)


def repo_slug() -> str:
    """Return ``owner/name`` for the arena repository."""
    return os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO


# ── Body parsing ─────────────────────────────────────────────


def split_sections(body: str) -> dict[str, str]:
    """Split a rendered issue-form body into ``{heading: text}``.

    Headings are lower-cased so lookups are case-insensitive.
    """
    body = body or ""
    sections: dict[str, str] = {}
    matches = list(_HEADING_RE.finditer(body))
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[match.group(1).strip().lower()] = body[start:end]
    return sections


def checkbox_state(sections: dict[str, str], heading: str) -> bool | None:
    """Return the checkbox answer under *heading*.

    ``True`` when ticked, ``False`` when present but unticked, ``None``
    when the section is missing entirely.
    """
    text = sections.get(heading.strip().lower())
    if text is None:
        return None
    if _CHECKED_RE.search(text):
        return True
    if _UNCHECKED_RE.search(text):
        return False
    return None


def classify_form(body: str) -> str | None:
    """Return ``"join"``, ``"leave"``, or ``None`` for an issue body.

    Detection is structural — it looks for the headings the issue forms
    produce — so an ordinary issue that happens to carry the label is
    never mistaken for a consent submission.
    """
    sections = split_sections(body)
    if LEAVE_HEADING.lower() in sections:
        return "leave"
    if JOIN_LEADERBOARD_HEADING.lower() in sections:
        return "join"
    return None


def parse_join(body: str) -> dict | None:
    """Extract join choices from a form body.

    Returns ``None`` when the required leaderboard consent is absent or
    unticked — the form marks it required, so an unticked box means the
    body was hand-edited and there is no consent to record.
    """
    sections = split_sections(body)
    leaderboard = checkbox_state(sections, JOIN_LEADERBOARD_HEADING)
    if leaderboard is not True:
        return None
    return {
        "leaderboard": True,
        "discussion": checkbox_state(sections, JOIN_DISCUSSION_HEADING)
        is True,
        "notifications_ack": checkbox_state(
            sections, JOIN_NOTIFICATIONS_HEADING
        )
        is True,
    }


def parse_leave(body: str) -> bool:
    """Return True if the leave form's confirmation box is ticked."""
    return checkbox_state(split_sections(body), LEAVE_HEADING) is True


# ── Scores purge ─────────────────────────────────────────────


def load_scores() -> dict:
    """Load scores.json, or an empty scaffold when it does not exist."""
    if SCORES_PATH.exists():
        try:
            with open(SCORES_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning(f"Could not read {SCORES_PATH}: {exc}")
    return {"players": {}, "credited_issues": [], "weekly": {}}


def save_scores(scores: dict) -> None:
    """Persist scores.json atomically."""
    atomic_write_json(SCORES_PATH, scores)


def purge_player(scores: dict, username: str) -> tuple[bool, str | None]:
    """Erase a player from scores.json.

    Removes the player entry and every weekly-contributor mention, and
    returns ``(removed, discussion_node_id)`` so the caller can delete the
    matching Discussion thread. ``credited_issues`` is deliberately left
    alone: it stores issue keys, not usernames, and clearing it would let
    an already-settled issue be credited a second time.
    """
    target = username.strip().lower()
    node_id: str | None = None
    removed = False

    for key in list(scores.get("players", {})):
        if key.strip().lower() != target:
            continue
        entry = scores["players"].pop(key)
        removed = True
        if isinstance(entry, dict):
            node_id = entry.get("discussion_node_id")

    weekly = scores.get("weekly", {})
    for week_id in list(weekly):
        kept = [u for u in weekly[week_id] if u.strip().lower() != target]
        if kept:
            weekly[week_id] = kept
        else:
            weekly.pop(week_id)

    return removed, node_id


def player_discussion_id(scores: dict, username: str) -> str | None:
    """Return the stored Discussion node ID for *username*, if any."""
    target = username.strip().lower()
    for key, entry in scores.get("players", {}).items():
        if key.strip().lower() == target and isinstance(entry, dict):
            return entry.get("discussion_node_id")
    return None


def clear_player_discussion(scores: dict, username: str) -> None:
    """Forget a player's Discussion node ID after the thread is gone."""
    target = username.strip().lower()
    for key, entry in scores.get("players", {}).items():
        if key.strip().lower() == target and isinstance(entry, dict):
            entry.pop("discussion_node_id", None)


def delete_thread(node_id: str) -> bool:
    """Delete a Discussion thread, logging and swallowing failures.

    A thread we cannot delete must not abort the rest of the opt-out —
    the preference change is what actually stops future notifications.
    """
    try:
        from discussions import delete_discussion

        delete_discussion(node_id)
        return True
    except Exception as exc:
        log.warning(f"Could not delete discussion {node_id}: {exc}")
        return False


# ── GitHub I/O ───────────────────────────────────────────────


def fetch_issue(number: int) -> dict | None:
    """Fetch a single issue by number."""
    url = f"https://api.github.com/repos/{repo_slug()}/issues/{number}"
    try:
        resp = github_get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.warning(f"Could not fetch issue #{number}: {exc}")
        return None


def fetch_open_issues() -> list[dict]:
    """Fetch every open issue in the arena repo (pull requests excluded)."""
    url = (
        f"https://api.github.com/repos/{repo_slug()}/issues"
        f"?state=open&per_page=100"
    )
    issues: list[dict] = []
    try:
        while url:
            resp = github_get(url)
            resp.raise_for_status()
            issues.extend(resp.json())
            url = resp.links.get("next", {}).get("url")
    except Exception as exc:
        log.warning(f"Could not list open issues: {exc}")
        return []
    return [i for i in issues if "pull_request" not in i]


def comment_and_close(number: int, comment: str) -> None:
    """Post a summary comment and close the consent-form issue."""
    base = f"https://api.github.com/repos/{repo_slug()}/issues/{number}"
    try:
        resp = github_post(f"{base}/comments", {"body": comment})
        resp.raise_for_status()
    except Exception as exc:
        log.warning(f"Could not comment on issue #{number}: {exc}")
    try:
        resp = github_patch(
            base, {"state": "closed", "state_reason": "completed"}
        )
        resp.raise_for_status()
        log.info(f"Closed consent issue #{number}")
    except Exception as exc:
        log.warning(f"Could not close issue #{number}: {exc}")


# ── Comment bodies ───────────────────────────────────────────


def join_comment(username: str, choices: dict, updated: bool) -> str:
    """Confirmation comment for a stored join submission."""
    verb = "updated" if updated else "recorded"
    if choices["discussion"]:
        discussion_line = (
            "- **Personal discussion thread:** yes — one thread will be "
            "opened the first time a qualifying PR of yours is detected, "
            "and every later update lands there as a comment."
        )
    else:
        discussion_line = (
            "- **Personal discussion thread:** no — you will earn points "
            "without ever being mentioned in a Discussion."
        )
    return (
        f"Thanks @{username}, your preferences have been {verb}. "
        f"You are in the arena. \U0001f3df️\n\n"
        f"- **Public leaderboard:** yes\n"
        f"{discussion_line}\n\n"
        f"Nothing else happens until you open a pull request that closes "
        f"an arena issue — that is when the arena starts counting.\n\n"
        f"Want to change a choice? Submit the "
        f"[Join the Arena]({JOIN_FORM_LINK}) form again. Want out? "
        f"[Leave the Arena]({LEAVE_FORM_LINK}).\n\n"
        f"*Closing this issue — the form has done its job.*"
    )


def leave_comment(username: str, was_member: bool, purged: bool) -> str:
    """Confirmation comment for a processed leave submission."""
    if not was_member and not purged:
        return (
            f"@{username}, there was nothing on record for your account — "
            f"you were not in the arena to begin with, so nothing needed "
            f"removing.\n\n"
            f"*Closing this issue.*"
        )
    lines = [
        f"@{username}, you have been removed from the arena. \U0001f6aa\n",
        "- Your entry in `preferences.json` is gone, so no future PR of "
        "yours will be tracked.",
    ]
    if purged:
        lines.append(
            "- Your points, rank and contribution history have been erased "
            "from the leaderboard and the website."
        )
    lines.append(
        "- Your personal arena Discussion thread has been deleted if one "
        "existed."
    )
    lines.append(
        "\nYour pull requests are untouched — they live upstream and stay "
        "merged. If you ever change your mind, the "
        f"[Join the Arena]({JOIN_FORM_LINK}) form is always open.\n"
    )
    lines.append("*Closing this issue.*")
    return "\n".join(lines)


def invalid_comment(username: str) -> str:
    """Comment for a consent form that carries no usable answer."""
    return (
        f"@{username}, this form came through without the required "
        f"confirmation ticked, so there was nothing to record. Nothing "
        f"about your account has changed.\n\n"
        f"If you meant to join, please submit the "
        f"[Join the Arena]({JOIN_FORM_LINK}) form again and tick the "
        f"required boxes.\n\n"
        f"*Closing this issue.*"
    )


# ── Processing ───────────────────────────────────────────────


def process_issue(
    issue: dict,
    prefs: dict,
    scores: dict,
    dry_run: bool = False,
) -> str | None:
    """Apply one consent-form issue. Returns the action taken, or None.

    Mutates ``prefs`` and ``scores`` in place; the caller persists them.
    """
    number = issue.get("number")
    body = issue.get("body") or ""
    author = (issue.get("user") or {}).get("login")

    if not author:
        log.warning(f"Issue #{number} has no author — skipping")
        return None

    kind = classify_form(body)
    if kind is None:
        return None

    if is_issue_processed(prefs, number):
        log.info(f"Issue #{number} already processed — skipping")
        return None

    if kind == "join":
        choices = parse_join(body)
        if choices is None:
            log.warning(
                f"Issue #{number} from @{author}: join form without "
                f"leaderboard consent — recording nothing"
            )
            if not dry_run:
                mark_issue_processed(prefs, number)
                comment_and_close(number, invalid_comment(author))
            return "join-invalid"

        existing = get_user(prefs, author)
        updated = existing is not None
        had_discussion = bool(existing and existing.get("discussion"))

        if not dry_run:
            upsert_user(
                prefs,
                author,
                leaderboard=choices["leaderboard"],
                discussion=choices["discussion"],
                notifications_ack=choices["notifications_ack"],
                source_issue=number,
            )
            # Withdrawing discussion consent must take down the thread
            # that consent created — otherwise a public @mention of the
            # user outlives their permission for it.
            if had_discussion and not choices["discussion"]:
                node_id = player_discussion_id(scores, author)
                if node_id and delete_thread(node_id):
                    clear_player_discussion(scores, author)
            mark_issue_processed(prefs, number)
            comment_and_close(number, join_comment(author, choices, updated))

        log.info(
            f"@{author} joined (leaderboard=True, "
            f"discussion={choices['discussion']}) via #{number}"
        )
        return "join"

    # kind == "leave"
    if not parse_leave(body):
        log.warning(
            f"Issue #{number} from @{author}: leave form without "
            f"confirmation — recording nothing"
        )
        if not dry_run:
            mark_issue_processed(prefs, number)
            comment_and_close(number, invalid_comment(author))
        return "leave-invalid"

    node_id = player_discussion_id(scores, author)
    if not dry_run:
        was_member = remove_user(prefs, author)
        purged, purged_node_id = purge_player(scores, author)
        node_id = node_id or purged_node_id
        if node_id:
            delete_thread(node_id)
        mark_issue_processed(prefs, number)
        comment_and_close(number, leave_comment(author, was_member, purged))
        log.info(
            f"@{author} left the arena via #{number} "
            f"(preferences={was_member}, scores={purged})"
        )
    return "leave"


def main() -> None:
    """Sync consent-form issues into preferences.json."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--issue",
        type=int,
        default=None,
        help="Process a single issue number instead of sweeping",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing or closing",
    )
    args = parser.parse_args()

    prefs = load_preferences()
    scores = load_scores()

    if args.issue is not None:
        issue = fetch_issue(args.issue)
        issues = [issue] if issue else []
    else:
        issues = fetch_open_issues()

    actions: list[str] = []
    for issue in issues:
        action = process_issue(issue, prefs, scores, dry_run=args.dry_run)
        if action:
            actions.append(f"#{issue.get('number')}: {action}")

    if args.dry_run:
        log.info("Dry run — no files written, no issues closed.")
        for action in actions:
            print(f"  {action}")
        return

    if actions:
        save_preferences(prefs)
        save_scores(scores)
        log.info(f"Processed {len(actions)} consent form(s).")
        for action in actions:
            log.info(f"  {action}")
    else:
        log.info("No consent forms to process.")


if __name__ == "__main__":
    main()
