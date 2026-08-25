"""A page must TEACH the subject, not merely contain its words.

Regression: "Mirad Grammar/Word Families" — a page about a CONSTRUCTED
LANGUAGE — was cited as a textbook source on 8 of 22 concepts of a quantum
computing course, while the course reported Source confidence 1.00.

The page is 208,000 characters and contains "quantum" ZERO times. It passed
because half the query's words ("state", "notation", "families") appear in any
document that large, and the old raw count of >= 3 is trivial at that length.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "services", "research"))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "rs_relevance",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "services", "research", "research_server.py"))


def _load():
    """Import only the pure helper; the module touches /app at import time."""
    import re as _re
    src = open(_spec.origin).read()
    ns = {"re": _re}
    start = src.index("_OFF_TOPIC_PREFIXES =")
    end = src.index("def textbook_lookup")
    exec(compile(src[start:end], "rs", "exec"), ns)
    return ns["_is_relevant"]


_is_relevant = _load()

# Densities measured from the real pages (per 10k chars):
#                      quantum  qubit  entanglement  state
#  Mirad grammar         0.00   0.00      0.00        0.91
#  real quantum chapter  2.65   2.60      2.42       16.97
MIRAD = ("state " * 19 + "notation families " + "grammar word " * 900) * 1
QUANTUM = ("quantum qubit entanglement state " * 60 + "filler text " * 400)


class TestRelevance(unittest.TestCase):
    def test_rejects_page_that_never_mentions_the_subject(self):
        self.assertFalse(
            _is_relevant("Quantum State Notation Families",
                         "Mirad Grammar/Word Families", MIRAD))

    def test_rejects_on_generic_word_overlap_alone(self):
        """'state'/'notation'/'families' are half the query and prove nothing."""
        self.assertFalse(_is_relevant("Quantum Computing",
                                      "Mirad Grammar/Word Families", MIRAD))

    def test_keeps_a_page_that_genuinely_teaches_the_subject(self):
        self.assertTrue(
            _is_relevant("entanglement properties",
                         "Quantum theory of observation/Entanglement", QUANTUM))

    def test_long_page_does_not_get_an_easier_pass(self):
        """A raw count threshold grows more permissive as pages get longer;
        density must not."""
        padded = MIRAD + ("unrelated prose " * 20000)
        self.assertFalse(_is_relevant("Quantum Computing",
                                      "Mirad Grammar/Word Families", padded))

    def test_off_topic_title_prefixes_still_rejected(self):
        self.assertFalse(_is_relevant("cell biology",
                                      "Pinyin/Cell (biology)", QUANTUM))

    def test_empty_query_is_permissive(self):
        self.assertTrue(_is_relevant("", "Anything", QUANTUM))


if __name__ == "__main__":
    unittest.main()


class TestShortSubjectTerms(unittest.TestCase):
    """'sql' is three letters, and the extractor required four.

    _is_relevant built its term set with [a-z]{4,}, so the subject word of an
    SQL course was discarded from every query. Appending the subject to the
    relevance check therefore added nothing — the only token that could
    discriminate was the one being dropped. Same for AI, ML, DNA, RNA, GDP.

    Measured on the Advanced SQL build: "Sequential Scan Cost" was grounded on
    a radiation-oncology page, because a workup is sequential, involves scans
    and has a cost.
    """

    def setUp(self):
        # _load() returns the function, not a module — grab the namespace the
        # same way the file already does at import time.
        import re as _re
        src = open(_spec.origin).read()
        ns = {"re": _re}
        start = src.index("_OFF_TOPIC_PREFIXES =")
        end = src.index("def textbook_lookup")
        exec(compile(src[start:end], "rs", "exec"), ns)
        self.ns = ns

    def test_a_three_letter_subject_survives_extraction(self):
        terms = self.ns['_content_terms']("Sequential Scan Cost advanced sql")
        self.assertIn("sql", terms)

    def test_function_words_do_not(self):
        terms = self.ns['_content_terms']("the cost of the scan and its use")
        for w in ("the", "and", "its", "use"):
            self.assertNotIn(w, terms)

    def test_the_subject_is_required_not_counted(self):
        med = ("Radiation oncology for prostate cancer. Workup includes PSA and "
               "staging. The sequential workup determines cost and scan protocols. ") * 30
        self.assertFalse(self.ns['_is_relevant'](
            "Sequential Scan Cost", "Radiation Oncology", med,
            must_include="advanced sql"))

    def test_a_real_page_still_passes(self):
        sql = ("In SQL a sequential scan reads every page of the table. The planner "
               "estimates the cost of a sequential scan against an index scan. ") * 30
        self.assertTrue(self.ns['_is_relevant'](
            "Sequential Scan Cost", "Seq Scan", sql, must_include="advanced sql"))
