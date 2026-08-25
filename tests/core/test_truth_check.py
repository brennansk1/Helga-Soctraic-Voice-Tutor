"""The truth check, which had never run against a real verifier.

Three things stopped it, and each was invisible until the model was actually
loaded and pointed at a real course:

1. `transformers` is not installed anywhere — it was deliberately removed from
   the core and rag images — so `available()` was False and `claim_verifier`
   had exactly one caller, a manual tool.
2. The promoted `check_truth` called `verifier.supported(...)`. No verifier in
   this repo exposes that; `get_verifier()` returns a CALLABLE. It could not
   have run even with the model present.
3. Claims were paired against every passage retained for their concept — a
   cartesian product. Measured: 39 of 40 pairs unsupported, a number about the
   pairing rather than the course.

It is ADVISORY and returns ok=True whatever it finds. On its seeded set it
caught 3 of 3 false claims and rejected 2 of 3 TRUE ones, both needing a single
inference step. Teaching material rephrases its sources by nature, so failing a
course on this would reject correct content faster than it catches wrong.
"""
import sqlite3

import pytest

from services.core.course_qa import check_truth, _chunks, _overlap


@pytest.fixture()
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE taught_claims (course_uid TEXT, concept_uid TEXT,
                                    ordinal INT, claim TEXT, keywords TEXT);
        CREATE TABLE sources (source_id INTEGER PRIMARY KEY, course_uid TEXT,
                              concept_uid TEXT, title TEXT, url TEXT,
                              passage TEXT, source_type TEXT);
    """)
    return conn


def _seed(conn, claim, passage):
    conn.execute("INSERT INTO taught_claims VALUES ('c1','con_1',0,?,'')", (claim,))
    conn.execute("INSERT INTO sources (course_uid, concept_uid, title, url, "
                 "passage, source_type) VALUES ('c1','con_1','T','u',?,'web')",
                 (passage,))
    conn.commit()


PASSAGE = ("In PostgreSQL, NULL values sort as if they were larger than any "
           "non-null value. Under ORDER BY x ASC they therefore appear last, "
           "and under ORDER BY x DESC they appear first. This can be overridden "
           "with the NULLS FIRST and NULLS LAST modifiers on the clause. ") * 2


def test_no_verifier_is_not_measured_never_a_pass(db):
    r = check_truth(db, "c1", verifier=None)
    assert r["checked"] is False
    assert "NOT measured" in r["reason"]


def test_a_callable_verifier_is_accepted(db):
    """get_verifier() returns a callable, not an object with .supported()."""
    _seed(db, "PostgreSQL sorts NULLs last under ORDER BY ASC.", PASSAGE)
    r = check_truth(db, "c1", verifier=lambda claim, passage: True)
    assert r["checked"] is True, f"a callable verifier was rejected: {r}"
    assert r["unsupported"] == 0


def test_an_object_verifier_is_also_accepted(db):
    class V:
        def supported(self, claim, passage):
            return True
    _seed(db, "PostgreSQL sorts NULLs last under ORDER BY ASC.", PASSAGE)
    assert check_truth(db, "c1", verifier=V())["checked"] is True


def test_it_never_fails_a_course(db):
    """Advisory by design — see the module docstring."""
    _seed(db, "PostgreSQL sorts NULLs last under ORDER BY ASC.", PASSAGE)
    r = check_truth(db, "c1", verifier=lambda c, p: False)
    assert r["ok"] is True, "an advisory check must not fail a course"
    assert r["advisory"] is True


def test_a_near_total_unsupported_share_is_called_thin_evidence(db):
    """Reporting "100% unsupported" as a content finding would send correct
    material to be rewritten. It is a statement about the sourcing."""
    _seed(db, "PostgreSQL sorts NULLs last under ORDER BY ASC.", PASSAGE)
    r = check_truth(db, "c1", verifier=lambda c, p: False)
    assert r["evidence_too_thin"] is True
    assert "SOURCING" in r["note"]


def test_an_irrelevant_passage_is_not_judged_at_all(db):
    """No evidence is not a false claim, and must not manufacture one."""
    _seed(db, "The mitochondrion is the powerhouse of the cell.",
          "A window function computes a value across a set of table rows.")
    r = check_truth(db, "c1", verifier=lambda c, p: False)
    assert r["checked"] is False
    assert "relevant" in r["reason"]


# --- chunking ---------------------------------------------------------------

def test_a_long_source_is_split_so_the_opening_is_not_the_only_evidence():
    """A source is retained as one 4,000-character block. Choosing between
    whole sources handed the model each document's OPENING, which supported
    none of the specific claims measured against it."""
    chunks = _chunks(PASSAGE * 4)
    assert len(chunks) > 1, "a long passage was not split at all"
    assert all(len(c.split()) >= 20 for c in chunks)


def test_chunking_picks_the_relevant_part():
    doc = ("Window functions compute across rows. " * 20 + "\n\n"
           + "Under ORDER BY x DESC, NULL values appear first in PostgreSQL. " * 5)
    claim = "Under ORDER BY DESC, NULLs appear first in PostgreSQL."
    best = max(_chunks(doc), key=lambda c: _overlap(claim, c))
    assert "DESC" in best, "retrieval chose a chunk that cannot settle the claim"


def test_a_short_passage_survives_chunking():
    short = "NULLs sort last under ASC in PostgreSQL, and first under DESC."
    assert _chunks(short) == [short]
