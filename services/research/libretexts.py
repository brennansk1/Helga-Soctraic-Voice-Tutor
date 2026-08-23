"""LibreTexts as a course source: edited textbooks, fetched within robots.

WHY THIS EXISTS
---------------
Two of the three domain extensions had no way to obtain a book at all.

`mathematics.source_for()` named the right OpenStax title and then returned
`{"content": "local copy required"}`, because OpenStax `robots.txt` disallows
`/contents` and `/apps/archive` for EVERY agent — so the book could be named
but never read. `history` had no `source_for` at all and was never asked. The
result was that both domains' mining layers (`worked_examples`,
`source_mining`) read `lesson["source_text"]`, a key nothing in production ever
wrote. They were wired to a source that could not arrive.

LibreTexts republishes the same edited textbooks — including the OpenStax ones
— on a host whose `robots.txt` permits reading ordinary content pages. So the
book that could only be named can now be read.

WHAT ROBOTS ACTUALLY SAYS (fetched verbatim 2026-08-23, both libraries)
----------------------------------------------------------------------
    User-Agent: *
    Crawl-delay: 5
    Request-rate: 1/5
    Sitemap: https://<lib>.libretexts.org/sitemap.xml
    Allow:    /@api/deki/files/
    Disallow: /Special:*   /*title=Special:*   /Template:*   /*title=Template:*
    Disallow: /User:*      /*title=User:*      /deki/  /*action=*  /@*

Three consequences, each enforced in code below rather than remembered:

  1. `/@*` is disallowed, so the MindTouch `/@api/deki/pages/{id}/contents`
     endpoint is OFF LIMITS despite being the obvious way to do this. A web
     search recommended it; robots.txt refuses it. Only `/@api/deki/files/` is
     carved out, and that is attachments, not page text.
  2. `Special:*` is disallowed, so LibreTexts' own SEARCH cannot be used to
     find a book. The advertised `Sitemap:` is the sanctioned discovery route,
     and is what `sitemap_urls` uses.
  3. `Crawl-delay: 5` is a PUBLISHED limit, not a guess. It goes in
     `ratelimit._MIN_INTERVAL` as 5.0 s, where the module's own convention
     marks documented limits as hard.

`_allowed()` re-checks every URL against those Disallow rules before any
request. The existing OpenStax call in `syllabus_sources._openstax_chapters`
violates that site's wildcard rule precisely because nothing re-checked it, so
here the check is a function every fetch goes through rather than a comment.

WHY `/Bookshelves/` AND NOT `/Courses/`
--------------------------------------
Both are permitted; only one is worth reading. `/Courses/` is one institution's
remix of a book for one term — 43,381 URLs on the mathematics library, most of
them a specific university's section numbering. `/Bookshelves/` is the curated,
edited shelf: 157 books on mathematics, 111 on humanities.

That distinction also fixes a failure `mathematics.source_for` documents:

    the generic relevance matcher answers "linear algebra" with *Algebra 1*,
    a high-school text for a university subject

On the shelf, `Linear_Algebra/` and `Algebra/` are SEPARATE directories, so the
shelf name carries the level and the match cannot silently drop a university
subject onto a school textbook.

FINDING WHERE A BOOK STARTS
---------------------------
The book is not at a fixed depth. Measured across both sitemaps:

    mathematics   Bookshelves/Calculus/Calculus_(OpenStax)/...          depth 2
    history       Bookshelves/History/National_History/U.S._History/... depth 3

and markers appear as deep as 5. Anything assuming a fixed depth works on one
library and silently finds nothing on the other — the failure mode this
repository keeps meeting.

So a book is located STRUCTURALLY: LibreTexts gives every book a
`00:_Front_Matter` and a `zz:_Back_Matter`, so the book root is the parent of
those. Measured: 629 books discoverable this way across the two libraries.
"""
import logging
import re
import urllib.parse

logger = logging.getLogger(__name__)

try:  # container (flat)
    import ratelimit as _rl
except ImportError:  # imported as a package
    from services.research import ratelimit as _rl

#: Which library hosts which subjects. LibreTexts splits by discipline, and
#: asking the wrong one returns nothing rather than something wrong.
LIBRARIES = {
    # "statistics" and "probability" are deliberately NOT here. LibreTexts
    # keeps a separate statistics library, and routing them to `math` selected
    # *Math For Liberal Art Students* (94 pages) over a statistics text. The
    # mathematics DOMAIN still claims these subjects; the LIBRARY is chosen by
    # what the subject is about.
    "math": ("mathematics", "math", "maths", "algebra", "calculus", "geometry",
             "trigonometry", "precalculus",
             "linear algebra", "differential equations", "number theory",
             "discrete mathematics", "arithmetic", "prealgebra", "analysis"),
    "human": ("history", "philosophy", "literature", "composition", "writing",
              "rhetoric", "classics", "languages", "religion", "art history"),
    "bio": ("biology", "genetics", "ecology", "microbiology", "botany",
            "zoology", "evolution", "anatomy", "physiology"),
    "chem": ("chemistry", "organic chemistry", "biochemistry", "analytical"),
    "phys": ("physics", "astronomy", "mechanics", "thermodynamics", "optics",
             "relativity", "quantum"),
    "stats": ("statistics", "data analysis", "regression", "inference"),
    "socialsci": ("psychology", "sociology", "anthropology", "political",
                  "geography", "economics"),
    "eng": ("engineering", "materials", "civil", "electrical", "mechanical"),
    "biz": ("business", "accounting", "finance", "management", "marketing"),
}

_HOST = "https://{lib}.libretexts.org"

#: Disallow rules from robots.txt, as patterns rather than prose. Checked on
#: every URL before it is fetched.
_DISALLOWED = (
    re.compile(r"/Special:", re.I),
    re.compile(r"[?&]title=Special:", re.I),
    re.compile(r"/Template:", re.I),
    re.compile(r"[?&]title=Template:", re.I),
    re.compile(r"/User:", re.I),
    re.compile(r"[?&]title=User:", re.I),
    re.compile(r"/deki/", re.I),
    re.compile(r"[?&]action=", re.I),
    re.compile(r"/@"),
)

#: A book's boundary markers. Every LibreTexts book has both.
_MATTER = ("00:", "zz:")

#: Words in half the book titles on any shelf, carrying no matching signal.
#: The two-letter entries are here because `_WORD` had to be widened to catch
#: "US" — see `_norm` — which let ordinary prepositions in with it.
_STOP = {"the", "and", "for", "with", "from", "book", "introduction",
         "introductory", "course", "text", "textbook", "edition", "volume",
         "concepts", "principles", "fundamentals", "essentials", "openstax",
         "of", "in", "to", "on", "at", "by", "is", "as", "an", "or", "it",
         "be", "a"}

#: TWO letters, not three. Measured: with `{3,}`, "US History" scored only on
#: "history" and selected *Art History II (Lumen)* off the Art shelf, because
#: "US" was discarded and "history" matches `Art_History_and_Theory` as well as
#: it matches any history book.
_WORD = re.compile(r"[A-Za-z]{2,}")

#: Dotted acronyms, so a book titled "U.S._History" is comparable with a
#: subject written "US History". Without this both sides tokenise to nothing.
_DOTTED = re.compile(r"\b(?:[A-Za-z]\.){2,}")


def _norm(s):
    """Lowercased, with dotted acronyms closed up and separators removed."""
    s = (s or "").replace("_", " ").replace("%3A", ":").replace("%27", "'")
    s = _DOTTED.sub(lambda m: m.group(0).replace(".", ""), s)
    return s.lower()

CACHE_TTL = 604800          # 7 days — a textbook does not change weekly
MAX_PAGES = 60              # a course needs a shelf's worth, not a whole book


def _cache():
    """The shared disk cache, or None when diskcache is unavailable.

    Absent cache must degrade to "fetch it again", never to an exception: the
    sitemap is 11.6 MB on the mathematics library and 21 MB across the two
    humanities shards, so caching is a courtesy to LibreTexts as much as a
    speed-up, but losing it must not lose the source.
    """
    try:
        import os
        from diskcache import Cache
        return Cache(os.environ.get("HELGA_CACHE_DIR", "/tmp/helga-doc-cache"))
    except Exception as e:                       # pragma: no cover - defensive
        logger.debug(f"[LT] cache unavailable: {e}")
        return None


def _allowed(url):
    """True when robots.txt permits fetching this URL.

    Called before every request. The OpenStax violation already in this
    codebase exists because the equivalent check was a comment rather than a
    function, so this one is a function.
    """
    try:
        path = urllib.parse.urlsplit(url).path or "/"
        query = urllib.parse.urlsplit(url).query or ""
    except Exception:
        return False
    probe = path + ("?" + query if query else "")
    return not any(p.search(probe) for p in _DISALLOWED)


def _get(url, timeout=45):
    """Fetch one URL, honouring the published crawl delay. None on failure."""
    if not _allowed(url):
        logger.warning(f"[LT] robots.txt disallows {url} — not fetched")
        return None
    import urllib.request
    try:
        _rl.wait(url)
        req = urllib.request.Request(url, headers=_rl.headers("Helga/1.0"))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            try:
                _rl.note_response(url, status=r.status,
                                  resp_headers=dict(r.headers))
            except Exception:
                pass
        return body.decode("utf-8", errors="replace")
    except Exception as e:
        logger.info(f"[LT] fetch failed {url}: {e}")
        return None


def library_for(subject):
    """Which LibreTexts library serves this subject, or None."""
    s = (subject or "").lower()
    best, score = None, 0
    for lib, words in LIBRARIES.items():
        hit = sum(len(w) for w in words if w in s)
        if hit > score:
            best, score = lib, hit
    return best


def sitemap_urls(lib):
    """Every content URL a library advertises.

    Handles BOTH sitemap shapes. The mathematics library serves a flat
    `<urlset>`; the humanities library serves a `<sitemapindex>` pointing at
    `sitemap_0.xml` / `sitemap_1.xml`. Assuming one shape finds 43,381 URLs on
    one library and 2 on the other, which is the silent-nothing failure this
    project keeps hitting — so both are handled and the shape is detected, not
    assumed.
    """
    key = f"lt:sitemap:{lib}"
    c = _cache()
    if c is not None:
        hit = c.get(key)
        if hit:
            return hit

    root = _HOST.format(lib=lib) + "/sitemap.xml"
    body = _get(root, timeout=120)
    if not body:
        return []

    urls = []
    if "<sitemapindex" in body[:400]:
        shards = re.findall(r"<loc>(.*?)</loc>", body)
        for shard in shards[:8]:            # bounded: a library is not endless
            part = _get(shard.strip(), timeout=120)
            if part:
                urls += re.findall(r"<loc>(.*?)</loc>", part)
    else:
        urls = re.findall(r"<loc>(.*?)</loc>", body)

    urls = [u.strip() for u in urls if "/Bookshelves/" in u]
    logger.info(f"[LT] {lib}: {len(urls)} bookshelf URLs")
    if c is not None and urls:
        try:
            c.set(key, urls, expire=CACHE_TTL)
        except Exception:
            pass
    return urls


def books(lib):
    """Book roots on a library's shelves: {path -> [page urls]}.

    A book root is the parent of a `00:_Front_Matter` / `zz:_Back_Matter`
    segment — see the module docstring on why depth cannot be assumed.
    """
    key = f"lt:books:{lib}"
    c = _cache()
    if c is not None:
        hit = c.get(key)
        if hit:
            return hit

    roots = set()
    urls = sitemap_urls(lib)
    for u in urls:
        try:
            tail = u.split("/Bookshelves/", 1)[1]
        except IndexError:
            continue
        segs = [urllib.parse.unquote(s) for s in tail.split("/")]
        for i, s in enumerate(segs):
            if s.startswith(_MATTER):
                if i:
                    roots.add("/".join(segs[:i]))
                break

    out = {}
    for u in urls:
        try:
            tail = urllib.parse.unquote(u.split("/Bookshelves/", 1)[1])
        except IndexError:
            continue
        for r in roots:
            if tail.startswith(r + "/"):
                out.setdefault(r, []).append(u)
                break

    logger.info(f"[LT] {lib}: {len(out)} books")
    if c is not None and out:
        try:
            c.set(key, out, expire=CACHE_TTL)
        except Exception:
            pass
    return out


def _score(subject, path):
    """How well a book path answers this subject.

    The SHELF is scored as well as the title, and that is the point: the shelf
    carries the level. "linear algebra" matches the `Linear_Algebra` shelf
    exactly and the `Algebra` shelf only partly, which is what stops a
    university subject being answered with a school textbook.
    """
    want = {w for w in _WORD.findall(_norm(subject))} - _STOP
    if not want:
        return 0.0
    segs = path.split("/")
    have_shelf = {w for w in _WORD.findall(_norm(" ".join(segs[:-1])))} - _STOP
    have_title = {w for w in _WORD.findall(_norm(segs[-1]))} - _STOP

    # Shelf agreement is weighted above title agreement deliberately: a title
    # can mention a topic the book merely touches, while the shelf states what
    # the book IS.
    s = 2.0 * len(want & have_shelf) + 1.0 * len(want & have_title)
    # An exact multi-word shelf match ("linear algebra") is the strongest
    # signal there is, and word-set overlap alone cannot see it.
    if len(want) > 1 and " ".join(sorted(want)) in " ".join(sorted(have_shelf)):
        s += 3.0
    return s / (len(want) or 1)


#: Below this, a subject has not actually named a book — see `find`.
MIN_SCORE = 1.0


def _coverage(subject, urls, cap=400):
    """How much of this book is ABOUT the subject, read off its contents.

    Title matching alone cannot answer "ancient Rome": no history book is
    called that, so every candidate scored 0 and the fallback handed back
    *U.S. History* — a survey that does not cover Rome at all.

    But the sitemap already gives every chapter and section name in every book,
    which is the table of contents. Asking how many of them mention the subject
    measures COVERAGE rather than naming, and that is the question actually
    being asked of a textbook.

    Returns `(sections, fraction)`, and the CALLER ranks on the COUNT. That
    distinction was measured: ranking by fraction answered "the Civil War" with
    *La guerra civil española para estudiantes* — 29 pages, in Spanish —
    because six relevant sections out of twenty-nine beats twenty-five out of
    three hundred and thirty-eight. The fraction says how CONCENTRATED a book
    is; the count says how much of the subject it actually teaches, and a
    course is built from the latter. The fraction is kept as a relevance floor
    so a large book cannot win on incidental mentions.
    """
    want = {w for w in _WORD.findall(_norm(subject))} - _STOP
    if not want or not urls:
        return 0, 0.0
    hits = 0
    sample = urls[:cap]
    for u in sample:
        tail = _norm(urllib.parse.unquote(u).rsplit("/", 2)[-1])
        if any(w in tail for w in want):
            hits += 1
    return hits, hits / len(sample)


def find(subject, lib=None, shelf=None):
    """The best book for a subject: (path, page_urls, lib) or (None, [], None).

    `shelf` constrains the search to one top-level shelf. The humanities
    library holds Art, Literature, Philosophy and History together, and
    measured without this the history domain's own subjects selected art books:
    "US History" returned *Art History II (Lumen)*. The shelf carries the
    DISCIPLINE here exactly as it carries the level on the mathematics library,
    so a domain that knows its discipline should say so.

    WHEN NOTHING SCORES: a subject like "the Cold War" names a topic, not a
    book, and matching it on one shared word picked *Tokyo University and the
    War* (30 pages) over any survey text. Below `MIN_SCORE` the fullest book on
    the constrained shelf is returned instead — for a topic within a
    discipline, the discipline's largest survey is a defensible answer and a
    30-page monograph on one word is not.
    """
    lib = lib or library_for(subject)
    if not lib:
        return None, [], None
    all_books = books(lib)
    if not all_books:
        return None, [], lib
    if shelf:
        pre = shelf.rstrip("/") + "/"
        scoped = {p: u for p, u in all_books.items() if p.startswith(pre)}
        all_books = scoped or all_books

    best, best_s = None, 0.0
    for path in all_books:
        s = _score(subject, path)
        # Prefer the fuller book when two score alike: a 20-page stub and a
        # 285-page textbook are not equally good answers to the same request.
        if s > best_s or (s == best_s and best and
                          len(all_books[path]) > len(all_books[best])):
            best, best_s = path, s

    if best_s < MIN_SCORE:
        # Nothing was NAMED. Ask which book COVERS it instead — the contents
        # are already in hand and answer a question the title cannot.
        cov = {p: _coverage(subject, u) for p, u in all_books.items()}
        # Ranked on the COUNT, floored on the fraction — see `_coverage`.
        eligible = {p: c for p, c in cov.items() if c[1] >= 0.02}
        top = max(eligible, key=lambda p: (eligible[p][0], len(all_books[p])),
                  default=None)
        if top:
            n, frac = eligible[top]
            logger.info(f"[LT] {subject!r} named no book; {top} teaches it in "
                        f"{n} sections ({frac:.0%})")
            return top, all_books[top], lib
        fallback = max(all_books, key=lambda p: len(all_books[p]), default=None)
        if fallback and shelf:
            logger.info(f"[LT] {subject!r} matched nothing on {shelf} — "
                        f"using the fullest survey: {fallback}")
            return fallback, all_books[fallback], lib
        if best_s <= 0:
            return None, [], lib
    return best, all_books[best], lib


# --- page extraction ---------------------------------------------------------
#
# What the miners need, and therefore what must survive extraction:
#
#   mathematics/worked_examples.examples_in_text  wants "Example" and
#       "Solution" each at the START OF A LINE, with the maths still present.
#   history/teaching_moves.from_text  wants provenance phrases and
#       "<Name> argues" intact, which means sentence structure must survive.
#
# So headings become their own lines rather than being flattened into the
# surrounding prose. Flattening is what turns a book into "a wall of scraped
# text" and it is exactly what the domain readers exist to avoid.

_DROP = ("script", "style", "nav", "header", "footer", "form", "noscript")


def _normalise_math(text):
    r"""LibreTexts delimits maths as `\(...\)` and `\[...\]`.

    Normalised to `$...$`, which is what `worked_examples._has_math` looks for
    and what the vendored KaTeX renders. No MathML is served here, so the
    `mathematics.mathml` converter is not needed on this path — that module
    remains for OpenStax's own MathML, which is a different source.
    """
    text = re.sub(r"\\\((.+?)\\\)", lambda m: f"${m.group(1).strip()}$",
                  text, flags=re.S)
    text = re.sub(r"\\\[(.+?)\\\]", lambda m: f"\n$${m.group(1).strip()}$$\n",
                  text, flags=re.S)
    return text


def extract(html):
    """(title, text) from one LibreTexts page, headings preserved as lines."""
    if not html:
        return "", ""
    try:
        from bs4 import BeautifulSoup
    except ImportError:                          # pragma: no cover - defensive
        logger.warning("[LT] bs4 unavailable — cannot extract")
        return "", ""

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_DROP)):
        tag.decompose()

    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)

    body = (soup.find("section", class_="mt-content-container")
            or soup.find(id="mt-content")
            or soup.find("main") or soup.body or soup)

    lines = []
    for el in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li",
                             "dt", "dd", "td", "blockquote", "pre"]):
        txt = el.get_text(" ", strip=True)
        if not txt:
            continue
        if el.name.startswith("h"):
            # Its own line, and stripped of LibreTexts' \(\PageIndex{1}\)
            # counter, so "Example \(\PageIndex{1}\): Evaluating Functions"
            # becomes "Example 1: Evaluating Functions" and the miner's
            # `^\s*example\s*(\d+)?` actually matches.
            txt = re.sub(r"\\\(\\PageIndex\{(\d+)\}\\\)", r"\1", txt)
            txt = re.sub(r"\s+", " ", txt).strip()
            lines.append("\n" + txt)
        else:
            lines.append(txt)

    text = "\n".join(lines)
    text = _normalise_math(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return title, text.strip()


def chapters_for(subject, lib=None, cap=60):
    """Top-level chapter titles for the best-matching book, in reading order.

    Exists so `syllabus_sources` can build an OpenStax outline WITHOUT fetching
    `openstax.org/apps/archive/.../contents/...`, which that site's robots.txt
    disallows for every agent. LibreTexts republishes the same books — Calculus
    (OpenStax), U.S. History (OpenStax), Precalculus 2e — and permits reading
    content pages, so the chapter list is obtainable without the violation.

    Costs no page fetches: the chapter names are already in the sitemap.
    """
    path, urls, lib = find(subject, lib=lib)
    if not path or not urls:
        return []
    depth = len(path.split("/"))
    seen, out = set(), []
    for u in sorted(urls, key=lambda x: urllib.parse.unquote(x)):
        segs = urllib.parse.unquote(u).split("/Bookshelves/", 1)[-1].split("/")
        if len(segs) <= depth:
            continue
        chapter = segs[depth]
        if chapter.startswith(_MATTER) or chapter in seen:
            continue
        seen.add(chapter)
        # "05: Integration" -> "Integration". The number is positional and
        # would end up inside a concept title downstream.
        clean = re.sub(r"^\d+\s*[:.]\s*", "", chapter.replace("_", " ")).strip()
        if clean:
            out.append(clean)
        if len(out) >= cap:
            break
    return out


def pages_for(subject, lib=None, max_pages=MAX_PAGES, shelf=None):
    """Fetch a book's pages for `subject`.

    Returns `(pages, meta)` in the shape `book_skeleton` already consumes from
    `computer_science.source_for` — `{url, title, section, entries, text,
    code_blocks}` — so this plugs into the existing wiring point rather than
    needing a second one.
    """
    path, urls, lib = find(subject, lib=lib, shelf=shelf)
    if not path or not urls:
        return [], {}

    # Reading order. LibreTexts numbers its sections ("1.01:", "10.3:"), and
    # sorting those as strings puts chapter 10 before chapter 2 — a book whose
    # argument is cumulative then arrives shuffled.
    def _key(u):
        tail = urllib.parse.unquote(u).rsplit("/", 1)[-1]
        m = re.match(r"(\d+)(?:[.:](\d+))?", tail)
        return ((int(m.group(1)), int(m.group(2) or 0)) if m
                else (9999, 0), tail)

    ordered = sorted(urls, key=_key)
    ordered = [u for u in ordered
               if not re.search(r"/(00%3A_Front|zz%3A_Back)", u, re.I)]

    pages = []
    for u in ordered[:max_pages]:
        html = _get(u)
        if not html:
            continue
        title, text = extract(html)
        if len(text) < 200:
            continue
        segs = urllib.parse.unquote(u).split("/")
        section = segs[-2].replace("_", " ") if len(segs) > 1 else None
        section = re.sub(r"^\d+\s*:\s*", "", section or "").strip() or None
        pages.append({
            "url": u,
            "title": (title or segs[-1].replace("_", " "))[:120],
            "section": section,
            "entries": [],
            "text": text,
            # Maths and history books have no code. Kept at 0 rather than
            # omitted because `DocSet.material` sums this field unconditionally.
            "code_blocks": 0,
        })

    meta = {
        "source": "libretexts",
        "slug": path,
        "name": path.split("/")[-1].replace("_", " ").replace("%3A", ":"),
        "library": lib,
        "url": f"{_HOST.format(lib=lib)}/Bookshelves/"
               + urllib.parse.quote(path, safe="/"),
        "available_pages": len(ordered),
        "licence": "CC BY-NC-SA (varies by book; check the book's front matter)",
    }
    logger.info(f"[LT] {subject!r} -> {path} "
                f"({len(pages)}/{len(ordered)} pages)")
    return pages, meta
