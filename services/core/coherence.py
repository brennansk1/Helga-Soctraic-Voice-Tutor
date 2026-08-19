"""Internal coherence: does this course teach things before it needs them?

WHY THIS EXISTS
---------------
The quality gate is two-configuration. With a matched textbook it can ask
"does this cover the source, in the source's order?" — key-term coverage and
sequencing, both model-free. **Without one, those criteria cannot run at all.**

Marking them N/A is correct — scoring a missing reference as 0 would make a
sourceless course look identical to one that failed against its source — but N/A
must not mean *easier*. A course on a subject with no open textbook should face
an equally hard bar, or "no source" becomes the way to pass.

So this is the replacement, and it asks the strongest question available with no
external reference: **is the course coherent on its own terms?** A curriculum
that uses a concept before teaching it is defective regardless of what any
textbook says, and that defect is detectable without a model.

WHAT IT DETECTS
---------------
A forward reference: a concept whose title uses a term that the course does not
introduce until later. "Diagonalizing a Matrix" in module 2 when "Eigenvalue" is
first taught in module 5 means a learner meets the word before the idea.

WHAT IT CANNOT DETECT
---------------------
Whether the content is correct, deep, or well explained. Those are the depth
contract's and fact-check's job, and both run in BOTH configurations — they are
what actually carries the quality bar when no source exists. This adds the one
structural check that a source would otherwise have provided.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Words that carry no subject meaning, so their position says nothing.
_STOP = {
    "the", "and", "for", "with", "from", "into", "その", "introduction", "overview",
    "basic", "basics", "advanced", "further", "using", "via", "part", "chapter",
    "unit", "lesson", "concept", "review", "summary", "applications", "example",
    "examples", "practice", "problems", "understanding", "exploring", "analysis",
    "properties", "methods", "techniques", "fundamentals", "principles", "theory",
    "systems", "topics", "concepts", "computing", "determining", "solving",
}


def _terms(title):
    """Content words of a title, lowercased and singularised crudely."""
    out = []
    for w in re.findall(r"[a-z]+", (title or "").lower()):
        if len(w) < 4 or w in _STOP:
            continue
        out.append(w[:-1] if w.endswith("s") and len(w) > 4 else w)
    return out


def _ordered_concepts(struct):
    """Concepts in teaching order, each with the module it sits in."""
    out = []
    for mi, m in enumerate((struct or {}).get("modules") or []):
        for u in (m.get("units") or []):
            for l in (u.get("lessons") or []):
                for c in (l.get("concepts") or []):
                    t = (c.get("title") or "").strip()
                    if t:
                        out.append((mi, t))
    return out


def check_coherence(struct, max_report=10):
    """Forward references in a course. No model involved.

    A term is "introduced" at the first concept whose title contains it. A later
    concept using it is fine; an EARLIER one is the defect — it needs an idea the
    course has not taught yet.
    """
    concepts = _ordered_concepts(struct)
    if len(concepts) < 6:
        return {"checked": False, "reason": "too few concepts to judge ordering"}

    # WHERE A TERM IS INTRODUCED, not merely where it first appears.
    #
    # Taking the first occurrence makes the check vacuous: the forward reference
    # IS the first occurrence, so it can never be earlier than itself. A term is
    # introduced where it is the SUBJECT of a concept — the leading content word
    # of the title — and used everywhere else.
    #
    #   "Eigenvalue Definition"              -> introduces "eigenvalue"
    #   "Diagonalizing with Eigenvalue..."   -> uses it
    introduced = {}
    for idx, (module, title) in enumerate(concepts):
        lead = _terms(title)[:1]
        for term in lead:
            introduced.setdefault(term, idx)
    # A term that is never the subject of any concept has no introduction to be
    # early of, so fall back to its first appearance and it can never be flagged.
    first_seen = dict(introduced)
    for idx, (module, title) in enumerate(concepts):
        for term in _terms(title):
            first_seen.setdefault(term, idx)

    # A term is only worth judging if it is a real subject term — one that names
    # a concept somewhere rather than appearing once in passing.
    counts = {}
    for _, title in concepts:
        for term in set(_terms(title)):
            counts[term] = counts.get(term, 0) + 1
    salient = {t for t, n in counts.items() if n >= 2}

    # NARROWED, because the first version was mostly false positives.
    #
    # Defining "introduced" as the LEADING word means a multi-word title cannot
    # introduce its own subject, and the check fired on:
    #
    #   "Matrix Transpose"      flagged for using 'transpose'
    #   "Vector Space Elements" flagged for using 'space'
    #   "Linear Transformations" at #24 vs 'transformation' at #25
    #
    # Each of those concepts IS the introduction. A dense subject reuses its
    # vocabulary constantly, and a check that fires on that is noise — worse
    # than no check, because a gate people learn to ignore stops protecting
    # anything.
    #
    # The defect actually worth catching is narrow: a concept using an idea that
    # the course does not treat ANYWHERE until a later MODULE. Same-module
    # ordering is editorial; crossing a module boundary is a curriculum error.
    module_of = {i: m for i, (m, _) in enumerate(concepts)}
    forward = []
    for idx, (module, title) in enumerate(concepts):
        terms = _terms(title)
        for term in terms:
            if term not in salient:
                continue
            intro = first_seen.get(term, idx)
            if intro <= idx:
                continue
            # Only across a module boundary. Within a module, ordering is
            # editorial and the leading-word heuristic is too weak to judge it:
            # "Matrix Transpose" introduces the transpose as surely as
            # "Transpose Rules" does, and no title-text rule separates them.
            # Across modules the claim is stronger — the course spends a whole
            # module using an idea it does not treat until a later one.
            if module_of.get(intro, module) <= module:
                continue
            forward.append({"concept": title, "term": term,
                            "introduced_at": intro, "used_at": idx,
                            "modules": f"{module} -> {module_of.get(intro)}"})

    total = len(concepts)
    rate = len(forward) / max(1, total)
    return {
        "checked": True,
        "concepts": total,
        "forward_references": len(forward),
        "rate": round(rate, 3),
        # A handful of forward references is normal writing; a curriculum built
        # backwards is not. The threshold is deliberately generous so this fires
        # on incoherence rather than on style.
        "verdict": "INCOHERENT" if rate > 0.15 else "ok",
        "examples": forward[:max_report],
        "instrument": "forward-reference scan (no model)",
    }


def applicable_criteria(has_source):
    """Which gate criteria can run, and which replaces the ones that cannot.

    Returned explicitly so a summary can state WHICH configuration ran. A gate
    that silently drops criteria reports a clean pass on a weaker test, and
    "no source" then becomes the easiest way to pass.
    """
    common = ["depth_contract", "level_calibration", "fact_check", "structure",
              "grounding", "internal_coherence"]
    if has_source:
        return common + ["source_coverage", "sequencing"]
    return common


def gate_summary(results, has_source):
    """Pass/fail over the criteria that COULD run, with the configuration named.

    Normalised deliberately: a sourceless course clears the same PROPORTION of a
    smaller set, so it is not held to a lower standard — it is held to the same
    standard on the questions that can honestly be asked of it.
    """
    criteria = applicable_criteria(has_source)
    graded = {k: v for k, v in (results or {}).items() if k in criteria}
    missing = [c for c in criteria if c not in graded]
    passed = [k for k, v in graded.items() if v]
    return {
        "configuration": "sourced" if has_source else "sourceless",
        "criteria_applicable": criteria,
        "criteria_graded": sorted(graded),
        "criteria_not_run": missing,
        "passed": sorted(passed),
        "failed": sorted(k for k in graded if k not in passed),
        "pass_rate": round(len(passed) / max(1, len(graded)), 3),
        # N/A is never a pass. A criterion that could not run is reported as not
        # run, so a sourceless summary cannot look complete by omission.
        "complete": not missing,
    }
