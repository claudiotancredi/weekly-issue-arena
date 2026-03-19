# Contributing to Weekly Issue Arena

Thanks for your interest! There are two ways to contribute:

## 1. Play the Arena (contribute to listed issues)

Just pick an issue from the README and open a PR in that repo. No registration needed.
Points are tracked automatically.

## 2. Improve the Arena itself

### Development setup

```bash
# Clone and install dependencies
git clone https://github.com/claudiotancredi/weekly-issue-arena.git
cd weekly-issue-arena
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Set up pre-commit hooks (runs ruff lint + format on every commit)
pip install pre-commit
pre-commit install

# Set your GitHub token for API access
export GITHUB_TOKEN="ghp_..."
```

### Running tests

```bash
pytest tests/ -v
```

All tests use mocked HTTP responses — no GitHub token or network access required.

### Dry-run mode

Both scripts support `--dry-run` to preview changes without writing files:

```bash
python scripts/fetch_issues.py --dry-run    # Fetch issues, log output, skip file writes
python scripts/update_leaderboard.py --dry-run  # Check credits, skip file writes
```

### Suggest a new repo

Edit [`config/repos.yml`](config/repos.yml) and open a PR. Good candidates:
- Actively maintained (recent commits)
- Uses standard GitHub labels (`good first issue`, `bug`, etc.)
- Welcomes external contributions

### Fix a bug or improve the scripts

- Fork, clone, make changes, open a PR
- Scripts are in [`scripts/`](scripts/) and are plain Python — no framework needed
- Shared utilities live in [`scripts/utils.py`](scripts/utils.py)
- Run `ruff check scripts/` and `ruff format --check scripts/` before submitting
- Make sure `pytest tests/ -v` passes

### Report issues

Open a GitHub Issue in this repo. Include:
- What you expected
- What happened instead
- Any relevant logs or screenshots

### Discuss ideas

Use the [Discussions](https://github.com/claudiotancredi/weekly-issue-arena/discussions) tab for feature ideas, edge cases, or general feedback.

---

All contributions are welcome, from fixing a typo to overhauling the tracking logic.
