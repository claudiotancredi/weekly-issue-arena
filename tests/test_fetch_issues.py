"""Tests for the fetch_issues module.

Covers get_issues_for_repo, fetch_all_issues,
enforce_repo_diversity, truncate_title, build_issue_table,
save_state, and save_current_issues.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "scripts")

import fetch_issues  # noqa: E402, I001


# ── helpers ─────────────────────────────────────────────────────


def _issue(
    owner="org",
    repo="project",
    number=1,
    title="Fix bug",
    url=None,
    repo_url=None,
):
    """Return a minimal issue dict for testing.

    Args:
        owner: Repository owner.
        repo: Repository name.
        number: Issue number.
        title: Issue title.
        url: Full issue URL.
        repo_url: Repository URL.

    Returns:
        dict: A minimal issue dictionary.
    """
    if url is None:
        url = f"https://github.com/{owner}/{repo}/issues/{number}"
    if repo_url is None:
        repo_url = f"https://github.com/{owner}/{repo}"
    return {
        "number": number,
        "title": title,
        "url": url,
        "owner": owner,
        "repo": repo,
        "repo_url": repo_url,
    }


# ── helpers for raw GitHub API responses ────────────────────────


def _gh_issue(issue_id, number, owner="org", repo="proj", is_pr=False):
    """Build a minimal GitHub API issue response."""
    d = {
        "id": issue_id,
        "number": number,
        "title": f"Issue #{number}",
        "html_url": f"https://github.com/{owner}/{repo}/issues/{number}",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-03-01T00:00:00Z",
        "user": {"login": "author"},
    }
    if is_pr:
        d["pull_request"] = {"url": "..."}
    return d


def _mock_github_get(responses):
    """Return a side_effect function that yields responses in order.

    Each entry in *responses* is a list of GitHub issue dicts
    for one label query.
    """
    calls = iter(responses)

    def _side_effect(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = next(calls)
        return resp

    return _side_effect


# ── get_issues_for_repo ────────────────────────────────────────


class TestGetIssuesForRepo:
    """Tests for get_issues_for_repo."""

    @patch("fetch_issues.github_get")
    def test_dedup_across_labels(self, mock_get):
        """Same issue from two labels is returned only once."""
        issue = _gh_issue(100, 1)
        mock_get.side_effect = _mock_github_get(
            [
                [issue],  # label "good first issue"
                [issue],  # label "beginner" — same issue
            ]
        )
        result = fetch_issues.get_issues_for_repo(
            "org", "proj", ["good first issue", "beginner"], limit=5
        )
        assert len(result) == 1
        assert result[0]["id"] == 100

    @patch("fetch_issues.github_get")
    def test_different_issues_from_labels(self, mock_get):
        """Different issues from different labels are all kept."""
        mock_get.side_effect = _mock_github_get(
            [
                [_gh_issue(100, 1)],
                [_gh_issue(200, 2)],
            ]
        )
        result = fetch_issues.get_issues_for_repo(
            "org", "proj", ["good first issue", "beginner"], limit=5
        )
        assert len(result) == 2

    @patch("fetch_issues.github_get")
    def test_prs_excluded_with_dedup(self, mock_get):
        """Pull requests are excluded even with dedup logic."""
        mock_get.side_effect = _mock_github_get(
            [
                [_gh_issue(100, 1), _gh_issue(200, 2, is_pr=True)],
            ]
        )
        result = fetch_issues.get_issues_for_repo(
            "org", "proj", ["bug"], limit=5
        )
        assert len(result) == 1
        assert result[0]["number"] == 1

    @patch("fetch_issues.github_get")
    def test_respects_limit(self, mock_get):
        """No more than *limit* issues are returned."""
        issues = [_gh_issue(i, i) for i in range(10)]
        mock_get.side_effect = _mock_github_get([issues])
        result = fetch_issues.get_issues_for_repo(
            "org", "proj", ["bug"], limit=3
        )
        assert len(result) == 3

    @patch("fetch_issues.github_get")
    def test_api_failure_returns_empty(self, mock_get):
        """API failure is handled gracefully."""
        mock_get.side_effect = Exception("rate limited")
        result = fetch_issues.get_issues_for_repo(
            "org", "proj", ["bug"], limit=5
        )
        assert result == []


# ── fetch_all_issues (cross-category dedup) ────────────────────


class TestFetchAllIssuesCrossCategory:
    """Tests for cross-category dedup in fetch_all_issues."""

    def _config(self, repos):
        return {
            "repos": repos,
            "label_mappings": {
                "gfi": ["good first issue"],
                "bug": ["bug"],
                "hard": ["hard"],
            },
            "limits": {"gfi": 20, "bug": 14, "hard": 10},
        }

    @patch("fetch_issues.has_linked_pr", return_value=False)
    @patch("fetch_issues.get_issues_for_repo")
    def test_multi_label_issue_lands_in_higher_category(
        self, mock_fetch, _mock_linked
    ):
        """Issue with both hard and gfi labels goes to hard."""
        # Same issue returned for all three categories
        shared = _gh_issue(100, 1)
        mock_fetch.return_value = [shared]

        config = self._config([{"owner": "org", "repo": "proj"}])
        result = fetch_issues.fetch_all_issues(config)

        # Should appear in hard (highest value, checked first)
        hard_nums = [i["number"] for i in result["hard"]]
        bug_nums = [i["number"] for i in result["bug"]]
        gfi_nums = [i["number"] for i in result["gfi"]]
        assert 1 in hard_nums
        assert 1 not in bug_nums
        assert 1 not in gfi_nums

    @patch("fetch_issues.has_linked_pr", return_value=False)
    @patch("fetch_issues.get_issues_for_repo")
    def test_multi_label_bug_and_gfi(self, mock_fetch, _mock_linked):
        """Issue with bug and gfi labels goes to bug (higher)."""
        shared = _gh_issue(100, 1)

        def side_effect(owner, repo, labels, limit):
            if labels == ["hard"]:
                return []
            return [shared]

        mock_fetch.side_effect = side_effect

        config = self._config([{"owner": "org", "repo": "proj"}])
        result = fetch_issues.fetch_all_issues(config)

        assert len(result["bug"]) == 1
        assert result["bug"][0]["number"] == 1
        assert len(result["gfi"]) == 0

    @patch("fetch_issues.has_linked_pr", return_value=False)
    @patch("fetch_issues.get_issues_for_repo")
    def test_distinct_issues_not_deduped(self, mock_fetch, _mock_linked):
        """Different issues in different categories all survive."""

        def side_effect(owner, repo, labels, limit):
            if labels == ["hard"]:
                return [_gh_issue(300, 3)]
            if labels == ["bug"]:
                return [_gh_issue(200, 2)]
            return [_gh_issue(100, 1)]

        mock_fetch.side_effect = side_effect

        config = self._config([{"owner": "org", "repo": "proj"}])
        result = fetch_issues.fetch_all_issues(config)

        assert len(result["hard"]) == 1
        assert len(result["bug"]) == 1
        assert len(result["gfi"]) == 1

    @patch("fetch_issues.has_linked_pr", return_value=False)
    @patch("fetch_issues.get_issues_for_repo")
    def test_dedup_across_repos(self, mock_fetch, _mock_linked):
        """Same issue number in different repos is NOT deduped."""

        def side_effect(owner, repo, labels, limit):
            if labels == ["bug"]:
                return [
                    _gh_issue(
                        100 if repo == "a" else 200,
                        1,
                        owner=owner,
                        repo=repo,
                    )
                ]
            return []

        mock_fetch.side_effect = side_effect

        config = self._config(
            [
                {"owner": "org", "repo": "a"},
                {"owner": "org", "repo": "b"},
            ]
        )
        result = fetch_issues.fetch_all_issues(config)

        assert len(result["bug"]) == 2

    @patch("fetch_issues.has_linked_pr", return_value=False)
    @patch("fetch_issues.get_issues_for_repo")
    def test_category_iteration_order(self, mock_fetch, _mock_linked):
        """Categories are checked hard -> bug -> gfi."""
        call_labels = []

        def side_effect(owner, repo, labels, limit):
            call_labels.append(labels[0])
            return []

        mock_fetch.side_effect = side_effect

        config = self._config([{"owner": "org", "repo": "proj"}])
        fetch_issues.fetch_all_issues(config)

        assert call_labels == ["hard", "bug", "good first issue"]

    @patch("fetch_issues.has_linked_pr")
    @patch("fetch_issues.get_issues_for_repo")
    def test_issues_with_linked_pr_filtered_out(self, mock_fetch, mock_linked):
        """Issues with an open linked PR are excluded."""

        def side_effect(owner, repo, labels, limit):
            if labels == ["bug"]:
                return [
                    _gh_issue(100, 1),
                    _gh_issue(200, 2),
                    _gh_issue(300, 3),
                ]
            return []

        mock_fetch.side_effect = side_effect
        # Issue #2 has a linked PR, others don't
        mock_linked.side_effect = lambda o, r, n: n == 2

        config = self._config([{"owner": "org", "repo": "proj"}])
        result = fetch_issues.fetch_all_issues(config)

        bug_nums = [i["number"] for i in result["bug"]]
        assert 2 not in bug_nums
        assert 1 in bug_nums
        assert 3 in bug_nums

    @patch("fetch_issues.has_linked_pr")
    @patch("fetch_issues.get_issues_for_repo")
    def test_all_issues_with_linked_pr_yields_empty(
        self, mock_fetch, mock_linked
    ):
        """All candidates having linked PRs yields an empty list."""
        mock_fetch.return_value = [_gh_issue(100, 1)]
        mock_linked.return_value = True

        config = self._config([{"owner": "org", "repo": "proj"}])
        result = fetch_issues.fetch_all_issues(config)

        assert result["hard"] == []
        assert result["bug"] == []
        assert result["gfi"] == []


# ── enforce_repo_diversity ──────────────────────────────────────


class TestEnforceRepoDiversity:
    """Tests for enforce_repo_diversity."""

    def test_under_limit_keeps_all(self):
        """Issues stay when repo count is within limit."""
        issues = [
            _issue("a", "r1", 1),
            _issue("a", "r1", 2),
        ]
        result = fetch_issues.enforce_repo_diversity(issues, max_per_repo=2)
        assert len(result) == 2

    def test_over_limit_caps(self):
        """Third issue from same repo is dropped."""
        issues = [
            _issue("a", "r1", 1),
            _issue("a", "r1", 2),
            _issue("a", "r1", 3),
        ]
        result = fetch_issues.enforce_repo_diversity(issues, max_per_repo=2)
        assert len(result) == 2
        assert result[0]["number"] == 1
        assert result[1]["number"] == 2

    def test_preserves_order(self):
        """Output order matches input order."""
        issues = [
            _issue("a", "r1", 10),
            _issue("b", "r2", 20),
            _issue("a", "r1", 11),
            _issue("b", "r2", 21),
        ]
        result = fetch_issues.enforce_repo_diversity(issues, max_per_repo=2)
        numbers = [i["number"] for i in result]
        assert numbers == [10, 20, 11, 21]

    def test_multiple_repos_capped_independently(self):
        """Each repo is capped independently."""
        issues = [
            _issue("a", "r1", 1),
            _issue("a", "r1", 2),
            _issue("a", "r1", 3),
            _issue("b", "r2", 4),
            _issue("b", "r2", 5),
            _issue("b", "r2", 6),
        ]
        result = fetch_issues.enforce_repo_diversity(issues, max_per_repo=2)
        assert len(result) == 4
        owners = {f"{i['owner']}/{i['repo']}": 0 for i in result}
        for i in result:
            owners[f"{i['owner']}/{i['repo']}"] += 1
        assert owners["a/r1"] == 2
        assert owners["b/r2"] == 2

    def test_empty_list(self):
        """Empty input yields empty output."""
        result = fetch_issues.enforce_repo_diversity([])
        assert result == []

    def test_max_per_repo_one(self):
        """Only the first issue per repo survives with limit 1."""
        issues = [
            _issue("a", "r1", 1),
            _issue("a", "r1", 2),
            _issue("b", "r2", 3),
        ]
        result = fetch_issues.enforce_repo_diversity(issues, max_per_repo=1)
        assert len(result) == 2
        assert result[0]["number"] == 1
        assert result[1]["number"] == 3

    def test_default_max_per_repo_is_two(self):
        """Default max_per_repo allows exactly two per repo."""
        issues = [_issue("x", "y", n) for n in range(5)]
        result = fetch_issues.enforce_repo_diversity(issues)
        assert len(result) == 2


# ── truncate_title ──────────────────────────────────────────────


class TestTruncateTitle:
    """Tests for truncate_title."""

    def test_short_title_unchanged(self):
        """Titles within the limit are returned as-is."""
        assert fetch_issues.truncate_title("Hello") == "Hello"

    def test_exact_length_unchanged(self):
        """Title at exactly max_len is not truncated."""
        title = "a" * 60
        assert fetch_issues.truncate_title(title) == title

    def test_long_title_truncated(self):
        """Titles longer than max_len get an ellipsis."""
        title = "a" * 61
        result = fetch_issues.truncate_title(title)
        assert result.endswith("...")
        assert len(result) == 60

    def test_custom_max_len(self):
        """Custom max_len is respected."""
        title = "abcdefghij"  # 10 chars
        result = fetch_issues.truncate_title(title, max_len=8)
        assert result == "abcde..."
        assert len(result) == 8

    def test_very_long_title(self):
        """Very long titles are still capped at max_len."""
        title = "x" * 500
        result = fetch_issues.truncate_title(title)
        assert len(result) == 60
        assert result == "x" * 57 + "..."

    def test_empty_string(self):
        """Empty string is returned unchanged."""
        assert fetch_issues.truncate_title("") == ""

    def test_max_len_three(self):
        """Edge case: max_len equal to ellipsis length."""
        result = fetch_issues.truncate_title("abcdef", max_len=3)
        assert result == "..."
        assert len(result) == 3


# ── build_issue_table ───────────────────────────────────────────


class TestBuildIssueTable:
    """Tests for build_issue_table."""

    def test_empty_list_fallback_row(self):
        """Empty list produces a 'no issues' placeholder row."""
        table = fetch_issues.build_issue_table([])
        assert "No issues found this week" in table
        assert table.startswith("| # |")

    def test_single_issue_row(self):
        """Single issue produces one numbered row."""
        issues = [
            _issue("org", "repo", 42, "My title"),
        ]
        table = fetch_issues.build_issue_table(issues)
        assert "| 1 |" in table
        assert "[My title]" in table
        assert "[org/repo]" in table
        assert "Open" in table

    def test_multiple_issues_numbered(self):
        """Multiple issues are numbered sequentially."""
        issues = [
            _issue("a", "r1", 1, "First"),
            _issue("b", "r2", 2, "Second"),
            _issue("c", "r3", 3, "Third"),
        ]
        table = fetch_issues.build_issue_table(issues)
        assert "| 1 |" in table
        assert "| 2 |" in table
        assert "| 3 |" in table

    def test_table_has_header(self):
        """Table starts with a proper Markdown header."""
        table = fetch_issues.build_issue_table([_issue()])
        lines = table.split("\n")
        assert "# | Title | Repository | Status" in lines[0]
        assert "---|-------" in lines[1]

    def test_issue_url_in_link(self):
        """Issue URL appears as a Markdown link."""
        issue = _issue("org", "repo", 99, "Bug")
        table = fetch_issues.build_issue_table([issue])
        expected_link = "[Bug](https://github.com/org/repo/issues/99)"
        assert expected_link in table

    def test_repo_url_in_link(self):
        """Repo URL appears as a Markdown link."""
        issue = _issue("org", "repo", 1, "T")
        table = fetch_issues.build_issue_table([issue])
        expected = "[org/repo](https://github.com/org/repo)"
        assert expected in table

    def test_long_title_is_truncated(self):
        """Titles exceeding 60 chars are truncated in table."""
        long_title = "A" * 80
        issue = _issue(title=long_title)
        table = fetch_issues.build_issue_table([issue])
        assert "..." in table
        assert long_title not in table

    def test_open_status_emoji(self):
        """Each row contains the open status indicator."""
        table = fetch_issues.build_issue_table([_issue()])
        assert "\U0001f7e2 Open" in table


# ── save_state ──────────────────────────────────────────────────


class TestSaveState:
    """Tests for save_state with tmp_path isolation."""

    @pytest.fixture(autouse=True)
    def _patch_state_path(self, tmp_path, monkeypatch):
        """Redirect STATE_PATH to a temp directory.

        Args:
            tmp_path: Pytest tmp_path fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        self.state_file = tmp_path / ".arena_state" / "issues.json"
        monkeypatch.setattr(fetch_issues, "STATE_PATH", self.state_file)

    def test_creates_directory_and_file(self):
        """State dir and file are created from scratch."""
        issues = {"gfi": [_issue()]}
        fetch_issues.save_state(issues, "2026-W12")
        assert self.state_file.exists()
        data = json.loads(self.state_file.read_text())
        assert "2026-W12" in data

    def test_merges_with_existing_state(self):
        """New week is added alongside existing weeks."""
        self.state_file.parent.mkdir(parents=True)
        existing = {
            "2026-W11": {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "issues": {"gfi": []},
            },
        }
        self.state_file.write_text(json.dumps(existing))

        fetch_issues.save_state({"gfi": [_issue()]}, "2026-W12")
        data = json.loads(self.state_file.read_text())
        assert "2026-W11" in data
        assert "2026-W12" in data

    def test_prunes_old_entries(self):
        """Entries older than 28 weeks are removed."""
        self.state_file.parent.mkdir(parents=True)
        old_dt = datetime.now(timezone.utc) - timedelta(weeks=30)
        existing = {
            "2025-W01": {
                "fetched_at": old_dt.isoformat(),
                "issues": {"gfi": []},
            },
        }
        self.state_file.write_text(json.dumps(existing))

        fetch_issues.save_state({"gfi": []}, "2026-W12")
        data = json.loads(self.state_file.read_text())
        assert "2025-W01" not in data
        assert "2026-W12" in data

    def test_recent_entries_not_pruned(self):
        """Entries within 28 weeks are kept."""
        self.state_file.parent.mkdir(parents=True)
        recent_dt = datetime.now(timezone.utc) - timedelta(weeks=5)
        existing = {
            "2026-W07": {
                "fetched_at": recent_dt.isoformat(),
                "issues": {"bug": []},
            },
        }
        self.state_file.write_text(json.dumps(existing))

        fetch_issues.save_state({"gfi": []}, "2026-W12")
        data = json.loads(self.state_file.read_text())
        assert "2026-W07" in data

    def test_overwrites_same_week(self):
        """Re-saving the same week replaces its data."""
        fetch_issues.save_state({"gfi": [_issue(number=1)]}, "2026-W12")
        fetch_issues.save_state({"gfi": [_issue(number=99)]}, "2026-W12")
        data = json.loads(self.state_file.read_text())
        issues = data["2026-W12"]["issues"]["gfi"]
        assert len(issues) == 1
        assert issues[0]["number"] == 99

    def test_state_is_valid_json(self):
        """Output file is well-formed JSON."""
        fetch_issues.save_state({"gfi": []}, "2026-W12")
        text = self.state_file.read_text()
        parsed = json.loads(text)
        assert isinstance(parsed, dict)


# ── save_current_issues ─────────────────────────────────────────


class TestSaveCurrentIssues:
    """Tests for save_current_issues with tmp_path."""

    @pytest.fixture(autouse=True)
    def _patch_current_path(self, tmp_path, monkeypatch):
        """Redirect the current-issues output path.

        Args:
            tmp_path: Pytest tmp_path fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        self.out_dir = tmp_path / ".arena_state"
        self.out_dir.mkdir()
        self.out_file = self.out_dir / "current_issues.json"
        # Monkeypatch Path in save_current_issues by
        # replacing the function's internal Path call.
        original_path = Path

        def _patched_path(p):
            if p == ".arena_state/current_issues.json":
                return self.out_file
            return original_path(p)

        monkeypatch.setattr(fetch_issues, "Path", _patched_path)

    def test_writes_file(self):
        """Output file is created."""
        issues = {"gfi": [_issue("a", "r1", 10)]}
        fetch_issues.save_current_issues(issues)
        assert self.out_file.exists()

    def test_contains_expected_keys(self):
        """Each entry has owner, repo, number, category."""
        issues = {"gfi": [_issue("a", "r1", 10)]}
        fetch_issues.save_current_issues(issues)
        data = json.loads(self.out_file.read_text())
        assert len(data) == 1
        entry = data[0]
        assert entry["owner"] == "a"
        assert entry["repo"] == "r1"
        assert entry["number"] == 10
        assert entry["category"] == "gfi"

    def test_multiple_categories(self):
        """Issues from different categories are combined."""
        issues = {
            "gfi": [_issue("a", "r1", 1)],
            "bug": [
                _issue("b", "r2", 2),
                _issue("c", "r3", 3),
            ],
            "hard": [],
        }
        fetch_issues.save_current_issues(issues)
        data = json.loads(self.out_file.read_text())
        assert len(data) == 3
        categories = {e["category"] for e in data}
        assert categories == {"gfi", "bug"}

    def test_empty_issues(self):
        """Empty categories produce an empty JSON array."""
        issues = {"gfi": [], "bug": [], "hard": []}
        fetch_issues.save_current_issues(issues)
        data = json.loads(self.out_file.read_text())
        assert data == []

    def test_output_is_valid_json(self):
        """Output is well-formed JSON."""
        issues = {"gfi": [_issue()]}
        fetch_issues.save_current_issues(issues)
        text = self.out_file.read_text()
        parsed = json.loads(text)
        assert isinstance(parsed, list)

    def test_no_extra_fields(self):
        """Only the four required fields are written."""
        issues = {"hard": [_issue("x", "y", 7)]}
        fetch_issues.save_current_issues(issues)
        data = json.loads(self.out_file.read_text())
        assert set(data[0].keys()) == {
            "owner",
            "repo",
            "number",
            "category",
        }


# ── load_configured_repos ────────────────────────────────────


class TestLoadConfiguredRepos:
    """Tests for the load_configured_repos function.

    The function loads label_mappings from ``config/repos.yml``, derives
    per-category limits from the current arena level
    (``config/arena_levels.json`` + ``.arena_state/milestones.json``), and
    pulls the repo list from a three-level fallback chain:
    pool → anchor → empty.
    """

    @staticmethod
    def _write_main_config(tmp_path) -> Path:
        """Write a minimal config/repos.yml with label_mappings only."""
        import yaml

        config = {
            "label_mappings": {
                "gfi": ["good first issue"],
                "bug": ["bug"],
                "hard": ["hard"],
            },
        }
        f = tmp_path / "repos.yml"
        f.write_text(yaml.dump(config), encoding="utf-8")
        return f

    @staticmethod
    def _stub_levels(monkeypatch, level=0, baseline=None):
        """Stub the level helpers fetch_issues imports.

        Returns limits = baseline + level (per category) so tests can
        assert arena-level-aware behavior without touching real state.
        """
        if baseline is None:
            baseline = {"gfi": 5, "bug": 3, "hard": 2}
        config = {
            "version": 1,
            "baseline": baseline,
            "levels": [
                {
                    "level": lv,
                    "threshold": lv * 10,
                    "bonus": {"gfi": lv, "bug": lv, "hard": lv},
                }
                for lv in range(level + 1)
            ],
        }
        monkeypatch.setattr("fetch_issues.load_levels_config", lambda: config)
        monkeypatch.setattr(
            "fetch_issues.current_level_from_state", lambda: level
        )
        return config

    def test_loads_repos_from_pool(self, tmp_path, monkeypatch):
        """When the pool file exists, repos come from it."""
        cfg = self._write_main_config(tmp_path)
        self._stub_levels(monkeypatch, level=0)
        pool = tmp_path / "repo_pool.json"
        pool.write_text(
            json.dumps(
                {
                    "anchor_count": 1,
                    "dynamic_count": 1,
                    "total_count": 2,
                    "repos": [
                        {
                            "owner": "torchgeo",
                            "repo": "torchgeo",
                            "source": "anchor",
                        },
                        {
                            "owner": "vllm-project",
                            "repo": "vllm",
                            "source": "dynamic",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("fetch_issues.CONFIG_PATH", cfg)
        monkeypatch.setattr("fetch_issues.POOL_PATH", pool)
        monkeypatch.setattr(
            "fetch_issues.ANCHOR_PATH", tmp_path / "missing.yml"
        )
        result = fetch_issues.load_configured_repos()
        assert len(result["repos"]) == 2
        assert result["repos"][0]["owner"] == "torchgeo"
        # Level-0 baseline limits are injected from arena_levels config.
        assert result["limits"]["gfi"] == 5
        assert result["arena_level"] == 0

    def test_falls_back_to_anchor_when_no_pool(
        self, tmp_path, monkeypatch, caplog
    ):
        """No pool but anchor exists → loads anchor and warns."""
        import logging

        cfg = self._write_main_config(tmp_path)
        anchor = tmp_path / "anchor_repos.yml"
        anchor.write_text(
            "repos:\n"
            "  - owner: torchgeo\n"
            "    repo: torchgeo\n"
            "  - owner: pytorch\n"
            "    repo: pytorch\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("fetch_issues.CONFIG_PATH", cfg)
        monkeypatch.setattr(
            "fetch_issues.POOL_PATH", tmp_path / "missing.json"
        )
        monkeypatch.setattr("fetch_issues.ANCHOR_PATH", anchor)
        with caplog.at_level(logging.WARNING):
            result = fetch_issues.load_configured_repos()
        assert len(result["repos"]) == 2
        assert result["repos"][0]["owner"] == "torchgeo"
        assert any("anchor" in r.message.lower() for r in caplog.records)

    def test_no_source_returns_empty_list(self, tmp_path, monkeypatch, caplog):
        """Neither pool nor anchor → empty repos list, error logged."""
        import logging

        cfg = self._write_main_config(tmp_path)
        monkeypatch.setattr("fetch_issues.CONFIG_PATH", cfg)
        monkeypatch.setattr(
            "fetch_issues.POOL_PATH", tmp_path / "missing.json"
        )
        monkeypatch.setattr(
            "fetch_issues.ANCHOR_PATH", tmp_path / "missing.yml"
        )
        with caplog.at_level(logging.ERROR):
            result = fetch_issues.load_configured_repos()
        assert result["repos"] == []
        assert any(
            "no repo source" in r.message.lower() for r in caplog.records
        )

    def test_label_mappings_from_config_limits_from_arena_level(
        self, tmp_path, monkeypatch
    ):
        """label_mappings come from config/repos.yml.

        Per-category limits come from the arena level helpers and
        scale with the current arena level.
        """
        cfg = self._write_main_config(tmp_path)
        # Level 2 → baseline + 2 per category = (7, 5, 4) given the
        # baseline used by _stub_levels.
        self._stub_levels(monkeypatch, level=2)
        pool = tmp_path / "repo_pool.json"
        pool.write_text(
            json.dumps(
                {
                    "anchor_count": 0,
                    "dynamic_count": 1,
                    "total_count": 1,
                    "repos": [
                        {
                            "owner": "a",
                            "repo": "b",
                            "source": "dynamic",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("fetch_issues.CONFIG_PATH", cfg)
        monkeypatch.setattr("fetch_issues.POOL_PATH", pool)
        monkeypatch.setattr(
            "fetch_issues.ANCHOR_PATH", tmp_path / "missing.yml"
        )
        result = fetch_issues.load_configured_repos()
        assert result["label_mappings"]["gfi"] == ["good first issue"]
        assert result["arena_level"] == 2
        assert result["limits"] == {"gfi": 7, "bug": 5, "hard": 4}

    def test_arena_level_test_extra_levels_unlock_more_issues(
        self, tmp_path, monkeypatch
    ):
        """Higher arena level → strictly more issues per category."""
        cfg = self._write_main_config(tmp_path)
        pool = tmp_path / "repo_pool.json"
        pool.write_text(
            json.dumps(
                {
                    "anchor_count": 0,
                    "dynamic_count": 0,
                    "total_count": 0,
                    "repos": [],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("fetch_issues.CONFIG_PATH", cfg)
        monkeypatch.setattr("fetch_issues.POOL_PATH", pool)
        monkeypatch.setattr(
            "fetch_issues.ANCHOR_PATH", tmp_path / "missing.yml"
        )

        self._stub_levels(monkeypatch, level=0)
        baseline_result = fetch_issues.load_configured_repos()

        self._stub_levels(monkeypatch, level=3)
        leveled_result = fetch_issues.load_configured_repos()

        for cat in ("gfi", "bug", "hard"):
            assert (
                leveled_result["limits"][cat] > baseline_result["limits"][cat]
            )

    def test_missing_main_config_raises(self, tmp_path, monkeypatch):
        """Missing config/repos.yml raises FileNotFoundError."""
        monkeypatch.setattr(
            "fetch_issues.CONFIG_PATH", tmp_path / "missing.yml"
        )
        with pytest.raises(FileNotFoundError):
            fetch_issues.load_configured_repos()


# ── select_category_issues ───────────────────────────────────


def _mk_issue(owner="acme", repo="proj", number=1):
    """Build a minimal issue dict."""
    return {
        "number": number,
        "title": f"Issue #{number}",
        "url": f"https://github.com/{owner}/{repo}/issues/{number}",
        "owner": owner,
        "repo": repo,
    }


class TestSelectCategoryIssues:
    """Tests for the pure-random category selector."""

    def test_seeded_rng_is_reproducible(self):
        """Same rng seed → same selection output."""
        issues = [
            _mk_issue(owner="o", repo=f"r{i}", number=i) for i in range(20)
        ]
        import random

        with patch("fetch_issues.has_linked_pr", return_value=False):
            out_a = fetch_issues.select_category_issues(
                issues, limit=5, rng=random.Random(123)
            )
            out_b = fetch_issues.select_category_issues(
                issues, limit=5, rng=random.Random(123)
            )
        assert [i["number"] for i in out_a] == [i["number"] for i in out_b]

    def test_different_seed_differs(self):
        """Different rng seeds produce different orderings."""
        issues = [
            _mk_issue(owner="o", repo=f"r{i}", number=i) for i in range(20)
        ]
        import random

        with patch("fetch_issues.has_linked_pr", return_value=False):
            out_a = fetch_issues.select_category_issues(
                issues, limit=5, rng=random.Random(1)
            )
            out_b = fetch_issues.select_category_issues(
                issues, limit=5, rng=random.Random(9999)
            )
        assert [i["number"] for i in out_a] != [i["number"] for i in out_b]

    def test_diversity_cap_enforced(self):
        """At most 2 issues from any single repo."""
        issues = [
            _mk_issue(owner="acme", repo="proj", number=i) for i in range(10)
        ]
        import random

        rng = random.Random(42)
        with patch("fetch_issues.has_linked_pr", return_value=False):
            out = fetch_issues.select_category_issues(
                issues, limit=10, rng=rng
            )
        assert len(out) == 2

    def test_skips_linked_pr(self):
        """Issues with a linked PR are filtered out."""
        issues = [
            _mk_issue(owner="a", repo="r1", number=1),
            _mk_issue(owner="a", repo="r2", number=2),
            _mk_issue(owner="a", repo="r3", number=3),
        ]
        import random

        def fake_linked(owner, repo, number):
            return number == 2

        rng = random.Random(42)
        with patch("fetch_issues.has_linked_pr", side_effect=fake_linked):
            out = fetch_issues.select_category_issues(issues, limit=5, rng=rng)
        assert 2 not in [i["number"] for i in out]
        assert len(out) == 2

    def test_limit_caps_output(self):
        """Never returns more than `limit` issues."""
        issues = [
            _mk_issue(owner="o", repo=f"r{i}", number=i) for i in range(50)
        ]
        import random

        rng = random.Random(42)
        with patch("fetch_issues.has_linked_pr", return_value=False):
            out = fetch_issues.select_category_issues(
                issues, limit=10, rng=rng
            )
        assert len(out) == 10

    def test_empty_input(self):
        """Empty input → empty output."""
        import random

        with patch("fetch_issues.has_linked_pr", return_value=False):
            out = fetch_issues.select_category_issues(
                [], limit=10, rng=random.Random(0)
            )
        assert out == []


# ── fetch_all_issues integration ─────────────────────────────


class TestFetchAllIssuesIntegration:
    """fetch_all_issues returns issues matching per-category limits."""

    @staticmethod
    def _config() -> dict:
        return {
            "repos": [
                {"owner": "a", "repo": "r1"},
                {"owner": "t", "repo": "r1"},
                {"owner": "d", "repo": "r1"},
            ],
            "label_mappings": {
                "gfi": ["good first issue"],
                "bug": ["bug"],
                "hard": ["hard"],
            },
            "limits": {"gfi": 3, "bug": 3, "hard": 3},
        }

    def test_returns_issues_without_repo_bucket(self, monkeypatch):
        """Fetched issues carry the expected keys and no repo_bucket tag."""

        def fake_get(owner, repo, labels, limit):
            if labels != ["hard"]:
                return []
            return [
                {
                    "number": ord(owner[0]),
                    "title": f"{owner}-hard",
                    "html_url": (
                        f"https://github.com/{owner}/{repo}/issues/"
                        f"{ord(owner[0])}"
                    ),
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-02T00:00:00Z",
                    "user": {"login": "u"},
                }
            ]

        monkeypatch.setattr(fetch_issues, "get_issues_for_repo", fake_get)
        with patch("fetch_issues.has_linked_pr", return_value=False):
            result = fetch_issues.fetch_all_issues(self._config())
        hard = result["hard"]
        assert len(hard) == 3
        for issue in hard:
            assert "repo_bucket" not in issue
            assert {"owner", "repo", "number", "title", "url"} <= issue.keys()
