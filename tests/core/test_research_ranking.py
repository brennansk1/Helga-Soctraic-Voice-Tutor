"""Tests for the research-service pure helpers (B12 — online search)."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/research'))
from ranking import domain_tier, build_search_queries, compute_confidence, dedup_by_url


def test_domain_tier_blocked():
    assert domain_tier("https://www.chegg.com/q/123") == -1


def test_domain_tier_tier1_and_edu_gov():
    assert domain_tier("https://en.wikipedia.org/wiki/Photosynthesis") == 1
    assert domain_tier("https://ocw.mit.edu/x") == 1
    assert domain_tier("https://stanford.edu/y") == 1
    assert domain_tier("https://nasa.gov/z") == 1


def test_domain_tier_tier2_and_default():
    assert domain_tier("https://developer.mozilla.org/en/docs") == 2
    assert domain_tier("https://docs.djangoproject.com/") == 2  # startswith docs.
    assert domain_tier("https://some-random-blog.example/post") == 3


def test_build_queries_base_count():
    assert len(build_search_queries("Newton's Laws", "Mechanics", mastery=1)) == 2


def test_build_queries_mastery_adds_academic():
    q3 = build_search_queries("Newton's Laws", "Mechanics", mastery=3)
    q4 = build_search_queries("Newton's Laws", "Mechanics", mastery=4)
    assert len(q3) == 3 and any("in-depth" in q for q in q3)
    assert len(q4) == 4 and any("academic" in q for q in q4)


def test_confidence_scoring():
    assert compute_confidence(False, 0) == 0.0
    assert compute_confidence(True, 0) == 0.4
    assert compute_confidence(True, 2) == 0.8       # 0.4 + 0.4
    # Web is capped at 0.4 (was 0.6). Full confidence must be EARNED with a
    # textbook or a primary source: an arbitrary pile of web results is the
    # evidence profile that looks strong and teaches badly.
    assert compute_confidence(True, 10) == 0.8      # capped (0.4 + 0.4)
    assert compute_confidence(True, 10, 0, 2) == 1.0  # textbooks lift it
    assert compute_confidence(False, 5) == 0.4      # web cap only


def test_dedup_by_url_is_stable():
    rows = [{"url": "a"}, {"url": "b"}, {"url": "a"}, {"url": "c"}]
    assert [r["url"] for r in dedup_by_url(rows)] == ["a", "b", "c"]
