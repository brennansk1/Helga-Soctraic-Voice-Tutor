"""Read a documentation SET, not one page of it.

THE GAP THIS CLOSES
-------------------
`ranking.is_documentation()` classifies a URL as official docs and weights it
0.30 (capped 0.60) — the highest non-Wikipedia weight in the system. But
classification was all it did. Fetching was unchanged: `extract_page` pulls ONE
url, keeps 6000 characters, and calls `trafilatura.extract(..., include_links=
False)` — which discards the very links that ARE a documentation site's
structure.

So for anything whose documentation spans pages — the Python tutorial, the dbt
docs, a library's guide — a course was built from one page of one search hit
and recorded as "grounded in documentation". The confidence weight said 0.30;
the coverage did not earn it.

WHY A BOUNDED CRAWL AND NOT A GENERAL ONE
-----------------------------------------
A general crawler is the wrong tool: it wanders off-topic, it is slow, and it
is rude to the host. Documentation has structure that makes a *bounded* crawl
both sufficient and safe:

  * it lives under a common path root (/docs/, /guide/, /reference/)
  * its navigation links to its own sibling and child pages
  * it is finite and small — tens of pages, not millions

So: same host, same path root, one hop from the entry page, hard page cap.
That reaches the sections a guide actually links to without becoming a spider.

WHAT IT REFUSES TO DO
---------------------
Off-host links, links above the doc root, non-HTML assets, anything past the
page cap, and any URL when the entry page is NOT classed as documentation.
When discovery yields nothing it returns just the entry page, so the caller's
contract is unchanged and a site with unusual markup degrades to today's
behaviour rather than breaking.
"""
import logging
import re
from urllib.parse import urljoin, urlparse, urldefrag

logger = logging.getLogger(__name__)

#: Hard cap on pages fetched per documentation source. Ten pages of a guide is
#: far more than the 6000 chars a single page contributed, and still a polite
#: number of requests to make of somebody's docs host for one concept.
MAX_PAGES = 10

#: Characters kept per page. Lower than the 6000 a lone page gets, because ten
#: pages at 6000 would swamp the concept document; the hydrator's budget is
#: ~3000 characters after packing.
PER_PAGE_CHARS = 2500

#: Total across the set, so a doc-heavy concept cannot crowd out every other
#: source in the combined research text.
TOTAL_CHARS = 18000

#: Path segments that mark a documentation root. A link is only followed if it
#: shares the entry URL's root, which is what keeps the crawl inside the docs
#: instead of wandering into a marketing site or a blog.
_DOC_ROOTS = ("/docs", "/doc", "/guide", "/guides", "/reference", "/manual",
              "/tutorial", "/tutorials", "/learn", "/api", "/handbook",
              "/documentation", "/latest", "/stable", "/en/")

#: Things that are not prose pages.
_SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".zip", ".gz",
             ".js", ".css", ".ico", ".woff", ".woff2", ".xml", ".json")

_HREF = re.compile(r'href=["\']([^"\'#>]+)', re.I)


def _doc_root(url):
    """The path prefix a sibling page must share, or None.

    For https://docs.example.com/guide/models/intro this returns "/guide",
    so /guide/models/tests is a sibling but /pricing is not.
    """
    try:
        path = urlparse(url).path or "/"
        low = path.lower()
        for root in _DOC_ROOTS:
            i = low.find(root)
            if i != -1:
                return path[:i + len(root)]
        # A host that is entirely documentation (docs.foo.com/x) has no marker
        # segment; the root is then the host itself, expressed as "/".
        host = (urlparse(url).netloc or "").lower()
        if host.startswith("docs.") or host.startswith("readthedocs"):
            return "/"
        return None
    except Exception:                        # pragma: no cover - defensive
        return None


def discover(entry_url, html):
    """Sibling/child documentation URLs linked from `html`, in page order.

    Deduplicated, fragment-stripped, capped. Returns [] when the entry URL is
    not recognisably a documentation page — the caller then behaves exactly as
    it did before this module existed.
    """
    root = _doc_root(entry_url)
    if root is None or not html:
        return []
    try:
        base = urlparse(entry_url)
        seen, out = set(), []
        for href in _HREF.findall(html):
            if href.lower().startswith(("mailto:", "javascript:", "tel:")):
                continue
            absolute = urldefrag(urljoin(entry_url, href))[0]
            p = urlparse(absolute)
            if p.scheme not in ("http", "https"):
                continue
            if p.netloc != base.netloc:      # never leave the host
                continue
            if not (p.path or "/").startswith(root):
                continue                     # never climb above the doc root
            if p.path.lower().endswith(_SKIP_EXT):
                continue
            if absolute == entry_url or absolute in seen:
                continue
            seen.add(absolute)
            out.append(absolute)
            if len(out) >= MAX_PAGES:
                break
        return out
    except Exception:                        # pragma: no cover - defensive
        return []


def combine(pages):
    """Join fetched (url, title, text) tuples into one grounded block.

    Each page keeps its own heading so a citation can point at the page that
    actually said a thing, rather than at the doc set as a whole.
    """
    parts, total = [], 0
    for url, title, text in pages:
        if not text:
            continue
        body = text.strip()[:PER_PAGE_CHARS]
        chunk = f"### {title or url}\n{body}"
        if total + len(chunk) > TOTAL_CHARS:
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n\n".join(parts)
