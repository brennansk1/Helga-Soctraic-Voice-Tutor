"""Grounding-confidence ranking for the research service.

The file this tests has been the site of the same bug three times: a source
kind the CALLER does not pass is a source kind that does not exist. So these
tests guard the WIRING as well as the arithmetic.
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(_ROOT, "services/research"))



# ------------------------------------------- official documentation as a kind
#
# For a technical concept the authoritative source is the project's own
# documentation. "What does dbt's ref() do at compile time" is answered
# definitively by docs.getdbt.com and approximately by everything else; there
# is no textbook, no paper, and the Wikipedia article is a stub.
#
# Scored as generic "web" it took the LOWEST weight (0.20, cap 0.40), so a CS
# concept grounded in three pages of primary documentation scored BELOW a
# history concept grounded in one Wikipedia article.

from ranking import compute_confidence, is_documentation  # noqa: E402


def test_known_documentation_hosts_are_recognised():
    for url in ("https://docs.getdbt.com/docs/build/materializations",
                "https://docs.python.org/3/library/re.html",
                "https://www.postgresql.org/docs/current/sql-select.html",
                "https://developer.mozilla.org/en-US/docs/Web/JavaScript",
                "https://kubernetes.io/docs/concepts/",
                "https://www.rfc-editor.org/rfc/rfc9110"):
        assert is_documentation(url), url


def test_a_marketing_site_with_a_docs_path_is_not_documentation():
    """A registry, not a heuristic. A false positive here manufactures
    confidence, which is the failure this whole module exists to avoid."""
    for url in ("https://somevendor.com/docs/marketing",
                "https://medium.com/@someone/dbt-tips",
                "https://blog.example.com/docs/how-i-learned-python",
                "https://notdocs.python.org.evil.com/x"):
        assert not is_documentation(url), url


def test_subdomains_and_www_are_handled():
    assert is_documentation("https://www.postgresql.org/docs/")
    assert is_documentation("https://three.docs.rs/foo")


def test_malformed_urls_do_not_raise():
    for bad in (None, "", "not a url", "://"):
        assert is_documentation(bad) is False


def test_documentation_outweighs_the_generic_web_bucket():
    """The whole point: three doc pages must beat three blog posts."""
    docs = compute_confidence(False, 0, documentation_count=3)
    web = compute_confidence(False, 3)
    assert docs > web, (docs, web)


def test_documentation_is_weighted_level_with_textbooks():
    """Both are the settled canon for their subject."""
    assert (compute_confidence(False, 0, documentation_count=2)
            == compute_confidence(False, 0, textbook_count=2))


def test_documentation_is_capped_like_every_other_kind():
    """Without caps this rewards COUNT, and a pile of pages manufactures
    confidence rather than measuring it."""
    assert compute_confidence(False, 0, documentation_count=99) <= 0.6


def test_a_cs_concept_can_now_reach_full_confidence():
    """Before: Wikipedia stub + 3 doc pages = 0.80, capped by the web bucket.
    A tool's own documentation could never earn full grounding."""
    assert compute_confidence(True, 0, documentation_count=2) >= 1.0


def test_the_caller_passes_the_new_kind():
    """This function has been the site of the same bug twice: a kind the
    CALLER does not pass does not exist. Guard the wiring, not just the math."""
    import os
    import re
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    src = open(os.path.join(root, "services/research/research_server.py"),
               encoding="utf-8").read()
    idx = src.index("confidence = compute_confidence(")
    call = src[idx:idx + 300]
    assert "doc_sources" in call, (
        "documentation is computed but never passed — the third instance of "
        "this exact bug")
