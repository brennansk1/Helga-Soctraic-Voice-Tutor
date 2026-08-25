"""A documentation website, read as a Book.

WHY THIS SHAPE AND NOT A THIRD PIPELINE
---------------------------------------
There are already two course pipelines: the *researched* path (search the web
for evidence, synthesise a skeleton) and the *book* path (parse an upload,
shape the course like the book, hydrate by quoting it). `docs/COURSE_PIPELINE.md`
describes both.

A documentation site is not a third thing. It is a book:

    nav tree  -> table of contents
    section   -> part
    page      -> chapter

So this module produces `book_reader.Book` rather than a new structure, and the
whole downstream pipeline — `book_skeleton.choose_shape`, `book_source
.attach_concepts`, hydrate-from-text, `book_course_qa` — works unchanged.
Everything that already makes a book-sourced course good (shape recorded with a
*why*, concepts named by READING rather than recall, hydration that quotes the
source instead of the model's memory) applies to dbt's docs for free.

CODE IS THE POINT, SO CODE MUST SURVIVE
---------------------------------------
`research_server.extract_page` calls `trafilatura.extract(include_formatting=
False)`, which flattens `<pre><code>` into prose. For a marketing page that is
correct. For a coding course it destroys the material: a dbt page stripped of
its SQL and YAML is a page about nothing. Extraction here keeps fenced code, and
records how much of it a page carries, because a documentation page with no code
is usually a landing page rather than a lesson.

SMART STRETCH
-------------
Smart stretch is the ability to gather ENOUGH real material for the requested
size — a course or a degree — without going shallow or padding. That makes
coverage a first-class output, not a side effect: `DocSet.material` reports
pages, words and code blocks actually retrieved, so `scope_fit` can judge
whether the request is carriable BEFORE compute is spent, and the builder can
decide to supplement from the researched path rather than pad.

A crawl that stops at the first ten pages would silently under-report a subject
and make every course from it shallow. So the page cap here is a budget the
caller sets, not a constant, and the traversal is breadth-first in nav order so
a truncated crawl still covers the whole site broadly rather than one deep
branch of it.
"""
import logging
import os
import re
import time
from collections import deque
from urllib.parse import urljoin, urldefrag, urlparse

logger = logging.getLogger(__name__)

#: Politeness delay between requests to the same host, seconds.
CRAWL_DELAY = 0.25

#: Default breadth. A real doc set (dbt, Django, React) runs to hundreds of
#: pages; a degree-sized request needs most of them, a single course far fewer.
#: The caller passes what the requested size actually needs.
DEFAULT_MAX_PAGES = 120

#: How deep to follow nav links from the entry page. Documentation nests
#: (/docs/build/models/sources), so depth 1 — which is all `doc_crawler`
#: offered — reaches a fraction of a site.
DEFAULT_MAX_DEPTH = 3

#: Below this a page is nav furniture (a section index, a redirect stub), not a
#: lesson. Keeping them creates chapters with nothing to teach from.
MIN_PAGE_CHARS = 400

_SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".zip", ".gz",
             ".js", ".css", ".ico", ".woff", ".woff2", ".xml", ".json", ".txt")

_HREF = re.compile(r'href=["\']([^"\'#>]+)', re.I)
_CODE_BLOCK = re.compile(r"<pre[^>]*>(.*?)</pre>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)

#: Well-known documentation homes, so "teach me dbt" can find the docs instead
#: of hoping a web search surfaces them. Deliberately small and explicit — a
#: guessed URL that 404s costs a request and teaches nothing.
KNOWN_DOCS = {
    "dbt": "https://docs.getdbt.com/docs/introduction",
    "django": "https://docs.djangoproject.com/en/stable/",
    "react": "https://react.dev/learn",
    "kubernetes": "https://kubernetes.io/docs/home/",
    "pandas": "https://pandas.pydata.org/docs/user_guide/index.html",
    # SQL IS A STANDARD, NOT A PRODUCT, so it has no single home — and
    # `resolve("SQL")` therefore returned None while `resolve("postgresql")`
    # worked. A course on "SQL" got no documentation at all for want of an
    # alias. PostgreSQL's manual is the reference the standard is usually read
    # through: complete, free, and the closest thing to a canonical text.
    "sql": "https://www.postgresql.org/docs/current/sql.html",
    "postgres": "https://www.postgresql.org/docs/current/",
    "postgresql": "https://www.postgresql.org/docs/current/",
    "mysql": "https://dev.mysql.com/doc/refman/8.0/en/",
    "sqlite": "https://sqlite.org/docs.html",
    "duckdb": "https://duckdb.org/docs/",
    "python": "https://docs.python.org/3/tutorial/index.html",
    "terraform": "https://developer.hashicorp.com/terraform/docs",
    "airflow": "https://airflow.apache.org/docs/apache-airflow/stable/index.html",
    "fastapi": "https://fastapi.tiangolo.com/learn/",
}


def resolve(subject, fetch=None, searxng_url=None):
    """A documentation entry URL for `subject`, or None.

    THREE STRATEGIES, CHEAPEST FIRST.

    1. A URL, used as given.
    2. `KNOWN_DOCS`, for the handful of subjects worth pinning.
    3. SEARCH. This is the one that matters: a hardcoded dict of ten entries
       meant Polars, Dagster, Svelte, or any internal tool resolved to None and
       got no course at all. SearXNG is already running for the research
       service, and `ranking.is_documentation()` already knows which hosts are
       authoritative — so searching and filtering by doc-host turns "ten
       subjects" into "any subject with public documentation".

    Guessing a URL is still refused. `https://<subject>.io/docs` produces
    confident 404s, and a pipeline that cannot tell "no docs exist" from "my
    guess was wrong" will build a course out of an error page.
    """
    s = (subject or "").strip()
    if not s:
        return None
    if s.lower().startswith(("http://", "https://")):
        return s
    low = s.lower()
    if low in KNOWN_DOCS:
        return KNOWN_DOCS[low]
    for key, url in KNOWN_DOCS.items():
        if key in low.split() or key in low:
            return url
    return search_for_docs(s, fetch=fetch, searxng_url=searxng_url)


def search_for_docs(subject, fetch=None, searxng_url=None, limit=10):
    """Find official documentation for `subject` via SearXNG. None if unsure.

    Ranks candidates by `ranking.is_documentation()` — the same authority list
    the research service weights sources with — so the answer is consistent
    with how the rest of the pipeline judges a source, rather than being a
    second private opinion about what counts as documentation.
    """
    import json as _json
    from urllib.parse import quote_plus
    base = (searxng_url or os.environ.get("SEARXNG_URL")
            or "http://localhost:8080").rstrip("/")
    fetch = fetch or _plain_fetch
    query = quote_plus(f"{subject} official documentation")
    body = fetch(f"{base}/search?q={query}&format=json&language=en")
    if not body:
        logger.info(f"[DOCS] SearXNG unavailable; cannot resolve {subject!r}")
        return None
    try:
        results = (_json.loads(body) or {}).get("results", [])[:limit]
    except Exception:
        return None
    try:
        try:  # container (flat layout: modules live at /app)
            from ranking import is_documentation
        except ImportError:  # imported as a package
            from services.research.ranking import is_documentation
    except Exception:                        # pragma: no cover - defensive
        def is_documentation(_):
            return False

    best = None
    for r in results:
        url = (r.get("url") or "").strip()
        if not url or not doc_root(url):
            continue
        # An authoritative host that also looks like a doc root wins outright.
        if is_documentation(url):
            return url
        if best is None:
            best = url
    if best:
        logger.info(f"[DOCS] {subject!r}: no known-authoritative host; "
                    f"falling back to {best}")
    return best


def _plain_fetch(url):
    import urllib.request
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Helga-Research/1.0 (course builder)"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", "replace") if r.status == 200 else None
    except Exception:
        return None


def _text_and_code(html):
    """(prose_with_code_fenced, code_block_count).

    Parsed with BeautifulSoup rather than regex. The regex version broke on
    exactly the things real documentation contains — `<a title="a>b">`, HTML
    comments, CDATA — and it could not read the ONE piece of metadata that
    matters most here: `class="language-sql"`, which tells you the language
    instead of making you guess it from the source.

    Code blocks are still handled by hand, and that is deliberate rather than
    reinvention. `trafilatura` is the right tool for article bodies and it
    DESTROYS code: measured at 1.12 in markdown mode with formatting on, a
    four-space-indented SQL block came back as `select id, status from ...` on
    one line, and a YAML block as `models: my_project: +materialized: table`.
    The bs4-decompose-then-trafilatura hybrid the literature recommends fails
    the same way, because the collapse happens inside trafilatura. For prose
    that is correct behaviour; for a coding course it is the destruction of the
    material, since indentation IS the syntax in Python and YAML.
    """
    if not html:
        return "", 0
    try:
        from bs4 import BeautifulSoup
    except ImportError:                      # pragma: no cover - bs4 is a dep
        return _text_and_code_regex(html)
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:                        # pragma: no cover - defensive
        return _text_and_code_regex(html)

    # Chrome, not content. Removed before extraction so a nav link never
    # becomes a sentence in a lesson.
    for tag in soup(["script", "style", "nav", "header", "footer", "aside",
                     "form", "noscript", "iframe", "svg"]):
        tag.decompose()

    n_code = 0
    for pre in soup.find_all("pre"):
        code = _code_text(pre)
        if not code.strip():
            pre.decompose()
            continue
        lang = _lang_of(pre)
        n_code += 1
        pre.replace_with("\n\n```" + lang + "\n" + code + "\n```\n\n")

    text = soup.get_text("\n")
    return _tidy_prose(text), n_code


def _code_text(pre):
    """Text of a <pre>, with LINES preserved and tokens joined.

    Neither separator works alone, and both failures were measured on real dbt
    pages:

      get_text("\\n")  -> a newline between EVERY child node. Highlighted code
                         wraps each TOKEN in a <span>, so SQL arrived one token
                         per line: "models" / ":" / " " / "-".
      get_text("")     -> no separator at all. dbt's markup carries its line
                         breaks in ELEMENT STRUCTURE rather than as text nodes,
                         so the whole block arrived on one line.

    Line breaks in HTML code blocks come from three places, and all three have
    to be honoured: literal newlines in text nodes, <br>, and line-level
    containers (<div>, <tr>, <p>, <li>) that documentation generators emit one
    per line. Inline elements (<span>, <a>, <em>) are tokens WITHIN a line and
    must not introduce one.
    """
    LINE_TAGS = ("div", "tr", "p", "li")
    try:
        for br in pre.find_all("br"):
            br.replace_with("\n")
        for tag in pre.find_all(LINE_TAGS):
            # Mark the END of each line-level container. Appending rather than
            # replacing keeps the container's own text.
            tag.append("\n")
        text = pre.get_text("")
    except Exception:                        # pragma: no cover - defensive
        text = pre.get_text("\n")
    # A generator that uses BOTH a line container and a literal newline emits
    # doubles; collapse them without touching deliberate blank lines inside
    # code, which are rare and never more than one.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n")


def _lang_of(pre):
    """The declared language of a code block, from its own markup.

    Documentation generators emit `class="language-sql"`, `class="highlight-py"`
    or `data-lang="yaml"`. Reading it beats guessing from content: a two-line
    shell snippet and a two-line YAML fragment are hard to tell apart by
    inspection and trivial to tell apart from the class the publisher wrote.
    """
    try:
        nodes = [pre] + pre.find_all("code", limit=2)
        for node in nodes:
            for attr in ("data-lang", "data-language", "lang"):
                v = node.get(attr)
                if v:
                    return str(v).strip().lower()[:16]
            for cls in (node.get("class") or []):
                c = str(cls).lower()
                for prefix in ("language-", "lang-", "highlight-", "brush:"):
                    if c.startswith(prefix):
                        return c[len(prefix):].strip()[:16]
                if c in ("python", "sql", "yaml", "json", "bash", "shell",
                         "js", "javascript", "typescript", "go", "rust",
                         "java", "ruby", "html", "css", "jinja", "toml"):
                    return c
    except Exception:                        # pragma: no cover - defensive
        pass
    return ""


def _tidy_prose(text):
    """Normalise whitespace OUTSIDE fenced blocks only.

    Collapsing runs of spaces is right for prose and fatal to code — the same
    fix `book_reader._clean` needed, for the same reason.
    """
    blocks = []

    def _lift(m):
        blocks.append(m.group(0))
        return "\x00F%d\x00" % (len(blocks) - 1)

    text = re.sub(r"```.*?```", _lift, text or "", flags=re.S)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    text = text.strip()
    for i, b in enumerate(blocks):
        text = text.replace("\x00F%d\x00" % i, b)
    return text


def _text_and_code_regex(html):
    """Regex fallback, used only when bs4 is unavailable."""
    blocks = []

    def _stash(m):
        inner = re.sub(r"</(div|tr|li|p)>", "\n", m.group(1), flags=re.I)
        inner = re.sub(r"<br\s*/?>", "\n", inner, flags=re.I)
        inner = _unescape(_TAG.sub("", inner)).strip("\n")
        if not inner.strip():
            return ""
        blocks.append(inner)
        return "\n\x00CODE%d\x00\n" % (len(blocks) - 1)

    body = _CODE_BLOCK.sub(_stash, html)
    body = re.sub(r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ",
                  body, flags=re.I | re.S)
    body = re.sub(r"<(p|div|li|h[1-6]|tr|br)[^>]*>", "\n", body, flags=re.I)
    body = _tidy_prose(_unescape(_TAG.sub(" ", body)))
    for i, blk in enumerate(blocks):
        body = body.replace("\x00CODE%d\x00" % i, "\n```\n" + blk + "\n```\n")
    return body, len(blocks)


def _unescape(s):
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        s = s.replace(a, b)
    return s


def _page_title(html, url):
    for pat in (_H1, _TITLE):
        m = pat.search(html or "")
        if m:
            t = _unescape(_TAG.sub("", m.group(1))).strip()
            t = re.split(r"\s*[|—·]\s*", t)[0].strip()
            if t:
                return t[:120]
    return (urlparse(url).path.rstrip("/").split("/")[-1] or url).replace("-", " ")


def doc_root(url):
    """The path prefix a page must share to count as part of this doc set."""
    try:
        p = urlparse(url)
        path = p.path or "/"
        low = path.lower()
        for marker in ("/docs", "/doc", "/guide", "/guides", "/reference",
                       "/manual", "/tutorial", "/tutorials", "/learn", "/api",
                       "/handbook", "/documentation"):
            i = low.find(marker)
            if i != -1:
                return path[:i + len(marker)]
        host = (p.netloc or "").lower()
        if host.startswith("docs.") or "readthedocs" in host:
            return "/"
        return None
    except Exception:                        # pragma: no cover - defensive
        return None


def _section_of(url, root):
    """The nav section a page belongs to — becomes the Book `part`."""
    try:
        path = urlparse(url).path or "/"
        rest = path[len(root):].strip("/") if root and root != "/" else path.strip("/")
        seg = rest.split("/")[0] if rest else ""
        return seg.replace("-", " ").replace("_", " ").title() or None
    except Exception:                        # pragma: no cover - defensive
        return None


def _links(html, base_url, root):
    out, seen = [], set()
    for href in _HREF.findall(html or ""):
        if href.lower().startswith(("mailto:", "javascript:", "tel:")):
            continue
        absolute = urldefrag(urljoin(base_url, href))[0]
        p = urlparse(absolute)
        if p.scheme not in ("http", "https"):
            continue
        if p.netloc != urlparse(base_url).netloc:
            continue
        if root and root != "/" and not (p.path or "/").startswith(root):
            continue
        if (p.path or "").lower().endswith(_SKIP_EXT):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(absolute)
    return out


class DocSet:
    """What a crawl found, and how much material it actually is."""

    def __init__(self, entry_url, pages):
        self.entry_url = entry_url
        self.pages = pages           # [{url, title, section, text, code_blocks}]

    def __init__(self, entry_url, pages, available_pages=None):
        self.entry_url = entry_url
        self.pages = pages
        #: How many pages the doc set HAS, which is not how many were fetched.
        #: Smart stretch must judge the subject, not the crawl budget: dbt has
        #: 491 doc pages, and a 45-page sample that reported "45" would call a
        #: well-documented subject thin and refuse a course it can easily carry.
        self.available_pages = (len(pages) if available_pages is None
                                else available_pages)

    @property
    def material(self):
        """Coverage, for `scope_fit` — smart stretch needs this BEFORE building."""
        words = sum(len(p["text"].split()) for p in self.pages)
        fetched = max(len(self.pages), 1)
        return {
            "pages_fetched": len(self.pages),
            "pages_available": self.available_pages,
            "words": words,
            # Projected over the whole set, so a sampled crawl still describes
            # the SUBJECT rather than the sample.
            "words_available": int(words / fetched * self.available_pages),
            "code_blocks": sum(p["code_blocks"] for p in self.pages),
            "sections": len({p["section"] for p in self.pages if p["section"]}),
        }

    def as_brief(self):
        """A `scope_fit`-shaped brief, so doc courses get smart stretch too.

        `assess_scope` reads `chapter_count` and `structural_sources`. A
        documentation page is a chapter's worth of syllabus — it is a titled,
        self-contained unit written by the people who build the thing — so the
        page count is the honest chapter count.

        `structural_sources` is 1: official documentation is ONE account of how
        the subject is organised, however many pages it runs to. Claiming more
        would inflate confidence exactly the way `domain_sources` warns about.
        A subject whose only structure is its vendor docs genuinely has one
        structural source, and the builder should know that.
        """
        return {
            "chapter_count": self.available_pages,
            "structural_sources": 1 if self.pages else 0,
            "degraded": not self.pages,
            "source": "documentation",
            "entry_url": self.entry_url,
        }


_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


def sitemap_urls(entry_url, fetch, limit=2000):
    """Every page of this doc set, from the site's sitemap. [] if none.

    WHY THIS IS THE PRIMARY ENUMERATION AND LINK-FOLLOWING IS THE FALLBACK.
    Modern documentation is overwhelmingly JavaScript-rendered. Measured on the
    real dbt docs: the entry page is 58 KB of HTML containing ELEVEN hrefs,
    because the whole navigation sidebar is built client-side by Docusaurus. A
    link-following crawl of that site reaches 3 pages and reports dbt as a
    subject with almost no material — which would make every dbt course
    shallow, and would make `scope_fit` refuse a course that the docs can amply
    support.

    The same site's sitemap.xml lists 1,552 URLs, 491 of them under /docs/.
    Sitemaps exist precisely to enumerate a site for machines, they are one
    request, and they do not care how the page is rendered.

    Handles sitemap indexes (a sitemap of sitemaps), which large doc sets use.
    """
    # CACHED. The PAGES a sitemap points at are cached for 7 days, but the
    # sitemap itself was re-fetched on every crawl — including its sub-sitemaps,
    # up to 25 documents — purely to rediscover URLs whose content was already
    # local. Keyed on the entry URL because the doc root filters the result.
    _ck = f"sitemap:{entry_url}"
    try:
        try:  # container (flat layout: modules live at /app)
            from doc_fetch import _cache as _dc
        except ImportError:  # imported as a package
            from services.research.doc_fetch import _cache as _dc
        _c = _dc()
        if _c is not None:
            _hit = _c.get(_ck)
            if _hit:
                logger.info(f"[DOCS] sitemap (cached) {len(_hit)} page(s)")
                return _hit
    except Exception:
        _c = None
    try:
        p = urlparse(entry_url)
        origin = f"{p.scheme}://{p.netloc}"
        root = doc_root(entry_url)
        candidates = [f"{origin}/sitemap.xml", f"{origin}/sitemap_index.xml"]

        # robots.txt may name a sitemap in a non-standard place.
        robots = fetch(f"{origin}/robots.txt") or ""
        for line in robots.splitlines():
            if line.lower().startswith("sitemap:"):
                candidates.append(line.split(":", 1)[1].strip())

        seen_maps, out, seen = set(), [], set()
        queue = deque(candidates)
        while queue and len(out) < limit:
            sm = queue.popleft()
            if sm in seen_maps:
                continue
            seen_maps.add(sm)
            body = fetch(sm)
            if not body or "<loc" not in body.lower():
                continue
            locs = _LOC.findall(body)
            is_index = "<sitemapindex" in body[:2000].lower()
            for loc in locs:
                if is_index:
                    if loc not in seen_maps and len(seen_maps) < 25:
                        queue.append(loc)
                    continue
                lp = urlparse(loc)
                if lp.netloc != p.netloc:
                    continue
                if root and root != "/" and not (lp.path or "/").startswith(root):
                    continue
                if (lp.path or "").lower().endswith(_SKIP_EXT):
                    continue
                clean = urldefrag(loc)[0]
                if clean not in seen:
                    seen.add(clean)
                    out.append(clean)
        if out:
            logger.info(f"[DOCS] sitemap gave {len(out)} page(s) for {entry_url}")
            try:
                if _c is not None:
                    _c.set(_ck, out, expire=86400)
            except Exception:
                pass
        return out
    except Exception as e:                   # pragma: no cover - defensive
        logger.warning(f"[DOCS] sitemap lookup failed: {e}")
        return []


def crawl(entry_url, fetch, max_pages=DEFAULT_MAX_PAGES,
          max_depth=DEFAULT_MAX_DEPTH, delay=CRAWL_DELAY, use_sitemap=True):
    """Breadth-first crawl of one documentation set.

    `fetch(url) -> html or None` is injected so this is testable without a
    network and so the caller owns rate limiting, caching and user-agent.

    BREADTH-FIRST ON PURPOSE. A depth-first crawl truncated at the page budget
    returns one deep branch and reports the subject as narrow; breadth-first
    truncated at the same budget covers every section shallowly, which is both
    a fairer sample and a better basis for judging scope.
    """
    root = doc_root(entry_url)
    if root is None:
        logger.warning(f"[DOCS] {entry_url} is not a recognisable doc root")
        return DocSet(entry_url, [])

    # Sitemap first — see `sitemap_urls` for why link-following alone reaches 3
    # pages of a 491-page doc set. The entry URL leads so the crawl still
    # starts where the caller pointed it.
    seeded, available = [], None
    if use_sitemap:
        all_urls = [u for u in sitemap_urls(entry_url, fetch) if u != entry_url]
        available = len(all_urls) + 1 if all_urls else None
        # INTERLEAVE BY SECTION. Sitemaps are emitted in alphabetical path
        # order, so taking the first N gives every page of "about-*" and
        # nothing from "deploy" or "build". Measured on dbt: the first 45 URLs
        # covered 5 of 36 sections. A sample that misses 31 sections describes
        # a different, much narrower subject than the one being taught, and
        # every downstream judgement — shape, scope, module list — inherits
        # that distortion. Round-robin across sections instead.
        buckets = {}
        for u in all_urls:
            buckets.setdefault(_section_of(u, root) or "", []).append(u)
        order = sorted(buckets, key=lambda s: (-len(buckets[s]), s))
        while any(buckets[s] for s in order):
            for s in order:
                if buckets[s]:
                    seeded.append(buckets[s].pop(0))

    # BULK-FETCH THE SITEMAP PAGES CONCURRENTLY.
    #
    # These are known up front, so there is no reason to discover them one at a
    # time: 491 pages fetched serially is ~6 minutes of pure waiting, and the
    # same set through a bounded, cached, robots-aware pool is a fraction of
    # that. Link-discovered pages still go through the serial queue below,
    # because their URLs are not known until their parents are read.
    prefetched = {}
    if seeded:
        try:
            try:  # container (flat layout: modules live at /app)
                from doc_fetch import PoliteFetcher
            except ImportError:  # imported as a package
                from services.research.doc_fetch import PoliteFetcher
            pf = PoliteFetcher()
            prefetched = pf.fetch_many([entry_url] + seeded[:max_pages])
            logger.info(f"[DOCS] prefetch {pf.stats}")
        except Exception as e:
            logger.warning(f"[DOCS] concurrent prefetch unavailable: {e}")

    def _get(u):
        if u in prefetched:
            return prefetched[u]
        return fetch(u)

    seen = {entry_url}
    queue = deque([(entry_url, 0)])
    for u in seeded:
        if u not in seen:
            seen.add(u)
            # Depth 0: sitemap entries are first-class pages, not discoveries,
            # so a depth cap meant for link-following must not exclude them.
            queue.append((u, 0))
    pages = []
    while queue and len(pages) < max_pages:
        url, depth = queue.popleft()
        html = _get(url)
        if delay and url not in prefetched:
            time.sleep(delay)
        if not html:
            continue
        text, n_code = _text_and_code(html)
        if len(text) >= MIN_PAGE_CHARS:
            pages.append({
                "url": url,
                "title": _page_title(html, url),
                "section": _section_of(url, root),
                "text": text,
                "code_blocks": n_code,
            })
        if depth < max_depth:
            for link in _links(html, url, root):
                if link not in seen:
                    seen.add(link)
                    queue.append((link, depth + 1))
    logger.info(f"[DOCS] {entry_url}: {len(pages)} page(s) of "
                f"{available or len(pages)} available, "
                f"{sum(p['code_blocks'] for p in pages)} code block(s)")
    return DocSet(entry_url, pages, available_pages=available)


def to_book(docset, title=None):
    """A `book_reader.Book` over a crawled documentation set.

    Returns None when the crawl found nothing usable, matching `open_book`'s
    contract so callers need no special case.
    """
    if not docset or not docset.pages:
        return None
    try:
        try:  # container (flat layout: modules live at /app)
            from book_reader import Book, Chapter
        except ImportError:  # imported as a package
            from services.research.book_reader import Book, Chapter
    except Exception as e:                   # pragma: no cover - defensive
        logger.warning(f"[DOCS] book_reader unavailable: {e}")
        return None
    chapters = [
        Chapter(title=p["title"], text=p["text"], order=i,
                part=p["section"], level=1)
        for i, p in enumerate(docset.pages)
    ]
    host = urlparse(docset.entry_url).netloc
    return Book(title or f"{host} documentation", chapters,
                source_path=docset.entry_url, fmt="docs")


# --- ordering -----------------------------------------------------------------
#
# DOCUMENTATION IS ORGANISED FOR REFERENCE, NOT FOR LEARNING.
#
# A book earns its order: the author decided chapter 3 follows chapter 2, with
# the whole subject in view. Documentation has no such sequence. Its nav is
# grouped by product surface, and its sitemap — which is how a JS-rendered site
# has to be enumerated at all — is emitted in ALPHABETICAL PATH ORDER.
#
# Taking that order literally produces a course that teaches "About MetricFlow"
# before "What is dbt?". `course_builder._looks_alphabetical` exists precisely
# because this failure has been seen before, and the builder already warns that
# an index "is not a teaching sequence".
#
# So a doc-sourced course must be SEQUENCED explicitly. The tiers below are
# documentation's own near-universal conventions — every doc set has an
# orientation section, a getting-started path, a conceptual core, task guides,
# and a reference tail — and ordering by them is deterministic, explainable and
# free. Within a tier the crawl order is preserved, because inside a section the
# publisher's ordering is usually meaningful even when the whole is not.

_TIERS = (
    # (tier, why, patterns matched against title + url path)
    # NOT a bare "about": dbt names advanced product areas "About MetricFlow",
    # "About dbt Mesh", "About static analysis". Matching `about\b` put ELEVEN
    # such pages in tier 0 and taught the semantic layer before installation.
    # Orientation has to be claimed by explicit orienting language.
    (0, "orientation — what this is and why",
     r"what[- ]is|^introduction|^intro\b|^overview|^welcome|getting to know"
     r"|core concepts?|key concepts?|why (use|choose)"),
    (1, "setup — you cannot practise without it",
     r"install|setup|set[- ]up|getting[- ]started|quickstart|quick[- ]start|prerequisite"),
    (2, "core tutorial — the guided first build",
     r"tutorial|guide\b|walkthrough|first[- ]|learn\b|basics|fundamental"),
    (3, "building — the everyday work of the subject",
     r"build|model|develop|write|create|author|syntax|structure"),
    (4, "testing and quality",
     r"test|quality|validat|lint|debug|assert|snapshot"),
    (5, "operating — running it for real",
     r"deploy|orchestrat|schedul|run\b|production|monitor|observ|environment"),
    (6, "advanced and integration",
     r"advanced|optimi|performance|scal|integrat|extend|plugin|adapter|mesh"),
    (7, "reference — looked up, not read through",
     r"reference|api\b|cli\b|command|configuration|config\b|spec\b|glossary|faq"),
    (8, "release notes and versions — not teaching material",
     r"release|changelog|version|upgrade|migrat|deprecat|whats[- ]new|security"),
)

_COMPILED_TIERS = [(n, why, re.compile(pat, re.I)) for n, why, pat in _TIERS]

#: Where a page goes when nothing matches: after the tutorial, before reference.
_DEFAULT_TIER = 3


#: Pages that are artifacts rather than documentation. Measured on dbt:
#: `_wizard-cli-full-generated` became a lesson in a real build.
_JUNK = re.compile(r"^_|--generated|_generated|\bsitemap\b|^index$|^404", re.I)


def is_junk(page):
    """A generated artifact or machine file, not a teachable page."""
    title = (page.get("title") or "").strip()
    slug = urlparse(page.get("url", "")).path.rstrip("/").split("/")[-1]
    return bool(_JUNK.search(title) or _JUNK.search(slug))


def tier_of(page):
    """(tier, why) for one crawled page."""
    hay = f"{page.get('title','')} {urlparse(page.get('url','')).path}"
    for n, why, pat in _COMPILED_TIERS:
        if pat.search(hay):
            return n, why
    return _DEFAULT_TIER, "unclassified — placed with the building material"


def looks_unsequenced(pages):
    """Is this crawl in reference order rather than teaching order?

    Reuses the builder's own detector so doc courses and researched courses
    answer the question the same way.
    """
    titles = [p.get("title", "") for p in pages]
    try:
        from services.core.course_builder import _looks_alphabetical
        return _looks_alphabetical(titles)
    except Exception:                        # pragma: no cover - defensive
        items = [t.strip().lower() for t in titles][:25]
        if len(items) < 5:
            return False
        ordered = sum(1 for a, b in zip(items, items[1:]) if a <= b)
        return ordered / max(1, len(items) - 1) >= 0.9


def sequence(pages):
    """Pages reordered into a teaching sequence, with the reason recorded.

    Returns (ordered_pages, report). The report travels with the course so the
    ordering is auditable rather than mysterious — the same discipline
    `book_skeleton.choose_shape` follows in recording a *why* for its shape.

    Stable within a tier: `sorted` with a tier key preserves crawl order for
    equal keys, so a publisher's meaningful intra-section ordering survives.
    """
    kept = [p for p in (pages or []) if not is_junk(p)]
    dropped = len(pages or []) - len(kept)
    annotated = []
    for i, p in enumerate(kept):
        t, why = tier_of(p)
        q = dict(p)
        q["_tier"], q["_tier_why"] = t, why
        annotated.append((t, i, q))
    ordered = [q for _, _, q in sorted(annotated, key=lambda x: (x[0], x[1]))]
    counts = {}
    for _, _, q in annotated:
        counts.setdefault(q["_tier"], 0)
        counts[q["_tier"]] += 1
    report = {
        "was_unsequenced": looks_unsequenced(pages),
        "junk_dropped": dropped,
        "method": "documentation-convention tiers",
        "tiers": {str(n): {"pages": counts.get(n, 0), "why": why}
                  for n, why, _ in _COMPILED_TIERS if counts.get(n)},
        "first": [p["title"] for p in ordered[:5]],
        "last": [p["title"] for p in ordered[-3:]],
    }
    return ordered, report
