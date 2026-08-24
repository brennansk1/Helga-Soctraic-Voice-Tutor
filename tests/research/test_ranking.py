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

from ranking import (  # noqa: E402
    SOURCE_KIND_WEIGHTS, confidence_from_sources, is_documentation)


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


def _sources(**kinds):
    """Source dicts, which is what the scorer now reads."""
    out = []
    for kind, n in kinds.items():
        out += [{"type": kind}] * n
    return out


# THE CALL SHAPE CHANGED; THE CLAIMS DID NOT.
#
# These asserted against `compute_confidence(..., documentation_count=N)`.
# Grounding confidence is now read off the SOURCE DICTS through one weights
# table (ranking.SOURCE_KIND_WEIGHTS), because a hand-maintained list of kinds
# in the CALLER was the same defect three times over. Every assertion below is
# the original one, restated against the table.


def test_documentation_outweighs_the_generic_web_bucket():
    """The whole point: three doc pages must beat three blog posts."""
    docs = confidence_from_sources(_sources(documentation=3))
    web = confidence_from_sources(_sources(web=3))
    assert docs > web, (docs, web)


def test_documentation_is_weighted_level_with_textbooks():
    """Both are the settled canon for their subject."""
    assert (confidence_from_sources(_sources(documentation=2))
            == confidence_from_sources(_sources(textbook=2)))


def test_documentation_is_capped_like_every_other_kind():
    """Without caps this rewards COUNT, and a pile of pages manufactures
    confidence rather than measuring it."""
    assert confidence_from_sources(_sources(documentation=99)) <= 0.6


def test_a_cs_concept_can_now_reach_full_confidence():
    """Before: Wikipedia stub + 3 doc pages = 0.80, capped by the web bucket.
    A tool's own documentation could never earn full grounding."""
    assert confidence_from_sources(
        _sources(wikipedia=1, documentation=2)) >= 1.0


def test_documentation_is_a_registered_kind():
    """An unregistered kind scores on the conservative default and is merely
    logged, so a typo here would be silent."""
    assert "documentation" in SOURCE_KIND_WEIGHTS


def test_the_caller_actually_assigns_the_kind():
    """This has been the site of the same bug three times: a kind the CALLER
    never assigns does not exist. `is_documentation` knew, and nothing asked
    it at the point the kind was decided. Guard the wiring, not just the math.
    """
    import os
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    src = open(os.path.join(root, "services/research/research_server.py"),
               encoding="utf-8").read()
    assert '"documentation"' in src and "is_documentation(result[" in src, (
        "documentation is registered in the weights table but never assigned "
        "to a source — a dead table entry")
