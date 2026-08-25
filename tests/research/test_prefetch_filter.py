"""Don't fetch a page we were always going to throw away.

The relevance gate is precise and runs on full text — but every page has
already been fetched by the time it runs, and it then discards 77% of them
(1,047 of 1,360, measured on the current build). That is four times the
network, the extraction and the load on other people's servers, spent on
material that was never going to be used.

The pre-filter is deliberately WEAK: it drops a hit only when the title and
snippet share no topic word with the concept at all. That is not a borderline
judgement — it is "Merism" returned for "Partitioning Scope".

The division of labour is the point of these tests. Anything subtler than zero
overlap must still reach the precise gate, because a snippet often omits the
subject word that a good page spends three paragraphs on, and dropping those
costs a real source.
"""
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
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "research_cache"))
    import importlib
    import research_server
    return importlib.reload(research_server)


# Every one of these was recorded by the gate as an actual off-topic hit.
@pytest.mark.parametrize("concept,title,snippet", [
    ("Partitioning Scope", "Merism", "A merism is a rhetorical device pairing"),
    ("Partitioning Scope", "Advanced Placement",
     "AP is a program in the United States created by the College Board"),
    ("NULL handling", "List of chemical elements", "periodic table of elements"),
])
def test_obvious_misses_are_never_fetched(rs, concept, title, snippet):
    assert not rs._worth_fetching(concept, title, snippet,
                                  must_include=f"{concept} SQL")


@pytest.mark.parametrize("concept,title,snippet", [
    ("Partitioning Scope", "Table partitioning in PostgreSQL",
     "How to partition a large table"),
    ("Window Functions", "PostgreSQL: Documentation: Window Functions",
     "Window functions compute across rows"),
    # The snippet says nothing useful. Fetch it — judging a page by a bad
    # summary is how a good source gets thrown away.
    ("Recursive CTEs", "Recursive Queries", ""),
])
def test_plausible_hits_are_still_fetched(rs, concept, title, snippet):
    assert rs._worth_fetching(concept, title, snippet,
                              must_include=f"{concept} SQL")


def test_a_shared_word_is_enough_to_earn_a_fetch(rs):
    """The pre-filter must NOT try to be the gate.

    "Sequential Scan Cost" against a radiation-oncology page shares
    sequential, scan and cost — real overlap, wrong subject. The pre-filter
    lets it through on purpose; the precise gate is what rejects it, on the
    full body, because SQL appears nowhere in it.
    """
    concept = "Sequential Scan Cost"
    title = "Radiation oncology workup"
    snippet = "a sequential series of scans and the cost of care"
    assert rs._worth_fetching(concept, title, snippet,
                              must_include=f"{concept} SQL"), \
        "pre-filter over-reached into the gate's job"

    # The gate as it is ACTUALLY called: must_include is the course title
    # alone. Passing the concept title in as well would dilute the rule the
    # gate exists for — "sequential" would satisfy a requirement meant to be
    # satisfied only by "SQL".
    body = ("The workup proceeds as a sequential series of scans. "
            "The cost of care is assessed at each stage. ") * 6
    assert not rs._is_relevant(f"{concept} SQL", title, body,
                               must_include="SQL"), \
        "the precise gate failed to catch what the pre-filter passed on"


def test_no_terms_means_fetch(rs):
    """With nothing to judge against, judging is guessing."""
    assert rs._worth_fetching("", "Anything", "any snippet", must_include="")
