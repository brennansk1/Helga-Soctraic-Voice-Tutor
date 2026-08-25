"""Stage 4, Pass 1 — everything that can be settled without a model.

The deterministic half of the audit. It runs over a FINISHED course, reading
what is actually stored, and answers only questions that have a definite
answer: does the file carry the sections the tutor reads, does it meet its
depth contract, does a claim survive being executed, does a citation point at
something real, and do two concepts contradict each other.

WHY IT RE-RUNS CHECKS THAT ALREADY RAN AT THE WRITE PATH

Because the write path is not the only way content arrives. Every defect the
2026-08-25 audit found was in a concept that had passed the write-path gates —
some because the gate did not exist yet, some because the concept was written
by a resume, a repair, or a hand edit that did not go through it. A check that
only runs where content is created cannot answer "is this course, as it now
stands on disk, fit to teach". That is a different question and this is where
it gets asked.

WHAT IT REFUSES TO DO

Report coverage it does not have. Every result carries what was checked
alongside what was found, because the failure this whole stage exists to
prevent is a checker that reported clean on a course with seven errors — it
sampled 34% and its output made "no findings" and "no coverage" look identical.

`not_applicable` is a first-class outcome. A history concept has no SQL to
execute, and saying so is different from saying it passed.
"""
import json
import logging
import os
import re
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

try:
    from services.core import content_guards
    from services.core import sql_ground_truth
    from services.core.depth_contract import validate_concept, infer_domain
except ImportError:  # running inside the service container, flat layout
    import content_guards
    import sql_ground_truth
    from depth_contract import validate_concept, infer_domain


# Sections the tutor actually reads when teaching. A concept missing one is not
# cosmetically incomplete — the lesson is degraded in a specific, nameable way.
TUTOR_SECTIONS = ("Core Explanation", "Misconceptions", "Analogies")

SEVERITY = ("blocking", "serious", "minor")


class Finding:
    """One defect, in the terms a person would use to act on it."""

    __slots__ = ("concept_uid", "title", "check", "severity", "detail", "quote")

    def __init__(self, concept_uid, title, check, severity, detail, quote=""):
        self.concept_uid = concept_uid
        self.title = title
        self.check = check
        self.severity = severity
        self.detail = detail
        self.quote = quote

    def as_dict(self):
        return {"concept_uid": self.concept_uid, "title": self.title,
                "check": self.check, "severity": self.severity,
                "detail": self.detail, "quote": self.quote}


def walk_concepts(structure):
    """Every concept in path order, with the path that reaches it."""
    for module in structure.get("modules") or []:
        for unit in module.get("units") or []:
            for lesson in unit.get("lessons") or []:
                for concept in lesson.get("concepts") or []:
                    yield concept, (module.get("title", ""),
                                    unit.get("title", ""),
                                    lesson.get("title", ""))


def _section_body(markdown, heading):
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^#{{1,3}}\s|\Z)",
                  markdown or "", re.MULTILINE | re.DOTALL)
    return (m.group(1) if m else "").strip()


# A section present but under this many words is missing, not present. The stub
# the pipeline used to inject for a missing heading was nine words long and
# satisfied every check that asked only whether the heading existed.
MIN_SECTION_WORDS = 25


def audit_concept(markdown, concept, course_title, mastery, domain, sources=None):
    """Deterministic checks for one concept. Returns (findings, checks_run)."""
    uid = concept.get("uid", "")
    title = concept.get("title", "")
    findings, ran = [], []

    # 1. Hygiene — deliberation, stubs, build apologies, splitter artefacts.
    ran.append("content_guards")
    for problem in content_guards.inspect(markdown, title=title,
                                          course_title=course_title):
        findings.append(Finding(uid, title, "content_guards", "serious", problem))

    # 2. Depth contract, against what is stored rather than what was generated.
    ran.append("depth_contract")
    try:
        ok, problems, _detail = validate_concept(
            markdown, mastery, course_title, domain, sources=sources)
        if not ok:
            for p in problems:
                findings.append(Finding(uid, title, "depth_contract", "minor", p))
    except Exception as e:
        logger.warning("depth contract failed for %s: %s", title, e)
        ran.remove("depth_contract")

    # 3. The sections the tutor reads.
    ran.append("tutor_sections")
    for heading in TUTOR_SECTIONS:
        body = _section_body(markdown, heading)
        if not body:
            findings.append(Finding(
                uid, title, "tutor_sections", "serious",
                f"## {heading} is missing — the tutor reads it when teaching"))
        elif len(body.split()) < MIN_SECTION_WORDS:
            findings.append(Finding(
                uid, title, "tutor_sections", "serious",
                f"## {heading} is only {len(body.split())} words — present but "
                f"empty of content", quote=" ".join(body.split())[:120]))

    # 4. Thin content — specific to this concept, or fluent and empty.
    ran.append("thin_content")
    thin, measures = audit_thinness(markdown, title)
    if thin:
        findings.append(Finding(
            uid, title, "thin_content", "serious",
            "meets its structure but teaches little: " + "; ".join(thin),
            quote=str(measures)))

    # 5. Claims a real engine can settle. Domain-gated: silence from a probe
    #    that does not apply is `not_applicable`, never a pass.
    haystack = f"{course_title} {title}".lower()
    if any(k in haystack for k in ("sql", "database", "postgres", "query")):
        try:
            hits, probes = sql_ground_truth.check_markdown(markdown)
            if probes:
                ran.append("executable_claims")
                for h in hits:
                    findings.append(Finding(
                        uid, title, "executable_claims", "blocking",
                        h["engine_says"], quote=h["claim"][:160]))
        except Exception as e:
            logger.warning("ground truth failed for %s: %s", title, e)

    return findings, ran



# --- thin content -----------------------------------------------------------
#
# A concept can pass everything above and still teach nothing.
#
# The depth contract counts words and required elements. content_guards catches
# an obvious stub. `check_substance` counts claims and `check_hollowness` counts
# filled slots in the teaching object — both real, both model-free, and both
# COURSE-LEVEL AVERAGES. They report that half the course is hollow without
# naming a single concept, so nothing downstream can repair what they find.
#
# This asks a per-concept question the others do not ask at all: is the text
# SPECIFIC? A concept about DENSE_RANK that never writes `DENSE_RANK()`, quotes
# no value, names no clause, and could have its title swapped for any other
# concept in the course without a sentence becoming false, has met every
# structural requirement and taught nobody anything.
#
# Three independent signals, because any one alone over-flags:
#
#   concrete density  — code spans, numerals, identifiers, and the concept's
#                       own terms, as a share of content words. Generic prose
#                       has almost none.
#   self-repetition   — the same shingle recurring across paragraphs, which is
#                       padding to a word count rather than explanation.
#   empty sentences   — sentences carrying no concrete token whatsoever.
#
# A concept is reported only when it fails on MULTIPLE axes. One low number is
# a style; two is thin content. That threshold is set deliberately high because
# a false "thin" verdict sends good teaching to be rewritten.

_CODE_SPAN = re.compile(r"`[^`\n]+`|```.*?```", re.DOTALL)
_NUMERAL = re.compile(r"\b\d[\d,.]*\b")
_IDENTIFIER = re.compile(r"\b[A-Z][A-Z_]{2,}\b|\b\w+\(\)")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# CALIBRATED AGAINST THE CORPUS, NOT INVENTED.
#
# The first version of these numbers was guessed, and every one of them was
# dead. Measured across 177 concepts of SQL and Advanced SQL:
#
#     concrete_density       min 0.094  p05 0.130  med 0.198  max 0.551
#     empty_sentence_share   min 0.000  med 0.176  max 0.404
#     self_repetition        min 0.000  med 0.023  max 0.583
#
# The guessed floor for density was 0.020 — below the thinnest concept in the
# corpus by a factor of five, so it could never fire. The empty-sentence
# ceiling was 0.72 against an observed maximum of 0.404. Two of the three
# checks were incapable of failing anything, which is the same as not having
# written them, and the run reporting "0 findings" looked exactly like success.
#
# These sit in the measured tail instead: roughly the 5th percentile for
# density and the 90th for the two ceilings. Combined with the two-axis rule
# below, a concept has to be simultaneously in the tail on two independent
# measures before anyone is asked to look at it.
# A PERCENTILE OF GOOD CONTENT IS THE WRONG PLACE TO PUT A THRESHOLD.
#
# These were first set at p05/p95 of the corpus, which flags 5% of acceptable
# content by construction. It duly did: five concepts, every one a false
# positive on inspection — dense technical prose about declarative
# partitioning, recursive CTE fixed-point iteration, cycle detection. Reading
# them is what settled it; the numbers alone looked like a working check.
#
# A false "thin" verdict costs a rewrite of good teaching, so the bar belongs
# OUTSIDE the range of content we accept, not at its edge. Observed across 178
# concepts: density never below 0.109, empty-sentence share never above 0.389.
# These sit past both, so the check fires only on content unlike anything in a
# course we consider good — and reports nothing on a good course, which is the
# correct answer rather than a failure to find something.
MIN_CONCRETE_DENSITY = 0.090      # below the observed minimum (0.109)
MAX_EMPTY_SENTENCE_SHARE = 0.450  # above the observed maximum (0.389)
MAX_SELF_REPETITION = 0.500       # above p95 (0.443); >0.80 flags on its own


def _concrete_tokens(text, title_terms):
    """Tokens that could only belong to THIS concept."""
    n = 0
    for pat in (_CODE_SPAN, _NUMERAL, _IDENTIFIER):
        n += len(pat.findall(text or ""))
    words = re.findall(r"[a-z]{3,}", (text or "").lower())
    n += sum(1 for w in words if w in title_terms)
    return n


def _self_repetition(text):
    """Highest overlap between any two paragraphs of the same concept."""
    paras = [p for p in re.split(r"\n\s*\n", text or "") if len(p.split()) > 25]
    if len(paras) < 2:
        return 0.0
    def shing(p):
        w = re.findall(r"[a-z]{3,}", p.lower())
        return {tuple(w[i:i + 4]) for i in range(max(0, len(w) - 3))}
    sets = [shing(p) for p in paras]
    worst = 0.0
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            a, b = sets[i], sets[j]
            if not a or not b:
                continue
            worst = max(worst, len(a & b) / len(a | b))
    return worst


def audit_thinness(markdown, title):
    """Is this concept specific, or fluent and empty? (findings, measures)"""
    # MEASURE THE TEXT THAT WAS WRITTEN, NOT THE TEXT WITH THE EVIDENCE
    # REMOVED.
    #
    # This used `teaching_text()`, which strips fenced code blocks. So density
    # was computed on prose with the code taken out, and then the concept was
    # penalised for containing no code. All four concepts it flagged on the
    # first calibrated run carried between two and five code blocks each —
    # every finding was false, and each one would have sent a good concept to
    # be rewritten.
    body = markdown or ""

    words = re.findall(r"[a-z]{3,}", body.lower())
    if len(words) < 60:
        # Too short to measure density on; the depth contract owns that case
        # and would already have failed it.
        return [], {}

    title_terms = {w for w in re.findall(r"[a-z]{3,}", (title or "").lower())
                   if w not in _STOPWORDS}
    concrete = _concrete_tokens(body, title_terms)
    density = concrete / max(1, len(words))

    sentences = [s for s in _SENTENCE_SPLIT.split(body) if len(s.split()) > 5]
    empty = sum(1 for s in sentences
                if _concrete_tokens(s, title_terms) == 0)
    empty_share = empty / max(1, len(sentences))

    repetition = _self_repetition(body)

    measures = {"concrete_density": round(density, 4),
                "empty_sentence_share": round(empty_share, 3),
                "self_repetition": round(repetition, 3),
                "words": len(words)}

    failed = []
    if density < MIN_CONCRETE_DENSITY:
        failed.append(f"almost nothing specific to this concept "
                      f"({concrete} concrete tokens in {len(words)} words)")
    if empty_share > MAX_EMPTY_SENTENCE_SHARE:
        failed.append(f"{empty}/{len(sentences)} sentences contain no code, "
                      f"value, identifier or term of its own subject")
    if repetition > MAX_SELF_REPETITION:
        failed.append(f"paragraphs repeat each other "
                      f"(overlap {repetition:.0%}) — padding, not explanation")

    # NEAR-DUPLICATE PARAGRAPHS NEED NO CORROBORATION.
    #
    # The two-axis rule protects good writing from a single unusual measure.
    # It also, on the validation set, cleared a concept whose paragraphs were
    # 100% identical to each other — one real sentence about ROW_NUMBER()
    # repeated eight times — because the repeated text was full of concrete
    # SQL tokens and so passed the other two axes comfortably.
    #
    # Repetition at that level is not a stylistic signal to be weighed against
    # others. It is the same paragraph twice, and no amount of concreteness
    # makes reading it again worthwhile.
    if repetition > 0.80:
        return ([f"paragraphs are near-identical to each other "
                 f"(overlap {repetition:.0%}) — the same text repeated"],
                measures)

    if len(failed) < 2:
        return [], measures
    return failed, measures


def audit_citations(uid, title, sources):
    """Is the evidence for this concept real, present and on-topic."""
    findings = []
    if not sources:
        findings.append(Finding(uid, title, "citations", "serious",
                                "no sources recorded for this concept"))
        return findings
    without_passage = [s for s in sources
                       if not (s.get("passage") or "").strip()]
    if without_passage:
        findings.append(Finding(
            uid, title, "citations", "serious",
            f"{len(without_passage)} of {len(sources)} sources have no stored "
            f"passage — nothing downstream can verify a claim against them"))
    for s in sources:
        url = (s.get("url") or "").strip()
        if not url or not url.startswith(("http://", "https://")):
            findings.append(Finding(
                uid, title, "citations", "minor",
                f"source {s.get('title') or '?'!r} has no usable URL"))
    return findings


_STOPWORDS = {"the", "and", "for", "with", "from", "that", "this", "are", "was",
              "using", "into", "how", "what", "why", "its", "our", "your"}


def _terms(text):
    return {w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower())
            if w not in _STOPWORDS}


def audit_course_coherence(concepts):
    """The checks no per-concept gate can make, because they need two files.

    Duplicate coverage, splitter artefacts left in the path, and the case that
    motivated the whole stage: two concepts teaching contradictory things,
    each internally consistent and individually passing every gate.
    """
    findings = []
    by_title = defaultdict(list)
    for concept, path in concepts:
        title = (concept.get("title") or "").strip()
        by_title[title.lower()].append((concept, path))
        for part in path:
            if re.search(r"\bPart\s+\d+\b", part or "", re.IGNORECASE):
                findings.append(Finding(
                    concept.get("uid", ""), title, "coherence", "minor",
                    f"curriculum path contains a splitter artefact: {part!r}"))
                break

    for title_l, group in by_title.items():
        if len(group) > 1 and title_l:
            uids = ", ".join(c.get("uid", "?") for c, _ in group)
            findings.append(Finding(
                group[0][0].get("uid", ""), group[0][0].get("title", ""),
                "coherence", "minor",
                f"{len(group)} concepts share this title ({uids}) — a learner "
                f"cannot tell them apart in search or the path"))
    return findings



# WHEN EVERY CONCEPT HAS THE SAME FINDING, IT IS ONE FINDING.
#
# The first run over the SQL course produced 113 "serious" rows, 95 of which
# were the identical missing-passage defect — one per concept — and they buried
# the five blocking factual errors underneath. That is the same harm as
# reporting nothing: a report nobody can read is a report nobody reads.
#
# Folding is NOT suppression. The count is preserved, the affected concepts are
# listed, and the fold only happens above a threshold high enough that the
# defect is plainly systemic rather than a run of bad luck.
SYSTEMIC_SHARE = 0.5


def _fold_systemic(findings, audited):
    """Collapse a defect that affects most of the course into one row."""
    if audited < 4:
        return findings, []
    groups = defaultdict(list)
    for f in findings:
        if f.check in ("coherence", "executable_claims"):
            continue      # never fold a factual error or a course-level check
        groups[(f.check, _shape(f.detail))].append(f)

    folded, systemic = [], []
    drop = set()
    for (check, shape), group in groups.items():
        if len(group) / audited < SYSTEMIC_SHARE:
            continue
        drop.update(id(f) for f in group)
        example = group[0]
        systemic.append({
            "check": check,
            "severity": example.severity,
            "concepts": len(group),
            "share": round(len(group) / audited, 2),
            "detail": example.detail,
            "affected": [f.concept_uid for f in group],
        })
    for f in findings:
        if id(f) not in drop:
            folded.append(f)
    return folded, systemic


_NUMBERS = re.compile(r"\d+")


def _shape(detail):
    """A finding's wording with the counts removed, so "2 of 2 sources" and
    "5 of 5 sources" are recognised as the same defect."""
    return _NUMBERS.sub("#", detail or "")


def audit_course(structure, contents, sources_by_uid=None, mastery=None,
                 course_title=None, domain=None):
    """Pass 1 over a whole course.

    `contents` maps concept uid -> markdown. A uid absent from it is a concept
    with no file, which is reported rather than skipped.
    """
    t0 = time.time()
    course_title = course_title or structure.get("title") or ""
    mastery = mastery or structure.get("mastery_level") or 3
    domain = domain or structure.get("teaching_domain") or infer_domain(course_title)
    sources_by_uid = sources_by_uid or {}

    concepts = list(walk_concepts(structure))
    findings = []
    checks_run = defaultdict(int)
    audited = 0

    for concept, _path in concepts:
        uid = concept.get("uid", "")
        title = concept.get("title", "")
        markdown = contents.get(uid)
        if not markdown or not markdown.strip():
            findings.append(Finding(uid, title, "missing_content", "blocking",
                                    "concept has no content file"))
            continue
        audited += 1
        f, ran = audit_concept(markdown, concept, course_title, mastery, domain,
                               sources=sources_by_uid.get(uid))
        findings.extend(f)
        for name in ran:
            checks_run[name] += 1
        findings.extend(audit_citations(uid, title, sources_by_uid.get(uid)))
        checks_run["citations"] += 1

    findings.extend(audit_course_coherence(concepts))
    checks_run["coherence"] = 1

    findings, systemic = _fold_systemic(findings, audited)

    by_severity = defaultdict(int)
    by_check = defaultdict(int)
    concepts_with_findings = set()
    for f in findings:
        by_severity[f.severity] += 1
        by_check[f.check] += 1
        if f.severity in ("blocking", "serious"):
            concepts_with_findings.add(f.concept_uid)
    for sysf in systemic:
        by_check[sysf["check"]] += sysf["concepts"]
        by_severity[sysf["severity"]] += sysf["concepts"]
        # A FOLDED FINDING IS STILL A FINDING AGAINST ITS CONCEPTS.
        #
        # Folding moved these out of the per-concept list, and the affected
        # concepts stopped being counted with it — so a 4-concept course whose
        # every concept was missing three sections the tutor reads reported
        # "0 concepts with findings" beside "serious: 16". A report that
        # contradicts itself is the failure this whole stage exists to catch,
        # and it must not contain one.
        if sysf["severity"] in ("blocking", "serious"):
            concepts_with_findings.update(sysf.get("affected") or [])

    return {
        "course_title": course_title,
        "concepts_total": len(concepts),
        "concepts_audited": audited,
        # Named separately from "clean": a concept with no file was not audited
        # and must not be counted as having passed.
        "concepts_not_audited": len(concepts) - audited,
        # Clean means audited AND nothing found. A course-wide systemic defect
        # means no concept is clean, and saying otherwise would be the exact
        # over-claim this stage exists to stop.
        "concepts_clean": (0 if systemic
                           else audited - len(concepts_with_findings)),
        "concepts_with_findings": len(concepts_with_findings),
        "systemic": systemic,
        "findings": [f.as_dict() for f in findings],
        "by_severity": dict(by_severity),
        "by_check": dict(by_check),
        "checks_run": dict(checks_run),
        "seconds": round(time.time() - t0, 2),
    }
