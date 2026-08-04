"""Phase-1 research: gather and analyse material BEFORE any structure exists.

The research node used to run only during hydration — Phase 2, once the
skeleton already existed. So the most consequential decision in the pipeline,
*what this course is made of*, was taken with no evidence at all: one LLM call
from recall. The measured result was a course covering 42% of its own subject
that passed every structural detector, because it was structurally clean and
substantively hollow.

Research now runs in both phases. These test the Phase-1 logic without network.
"""

import os
import sys
import unittest
from unittest.mock import patch

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
for p in (_ROOT, os.path.join(_ROOT, 'services/research')):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.research import curriculum_research as cr  # noqa: E402


def _syllabus(n=6, source='Wikibooks', book='Geometry'):
    return {'source': source, 'book': book, 'url': 'u',
            'chapters': [f'Chapter {i}' for i in range(n)]}


class TestEvidenceMustBeStructural(unittest.TestCase):
    """Book titles are colour; chapter lists are evidence."""

    def _brief(self, syllabi=None, courses=None, texts=None):
        with patch.object(cr, 'subject_outline',
                          return_value={'outlines': syllabi or [], 'vocabulary': []}), \
             patch.object(cr, '_wikiversity_course_shapes', return_value=courses or []), \
             patch.object(cr, '_internet_archive_books', return_value=texts or []), \
             patch.object(cr.time, 'sleep', lambda *a: None):
            return cr.curriculum_brief('x', mastery=3, scope=3)

    def test_titles_alone_are_not_evidence(self):
        """A brief holding six book titles and zero chapters guided nothing
        while reporting '6 sources' and looking like a success — the same
        failure as scoring a missing value as zero."""
        b = self._brief(texts=[{'title': f'Book {i}'} for i in range(6)])
        self.assertFalse(b['found'])
        self.assertEqual(b['chapter_count'], 0)

    def test_chapters_are_evidence(self):
        b = self._brief(syllabi=[_syllabus(6)])
        self.assertTrue(b['found'])
        self.assertEqual(b['chapter_count'], 6)

    def test_nothing_at_all_is_not_found(self):
        self.assertFalse(self._brief()['found'])

    def test_never_raises_when_every_source_fails(self):
        def boom(*a, **k):
            raise RuntimeError('network down')
        with patch.object(cr, 'subject_outline', side_effect=boom), \
             patch.object(cr, '_wikiversity_course_shapes', side_effect=boom), \
             patch.object(cr, '_internet_archive_books', side_effect=boom), \
             patch.object(cr.time, 'sleep', lambda *a: None):
            b = cr.curriculum_brief('x')
        self.assertFalse(b['found'])


class TestOneSourceIsNotCountedTwice(unittest.TestCase):
    """subject_outline already consults Wikiversity. Listing the same book
    under both [Syllabus] and [Course] makes one account look like two
    independent ones — which is exactly the signal the 'appears in MOST of
    them' instruction relies on."""

    def test_wikiversity_is_not_double_counted(self):
        dup = {'course': 'Cell biology', 'sections': ['a', 'b', 'c']}
        with patch.object(cr, 'subject_outline', return_value={
                'outlines': [_syllabus(5, 'Wikiversity', 'Cell biology')],
                'vocabulary': []}), \
             patch.object(cr, '_wikiversity_course_shapes', return_value=[dup]), \
             patch.object(cr, '_internet_archive_books', return_value=[]), \
             patch.object(cr.time, 'sleep', lambda *a: None):
            b = cr.curriculum_brief('cell biology')
        self.assertEqual(b['courses'], [], 'the same book was counted twice')


class TestLevelSteersTheSearch(unittest.TestCase):
    """The preset is not a post-hoc filter. Fetching graduate texts for a
    beginner and asking the model to simplify them produces the worst of
    both."""

    def test_levels_map_to_distinct_profiles(self):
        names = {cr._level_profile(m)['name'] for m in (1, 2, 3, 4, 5)}
        self.assertEqual(len(names), 5)

    def test_beginner_and_graduate_search_differently(self):
        self.assertNotEqual(cr._level_profile(1)['ia_terms'],
                            cr._level_profile(5)['ia_terms'])

    def test_out_of_range_mastery_is_clamped(self):
        self.assertEqual(cr._level_profile(99)['name'],
                         cr._level_profile(5)['name'])
        self.assertEqual(cr._level_profile(0)['name'],
                         cr._level_profile(1)['name'])


class TestBriefIsFramedAsSynthesis(unittest.TestCase):
    """Handing a model a table of contents invites it to copy one, and a copied
    TOC is somebody else's course at somebody else's level in somebody else's
    order."""

    def _text(self):
        return cr.format_brief({
            'topic': 'Geometry', 'level': 'undergraduate', 'scope': 3,
            'found': True, 'syllabi': [_syllabus(4)], 'courses': [],
            'canonical_texts': [], 'chapter_count': 4,
        })

    def test_says_synthesise_not_copy(self):
        self.assertIn('SYNTHESISE, DO NOT COPY', self._text())

    def test_warns_the_accounts_disagree(self):
        self.assertIn('will not agree', self._text())

    def test_requires_cutting_to_scope(self):
        self.assertIn('not a course', self._text())

    def test_requires_pedagogical_resequencing(self):
        self.assertIn('Re-sequence for learning', self._text())

    def test_requires_the_result_to_be_its_own(self):
        self.assertIn('YOUR structure', self._text())

    def test_empty_when_no_evidence(self):
        self.assertEqual(cr.format_brief({'found': False}), '')


class TestNoisySignalsWereRemovedNotShipped(unittest.TestCase):
    def test_adjacency_is_gone_and_documented(self):
        """Wikipedia's prop=links returns links ALPHABETICALLY, so 'cell
        biology' produced Actinide chemistry. Noise in a prompt is worse than
        absence — the model cannot tell a bad signal from a good one."""
        src = open(os.path.join(
            _ROOT, 'services/research/curriculum_research.py')).read()
        self.assertNotIn('def _related_subjects', src)
        self.assertIn('Actinide chemistry', src, 'the reason must survive')


if __name__ == '__main__':
    unittest.main()
