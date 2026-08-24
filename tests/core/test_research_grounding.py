"""Grounding must be per-CONCEPT, scored on every kind, and cited only if read.

Three defects are guarded here, each of which shipped and each of which looked
fine from the outside:

1. EVERY CONCEPT IN A MODULE GOT THE SAME RESEARCH. The two largest
   contributors to combined_text — two textbook extracts at 6000 chars, plus
   the primary literature — were looked up on `module_title or course_title or
   title`, and `module_title` is always non-empty on the hydration path. They
   were disk-cached on the module, so concept 1 and concept 12 received
   byte-identical text. With SearXNG down (the only concept-specific source) a
   12-concept module had ONE grounding blob.

2. THE CONFIDENCE SCORE WAS BLIND TO THREE OF SEVEN KINDS, for the third time.
   A history concept grounded in four Library of Congress documents and two Met
   artefacts scored 0.00, was stamped "Limited sources" to the learner, and
   triggered a wasted retry — while carrying six real citations.

3. SOURCES WERE CITED FOR TEXT THAT NEVER REACHED THE MODEL: primary literature
   contributed a heading and a URL, domain sources were cited whether or not
   they had text (or a URL), and the 3000-word truncation ran AFTER assembly so
   it always cut the tail — which was the web extracts, the only
   concept-specific text — while still listing them as sources.

No network: every lookup is patched.
"""

import asyncio
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
for _p in (_ROOT, os.path.join(_ROOT, 'services/research')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# research_server opens a diskcache at import time; keep it out of /app.
os.environ.setdefault("CACHE_DIR", tempfile.mkdtemp(prefix="helga_research_"))

import ranking                     # noqa: E402
import research_server as rs       # noqa: E402
import syllabus_sources            # noqa: E402


# --- 2. the score must see every kind ----------------------------------------

class TestEverySourceKindIsRegistered(unittest.TestCase):
    """THE GUARD THAT SHOULD HAVE EXISTED THREE BUGS AGO.

    The previous guard asked "does the caller mention textbook/journal/web?",
    which can only ever catch the kinds that already exist — it passed happily
    while `artefact`, `primary_document` and `structured_fact` scored zero.
    This one derives the answer from the code: every source kind emitted
    ANYWHERE in the research service must be registered in the weight table, so
    a new kind fails here on the day it is added.
    """

    def _emitted_kinds(self):
        import re
        kinds = set()
        d = os.path.join(_ROOT, 'services/research')
        for name in sorted(os.listdir(d)):
            if not name.endswith('.py'):
                continue
            src = open(os.path.join(d, name)).read()
            # Both spellings: producers write "type", the assembler writes
            # "kind" while the entry is still internal.
            # Both spellings, AND the conditional form — but ONLY in the
            # value position. This matched a literal directly after the key,
            # so writing
            #     "kind": ("documentation" if is_documentation(u) else "web")
            # hid BOTH kinds from the guard. A guard with a blind spot is how
            # an unregistered kind gets in, which is what this class exists to
            # catch. Anchored to the value so it cannot drift onto the
            # neighbouring dict keys ("label", "url", "title", ...).
            for m in re.finditer(
                    r'"(?:type|kind)"\s*(?::|\]\s*=)\s*\(?\s*"([a-z_]+)"'
                    r'(?:[\s\S]{0,160}?\belse\s+"([a-z_]+)")?', src):
                kinds |= {g for g in m.groups() if g}
        return kinds

    def test_the_service_emits_at_least_the_known_kinds(self):
        """A sanity check on the scanner itself — if it silently matched
        nothing, the real test below would pass for the wrong reason."""
        found = self._emitted_kinds()
        self.assertTrue({'artefact', 'primary_document', 'structured_fact',
                         'textbook', 'journal', 'preprint',
                         'web', 'wikipedia'} <= found, found)

    def test_every_emitted_kind_is_scored(self):
        unregistered = self._emitted_kinds() - set(ranking.SOURCE_KIND_WEIGHTS)
        self.assertEqual(
            unregistered, set(),
            f"source kinds {sorted(unregistered)} are produced but not in "
            f"ranking.SOURCE_KIND_WEIGHTS — an unregistered kind is invisible "
            f"to grounding confidence, which is this exact bug for the fourth "
            f"time. Register them (weight + cap family).")

    def test_every_registered_kind_has_a_known_cap_family(self):
        for kind, (_w, family) in ranking.SOURCE_KIND_WEIGHTS.items():
            self.assertIn(family, ranking.FAMILY_CAPS, kind)


class TestArchiveKindsScore(unittest.TestCase):

    def test_the_measured_history_case_is_no_longer_zero(self):
        """Four LoC documents + two Met artefacts scored 0.00 and were stamped
        "Limited sources ... leans more on the model's own knowledge"."""
        sources = ([{"type": "primary_document"}] * 4
                   + [{"type": "artefact"}] * 2)
        self.assertGreater(ranking.confidence_from_sources(sources), 0.0)

    def test_catalogue_entries_cannot_add_up_to_a_textbook(self):
        """Six 600-char museum labels are not a textbook chapter. Kinds that
        substitute for one another share a cap, or the score rewards COUNT."""
        archive = [{"type": "artefact"}] * 3 + [{"type": "primary_document"}] * 3
        self.assertLess(ranking.confidence_from_sources(archive),
                        ranking.confidence_from_sources(
                            [{"type": "textbook"}] * 2))

    def test_an_unregistered_kind_is_under_weighted_not_invisible(self):
        """Scoring an unknown kind as zero is precisely how this failed three
        times. It must cost points, not disappear."""
        self.assertGreater(
            ranking.confidence_from_sources([{"type": "brand_new_kind"}]), 0.0)

    def test_the_confidence_call_takes_sources_not_counts(self):
        import inspect
        self.assertIn('confidence_from_sources(sources)',
                      inspect.getsource(rs._research_concept_async))


# --- 1. grounding must differ between concepts -------------------------------

class TestSubjectIsConceptFirst(unittest.TestCase):

    def test_the_concept_leads_and_the_module_is_only_a_fallback(self):
        subs = rs._lookup_subjects('Identify the Right Angle',
                                   'Right Triangles', 'Geometry')
        self.assertEqual(subs[0], 'Identify the Right Angle')
        self.assertIn('Right Triangles', subs)      # still reachable
        self.assertIn('Geometry', subs)

    def test_two_concepts_in_one_module_do_not_collapse(self):
        """The cache key follows the query, so if the queries are identical the
        cached grounding is identical — which is what made concept 1 and
        concept 12 byte-for-byte the same."""
        a = rs._lookup_subjects('Identify the Right Angle', 'Right Triangles', 'Geometry')
        b = rs._lookup_subjects('Name the Short Sides', 'Right Triangles', 'Geometry')
        self.assertNotEqual(a[0], b[0])
        self.assertNotEqual(a[:2], b[:2])

    def test_empty_and_duplicate_subjects_are_dropped(self):
        subs = rs._lookup_subjects('Geometry', '', 'Geometry')
        self.assertEqual(subs, ['Geometry'])

    def test_no_lookup_is_keyed_on_the_module_alone(self):
        """The defect is quoted in the module's comments on purpose — that is
        the record of WHY the subject ladder exists — so this checks the
        pipeline body, not the file."""
        import inspect
        body = inspect.getsource(rs._research_concept_async)
        self.assertNotIn('(module_title or course_title or title)', body,
                         "module-first keying is what made a whole module's "
                         "concepts share one grounding blob")

    def test_gather_tops_up_from_the_broader_subject(self):
        """A concept that finds one textbook of its own should get its second
        from the module — not be replaced by the module's two."""
        def fake(subject, limit=2):
            if subject == 'narrow':
                return [{"url": "u1", "title": "own"}]
            return [{"url": "u2", "title": "module"},
                    {"url": "u3", "title": "module2"}]

        out = rs._gather(fake, ['narrow', 'broad'], 2)
        self.assertEqual([r["url"] for r in out], ["u1", "u2"])

    def test_gather_never_duplicates_a_url(self):
        def fake(subject, limit=2):
            return [{"url": "same", "title": "x"}]
        self.assertEqual(len(rs._gather(fake, ['a', 'b', 'c'], 3)), 1)


# --- 3. cite only what the model actually read -------------------------------

def _entry(kind, url="https://example.org/x", text="word " * 500, title="T"):
    return {"kind": kind, "label": kind, "title": title, "url": url,
            "text": text, "tier": 1}


class TestOnlyWhatReachedTheModelIsCited(unittest.TestCase):

    def test_a_source_with_no_text_is_not_cited(self):
        """Primary literature used to contribute a heading and a URL, and was
        still rendered to the learner as a citation worth 0.25 confidence."""
        text, cited = rs._assemble([_entry("journal", text="")])
        self.assertEqual(cited, [])
        self.assertEqual(text, "")

    def test_a_source_with_no_url_is_not_cited(self):
        """Domain sources were appended unconditionally, and an empty url
        rendered as "[Title]()" in the learner-facing citation list."""
        _t, cited = rs._assemble([_entry("artefact", url="")])
        self.assertEqual(cited, [])

    def test_the_budget_does_not_always_cut_the_same_kind(self):
        """Truncating the assembled blob at 3000 words always removed the tail,
        and the tail was the web extracts — the only concept-specific text in
        the whole prompt — while `sources` went on listing them."""
        entries = ([_entry("textbook", url=f"tb{i}", text="word " * 4000)
                    for i in range(3)]
                   + [_entry("web", url=f"web{i}", text="word " * 4000)
                      for i in range(3)])
        _text, cited = rs._assemble(entries)
        kinds = {c["kind"] for c in cited}
        self.assertIn("web", kinds,
                      "the concept-specific source must survive the budget")
        self.assertIn("textbook", kinds)

    def test_every_cited_source_appears_in_the_text(self):
        entries = [_entry("wikipedia", url="w", title="Wiki page"),
                   _entry("textbook", url="t", title="Book chapter"),
                   _entry("journal", url="j", title="Paper", text=""),
                   _entry("web", url="e", title="Page")]
        text, cited = rs._assemble(entries)
        for c in cited:
            self.assertIn(c["title"], text)
        self.assertNotIn("Paper", text)
        self.assertNotIn("Paper", [c["title"] for c in cited])

    def test_the_budget_is_respected(self):
        entries = [_entry("web", url=f"u{i}", text="word " * 5000)
                   for i in range(40)]
        text, _cited = rs._assemble(entries)
        # Headers add a few words per part; the body must stay in budget.
        self.assertLess(len(text.split()), rs.WORD_BUDGET * 1.2)

    def test_primary_literature_requires_an_abstract(self):
        src = open(os.path.join(
            _ROOT, 'services/research/research_server.py')).read()
        self.assertIn('PRIMARY_MIN_ABSTRACT', src)
        block = src[src.index('def primary_source_lookup'):]
        block = block[:block.index('def wiki_lookup')]
        self.assertIn('abstract', block)


# --- the whole pipeline, offline ---------------------------------------------

class _FakeDomainSources:
    """Stand-in for the domain_sources module imported inside the pipeline."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.queries = []

    def classify_domains(self, *texts):
        return ['history']

    def fetch_domain_sources(self, query, domains, **kw):
        self.queries.append(query)
        return list(self.rows)


def _run_pipeline(title, module='Right Triangles', course='Geometry',
                  broaden=False, textbooks=None, primaries=None,
                  domain_rows=(), web=None):
    seen = {"textbook": [], "primary": [], "search": []}

    def fake_textbook(subject, limit=2):
        seen["textbook"].append(subject)
        return list((textbooks or {}).get(subject, []))

    def fake_primary(subject, limit=2):
        seen["primary"].append(subject)
        return list((primaries or {}).get(subject, []))

    async def fake_search(session, query, max_results=5):
        seen["search"].append(query)
        return list((web or {}).get(query, []))

    async def fake_extract(session, url):
        return "extracted " * 200

    fake_ds = _FakeDomainSources(domain_rows)
    with patch.object(syllabus_sources, 'discover_broader_subjects',
                      lambda topic, limit=3: ['Trigonometry', 'Euclidean geometry']), \
         patch.object(rs, 'textbook_lookup', fake_textbook), \
         patch.object(rs, 'primary_source_lookup', fake_primary), \
         patch.object(rs, 'wiki_lookup', lambda t: None), \
         patch.object(rs, 'wiki_search_title', lambda t: None), \
         patch.object(rs, 'searxng_search', fake_search), \
         patch.object(rs, 'extract_page', fake_extract), \
         patch.dict(sys.modules, {'domain_sources': fake_ds}):
        result = asyncio.new_event_loop().run_until_complete(
            rs._research_concept_async(title, module, course, 3, broaden))
    return result, seen, fake_ds


class TestPipelineIsConceptSpecific(unittest.TestCase):

    def test_the_concept_is_the_first_thing_looked_up(self):
        _r, seen, _ds = _run_pipeline('Identify the Right Angle')
        self.assertEqual(seen["textbook"][0], 'Identify the Right Angle')
        self.assertEqual(seen["primary"][0], 'Identify the Right Angle')

    def test_a_concept_hit_is_kept_instead_of_the_module_one(self):
        r, _seen, _ds = _run_pipeline(
            'Pythagorean theorem',
            textbooks={'Pythagorean theorem': [
                {"url": "https://en.wikibooks.org/wiki/Own", "title": "Own book",
                 "source": "Wikibooks", "text": "own " * 300}],
                'Right Triangles': [
                {"url": "https://en.wikibooks.org/wiki/Mod", "title": "Module book",
                 "source": "Wikibooks", "text": "mod " * 300}]})
        titles = [s["title"] for s in r["sources"]]
        self.assertIn("Own book", titles)

    def test_a_source_without_text_never_becomes_a_citation(self):
        r, _seen, _ds = _run_pipeline(
            'X',
            domain_rows=[{"type": "artefact", "source": "Met Museum",
                          "title": "Vase", "url": "", "text": "clay"},
                         {"type": "primary_document", "source": "LoC",
                          "title": "Paper", "url": "https://loc.gov/1",
                          "text": ""}])
        self.assertEqual([s["title"] for s in r["sources"]
                          if s["title"] in ("Vase", "Paper")], [])

    def test_confidence_matches_the_cited_sources(self):
        r, _seen, _ds = _run_pipeline('X')
        self.assertEqual(r["confidence"],
                         ranking.confidence_from_sources(r["sources"]))


class TestBroadenActuallyBroadens(unittest.TestCase):
    """The retry cost a full pipeline run — up to 20 s per concept — and could
    not change the answer: `broaden` was never read, and the module-keyed
    lookups it re-ran hit cache and returned an identical confidence, so the
    caller's strict `>` comparison failed deterministically."""

    def test_the_flag_is_read(self):
        import inspect
        self.assertIn('broaden', inspect.getsource(rs.api_research_concept))
        self.assertIn('broaden', inspect.getsource(rs._research_concept_async))

    def test_broadening_widens_the_subjects_searched(self):
        with patch.object(rs, '_lookup_subjects', wraps=rs._lookup_subjects):
            narrow, seen_n, _ = _run_pipeline('Concept A', broaden=False)
            wide, seen_w, _ = _run_pipeline('Concept A', broaden=True)
        self.assertGreaterEqual(len(seen_w["search"]), len(seen_n["search"]))
        self.assertGreaterEqual(len(wide["subjects_tried"]),
                                len(narrow["subjects_tried"]))

    def test_broadening_keeps_the_narrow_queries(self):
        """A retry that drops what the first pass found is not a broader
        search, it is a different one — and the caller replaces the whole
        result when confidence improves."""
        _n, seen_n, _ = _run_pipeline('Concept A', broaden=False)
        _w, seen_w, _ = _run_pipeline('Concept A', broaden=True)
        for q in seen_n["search"]:
            self.assertIn(q, seen_w["search"])
        self.assertEqual(seen_w["textbook"][0], 'Concept A')

    def test_broadening_raises_the_ceiling_per_kind(self):
        import inspect
        src = inspect.getsource(rs._research_concept_async)
        self.assertIn('n_textbook = 3 if broaden', src)


# --- 6. the blocklist must actually block ------------------------------------

class TestBlockedHostsIncludeTheirCanonicalForm(unittest.TestCase):
    """The blocklist matched netloc exactly and spelled only two of six entries
    with "www.", so the four sites that serve from www — which is their
    canonical host — were never blocked at all."""

    def test_www_variants_are_blocked(self):
        for url in ("https://www.studocu.com/doc/1",
                    "https://www.quizlet.com/x",
                    "https://www.brainly.com/q",
                    "https://www.bartleby.com/q",
                    "https://www.chegg.com/q",
                    "https://www.coursehero.com/file/1"):
            self.assertEqual(ranking.domain_tier(url), -1, url)

    def test_bare_and_subdomain_variants_are_blocked(self):
        for url in ("https://quizlet.com/x", "https://es.coursehero.com/f"):
            self.assertEqual(ranking.domain_tier(url), -1, url)

    def test_www_tier_1_is_still_tier_1(self):
        for url in ("https://www.arxiv.org/abs/1", "https://arxiv.org/abs/1",
                    "https://www.nature.com/a", "https://www.britannica.com/a",
                    "https://www.khanacademy.org/x"):
            self.assertEqual(ranking.domain_tier(url), 1, url)

    def test_an_unrelated_host_is_not_blocked_by_a_suffix_accident(self):
        self.assertNotEqual(ranking.domain_tier("https://notchegg.com/x"), -1)


if __name__ == '__main__':
    unittest.main()
