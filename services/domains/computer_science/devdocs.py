"""DevDocs as a source: curated documentation for ~824 technologies.

WHY THIS BEATS CRAWLING
-----------------------
The doc crawler works, but it is doing a hard job: enumerate a site via its
sitemap, fetch politely, strip chrome, recover code blocks from whatever markup
the publisher's generator emitted. DevDocs has already done all of that, for
824 technologies, and publishes the result as JSON:

    index.json -> {"entries": [{"name", "path", "type"}], "types": [...]}
    db.json    -> {path: cleaned_html}

Three things that buys:

  * **No chrome to strip.** The content is already the content.
  * **Curated grouping.** `types` are editorial groupings — "Block Elements",
    "Built-in Functions", "Statements" — which map onto course units far better
    than a URL path segment does. The crawler had to infer sections from
    `/docs/build/...`; here they are stated.
  * **One request per version.** No 491-page crawl, no politeness budget, no
    robots question.

MEMBERSHIP IS THE CLASSIFIER
----------------------------
"Teach me Rust" and "teach me recursion" need completely different pipelines,
and telling them apart by keyword is guesswork. DevDocs answers it as a fact:
`python` and `rust` are in the manifest, `recursion` is not — because DevDocs
indexes TECHNOLOGIES THAT SHIP DOCUMENTATION, which is exactly the distinction.

So `classify_subject` returns:

    TECHNOLOGY  — in DevDocs; use this module
    TOOL        — not in DevDocs but resolves to a real doc site (dbt is here);
                  use the doc crawler
    CONCEPT     — neither; recursion, Big-O, design patterns. These have no
                  single authoritative site and must go through the RESEARCHED
                  path, which gathers several accounts and synthesises.

Getting that wrong is expensive in both directions: crawling for "recursion"
finds a blog and builds a course from one person's opinion, and researching
"Rust" ignores the authoritative source sitting right there.
"""
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

MANIFEST_URL = "https://devdocs.io/docs.json"
DOCS_HOST = "https://documents.devdocs.io"

#: Manifest changes rarely; a day is generous and keeps a build offline-fast.
MANIFEST_TTL = 86400
#: Content is versioned by `mtime` in the URL, so a long TTL is safe — a new
#: release produces a different key rather than a stale hit.
CONTENT_TTL = 2592000        # 30 days

TECHNOLOGY, TOOL, CONCEPT = "TECHNOLOGY", "TOOL", "CONCEPT"

_TAG = re.compile(r"<[^>]+>")


def _cache():
    try:
        from services.research.research_server import cache
        return cache
    except Exception:
        try:
            from diskcache import Cache
            return Cache(os.environ.get("HELGA_CACHE_DIR", "/tmp/helga-doc-cache"))
        except Exception:                    # pragma: no cover - defensive
            return None


def _get(url, timeout=45):
    import urllib.request
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Helga-Research/1.0 (course builder)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return None
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        logger.debug(f"[DEVDOCS] {url}: {type(e).__name__}")
        return None


def manifest(refresh=False):
    """Every documentation set DevDocs publishes. [] if unreachable."""
    c = _cache()
    key = "devdocs:manifest"
    if c is not None and not refresh:
        try:
            hit = c.get(key)
            if hit:
                return hit
        except Exception:
            pass
    body = _get(MANIFEST_URL)
    if not body:
        return []
    try:
        docs = json.loads(body)
    except Exception:
        return []
    if c is not None:
        try:
            c.set(key, docs, expire=MANIFEST_TTL)
        except Exception:
            pass
    return docs


def _norm(s):
    return re.sub(r"[^a-z0-9+#.]+", " ", (s or "").lower()).strip()


def find(subject, docs=None):
    """The best DevDocs entry for `subject`, or None.

    Prefers the UNVERSIONED slug (`python`) over a pinned one (`python~3.11`):
    a course should teach the current release unless the learner asked for a
    specific one, and `python~3.9` would quietly teach a deprecated dialect.
    """
    docs = docs if docs is not None else manifest()
    if not docs:
        return None
    want = _norm(subject)
    if not want:
        return None
    exact, prefixed = [], []
    for d in docs:
        name = _norm(d.get("name"))
        slug = d.get("slug", "")
        alias = _norm(d.get("alias") or "")
        if name == want or alias == want:
            exact.append(d)
        elif want and (want in name.split() or name in want.split()):
            prefixed.append(d)
    pool = exact or prefixed
    if not pool:
        return None
    # Unversioned slug first, then the highest version.
    pool.sort(key=lambda d: ("~" in d.get("slug", ""),
                             -(d.get("mtime") or 0)))
    return pool[0]


def classify_subject(subject, doc_resolver=None):
    """TECHNOLOGY, TOOL or CONCEPT — which pipeline this subject needs.

    `doc_resolver(subject) -> url or None` is injected (normally
    `doc_reader.resolve`) so this module does not depend on the crawler, and so
    a caller can test the three branches without a network.
    """
    # Cheap set lookup first: a miss skips the 824-entry scan entirely.
    if has(subject) and find(subject):
        return TECHNOLOGY
    if doc_resolver:
        try:
            if doc_resolver(subject):
                return TOOL
        except Exception:                    # pragma: no cover - defensive
            pass
    return CONCEPT


def _heading_of(html):
    """The page's own <h1>/<h2>, or ""."""
    try:
        from bs4 import BeautifulSoup
        h = BeautifulSoup(html or "", "html.parser").find(["h1", "h2"])
        return h.get_text(" ", strip=True) if h else ""
    except Exception:                        # pragma: no cover - defensive
        m = re.search(r"<h[12][^>]*>(.*?)</h[12]>", html or "", re.I | re.S)
        return _TAG.sub("", m.group(1)).strip() if m else ""


def _text_and_code(html):
    """DevDocs HTML -> prose with fenced code. Same contract as doc_reader."""
    if not html:
        return "", 0
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        n = 0
        for pre in soup.find_all("pre"):
            # Shared with doc_reader: line-level elements make lines,
            # inline spans do not. Neither separator alone is correct.
            from services.research.doc_reader import _code_text
            code = _code_text(pre)
            if not code.strip():
                pre.decompose()
                continue
            lang = ""
            cls = " ".join(pre.get("class") or [])
            m = re.search(r"language-([\w+#.-]+)", cls)
            if m:
                lang = m.group(1).lower()
            n += 1
            pre.replace_with(f"\n\n```{lang}\n{code}\n```\n\n")
        text = soup.get_text("\n")
    except Exception:                        # pragma: no cover - defensive
        text, n = _TAG.sub(" ", html), 0
    try:
        from services.research.doc_reader import _tidy_prose
        text = _tidy_prose(text)
    except Exception:
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, n


def pages_for(subject, max_pages=200):
    """Documentation pages for `subject`, in the doc_reader page shape.

    Returns (pages, meta) or ([], {}). Each page is
    {url, title, section, text, code_blocks} — deliberately identical to what
    `doc_reader.crawl` produces, so `to_book`, `sequence` and the curriculum
    synthesiser all work on it unchanged.

    `section` comes from DevDocs' own `type` grouping, which is editorial
    rather than inferred from a URL — the crawler has to guess sections from
    path segments and gets `Dbt Apis` and `Fusion`; this gets `Built-in
    Functions` and `Statements`.
    """
    entry = find(subject)
    if not entry:
        return [], {}
    slug = entry["slug"]
    mtime = entry.get("mtime") or ""
    c = _cache()
    ckey = f"devdocs:pages:{slug}:{mtime}"
    if c is not None:
        try:
            hit = c.get(ckey)
            if hit:
                return hit[0], hit[1]
        except Exception:
            pass

    idx_raw = _get(f"{DOCS_HOST}/{slug}/index.json?{mtime}")
    db_raw = _get(f"{DOCS_HOST}/{slug}/db.json?{mtime}", timeout=90)
    if not idx_raw or not db_raw:
        logger.info(f"[DEVDOCS] could not fetch {slug}")
        return [], {}
    try:
        idx = json.loads(idx_raw)
        db = json.loads(db_raw)
    except Exception:
        return [], {}

    # One page per PATH, not per entry: several entries commonly share a page
    # (`index#autolink`, `index#header`), and one page per anchor would produce
    # a course of fragments.
    by_path = {}
    for e in idx.get("entries", []):
        path = (e.get("path") or "").split("#")[0]
        if not path:
            continue
        by_path.setdefault(path, {"names": [], "type": e.get("type") or ""})
        by_path[path]["names"].append(e.get("name") or "")

    # PAGES THE INDEX DOES NOT LINK TO.
    #
    # `index.json` lists searchable ENTRIES, and `db.json` holds PAGES. They
    # are not the same set: measured on redux, db.json carried a page (`index`
    # — the overview) that no entry referenced, and dropping it lost exactly
    # the orientation content a course should open with. So the union is taken,
    # with entry metadata where it exists and the page's own heading where it
    # does not.
    for path in db:
        clean = path.rstrip("/")
        if clean not in by_path and path not in by_path:
            by_path[path] = {"names": [], "type": ""}

    pages = []
    for path, info in list(by_path.items())[:max_pages]:
        html = db.get(path) or db.get(path + "/") or ""
        if not html:
            continue
        text, n_code = _text_and_code(html)
        if len(text) < 200:
            continue
        # TITLE FROM THE PAGE, NOT FROM ONE OF ITS ANCHORS.
        #
        # Five of redux's eleven entries are `#anchor` links into a shared
        # page. Naming that page after whichever anchor happened to be first
        # describes a fragment, not the lesson. The page's own <h1> is what the
        # author called it; entry names are the fallback.
        title = _heading_of(html) or (info["names"][0] if info["names"] else "")
        if not title:
            title = path.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ")
        pages.append({
            "url": f"https://devdocs.io/{slug}/{path}",
            "title": title.strip()[:120],
            "section": (info["type"] or "").strip() or None,
            # Anchored entries are the page's own contents list — useful to the
            # curriculum synthesiser, which can see what a page actually covers
            # instead of inferring it from a title.
            "entries": [n for n in info["names"] if n][:12],
            "text": text,
            "code_blocks": n_code,
        })
    meta = {
        "source": "devdocs",
        "slug": slug,
        "name": entry.get("name"),
        "version": entry.get("version") or entry.get("release"),
        "attribution": entry.get("attribution"),
        "types": [t.get("name") for t in idx.get("types", [])],
        "entries": len(idx.get("entries", [])),
    }
    if c is not None and pages:
        try:
            c.set(ckey, (pages, meta), expire=CONTENT_TTL)
        except Exception:
            pass
    logger.info(f"[DEVDOCS] {slug}: {len(pages)} page(s), "
                f"{sum(p['code_blocks'] for p in pages)} code block(s)")
    return pages, meta


# --- fast availability index ------------------------------------------------
#
# WHY A LOCAL INDEX AND NOT JUST THE MANIFEST
#
# `manifest()` is 824 entries and ~200 KB. Cached, it is fast — but the FIRST
# call on a cold cache is a network round-trip, and it happens at the moment a
# learner asks for a course. Worse, the answer we usually need is a single bit:
# "is this subject here at all?" Fetching 200 KB of JSON to learn that Rust is
# present, or that recursion is not, is the expensive way to ask a cheap
# question.
#
# So the names and aliases are distilled to a small set, persisted, and used
# for the yes/no. A miss short-circuits straight to the crawler or the
# researched path without touching the network at all — which is the case that
# matters, because most subjects are NOT in DevDocs.

_INDEX_KEY = "devdocs:index:v1"
_INDEX_TTL = 86400
_MEM_INDEX = {"names": None}


def _build_index(docs=None):
    docs = docs if docs is not None else manifest()
    names = set()
    for d in docs or ():
        for field in ("name", "alias"):
            v = _norm(d.get(field))
            if v:
                names.add(v)
                # First token too: "Node.js" should answer to "node", and
                # "Ruby on Rails" to "rails" — a learner does not type the
                # publisher's full product name.
                parts = v.split()
                if len(parts) > 1:
                    names.add(parts[0])
                    names.add(parts[-1])
            # "Node.js" normalises to "node.js", which never splits on
            # whitespace — so a learner typing "node" missed it entirely.
            if "." in v:
                head = v.split(".")[0].strip()
                if len(head) > 1:
                    names.add(head)
    return names


def index(refresh=False):
    """The set of subject names DevDocs can answer for. Cheap and cached."""
    if _MEM_INDEX["names"] is not None and not refresh:
        return _MEM_INDEX["names"]
    c = _cache()
    if c is not None and not refresh:
        try:
            hit = c.get(_INDEX_KEY)
            if hit:
                _MEM_INDEX["names"] = set(hit)
                return _MEM_INDEX["names"]
        except Exception:
            pass
    names = _build_index()
    if names:
        _MEM_INDEX["names"] = names
        if c is not None:
            try:
                c.set(_INDEX_KEY, list(names), expire=_INDEX_TTL)
            except Exception:
                pass
    return names or set()


def has(subject):
    """Is `subject` in DevDocs at all? One set lookup, no network on a warm index.

    The fast NO is the point. `find()` scans 824 dicts and `pages_for()` fetches
    two documents; this answers "skip DevDocs, go elsewhere" immediately, which
    is the answer for most subjects a learner will ask about.
    """
    want = _norm(subject)
    if not want:
        return False
    names = index()
    if want in names:
        return True
    toks = want.split()
    return any(t in names for t in toks if len(t) > 2)
