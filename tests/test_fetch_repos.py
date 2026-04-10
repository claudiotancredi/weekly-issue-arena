"""Tests for scripts/fetch_repos.py."""

import json
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, "scripts")

import fetch_repos  # noqa: E402, I001


# ── helpers ──────────────────────────────────────────────────


def _api_item(
    owner="acme",
    name="proj",
    stars=1000,
    topics=None,
    has_issues=True,
    open_issues_count=100,
):
    """Build a minimal GitHub Search API item."""
    return {
        "name": name,
        "owner": {"login": owner},
        "stargazers_count": stars,
        "topics": topics or [],
        "has_issues": has_issues,
        "open_issues_count": open_issues_count,
    }


def _norm_repo(
    owner="acme",
    name="proj",
    stars=1000,
    topics=None,
    has_issues=True,
    open_issues_count=100,
):
    """Build a normalized repo dict (post-search_repos shape)."""
    return {
        "owner": owner,
        "repo": name,
        "name": name,
        "stars": stars,
        "topics": topics or [],
        "has_issues": has_issues,
        "open_issues_count": open_issues_count,
    }


def _mock_search_response(items):
    """Build a fake requests.Response for the Search API."""
    mock = MagicMock()
    mock.json.return_value = {"items": items}
    mock.raise_for_status.return_value = None
    return mock


# ── search_repos ─────────────────────────────────────────────


def test_search_repos_parses_response():
    """A single API item is normalized to the expected dict shape."""
    items = [_api_item(owner="pytorch", name="pytorch", stars=99000)]
    with patch(
        "fetch_repos.github_get",
        return_value=_mock_search_response(items),
    ):
        result = fetch_repos.search_repos("query", "stars", max_pages=1)
    assert len(result) == 1
    assert result[0]["owner"] == "pytorch"
    assert result[0]["repo"] == "pytorch"
    assert result[0]["stars"] == 99000


def test_search_repos_pagination():
    """Two full pages are merged into a single result list."""
    page1 = [_api_item(name=f"r{i}") for i in range(100)]
    page2 = [_api_item(name=f"r{i}") for i in range(100, 150)]
    responses = [
        _mock_search_response(page1),
        _mock_search_response(page2),
    ]
    with patch("fetch_repos.github_get", side_effect=responses) as mock_get:
        result = fetch_repos.search_repos("q", "stars", max_pages=2)
    assert mock_get.call_count == 2
    assert len(result) == 150


def test_search_repos_handles_empty():
    """An empty items list yields an empty result."""
    with patch(
        "fetch_repos.github_get",
        return_value=_mock_search_response([]),
    ):
        result = fetch_repos.search_repos("q", "stars", max_pages=2)
    assert result == []


def test_search_repos_stops_early_on_short_page():
    """If a page returns less than per_page items, no further pages."""
    page1 = [_api_item(name=f"r{i}") for i in range(50)]  # < 100
    with patch(
        "fetch_repos.github_get",
        side_effect=[_mock_search_response(page1)],
    ) as mock_get:
        result = fetch_repos.search_repos("q", "stars", max_pages=2)
    assert mock_get.call_count == 1  # only 1 call, not 2
    assert len(result) == 50


# ── is_noise ─────────────────────────────────────────────────


def test_is_noise_topic_awesome():
    """A repo with topic 'awesome' is noise."""
    assert fetch_repos.is_noise(_norm_repo(topics=["awesome", "ml"]))


def test_is_noise_topic_skills():
    """A repo with topic 'agent-skills' is noise."""
    assert fetch_repos.is_noise(_norm_repo(topics=["agent-skills", "ai"]))


def test_is_noise_topic_tutorial():
    """A repo with topic 'tutorial' is noise."""
    assert fetch_repos.is_noise(_norm_repo(topics=["tutorial"]))


def test_is_noise_name_awesome_prefix():
    """A repo named 'awesome-*' is noise."""
    assert fetch_repos.is_noise(_norm_repo(name="awesome-llm"))


def test_is_noise_name_oh_my():
    """A repo named 'oh-my-*' is noise."""
    assert fetch_repos.is_noise(_norm_repo(name="oh-my-zsh"))


def test_is_noise_name_skill_suffix():
    """A repo with a '-skill' suffix is noise."""
    assert fetch_repos.is_noise(_norm_repo(name="ui-pro-skill"))


def test_is_noise_name_skills_suffix():
    """A repo with a '-skills' suffix is noise."""
    assert fetch_repos.is_noise(_norm_repo(name="some-tool-skills"))


def test_is_noise_low_issue_count():
    """A repo with too few open issues is noise."""
    assert fetch_repos.is_noise(_norm_repo(open_issues_count=5))


def test_is_noise_no_issues_disabled():
    """A repo with the issues tab disabled is noise."""
    assert fetch_repos.is_noise(_norm_repo(has_issues=False))


def test_is_noise_clean_pytorch():
    """pytorch/pytorch is real software and not noise."""
    repo = _norm_repo(
        owner="pytorch",
        name="pytorch",
        stars=99000,
        topics=["machine-learning", "deep-learning"],
        has_issues=True,
        open_issues_count=15000,
    )
    assert not fetch_repos.is_noise(repo)


def test_is_noise_clean_openclaw():
    """Real software with bug labels but no GFI labels — must pass."""
    repo = _norm_repo(
        owner="openclaw",
        name="openclaw",
        stars=353000,
        topics=["ai", "assistant"],
        has_issues=True,
        open_issues_count=17981,
    )
    assert not fetch_repos.is_noise(repo)


# ── fetch_dynamic_candidates ─────────────────────────────────


def test_fetch_dynamic_candidates_dedupes_and_counts_matches():
    """A repo appearing in 2 queries → query_matches == 2."""
    queries = [
        {"q": "q1", "sort": "stars"},
        {"q": "q2", "sort": "updated"},
    ]
    with patch(
        "fetch_repos.search_repos",
        side_effect=[
            [_norm_repo(owner="vllm-project", name="vllm")],
            [_norm_repo(owner="vllm-project", name="vllm")],
        ],
    ):
        candidates = fetch_repos.fetch_dynamic_candidates(queries)
    assert len(candidates) == 1
    assert candidates["vllm-project/vllm"]["query_matches"] == 2


def test_fetch_dynamic_candidates_filters_noise():
    """Awesome-list dropped before being added to candidates."""
    queries = [{"q": "q1", "sort": "stars"}]
    with patch(
        "fetch_repos.search_repos",
        return_value=[
            _norm_repo(owner="someone", name="awesome-llm"),
            _norm_repo(owner="pytorch", name="pytorch"),
        ],
    ):
        candidates = fetch_repos.fetch_dynamic_candidates(queries)
    assert "pytorch/pytorch" in candidates
    assert "someone/awesome-llm" not in candidates


# ── rank_dynamic ─────────────────────────────────────────────


def test_rank_dynamic_excludes_anchor():
    """Repos in the anchor key set are removed from the dynamic ranking."""
    candidates = {
        "pytorch/pytorch": {
            "owner": "pytorch",
            "repo": "pytorch",
            "stars": 99000,
            "query_matches": 3,
        },
        "vllm-project/vllm": {
            "owner": "vllm-project",
            "repo": "vllm",
            "stars": 75000,
            "query_matches": 2,
        },
    }
    ranked = fetch_repos.rank_dynamic(candidates, {"pytorch/pytorch"})
    assert len(ranked) == 1
    assert ranked[0]["repo"] == "vllm"


def test_rank_dynamic_orders_by_matches_then_stars():
    """Sort key is (query_matches desc, stars desc)."""
    candidates = {
        "a/low": {
            "owner": "a",
            "repo": "low",
            "stars": 50000,
            "query_matches": 1,
        },
        "b/high": {
            "owner": "b",
            "repo": "high",
            "stars": 1000,
            "query_matches": 3,
        },
        "c/mid": {
            "owner": "c",
            "repo": "mid",
            "stars": 90000,
            "query_matches": 1,
        },
    }
    ranked = fetch_repos.rank_dynamic(candidates, set())
    # higher query_matches first, then higher stars within same matches
    assert ranked[0]["repo"] == "high"
    assert ranked[1]["repo"] == "mid"
    assert ranked[2]["repo"] == "low"


# ── build_pool ───────────────────────────────────────────────


def test_build_pool_hits_target_250():
    """50 anchor + 200 dynamic produces a 250-repo pool."""
    anchor = [{"owner": f"a{i}", "repo": "r"} for i in range(50)]
    dynamic = [
        {
            "owner": f"d{i}",
            "repo": "r",
            "stars": 1000,
            "query_matches": 1,
        }
        for i in range(300)
    ]
    pool = fetch_repos.build_pool(anchor, dynamic)
    assert pool["total_count"] == 250
    assert pool["anchor_count"] == 50
    assert pool["dynamic_count"] == 200


def test_build_pool_short_dynamic_warns(caplog):
    """If dynamic pool is short, build_pool logs a warning."""
    import logging

    anchor = [{"owner": f"a{i}", "repo": "r"} for i in range(50)]
    dynamic = [
        {
            "owner": f"d{i}",
            "repo": "r",
            "stars": 1000,
            "query_matches": 1,
        }
        for i in range(100)  # only 100, not 200
    ]
    with caplog.at_level(logging.WARNING):
        pool = fetch_repos.build_pool(anchor, dynamic)
    assert pool["total_count"] == 150
    assert pool["dynamic_count"] == 100
    assert any("short" in r.message.lower() for r in caplog.records)


def test_build_pool_anchor_entries_marked():
    """Anchor repos in the pool carry source='anchor'."""
    anchor = [{"owner": "torchgeo", "repo": "torchgeo"}]
    pool = fetch_repos.build_pool(anchor, [])
    assert pool["repos"][0]["source"] == "anchor"


def test_build_pool_dynamic_entries_marked():
    """Dynamic repos in the pool carry source='dynamic' and stars."""
    anchor = []
    dynamic = [
        {
            "owner": "vllm-project",
            "repo": "vllm",
            "stars": 75000,
            "query_matches": 3,
        }
    ]
    pool = fetch_repos.build_pool(anchor, dynamic)
    assert pool["repos"][0]["source"] == "dynamic"
    assert pool["repos"][0]["stars"] == 75000


# ── save / load ──────────────────────────────────────────────


def test_save_repo_pool_format(tmp_path, monkeypatch):
    """save_repo_pool writes a JSON file with the expected fields."""
    pool = {
        "fetched_at": "2026-04-10T17:00:00+00:00",
        "anchor_count": 1,
        "dynamic_count": 1,
        "total_count": 2,
        "repos": [
            {"owner": "a", "repo": "b", "source": "anchor"},
            {
                "owner": "c",
                "repo": "d",
                "source": "dynamic",
                "stars": 100,
                "query_matches": 1,
            },
        ],
    }
    target = tmp_path / "repo_pool.json"
    monkeypatch.setattr(fetch_repos, "POOL_PATH", target)
    fetch_repos.save_repo_pool(pool)
    saved = json.loads(target.read_text())
    assert saved["total_count"] == 2
    assert len(saved["repos"]) == 2


def test_load_anchor_repos(tmp_path, monkeypatch):
    """load_anchor_repos parses the YAML repos list."""
    yml = tmp_path / "anchor_repos.yml"
    yml.write_text(
        "repos:\n"
        "  - owner: torchgeo\n"
        "    repo: torchgeo\n"
        "  - owner: pytorch\n"
        "    repo: pytorch\n"
    )
    monkeypatch.setattr(fetch_repos, "ANCHOR_PATH", yml)
    anchor = fetch_repos.load_anchor_repos()
    assert len(anchor) == 2
    assert anchor[0]["owner"] == "torchgeo"


# ── main / dry-run ───────────────────────────────────────────


def test_dry_run_does_not_write(tmp_path, monkeypatch, capsys):
    """--dry-run should not call save_repo_pool."""
    monkeypatch.setattr(
        fetch_repos,
        "load_anchor_repos",
        lambda: [{"owner": "torchgeo", "repo": "torchgeo"}],
    )
    monkeypatch.setattr(fetch_repos, "fetch_dynamic_candidates", lambda q: {})
    save_mock = MagicMock()
    monkeypatch.setattr(fetch_repos, "save_repo_pool", save_mock)
    monkeypatch.setattr(sys, "argv", ["fetch_repos.py", "--dry-run"])
    fetch_repos.main()
    save_mock.assert_not_called()


def test_main_writes_pool(tmp_path, monkeypatch):
    """Without --dry-run, save_repo_pool should be called."""
    monkeypatch.setattr(
        fetch_repos,
        "load_anchor_repos",
        lambda: [{"owner": "torchgeo", "repo": "torchgeo"}],
    )
    monkeypatch.setattr(fetch_repos, "fetch_dynamic_candidates", lambda q: {})
    save_mock = MagicMock()
    monkeypatch.setattr(fetch_repos, "save_repo_pool", save_mock)
    monkeypatch.setattr(sys, "argv", ["fetch_repos.py"])
    fetch_repos.main()
    save_mock.assert_called_once()


# ── query templates ──────────────────────────────────────────


def test_build_queries_substitutes_dates():
    """Date placeholders in query templates are filled in."""
    queries = fetch_repos.build_queries("2026-03-10", "2025-10-10")
    # Each query string should contain at least one of the dates
    assert all(
        "2026-03-10" in q["q"] or "2025-10-10" in q["q"] for q in queries
    )


def test_build_queries_count():
    """build_queries returns exactly 12 queries."""
    queries = fetch_repos.build_queries("2026-03-10", "2025-10-10")
    assert len(queries) == 12  # GFI(6) + help-wanted(5) + trending(1)


def test_build_queries_bucket_assignment():
    """The trending newcomers query is tagged trending; others dynamic."""
    queries = fetch_repos.build_queries("2026-03-10", "2025-10-10")
    trending = [q for q in queries if q["bucket"] == "trending"]
    dynamic = [q for q in queries if q["bucket"] == "dynamic"]
    assert len(trending) == 1
    assert len(dynamic) == 11
    # Sanity: the trending query is the one keyed on "created:>="
    assert "created:>=" in trending[0]["q"]


# ── buckets end-to-end ──────────────────────────────────────


def test_fetch_dynamic_candidates_tracks_buckets_per_repo():
    """A repo matched by both a dynamic and the trending query has both."""
    queries = [
        {"q": "q-dyn", "sort": "stars", "bucket": "dynamic"},
        {"q": "q-trend", "sort": "stars", "bucket": "trending"},
    ]
    with patch(
        "fetch_repos.search_repos",
        side_effect=[
            [_norm_repo(owner="voxel51", name="fiftyone")],
            [_norm_repo(owner="voxel51", name="fiftyone")],
        ],
    ):
        candidates = fetch_repos.fetch_dynamic_candidates(queries)
    buckets = candidates["voxel51/fiftyone"]["buckets"]
    assert buckets == {"dynamic", "trending"}


def test_fetch_dynamic_candidates_bucket_defaults_to_dynamic():
    """Queries without a bucket key fall back to dynamic."""
    queries = [{"q": "q1", "sort": "stars"}]  # no bucket
    with patch(
        "fetch_repos.search_repos",
        return_value=[_norm_repo(owner="pytorch", name="pytorch")],
    ):
        candidates = fetch_repos.fetch_dynamic_candidates(queries)
    assert candidates["pytorch/pytorch"]["buckets"] == {"dynamic"}


def test_build_pool_emits_trending_source():
    """A candidate with 'trending' in its buckets gets source=trending."""
    anchor = []
    dynamic = [
        {
            "owner": "voxel51",
            "repo": "fiftyone",
            "stars": 10000,
            "query_matches": 2,
            "buckets": {"dynamic", "trending"},
        }
    ]
    pool = fetch_repos.build_pool(anchor, dynamic)
    assert pool["repos"][0]["source"] == "trending"
    assert pool["trending_count"] == 1
    assert pool["dynamic_count"] == 0


def test_build_pool_dynamic_only_candidate():
    """A candidate matched only by dynamic queries stays source=dynamic."""
    anchor = []
    dynamic = [
        {
            "owner": "pytorch",
            "repo": "pytorch",
            "stars": 99000,
            "query_matches": 3,
            "buckets": {"dynamic"},
        }
    ]
    pool = fetch_repos.build_pool(anchor, dynamic)
    assert pool["repos"][0]["source"] == "dynamic"
    assert pool["trending_count"] == 0
    assert pool["dynamic_count"] == 1


def test_build_pool_anchor_overrides_trending_via_rank():
    """An anchor repo is removed from dynamic candidates by rank_dynamic.

    Guarantees the pool never double-counts or mis-tags an anchor repo
    that happened to also surface from the trending query.
    """
    candidates = {
        "pytorch/pytorch": {
            "owner": "pytorch",
            "repo": "pytorch",
            "stars": 99000,
            "query_matches": 2,
            "buckets": {"trending"},
        },
    }
    ranked = fetch_repos.rank_dynamic(candidates, {"pytorch/pytorch"})
    assert ranked == []

    pool = fetch_repos.build_pool(
        [{"owner": "pytorch", "repo": "pytorch"}], ranked
    )
    assert len(pool["repos"]) == 1
    assert pool["repos"][0]["source"] == "anchor"
    assert pool["trending_count"] == 0


def test_build_pool_missing_buckets_key_defaults_to_dynamic():
    """A legacy candidate dict without 'buckets' gets source=dynamic."""
    anchor = []
    dynamic = [
        {
            "owner": "legacy",
            "repo": "repo",
            "stars": 500,
            "query_matches": 1,
        }
    ]
    pool = fetch_repos.build_pool(anchor, dynamic)
    assert pool["repos"][0]["source"] == "dynamic"


def test_build_pool_has_trending_count_field():
    """Pool output always exposes trending_count key."""
    pool = fetch_repos.build_pool([{"owner": "a", "repo": "b"}], [])
    assert "trending_count" in pool
    assert pool["trending_count"] == 0
