"""The Wikisource reader: attribution, index pages, markup. No network."""
import pytest

from services.research import wikisource as ws


_ZIMMERMANN = """{{no source}}
{{header
 | title    = Zimmermann Telegram
 | author   = Arthur Zimmermann
 | year = 1917
 | notes    = A coded telegram dispatched by the Foreign Secretary of the
German Empire {{small|(1917)}} to the ambassador in Mexico.
}}

TELEGRAM RECEIVED. "We intend to begin on the first of February unrestricted
submarine warfare. We shall endeavor in spite of this to keep the United States
of America neutral. In the event of this not succeeding, we make Mexico a
proposal of alliance on the following basis: make war together, make peace
together, generous financial support, and an understanding on our part that
Mexico is to reconquer the lost territory in Texas, New Mexico and Arizona."
[[Category:World War I]] [[w:Zimmermann Telegram|see also]]
"""

_BLISS = """{{header
 | title      = Gettysburg Address
 | author     = |override_author=[[Author:Abraham Lincoln|Abraham Lincoln]]
 | year       =
 | notes      = The Bliss copy.
}}
Four score and seven years ago our fathers brought forth on this continent a
new nation, conceived in liberty, and dedicated to the proposition that all men
are created equal. Now we are engaged in a great civil war, testing whether
that nation, or any nation so conceived and so dedicated, can long endure. We
are met on a great battle-field of that war. We have come to dedicate a portion
of that field as a final resting place for those who here gave their lives that
that nation might live.
"""
# NOTE: both fixtures are long on purpose. Earlier versions were ~110 characters
# against `wikisource.MIN_CHARS = 200`, so `documents()` correctly dropped them
# and the tests read as a broken reader. Fixtures shorter than the thresholds
# they exercise have impersonated a broken detector repeatedly here.

_VERSAILLES = """{{header
 | title  = Treaty of Versailles
 | author = the [[w:Allied and Associated Powers|Allied Powers]]
 | year   = 1919
}}
The Covenant of the League of Nations.
"""

_DISAMBIG = """{{disambig
 | title = Emancipation Proclamation
 | notes = The popular name given to two Presidential Proclamations.
}}
"""

_VERSIONS = """{{versions
 | title  = Gettysburg Address
 | author = Abraham Lincoln
}}
Gettysburg Address (1863), Nicolay draft
Gettysburg Address (1864), Bliss copy
"""


# --- brace counting ----------------------------------------------------------

def test_nested_template_does_not_truncate_the_header():
    """A non-greedy `{{header.*?}}` stops at the FIRST `}}`, which on a real
    page is a template nested in the header's own `notes`. Measured: that left
    a stray `}}` as the opening line of the Zimmermann Telegram."""
    text = ws._clean(_ZIMMERMANN)
    assert not text.startswith("}}")
    assert "}}" not in text
    assert text.startswith("TELEGRAM RECEIVED")


def test_template_span_reports_absence_and_imbalance():
    assert ws._template_span("no templates here", "header") == (-1, -1)
    start, end = ws._template_span("{{header | a = b", "header")
    assert start == 0 and end == len("{{header | a = b")


# --- attribution -------------------------------------------------------------

def test_header_fields():
    head = ws._header(_ZIMMERMANN)
    assert head["author"] == "Arthur Zimmermann"
    assert head["year"] == "1917"


def test_override_author_is_used_when_author_is_empty():
    """The Gettysburg copies all write `author =` empty with the real name in
    `override_author`. An author-or-nothing rule discards Lincoln from
    Lincoln's own speech."""
    assert ws._header(_BLISS)["author"] == "Abraham Lincoln"


def test_links_are_resolved_before_fields_are_split():
    """`_FIELD` ends a value at the first `|`, and a wiki-link contains one.
    Measured: the Treaty of Versailles was attributed to
    "the [[w:Allied and Associated Powers"."""
    author = ws._header(_VERSAILLES)["author"]
    assert author == "the Allied Powers"
    assert "[[" not in author


def test_provenance_reads_as_a_sentence_the_domain_accepts():
    """It is handed to the tutor as the attribution to interrogate, and
    `teaching_moves._PROVENANCE` must recognise it."""
    from services.domains.history.teaching_moves import _PROVENANCE
    prov = ws._provenance(ws._header(_ZIMMERMANN), "Zimmermann Telegram")
    assert "Arthur Zimmermann" in prov and "1917" in prov
    assert _PROVENANCE.search(prov)


def test_unknown_author_is_not_treated_as_an_author():
    head = {"author": "unknown", "year": ""}
    assert ws._provenance(head, "Some Document") == ""


# --- index pages -------------------------------------------------------------

def test_disambiguation_pages_are_not_documents():
    assert ws.is_document(_DISAMBIG) is False


def test_versions_pages_are_not_documents():
    """They carry a real author, so every attribution check passes — and the
    "document" returned is a list of the six surviving drafts."""
    assert ws.is_document(_VERSIONS) is False
    assert ws._header(_VERSIONS)["author"] == "Abraham Lincoln"


def test_a_real_document_is_a_document():
    assert ws.is_document(_ZIMMERMANN) is True
    assert ws.is_document(_BLISS) is True


# --- markup ------------------------------------------------------------------

def test_wiki_markup_does_not_reach_the_learner():
    text = ws._clean(_ZIMMERMANN)
    for marker in ("[[", "]]", "{{", "Category:"):
        assert marker not in text


# --- the whole call, with the network stubbed --------------------------------

def _stub_api(pages):
    """Return an `_api` replacement serving canned wikitext."""
    def _api(params, timeout=30, attempts=3):
        if params.get("list") == "search":
            return {"query": {"search": [{"title": t} for t in pages]}}
        title = params.get("titles")
        return {"query": {"pages": [
            {"revisions": [{"slots": {"main": {"*": pages[title]}}}]}]}}
    return _api


def test_documents_skips_index_pages_and_returns_the_real_one(monkeypatch):
    pages = {"Gettysburg Address": _VERSIONS,
             "Gettysburg Address (Bliss copy)": _BLISS}
    monkeypatch.setattr(ws, "_api", _stub_api(pages))
    docs = ws.documents("Gettysburg Address", limit=1)
    assert len(docs) == 1
    assert docs[0]["title"] == "Gettysburg Address (Bliss copy)"
    assert "Abraham Lincoln" in docs[0]["provenance"]


def test_documents_drops_anything_without_attribution(monkeypatch):
    """`SOURCE_CHECK` refuses an extract with no provenance, so handing it one
    only moves the refusal later."""
    bare = "Just some text with no header at all. " * 10
    monkeypatch.setattr(ws, "_api", _stub_api({"Anon": bare}))
    assert ws.documents("Anon", limit=1) == []


def test_documents_survives_a_dead_api(monkeypatch):
    monkeypatch.setattr(ws, "_api", lambda *a, **k: None)
    assert ws.documents("anything", limit=1) == []


@pytest.mark.parametrize("title", [
    "Author:Abraham Lincoln", "Portal:World War I", "Index:Something.djvu",
    "Page:Book.djvu/12", "Category:Treaties", "Translation:Iliad",
])
def test_non_document_namespaces_are_skipped(title, monkeypatch):
    """Author: and Portal: pages index works; Page:/Index: are the
    scan-proofreading layer rather than the finished text."""
    monkeypatch.setattr(ws, "_api", lambda p, **k: {"query": {"search": [
        {"title": title}, {"title": "Gettysburg Address"}]}})
    assert ws.search("x", limit=5) == ["Gettysburg Address"]


def test_search_skips_index_namespaces(monkeypatch):
    monkeypatch.setattr(ws, "_api", lambda p, **k: {"query": {"search": [
        {"title": "Author:Abraham Lincoln"},
        {"title": "Portal:World War I"},
        {"title": "Gettysburg Address"},
    ]}})
    assert ws.search("lincoln", limit=5) == ["Gettysburg Address"]


def test_throttle_is_fed_back_rather_than_retried(monkeypatch):
    """A 429 raised straight past `note_response` in the first version, so the
    retry loop hammered through the very block being signalled. Measured: a
    real 429 from Wikimedia during development."""
    import urllib.error
    noted, attempts = [], []

    class _Resp(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("u", 429, "Too Many", {"Retry-After": "30"}, None)

    def _open(req, timeout=30):
        attempts.append(1)
        raise _Resp()

    monkeypatch.setattr(ws._rl, "note_response",
                        lambda url, status=None, resp_headers=None:
                        noted.append(status))
    monkeypatch.setattr(ws._rl, "wait", lambda u: 0)
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", _open)

    assert ws._api({"action": "query"}) is None
    assert noted == [429], "the throttle was not reported to the rate limiter"
    assert len(attempts) == 1, "a 429 must not be retried immediately"
