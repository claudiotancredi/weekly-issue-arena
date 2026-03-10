# Contributing to Weekly Issue Arena

Thanks for your interest! There are two ways to contribute:

## 1. Play the Arena (contribute to listed issues)

Just pick an issue from the README and open a PR in that repo. No registration needed.
Points are tracked automatically.

## 2. Improve the Arena itself

### Suggest a new repo
Edit [`config/repos.yml`](config/repos.yml) and open a PR. Good candidates:
- Actively maintained (recent commits)
- Uses standard GitHub labels (`good first issue`, `bug`, etc.)
- Welcomes external contributions

### Fix a bug or improve the scripts
- Fork, clone, make changes, open a PR
- Scripts are in [`scripts/`](scripts/) and are plain Python — no framework needed
- Please test locally before opening a PR: `python scripts/fetch_issues.py` (set `GITHUB_TOKEN` first)

### Report issues
Open a GitHub Issue in this repo. Include:
- What you expected
- What happened instead
- Any relevant logs or screenshots

### Discuss ideas
Use the [Discussions](../../discussions) tab for feature ideas, edge cases, or general feedback.

---

All contributions are welcome, from fixing a typo to overhauling the tracking logic.
