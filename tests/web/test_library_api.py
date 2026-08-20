"""The /library multi-source search: merging, degradation, and blank covers.

Every test here runs OFFLINE. The adapters are stubbed rather than called,
because a test that needs archive.org to be up tells you about archive.org, not
about this code -- and this project's whole premise is a machine with no
internet. The endpoints themselves were verified live before they were written
into library_api.py; the measurements live in that file's docstrings.

Three properties are worth a test because all three have a failure mode that
looks like success:

  1. MERGING. The same work appears in the Archive and in Gutenberg, and the
     Archive alone holds a dozen scans of one title. A merge that silently
     prefers the unreadable copy leaves a full-text book invisible behind a
     lending-only one.
  2. DEGRADATION. One dead source must not empty the result set, and must be
     NAMED. Fewer books with no explanation reads as "that source has nothing
     on this subject", which is a different and false claim.
  3. THE BLANK COVER. archive.org answers HTTP 200 for an identifier that does
     not exist and serves a 2212-byte placeholder PNG, so "no cover" cannot be
     detected by status code. Getting this wrong puts a grey rectangle on
     screen as though it were cover art, or worse, a broken image.
"""

import hashlib
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "services/web-ui"))

from flask import Flask                                    # noqa: E402

import library_api                                         # noqa: E402
from library_api import (FULL_TEXT, METADATA, RESTRICTED,   # noqa: E402
                         UNKNOWN, _merge, _result, _work_key)


def _app():
    app = Flask(__name__,
                static_folder=os.path.join(_ROOT, "services/web-ui/static"))
    app.register_blueprint(library_api.library_api)
    return app.test_client()


# ---------------------------------------------------------------------------
# Merge and dedup
# ---------------------------------------------------------------------------

class TestWorkKey(unittest.TestCase):
    """One WORK, not one scan. Punctuation, leading articles and the subtitle
    after a colon are editorial noise on the same book."""

    def test_articles_and_punctuation_do_not_split_a_work(self):
        self.assertEqual(_work_key("The Elements of Euclid", "Euclid"),
                         _work_key("elements of euclid!", "Euclid"))

    def test_subtitle_after_a_colon_does_not_split_a_work(self):
        self.assertEqual(
            _work_key("The Elements of Euclid: viz. the first six books",
                      "Euclid"),
            _work_key("Elements of Euclid", "Euclid"))

    def test_different_authors_are_different_works(self):
        # "Elements" alone collides across genuinely different books, which is
        # why the surname is part of the key.
        self.assertNotEqual(_work_key("Elements", "Euclid"),
                            _work_key("Elements", "Coolidge"))


class TestMerge(unittest.TestCase):

    def test_the_readable_copy_wins_over_the_lending_only_one(self):
        rows = [
            _result('internet_archive', 'ia1', 'Elements of Euclid',
                    author='Euclid', availability=RESTRICTED),
            _result('gutenberg', '21076', 'The Elements of Euclid',
                    author='Euclid', availability=FULL_TEXT),
        ]
        merged = _merge(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['source'], 'gutenberg')
        self.assertEqual(merged[0]['availability'], FULL_TEXT)

    def test_the_collapsed_copies_are_disclosed_not_hidden(self):
        rows = [
            _result('gutenberg', '21076', 'Elements of Euclid',
                    author='Euclid', availability=FULL_TEXT),
            _result('internet_archive', 'ia1', 'The Elements of Euclid',
                    author='Euclid', availability=RESTRICTED),
        ]
        merged = _merge(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual([a['source'] for a in merged[0]['also_in']],
                         ['internet_archive'])

    def test_many_scans_of_one_title_collapse_to_one_card(self):
        rows = [_result('internet_archive', f'scan{i}', 'Elements of Euclid',
                        author='Euclid', availability=UNKNOWN)
                for i in range(6)]
        self.assertEqual(len(_merge(rows)), 1)

    def test_availability_outranks_source_preference(self):
        # Gutenberg is the preferred source, but a readable Archive scan beats
        # a Gutenberg record with no readable format. Preference only breaks
        # ties AFTER availability -- otherwise the merge hides the usable copy.
        rows = [
            _result('gutenberg', '1', 'Optics', author='Newton',
                    availability=METADATA),
            _result('internet_archive', 'ia9', 'Optics', author='Newton',
                    availability=FULL_TEXT),
        ]
        merged = _merge(rows)
        self.assertEqual(merged[0]['source'], 'internet_archive')

    def test_unknown_beats_a_known_stub(self):
        # An Archive row we have not checked yet may well be full text.
        # Dropping it for a known-thin wiki page would hide the good copy.
        rows = [
            _result('wikibooks', 'Stub', 'Thermodynamics', author='',
                    availability=METADATA),
            _result('internet_archive', 'ia3', 'Thermodynamics', author='',
                    availability=UNKNOWN),
        ]
        self.assertEqual(_merge(rows)[0]['source'], 'internet_archive')

    def test_distinct_works_are_not_merged(self):
        rows = [
            _result('gutenberg', '1', 'Elements of Euclid', author='Euclid',
                    availability=FULL_TEXT),
            _result('gutenberg', '2', 'Non-Euclidean Geometry',
                    author='Coolidge', availability=FULL_TEXT),
        ]
        self.assertEqual(len(_merge(rows)), 2)


# ---------------------------------------------------------------------------
# Search: concurrency, degradation, filters
# ---------------------------------------------------------------------------

class _SourcePatch:
    """Swap the live adapters for deterministic ones for the duration of a
    test. The route reads library_api.SOURCES at call time, so replacing the
    dict entries is enough and no network is touched."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.original = None

    def __enter__(self):
        self.original = dict(library_api.SOURCES)
        library_api.SOURCES.clear()
        library_api.SOURCES.update(self.mapping)
        return self

    def __exit__(self, *exc):
        library_api.SOURCES.clear()
        library_api.SOURCES.update(self.original)


def _ok(source, titles, availability=FULL_TEXT, **extra):
    def go(q, page, limit):
        return ([_result(source, f'{source}-{i}', t, author='A',
                         availability=availability, **extra)
                 for i, t in enumerate(titles)], len(titles))
    return go


def _boom(exc=RuntimeError('source exploded')):
    def go(q, page, limit):
        raise exc
    return go


class TestSearchDegradation(unittest.TestCase):

    def test_a_failing_source_does_not_kill_the_others(self):
        with _SourcePatch({
            'gutenberg': _ok('gutenberg', ['Euclid A', 'Euclid B']),
            'internet_archive': _boom(),
            'wikibooks': _ok('wikibooks', ['Euclid C']),
        }):
            r = _app().get('/api/library/search?q=euclid')
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(len(d['results']), 3)
        self.assertFalse(d['all_sources_failed'])

    def test_the_failing_source_is_named_never_silent(self):
        with _SourcePatch({
            'gutenberg': _ok('gutenberg', ['Euclid A']),
            'internet_archive': _boom(),
        }):
            d = _app().get('/api/library/search?q=euclid').get_json()
        down = [s for s in d['sources'] if not s['ok']]
        self.assertEqual(len(down), 1)
        self.assertEqual(down[0]['source'], 'internet_archive')
        # A named source in a sentence a person can act on, not a bare flag.
        self.assertIn('Internet Archive', down[0]['error'])

    def test_a_slow_source_times_out_without_holding_the_rest(self):
        import time

        def slow(q, page, limit):
            time.sleep(library_api.SOURCE_TIMEOUT + 5)
            return ([], 0)

        original = library_api.SOURCE_TIMEOUT
        library_api.SOURCE_TIMEOUT = 0.4          # keep the test quick
        try:
            with _SourcePatch({'gutenberg': _ok('gutenberg', ['Fast book']),
                               'internet_archive': slow}):
                started = time.time()
                d = _app().get('/api/library/search?q=euclid').get_json()
                elapsed = time.time() - started
        finally:
            library_api.SOURCE_TIMEOUT = original

        self.assertEqual(len(d['results']), 1)
        self.assertLess(elapsed, 12, 'the fast source waited on the slow one')
        down = [s for s in d['sources'] if not s['ok']]
        self.assertIn('did not answer in time', down[0]['error'])

    def test_every_source_down_is_a_502_and_says_so(self):
        # Distinguishable from "no results": the client shows different words
        # because the two call for different next actions.
        with _SourcePatch({'gutenberg': _boom(), 'internet_archive': _boom()}):
            r = _app().get('/api/library/search?q=euclid')
        self.assertEqual(r.status_code, 502)
        d = r.get_json()
        self.assertTrue(d['all_sources_failed'])
        self.assertIn('No source answered', d['error'])
        self.assertEqual(d['results'], [])

    def test_no_matches_is_not_reported_as_a_failure(self):
        with _SourcePatch({'gutenberg': _ok('gutenberg', []),
                           'wikibooks': _ok('wikibooks', [])}):
            r = _app().get('/api/library/search?q=zzzznothing')
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertFalse(d['all_sources_failed'])
        self.assertEqual(d['results'], [])
        self.assertNotIn('error', d)

    def test_every_result_says_which_source_it_came_from(self):
        with _SourcePatch({'gutenberg': _ok('gutenberg', ['A']),
                           'internet_archive': _ok('internet_archive', ['B'])}):
            d = _app().get('/api/library/search?q=x').get_json()
        for row in d['results']:
            self.assertIn(row['source'], library_api.SOURCE_LABELS)
            self.assertTrue(row['source_label'])

    def test_a_query_is_required(self):
        self.assertEqual(_app().get('/api/library/search').status_code, 400)


class TestSearchFilters(unittest.TestCase):

    def test_full_text_filter_hides_what_helga_cannot_read(self):
        with _SourcePatch({
            'gutenberg': _ok('gutenberg', ['Readable'], availability=FULL_TEXT),
            'wikibooks': _ok('wikibooks', ['Stub'], availability=METADATA),
        }):
            d = _app().get(
                '/api/library/search?q=x&availability=full_text').get_json()
        self.assertEqual([r['title'] for r in d['results']], ['Readable'])

    def test_full_text_filter_keeps_the_not_yet_checked(self):
        # `unknown` has not been ruled out. Excluding it here would silently
        # hide most of the Archive, the largest source on the page.
        with _SourcePatch({'internet_archive':
                           _ok('internet_archive', ['Unchecked'],
                               availability=UNKNOWN)}):
            d = _app().get(
                '/api/library/search?q=x&availability=full_text').get_json()
        self.assertEqual(len(d['results']), 1)

    def test_everything_shows_the_lending_only_titles_too(self):
        with _SourcePatch({'internet_archive':
                           _ok('internet_archive', ['Borrow me'],
                               availability=RESTRICTED)}):
            d = _app().get(
                '/api/library/search?q=x&availability=any').get_json()
        self.assertEqual(d['results'][0]['availability'], RESTRICTED)

    def test_open_licence_filter(self):
        with _SourcePatch({
            'gutenberg': _ok('gutenberg', ['Free'], open_license=True),
            'internet_archive': _ok('internet_archive', ['Unclear'],
                                    open_license=False),
        }):
            d = _app().get(
                '/api/library/search?q=x&open_only=1').get_json()
        self.assertEqual([r['title'] for r in d['results']], ['Free'])

    def test_source_selection_only_queries_what_was_asked_for(self):
        called = []

        def spy(name):
            def go(q, page, limit):
                called.append(name)
                return ([], 0)
            return go

        with _SourcePatch({'gutenberg': spy('gutenberg'),
                           'wikibooks': spy('wikibooks')}):
            _app().get('/api/library/search?q=x&sources=gutenberg')
        self.assertEqual(called, ['gutenberg'])

    def test_title_sort_is_applied(self):
        with _SourcePatch({'gutenberg': _ok('gutenberg', ['Zeta', 'Alpha'])}):
            d = _app().get('/api/library/search?q=x&sort=title').get_json()
        self.assertEqual([r['title'] for r in d['results']], ['Alpha', 'Zeta'])


# ---------------------------------------------------------------------------
# Covers and the blank fallback
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status=200, ctype='image/jpeg', body=b'\xff\xd8jpegbytes'):
        self.status_code = status
        self.headers = {'Content-Type': ctype}
        self._body = body
        self.raw = self

    def read(self, *a, **kw):
        return self._body


class TestCovers(unittest.TestCase):
    """The blank must appear wherever a cover cannot, and it must be an IMAGE.

    The user asked for a blank book rather than a broken image or a hidden
    card, so every failure path below has to end in 200 + an image body. An
    error status would put a broken-image glyph in the grid, which is the
    outcome this whole path exists to avoid.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='helga_cover_test_')
        self._old_root = os.environ.get('DATA_ROOT')
        os.environ['DATA_ROOT'] = self.tmp
        self._old_get = library_api._get
        self._old_upstream = library_api._upstream_cover_url
        self.client = _app()

    def tearDown(self):
        library_api._get = self._old_get
        library_api._upstream_cover_url = self._old_upstream
        if self._old_root is None:
            os.environ.pop('DATA_ROOT', None)
        else:
            os.environ['DATA_ROOT'] = self._old_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _assert_is_blank(self, r, reason=None):
        self.assertEqual(r.status_code, 200, 'a blank is served, never an error')
        self.assertIn('image/svg+xml', r.headers['Content-Type'])
        self.assertIn(b'<svg', r.data)
        if reason:
            self.assertEqual(r.headers.get('X-Cover-Fallback'), reason)

    def test_the_blank_asset_exists_and_is_local(self):
        # Generated locally on purpose: fetching a placeholder from the network
        # would fail in exactly the weather that produced the missing cover.
        path = library_api._blank_path()
        self.assertTrue(os.path.exists(path), path)
        with open(path, 'rb') as fh:
            self.assertIn(b'<svg', fh.read(200))

    def test_no_cover_url_yields_the_blank(self):
        library_api._upstream_cover_url = lambda s, i: None
        r = self.client.get('/api/library/cover?source=gutenberg&id=1')
        self._assert_is_blank(r, 'no-cover-url')

    def test_the_archive_placeholder_is_detected_by_digest_not_status(self):
        """MEASURED: archive.org returns HTTP 200 with a byte-identical
        2212-byte PNG for an identifier that does not exist. Status code
        cannot tell us anything here, so the digest has to."""
        placeholder = b'\x89PNG' + b'\x00' * (
            library_api._IA_PLACEHOLDER_LEN - 4)
        library_api._upstream_cover_url = lambda s, i: 'https://example/img'
        library_api._get = lambda url, **kw: _FakeResponse(
            200, 'image/png', placeholder)
        library_api._IA_PLACEHOLDER_MD5 = hashlib.md5(placeholder).hexdigest()

        r = self.client.get(
            '/api/library/cover?source=internet_archive&id=nope')
        self._assert_is_blank(r, 'ia-placeholder')

    def test_an_html_error_page_served_at_200_is_not_treated_as_a_cover(self):
        library_api._upstream_cover_url = lambda s, i: 'https://example/img'
        library_api._get = lambda url, **kw: _FakeResponse(
            200, 'text/html', b'<html>not found</html>')
        r = self.client.get('/api/library/cover?source=gutenberg&id=1')
        self._assert_is_blank(r, 'not-an-image')

    def test_upstream_404_yields_the_blank(self):
        library_api._upstream_cover_url = lambda s, i: 'https://example/img'
        library_api._get = lambda url, **kw: _FakeResponse(404, 'text/html', b'')
        r = self.client.get('/api/library/cover?source=openstax&id=x')
        self._assert_is_blank(r, 'upstream-404')

    def test_a_network_failure_yields_the_blank_and_is_not_cached_as_a_miss(self):
        """The network being down is not the book being coverless, and caching
        the two the same way would leave a permanently blank cover behind one
        bad afternoon."""
        def explode(url, **kw):
            raise OSError('network unreachable')

        library_api._upstream_cover_url = lambda s, i: 'https://example/img'
        library_api._get = explode
        r = self.client.get('/api/library/cover?source=gutenberg&id=7')
        self._assert_is_blank(r, 'fetch-failed')

        misses = [f for f in os.listdir(
            library_api._cover_cache_dir()) if f.endswith('.miss')]
        self.assertEqual(misses, [], 'a transient outage was cached as "no cover"')

    def test_a_real_cover_is_proxied_and_then_served_from_disk(self):
        calls = []

        def once(url, **kw):
            calls.append(url)
            return _FakeResponse(200, 'image/jpeg', b'\xff\xd8realjpeg')

        library_api._upstream_cover_url = lambda s, i: 'https://example/img'
        library_api._get = once

        first = self.client.get('/api/library/cover?source=gutenberg&id=42')
        self.assertEqual(first.headers.get('X-Cover-Cache'), 'miss')
        self.assertEqual(first.data, b'\xff\xd8realjpeg')

        second = self.client.get('/api/library/cover?source=gutenberg&id=42')
        self.assertEqual(second.headers.get('X-Cover-Cache'), 'hit')
        self.assertEqual(second.data, b'\xff\xd8realjpeg')
        # Cached on disk so repeat searches are instant AND the cover still
        # renders after the appliance goes offline.
        self.assertEqual(len(calls), 1, 'the second request hit the network')

    def test_a_known_missing_cover_is_only_looked_up_once(self):
        calls = []

        def lookup(source, ident):
            calls.append(ident)
            return None

        library_api._upstream_cover_url = lookup
        self.client.get('/api/library/cover?source=wikibooks&id=Nothing')
        r = self.client.get('/api/library/cover?source=wikibooks&id=Nothing')
        self._assert_is_blank(r, 'cached-miss')
        self.assertEqual(len(calls), 1)

    def test_a_bad_request_still_returns_an_image(self):
        # Even a malformed request renders as a blank book rather than a
        # broken-image glyph sitting in the middle of the grid.
        self._assert_is_blank(
            self.client.get('/api/library/cover?source=nope&id='),
            'bad-request')


# ---------------------------------------------------------------------------
# Availability: the three-way distinction must survive
# ---------------------------------------------------------------------------

class TestAvailabilityStates(unittest.TestCase):

    def test_the_three_states_are_distinct_values(self):
        self.assertEqual(len({FULL_TEXT, RESTRICTED, METADATA}), 3)

    def test_only_full_text_is_buildable(self):
        for state in (RESTRICTED, METADATA, UNKNOWN):
            row = _result('internet_archive', 'x', 'T', availability=state)
            self.assertFalse(row['can_build'],
                             f'{state} must not offer a Build button')
        self.assertTrue(
            _result('gutenberg', 'x', 'T', availability=FULL_TEXT)['can_build'])

    def test_a_lending_only_archive_item_is_restricted_with_a_reason(self):
        library_api._get = lambda url, **kw: _MetaResponse({
            'metadata': {'access-restricted-item': 'true'},
            'files': [{'name': 'scan_djvu.txt'}]})
        try:
            d = _app().get('/api/library/availability'
                           '?source=internet_archive&id=x').get_json()
        finally:
            library_api._get = _RESTORE_GET
        self.assertEqual(d['availability'], RESTRICTED)
        self.assertFalse(d['can_build'])
        # The reason is the whole point: a learner told "lending only" makes a
        # different choice than one handed a course built from a blurb.
        self.assertIn('lending-only', d['reason'])

    def test_a_catalogue_only_item_is_not_reported_as_lending_only(self):
        library_api._get = lambda url, **kw: _MetaResponse(
            {'metadata': {}, 'files': [{'name': 'cover.jpg'}]})
        try:
            d = _app().get('/api/library/availability'
                           '?source=internet_archive&id=x').get_json()
        finally:
            library_api._get = _RESTORE_GET
        self.assertEqual(d['availability'], METADATA)
        self.assertFalse(d['can_build'])

    def test_a_readable_scan_is_full_text(self):
        library_api._get = lambda url, **kw: _MetaResponse(
            {'metadata': {}, 'files': [{'name': 'book_djvu.txt'}]})
        try:
            d = _app().get('/api/library/availability'
                           '?source=internet_archive&id=x').get_json()
        finally:
            library_api._get = _RESTORE_GET
        self.assertEqual(d['availability'], FULL_TEXT)
        self.assertTrue(d['can_build'])

    def test_an_unreachable_archive_is_unknown_not_full_text(self):
        def explode(url, **kw):
            raise OSError('down')
        library_api._get = explode
        try:
            r = _app().get('/api/library/availability'
                           '?source=internet_archive&id=x')
        finally:
            library_api._get = _RESTORE_GET
        self.assertEqual(r.status_code, 502)
        d = r.get_json()
        self.assertEqual(d['availability'], UNKNOWN)
        self.assertFalse(d['can_build'])


class _MetaResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


_RESTORE_GET = library_api._get


class TestGutenbergFormats(unittest.TestCase):
    """Gutenberg guarantees public domain but NOT that every book has plain
    text -- several verified rows offered only page images and a PDF. The
    format map is the honest availability signal here, the same way the file
    list is for the Archive, so reading it wrong understates what Helga can
    teach from.
    """

    def test_plain_text_is_preferred_and_the_charset_is_ignored(self):
        url, label = library_api._pg_readable({
            'text/html': 'h', 'text/plain; charset=utf-8': 'p'})
        self.assertEqual((url, label), ('p', 'plain text'))

    def test_an_epub_only_book_is_readable(self):
        # Regression: an over-eager filter rejected every '+zip' subtype, so an
        # EPUB-only book was reported as catalogue-only -- while Helga's own
        # upload path accepts EPUB perfectly well.
        url, label = library_api._pg_readable({'application/epub+zip': 'e'})
        self.assertEqual((url, label), ('e', 'EPUB'))

    def test_page_images_alone_are_not_readable(self):
        url, label = library_api._pg_readable({
            'application/octet-stream': 'png', 'application/rdf+xml': 'r'})
        self.assertIsNone(url)
        self.assertIsNone(label)


class TestSourcesEndpoint(unittest.TestCase):

    def test_it_lists_every_wired_adapter_without_touching_the_network(self):
        d = _app().get('/api/library/sources').get_json()
        ids = {s['id'] for s in d['sources']}
        self.assertEqual(ids, set(library_api.SOURCES))
        for s in d['sources']:
            self.assertTrue(s['label'])


class TestUserAgent(unittest.TestCase):
    """MEASURED: a generic User-Agent got HTTP 429 from Wikimedia on the very
    first request; a descriptive one succeeded immediately."""

    def test_the_user_agent_identifies_this_application(self):
        self.assertIn('Helga', library_api.USER_AGENT)
        self.assertTrue(len(library_api.USER_AGENT) > 20)

    def test_no_fake_contact_address_is_claimed(self):
        # ratelimit.py makes the same point about the Crossref polite pool: a
        # contact that cannot be contacted is worse than no claim at all.
        self.assertNotIn('noreply@localhost', library_api.USER_AGENT)


if __name__ == '__main__':
    unittest.main()
