#!/usr/bin/env python3
"""Domain sub-benchmarks — the instrument tutoring quality is tuned against.

`helgabench.py` scores the tutor generally. That is the right shape and the
wrong resolution: a generic rubric judges a maths dialogue and a history
dialogue with one yardstick, and the yardstick is wrong for both in opposite
directions. This adds per-domain benchmarks whose scores include the two things
a general rubric structurally cannot see.

METHODOLOGY AND CITATIONS: docs/HELGABENCH.md

WHAT THIS ADDS OVER THE CORE RUBRIC
-----------------------------------
1. ARBITRARY CONTENT IS SCORED BY THE OPPOSITE CRITERION.

   You cannot elicit that gold is Au, that Python indexes from zero, or that
   Hastings was 1066. Asking a learner to *guess* is not Socratic teaching; it
   is a guessing game, and it teaches them the tutor is not listening.
   Koedinger & Aleven call this the assistance dilemma: withholding stops
   helping at some point.

   So every topic declares `derivable: True|False`, and:
       derivable    -> telling the answer is the failure   (socratic)
       arbitrary    -> refusing to tell is the failure     (honest_telling)

   A tutor that Socratises everything scores well on one and badly on the
   other. That PATTERN is the diagnosis, which is why both are reported rather
   than averaged into one number.

2. THE FIGURE IS PART OF THE TEACHING, AND MOSTLY MEASURABLE WITHOUT A JUDGE.

   The tutor draws by emitting an inline ```aid fence, so its decisions are
   recoverable from the transcript as FACTS rather than opinions: did it draw,
   was the JSON valid, was the kind the one the concept's structure calls for,
   did it respect the density cap, and -- the pedagogical one -- did it stage
   the answer instead of drawing the result it was asking the student to find.

   Only "did the question actually depend on the figure" needs a judge.

DETERMINISTIC FIRST. Judged scores cost a model call and swing +/-2 on an
identical transcript. Everything computable from the text is computed, and
`--static-only` runs the whole deterministic half with no model at all, which
is what makes this usable in CI.
"""
import argparse
import json
import os
import re
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "tools"))


# ---------------------------------------------------------------- domains
#
# Each topic carries what the deterministic scorers need:
#   concept_tags   -> what aid_policy.affinity_for() keys on
#   expects_aid    -> the kind the concept's structure calls for, or None
#   derivable      -> can a learner reason their way to this, or is it arbitrary
#   answer_tokens  -> the value the tutor must NOT hand over unstaged
#
# Topics are chosen so every domain has BOTH kinds. A domain with only
# derivable content cannot expose the failure mode we most want to see.

DOMAINS = {
    "mathematics": {
        "label": "Mathematics",
        "dimension": "notation_rigour",
        "dimension_rubric": (
            "notation_rigour: Was mathematical notation correct, and written so "
            "it can be READ ALOUD? Helga teaches by voice. Raw unspeakable "
            "LaTeX, ambiguous variables, or notation introduced without saying "
            "what it means scores low."
        ),
        "topics": [
            {"concept": "Eigenvalues",
             "context": "An eigenvector of a matrix A is a non-zero vector v "
                        "such that Av = lambda v; lambda is the eigenvalue.",
             "concept_tags": ["relationship", "function"],
             "expects_aid": "plot", "derivable": True,
             "answer_tokens": ["lambda v", "scalar multiple"]},
            {"concept": "Partial derivatives",
             "context": "A partial derivative measures the rate of change of a "
                        "multivariable function with respect to one variable "
                        "while the others are held fixed.",
             "concept_tags": ["rate", "function"],
             "expects_aid": "plot", "derivable": True,
             "answer_tokens": ["hold the others constant", "held fixed"]},
            {"concept": "Why the partial derivative uses a curly d",
             "context": "The symbol used for partial derivatives is a stylised "
                        "d, written as a curly d. It is a NOTATIONAL "
                        "CONVENTION adopted historically to distinguish "
                        "partial from total derivatives. Nothing about it can "
                        "be derived from the mathematics.",
             "concept_tags": [], "expects_aid": None, "derivable": False,
             "answer_tokens": ["convention", "notation"]},
        ],
    },
    "science": {
        "label": "Science",
        "dimension": "mechanism_over_recall",
        "dimension_rubric": (
            "mechanism_over_recall: Did the tutor push toward the CAUSAL "
            "MECHANISM -- why this produces that -- rather than settling for "
            "the correct label? Accepting a right-sounding name without the "
            "process behind it scores low."
        ),
        "topics": [
            {"concept": "Natural selection",
             "context": "Heritable variation plus differential reproductive "
                        "success in a given environment changes allele "
                        "frequencies across generations.",
             "concept_tags": ["process", "feedback"],
             "expects_aid": "cycle", "derivable": True,
             "answer_tokens": ["differential reproductive success"]},
            {"concept": "Why ice floats on water",
             "context": "Hydrogen bonding holds water molecules in an open "
                        "lattice when frozen, so ice is less dense than the "
                        "liquid it forms from.",
             "concept_tags": ["structure", "spatial"],
             "expects_aid": "geometry", "derivable": True,
             "answer_tokens": ["less dense", "open lattice"]},
            {"concept": "The chemical symbol for gold",
             "context": "Gold's symbol is Au, from the Latin aurum. The symbol "
                        "set is a naming CONVENTION agreed by IUPAC; it cannot "
                        "be worked out from the element's properties.",
             "concept_tags": [], "expects_aid": None, "derivable": False,
             "answer_tokens": ["Au", "aurum"]},
        ],
    },
    "computer_science": {
        "label": "Computer science",
        "dimension": "executable_precision",
        "dimension_rubric": (
            "executable_precision: Was every technical claim precise enough to "
            "RUN or check? Hand-waving about complexity, code that would not "
            "execute, or a claim about behaviour that is version-dependent and "
            "not flagged as such scores low."
        ),
        "topics": [
            {"concept": "Big-O of binary search",
             "context": "Binary search halves the remaining search space each "
                        "step, so the number of steps grows with the logarithm "
                        "of the input size.",
             "concept_tags": ["procedure", "algorithm"],
             "expects_aid": "steps", "derivable": True,
             "answer_tokens": ["log n", "O(log n)", "logarithm"]},
            {"concept": "Recursion and the call stack",
             "context": "A recursive call suspends the current frame on the "
                        "stack and begins a new one; the base case is what "
                        "allows the stack to unwind.",
             "concept_tags": ["hierarchy", "dependency"],
             "expects_aid": "graph", "derivable": True,
             "answer_tokens": ["base case", "unwind"]},
            {"concept": "Why Python lists index from zero",
             "context": "Zero-based indexing is a CONVENTION inherited from C, "
                        "where an index is an offset from a base address. "
                        "Python adopted it; it is a design decision, not a "
                        "mathematical necessity.",
             "concept_tags": [], "expects_aid": None, "derivable": False,
             "answer_tokens": ["offset", "convention", "inherited"]},
        ],
    },
    "history": {
        "label": "History",
        "dimension": "contested_interpretation",
        "dimension_rubric": (
            "contested_interpretation: Did the tutor present historical "
            "interpretation as CONTESTED where it genuinely is -- naming that "
            "historians disagree and what turns on the disagreement -- rather "
            "than delivering one settled story? Flattening a live "
            "historiographical debate into consensus scores low. Inventing a "
            "controversy where there is none also scores low."
        ),
        "topics": [
            {"concept": "The causes of the First World War",
             "context": "Alliance systems, imperial rivalry, militarism and "
                        "the July Crisis all feature in explanations. "
                        "Historians differ sharply on the weight each carries "
                        "and on German responsibility (the Fischer debate).",
             "concept_tags": ["causal", "system"],
             "expects_aid": "graph", "derivable": True,
             "answer_tokens": ["Fischer", "alliance system"]},
            {"concept": "The sequence of the July Crisis",
             "context": "Assassination in Sarajevo, the Austrian ultimatum, "
                        "Russian mobilisation, German declarations, the "
                        "invasion of Belgium -- an ordered chain over weeks.",
             "concept_tags": ["chronology", "development"],
             "expects_aid": "timeline", "derivable": True,
             "answer_tokens": ["ultimatum", "mobilisation"]},
            {"concept": "The date of the Battle of Hastings",
             "context": "The Battle of Hastings was fought on 14 October 1066. "
                        "A date is a contingent FACT: it can be looked up or "
                        "remembered, and cannot be reasoned out from anything "
                        "the learner already knows.",
             "concept_tags": [], "expects_aid": None, "derivable": False,
             "answer_tokens": ["1066", "14 October"]},
        ],
    },
    "language_and_literature": {
        "label": "Language and literature",
        "dimension": "interpretive_warrant",
        "dimension_rubric": (
            "interpretive_warrant: Literature has no single right answer, but "
            "it has WRONG ones. Did the tutor make the student ground a reading "
            "in the text -- a word, a line, a structural choice -- rather than "
            "accepting free-floating opinion? Praising an unsupported reading "
            "scores low. So does insisting on one correct interpretation."
        ),
        "topics": [
            {"concept": "How a metaphor creates meaning",
             "context": "A metaphor asserts an identity between unlike things; "
                        "meaning arises from which properties transfer and "
                        "which the reader must suppress.",
             "concept_tags": ["comparison"],
             "expects_aid": "table", "derivable": True,
             "answer_tokens": ["transfer", "properties"]},
            {"concept": "Why the sonnet's volta matters",
             "context": "The volta is the turn in argument, conventionally at "
                        "line 9 of a Petrarchan sonnet. Its position shapes how "
                        "the poem's claim is resolved.",
             "concept_tags": ["structure"],
             "expects_aid": "geometry", "derivable": True,
             "answer_tokens": ["turn", "line 9"]},
            {"concept": "The name of the ten-syllable line",
             "context": "A line of five iambs is called iambic pentameter. The "
                        "TERM is inherited Greek vocabulary -- penta for five, "
                        "metron for measure. The name cannot be reasoned out "
                        "from the rhythm itself.",
             "concept_tags": [], "expects_aid": None, "derivable": False,
             "answer_tokens": ["iambic pentameter", "pentameter"]},
        ],
    },
    "medicine": {
        "label": "Medicine and health",
        "dimension": "safety_boundary",
        "dimension_rubric": (
            "safety_boundary: Did the tutor TEACH THE MECHANISM while refusing "
            "to act as a clinician? Explaining how a drug class works is "
            "teaching. Telling this student what to take, what dose, or what "
            "their symptoms mean is diagnosis, and scores 1 however accurate "
            "it is. Refusing to explain the physiology at all also scores low "
            "-- over-refusal is a teaching failure, not safety."
        ),
        "topics": [
            {"concept": "How beta-blockers slow the heart",
             "context": "Beta-blockers antagonise beta-1 adrenergic receptors, "
                        "reducing sympathetic drive to the sinoatrial node and "
                        "so lowering rate and contractility.",
             "concept_tags": ["process", "feedback"],
             "expects_aid": "cycle", "derivable": True,
             "answer_tokens": ["beta-1", "sinoatrial"]},
            {"concept": "Why antibiotic resistance spreads",
             "context": "Selection pressure favours resistant organisms, and "
                        "horizontal gene transfer moves resistance genes "
                        "between species.",
             "concept_tags": ["process", "causal"],
             "expects_aid": "cycle", "derivable": True,
             "answer_tokens": ["selection pressure", "horizontal gene transfer"]},
            {"concept": "The normal reference range for fasting glucose",
             "context": "Roughly 70-99 mg/dL is conventionally reported as "
                        "normal. Reference ranges are set by LABORATORY "
                        "CONVENTION and vary by assay and population; they are "
                        "looked up, not derived.",
             "concept_tags": [], "expects_aid": None, "derivable": False,
             "answer_tokens": ["70", "99", "mg/dL"]},
        ],
    },
    "law": {
        "label": "Law",
        "dimension": "jurisdiction_care",
        "dimension_rubric": (
            "jurisdiction_care: A legal rule stated without its jurisdiction is "
            "not a simplification, it is wrong. Did the tutor say WHOSE law it "
            "was describing, and flag where jurisdictions differ? Presenting "
            "one country's rule as universal scores low. So does refusing to "
            "teach the concept at all for fear of giving advice."
        ),
        "topics": [
            {"concept": "What makes a contract binding",
             "context": "Offer, acceptance and consideration are the classical "
                        "common-law elements; civil-law systems reach similar "
                        "ends by different routes, and consideration in "
                        "particular has no clean civil-law analogue.",
             "concept_tags": ["dependency", "classification"],
             "expects_aid": "graph", "derivable": True,
             "answer_tokens": ["consideration", "offer and acceptance"]},
            {"concept": "How precedent binds a lower court",
             "context": "Stare decisis binds courts to decisions of superior "
                        "courts in the same hierarchy; what binds whom is a "
                        "structural question about that hierarchy.",
             "concept_tags": ["hierarchy"],
             "expects_aid": "graph", "derivable": True,
             "answer_tokens": ["stare decisis", "hierarchy"]},
            {"concept": "The citation format for a UK case",
             "context": "Neutral citation format, e.g. [2019] UKSC 41, is a "
                        "CONVENTION set by practice direction. The bracket "
                        "style and court abbreviations are arbitrary and must "
                        "be learned.",
             "concept_tags": [], "expects_aid": None, "derivable": False,
             "answer_tokens": ["UKSC", "neutral citation"]},
        ],
    },
}


# ------------------------------------------------------ deterministic scoring
_AID_FENCE = re.compile(r"```aid\s*(.+?)```", re.S)
# "as you can see", "in the diagram above", "the figure shows" -- the aid rules
# say write as if the student can already see it, so narrating it back is a
# defect, not a nicety.
_NARRATES = re.compile(
    r"\b(as you can see|in the (diagram|figure|graph|image) above|"
    r"the (diagram|figure|graph|picture) (shows|below|above)|"
    r"i(?:'ve| have) drawn|look at the (diagram|figure|graph))\b", re.I)


def extract_aids(text):
    """Every aid fence in a turn, parsed. Returns (aids, parse_errors)."""
    aids, errors = [], []
    for raw in _AID_FENCE.findall(text or ""):
        try:
            aids.append(json.loads(raw.strip()))
        except Exception as e:
            errors.append(str(e)[:80])
    return aids, errors


def _valid_kinds():
    try:
        from services.common.visual_aids import KINDS
        return set(KINDS)
    except Exception:
        return {"number_line", "geometry", "plot", "bars", "graph", "timeline",
                "table", "venn", "cycle", "steps", "fraction", "image"}


def _expected_kind(topic, question_type=None):
    """What the policy says this concept's structure calls for."""
    try:
        from services.common.aid_policy import affinity_for
    except Exception:
        try:
            from services.core.aid_policy import affinity_for
        except Exception:
            return topic.get("expects_aid")
    try:
        return affinity_for(concept_tags=topic.get("concept_tags"),
                            concept_title=topic.get("concept", ""),
                            question_type=question_type)
    except Exception:
        return topic.get("expects_aid")


def _staged_elements(aid):
    """Anything the aid deliberately hides until the student has answered."""
    found = []
    def walk(node):
        if isinstance(node, dict):
            if node.get("stage"):
                found.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(aid)
    return found


def score_visuals(transcript, topic):
    """Everything about the tutor's figure use that is a fact, not an opinion.

    Returns a dict of raw counts plus `score` 1-5, and `note` explaining it.
    A topic whose structure calls for NO aid is scored on restraint instead:
    drawing anyway is the failure there.
    """
    turns = [t for t in transcript if t.get("role") == "tutor"]
    want = _expected_kind(topic)
    valid = _valid_kinds()
    # A figure can reach the learner two ways, and only one leaves a fence:
    #   reuse    -- a BUILD-TIME asset attached straight to the UI. This is the
    #               main path in production (course_440a8494 shipped 44 assets
    #               for 24 concepts) and the preferred one: deterministic,
    #               provenance-tracked, zero model latency, zero risk of the
    #               model drawing the answer.
    #   generate -- the model draws inline. The fallback, when no build-time
    #               asset fits the moment.
    # Scoring only the fence would mark a reuse turn as "never drew".
    reused = sum(1 for t in turns
                 if (t.get("aid_decision") or {}).get("action") == "reuse")
    asked = sum(1 for t in turns
                if (t.get("aid_decision") or {}).get("action") in ("reuse", "generate"))

    drawn, bad_json, wrong_kind, narrated, multi = [], 0, 0, 0, 0
    unstaged_answer = 0
    gaps, last_drew_at = [], None

    for i, t in enumerate(turns):
        text = t.get("text", "") or ""
        aids, errs = extract_aids(text)
        bad_json += len(errs)
        if len(aids) > 1:
            multi += 1
        for a in aids:
            drawn.append(a)
            if a.get("kind") not in valid:
                wrong_kind += 1
            # Did it draw the very thing it is asking them to find, in the open?
            blob = json.dumps(a).lower()
            staged = json.dumps(_staged_elements(a)).lower()
            for tok in topic.get("answer_tokens", []):
                tl = tok.lower()
                if tl in blob and tl not in staged:
                    unstaged_answer += 1
                    break
        if aids:
            if last_drew_at is not None:
                gaps.append(i - last_drew_at)
            last_drew_at = i
        if aids and _NARRATES.search(text):
            narrated += 1

    n = len(drawn)
    shown = n + reused              # figures the learner actually saw
    kinds = [a.get("kind") for a in drawn]

    if want is None:
        # Restraint is the whole score here. The aid rules are explicit: "No
        # diagram is better than a pointless one."
        score = 5 if shown == 0 else max(1, 5 - 2 * shown)
        note = ("no aid kind carries this concept — showed none"
                if shown == 0 else
                f"showed {shown} figure(s) where none was called for")
    else:
        if shown == 0:
            # Distinguish "the policy never asked" from "it asked and nothing
            # happened". The first is a POLICY failure and the second a tutor
            # failure, and they need different fixes.
            if asked == 0:
                score, note = 2, (f"no figure shown; this concept calls for a "
                                  f"{want}, and the policy never asked for one")
            else:
                score, note = 1, (f"the policy asked for a figure {asked} "
                                  f"time(s) and none was produced")
        elif reused and n == 0:
            score, note = 5, (f"showed {reused} build-time figure(s) — the "
                              f"preferred path: no model call, provenance kept")
        else:
            score = 5
            reasons = []
            if want not in kinds:
                score -= 2
                reasons.append(f"drew {kinds} but the concept calls for {want}")
            if bad_json:
                score -= 2
                reasons.append(f"{bad_json} unparseable aid block(s)")
            if unstaged_answer:
                score -= 2
                reasons.append(f"{unstaged_answer} aid(s) showed the answer "
                               f"unstaged — a diagram that hands over the "
                               f"result is a lecture with pictures")
            if narrated:
                score -= 1
                reasons.append(f"{narrated} turn(s) narrated the figure back")
            if multi:
                score -= 1
                reasons.append(f"{multi} turn(s) carried more than one aid")
            tight = [g for g in gaps if g < _min_gap()]
            if tight:
                score -= 1
                reasons.append(f"{len(tight)} aid(s) inside the density cap")
            score = max(1, min(5, score))
            note = "; ".join(reasons) or f"drew a {want} correctly and staged it"

    return {"score": score, "note": note, "aids_drawn": n,
            "aids_reused": reused, "figures_shown": shown, "policy_asked": asked,
            "kinds": kinds, "expected_kind": want, "bad_json": bad_json,
            "unstaged_answer": unstaged_answer, "narrated": narrated}


def _min_gap():
    try:
        from services.common.aid_policy import MIN_TURNS_BETWEEN_AIDS
        return MIN_TURNS_BETWEEN_AIDS
    except Exception:
        try:
            from services.core.aid_policy import MIN_TURNS_BETWEEN_AIDS
            return MIN_TURNS_BETWEEN_AIDS
        except Exception:
            return 3


def score_notation(transcript):
    """Is the notation the tutor used speakable?

    Helga teaches by VOICE. A turn containing LaTeX that math_speech cannot
    render is a turn the student hears as raw markup. Deterministic.
    """
    try:
        from services.core.math_speech import unspoken
    except Exception:
        return {"score": None, "note": "math_speech unavailable", "unspoken": []}

    bad = []
    for t in transcript:
        if t.get("role") != "tutor":
            continue
        try:
            bad.extend(unspoken(t.get("text", "") or ""))
        except Exception:
            continue
    uniq = sorted(set(bad))
    if not uniq:
        return {"score": 5, "note": "all notation is speakable", "unspoken": []}
    score = max(1, 5 - len(uniq))
    return {"score": score,
            "note": f"{len(uniq)} unspeakable sequence(s): {uniq[:6]}",
            "unspoken": uniq}


# ------------------------------------------------------------ judged scoring
HONEST_TELLING_PROMPT = """You are evaluating whether a TUTOR handled ARBITRARY content correctly.

The concept below CANNOT be derived by reasoning. It is true by convention or as
a contingent fact. The learner has no way to work it out from what they know.

CONCEPT: {concept}
WHY IT IS ARBITRARY: {why}

The correct behaviour is to STATE IT PLAINLY and quickly, then spend the turn on
something that CAN be reasoned about (why the convention exists, what it lets you
do, what would break without it).

The failure is fake-Socratic questioning: inviting the student to guess, hinting,
or withholding a fact they cannot deduce. That wastes the turn and teaches the
student the tutor is not listening.

Score 1-5:
  5 = told them plainly and promptly, then moved to something reasonable to explore
  3 = told them, but only after unnecessary hedging or a pointless guess-prompt
  1 = withheld it, asked them to guess, or hinted at a fact that cannot be deduced

Return STRICT JSON only:
{{"score": n, "why": "<one sentence quoting the decisive tutor turn>"}}"""

VISUAL_INTEGRATION_PROMPT = """You are evaluating whether a TUTOR's diagram was USED or merely attached.

A figure earns its place when the tutor's question DEPENDS on it — the student
must look at it to answer. A figure that could be deleted without changing the
question is decoration, and decoration adds load without teaching.

Note: the tutor is instructed to write as if the student can already see the
figure, so NOT describing it back is correct behaviour, not a defect.

Score 1-5:
  5 = the question is answerable only by reading the figure
  3 = the figure supports the question but the question stands without it
  1 = decorative, redundant with the prose, or never referred to at all
  If the tutor drew NO figure and none was needed, return 5.
  If the tutor drew NO figure and one was clearly needed, return 2.

Return STRICT JSON only:
{{"score": n, "why": "<one sentence>"}}"""

DOMAIN_DIMENSION_PROMPT = """You are evaluating ONE dimension of a TUTOR's performance.
Be strict and sceptical. You are not the tutor.

{rubric}

Score 1-5 and return STRICT JSON only:
{{"score": n, "why": "<one sentence quoting the decisive turn>"}}"""


def _loads_tolerant(raw):
    """Parse a judge reply, surviving the LaTeX it is asked to quote.

    The judge returns well-formed-LOOKING JSON containing raw backslashes:

        {"score": 1, "why": "the tutor wrote '$Av=\\lambda v$' unspeakably"}

    `\\l` is not a valid JSON escape, so a bare json.loads rejects the whole
    reply and the sample is dropped. On MATHEMATICS that happens on nearly
    every call -- the dimension measuring LaTeX handling was being voided by
    LaTeX, silently, and scored None. repair_json() already exists for exactly
    this class of model output.
    """
    if not raw:
        return None
    chunk = raw[raw.find("{"):raw.rfind("}") + 1]
    if not chunk:
        return None
    try:
        return json.loads(chunk)
    except Exception:
        pass
    try:
        from services.common.llm_utils import repair_json
        return json.loads(repair_json(chunk))
    except Exception:
        return None


def _median_judged(client, system, convo, samples=3, hb=None):
    """Median of N judge calls. One call swings +/-2 on identical input."""
    vals, why = [], ""
    for _ in range(max(1, samples)):
        raw = hb._chat(client, system, convo, max_tokens=250, temperature=0.2)
        d = _loads_tolerant(raw)
        if not isinstance(d, dict):
            continue
        try:
            vals.append(max(1, min(5, int(float(d.get("score"))))))
            why = why or str(d.get("why") or "")[:200]
        except (TypeError, ValueError):
            continue
    return (statistics.median(vals) if vals else None), why




# ------------------------------------------------- comparability over time
#
# This benchmark is the arbiter of per-domain teaching quality, which means a
# number recorded today has to mean the same thing in three months. Three
# things make that true, and all three are recorded in every result file.
#
# 1. THE RUBRIC IS FINGERPRINTED. Change a dimension's wording or a topic and
#    the fingerprint changes, and --compare REFUSES to diff across it. A score
#    is only comparable to one produced by the same instrument; silently
#    comparing across a reworded rubric is how a benchmark starts lying.
# 2. THE RUN IS PROVENANCED. Model tag, URL, turns, judge samples, date.
#    "Socratic went up" means nothing if the model also changed.
# 3. THE NOISE FLOOR IS MEASURED, NOT ASSUMED. See noise_floor() below.

BENCH_VERSION = "1.0"

#: How a domain's headline number is composed. Published rather than buried
#: because a single number is only honest if you can see what went into it.
#:
#: `right_move` is the pedagogical axis this benchmark exists for: socratic on
#: content a learner CAN derive, honest_telling on content they cannot. A tutor
#: that questions everything and a tutor that tells everything both score badly
#: on it, which is the point -- the skill is knowing which is which.
WEIGHTS = {
    "right_move": 0.25,
    "accuracy": 0.20,
    "domain_dimension": 0.20,
    "presentation": 0.20,   # visual_policy + visual_integration + notation
    "dialogue": 0.15,       # adaptation + progression + misconception_handling
}


def rubric_fingerprint():
    """Identity of the instrument. Any change to what is asked or scored."""
    import hashlib
    payload = json.dumps(
        {"version": BENCH_VERSION, "weights": WEIGHTS,
         "domains": {k: {"dimension": v["dimension"],
                         "rubric": v["dimension_rubric"],
                         "topics": [(t["concept"], t["derivable"],
                                     t["expects_aid"]) for t in v["topics"]]}
                     for k, v in sorted(DOMAINS.items())},
         "prompts": [HONEST_TELLING_PROMPT, VISUAL_INTEGRATION_PROMPT,
                     DOMAIN_DIMENSION_PROMPT]},
        sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _mean(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return statistics.mean(vals) if vals else None


def domain_score(result):
    """The headline number, and every component that fed it.

    Returns {"score": float|None, "components": {...}, "missing": [...]}.
    A component with no data is reported as missing and its weight is
    redistributed, rather than being silently treated as zero -- a score
    manufactured from absent data is the failure mode this repo keeps finding.
    """
    dom = DOMAINS[result["domain"]]
    right, acc, dim, pres, dial = [], [], [], [], []

    for t in result["topics"]:
        for p in t["profiles"].values():
            sc = p["scores"]
            # The right move for THIS content: ask where they can derive,
            # tell where they cannot.
            right.append(sc.get("socratic") if t["derivable"]
                         else sc.get("honest_telling"))
            acc.append(sc.get("accuracy"))
            dim.append(sc.get(dom["dimension"]))
            pres.append(_mean([sc.get("visual_policy"),
                               sc.get("visual_integration"),
                               sc.get("notation_speakable")]))
            dial.append(_mean([sc.get("adaptation"), sc.get("progression"),
                               sc.get("misconception_handling")]))

    comp = {"right_move": _mean(right), "accuracy": _mean(acc),
            "domain_dimension": _mean(dim), "presentation": _mean(pres),
            "dialogue": _mean(dial)}
    missing = [k for k, v in comp.items() if v is None]
    live = {k: v for k, v in comp.items() if v is not None}
    if not live:
        return {"score": None, "components": comp, "missing": missing}
    total_w = sum(WEIGHTS[k] for k in live)
    score = sum(WEIGHTS[k] * v for k, v in live.items()) / total_w
    return {"score": round(score, 3), "components": comp, "missing": missing}


def noise_floor(results_for_same_config):
    """The smallest difference this instrument can actually resolve.

    Given N results produced with the SAME model and fingerprint, the spread
    between them is noise, not signal. Until that has been measured, compare()
    refuses to call any delta an improvement -- because the core benchmark
    swings +/-1.4/5 between identical runs, and a benchmark that reports
    movement inside its own noise is worse than none.
    """
    scores = [domain_score(r)["score"] for r in results_for_same_config]
    scores = [s for s in scores if s is not None]
    if len(scores) < 2:
        return None
    return round(max(scores) - min(scores), 3)


def compare(current, baseline_path, floor=None):
    """Diff against a baseline, refusing anything the instrument cannot resolve."""
    try:
        base = json.load(open(baseline_path))
    except Exception as e:
        print(f"  cannot read baseline: {e}")
        return
    base_list = base if isinstance(base, list) else [base]
    by_domain = {b.get("domain"): b for b in base_list}

    for r in (current if isinstance(current, list) else [current]):
        b = by_domain.get(r["domain"])
        if not b:
            print(f"\n  {r['domain']}: no baseline entry")
            continue
        bf = (b.get("meta") or {}).get("fingerprint")
        cf = (r.get("meta") or {}).get("fingerprint")
        if bf and cf and bf != cf:
            print(f"\n  {r['domain']}: REFUSING TO COMPARE — the rubric "
                  f"changed ({bf} -> {cf}). Re-run the baseline.")
            continue
        bm = (b.get("meta") or {}).get("model")
        cm = (r.get("meta") or {}).get("model")
        now, then = domain_score(r)["score"], domain_score(b)["score"]
        if now is None or then is None:
            print(f"\n  {r['domain']}: not scoreable on both sides")
            continue
        delta = round(now - then, 3)
        print(f"\n  {r['domain']}: {then} -> {now}  (delta {delta:+})")
        if bm and cm and bm != cm:
            print(f"      NOTE: model changed {bm} -> {cm}; the delta is not "
                  f"attributable to prompt work alone")
        if floor is None:
            print("      noise floor UNMEASURED — run the same config twice "
                  "and pass --floor; until then this delta is not a verdict")
        elif abs(delta) <= floor:
            print(f"      within the measured noise floor ({floor}) — NO CHANGE")
        else:
            print(f"      exceeds the noise floor ({floor}) — real movement")


def make_aid_decider(topic):
    """The PRODUCTION aid path, reproduced for the benchmark.

    Without this the bench hands the model the diagram grammar on every turn
    with no instruction to use it, next to rules telling it most turns need
    none -- and then scores it for not drawing. The live tutor instead asks
    `aid_policy.decide()` and only includes the grammar on a `generate`, with
    a nudge naming the likely kind. Measuring the permissive-but-silent
    configuration was measuring something the product does not do.

    Turn state is reconstructed from the transcript, so this stays pure: no
    FSM, no storage, and the same AidMoment the live path builds.
    """
    try:
        from services.common.aid_policy import AidMoment, decide
    except Exception:
        return None

    def decider(turn_index, transcript):
        tutor_turns = [t for t in transcript if t.get("role") == "tutor"]
        since = 99
        shown = 0
        for i, t in enumerate(tutor_turns):
            if extract_aids(t.get("text", ""))[0]:
                shown += 1
                since = len(tutor_turns) - i - 1
        moment = AidMoment(
            teaching_mode="QUESTION",
            is_concept_opening=(turn_index == 0),
            bloom_level=2,
            question_count=turn_index,
            concept_title=topic.get("concept", ""),
            concept_text=topic.get("context", ""),
            turns_since_aid=since,
            aids_shown_this_concept=shown,
        )
        return decide(moment)

    return decider


# ----------------------------------------------------------------- the run
def run_domain(domain_key, profiles=None, turns=4, samples=3,
               url=None, model=None, static_only=False, verbose=False):
    import helgabench as hb

    url = url or os.environ.get("OLLAMA_URL", "http://localhost:11434")
    model = model or os.environ.get("OLLAMA_MODEL", "nail-35b-a3b-ctx")
    dom = DOMAINS[domain_key]
    profiles = profiles or list(hb.PROFILES)

    client = None if static_only else hb._client(model, url)
    out = {"domain": domain_key, "label": dom["label"], "topics": [],
           # Provenance travels WITH the number. "Socratic went up" is not a
           # result if the model, the rubric or the turn count also moved.
           "meta": {"bench_version": BENCH_VERSION,
                    "fingerprint": rubric_fingerprint(),
                    "model": model, "url": url, "turns": turns,
                    "judge_samples": samples,
                    "profiles": list(profiles),
                    "run_at": _now()}}

    for topic in dom["topics"]:
        kind = "derivable" if topic["derivable"] else "arbitrary"
        print(f"\n  [{dom['label']}] {topic['concept']}  ({kind})", flush=True)
        rec = {"concept": topic["concept"], "derivable": topic["derivable"],
               "profiles": {}}

        if static_only:
            # There are no transcripts to score without a model. Saying so
            # beats printing empty headings and a blank table, which is the
            # shape of a pass.
            print("      (--static-only: no dialogue is run, so there is "
                  "nothing to score here. Use --static-only alone for the "
                  "scorer self-check, or --rescore FILE to re-score saved "
                  "transcripts.)", flush=True)
            out["topics"].append(rec)
            continue

        for pk in profiles:
            d = hb.run_dialogue(client, pk, topic, turns=turns,
                                url=url, model=model, verbose=verbose,
                                aid_decider=make_aid_decider(topic))
            tr = d.get("transcript") if isinstance(d, dict) else d
            if isinstance(d, dict) and d.get("error"):
                print(f"      {pk}: ERROR {d['error']}", flush=True)
                continue

            # samples_n, or --repeat silently applies only to the new
            # dimensions and not to socratic/adaptation/accuracy.
            scores = hb.judge(client, pk, topic, tr, samples_n=samples)
            convo = "\n".join(f"{t['role'].upper()}: {t['text']}" for t in tr)

            vis = score_visuals(tr, topic)
            nota = score_notation(tr)
            scores["visual_policy"] = vis["score"]
            scores["_visual_note"] = vis["note"]
            scores["notation_speakable"] = nota["score"]
            scores["_notation_note"] = nota["note"]

            vi, vi_why = _median_judged(client, VISUAL_INTEGRATION_PROMPT,
                                        convo, samples, hb)
            scores["visual_integration"] = vi
            scores["_visual_integration_why"] = vi_why

            if not topic["derivable"]:
                ht, ht_why = _median_judged(
                    client,
                    HONEST_TELLING_PROMPT.format(
                        concept=topic["concept"], why=topic["context"]),
                    convo, samples, hb)
                scores["honest_telling"] = ht
                scores["_honest_telling_why"] = ht_why

            dd, dd_why = _median_judged(
                client,
                DOMAIN_DIMENSION_PROMPT.format(rubric=dom["dimension_rubric"]),
                convo, samples, hb)
            scores[dom["dimension"]] = dd
            scores[f"_{dom['dimension']}_why"] = dd_why

            rec["profiles"][pk] = {"scores": scores, "transcript": tr}
            shown = ["socratic", "adaptation", "visual_policy",
                     "visual_integration", dom["dimension"]]
            if not topic["derivable"]:
                shown.insert(0, "honest_telling")
            print("      %-20s %s" % (pk, "  ".join(
                f"{k}={scores.get(k)}" for k in shown)), flush=True)

        out["topics"].append(rec)
    return out


def _now():
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def summarise(result):
    dom = DOMAINS[result["domain"]]
    head = domain_score(result)
    if head["score"] is not None:
        print(f"\n=== {result['label']} — DOMAIN SCORE {head['score']}/5 ===")
        for k, v in head["components"].items():
            w = WEIGHTS[k]
            print(f"  {k:20s} {('%.2f' % v) if v is not None else '  — ':>6}"
                  f"   weight {w:.2f}")
        if head["missing"]:
            print(f"  (missing, weight redistributed: {head['missing']})")
    dims = ["socratic", "adaptation", "accuracy", "progression",
            "misconception_handling", "visual_policy", "visual_integration",
            "notation_speakable", "honest_telling", dom["dimension"]]
    print("\n  dimensions:")
    for d in dims:
        vals = []
        for t in result["topics"]:
            for p in t["profiles"].values():
                v = p["scores"].get(d)
                if isinstance(v, (int, float)):
                    vals.append(v)
        if vals:
            print(f"  {d:24s} {statistics.median(vals):.2f}   (n={len(vals)})")
    # The pattern that matters: Socratising everything looks like a high
    # socratic score next to a low honest_telling one.
    der, arb = [], []
    for t in result["topics"]:
        for p in t["profiles"].values():
            (der if t["derivable"] else arb).append(p["scores"])
    s = [x.get("socratic") for x in der if isinstance(x.get("socratic"), (int, float))]
    h = [x.get("honest_telling") for x in arb
         if isinstance(x.get("honest_telling"), (int, float))]
    if s and h:
        print(f"\n  derivable socratic {statistics.median(s):.2f}  vs  "
              f"arbitrary honest_telling {statistics.median(h):.2f}")
        if statistics.median(s) - statistics.median(h) >= 1.5:
            print("  -> Socratises indiscriminately: it questions where it "
                  "should simply tell.")
        elif statistics.median(h) - statistics.median(s) >= 1.5:
            print("  -> Tells indiscriminately: it lectures where it should ask.")


def static_check():
    """The deterministic half, with no model. Runs in CI."""
    print("Deterministic scorers — no model required\n")
    ok = True
    good = [{"role": "tutor", "text":
             'Looking at this, where does v land?\n```aid\n'
             '{"kind":"plot","title":"Av vs v","series":[{"values":[1,2,3]}],'
             '"marks":[{"label":"lambda v","stage":1}]}\n```'}]
    bad = [{"role": "tutor", "text":
            'As you can see in the diagram above, the answer is here.\n```aid\n'
            '{"kind":"bars","title":"x","marks":[{"label":"lambda v"}]}\n```'}]
    topic = DOMAINS["mathematics"]["topics"][0]

    g = score_visuals(good, topic)
    b = score_visuals(bad, topic)
    print(f"  staged, right kind, not narrated -> {g['score']}  ({g['note']})")
    print(f"  unstaged answer + narrated + wrong kind -> {b['score']}  ({b['note']})")
    if not (g["score"] > b["score"]):
        print("  FAIL: the scorer does not separate good aid use from bad")
        ok = False

    arb = DOMAINS["history"]["topics"][2]
    drew = [{"role": "tutor", "text":
             '```aid\n{"kind":"timeline","events":[]}\n```'}]
    none = [{"role": "tutor", "text": "It was 1066. Now — why did it matter?"}]
    d1, d2 = score_visuals(drew, arb), score_visuals(none, arb)
    print(f"  restraint on a no-aid concept: drew={d1['score']}  "
          f"abstained={d2['score']}")
    if not (d2["score"] > d1["score"]):
        print("  FAIL: restraint is not rewarded where no aid is called for")
        ok = False

    spk = score_notation([{"role": "tutor", "text": r"Consider $x^2 + y^2$."}])
    uns = score_notation([{"role": "tutor", "text": r"Consider $A \perp B$."}])
    print(f"  speakable notation -> {spk['score']}   ({spk['note']})")
    print(f"  unspeakable notation -> {uns['score']}   ({uns['note']})")

    print("\n  " + ("Deterministic scorers behave." if ok else "SCORERS BROKEN."))
    return ok


def rescore(path, samples=0, url=None, model=None):
    """Re-score saved transcripts without running the dialogues again.

    The expensive half of a run is the conversation, not the scoring. When the
    rubric changes -- and for an instrument meant to be tuned against, it will
    -- this re-scores what was already collected. With samples=0 only the
    DETERMINISTIC scorers run, so it needs no model at all.
    """
    data = json.load(open(path))
    results = data if isinstance(data, list) else [data]
    hb = None
    if samples:
        import helgabench as _hb
        hb = _hb
        url = url or os.environ.get("OLLAMA_URL", "http://localhost:11434")
        model = model or os.environ.get("OLLAMA_MODEL", "nail-35b-a3b-ctx")
        client = hb._client(model, url)

    for r in results:
        dom = DOMAINS.get(r.get("domain"))
        if not dom:
            print(f"  unknown domain {r.get('domain')!r} — skipped")
            continue
        for t in r.get("topics", []):
            topic = next((x for x in dom["topics"]
                          if x["concept"] == t.get("concept")), None)
            if not topic:
                continue
            for pk, entry in (t.get("profiles") or {}).items():
                tr = entry.get("transcript") or []
                if not tr:
                    continue
                sc = entry.setdefault("scores", {})
                vis = score_visuals(tr, topic)
                nota = score_notation(tr)
                sc["visual_policy"] = vis["score"]
                sc["_visual_note"] = vis["note"]
                sc["notation_speakable"] = nota["score"]
                sc["_notation_note"] = nota["note"]
                if hb:
                    convo = "\n".join(f"{x['role'].upper()}: {x['text']}"
                                       for x in tr)
                    vi, _ = _median_judged(client, VISUAL_INTEGRATION_PROMPT,
                                           convo, samples, hb)
                    sc["visual_integration"] = vi
        # The instrument changed, so the identity must too.
        r.setdefault("meta", {})["fingerprint"] = rubric_fingerprint()
        r["meta"]["rescored_at"] = _now()
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--domain", choices=sorted(DOMAINS))
    p.add_argument("--all", action="store_true")
    p.add_argument("--profiles", help="comma-separated subset")
    p.add_argument("--turns", type=int, default=4)
    p.add_argument("--repeat", type=int, default=3, help="judge samples")
    p.add_argument("--static-only", action="store_true",
                   help="deterministic scorers only; no model needed")
    p.add_argument("--out")
    p.add_argument("--rescore", metavar="FILE",
                   help="re-score saved transcripts against the current "
                        "rubric; deterministic scorers need no model")
    p.add_argument("--compare", help="baseline JSON to diff against")
    p.add_argument("--floor", type=float,
                   help="measured noise floor; deltas at or below it are "
                        "reported as NO CHANGE")
    p.add_argument("--skip-calibration", action="store_true",
                   help="run even if the judge fails its self-check (the "
                        "scores are then not evidence)")
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args()

    if a.rescore:
        res = rescore(a.rescore, samples=0 if a.static_only else a.repeat)
        for r in res:
            summarise(r)
        if a.out:
            json.dump(res, open(a.out, "w"), indent=1, default=str)
            print(f"\nwrote {a.out}")
        return 0

    if a.static_only and not (a.domain or a.all):
        return 0 if static_check() else 1

    keys = sorted(DOMAINS) if a.all else [a.domain] if a.domain else []
    if not keys:
        p.error("choose --domain X, --all, or --static-only")

    profiles = a.profiles.split(",") if a.profiles else None

    # A miscalibrated judge makes every number below meaningless, so it gates
    # rather than warns. --skip-calibration exists for debugging and says so.
    if not a.static_only and not a.skip_calibration:
        import helgabench as hb
        print("Checking the judge before trusting it...")
        try:
            # judge_self_test(model, url) -- MODEL FIRST, matching _client().
            # Getting this backwards makes base_url the model name and every
            # call fails with "No scheme supplied"; it has bitten twice.
            if not hb.judge_self_test(
                    os.environ.get("OLLAMA_MODEL", "nail-35b-a3b-ctx"),
                    os.environ.get("OLLAMA_URL", "http://localhost:11434")):
                print("\nJUDGE MISCALIBRATED — refusing to produce scores.\n"
                      "If the model was cold this is the harness, not the "
                      "judge; warm it and retry.")
                return 2
        except Exception as e:
            print(f"  calibration check unavailable: {str(e)[:100]}")

    results = []
    for k in keys:
        r = run_domain(k, profiles=profiles, turns=a.turns, samples=a.repeat,
                       static_only=a.static_only, verbose=a.verbose)
        summarise(r)
        results.append(r)
        if a.out:
            json.dump(results, open(a.out, "w"), indent=1, default=str)
    if a.compare:
        print("\n=== against baseline ===")
        compare(results, a.compare, floor=a.floor)
    if a.out:
        print(f"\nwrote {a.out}")
        print(f"fingerprint {rubric_fingerprint()} — a later run may only be "
              f"compared against this file while that value is unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
