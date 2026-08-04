"""Open-textbook grounding: the right SHAPE of source for a course.

Crossref and arXiv raise grounding confidence, but they are the wrong shape for
building a course. A paper reports a result at the frontier; a course teaches
the settled canon, explained. You do not build introductory biology from a 2024
Nature letter — you build it from a textbook.

Wikibooks and Wikiversity are open textbooks and course materials: content that
has already done the work of selecting, sequencing and explaining.

These test the pure filtering logic, so they need no network.
"""

import os
import sys
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load_is_relevant():
    """Import just the predicate, without research_server's heavy deps."""
    import re as _re
    src = open(os.path.join(_ROOT, 'services/research/research_server.py')).read()
    start = src.index('_OFF_TOPIC_PREFIXES')
    end = src.index('def textbook_lookup')
    ns = {'re': _re}
    exec(src[start:end], ns)
    return ns['_is_relevant']


class TestRelevanceFilter(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.is_relevant = staticmethod(_load_is_relevant())

    def test_rejects_a_language_page_about_the_word(self):
        """MEASURED FALSE POSITIVE. Searching "Cell Biology" returned
        "Pinyin/Cell (biology)" — a Chinese-language-learning page about the
        WORD "cell" — which would have been cited to a learner as a biology
        textbook. Title matching alone cannot tell those apart."""
        self.assertFalse(self.is_relevant(
            "Cell Biology", "Pinyin/Cell (biology)",
            "xibao. The pinyin for this term is written with the third tone."))

    def test_rejects_other_navigational_namespaces(self):
        for title in ("Subject:Biology", "Shelf:Science", "Wikibooks:Policy",
                      "Portal:Mathematics", "Wikijunior:Animals"):
            self.assertFalse(self.is_relevant("Biology", title, "x" * 900),
                             f"{title} is navigation, not teaching material")

    def test_accepts_a_real_textbook_chapter(self):
        body = ("Cell biology is the study of cell structure and function. "
                "Every cell contains a membrane. The cell membrane regulates "
                "transport. Cell organelles perform specialised roles in the "
                "cell. ") * 4
        self.assertTrue(self.is_relevant("Cell Biology", "Cell Biology", body))

    def test_rejects_a_page_that_merely_mentions_the_subject_once(self):
        """A gloss is not a textbook. A page that teaches a subject uses its
        vocabulary repeatedly, not once in passing."""
        body = ("This article is about medieval poetry. It briefly notes that "
                "cell biology was unknown at the time. " + "poetry " * 200)
        self.assertFalse(self.is_relevant("Cell Biology", "Medieval Poetry", body))

    def test_requires_most_query_terms_to_appear(self):
        body = "eigenvalues eigenvalues eigenvalues " * 20   # no 'linear'/'algebra'
        self.assertFalse(self.is_relevant(
            "linear algebra eigenvalues", "Something", body))

    def test_an_empty_query_is_not_used_to_reject(self):
        self.assertTrue(self.is_relevant("", "Any Title", "x" * 900))


class TestTextbookSourcesAreTreatedAsGrounding(unittest.TestCase):

    def test_a_stub_threshold_exists(self):
        src = open(os.path.join(
            _ROOT, 'services/research/research_server.py')).read()
        self.assertIn('TEXTBOOK_MIN_CHARS', src,
                      "a one-line stub adds a citation and a confidence point "
                      "while teaching the learner nothing")

    def test_cache_key_is_versioned(self):
        """The relevance filter shipped and the cache kept serving
        'Pinyin/Cell (biology)' — entries cached under old behaviour are wrong,
        not merely old."""
        src = open(os.path.join(
            _ROOT, 'services/research/research_server.py')).read()
        block = src[src.index('def textbook_lookup'):][:800]
        self.assertIn('v2|', block)


if __name__ == '__main__':
    unittest.main()
