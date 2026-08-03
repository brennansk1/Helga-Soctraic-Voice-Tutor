"""Primary literature must count toward grounding confidence.

It did not. The caller filtered `[s for s in sources if s["type"] == "web"]`,
and Crossref/arXiv results carry type "journal"/"preprint" — so a concept
grounded in Wikipedia PLUS two peer-reviewed papers scored 0.40, identical to
Wikipedia alone. With SearXNG down that capped every concept at 0.4 against a
0.5 floor, so every concept in every course shipped marked "Limited sources"
and no course could clear the grounding criterion.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
for p in (_root, os.path.join(_root, 'services/research')):
    if p not in sys.path:
        sys.path.insert(0, p)

from ranking import compute_confidence  # noqa: E402

FLOOR = 0.5  # HELGA_CONFIDENCE_FLOOR default


class TestPrimarySourcesCount(unittest.TestCase):
    def test_primary_sources_raise_confidence(self):
        self.assertGreater(compute_confidence(True, 0, 2),
                           compute_confidence(True, 0, 0))

    def test_wikipedia_plus_primary_clears_the_floor(self):
        """The exact real-world case: SearXNG down, Wikipedia + 1 DOI."""
        self.assertGreaterEqual(compute_confidence(True, 0, 1), FLOOR)

    def test_wikipedia_alone_does_not_clear_the_floor(self):
        """Thin grounding must still be reported as thin."""
        self.assertLess(compute_confidence(True, 0, 0), FLOOR)

    def test_nothing_scores_zero(self):
        self.assertEqual(compute_confidence(False, 0, 0), 0.0)

    def test_primary_outweighs_a_generic_web_page(self):
        """Peer-reviewed literature is stronger evidence than an arbitrary page."""
        self.assertGreater(compute_confidence(False, 0, 1),
                           compute_confidence(False, 1, 0))

    def test_confidence_never_exceeds_one(self):
        self.assertLessEqual(compute_confidence(True, 10, 10), 1.0)

    def test_backwards_compatible_two_arg_call(self):
        """Existing callers must keep working."""
        self.assertEqual(compute_confidence(True, 1), 0.6)


class TestFetchedAtEveryLevel(unittest.TestCase):
    def test_primary_lookup_is_not_gated_on_mastery(self):
        """The contract REQUIRES a primary citation only at mastery >= 4, but
        the confidence floor applies at every level. Gating the lookup meant a
        beginner course could reach 0.4 at best."""
        p = os.path.join(_root, 'services/research/research_server.py')
        with open(p) as f:
            src = f.read()
        self.assertNotIn("if (mastery or 1) >= 4:", src,
                         "primary lookup must not be gated on mastery")
        self.assertIn("primary_source_lookup", src)


if __name__ == '__main__':
    unittest.main()
