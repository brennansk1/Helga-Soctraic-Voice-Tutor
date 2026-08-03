"""Depth contract (sprint A1) — make the mastery slider mean something.

PROBLEM THIS EXISTS TO SOLVE
---------------------------
Setting a course to a high level does not currently produce work of that level.
Measured on the only course that existed before this harness: every concept
landed between 626 and 876 words (stdev 57.7) regardless of Bloom level, module
position, or requested mastery. `MASTERY_PROFILES` does declare escalating
targets (content_words 150 -> 800) and register guidance ("Write for a
researcher..."), but those are only *hints inside a prompt*. Nothing verifies
the result, so the model converges on its own comfortable length and register
and the slider becomes decorative.

Worse, length is the wrong proxy. A graduate treatment is not an undergraduate
one with more words — it is one with formal definitions, derivations, worked
examples, non-trivial exercises, and primary literature. That is what this
module checks.

DESIGN
------
Each mastery level gets a contract of (a) a word band and (b) REQUIRED
structural elements. `validate_concept()` returns the specific missing
elements so the generator can regenerate against a named deficiency rather
than blindly retrying, and so a course that cannot meet its level can be
refused that label instead of silently shipping.

Detection is deliberately heuristic and permissive in form — it accepts LaTeX,
unicode math, or plain prose phrasing — because the goal is to catch content
that is *categorically* missing rigor, not to enforce one house style.
"""

import re

# --- element detectors -------------------------------------------------------
# Each returns True if the concept body plausibly contains the element.

_MATH_INLINE = re.compile(r"\$[^$\n]+\$|\\\(|\\\[|\\begin\{(equation|align)")
_MATH_UNICODE = re.compile(r"[∑∫∂√≤≥≠≈∈∀∃→↦⊂∪∩·×]|\b[A-Za-z]_\{?\d")

_DETECTORS = {
    # A stated, named definition rather than a casual gloss.
    "formal_definition": re.compile(
        r"\*\*Definition\*\*|^#+\s*Definition|is defined as|we define\b|"
        r"\bLet\s+[A-Za-z\\$]|\bDenote\b|\bformally,",
        re.IGNORECASE | re.MULTILINE),

    # A derivation, proof, or explicit chain of reasoning to a result.
    "derivation_or_proof": re.compile(
        r"\bproof\b|\bQ\.?E\.?D\b|∎|\btherefore\b|\bit follows that\b|"
        r"\bderiv(e|ation)\b|\bsubstituting\b|\bwe obtain\b|\bhence\b",
        re.IGNORECASE),

    # A WORKED example — one carried through with concrete values or steps.
    #
    # Deliberately does NOT match a "Real-World Examples" prose section. The
    # sample course has such a section in all 36 concepts, and every one is a
    # narrative description of where a technique gets applied ("a study of drug
    # efficacy uses causal inference to...") with no numbers, no steps, and
    # nothing for the learner to follow. Describing that an example exists is
    # not working one. Requiring evidence of actual work is the whole point of
    # this contract, so this detector wants step markers, concrete assignment
    # of values, or a computation.
    "worked_example": re.compile(
        r"\bStep\s*\d\b|\*\*Step|^\s*\d+\.\s+(?:First|Compute|Calculate|Let|Set|Assume)|"
        r"\bsuppose\s+[A-Za-z$\\]+\s*=|\blet\s+[A-Za-z$\\]+\s*=|"
        r"\bwe (?:compute|calculate|obtain|get)\b|"
        r"\bplugging in\b|\bsubstitut\w+\s+(?:in|into)\b|"
        r"=\s*-?\d+(?:\.\d+)?\b",
        re.IGNORECASE | re.MULTILINE),

    # Something for the learner to actually do.
    "exercise": re.compile(
        r"^#+\s*(Exercise|Problem|Practice|Try)|\*\*Exercise\*\*|"
        r"\bexercise:|\bproblem set\b|\btry it yourself\b|\byour turn\b",
        re.IGNORECASE | re.MULTILINE),

    # NB: "formal_notation", "primary_source" and "any_source" are not regex
    # entries here — they need custom logic and are handled in _has().

    # Named results, not just narrative.
    "named_result": re.compile(
        r"\btheorem\b|\blemma\b|\bcorollary\b|\baxiom\b|\bproposition\b|"
        r"\b[A-Z][a-z]+(?:'s|s')\s+(theorem|lemma|law|rule|inequality|criterion)",
        re.IGNORECASE),
}

# Primary literature vs general reference. A graduate course citing only
# Wikipedia is not a graduate course.
_PRIMARY_SOURCE = re.compile(
    r"arxiv\.org|doi\.org|\bdoi:|pubmed|pmc\.ncbi|jstor|"
    r"sciencedirect|springer|wiley|nature\.com|sciencemag|acm\.org|ieee",
    re.IGNORECASE)
_ANY_SOURCE = re.compile(r"https?://\S+")


def _has_formal_notation(body):
    return bool(_MATH_INLINE.search(body) or _MATH_UNICODE.search(body))


def _has(element, body):
    if element == "formal_notation":
        return _has_formal_notation(body)
    if element == "primary_source":
        return bool(_PRIMARY_SOURCE.search(body))
    if element == "any_source":
        return bool(_ANY_SOURCE.search(body))
    det = _DETECTORS.get(element)
    return bool(det.search(body)) if det else False


# --- the contracts -----------------------------------------------------------
# word_min/word_max are a BAND, not a target: too short means thin, too long
# means the level isn't being distinguished (the observed failure was every
# level converging on ~770 words).
#
# `required` is the real lever. These escalate deliberately: a level-5 concept
# that contains no notation, no derivation and no primary source is not
# expert-level material no matter how long it is.

# CALIBRATION NOTE — length is a WEAK discriminator here; elements are the real one.
#
# Measured, over three full builds: a concept document has NINE mandatory
# sections, so its length is dominated by template overhead rather than by the
# requested level. The observed natural distribution at mastery 2 was
# 698-1242 words. Any max drawn through the middle of that fails roughly half
# the course for no pedagogical reason — an early 550 cap failed 9 of 12, and a
# later 850 cap still failed 4 of 12, purely on length.
#
# So the bands are deliberately WIDE and overlapping. They exist to catch
# genuine outliers — a 120-word stub, or a padded essay — not to encode the
# level. THE REQUIRED ELEMENTS DO THAT: a level-5 concept must show notation, a
# derivation, an exercise and primary literature; a level-1 concept need only be
# grounded. That was the design argument from the start ("length is the wrong
# proxy"), and enforcing level via word count contradicted it.
#
# If length is ever to discriminate again, the fix is a leaner template at low
# levels, not a tighter cap on a nine-section document.
DEPTH_CONTRACTS = {
    1: {
        "label": "Awareness",
        "word_min": 120, "word_max": 1000,
        "required": ["any_source"],
        "forbidden": [],
    },
    2: {
        "label": "Understanding",
        "word_min": 200, "word_max": 1300,
        "required": ["worked_example", "any_source"],
        "forbidden": [],
    },
    3: {
        "label": "Application",
        "word_min": 320, "word_max": 1500,
        "required": ["formal_definition", "worked_example", "any_source"],
        "forbidden": [],
    },
    4: {
        "label": "Proficiency",
        "word_min": 500, "word_max": 1800,
        "required": [
            "formal_definition", "worked_example", "named_result",
            "derivation_or_proof", "primary_source",
        ],
        "forbidden": [],
    },
    5: {
        "label": "Expertise",
        "word_min": 700, "word_max": 2200,
        "required": [
            # worked_example is retained from level 4 — see the monotonicity
            # note below. An earlier version dropped it here, which meant
            # "Expertise" could legitimately omit something "Proficiency"
            # required. A higher level meaning less rigorous in some dimension
            # is incoherent, and it was caught by a preset test asserting that
            # College-and-above demand real rigor.
            "formal_definition", "worked_example", "formal_notation",
            "named_result", "derivation_or_proof", "exercise",
            "primary_source",
        ],
        "forbidden": [],
    },
}

# INVARIANT: requirements are MONOTONIC — level N+1 requires everything level N
# does, plus more. Without this, "advance the slider" can silently relax a
# requirement, and the level names stop meaning anything. Enforced by
# tests/core/test_depth_contract.py and tests/core/test_presets.py.
for _lvl in sorted(DEPTH_CONTRACTS)[1:]:
    _lower = set(DEPTH_CONTRACTS[_lvl - 1]["required"])
    _here = DEPTH_CONTRACTS[_lvl]["required"]
    for _missing in _lower - set(_here):
        _here.append(_missing)
del _lvl, _lower, _here

# Domains where symbolic notation is not a meaningful rigor signal. Requiring
# equations in a history course would push the generator to fake them, which is
# worse than not requiring them.
#
# CAVEAT — keyword matching on a topic string is unreliable and should not be
# the primary mechanism. Two demonstrations from this repo: "the french
# revolution" contains no history keyword, and "linear algebra" matches none of
# the STEM keywords in course_builder._get_domain_constraints (which lists
# "math" and "algorithm" but not "algebra"). Prefer passing `domain` explicitly
# from the caller, which knows what it asked for. The heuristic is a fallback
# for legacy call sites only.
_NON_FORMAL_DOMAIN = re.compile(
    r"histor|literat|philosoph|\bart\b|music|\blaw\b|polit|language|"
    r"writing|ethic|religio|sociolog|revolution|dynast|empire|\bwar\b|"
    r"civiliz|ancient|medieval|novel|poetry|rhetoric|anthropolog|"
    r"theolog|linguistic|classic",
    re.IGNORECASE)

_FORMAL_DOMAIN = re.compile(
    r"math|algebra|geometry|calculus|statistic|probability|physic|chemistr|"
    r"engineer|comput|algorithm|inference|analysis|topolog|number theory|"
    r"mechanic|thermodynam|circuit|cryptograph|optimi[sz]ation|econometric",
    re.IGNORECASE)

DOMAIN_FORMAL = "formal"
DOMAIN_NARRATIVE = "narrative"


def infer_domain(topic):
    """Best-effort domain guess from a topic string.

    Returns DOMAIN_FORMAL, DOMAIN_NARRATIVE, or None when genuinely unsure —
    None is a real answer here, and callers should treat it as "don't impose
    notation requirements" rather than guessing.
    """
    if not topic:
        return None
    if _NON_FORMAL_DOMAIN.search(topic):
        return DOMAIN_NARRATIVE
    if _FORMAL_DOMAIN.search(topic):
        return DOMAIN_FORMAL
    return None


def contract_for(mastery, topic="", domain=None):
    """Return the contract for a mastery level, adapted to the domain.

    `domain` (DOMAIN_FORMAL / DOMAIN_NARRATIVE) should be supplied by the
    caller when known; `topic` is only used to guess when it isn't.
    """
    c = dict(DEPTH_CONTRACTS.get(int(mastery or 2), DEPTH_CONTRACTS[2]))
    c["required"] = list(c["required"])

    resolved = domain or infer_domain(topic)
    if resolved != DOMAIN_FORMAL:
        # Only demand symbolic notation where we positively believe it belongs.
        # Swap it for a narrative-appropriate rigor requirement rather than
        # dropping rigor altogether.
        if "formal_notation" in c["required"]:
            c["required"] = [e for e in c["required"] if e != "formal_notation"]
            if "named_result" not in c["required"]:
                c["required"].append("named_result")
    c["domain"] = resolved
    return c


def validate_concept(body, mastery, topic="", domain=None, sources=None):
    """Check one concept body against its depth contract.

    Returns (ok: bool, problems: list[str], detail: dict). `problems` names the
    specific deficiency so a regeneration prompt can target it instead of
    retrying blindly.
    """
    c = contract_for(mastery, topic, domain)
    words = len(body.split())

    # Source requirements are satisfied by the RETRIEVED sources, not by the
    # body text. Validation deliberately runs BEFORE the "## Sources" block is
    # appended (so a citation list cannot stand in for a rigor requirement) —
    # which made `any_source`/`primary_source` unsatisfiable by construction:
    # a real run scored 100% citation coverage and 12/12 Sources blocks while
    # still failing `any_source` on 10 of 12 concepts. Pass the sources in.
    src_urls = []
    for s_ in (sources or []):
        u = (s_.get("url") if isinstance(s_, dict) else str(s_)) or ""
        if u:
            src_urls.append(u)
    src_blob = " ".join(src_urls)
    problems = []

    if words < c["word_min"]:
        problems.append(
            f"too short for {c['label']}: {words} words (min {c['word_min']})")
    elif words > c["word_max"]:
        problems.append(
            f"too long for {c['label']}: {words} words (max {c['word_max']})")

    def _satisfied(e):
        if e in ("any_source", "primary_source") and src_blob:
            return _has(e, src_blob) or (e == "any_source")
        return _has(e, body)

    missing = [e for e in c["required"] if not _satisfied(e)]
    for e in missing:
        problems.append(f"missing required element: {e}")

    return (not problems), problems, {
        "words": words,
        "level": c["label"],
        "required": c["required"],
        "missing": missing,
        "word_band": (c["word_min"], c["word_max"]),
    }


def regeneration_hint(problems):
    """Turn contract failures into a targeted instruction for the model."""
    if not problems:
        return ""
    asks = []
    for p in problems:
        if "missing required element: formal_definition" in p:
            asks.append("state a precise formal definition of the key term")
        elif "missing required element: derivation_or_proof" in p:
            asks.append("include a step-by-step derivation or proof of the main result")
        elif "missing required element: worked_example" in p:
            asks.append("work through one concrete example end to end")
        elif "missing required element: exercise" in p:
            asks.append("end with a non-trivial exercise for the learner")
        elif "missing required element: formal_notation" in p:
            asks.append("express the core relationship in mathematical notation")
        elif "missing required element: named_result" in p:
            asks.append("name and state the relevant theorem, law, or principle")
        elif "missing required element: primary_source" in p:
            asks.append("cite at least one primary source (journal article, arXiv, or DOI) "
                        "rather than only general reference sites")
        elif "too short" in p:
            asks.append("expand the explanation substantially — it is too thin for this level")
        elif "too long" in p:
            asks.append("tighten the writing — it is padded for this level")
    return ("The previous draft did not meet the required depth. Revise so that it: "
            + "; ".join(dict.fromkeys(asks)) + ".")


def validate_course(concepts, mastery, topic="", domain=None):
    """Validate many concepts. Returns a summary dict for gate reporting.

    `concepts` is an iterable of (uid, body).
    """
    results, failing = {}, []
    for uid, body in concepts:
        ok, problems, detail = validate_concept(body, mastery, topic, domain)
        results[uid] = {"ok": ok, "problems": problems, **detail}
        if not ok:
            failing.append(uid)
    total = len(results)
    return {
        "total": total,
        "passing": total - len(failing),
        "failing": len(failing),
        "failing_uids": failing,
        "pass_rate": round(100 * (total - len(failing)) / total, 1) if total else 0.0,
        "results": results,
    }
