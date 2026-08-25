"""Wikisource as a primary-document source: the text AND its attribution.

WHY THIS EXISTS
---------------
History's `SOURCE_CHECK` move asks the one question the domain can always ask
without requiring recall: *given who wrote this, when, and for whom — what
would they stress, and what would they leave out?* It refuses an extract with
no provenance, and correctly: sourcing is a question ABOUT the attribution, and
without one the extract is merely a quotation.

`teaching_moves` recovers that provenance from prose with a regex, because the
material it was written for is a textbook. Measured against a real one, that
does not work:

    U.S. History (American YAWP), 14 pages sampled across the whole book,
    69,065 characters:
        historian attributions ("<Name> argues")   0
        labelled source blocks ("Source A: ...")   0
        the word "historian"                       1

The detectors were validated 9/9 on hand-built fixtures containing exactly
those constructions. Real survey textbooks do not write that way — they
narrate. So the fixtures described a book nobody publishes, and the mining
layer could not have fired on any genuine survey text.

The fix is not a wider regex. A narrative textbook genuinely does not contain
labelled primary sources, and loosening the pattern until something matches
manufactures moves out of ordinary prose — which is the failure the two-sided
`contested_interpretation` rubric punishes hardest.

Instead: get primary documents from an archive that PUBLISHES them with their
attribution. Wikisource stores it structurally, in the page's header template:

    {{header
     | title  = Zimmermann Telegram
     | author = Arthur Zimmermann
     | year   = 1917
     | notes  = ... dispatched by the Foreign Secretary of the German Empire...

That is who, when and in what capacity — parsed, not inferred. A regex over
prose was always a worse way to obtain something a database already holds.

ROBOTS AND POLICY
-----------------
Wikimedia's `robots.txt` blocks named misbehaving crawlers and leaves content
open; the `w/api.php` endpoint is the sanctioned programmatic route and is what
this module uses, never page scraping. `ratelimit` carries the host at the same
0.2 s interval as the other Wikimedia properties already in that table, and
Wikimedia's policy asks for a user agent with contact details, which
`ratelimit.headers()` supplies.
"""
import logging
import re
import urllib.parse

logger = logging.getLogger(__name__)

try:  # imported as a package (tests, other services)
    from services.research import ratelimit as _rl
except ImportError:  # container: this image mounts the modules flat at /app
    import ratelimit as _rl

API = "https://en.wikisource.org/w/api.php"

#: Namespaces that are not documents. Author: and Portal: pages are indexes,
#: Translation: duplicates a work already present, and Page:/Index: are the
#: scan-proofreading layer rather than the finished text.
_SKIP_PREFIX = ("author:", "portal:", "index:", "page:", "wikisource:",
                "help:", "category:", "template:", "translation:", "file:")

#: A header field, from the `{{header ... }}` template.
_FIELD = re.compile(r"\|\s*(\w+)\s*=\s*([^\n|]*)")

#: Wiki markup that must not reach a learner as if it were the document.
#:
#: ORDER MATTERS. The bookkeeping links go FIRST and are DELETED; the ordinary
#: links are unwrapped afterwards. Unwrapping everything left
#: "Category:World War I" appended to the Zimmermann Telegram, which a learner
#: would read as part of the document — the link text of a category link is
#: filing metadata, not prose.
_MARKUP = (
    (re.compile(r"\[\[\s*(?:Category|File|Image|w|wikipedia|s|Author|Portal)\s*:"
                r"[^\]]*\]\]", re.I), " "),
    (re.compile(r"\{\{[^{}]*\}\}"), " "),
    (re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]"), r"\1"),
    (re.compile(r"'''?"), ""),
    (re.compile(r"<ref[^>]*>.*?</ref>", re.S), " "),
    (re.compile(r"<[^>]+>"), " "),
    (re.compile(r"^[=*#:]+", re.M), " "),
)

MIN_CHARS = 200
MAX_CHARS = 1200


def _api(params, timeout=30, attempts=3):
    """One MediaWiki API call, or None. Retried, because bursts fail silently.

    `syllabus_sources._get_json` already documents this for the other Wikimedia
    properties: a burst is answered with a non-JSON body rather than a 429, so
    `json.loads` raises and the lookup returns nothing that looks exactly like
    "no such document". Measured here on the first run — three topics queried
    back to back returned zero results for two of them, and the same queries
    succeeded individually a moment later.

    Retrying does not resolve the ambiguity for the CALLER, but it removes the
    common cause. Absence after three spaced attempts is much closer to real
    absence than absence after one.
    """
    import json
    import time
    import urllib.error
    import urllib.request
    q = dict(params)
    q.update({"format": "json", "formatversion": "1"})
    url = API + "?" + urllib.parse.urlencode(q)
    for attempt in range(attempts):
        try:
            _rl.wait(url)
            req = urllib.request.Request(url,
                                         headers=_rl.headers("Helga/1.0"))
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", errors="replace")
                try:
                    _rl.note_response(url, status=r.status,
                                      resp_headers=dict(r.headers))
                except Exception:
                    pass
            return json.loads(body)
        except urllib.error.HTTPError as e:
            # FEED THE THROTTLE BACK IN. `note_response` honours Retry-After
            # and `wait()` blocks on it — but only if it is ever told, and it
            # was not: the original version called `note_response` inside the
            # success path only, so a 429 raised straight past it and the retry
            # loop then hammered through the very block being signalled.
            # Measured: Wikimedia returned 429 during this module's own
            # development, and the retries made it worse rather than waiting.
            try:
                _rl.note_response(url, status=e.code,
                                  resp_headers=dict(e.headers or {}))
            except Exception:
                pass
            if e.code in (429, 503) or attempt == attempts - 1:
                logger.info(f"[WS] api call failed ({e.code}); "
                            f"backing off rather than retrying")
                return None
            time.sleep(0.5 * (2 ** attempt))
        except Exception as e:
            if attempt == attempts - 1:
                logger.info(f"[WS] api call failed after {attempts}: {e}")
                return None
            time.sleep(0.5 * (2 ** attempt))
    return None


def search(topic, limit=6):
    """Candidate document titles for a topic, best first."""
    if not topic:
        return []
    data = _api({"action": "query", "list": "search",
                 "srsearch": topic, "srlimit": max(limit * 3, 10),
                 "srnamespace": "0"})
    out = []
    for hit in ((data or {}).get("query", {}) or {}).get("search", []):
        title = hit.get("title") or ""
        if title.lower().startswith(_SKIP_PREFIX):
            continue
        out.append(title)
        if len(out) >= limit:
            break
    return out


def _template_span(text, name):
    """(start, end) of a `{{name ...}}` template, by COUNTING braces.

    A non-greedy `\\{\\{header.*?\\}\\}` stops at the FIRST `}}`, which on a real
    page is a template nested inside the header's own `notes` field. Measured:
    that left a stray `}}` at the head of the Zimmermann Telegram, which is
    what a learner would have been shown as the document's opening line.

    Returns `(-1, -1)` when the template is absent, and `(start, len(text))`
    when its braces never balance — an unbalanced template is malformed markup,
    and showing the remainder is worse than dropping it.
    """
    start = text.lower().find("{{" + name)
    if start < 0:
        return -1, -1
    depth, i = 0, start
    while i < len(text) - 1:
        pair = text[i:i + 2]
        if pair == "{{":
            depth += 1
            i += 2
        elif pair == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                return start, i
        else:
            i += 1
    return start, len(text)


def _strip_template(text, name):
    """`text` with the named template removed."""
    start, end = _template_span(text, name)
    if start < 0:
        return text
    return text[:start] + " " + text[end:]


def _clean(wikitext):
    """Readable document text, with the header template and markup removed."""
    body = wikitext or ""
    # The header is metadata, parsed separately; leaving it in the body would
    # show a learner a template instead of a document.
    for name in _HEADER_TEMPLATES:
        body = _strip_template(body, name)
    for pat, rep in _MARKUP:
        body = pat.sub(rep, body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


#: Templates that carry a work's attribution.
_HEADER_TEMPLATES = ("header", "versions", "translation header")

#: A page that INDEXES works rather than being one.
#:
#: `disambig` is obvious. `versions` is not, and cost a wrong result: it DOES
#: carry a real author, so "Gettysburg Address" passed every attribution check
#: and returned — as the document — a list of the six surviving drafts. A page
#: that lists the texts of a work is not one of them. Search reaches the actual
#: versions ("Gettysburg Address (Bliss copy)", "Proclamation 93") on its own,
#: so skipping the index costs nothing.
_NOT_A_DOCUMENT = re.compile(r"\{\{\s*(disambig|dab|versions)\b", re.I)

#: `[[Author:Abraham Lincoln|Abraham Lincoln]]` -> `Abraham Lincoln`
_LINK = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]")


def is_document(wikitext):
    """False for disambiguation and index pages."""
    return not _NOT_A_DOCUMENT.search(wikitext or "")


def _header(wikitext):
    """The attribution fields: title, author, year, notes."""
    text = wikitext or ""
    for name in _HEADER_TEMPLATES:
        start, end = _template_span(text, name)
        if start < 0:
            continue
        # Links are resolved BEFORE the fields are split, not after. `_FIELD`
        # ends a value at the first `|`, and a wiki-link contains one — so
        # `[[w:Allied and Associated Powers|the Allies]]` was cut in half and
        # the Treaty of Versailles was attributed to
        # "the [[w:Allied and Associated Powers".
        block = _LINK.sub(r"\1", text[start:end])
        head = {}
        for k, v in _FIELD.findall(block):
            v = v.strip()
            if v:
                head[k.lower().strip()] = v
        # `author =` is sometimes left empty with the real name in
        # `override_author` — the Gettysburg Address copies are all like this,
        # so an author-or-nothing rule discards Lincoln from Lincoln's speech.
        if not head.get("author") and head.get("override_author"):
            head["author"] = head["override_author"]
        if head:
            return head
    return {}


def _provenance(head, title):
    """One line stating who wrote this, when — the sourcing question's subject.

    Returned as a SENTENCE rather than a dict because it is handed to the tutor
    as the attribution to interrogate, and because `teaching_moves._PROVENANCE`
    reads exactly this shape: a document noun, `by`/`from`, and a year.
    """
    author = head.get("author") or ""
    year = head.get("year") or head.get("date") or ""
    if author.lower() in ("unknown", "anonymous", ""):
        author = ""
    bits = [title or head.get("title") or "Document"]
    if author:
        bits.append(f"by {author}")
    if re.search(r"\b1[0-9]{3}|20[0-2][0-9]\b", year):
        bits.append(f", {year.strip()}")
    line = " ".join(bits).replace(" ,", ",")
    return line if (author or year) else ""


def documents(topic, limit=2):
    """Primary documents for a topic, each WITH its attribution.

    Returns `[{title, provenance, author, year, text, url}]`. A document whose
    attribution cannot be established is DROPPED rather than returned bare:
    `SOURCE_CHECK` refuses an extract with no provenance, so handing it one
    would only move the refusal later.
    """
    out = []
    for title in search(topic, limit=limit * 3):
        data = _api({"action": "query", "prop": "revisions", "titles": title,
                     "rvprop": "content", "rvslots": "main"})
        pages = ((data or {}).get("query", {}) or {}).get("pages", [])
        if isinstance(pages, dict):
            pages = list(pages.values())
        raw = ""
        for p in pages or []:
            try:
                raw = p["revisions"][0]["slots"]["main"]["*"]
            except Exception:
                try:
                    raw = p["revisions"][0]["*"]
                except Exception:
                    raw = ""
        if not raw:
            continue

        if not is_document(raw):
            logger.debug(f"[WS] {title!r} is an index page — skipped")
            continue
        head = _header(raw)
        prov = _provenance(head, title)
        if not prov:
            logger.debug(f"[WS] {title!r} has no attribution — dropped")
            continue
        text = _clean(raw)
        if len(text) < MIN_CHARS:
            continue

        out.append({
            "title": title,
            "provenance": prov,
            "author": head.get("author", ""),
            "year": head.get("year", ""),
            "notes": (head.get("notes", "") or "")[:400],
            "text": text[:MAX_CHARS],
            "url": "https://en.wikisource.org/wiki/"
                   + urllib.parse.quote(title.replace(" ", "_")),
        })
        if len(out) >= limit:
            break

    logger.info(f"[WS] {topic!r}: {len(out)} document(s) with provenance")
    return out
