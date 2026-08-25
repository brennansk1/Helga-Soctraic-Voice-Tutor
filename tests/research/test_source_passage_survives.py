"""The evidence must survive the trip from research to the ledger.

`sources.passage` was written empty 529 times out of 529 while its own schema
comment said "a claim cannot be verified against a passage that has expired"
and named the research cache as a speed layer that must never be the only copy.
It was the only copy, with a 24h TTL, so the evidence for any course older than
a day was gone — 1,480 recorded claims and nothing to check them against.

Nothing failed. The text still reached the model through `combined_text`, so
courses built fine and every gate passed. Only the AUDIT trail was empty, and
nothing read it, so nothing noticed.

These tests are therefore about the SEAM, not the function: what `_citation`
emits has to be what the ledger's insert looks for. A test that only checked
`_citation` in isolation would have passed throughout the entire period the
column was empty.
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


ENTRY = {
    "kind": "wikipedia", "label": "Wikipedia", "tier": 1,
    "title": "Null (SQL)",
    "url": "https://en.wikipedia.org/wiki/Null_(SQL)",
    "text": "In SQL, NULL marks a missing value. Under ORDER BY ... ASC, "
            "PostgreSQL sorts NULLs last." * 6,
}


def test_citation_carries_the_text(rs):
    cite = rs._citation(ENTRY)
    assert cite.get("passage"), "the evidence was dropped at the citation"
    assert "NULL marks a missing value" in cite["passage"]


def test_the_ledger_reads_the_key_the_citation_writes(rs):
    """The seam itself. These two lines live in different services."""
    cite = rs._citation(ENTRY)
    # Verbatim from ContentHydrator._record_sources.
    stored = cite.get("snippet") or cite.get("text") or cite.get("passage") or ""
    assert stored, ("the ledger looks for snippet/text/passage and the citation "
                    "emits none of them — this is exactly how the column ended "
                    "up empty 529 times")


def test_passage_is_bounded(rs):
    big = dict(ENTRY, text="x" * 50_000)
    assert len(rs._citation(big)["passage"]) <= rs.PASSAGE_CHARS


def test_a_source_with_no_text_gets_no_passage_key(rs):
    """Absent must stay distinguishable from empty — the schema relies on it:
    a retained row with no text is a source we fetched and got nothing from,
    which is not a source we never fetched."""
    assert "passage" not in rs._citation(dict(ENTRY, text=""))
    assert "passage" not in rs._citation({k: v for k, v in ENTRY.items()
                                          if k != "text"})


def test_identity_fields_are_unchanged(rs):
    """The citation is also what renders in the UI — do not disturb it."""
    cite = rs._citation(ENTRY)
    assert cite["url"] == ENTRY["url"]
    assert cite["title"] == ENTRY["title"]
    assert cite["type"] == ENTRY["kind"]
    assert cite["domain_tier"] == 1
