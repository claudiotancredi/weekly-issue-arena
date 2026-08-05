# This directory stores persistent arena state.
# It is committed to the repo and updated automatically by GitHub Actions.
# Do not edit these files manually.
#
# preferences.json    — who opted into the arena and what they consented to. Written by scripts/sync_preferences.py from the "Join the Arena" / "Leave the Arena" issue forms. Every other job reads it first: nobody absent from it is fetched, credited, listed or mentioned.
# scores.json         — points, ranks and contribution history (participants only).
# issues.json         — the rolling 28-week window of tracked issues.
# current_issues.json — this week's issues with their live status flags.
# milestones.json     — arena level and level-up history.
# repo_pool.json      — the weekly pool of repositories issues are pulled from.
