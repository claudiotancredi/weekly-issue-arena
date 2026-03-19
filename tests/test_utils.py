"""Tests for scripts/utils.py."""

import logging
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import responses

sys.path.insert(0, "scripts")

from utils import (  # noqa: E402, I001
    arena_week_id,
    github_get,
    update_readme_section,
)


# ── arena_week_id ─────────────────────────────────────────────


class TestArenaWeekId:
    """Tests for the arena_week_id helper."""

    def test_friday_after_cutoff(self):
        """Friday 18:00 UTC belongs to that Friday's week."""
        # Friday 2026-03-20 18:00 UTC
        dt = datetime(2026, 3, 20, 18, 0, tzinfo=timezone.utc)
        assert dt.weekday() == 4  # sanity: it is Friday
        result = arena_week_id(dt)
        assert result == "2026-W12"

    def test_friday_before_cutoff(self):
        """Friday 16:00 UTC falls in the previous Friday's week."""
        # Friday 2026-03-20 16:00 UTC
        dt = datetime(2026, 3, 20, 16, 0, tzinfo=timezone.utc)
        result = arena_week_id(dt)
        # Previous Friday is 2026-03-13 -> ISO week 11
        assert result == "2026-W11"

    def test_saturday(self):
        """Saturday maps to the current (preceding) Friday."""
        # Saturday 2026-03-21 10:00 UTC
        dt = datetime(2026, 3, 21, 10, 0, tzinfo=timezone.utc)
        assert dt.weekday() == 5  # sanity: Saturday
        result = arena_week_id(dt)
        # Preceding Friday is 2026-03-20 -> W12
        assert result == "2026-W12"

    def test_thursday(self):
        """Thursday maps to the previous Friday's week."""
        # Thursday 2026-03-19 12:00 UTC
        dt = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)
        assert dt.weekday() == 3  # sanity: Thursday
        result = arena_week_id(dt)
        # Previous Friday is 2026-03-13 -> W11
        assert result == "2026-W11"

    def test_default_uses_current_time(self):
        """Calling with no arg delegates to datetime.now."""
        fake_now = datetime(2026, 3, 20, 18, 0, tzinfo=timezone.utc)
        with patch(
            "utils.datetime",
            wraps=datetime,
        ) as mock_dt:
            mock_dt.now.return_value = fake_now
            result = arena_week_id()
        assert result == "2026-W12"

    def test_return_format(self):
        """Result matches the ``YYYY-Www`` ISO pattern."""
        dt = datetime(2026, 1, 9, 18, 0, tzinfo=timezone.utc)
        result = arena_week_id(dt)
        # Should look like "2026-W02"
        assert len(result) == 8
        assert result[:5] == "2026-"
        assert result[5] == "W"
        assert result[6:].isdigit()


# ── update_readme_section ─────────────────────────────────────


class TestUpdateReadmeSection:
    """Tests for the update_readme_section helper."""

    def test_replaces_content_between_markers(self):
        """Body between START/END markers is replaced."""
        content = (
            "# Title\n"
            "<!-- SCORES:START -->\n"
            "old scores\n"
            "<!-- SCORES:END -->\n"
            "footer"
        )
        result = update_readme_section(content, "SCORES", "new scores\n")
        assert "old scores" not in result
        assert "new scores" in result
        assert "<!-- SCORES:START -->" in result
        assert "<!-- SCORES:END -->" in result
        assert result.startswith("# Title\n")
        assert result.endswith("footer")

    def test_missing_marker_returns_unchanged(self, caplog):
        """Missing markers leave content intact and log a warning."""
        content = "# Title\nNo markers here.\n"
        with caplog.at_level(logging.WARNING):
            result = update_readme_section(content, "NOPE", "body\n")
        assert result == content
        assert "Marker NOPE not found" in caplog.text

    def test_multiple_markers_only_matching(self):
        """Only the targeted marker pair is replaced."""
        content = (
            "<!-- A:START -->aaa<!-- A:END -->\n"
            "<!-- B:START -->bbb<!-- B:END -->\n"
        )
        result = update_readme_section(content, "A", "new-a")
        assert "new-a" in result
        assert "bbb" in result
        assert "aaa" not in result


# ── github_get ────────────────────────────────────────────────


API_URL = "https://api.github.com/repos/test/test"


class TestGithubGet:
    """Tests for the github_get wrapper."""

    @responses.activate
    def test_normal_request(self):
        """A normal 200 response is returned as-is."""
        responses.add(
            responses.GET,
            API_URL,
            json={"id": 1},
            status=200,
        )
        resp = github_get(API_URL)
        assert resp.status_code == 200
        assert resp.json() == {"id": 1}

    @responses.activate
    def test_low_rate_limit_logs_warning(self, caplog):
        """A warning is logged when remaining < 100."""
        responses.add(
            responses.GET,
            API_URL,
            json={},
            status=200,
            headers={
                "X-RateLimit-Remaining": "50",
                "X-RateLimit-Reset": "1700000000",
            },
        )
        with caplog.at_level(logging.WARNING):
            resp = github_get(API_URL)
        assert resp.status_code == 200
        assert "rate limit low" in caplog.text.lower()

    @responses.activate
    def test_rate_limit_exhausted_raises(self):
        """403 with 0 remaining raises SystemExit."""
        responses.add(
            responses.GET,
            API_URL,
            json={"message": "rate limit"},
            status=403,
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "1700000000",
            },
        )
        with pytest.raises(SystemExit):
            github_get(API_URL)

    def test_default_timeout(self):
        """Default timeout of 15 s is applied."""
        mock_resp = MagicMock()
        mock_resp.headers = {}
        mock_resp.status_code = 200
        with patch(
            "utils.requests.get",
            return_value=mock_resp,
        ) as mock_get:
            github_get(API_URL)
        _, kwargs = mock_get.call_args
        assert kwargs["timeout"] == 15
