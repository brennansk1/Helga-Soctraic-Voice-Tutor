"""Pure ranking / query / scoring helpers for the research (online-search) service.

Extracted from research_server.py so this logic is unit-testable WITHOUT the heavy
network deps (aiohttp / trafilatura / wikipediaapi / diskcache). research_server
imports from here.

This module also owns the REGISTRY of source kinds (SOURCE_KIND_WEIGHTS). It is
the single source of truth for what a source kind is worth, precisely because
the alternative — each caller keeping its own list — produced the same bug three
times running. See the long note above the table.
"""
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# --- Domain quality tiers ---
#
# Write hosts in their REGISTRABLE form (no "www.", no port). Matching is
# exact-or-subdomain, so one entry covers every variant.
#
# It used to be exact netloc equality against a hand-written mix of bare and
# "www." spellings, and the list only carried "www." for two of the six blocked
# hosts. The result was that the blocklist did not block: studocu.com,
# quizlet.com, brainly.com and bartleby.com all serve from `www.`, which is
# their canonical host, so every one of them was reachable through the tier
# filter — the four sites the list exists to keep out. TIER_1 had the mirror
# defect: `www.arxiv.org` and `www.plato.stanford.edu` fell through to tier 3
# and were outranked by an arbitrary blog.
TIER_1 = {
    "en.wikipedia.org", "plato.stanford.edu", "ocw.mit.edu",
    "arxiv.org", "khanacademy.org", "mathworld.wolfram.com",
    "nature.com", "britannica.com", "ncbi.nlm.nih.gov",
}
TIER_2 = {
    "developer.mozilla.org", "docs.python.org", "realpython.com",
    "investopedia.com", "sciencedirect.com", "stackoverflow.com",
}
BLOCKED = {
    "chegg.com", "coursehero.com", "brainly.com", "quizlet.com",
    "studocu.com", "bartleby.com",
}


def _host(url: str) -> str:
    """Comparable host for a URL: lowercase, no port, no leading 'www.'."""
    netloc = urlparse(url or "").netloc.lower()
    if "@" in netloc:                     # strip any userinfo
        netloc = netloc.rsplit("@", 1)[1]
    netloc = netloc.split(":", 1)[0]
    return netloc[4:] if netloc.startswith("www.") else netloc


def _matches(host: str, entries) -> bool:
    """True if `host` IS one of `entries` or is a subdomain of one.

    Subdomain matching is what makes a single entry cover the real world:
    studocu serves as www.studocu.com, and Course Hero also answers on
    regional subdomains. A blocklist that only matches the bare name is a
    blocklist with holes in it.
    """
    return any(host == e or host.endswith("." + e) for e in entries)


def domain_tier(url: str) -> int:
    """Quality tier for a URL's domain. -1 = blocked, 1 = best, 3 = unknown."""
    domain = _host(url)
    if not domain:
        return 3
    if _matches(domain, BLOCKED):
        return -1
    if _matches(domain, TIER_1) or domain.endswith((".edu", ".gov")):
        return 1
    if _matches(domain, TIER_2) or domain.startswith("docs."):
        return 2
    return 3


def build_search_queries(title: str, module_title: str, mastery: int = 1):
    """Search queries for a concept. Deeper courses (higher mastery) get extra,
    more academic queries — this is the dormant feature now activated by passing
    mastery through from the hydrator."""
    queries = [
        f"{title} {module_title} explained",
        f"{title} definition examples",
    ]
    if mastery >= 3:
        queries.append(f"{title} in-depth analysis")
    if mastery >= 4:
        queries.append(f"{title} academic overview research")
    return queries


# --- Official documentation ---------------------------------------------------
#
# For a technical concept the authoritative source is the project's own
# documentation, and nothing else comes close. "What does dbt's ref() do at
# compile time" is answered definitively by docs.getdbt.com and approximately
# by everything else; there is no textbook and no paper, and the Wikipedia
# article is a stub.
#
# Without this, every such page is scored as a generic "web" result at 0.20
# capped at 0.40 — the LOWEST weight — so a computer-science concept grounded
# in three pages of primary documentation scores below a history concept
# grounded in one Wikipedia article. That is backwards, and it is why CS
# concepts came out of research with confidence near the 0.5 floor.
#
# Matched on host suffix so subdomains and language paths work
# (docs.python.org, three.docs.rs, /en/stable/...). Deliberately a REGISTRY of
# known-authoritative hosts rather than a heuristic like "contains /docs/":
# a marketing site with a /docs/ path is not primary documentation, and a
# false positive here manufactures confidence, which is the failure mode this
# whole function exists to avoid.
_DOC_HOSTS = (
    # languages and runtimes
    "docs.python.org", "docs.oracle.com", "doc.rust-lang.org", "docs.rs",
    "pkg.go.dev", "go.dev", "developer.mozilla.org", "docs.ruby-lang.org",
    "cppreference.com", "en.cppreference.com", "learn.microsoft.com",
    "docs.swift.org", "kotlinlang.org", "php.net", "docs.julialang.org",
    # data / analytics engineering
    "docs.getdbt.com", "docs.snowflake.com", "docs.databricks.com",
    "airflow.apache.org", "docs.dagster.io", "iceberg.apache.org",
    "duckdb.org", "spark.apache.org", "kafka.apache.org",
    "docs.sqlalchemy.org", "postgresql.org", "dev.mysql.com",
    "sqlite.org", "docs.pola.rs", "pandas.pydata.org", "numpy.org",
    # infra and tooling
    "docs.docker.com", "kubernetes.io", "developer.hashicorp.com",
    "git-scm.com", "docs.github.com", "docs.gitlab.com",
    # standards bodies — the primary source for a spec
    "w3.org", "whatwg.org", "ietf.org", "rfc-editor.org", "iso.org",
    "unicode.org", "ecma-international.org",
)


def is_documentation(url):
    """Is this URL official documentation from a known-authoritative host?"""
    try:
        host = (url or "").split("//", 1)[-1].split("/", 1)[0].lower()
        host = host.split("@")[-1].split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        return any(host == d or host.endswith("." + d) for d in _DOC_HOSTS)
    except Exception:                    # pragma: no cover - defensive
        return False

# --- Grounding confidence ----------------------------------------------------
#
# THE SAME BUG HAS NOW BEEN FOUND HERE THREE TIMES, so the mechanism that
# produced it is described before the table that replaces it.
#
#   1. primary literature. The caller filtered `[s for s in sources
#      if s["type"] == "web"]`, and Crossref/arXiv carry "journal"/"preprint",
#      so a concept grounded in Wikipedia plus two peer-reviewed papers scored
#      0.40 — identical to Wikipedia alone.
#   2. open textbooks, added with type "textbook". The caller still filtered
#      only "web"/"journal"/"preprint", so the source kind a COURSE most wants
#      counted for exactly nothing.
#   3. domain-routed archives: "artefact", "primary_document",
#      "structured_fact". A history concept grounded in four Library of
#      Congress documents and two Met artefacts scored **0.00**, was stamped
#      "Limited sources ... leans more on the model's own knowledge" in
#      learner-facing text, and triggered a wasted retry — while carrying six
#      real citations.
#
# Every one of those was the same defect: a hand-maintained list of kinds in
# the CALLER, updated only when somebody remembered. The comment above the
# third occurrence already said, in capitals, that every kind must be passed.
# A comment cannot enforce anything, so the list is gone: the table below is
# the single source of truth, the scorer reads the kinds off the sources
# themselves, and an unregistered kind is scored on a conservative default AND
# logged rather than silently vanishing.
#
# WEIGHTS: this score grades material for BUILDING A COURSE, not for advancing
# a field. A textbook chapter is the settled canon, already selected, sequenced
# and explained for a learner; a paper is one result at the frontier; a museum
# label is 600 characters of catalogue metadata. Both papers and artefacts are
# real evidence, but only one kind is the right shape for teaching.
#
# CAPS matter as much as weights. Without them the score rewards COUNT, and
# three mediocre web pages outscore one excellent textbook — which manufactures
# confidence instead of measuring it. Kinds that are substitutes for one
# another share a cap ("family"), so six catalogue entries cannot add up to a
# textbook.
#
# kind -> (points per source, cap family)
SOURCE_KIND_WEIGHTS = {
    "wikipedia":        (0.40, "tertiary"),
    "textbook":         (0.30, "textbook"),    # Wikibooks / Wikiversity
    # Official project docs: for "what does dbt's ref() do at compile
    # time" there is no textbook and the Wikipedia article is a stub.
    # Scored as generic "web" it sat at the LOWEST weight, which is why
    # CS concepts came out of research near the 0.5 floor.
    "documentation":    (0.30, "textbook"),    # see _DOC_HOSTS
    "journal":          (0.25, "primary"),     # Crossref DOI
    "preprint":         (0.25, "primary"),     # arXiv
    # Emitted by domain_sources and unregistered until the guard test caught
    # them — the fourth instance of a kind the scorer could not see. An open
    # book is the settled canon like a textbook; OpenAlex/arXiv metadata is a
    # result at the frontier like a journal, so each takes its neighbour's
    # weight and cap family rather than a number invented here.
    "book":             (0.30, "textbook"),    # Project Gutenberg / DOAB
    "scholarly":        (0.25, "primary"),     # OpenAlex / arXiv
    "web":              (0.20, "web"),
    "artefact":         (0.20, "archive"),     # Met / Art Institute
    "primary_document": (0.20, "archive"),     # Library of Congress
    "structured_fact":  (0.10, "structured"),  # Wikidata
    # Illustrations are collected by a different pipeline (image_sources) and
    # never enter combined_text. Registered explicitly at zero so the guard
    # test can be exhaustive over the whole service rather than knowing which
    # module to skip.
    "image":            (0.00, "none"),
}

# The tertiary cap is what keeps Wikipedia + any number of web pages at 0.80:
# FULL confidence has to be earned with a textbook or a primary source, because
# an arbitrary pile of web results is exactly the evidence profile that looks
# strong and teaches badly.
FAMILY_CAPS = {
    "tertiary": 0.4, "textbook": 0.6, "primary": 0.5,
    "web": 0.4, "archive": 0.4, "structured": 0.2, "none": 0.0,
}

# An unregistered kind is a bug, not a zero. Scoring it as absent is precisely
# how this failed three times, so the default is deliberately non-zero and
# loud: a new source kind is under-weighted until someone registers it, never
# invisible.
UNKNOWN_KIND_WEIGHT = 0.15
UNKNOWN_FAMILY = "unknown"
UNKNOWN_FAMILY_CAP = 0.3

KNOWN_SOURCE_KINDS = frozenset(SOURCE_KIND_WEIGHTS)

_warned_kinds = set()


def confidence_from_counts(counts) -> float:
    """Grounding confidence in [0, 1] from a {kind: count} mapping."""
    by_family = {}
    for kind, n in (counts or {}).items():
        if not n:
            continue
        weight, family = SOURCE_KIND_WEIGHTS.get(
            kind, (UNKNOWN_KIND_WEIGHT, UNKNOWN_FAMILY))
        if kind not in SOURCE_KIND_WEIGHTS and kind not in _warned_kinds:
            _warned_kinds.add(kind)
            logger.warning(
                "grounding: unregistered source kind %r scored at the default "
                "%.2f — register it in ranking.SOURCE_KIND_WEIGHTS",
                kind, UNKNOWN_KIND_WEIGHT)
        by_family[family] = by_family.get(family, 0.0) + weight * n

    total = sum(min(score, FAMILY_CAPS.get(family, UNKNOWN_FAMILY_CAP))
                for family, score in by_family.items())
    return round(min(total, 1.0), 2)


def count_source_kinds(sources):
    """{kind: count} for a list of source dicts. Missing type counts as web."""
    counts = {}
    for s in sources or ():
        kind = (s.get("type") or "web") if hasattr(s, "get") else "web"
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def confidence_from_sources(sources) -> float:
    """Grounding confidence for the sources that ACTUALLY grounded a concept.

    Takes the source dicts themselves so no caller can drop a kind on the way
    in — which was the bug, three times over. Pass only sources whose text
    reached the model: a citation the model never read is not grounding.
    """
    return confidence_from_counts(count_source_kinds(sources))


def compute_confidence(has_wikipedia: bool, web_source_count: int,
                       primary_source_count: int = 0,
                       textbook_count: int = 0) -> float:
    """Back-compatible count-based entry point (Wikipedia + three kinds).

    Kept because callers and tests use it, but it is the shape that caused the
    bug: it can only express the kinds named in its signature. New code should
    call confidence_from_sources() and let the table do the classifying.
    """
    return confidence_from_counts({
        "wikipedia": 1 if has_wikipedia else 0,
        "web": web_source_count,
        "journal": primary_source_count,
        "textbook": textbook_count,
    })


def dedup_by_url(results):
    """Stable de-dup of result dicts by their 'url' field."""
    seen = set()
    out = []
    for r in results:
        if r.get("url") not in seen:
            seen.add(r.get("url"))
            out.append(r)
    return out
