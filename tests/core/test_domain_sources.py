"""Domain-routed sources: the right archive for the right subject.

Querying every source for every concept is worse than not having them. A
biology concept has no use for a museum object, and a hit returned anyway is
not neutral — it costs latency AND adds a citation that inflates grounding
confidence while teaching nothing. That is how a scoring system starts
manufacturing confidence instead of measuring it.
"""

import os
import sys
import unittest
from unittest.mock import patch

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from services.research import domain_sources as ds  # noqa: E402


class TestDomainClassification(unittest.TestCase):

    def test_art_topics(self):
        self.assertIn('art', ds.classify_domains('Impressionism', 'Art History'))

    def test_history_topics(self):
        self.assertIn('history', ds.classify_domains("Women's suffrage", 'US History'))

    def test_science_topics(self):
        self.assertIn('science', ds.classify_domains('Cell membrane transport',
                                                     'Cell Biology'))

    def test_philosophy_topics(self):
        self.assertIn('philosophy',
                      ds.classify_domains("Kant's categorical imperative", 'Ethics'))

    def test_an_unclassifiable_topic_yields_nothing(self):
        self.assertEqual(ds.classify_domains('Widget assembly'), [])

    def test_empty_input_is_safe(self):
        self.assertEqual(ds.classify_domains(None, ''), [])


class TestRouting(unittest.TestCase):

    def _fetch(self, domains):
        calls = []

        def spy(name):
            def f(q, limit=2):
                calls.append(name)
                return [{'type': 't', 'source': name, 'title': 'x', 'text': 'y'}]
            return f

        registry = (
            ('met', spy('met'), ('art',)),
            ('loc', spy('loc'), ('history',)),
            ('wikidata', spy('wikidata'), ('*',)),
        )
        with patch.object(ds, 'DOMAIN_SOURCES', registry):
            ds.fetch_domain_sources('q', domains)
        return calls

    def test_a_biology_concept_never_queries_a_museum(self):
        self.assertNotIn('met', self._fetch(['science']))

    def test_an_art_concept_does(self):
        self.assertIn('met', self._fetch(['art']))

    def test_history_gets_primary_documents_not_artefacts(self):
        calls = self._fetch(['history'])
        self.assertIn('loc', calls)
        self.assertNotIn('met', calls)

    def test_universal_sources_run_for_every_domain(self):
        for d in (['science'], ['art'], []):
            self.assertIn('wikidata', self._fetch(d))

    def test_a_failing_source_does_not_break_the_others(self):
        def boom(q, limit=2):
            raise RuntimeError('archive down')

        def ok(q, limit=2):
            return [{'type': 't', 'source': 'ok', 'title': 'x', 'text': 'y'}]

        with patch.object(ds, 'DOMAIN_SOURCES',
                          (('bad', boom, ('*',)), ('good', ok, ('*',)))):
            out = ds.fetch_domain_sources('q', ['science'])
        self.assertEqual(len(out), 1)

    def test_the_budget_caps_total_citations(self):
        """More sources is not more grounding. A well-covered subject must not
        drown the concept in citations."""
        def many(q, limit=2):
            return [{'type': 't', 'source': 's', 'title': f't{i}', 'text': 'y'}
                    for i in range(10)]
        registry = tuple((f's{i}', many, ('*',)) for i in range(5))
        with patch.object(ds, 'DOMAIN_SOURCES', registry):
            out = ds.fetch_domain_sources('q', ['science'], budget=4)
        self.assertLessEqual(len(out), 4)

    def test_no_query_returns_nothing(self):
        self.assertEqual(ds.fetch_domain_sources('', ['art']), [])


class TestRetiredEndpointsAreDocumented(unittest.TestCase):
    """Three candidates did not work as published. Recording WHY stops them
    being re-added from a stale list."""

    def test_the_probe_findings_survive_in_the_module(self):
        src = open(os.path.join(
            _ROOT, 'services/research/domain_sources.py')).read()
        self.assertIn('RETIRED in 2025', src)      # Chronicling America legacy
        self.assertIn('HANGS', src)                # Open Library search.json
        self.assertIn('Data USA', src)             # 404

    def test_loc_uses_the_current_endpoint(self):
        """The retired host appears in the module docstring on purpose — that
        is the record of WHY it is not used. What must not happen is calling
        it, so this checks the function body, not the file."""
        import inspect
        body = inspect.getsource(ds.loc_chronicling_america)
        call = body[body.index('_get_json('):]
        self.assertIn('loc.gov/collections/chronicling-america', call)
        self.assertNotIn('chroniclingamerica.loc.gov/search', call)


if __name__ == '__main__':
    unittest.main()
