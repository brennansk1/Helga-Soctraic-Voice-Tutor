"""Domain-routed public sources: the right archive for the right subject.

WHY ROUTED AND NOT GLOBAL
-------------------------
Querying every source for every concept is worse than not having them. A
biology concept has no use for a museum object, and a hit returned anyway is
not neutral: it costs latency, and it adds a citation that inflates grounding
confidence while teaching nothing. That is how a scoring system starts
manufacturing confidence instead of measuring it.

So each source declares the domains it serves, and a concept only queries the
sources that match.

EVERY ENDPOINT HERE WAS PROBED BEFORE BEING WIRED (2026-08-04). Three of the
candidates did not work as published and are recorded so nobody re-adds them
from a stale list:

  - Chronicling America's legacy API (chroniclingamerica.loc.gov/search/...)
    was RETIRED in 2025 and 404s. The collection now lives behind the loc.gov
    JSON API, which is what this module uses.
  - Open Library's search.json HANGS — openlibrary.org itself answers in
    0.18 s, but the search endpoint did not respond in 45 s across repeated
    attempts. Internet Archive is the same corpus through a working door.
  - Data USA's documented endpoint 404s.

No key, no login, no rate-limit tier for anything below.
"""

import logging
import re
import time

logger = logging.getLogger(__name__)

try:  # container (flat)
    from syllabus_sources import _get_json
except ImportError:  # imported as a package
    from services.research.syllabus_sources import _get_json


# --- per-process result memo --------------------------------------------------
#
# These lookups run once per CONCEPT during hydration, and Wikidata serves every
# domain, so a 12-concept module made 12 Wikidata calls. Whenever a lookup falls
# back to the module or course subject — which is the common case for a concept
# whose title is a pedagogical task — those calls are byte-identical, and each
# one is a real round trip against a public API with no key and no rate-limit
# tier of ours. The research service is a long-lived process, so a small memo
# removes them.
#
# Deliberately NOT the diskcache used in research_server: this module is
# imported by the core service too (dual import shape), and it must not depend
# on a cache directory existing.

_MEMO_TTL = 6 * 3600
_MEMO_MAX = 512
_memo = {}


def _memoized(fn):
    """Cache a source lookup's result for this process, briefly."""
    def wrapper(query, limit=2, **kw):
        key = (fn.__name__, query, limit)
        hit = _memo.get(key)
        now = time.time()
        if hit and now - hit[0] < _MEMO_TTL:
            return hit[1]
        out = fn(query, limit=limit, **kw)
        if len(_memo) >= _MEMO_MAX:
            _memo.clear()          # crude, bounded, and never wrong
        _memo[key] = (now, out)
        return out
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    wrapper.__wrapped__ = fn
    return wrapper


# --- domain classification ---------------------------------------------------
#
# Deliberately keyword-based rather than an LLM call. This runs per concept
# during hydration, and a 4.5 s call to decide whether to query a museum would
# cost more than the museum lookup saves. Misrouting is cheap: the source
# simply returns nothing.

DOMAIN_KEYWORDS = {
    "art": ("art", "painting", "sculpture", "artist", "renaissance", "baroque",
            "impressionis", "modernis", "gallery", "museum", "aesthetic",
            "drawing", "portrait", "landscape art", "art history",
            # QUALIFIED ON PURPOSE. Bare "design" and "architecture" are art
            # words that computing uses constantly — "Constraint Design",
            # "Software Architecture", "design patterns", "schema design" —
            # and routing those to museum archives is how an SQL course ends
            # up citing the Met. Art history keeps plenty of unambiguous
            # signals (painting, sculpture, renaissance, gallery, artist), so
            # these two earn their place only in an art-shaped phrase.
            "graphic design", "design history", "art and design",
            "architectural history", "architecture of the", "photography"),
    "history": ("history", "historical", "war", "revolution", "century",
                "ancient", "medieval", "colonial", "empire", "civilization",
                "civilisation", "dynasty", "treaty", "suffrage", "movement",
                "reconstruction", "depression", "immigration"),
    "philosophy": ("philosophy", "ethic", "moral", "metaphysic", "epistem",
                   # "logic" is qualified for the same reason as "design":
                   # computing is full of "logical operators", "logical
                   # validation", "logical plan", none of which wants a
                   # philosophy archive.
                   "formal logic", "symbolic logic", "philosophical logic",
                   "existential", "phenomenolog", "aesthetics",
                   "political theory", "rationality", "consciousness"),
    "science": ("biology", "chemistry", "physics", "geology", "astronomy",
                "ecology", "genetic", "molecul", "organism", "quantum",
                "thermodynamic", "evolution", "neuroscience", "anatomy"),
    "social": ("economic", "sociolog", "psycholog", "demograph", "statistic",
               "policy", "population", "census", "inequality", "labor",
               "labour", "market", "poverty", "education system"),
}


# WORD STARTS, NOT SUBSTRINGS.
#
# This matched with `w in blob`, so "art" matched "P-ART-itioning" and every
# window-function concept in an SQL course routed to the art archives. Measured
# on 2026-08-25: classify_domains('Tie Interaction', 'Window Function Frame
# Semantics and Partitioning', 'advanced sql') returned ['art'], and the
# resulting course cited the Metropolitan Museum of Art for SQL window frames.
# "war" inside "software" does the same thing to history, and "logic" inside
# "logical" to philosophy.
#
# The stems here are deliberate — "impressionis", "sociolog", "epistem" are
# meant to catch their whole families — so the anchor is at the START of a word
# only, which keeps every intended match and drops the accidental ones.
_DOMAIN_PATTERNS = {
    # A BOUNDED SUFFIX, NOT AN OPEN ONE.
    #
    # A word-start anchor alone still matches "war" inside "WAR-ehouse", which
    # sent a data-warehouse course to the history archives. The entries here
    # are a mix of whole words ("war", "art") and deliberate stems
    # ("impressionis", "sociolog", "epistem"), so the rule allows a short
    # inflection and no more: sociolog+ical and epistem+ology match, war+ehouse
    # (six letters) does not.
    d: re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words)
                  + r")\w{0,5}\b", re.IGNORECASE)
    for d, words in DOMAIN_KEYWORDS.items()
}


def classify_domains(*texts):
    """Which domains a topic plausibly belongs to. May be several, or none."""
    blob = " ".join(t for t in texts if t).lower()
    hits = {d for d, pat in _DOMAIN_PATTERNS.items() if pat.search(blob)}
    return sorted(hits)


# --- sources -----------------------------------------------------------------

@_memoized
def met_museum(query, limit=3):
    """Metropolitan Museum of Art — objects with curatorial description.

    Art history is genuinely taught from primary artefacts, which is why this
    earns a place where a general web search would not.
    """
    ids = _get_json("https://collectionapi.metmuseum.org/public/collection/v1/search",
                    {"q": query, "hasImages": "true"}, timeout=15)
    if not ids or not ids.get("objectIDs"):
        return []
    out = []
    for oid in (ids["objectIDs"] or [])[:limit]:
        obj = _get_json(
            f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}",
            {}, timeout=15)
        if not obj or not obj.get("title"):
            continue
        bits = [obj.get("artistDisplayName"), obj.get("objectDate"),
                obj.get("medium"), obj.get("culture")]
        out.append({
            "type": "artefact", "source": "Met Museum",
            "title": obj["title"][:160],
            "url": obj.get("objectURL") or "",
            "text": " — ".join(b for b in bits if b)[:600],
        })
    return out


@_memoized
def art_institute(query, limit=3):
    """Art Institute of Chicago — artworks with descriptive text."""
    data = _get_json("https://api.artic.edu/api/v1/artworks/search",
                     {"q": query, "limit": limit,
                      "fields": "id,title,artist_display,date_display,medium_display"},
                     timeout=15)
    if not data:
        return []
    out = []
    for a in data.get("data", [])[:limit]:
        if not a.get("title"):
            continue
        bits = [a.get("artist_display"), a.get("date_display"),
                a.get("medium_display")]
        out.append({
            "type": "artefact", "source": "Art Institute of Chicago",
            "title": a["title"][:160],
            "url": f"https://www.artic.edu/artworks/{a.get('id')}",
            "text": " — ".join(b for b in bits if b)[:600],
        })
    return out


@_memoized
def loc_chronicling_america(query, limit=3):
    """Historical US newspapers via the loc.gov JSON API.

    History is genuinely taught from primary sources. The legacy
    chroniclingamerica.loc.gov API was retired in 2025; this is the current
    endpoint and was verified returning results.
    """
    data = _get_json("https://www.loc.gov/collections/chronicling-america/",
                     {"qs": query, "dl": "page", "c": limit, "fo": "json"},
                     timeout=25)
    if not data:
        return []
    out = []
    for r in (data.get("results") or [])[:limit]:
        title = r.get("title")
        if not title:
            continue
        out.append({
            "type": "primary_document", "source": "Library of Congress",
            "title": str(title)[:160],
            "url": r.get("id") or r.get("url") or "",
            "text": " ".join((r.get("description") or []))[:600]
                    if isinstance(r.get("description"), list)
                    else str(r.get("description") or "")[:600],
        })
    return out


@_memoized
def wikidata_facts(query, limit=1):
    """Structured entity facts — dates, classifications, relations.

    Useful as a factual spine: an LLM writing about an entity gets its
    identifiers and classification rather than recalling them.
    """
    hits = _get_json("https://www.wikidata.org/w/api.php",
                     {"action": "wbsearchentities", "search": query,
                      "language": "en", "format": "json", "limit": limit},
                     timeout=15)
    if not hits:
        return []
    out = []
    for h in (hits.get("search") or [])[:limit]:
        desc = h.get("description")
        if not desc:
            continue
        out.append({
            "type": "structured_fact", "source": "Wikidata",
            "title": h.get("label") or query,
            "url": h.get("concepturi") or "",
            "text": desc[:400],
        })
    return out



# --- open educational / scientific sources (2026-08-19) ----------------------
#
# The hydration research asked for sources that fill the SOURCELESS case, which
# is the weakest path in the pipeline. Each of these was chosen for three
# properties the existing set already demands: a machine-readable or blanket
# open licence, no key, and a stable endpoint.
#
# LICENCE NOTES THAT COST REAL MONEY TO GET WRONG:
#   * OpenAlex is CC0 with no restrictions, and offers a full bulk snapshot --
#     the best fit for a system that must work offline.
#   * PubChem is public domain (US government).
#   * arXiv METADATA is CC0 and safe; arXiv FULL TEXT is not ours to
#     redistribute, so only abstracts are taken here.
#   * Stack Exchange dumps are deliberately NOT wired: the content is CC-BY-SA
#     but since 2024 the dump terms exclude commercial use, redistribution and
#     LLM training. That is a licence trap, not an oversight.
#   * NC and ND variants are avoided everywhere, as elsewhere in this project.


_TERM_STOP = {"the", "and", "for", "with", "from", "into", "common", "basic",
              "introduction", "using", "your", "what", "how", "why", "this",
              "that", "data", "type", "types", "value", "values"}


def _distinctive_terms(text):
    """Content words worth matching on. Plurals folded to a stem."""
    import re as _re
    out = set()
    for w in _re.findall(r"[a-z]+", (text or "").lower()):
        if len(w) < 4 or w in _TERM_STOP:
            continue
        out.add(w[:-1] if w.endswith("s") and len(w) > 4 else w)
    return out


def _is_on_topic(query, title, min_hits=2):
    """Does this result share enough with the query to be about it?

    FREE-TEXT SCHOLARLY SEARCH IS POLYSEMOUS AND CONFIDENT ABOUT IT.
    Measured: OpenAlex answers "Common Table Expressions" with "limma powers
    differential expression analyses for RNA-sequencing" at relevance 1212 —
    it matched "expression" in the molecular-biology sense, and the paper
    outranks everything because it is heavily cited. Two genomics papers
    reached a SQL course that way, weighted 0.25 each as real evidence.

    One shared word is not aboutness; the biology paper shares "expression"
    too. Requiring TWO distinctive terms separates "Pushing Predicates into
    Recursive SQL Common Table Expressions" from "The UCSC Table Browser",
    which shares only "table". Concepts with a single distinctive term fall
    back to one, because for those it is all the signal there is.
    """
    q = _distinctive_terms(query)
    if not q:
        return True
    need = min(min_hits, len(q))
    return len(q & _distinctive_terms(title)) >= need


def openalex_works(query, limit=3):
    """OpenAlex — CC0 scholarly metadata and abstracts, no key, no tier.

    The broadest single answer to "this subject has no textbook": almost every
    field has literature, and CC0 means the abstract can be used without any
    downstream licence question.
    """
    data = _get_json("https://api.openalex.org/works",
                     {"search": query, "per-page": limit,
                      "mailto": "helga@localhost"}, timeout=20)
    if not data:
        return []
    out = []
    for w in (data.get("results") or [])[:limit]:
        # OpenAlex stores abstracts as an inverted index to sidestep publisher
        # restrictions, so it has to be rebuilt into prose.
        inv = w.get("abstract_inverted_index") or {}
        text = ""
        if inv:
            positions = {}
            for word, idxs in inv.items():
                for i in idxs:
                    positions[i] = word
            text = " ".join(positions[i] for i in sorted(positions))
        if not text:
            continue
        name = w.get("display_name") or ""
        if not _is_on_topic(query, name):
            logger.debug("openalex: dropped off-topic %r for %r", name[:60], query)
            continue
        out.append({
            "type": "scholarly", "source": "OpenAlex",
            "title": name or query,
            "url": w.get("doi") or w.get("id") or "",
            "text": text[:600], "license": "CC0",
        })
    return out


def pubchem_compound(query, limit=1):
    """PubChem — public-domain chemistry ground truth."""
    data = _get_json(
        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{query}/JSON",
        {}, timeout=20)
    if not data:
        return []
    out = []
    for c in (data.get("PC_Compounds") or [])[:limit]:
        props = {}
        for p in (c.get("props") or []):
            label = (p.get("urn") or {}).get("label")
            val = (p.get("value") or {})
            v = val.get("sval") or val.get("fval") or val.get("ival")
            if label and v is not None:
                props[label] = v
        if not props:
            continue
        cid = ((c.get("id") or {}).get("id") or {}).get("cid")
        out.append({
            "type": "structured_fact", "source": "PubChem",
            "title": f"{query} (PubChem)",
            "url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}" if cid else "",
            "text": "; ".join(f"{k}: {v}" for k, v in list(props.items())[:6])[:400],
            "license": "public domain",
        })
    return out


def arxiv_abstracts(query, limit=2):
    """arXiv ABSTRACTS only. Metadata is CC0; full text is not ours to mirror.

    Deliberately not fetching PDFs. The default arXiv licence grants arXiv
    distribution rights, not ours, and a system that generates teaching material
    from mirrored papers would be redistributing them by another name.
    """
    import re as _re
    from urllib.parse import urlencode
    import urllib.request
    url = ("http://export.arxiv.org/api/query?"
           + urlencode({"search_query": f"all:{query}", "max_results": limit}))
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            xml = r.read().decode("utf-8", "replace")
    except Exception as e:
        logger.debug(f"arxiv failed for {query!r}: {e}")
        return []
    out = []
    for entry in _re.findall(r"<entry>(.*?)</entry>", xml, _re.DOTALL)[:limit]:
        t = _re.search(r"<title>(.*?)</title>", entry, _re.DOTALL)
        a = _re.search(r"<summary>(.*?)</summary>", entry, _re.DOTALL)
        u = _re.search(r"<id>(.*?)</id>", entry, _re.DOTALL)
        if not (t and a):
            continue
        out.append({
            "type": "scholarly", "source": "arXiv",
            "title": " ".join(t.group(1).split())[:200],
            "url": u.group(1).strip() if u else "",
            "text": " ".join(a.group(1).split())[:600],
            "license": "metadata CC0; abstract only",
        })
    return out


def gutenberg_texts(query, limit=2):
    """Project Gutenberg via Gutendex — public-domain full texts.

    The humanities counterpart to OpenAlex: strong where a subject is old
    enough to be out of copyright and poorly served by open textbooks.
    """
    data = _get_json("https://gutendex.com/books", {"search": query}, timeout=20)
    if not data:
        return []
    out = []
    for b in (data.get("results") or [])[:limit]:
        authors = ", ".join(a.get("name", "") for a in (b.get("authors") or []))
        out.append({
            "type": "book", "source": "Project Gutenberg",
            "title": b.get("title") or query,
            "url": (b.get("formats") or {}).get("text/html") or "",
            "text": f"{b.get('title')} by {authors}. Subjects: "
                    + "; ".join((b.get("subjects") or [])[:6]),
            "license": "public domain",
        })
    return out


def oeis_sequence(query, limit=1):
    """OEIS — the authoritative reference for integer sequences.

    Narrow but unmatched where it applies: a concept about Fibonacci or Catalan
    numbers gets the canonical definition and terms rather than a recollection.
    """
    data = _get_json("https://oeis.org/search",
                     {"q": query, "fmt": "json"}, timeout=20)
    if not data:
        return []
    results = data if isinstance(data, list) else (data.get("results") or [])
    out = []
    for r in (results or [])[:limit]:
        if not isinstance(r, dict) or not r.get("name"):
            continue
        out.append({
            "type": "structured_fact", "source": "OEIS",
            "title": f"A{r.get('number', ''):06d}" if r.get("number") is not None else query,
            "url": f"https://oeis.org/A{r.get('number', 0):06d}",
            "text": f"{r.get('name')}. Terms: {(r.get('data') or '')[:200]}",
            "license": "CC BY-SA 4.0",
        })
    return out


def doab_books(query, limit=2):
    """Directory of Open Access Books — 94k+ peer-reviewed open books.

    Filtered to derivative-safe licences only. DOAB indexes NC and ND titles
    alongside open ones, and a course generated from a NoDerivatives book is a
    derivative work — so an unrecognised licence is dropped rather than assumed,
    the same fail-closed rule the image sources use.
    """
    data = _get_json("https://directory.doabooks.org/rest/search",
                     {"query": query, "expand": "metadata"}, timeout=25)
    if not data:
        return []
    out = []
    for b in (data if isinstance(data, list) else [])[:limit * 3]:
        meta = {m.get("key"): m.get("value")
                for m in (b.get("metadata") or []) if isinstance(m, dict)}
        lic = (meta.get("dc.rights.uri") or meta.get("dc.rights") or "").lower()
        if not lic or "nc" in lic or "nd" in lic:
            continue  # unknown, NonCommercial or NoDerivatives -> refuse
        out.append({
            "type": "book", "source": "DOAB",
            "title": meta.get("dc.title") or b.get("name") or query,
            "url": meta.get("dc.identifier.uri") or "",
            "text": (meta.get("dc.description.abstract") or "")[:600],
            "license": lic[:80],
        })
        if len(out) >= limit:
            break
    return out


# PROBED AND NOT WIRED (2026-08-19), recorded so nobody re-adds them from a list:
#
#   * LibreTexts   — the largest open STEM corpus, and exactly what the
#                    sourceless case wants, but it exposes no search API. It is
#                    a MediaWiki farm per subject; reaching it means per-domain
#                    endpoints and per-book licence checks (CC-BY / BY-SA /
#                    BY-NC-SA vary by book), which is a project rather than a
#                    function.
#   * PhET         — CC-BY and ideal for diagrams, but its catalogue is a
#                    JavaScript app with no public JSON index.
#   * NIST DLMF    — authoritative for special functions and published as HTML
#                    only; no API.
#
# All three are worth having and none is a one-function addition.


# Registry: source -> domains it serves. "*" means every subject.
DOMAIN_SOURCES = (
    ("met_museum", met_museum, ("art",)),
    ("art_institute", art_institute, ("art",)),
    ("loc_newspapers", loc_chronicling_america, ("history", "social")),
    ("pubchem", pubchem_compound, ("science",)),
    ("oeis", oeis_sequence, ("science",)),
    ("doab", doab_books, ("history", "philosophy", "social", "art")),
    ("arxiv", arxiv_abstracts, ("science",)),
    ("gutenberg", gutenberg_texts, ("history", "philosophy", "art")),
    ("wikidata", wikidata_facts, ("*",)),
    # OpenAlex last and universal: it answers for almost any subject, which is
    # exactly why it must not crowd out a domain-specific source that knows
    # more. `budget` in fetch_domain_sources is what enforces that ordering.
    ("openalex", openalex_works, ("*",)),
)


def fetch_domain_sources(query, domains, per_source=2, budget=4):
    """Sources relevant to `domains` only.

    `budget` caps the total returned so a well-covered subject cannot drown the
    concept in citations — more sources is not more grounding.

    Entries with no text are dropped here rather than downstream. A source
    whose text never reaches the model cannot be cited (the consumer enforces
    that), so returning it does nothing except consume `budget` and push a
    usable source out of the result.
    """
    if not query:
        return []
    out = []
    for name, fn, serves in DOMAIN_SOURCES:
        if len(out) >= budget:
            break
        if "*" not in serves and not (set(serves) & set(domains or ())):
            continue
        try:
            got = [r for r in (fn(query, limit=per_source) or [])
                   if (r.get("text") or "").strip()]
            out.extend(got[:per_source])
        except Exception as e:
            logger.debug(f"domain source {name} failed for {query!r}: {e}")
    return out[:budget]
