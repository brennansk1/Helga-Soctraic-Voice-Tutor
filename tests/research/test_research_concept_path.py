"""The whole research path must actually execute.

A pre-fetch filter was added referencing `query`, a name that does not exist in
that scope — the loop variable is `q`, and results are aggregated across every
query in the ladder. Every call to /api/research_concept returned 500.

88 research tests passed throughout. All of them exercise helper functions;
none of them ran the async path end to end, so a NameError on a live line was
invisible to the suite. This test exists to make that line executable in CI,
with the network mocked, because a unit test of a helper cannot tell you the
function that calls it still parses its own variables.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "services", "research"))


@pytest.fixture()
def rs(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    pytest.importorskip("diskcache")
    pytest.importorskip("aiohttp")
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "research_cache"))
    import importlib
    import research_server
    return importlib.reload(research_server)


@pytest.fixture()
def offline(rs, monkeypatch):
    """Every outbound call stubbed. The point is the code path, not the net."""
    async def _search(session, q):
        return [{"url": "https://example.org/a", "title": "Recursive CTEs in SQL",
                 "snippet": "A recursive CTE walks a hierarchy in SQL.", "tier": 1}]

    async def _extract(session, url):
        return ("A recursive common table expression repeats until it adds no "
                "new rows. " * 20)

    monkeypatch.setattr(rs, "searxng_search", _search)
    monkeypatch.setattr(rs, "extract_page", _extract)
    monkeypatch.setattr(rs, "wiki_lookup", lambda *a, **k: None)
    monkeypatch.setattr(rs, "wiki_search_title", lambda *a, **k: None)
    monkeypatch.setattr(rs, "primary_source_lookup", lambda *a, **k: [])
    monkeypatch.setattr(rs, "textbook_lookup", lambda *a, **k: [])
    return rs


def test_the_async_path_runs_without_a_nameerror(offline):
    """The regression, exactly: this raised NameError on a live line."""
    out = asyncio.get_event_loop().run_until_complete(
        offline._research_concept_async(
            "Recursive CTE Mechanics", "Recursive CTEs", "Advanced SQL",
            mastery=4))
    assert isinstance(out, dict)
    assert "sources" in out and "combined_text" in out


def test_the_path_carries_passages_back(offline):
    """The other half: a citation with no text cannot be verified later."""
    out = asyncio.get_event_loop().run_until_complete(
        offline._research_concept_async(
            "Recursive CTE Mechanics", "Recursive CTEs", "Advanced SQL",
            mastery=4))
    if not out["sources"]:
        pytest.skip("no source survived the relevance gate in this fixture")
    assert any((s.get("passage") or "").strip() for s in out["sources"]), \
        "every source came back without its text"


def test_evidence_sources_key_is_always_present(offline):
    """Downstream reads it unconditionally; absent must mean empty, not None."""
    out = asyncio.get_event_loop().run_until_complete(
        offline._research_concept_async(
            "Recursive CTE Mechanics", "Recursive CTEs", "Advanced SQL",
            mastery=4))
    assert isinstance(out.get("evidence_sources"), list)
