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

    @patch("fetch_issues.get_issues_for_repo")
    def test_multi_label_issue_lands_in_higher_category(self, mock_fetch):
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

    @patch("fetch_issues.get_issues_for_repo")
    def test_multi_label_bug_and_gfi(self, mock_fetch):
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

    @patch("fetch_issues.get_issues_for_repo")
    def test_distinct_issues_not_deduped(self, mock_fetch):
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

    @patch("fetch_issues.get_issues_for_repo")
    def test_dedup_across_repos(self, mock_fetch):
        """Same issue number in different repos is NOT deduped."""

        def side_effect(owner, repo, labels, limit):
            if labels == ["bug"]:
                return [
                    _gh_issue(
                        100 if repo == "a" else 200, 1, owner=owner, repo=repo
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

    @patch("fetch_issues.get_issues_for_repo")
    def test_category_iteration_order(self, mock_fetch):
        """Categories are checked hard -> bug -> gfi."""
        call_labels = []

        def side_effect(owner, repo, labels, limit):
            call_labels.append(labels[0])
            return []

        mock_fetch.side_effect = side_effect

        config = self._config([{"owner": "org", "repo": "proj"}])
        fetch_issues.fetch_all_issues(config)

        assert call_labels == ["hard", "bug", "good first issue"]


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
