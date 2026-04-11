"""Tests for the arena_level helper module."""

import json
import sys

import pytest

sys.path.insert(0, "scripts")

import arena_level  # noqa: E402, I001


def _bonus(n: int) -> dict:
    return {"gfi": n, "bug": n, "hard": n}


@pytest.fixture
def sample_config():
    """Synthetic levels config covering edge cases."""
    return {
        "version": 1,
        "baseline": {"gfi": 20, "bug": 14, "hard": 10},
        "levels": [
            {"level": 0, "threshold": 0, "bonus": _bonus(0)},
            {"level": 1, "threshold": 25, "bonus": _bonus(1)},
            {"level": 2, "threshold": 75, "bonus": _bonus(2)},
            {"level": 3, "threshold": 150, "bonus": _bonus(3)},
            {"level": 10, "threshold": 3000, "bonus": _bonus(10)},
        ],
    }


class TestComputeArenaPoints:
    """Tests for compute_arena_points."""

    def test_empty_scores(self):
        """Empty scores dict yields zero arena points."""
        assert arena_level.compute_arena_points({}) == 0
        assert arena_level.compute_arena_points({"players": {}}) == 0

    def test_sum_across_players(self):
        """Arena points sum every player's total_points."""
        scores = {
            "players": {
                "alice": {"total_points": 12},
                "bob": {"total_points": 30},
                "carol": {"total_points": 0},
            }
        }
        assert arena_level.compute_arena_points(scores) == 42

    def test_missing_total_points(self):
        """Missing total_points field is treated as zero."""
        scores = {"players": {"alice": {}}}
        assert arena_level.compute_arena_points(scores) == 0


class TestComputeArenaLevel:
    """Tests for compute_arena_level threshold logic."""

    def test_zero_points_is_level_zero(self, sample_config):
        """Zero arena points always maps to level zero."""
        assert arena_level.compute_arena_level(0, sample_config) == 0

    def test_just_below_threshold(self, sample_config):
        """Points one below a threshold stay on the lower level."""
        assert arena_level.compute_arena_level(24, sample_config) == 0
        assert arena_level.compute_arena_level(74, sample_config) == 1
        assert arena_level.compute_arena_level(149, sample_config) == 2

    def test_exactly_at_threshold(self, sample_config):
        """Reaching the threshold value promotes to that level."""
        assert arena_level.compute_arena_level(25, sample_config) == 1
        assert arena_level.compute_arena_level(75, sample_config) == 2
        assert arena_level.compute_arena_level(150, sample_config) == 3

    def test_above_max_clamps(self, sample_config):
        """Points beyond the highest threshold clamp to max level."""
        assert arena_level.compute_arena_level(3000, sample_config) == 10
        assert arena_level.compute_arena_level(99999, sample_config) == 10


class TestEffectiveLimits:
    """Tests for effective_limits bonus application."""

    def test_level_zero_returns_baseline(self, sample_config):
        """Level zero returns the baseline limits unchanged."""
        limits = arena_level.effective_limits(0, sample_config)
        assert limits == {"gfi": 20, "bug": 14, "hard": 10}

    def test_level_three_adds_bonus(self, sample_config):
        """Level bonuses are added on top of baseline per category."""
        limits = arena_level.effective_limits(3, sample_config)
        assert limits == {"gfi": 23, "bug": 17, "hard": 13}

    def test_max_level(self, sample_config):
        """Max level reaches the documented 74-issue cap."""
        limits = arena_level.effective_limits(10, sample_config)
        assert limits == {"gfi": 30, "bug": 24, "hard": 20}
        assert sum(limits.values()) == 74

    def test_unknown_level_clamps_to_max(self, sample_config):
        """Unknown level numbers fall back to the highest known level."""
        limits = arena_level.effective_limits(99, sample_config)
        assert limits == {"gfi": 30, "bug": 24, "hard": 20}


class TestTotalIssuesAtLevel:
    """Tests for total_issues_at_level."""

    def test_baseline_total(self, sample_config):
        """Baseline total matches the documented 44 issues."""
        assert arena_level.total_issues_at_level(0, sample_config) == 44

    def test_level_one_total(self, sample_config):
        """Level one adds three issues over baseline."""
        assert arena_level.total_issues_at_level(1, sample_config) == 47


class TestGetNextLevelEntry:
    """Tests for get_next_level_entry."""

    def test_returns_next_level(self, sample_config):
        """Returns the entry one level above the supplied level."""
        nxt = arena_level.get_next_level_entry(0, sample_config)
        assert nxt is not None
        assert nxt["level"] == 1
        assert nxt["threshold"] == 25

    def test_max_level_returns_none(self, sample_config):
        """At the max level there is no next entry."""
        assert arena_level.get_next_level_entry(10, sample_config) is None


class TestLoadLevelsConfig:
    """Tests for load_levels_config including fallback paths."""

    def test_loads_real_config(self):
        """Real config on disk is loaded and matches the documented schema."""
        config = arena_level.load_levels_config()
        assert "baseline" in config
        assert "levels" in config
        assert config["baseline"]["gfi"] == 20
        # Real config should reach level 10 with total 74 issues.
        assert arena_level.total_issues_at_level(10, config) == 74

    def test_missing_file_falls_back(self, tmp_path):
        """Missing config file falls back to the embedded defaults."""
        config = arena_level.load_levels_config(tmp_path / "missing.json")
        assert config["baseline"] == {"gfi": 20, "bug": 14, "hard": 10}
        assert arena_level.compute_arena_level(0, config) == 0

    def test_malformed_file_falls_back(self, tmp_path):
        """Malformed JSON falls back to defaults instead of raising."""
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json")
        config = arena_level.load_levels_config(bad)
        assert config["baseline"]["gfi"] == 20

    def test_missing_keys_falls_back(self, tmp_path):
        """Schemas missing required keys fall back to defaults."""
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"version": 1}))
        config = arena_level.load_levels_config(bad)
        assert "baseline" in config


class TestLoadAndSaveMilestones:
    """Tests for milestones load/save round-trip."""

    def test_load_missing_returns_default(self, tmp_path):
        """A missing milestones file returns a clean default state."""
        path = tmp_path / "milestones.json"
        ms = arena_level.load_milestones(path)
        assert ms["current_level"] == 0
        assert ms["current_arena_points"] == 0
        assert ms["history"] == []

    def test_round_trip(self, tmp_path):
        """Saved milestones load back as the exact same dict."""
        path = tmp_path / "milestones.json"
        original = {
            "current_level": 2,
            "current_arena_points": 80,
            "discussion_node_id": "D_xyz",
            "history": [
                {"level": 1, "reached_at": "2026-04-01T00:00:00+00:00"},
            ],
        }
        arena_level.save_milestones(original, path)
        loaded = arena_level.load_milestones(path)
        assert loaded == original
