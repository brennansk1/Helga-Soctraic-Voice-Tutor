# REGISTER WITH:  app.register_blueprint(__import__('library_api').library_api)
"""Multi-source public-book search for /library, with proxied cover art.

WHY A SEPARATE NAMESPACE
------------------------
Everything here lives under /api/library/* rather than extending the existing
/api/books/* handlers in app.py. Two rules on one URL is not an error in Flask,
it is a silent shadowing: whichever blueprint registered first wins, and the
other handler simply never runs again. That failure has no log line and no test
that catches it. A distinct namespace means registering this file cannot change
the behaviour of anything already shipped, and /api/books/build keeps its
current client contract untouched.

THE THING THIS MODULE MUST NOT LOSE
-----------------------------------
Availability is THREE different answers, not one: the full text is readable, the
book is lending-only, or all we have is a catalogue record. A learner who picks
a borrow-only book and receives a course generated from a 200-word blurb has
been misled by the interface, not by the model -- every structural check we run
would pass that course. Each source below therefore has to answer the question
in its own terms, and a source that cannot answer says `unknown` rather than
guessing `full_text`.

SOURCES, AND WHAT WAS ACTUALLY MEASURED
---------------------------------------
This project has been burned three times by an API that is documented, popular,
and broken. Every endpoint below was exercised live before it was written into
this file; the measurements are recorded at each source's adapter because they
are the reason the code has the shape it has.

  * Internet Archive advancedsearch.php -- works, ~0.2s, exposes numFound so
    real pagination is possible. Availability costs a second metadata call, so
    it is resolved lazily rather than for every row of every search.
  * Gutendex (Project Gutenberg) -- works, but ONLY with the trailing slash on
    /books/. Without it the request 301s and the measured round trip was 10.4s
    against 0.06s with it. That is the difference between a fast source and one
    that trips its own timeout.
  * Wikibooks / Wikiversity Action API -- works at ~0.2s, but a generic
    User-Agent was answered with HTTP 429 on the first request. Wikimedia asks
    automated clients for a descriptive UA with contact info; sending one made
    the same query succeed immediately.
  * OpenStax -- the Wagtail CMS API at openstax.org/apps/cms/api/v2/pages is
    keyless, supports ?search=, and returns cover_url, license and pdf_url.
    Verified live; 129 books, ~0.2s.

  NOT USED: Open Library's search.json. openlibrary.org answers in 0.18s but the
  search endpoint did not respond within 45s across repeated attempts, which is
  already recorded in services/research/domain_sources.py. covers.openlibrary.org
  works and correctly 404s, but it needs an ISBN that Archive rows rarely carry.

WHY COVERS ARE PROXIED RATHER THAN HOTLINKED
--------------------------------------------
Helga is an offline-first appliance and this search already runs server-side. If
the browser fetched cover art directly, every search would newly hand
archive.org, gutenberg.org and Wikimedia the learner's IP address and a list of
what they are reading -- a disclosure the current design does not make. Proxying
keeps that on the server, and caching the bytes to disk means the second search
is instant and covers still render after the machine goes offline.
"""

import hashlib
import html
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

import requests
from flask import Blueprint, jsonify, request, send_file, make_response

logger = logging.getLogger(__name__)

library_api = Blueprint('library_api', __name__)

# Wikimedia's UA policy asks for contact info and answered a generic UA with 429
# (measured). A fake address is worse than none -- ratelimit.py makes the same
# point about the Crossref polite pool -- so the contact clause is included only
# when HELGA_CONTACT is really set.
_CONTACT = os.environ.get('HELGA_CONTACT', '').strip()
USER_AGENT = (
    'Helga/1.0 (offline learning appliance; '
    + (f'contact {_CONTACT})' if _CONTACT else '+https://github.com/helga-tutor)')
)
_HEADERS = {'User-Agent': USER_AGENT}

# Per-source wall clock. A source is allowed to be slow; it is not allowed to
# hold the whole result set hostage, so this is enforced on the future as well
# as on the socket.
SOURCE_TIMEOUT = float(os.environ.get('LIBRARY_SOURCE_TIMEOUT', '9'))
COVER_TIMEOUT = 8
DEFAULT_PER_SOURCE = 12
MAX_PER_SOURCE = 30

# ---------------------------------------------------------------------------
# Throttling
#
# services/research/ratelimit.py already records these numbers, but it lives in
# a different service and is not importable from the web-ui container. Rather
# than add a cross-service dependency for twenty lines, the same intervals are
# restated here with the same provenance: archive.org is undocumented and
# observed at 1.0 req/s, Wikimedia publishes no anonymous cap but asks for
# serial requests, and the rest are deliberate conservatism.
# ---------------------------------------------------------------------------
_MIN_INTERVAL = {
    'archive.org': 1.0,
    'en.wikibooks.org': 0.2,
    'en.wikiversity.org': 0.2,
    'gutendex.com': 0.2,
    'www.gutenberg.org': 0.5,
    'openstax.org': 0.2,
    'assets.openstax.org': 0.2,
    'upload.wikimedia.org': 0.2,
}
_last_call = {}
_throttle_lock = threading.Lock()


def _throttle(host):
    """Space out calls to one host. Held across threads, which is the point:
    the search fans out concurrently and would otherwise burst one host."""
    interval = _MIN_INTERVAL.get(host)
    if not interval:
        return
    with _throttle_lock:
        wait = interval - (time.monotonic() - _last_call.get(host, 0.0))
        if wait > 0:
            time.sleep(min(wait, interval))
        _last_call[host] = time.monotonic()


def _get(url, **kw):
    host = re.sub(r'^https?://([^/]+).*$', r'\1', url)
    _throttle(host)
    kw.setdefault('headers', _HEADERS)
    kw.setdefault('timeout', SOURCE_TIMEOUT)
    return requests.get(url, **kw)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

FULL_TEXT, RESTRICTED, METADATA, UNKNOWN = (
    'full_text', 'restricted', 'metadata', 'unknown')

# Ranked by USEFULNESS TO THE LEARNER, which is not the same as certainty.
# `unknown` sits above both refusals on purpose: an Archive row we have not
# checked yet may well turn out to be full text, whereas lending-only and
# catalogue-only are already settled answers of "no". Ranking certainty instead
# would let a known-useless record outrank the one good copy of a book.
_AVAIL_RANK = {METADATA: 0, RESTRICTED: 1, UNKNOWN: 2, FULL_TEXT: 3}


def _one(v):
    """Archive fields arrive as either a scalar or a one-element list."""
    return v[0] if isinstance(v, list) and v else v


def _strip_tags(s):
    """Wiki snippets and OpenStax descriptions are HTML fragments. They end up
    in textContent on the client, so tags would be shown literally rather than
    rendered -- strip them here where the source is known."""
    if not s:
        return ''
    return html.unescape(re.sub(r'<[^>]+>', '', str(s))).strip()


def _work_key(title, author=''):
    """Normalised identity for one WORK, not one scan.

    The Archive alone holds a dozen scans of Euclid, and Gutenberg holds the
    same text again. Punctuation, leading articles and subtitles after a colon
    are all editorial noise on the same book, so they come off. The author's
    surname is included when known because 'Elements' alone collides across
    genuinely different books.
    """
    t = str(title or '').lower()
    t = t.split(':')[0]
    t = re.sub(r'^(the|a|an)\s+', '', t.strip())
    t = re.sub(r'[^a-z0-9]+', '', t)[:60]
    a = re.sub(r'[^a-z0-9]+', '', str(author or '').lower().split(',')[0])[:20]
    return f'{t}|{a}' if t else ''


def _result(source, ident, title, **kw):
    r = {
        'source': source,
        'source_label': SOURCE_LABELS.get(source, source),
        'id': str(ident),
        'title': str(title)[:200],
        'author': str(kw.get('author') or '')[:140],
        'year': kw.get('year'),
        'open_license': bool(kw.get('open_license')),
        'license': kw.get('license') or '',
        'availability': kw.get('availability', UNKNOWN),
        'availability_reason': kw.get('availability_reason', ''),
        'can_build': kw.get('availability') == FULL_TEXT,
        'description': _strip_tags(kw.get('description'))[:400],
        'has_cover': bool(kw.get('has_cover', True)),
        'also_in': [],
        'popularity': int(kw.get('popularity') or 0),
    }
    return r


SOURCE_LABELS = {
    'internet_archive': 'Internet Archive',
    'gutenberg': 'Project Gutenberg',
    'wikibooks': 'Wikibooks',
    'wikiversity': 'Wikiversity',
    'openstax': 'OpenStax',
}


# ---------------------------------------------------------------------------
# Source: Internet Archive
# ---------------------------------------------------------------------------

def _search_internet_archive(q, page, limit):
    """Scanned texts. Measured ~0.2s; numFound gives us honest pagination.

    Availability is deliberately left `unknown` here. Deciding it requires a
    /metadata call per identifier, and doing twelve of those inside the search
    would turn a 0.2s source into a 12s one. The client resolves each row
    against /api/library/availability once the card is on screen, which is the
    same bargain the original handler struck.
    """
    r = _get('https://archive.org/advancedsearch.php', params={
        'q': f'title:({q}) AND mediatype:texts',
        'fl[]': ['identifier', 'title', 'creator', 'year', 'licenseurl',
                 'downloads'],
        'rows': limit, 'page': page, 'output': 'json'})
    r.raise_for_status()
    body = (r.json().get('response') or {})
    out = []
    for d in body.get('docs', []):
        ident, title = d.get('identifier'), _one(d.get('title'))
        if not ident or not title:
            continue
        out.append(_result(
            'internet_archive', ident, title,
            author=_one(d.get('creator')),
            year=d.get('year'),
            open_license=bool(d.get('licenseurl')),
            license=_one(d.get('licenseurl')) or '',
            availability=UNKNOWN,
            popularity=d.get('downloads')))
    return out, int(body.get('numFound') or 0)


def _availability_internet_archive(ident):
    r = _get(f'https://archive.org/metadata/{ident}', timeout=SOURCE_TIMEOUT)
    meta = r.json()
    md = meta.get('metadata') or {}
    restricted = str(md.get('access-restricted-item', '')).lower() == 'true'
    files = [f.get('name', '') for f in (meta.get('files') or [])]
    fulltext = [f for f in files if f.endswith('_djvu.txt')] or \
               [f for f in files if f.endswith('.txt')]
    if restricted or not fulltext:
        return {
            'availability': RESTRICTED if restricted else METADATA,
            'can_build': False,
            'reason': ('This book is lending-only, so its full text cannot be '
                       'read. Helga will not build a course from a catalogue '
                       'description.') if restricted else
                      ('This record has no readable text file — only catalogue '
                       'metadata. There is nothing here to build a course from.'),
        }
    return {'availability': FULL_TEXT, 'can_build': True,
            'text_file': fulltext[0],
            'reason': 'The full scanned text is readable.'}


def _detail_internet_archive(ident):
    r = _get(f'https://archive.org/metadata/{ident}')
    meta = r.json()
    md = meta.get('metadata') or {}
    files = meta.get('files') or []
    avail = _availability_internet_archive(ident)
    subjects = md.get('subject') or []
    if isinstance(subjects, str):
        subjects = [s.strip() for s in subjects.split(';') if s.strip()]
    formats = sorted({f.get('format') for f in files if f.get('format')})
    excerpt, excerpt_note = '', ''
    if avail.get('text_file'):
        # The first page of the real scan is the fastest way to tell clean OCR
        # from a garbled one, and OCR quality is what decides whether the
        # resulting course is worth anything. A Range request keeps this to a
        # few KB instead of pulling a 40 MB text file.
        excerpt, excerpt_note = _range_excerpt(
            f'https://archive.org/download/{ident}/{avail["text_file"]}')
    else:
        excerpt_note = 'No readable text file, so there is nothing to preview.'
    return {
        'source': 'internet_archive', 'id': ident,
        'title': str(_one(md.get('title')) or ident),
        'author': str(_one(md.get('creator')) or ''),
        'year': md.get('year') or md.get('date'),
        'description': _strip_tags(_one(md.get('description')))[:2000],
        'subjects': [str(s)[:60] for s in subjects][:12],
        'formats': formats[:12],
        'license': _one(md.get('licenseurl')) or '',
        'pages': md.get('imagecount'),
        'availability': avail['availability'],
        'availability_reason': avail.get('reason', ''),
        'can_build': avail['can_build'],
        'excerpt': excerpt, 'excerpt_note': excerpt_note,
        'url': f'https://archive.org/details/{ident}',
    }


# ---------------------------------------------------------------------------
# Source: Project Gutenberg, via Gutendex
# ---------------------------------------------------------------------------

# MEASURED: https://gutendex.com/books?search=... 301s to /books/ and the round
# trip took 10.4s; with the trailing slash it was 1.0s cold and 0.06s warm. The
# slash is not cosmetic, it is the difference between this source answering and
# this source timing out.
GUTENDEX = 'https://gutendex.com/books/'

# Formats Helga's ingest can actually read, best first. Gutenberg guarantees
# public domain but NOT that every book has plain text -- several verified rows
# offered only page-image PNGs and a PDF -- so the format map is the honest
# availability signal here, the same way the file list is for the Archive.
_PG_READABLE = [
    ('text/plain; charset=utf-8', 'plain text'),
    ('text/plain; charset=us-ascii', 'plain text'),
    ('text/plain', 'plain text'),
    ('text/html', 'HTML'),
    ('application/epub+zip', 'EPUB'),
    ('application/pdf', 'PDF'),
]


def _pg_readable(formats):
    """First readable format Gutenberg offers, best first.

    startswith, because Gutendex spells plain text as
    'text/plain; charset=utf-8' and the charset is not part of the decision.
    """
    for key, label in _PG_READABLE:
        for have in formats:
            if have.startswith(key):
                return formats[have], label
    return None, None


def _pg_cover_url(formats):
    for k, v in (formats or {}).items():
        if k.startswith('image/'):
            return v
    return None


def _search_gutenberg(q, page, limit):
    r = _get(GUTENDEX, params={'search': q, 'page': page})
    r.raise_for_status()
    body = r.json()
    out = []
    for b in (body.get('results') or [])[:limit]:
        formats = b.get('formats') or {}
        url, label = _pg_readable(formats)
        authors = ', '.join(a.get('name', '') for a in (b.get('authors') or []))
        death = next((a.get('death_year') for a in (b.get('authors') or [])
                      if a.get('death_year')), None)
        out.append(_result(
            'gutenberg', b.get('id'), b.get('title') or '',
            author=authors,
            year=death,
            # `copyright: false` is Gutenberg stating the work is public domain.
            # None means they could not determine it, which is not the same as
            # yes and must not be reported as one.
            open_license=(b.get('copyright') is False),
            license='Public domain (Project Gutenberg)'
                    if b.get('copyright') is False else '',
            availability=FULL_TEXT if url else METADATA,
            availability_reason=(f'Full {label} is downloadable.' if url else
                                 'Gutenberg lists no readable text format for '
                                 'this record — only page images.'),
            description='; '.join((b.get('subjects') or [])[:4]),
            has_cover=bool(_pg_cover_url(formats)),
            popularity=b.get('download_count')))
    return out, int(body.get('count') or 0)


def _fetch_gutenberg_book(book_id):
    r = _get(f'{GUTENDEX}{int(book_id)}')
    r.raise_for_status()
    return r.json()


def _detail_gutenberg(book_id):
    b = _fetch_gutenberg_book(book_id)
    formats = b.get('formats') or {}
    url, label = _pg_readable(formats)
    excerpt, note = ('', 'Gutenberg has no readable text format for this book.')
    if url and label in ('plain text',):
        excerpt, note = _range_excerpt(url)
    elif url:
        note = (f'The text is available as {label}, which cannot be previewed '
                f'inline — Helga reads it at build time.')
    summaries = b.get('summaries') or []
    return {
        'source': 'gutenberg', 'id': str(b.get('id')),
        'title': b.get('title') or '',
        'author': ', '.join(a.get('name', '') for a in (b.get('authors') or [])),
        'year': next((a.get('death_year') for a in (b.get('authors') or [])
                      if a.get('death_year')), None),
        'description': _strip_tags(summaries[0] if summaries else '')[:2000],
        'subjects': (b.get('subjects') or [])[:12],
        'formats': sorted({k.split(';')[0] for k in formats})[:12],
        'license': 'Public domain (Project Gutenberg)'
                   if b.get('copyright') is False else '',
        'pages': None,
        'availability': FULL_TEXT if url else METADATA,
        'availability_reason': (f'Full {label} is downloadable.' if url
                                else 'No readable text format.'),
        'can_build': bool(url),
        'excerpt': excerpt, 'excerpt_note': note,
        'url': f'https://www.gutenberg.org/ebooks/{b.get("id")}',
        'downloads': b.get('download_count'),
    }


# ---------------------------------------------------------------------------
# Sources: Wikibooks and Wikiversity
# ---------------------------------------------------------------------------
#
# These are open TEXTBOOKS, which is the shape a course actually wants: a book
# already broken into a chapter tree by people teaching the subject.
#
# One call per source, using list=search. prop=extracts was tried with
# generator=search and returned an extract for only the FIRST page (the API caps
# exlimit at 1 alongside exchars), so the per-row description here is the search
# snippet, which is always present. Cover art is resolved on demand by the cover
# endpoint instead, where it is cached and costs nothing on a repeat search.

_WIKI_HOSTS = {'wikibooks': 'en.wikibooks.org',
               'wikiversity': 'en.wikiversity.org'}


def _search_wiki(kind):
    host = _WIKI_HOSTS[kind]

    def go(q, page, limit):
        r = _get(f'https://{host}/w/api.php', params={
            'action': 'query', 'list': 'search', 'srsearch': q,
            'srlimit': limit, 'srnamespace': 0,
            'sroffset': (page - 1) * limit,
            'format': 'json', 'formatversion': 2})
        r.raise_for_status()
        body = r.json().get('query') or {}
        out = []
        for p in body.get('search', []):
            words = p.get('wordcount') or 0
            # A stub of a few dozen words is a catalogue entry with a title, not
            # a textbook. Saying "full text" about it would be the same lie the
            # borrow-only case is guarding against, so it is METADATA.
            thin = words < 300
            out.append(_result(
                kind, p.get('title'), p.get('title') or '',
                author=SOURCE_LABELS[kind] + ' contributors',
                year=(p.get('timestamp') or '')[:4] or None,
                open_license=True,
                license='CC BY-SA 4.0',
                availability=METADATA if thin else FULL_TEXT,
                availability_reason=(
                    f'Only {words} words — this page is a stub or an index, '
                    'not enough to teach from.' if thin else
                    f'{words:,} words of open textbook, readable in full.'),
                description=_strip_tags(p.get('snippet')),
                # Deliberately NO popularity. The "most read" sort compares
                # download counts, and a wiki page has none -- feeding it
                # wordcount instead would rank a long stub above a genuinely
                # popular book while looking like a measurement. Word count
                # decides availability above, which is the one thing it can
                # honestly answer.
                popularity=0))
        return out, int(((body.get('searchinfo') or {}).get('totalhits')) or 0)
    return go


def _detail_wiki(kind, title):
    host = _WIKI_HOSTS[kind]
    r = _get(f'https://{host}/w/api.php', params={
        'action': 'query', 'prop': 'extracts|info|categories',
        'explaintext': 1, 'exchars': 1500, 'inprop': 'url',
        'cllimit': 12, 'titles': title,
        'format': 'json', 'formatversion': 2})
    r.raise_for_status()
    pages = ((r.json().get('query') or {}).get('pages') or [])
    if not pages or pages[0].get('missing'):
        raise LookupError(f'{SOURCE_LABELS[kind]} has no page called "{title}".')
    p = pages[0]
    extract = (p.get('extract') or '').strip()
    cats = [c.get('title', '').replace('Category:', '')
            for c in (p.get('categories') or [])]
    return {
        'source': kind, 'id': title, 'title': p.get('title') or title,
        'author': SOURCE_LABELS[kind] + ' contributors',
        'year': None,
        'description': extract[:2000],
        'subjects': cats[:12],
        'formats': ['wikitext', 'HTML'],
        'license': 'CC BY-SA 4.0',
        'pages': None,
        'availability': FULL_TEXT if extract else METADATA,
        'availability_reason': ('Open textbook, readable in full.' if extract
                                else 'This page is an index with no prose of '
                                     'its own.'),
        'can_build': bool(extract),
        'excerpt': extract[:1800],
        'excerpt_note': '' if extract else
                        'This page carries no body text — it is a table of '
                        'contents pointing at sub-pages.',
        'url': p.get('fullurl') or f'https://{host}/wiki/{title}',
    }


# ---------------------------------------------------------------------------
# Source: OpenStax
# ---------------------------------------------------------------------------
#
# VERIFIED live and keyless: the Wagtail CMS API supports ?search=, filters
# total_count to the match set, and exposes cover_url, license_name/url and
# pdf_url. These are peer-reviewed, openly licensed textbooks -- the highest
# quality per byte of anything on this page -- so they are worth the adapter.
#
# Availability is FULL_TEXT when pdf_url is set, and only then: a CMS record
# with no PDF is a landing page, and calling that "full text" is exactly the
# mistake this module exists to prevent.

OPENSTAX = 'https://openstax.org/apps/cms/api/v2/pages/'
_OX_FIELDS = ('title,cover_url,description,license_name,license_url,'
              'pdf_url,webview_rex_link,book_subjects,publish_date')


def _search_openstax(q, page, limit):
    r = _get(OPENSTAX, params={
        'type': 'books.Book', 'fields': _OX_FIELDS, 'search': q,
        'limit': limit, 'offset': (page - 1) * limit})
    r.raise_for_status()
    body = r.json()
    out = []
    for it in body.get('items', []):
        meta = it.get('meta') or {}
        pdf = it.get('pdf_url')
        subjects = ', '.join(s.get('subject_name', '')
                             for s in (it.get('book_subjects') or []))
        lic = it.get('license_name') or ''
        out.append(_result(
            'openstax', meta.get('slug') or it.get('id'), it.get('title') or '',
            author='OpenStax',
            year=(it.get('publish_date') or
                  meta.get('first_published_at') or '')[:4] or None,
            open_license=bool(lic),
            license=lic,
            availability=FULL_TEXT if pdf else METADATA,
            availability_reason=('The complete textbook PDF is downloadable.'
                                 if pdf else
                                 'OpenStax lists this book but publishes no '
                                 'downloadable PDF for it.'),
            description=subjects or _strip_tags(it.get('description')),
            has_cover=bool(it.get('cover_url'))))
    return out, int(((body.get('meta') or {}).get('total_count')) or 0)


def _openstax_record(slug):
    """OpenStax ids in our results are slugs, which the API cannot filter on
    directly; a bounded slug search is how we get back to the record."""
    r = _get(OPENSTAX, params={'type': 'books.Book', 'fields': _OX_FIELDS,
                               'slug': slug, 'limit': 2})
    r.raise_for_status()
    items = r.json().get('items') or []
    if not items:
        raise LookupError(f'OpenStax has no book with the slug "{slug}".')
    return items[0]


def _detail_openstax(slug):
    it = _openstax_record(slug)
    meta = it.get('meta') or {}
    pdf = it.get('pdf_url')
    return {
        'source': 'openstax', 'id': slug, 'title': it.get('title') or slug,
        'author': 'OpenStax',
        'year': (it.get('publish_date') or
                 meta.get('first_published_at') or '')[:4] or None,
        'description': _strip_tags(it.get('description'))[:2000],
        'subjects': [s.get('subject_name', '')
                     for s in (it.get('book_subjects') or [])][:12],
        'formats': ['PDF', 'Web'] if pdf else ['Web'],
        'license': it.get('license_name') or '',
        'pages': None,
        'availability': FULL_TEXT if pdf else METADATA,
        'availability_reason': ('The complete textbook PDF is downloadable.'
                                if pdf else 'No downloadable PDF.'),
        'can_build': bool(pdf),
        'excerpt': '',
        # Named, not blank: the user should know WHY there is no preview here
        # rather than wonder whether the request failed.
        'excerpt_note': ('OpenStax ships this book as a PDF, which cannot be '
                         'previewed inline. It is a peer-reviewed textbook '
                         'under ' + (it.get('license_name') or 'an open licence')
                         + '.') if pdf else 'No text to preview.',
        'url': it.get('webview_rex_link') or meta.get('html_url') or '',
    }


SOURCES = {
    'internet_archive': _search_internet_archive,
    'gutenberg': _search_gutenberg,
    'wikibooks': _search_wiki('wikibooks'),
    'wikiversity': _search_wiki('wikiversity'),
    'openstax': _search_openstax,
}


# ---------------------------------------------------------------------------
# Excerpts
# ---------------------------------------------------------------------------

def _range_excerpt(url, nbytes=4000):
    """Pull the opening of a text file without downloading the whole book.

    Verified 206 Partial Content on both archive.org/download and
    gutenberg.org/files. A server that ignores Range answers 200 with the whole
    body, so the slice is applied locally too rather than trusted.
    """
    try:
        r = _get(url, headers=dict(_HEADERS, Range=f'bytes=0-{nbytes}'),
                 timeout=SOURCE_TIMEOUT, stream=True)
        raw = r.raw.read(nbytes + 512, decode_content=True) or b''
        text = raw.decode('utf-8', 'replace')
    except Exception as e:
        logger.warning(f'excerpt fetch failed for {url}: {e}')
        return '', 'The excerpt could not be fetched just now.'
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if not text:
        return '', 'The source returned an empty excerpt.'
    return text[:nbytes], ''


# ---------------------------------------------------------------------------
# Merge, dedup, filter, sort
# ---------------------------------------------------------------------------

# When one work exists in several places, prefer the copy Helga can read most
# cleanly: Gutenberg's proofread text beats an OCR'd scan, and a curated
# textbook beats both. This only breaks ties AFTER availability, which always
# wins -- a readable scan is more use than an unreadable ebook.
_SOURCE_PREFERENCE = {'gutenberg': 5, 'openstax': 4, 'wikibooks': 3,
                      'wikiversity': 2, 'internet_archive': 1}


def _merge(groups):
    """Collapse the same work across sources, keeping the most useful copy.

    `unknown` availability is treated as better than `metadata` here on purpose:
    an Archive row we have not checked yet may well be full text, and dropping
    it in favour of a known-stub wiki page would hide the good copy. The card
    resolves it moments later and shows the truth either way.
    """
    merged = {}
    for r in groups:
        key = _work_key(r['title'], r['author'])
        if not key:
            continue
        cur = merged.get(key)
        if cur is None:
            merged[key] = r
            continue
        cand_score = (_AVAIL_RANK.get(r['availability'], 0),
                      _SOURCE_PREFERENCE.get(r['source'], 0))
        cur_score = (_AVAIL_RANK.get(cur['availability'], 0),
                     _SOURCE_PREFERENCE.get(cur['source'], 0))
        if cand_score > cur_score:
            r['also_in'] = cur['also_in'] + [
                {'source': cur['source'], 'source_label': cur['source_label'],
                 'id': cur['id']}]
            merged[key] = r
        else:
            cur['also_in'].append(
                {'source': r['source'], 'source_label': r['source_label'],
                 'id': r['id']})
    return list(merged.values())


def _apply_filters(rows, args):
    avail = (args.get('availability') or 'any').strip()
    if avail == 'full_text':
        # `unknown` survives this filter because it has not been ruled out yet.
        # Excluding it would silently hide most of the Archive, which is the
        # largest source on the page.
        rows = [r for r in rows if r['availability'] in (FULL_TEXT, UNKNOWN)]
    elif avail in (RESTRICTED, METADATA, UNKNOWN):
        rows = [r for r in rows if r['availability'] == avail]

    if (args.get('open_only') or '').lower() in ('1', 'true', 'yes'):
        rows = [r for r in rows if r['open_license']]

    def _year(r):
        try:
            return int(str(r.get('year'))[:4])
        except (TypeError, ValueError):
            return None
    for bound, cmp in (('year_min', lambda y, b: y >= b),
                       ('year_max', lambda y, b: y <= b)):
        raw = args.get(bound)
        if raw:
            try:
                b = int(raw)
            except ValueError:
                continue
            # A row with no year is kept: the Archive often omits it, and
            # dropping those would quietly discard good books.
            rows = [r for r in rows
                    if _year(r) is None or cmp(_year(r), b)]

    sort = (args.get('sort') or 'relevance').strip()
    if sort == 'year_desc':
        rows.sort(key=lambda r: _year(r) or -9999, reverse=True)
    elif sort == 'year_asc':
        rows.sort(key=lambda r: _year(r) or 9999)
    elif sort == 'title':
        rows.sort(key=lambda r: r['title'].lower())
    elif sort == 'popular':
        rows.sort(key=lambda r: r['popularity'], reverse=True)
    else:
        # Relevance: each source returned its own ranking, and interleaving
        # them keeps one prolific source from owning the top of the page.
        by_source = {}
        for r in rows:
            by_source.setdefault(r['source'], []).append(r)
        woven, i = [], 0
        while any(by_source.values()):
            for src in list(by_source):
                if by_source[src]:
                    woven.append(by_source[src].pop(0))
            i += 1
            if i > 200:
                break
        rows = woven
    return rows


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@library_api.route('/api/library/sources', methods=['GET'])
def library_sources():
    """What the filter UI offers. No network: this is a static description of
    the adapters that exist, so the filters render before any search runs."""
    return jsonify({'sources': [
        {'id': k, 'label': SOURCE_LABELS[k]} for k in SOURCES]})


@library_api.route('/api/library/search', methods=['GET'])
def library_search():
    """Search every source at once and say plainly what each one did.

    The sources are independent network calls, so they run concurrently -- done
    serially, five sources at up to nine seconds each is a search that feels
    broken. A slow or failing source degrades to a NAMED entry in `sources`
    rather than taking down the result set: "Gutenberg timed out" is a fact the
    user can act on, silently returning fewer books is not.
    """
    q = (request.args.get('q') or '').strip()
    if not q:
        return jsonify({'error': 'query required'}), 400

    try:
        page = max(1, int(request.args.get('page') or 1))
    except ValueError:
        page = 1
    try:
        limit = int(request.args.get('per_source') or DEFAULT_PER_SOURCE)
    except ValueError:
        limit = DEFAULT_PER_SOURCE
    limit = max(1, min(limit, MAX_PER_SOURCE))

    wanted = [s for s in (request.args.get('sources') or '').split(',')
              if s in SOURCES] or list(SOURCES)

    rows, status = [], []
    with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
        futures = {pool.submit(SOURCES[s], q, page, limit): s for s in wanted}
        for fut, name in futures.items():
            label = SOURCE_LABELS[name]
            try:
                got, total = fut.result(timeout=SOURCE_TIMEOUT + 3)
                rows.extend(got)
                status.append({'source': name, 'label': label, 'ok': True,
                               'count': len(got), 'total': total,
                               'has_more': total > page * limit})
            except FutureTimeout:
                status.append({'source': name, 'label': label, 'ok': False,
                               'count': 0, 'total': 0, 'has_more': False,
                               'error': f'{label} did not answer in time.'})
            except Exception as e:
                logger.warning(f'library source {name} failed: {e}')
                status.append({'source': name, 'label': label, 'ok': False,
                               'count': 0, 'total': 0, 'has_more': False,
                               'error': f'{label} is unavailable right now.'})

    raw_count = len(rows)
    rows = _apply_filters(_merge(rows), request.args)

    ok = [s for s in status if s['ok']]
    body = {
        'query': q, 'page': page, 'per_source': limit,
        'results': rows,
        'sources': status,
        'merged_from': raw_count,
        'has_more': any(s.get('has_more') for s in ok),
        # The client must be able to tell "nobody has this book" from "nothing
        # answered", because they call for different words on screen and a
        # different next action.
        'all_sources_failed': not ok,
    }
    if not ok:
        names = ', '.join(s['label'] for s in status)
        body['error'] = f'No source answered ({names}).'
        return jsonify(body), 502
    return jsonify(body)


@library_api.route('/api/library/availability', methods=['GET'])
def library_availability():
    """Can Helga actually READ this, or only see that it exists?

    Only the Archive needs a live call; the other adapters decide availability
    from data the search already returned, so those answers are echoed straight
    back and cost nothing.
    """
    source = (request.args.get('source') or 'internet_archive').strip()
    ident = (request.args.get('id') or '').strip()
    if not ident:
        return jsonify({'error': 'id required'}), 400
    if source not in SOURCES:
        return jsonify({'error': f'unknown source "{source}"'}), 400
    if source != 'internet_archive':
        return jsonify({'availability': UNKNOWN, 'can_build': False,
                        'reason': f'{SOURCE_LABELS[source]} reports '
                                  'availability with the search result itself.'
                        }), 200
    try:
        return jsonify(_availability_internet_archive(ident))
    except Exception as e:
        logger.warning(f'availability check failed for {ident}: {e}')
        return jsonify({'availability': UNKNOWN, 'can_build': False,
                        'reason': 'Could not reach the Internet Archive to '
                                  'check this book.'}), 502


_DETAILS = {
    'internet_archive': _detail_internet_archive,
    'gutenberg': _detail_gutenberg,
    'openstax': _detail_openstax,
    'wikibooks': lambda i: _detail_wiki('wikibooks', i),
    'wikiversity': lambda i: _detail_wiki('wikiversity', i),
}


@library_api.route('/api/library/detail', methods=['GET'])
def library_detail():
    """Everything needed to decide, including a real excerpt of the real text.

    The excerpt is the point of this view. A scan can be catalogued perfectly
    and still be OCR mush, and no metadata field says so -- reading the first
    page is the only cheap test, and it is the same text the builder would be
    handed.
    """
    source = (request.args.get('source') or '').strip()
    ident = (request.args.get('id') or '').strip()
    if source not in _DETAILS:
        return jsonify({'error': f'unknown source "{source}"'}), 400
    if not ident:
        return jsonify({'error': 'id required'}), 400
    try:
        body = _DETAILS[source](ident)
        # Stamped centrally so every adapter cannot forget it; the client shows
        # it on the "Open at ..." link and would otherwise print a raw slug.
        body['source_label'] = SOURCE_LABELS.get(source, source)
        return jsonify(body)
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logger.warning(f'library detail failed for {source}/{ident}: {e}')
        return jsonify({'error': f'{SOURCE_LABELS.get(source, source)} did not '
                                 'return details for this book.'}), 502


# ---------------------------------------------------------------------------
# Covers
# ---------------------------------------------------------------------------

# MEASURED, and the reason this cannot be a status-code check: archive.org's
# /services/img/{id} answers HTTP 200 for an identifier that does not exist, and
# serves a byte-identical 2212-byte PNG placeholder. Detecting "no cover" by
# status would put that grey rectangle on screen as though it were art. We match
# it by digest and substitute our own blank instead, which at least looks
# deliberate and matches the rest of the grid.
_IA_PLACEHOLDER_MD5 = 'dbc276acf4ba992e2e4d8fd572aaf45c'
_IA_PLACEHOLDER_LEN = 2212

_BLANK_ASSET = 'book-blank.svg'
_MAX_COVER_BYTES = 3 * 1024 * 1024
_ALLOWED_COVER_TYPES = {
    'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif',
    'image/webp': '.webp', 'image/svg+xml': '.svg',
}


def _cover_cache_dir():
    root = os.environ.get('DATA_ROOT', '/app/data')
    path = os.path.join(root, 'cache', 'covers')
    os.makedirs(path, exist_ok=True)
    return path


def _blank_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'static', 'img', _BLANK_ASSET)


def _serve_blank(reason):
    """The asked-for behaviour: a neutral blank book, never a broken image and
    never a hidden card. Generated locally and shipped in static/img, because
    fetching a placeholder from the network is the failure it exists to cover.
    """
    resp = make_response(send_file(_blank_path(), mimetype='image/svg+xml'))
    resp.headers['X-Cover-Fallback'] = reason
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp


def _upstream_cover_url(source, ident):
    if source == 'internet_archive':
        return f'https://archive.org/services/img/{ident}'
    if source == 'gutenberg':
        try:
            return _pg_cover_url(_fetch_gutenberg_book(ident).get('formats'))
        except Exception as e:
            logger.info(f'gutenberg cover lookup failed for {ident}: {e}')
            return None
    if source == 'openstax':
        try:
            return _openstax_record(ident).get('cover_url')
        except Exception as e:
            logger.info(f'openstax cover lookup failed for {ident}: {e}')
            return None
    if source in _WIKI_HOSTS:
        try:
            r = _get(f'https://{_WIKI_HOSTS[source]}/w/api.php', params={
                'action': 'query', 'prop': 'pageimages', 'piprop': 'thumbnail',
                'pithumbsize': 320, 'titles': ident,
                'format': 'json', 'formatversion': 2})
            pages = ((r.json().get('query') or {}).get('pages') or [])
            return ((pages[0].get('thumbnail') or {}).get('source')
                    if pages else None)
        except Exception as e:
            logger.info(f'wiki cover lookup failed for {ident}: {e}')
            return None
    return None


@library_api.route('/api/library/cover', methods=['GET'])
def library_cover():
    """Fetch a cover once, keep it on disk, serve it from there forever after.

    Two things are cached, not one. A HIT stores the image bytes; a MISS stores
    an empty marker file. Without the marker, every render of a coverless book
    would re-ask the network for an image we already know is not there -- and
    coverless books are common enough on the Archive that this dominates the
    traffic. Both survive the appliance going offline, which is the point.
    """
    source = (request.args.get('source') or '').strip()
    ident = (request.args.get('id') or '').strip()
    if source not in SOURCES or not ident:
        return _serve_blank('bad-request')

    key = hashlib.sha1(f'{source}:{ident}'.encode('utf-8')).hexdigest()
    cdir = _cover_cache_dir()
    miss_marker = os.path.join(cdir, key + '.miss')
    if os.path.exists(miss_marker):
        return _serve_blank('cached-miss')
    for ext in ('.jpg', '.png', '.gif', '.webp', '.svg'):
        hit = os.path.join(cdir, key + ext)
        if os.path.exists(hit):
            resp = make_response(send_file(hit))
            resp.headers['Cache-Control'] = 'public, max-age=604800'
            resp.headers['X-Cover-Cache'] = 'hit'
            return resp

    url = _upstream_cover_url(source, ident)
    if not url:
        _touch(miss_marker)
        return _serve_blank('no-cover-url')

    try:
        r = _get(url, timeout=COVER_TIMEOUT, stream=True,
                 allow_redirects=True)
        if r.status_code != 200:
            _touch(miss_marker)
            return _serve_blank(f'upstream-{r.status_code}')
        ctype = (r.headers.get('Content-Type') or '').split(';')[0].strip()
        if ctype not in _ALLOWED_COVER_TYPES:
            # Several of these hosts answer a missing image with an HTML error
            # page at status 200. Storing that as a cover would render as a
            # broken image on every future search.
            _touch(miss_marker)
            return _serve_blank('not-an-image')
        data = r.raw.read(_MAX_COVER_BYTES + 1, decode_content=True) or b''
    except Exception as e:
        logger.info(f'cover fetch failed for {source}/{ident}: {e}')
        # Deliberately NOT cached as a miss: this is the network being down, not
        # the book being coverless, and the two must not be confused.
        return _serve_blank('fetch-failed')

    if not data or len(data) > _MAX_COVER_BYTES:
        _touch(miss_marker)
        return _serve_blank('empty-or-oversized')
    if (source == 'internet_archive'
            and len(data) == _IA_PLACEHOLDER_LEN
            and hashlib.md5(data).hexdigest() == _IA_PLACEHOLDER_MD5):
        _touch(miss_marker)
        return _serve_blank('ia-placeholder')

    path = os.path.join(cdir, key + _ALLOWED_COVER_TYPES[ctype])
    try:
        tmp = path + '.part'
        with open(tmp, 'wb') as fh:
            fh.write(data)
        os.replace(tmp, path)          # never let a search read a half-file
    except OSError as e:
        logger.warning(f'cover cache write failed: {e}')

    resp = make_response(data)
    resp.headers['Content-Type'] = ctype
    resp.headers['Cache-Control'] = 'public, max-age=604800'
    resp.headers['X-Cover-Cache'] = 'miss'
    return resp


def _touch(path):
    try:
        with open(path, 'wb'):
            pass
    except OSError as e:
        logger.warning(f'could not write cover miss marker {path}: {e}')
