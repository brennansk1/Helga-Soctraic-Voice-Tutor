import os
import time
import logging
import re
import ast
import json
import uuid
import random
import requests
import difflib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional, Tuple, Any
# Content providers removed — all content is LLM-generated
from services.common.storage import StorageManager
from services.common.llm_utils import (
    llm_generate,
    extract_python_list,
    llm_generate_json,
)
from services.core.depth_contract import (
    validate_concept,
    regeneration_hint,
    infer_domain,
)

logger = logging.getLogger(__name__)

# Checked at the same point as the depth contract — see the module
# docstring there for what it exists to stop reaching a learner.
from services.core import content_guards
from services.core import sql_ground_truth


class CourseCreationError(Exception):
    """Raised when course creation fails irrecoverably (e.g., LLM retry exhaustion, hydration threshold exceeded)."""
    pass


LLM_API_URL = os.getenv(
    "LLM_API_URL", "http://host.docker.internal:11434/v1/chat/completions"
)
DATA_ROOT = os.getenv("DATA_ROOT", "/app/data")

# Concurrency guard: only one course build can run at a time (P2-13)
_build_lock = threading.Lock()

# Minimum title length enforced across the pipeline. Any title shorter than
# this gets rejected by the normalizer, flagged by the auditor for rename,
# and skipped by the hydrator. All three stages must share the same value
# or concepts can slip into the ContentHydrator and be silently dropped
# (which is how the "IV" concept ended up unhydrated in the Causal Inference
# course — see the postmortem in the commit message).
MIN_TITLE_LEN = 3

FORBIDDEN_TITLES = {
    "basics",
    "introduction",
    "intro",
    "foundations",
    "overview",
    "essentials",
    "general",
    "knowledge",
    "study",
    "comparative",
    "analysis",
    "mechanisms",
    "application",
    "applied",
    "topics",
    "conceptual",
    "fundamental",
    "core",
    "axiom",
    "definitions",
    "origins",
    "basic axioms",
    "axioms",
    "specifics",
    "context",
    "summary",
    "unit title",
    "lesson title",
    "concept title",
    "primary elements",
    "logical flow",
    "detailed patterns",
    "systemic view",
    "active components",
    "structural dynamics",
    "core definition",
    "component breakdown",
    "contextual explanation",
    "primary elements logical flow",
    # Single-word generic academic terms that LLMs produce as lazy placeholders
    "theoretical",
    "practical",
    "regression",
    "methodology",
    "framework",
    "paradigm",
    "synthesis",
    "components",
    "elements",
    "properties",
    "characteristics",
    "principles",
    "concepts",
    "techniques",
    "strategies",
    "approaches",
    "methods",
    "models",
    "systems",
    "structures",
    "dynamics",
    "interactions",
    "relationships",
    "patterns",
    "processes",
    "operations",
    "functions",
    "variables",
    "parameters",
    "factors",
    "core definitions",
    "practical application",
    "key understanding",
    "core specific",
    "key concepts",
    "main ideas",
    "primary concepts",
    # Common LLM-generated filler patterns
    "core systems",
    "essential framework",
    "fundamental structures",
    "operational mechanics",
    "technical architecture",
    "component dynamics",
    # Role description leakage patterns (LLM copies instruction text as titles)
    "systemic interactions",
    "active operational mechanisms",
    "operational mechanisms",
    "comparative theoretical critiques",
    "theoretical critiques",
    "systemic view",
    "active mechanisms",
    "foundational axioms",
    "complex synthesis",
    "high-order synthesis",
    # Example JSON bleed-through patterns (LLM copies example template titles)
    "fundamental axioms",
    "systemic dynamics",
    "theory layer",
    "mechanism layer",
    "synthesis layer",
    "foundational theory area",
    "core methods area",
    "advanced applications area",
    "advanced modeling",
    "advanced modelling",
    "interaction dynamics",
    "feedback loop",
    "feedback loops",
    "variable modelling",
    "variable modeling",
    "axioms in principle",
    "causal structures",
}

# Three-slider parameter system (scope, mastery, starting_from)
# `term_fraction` is how much of a 15-week semester the scope represents, which
# is what converts the calendar into a lesson budget. Scope 3 ("Standard") is the
# reference point: exactly one semester, 45 class sessions. Without this every
# preset inherited a full semester, so a "quick overview in an evening" was
# budgeted the same 45 lessons as a full college course.
SCOPE_PROFILES = {
    1: {"label": "Focused", "module_base": 3, "term_fraction": 0.10,
        "description": "A single narrow subtopic"},
    2: {"label": "Targeted", "module_base": 4, "term_fraction": 0.25,
        "description": "One specific area within the field"},
    3: {"label": "Standard", "module_base": 6, "term_fraction": 1.00,
        "description": "A subject area with context"},
    4: {"label": "Broad", "module_base": 8, "term_fraction": 1.25,
        "description": "A substantial field"},
    5: {"label": "Comprehensive", "module_base": 11, "term_fraction": 1.50,
        "description": "Full discipline survey"},
}

MASTERY_PROFILES = {
    1: {"label": "Awareness", "concepts_per_module": 3, "concepts_per_lesson": 2, "bloom_ceiling": 2, "content_words": 150,
        "vocabulary": "simple terms, everyday language, high-level intuition",
        "writing": "Write for a curious beginner. Use everyday language, analogies, and intuitive explanations. Avoid jargon."},
    2: {"label": "Understanding", "concepts_per_module": 4, "concepts_per_lesson": 2, "bloom_ceiling": 3, "content_words": 250,
        "vocabulary": "standard educational level, key technical terms introduced",
        "writing": "Write for an interested learner. Introduce technical terms with clear definitions. Use concrete examples."},
    3: {"label": "Application", "concepts_per_module": 5, "concepts_per_lesson": 3, "bloom_ceiling": 4, "content_words": 400,
        "vocabulary": "technical depth, precise mechanisms, named methods and properties",
        "writing": "Write for an undergraduate student. Use precise technical language. Explain mechanisms and formal relationships."},
    4: {"label": "Proficiency", "concepts_per_module": 7, "concepts_per_lesson": 3, "bloom_ceiling": 5, "content_words": 600,
        "vocabulary": "formal definitions, named theorems and criteria, professional terminology",
        "writing": "Write for a postgraduate/professional. Use full technical precision. Include formal definitions and edge cases."},
    5: {"label": "Expertise", "concepts_per_module": 10, "concepts_per_lesson": 4, "bloom_ceiling": 6, "content_words": 800,
        "vocabulary": "formal proofs, research methodologies, open problems, theoretical frameworks",
        "writing": "Write for a researcher. Include formal notation, theoretical implications, and frontier research."},
}

STARTING_FROM_PROFILES = {
    1: {"label": "No background", "skip_factor": 0, "bloom_floor": 1,
        "instruction": "Start from absolute basics. Define every term. Assume zero prior knowledge."},
    2: {"label": "Basic awareness", "skip_factor": 0, "bloom_floor": 1,
        "instruction": "Skip 'what is this field' intro but define all technical terms. Assume the student knows the field exists."},
    3: {"label": "Foundational", "skip_factor": 1, "bloom_floor": 2,
        "instruction": "Compress introductory content into brief review. Reference definitions without teaching them. Start at application level."},
    4: {"label": "Intermediate", "skip_factor": 2, "bloom_floor": 3,
        "instruction": "Skip all introductory and foundational content. Begin at analysis level. Assume working knowledge of the field."},
    5: {"label": "Advanced", "skip_factor": 3, "bloom_floor": 4,
        "instruction": "Jump directly to synthesis/evaluation content. Expert-to-expert register. No hand-holding."},
}


BLOOM_LABELS = {1: "Remember", 2: "Understand", 3: "Apply", 4: "Analyze", 5: "Evaluate", 6: "Create"}


def progressive_bloom(index: int, total: int, floor: int, ceiling: int) -> int:
    """Per-module Bloom target: module #index (0-based) of `total`, ramped
    linearly from `floor` to `ceiling`. Single source of truth so every call
    site computes the same value (B1.1.4)."""
    progress = index / max(total - 1, 1)
    return max(floor, min(ceiling, floor + round(progress * (ceiling - floor))))


# --- Presets -----------------------------------------------------------------
# Named starting points so a learner picks a recognisable KIND of course
# instead of reasoning about three abstract 1-5 dials. Each is just a
# (scope, mastery, starting_from) triple — nothing here is a new mechanism, so
# a preset and a hand-tuned course go through exactly the same pipeline and the
# same quality gate.
#
# `minutes` is a MEASURED estimate, not a guess: full-pipeline builds on this
# hardware (Ollama + qwen3.5:9b, depth contract + fact-check + calibration
# enabled) ran ~2 minutes per concept. It is surfaced because the difference
# between presets is 20 minutes and 3+ hours, and a learner should not discover
# that by waiting.

# MEASURED 2026-08-18 on nail-35b-a3b: 3 concepts hydrated in 269 s = 90 s each.
# The previous 2.0 was measured on qwen3.5:9b, which the project no longer uses.
_MINUTES_PER_CONCEPT = 1.5

COURSE_PRESETS = {
    "overview": {
        "label": "Quick Overview",
        "blurb": "The shape of the subject in an evening. Plain language, no prerequisites.",
        "scope": 2, "mastery": 1, "starting_from": 1,
    },
    "high_school": {
        "label": "High School",
        "blurb": "Solid grounding with worked examples. Assumes no background.",
        "scope": 3, "mastery": 2, "starting_from": 1,
    },
    "college": {
        "label": "College Course",
        "blurb": "An undergraduate treatment: formal definitions, worked problems, "
                 "real sources. Assumes you know the field exists.",
        "scope": 3, "mastery": 3, "starting_from": 2,
    },
    "college_advanced": {
        "label": "Advanced Undergraduate",
        "blurb": "Upper-division depth — named results, derivations and primary "
                 "literature. Assumes the basics.",
        "scope": 3, "mastery": 4, "starting_from": 3,
    },
    "graduate": {
        "label": "Graduate Seminar",
        "blurb": "Narrow and deep. Formal notation, proofs, exercises, research "
                 "sources. Expert-to-expert register.",
        "scope": 2, "mastery": 5, "starting_from": 4,
    },
    "survey": {
        "label": "Full Discipline Survey",
        "blurb": "Breadth over depth — the whole field, undergraduate level. "
                 "This is a long build.",
        "scope": 5, "mastery": 3, "starting_from": 1,
    },
    "refresher": {
        "label": "Refresher",
        "blurb": "You learned this once. Skips the introductions and restarts at "
                 "application level.",
        "scope": 3, "mastery": 3, "starting_from": 4,
    },
    "deep_dive": {
        "label": "Deep Dive",
        "blurb": "One narrow topic, taken as far as it goes.",
        "scope": 1, "mastery": 5, "starting_from": 3,
    },
}


def preset_summary(key):
    """Resolve a preset to its parameters plus what the learner actually gets.

    Returns None for an unknown key so callers can fall back to explicit
    sliders rather than silently substituting a default course.
    """
    p = COURSE_PRESETS.get(key)
    if not p:
        return None
    params = compute_course_params(p["scope"], p["mastery"], p["starting_from"])
    concepts = params["total_concepts_approx"]
    try:
        from services.core.depth_contract import DEPTH_CONTRACTS
        required = DEPTH_CONTRACTS.get(p["mastery"], {}).get("required", [])
    except Exception:
        required = []
    return {
        "key": key,
        "label": p["label"],
        "blurb": p["blurb"],
        "scope": p["scope"],
        "mastery": p["mastery"],
        "starting_from": p["starting_from"],
        "modules": params["modules"],
        "concepts": concepts,
        "bloom_floor": params["bloom_floor"],
        "bloom_ceiling": params["bloom_ceiling"],
        # What the depth contract will REQUIRE of every concept at this level —
        # the honest description of what "college" or "graduate" buys you.
        "requires": required,
        "est_minutes": int(round(concepts * _MINUTES_PER_CONCEPT)),
    }


def list_presets():
    """All presets, resolved. Ordered from lightest to heaviest build."""
    out = [preset_summary(k) for k in COURSE_PRESETS]
    return sorted([p for p in out if p], key=lambda p: p["est_minutes"])


# Verified real-world anchors (docs/AI_UNIVERSITY_DESIGN.md): a US semester is
# ~15 weeks and a 3-credit course meets 3 times a week, so a course is ~45 class
# sessions. A lesson is one class session.
WEEKS_PER_TERM = 15
SESSIONS_PER_WEEK = 3
# How far a real course may sit from the nominal calendar and still be that
# course. MIT 18.06 runs 34 lectures against a nominal 45, which is -24%, so a
# tolerance narrower than that would call a well-regarded semester course
# deficient.
LESSON_TOLERANCE = 0.25

# THE SCORE AT WHICH A SOURCE MAY SPEAK FOR THE SUBJECT.
#
# 6.0 is the exact-title-match bonus in syllabus_sources._relevance, so a source
# clearing it is about this subject rather than merely adjacent to it. Below the
# line a source is SUPPLEMENTARY: still useful, never authoritative.
#
# MEASURED FAILURE. This gate existed only inside _spine_from_syllabus, so it
# protected the structure and nothing else. A build of "Dungeon Mastering"
# matched an OpenStax SOCIOLOGY text (68 chapters), and because every other
# consumer took the brief unfiltered:
#
#   * scope_fit reported "68 chapters support roughly 408 concepts against 144
#     requested", ratio 2.83, verdict ok — so the thin-subject disclaimer never
#     fired for the exact case it was built for
#   * backfill treated sociology chapters as material the course MUST reach and
#     tried to inject six of them
#   * syllabus_check scored the course 28% against sociology keywords
#   * and the sourceless research loop never ran AT ALL, because the brief
#     counted as "found"
#
# A relevance gate applied at one consumer is not a relevance gate.
GROUNDING_RELEVANCE = 6.0

# HOW MUCH OF A COURSE A SUPPLEMENTARY SOURCE MAY ACCOUNT FOR.
#
# Demoting a weak source is not enough on its own — a course whose subject is
# poorly served by books can still end up mostly built from whatever adjacent
# material happened to match, which is how a Dungeon Mastering course becomes a
# storytelling course with dice in it. Supplementary material earns a minority
# share and no more; the rest has to come from the subject itself, which for a
# sourceless subject means the research loop rather than a closer book.
SUPPLEMENTARY_MAX_SHARE = 0.20


def compute_course_params(scope=2, mastery=2, starting_from=1):
    """Compute course structure parameters from three sliders."""
    s = SCOPE_PROFILES.get(scope, SCOPE_PROFILES[2])
    m = MASTERY_PROFILES.get(mastery, MASTERY_PROFILES[2])
    sf = STARTING_FROM_PROFILES.get(starting_from, STARTING_FROM_PROFILES[1])
    modules = max(2, s["module_base"] - sf["skip_factor"])
    concepts_per_module = m["concepts_per_module"]
    bloom_floor = sf["bloom_floor"]
    bloom_ceiling = m["bloom_ceiling"]
    # Fidelity guard (Task #11): the starting level is a hard floor (the learner
    # already knows the basics), so a contradictory pick — e.g. mastery=Awareness
    # (ceiling 2) with starting=Advanced (floor 4) — must not yield floor>ceiling
    # (which produced a degenerate Bloom ramp). Raise the ceiling to at least the
    # floor so the course never "ends below where it starts".
    bloom_ceiling = max(bloom_ceiling, bloom_floor)
    # THE CALENDAR IS THE ONLY FIXED THING. A 15-week semester at 3 sessions a
    # week is 45 class sessions, and a lesson IS a class session — that is a real
    # constraint and it does not bend. Everything below it does: a unit is a
    # TOPICAL grouping, so some units are one week and some are three, and how
    # many lessons a unit holds should follow the material rather than a
    # hardcoded number. Previously `units_per_module` and `lessons_per_unit` were
    # both pinned at 1 in the legacy DEPTH_PROFILES, which collapsed every course
    # to one lesson per module — measured in Task 0 as 6 modules / 6 units /
    # 6 lessons where the calendar calls for 45 lessons.
    lessons_total = int(round(SESSIONS_PER_WEEK * WEEKS_PER_TERM * s.get(
        "term_fraction", 1.0)))
    # A RANGE, NOT A NUMBER.
    #
    # A fixed lesson count and over-stretch detection pull against each other: if
    # the evidence supports 36 concepts and the ladder demands 144, the builder
    # pads — the exact hollow-content failure scope_fit exists to prevent.
    #
    # Real courses vary anyway. MIT 18.06 runs 34 lectures where the nominal
    # 15x3 calendar gives 45, and that is a normal, well-regarded course rather
    # than a deficient one. So the calendar sets a TARGET and a tolerance, and a
    # subject settles where its material actually lands: a rich one near the top,
    # a thin one near the bottom, neither padded nor truncated.
    lessons_min = max(1, int(round(lessons_total * (1 - LESSON_TOLERANCE))))
    lessons_max = max(lessons_min, int(round(lessons_total * (1 + LESSON_TOLERANCE))))
    lessons_per_module = max(1, round(lessons_total / max(1, modules)))
    lessons_per_module_min = max(1, round(lessons_min / max(1, modules)))
    concepts_per_lesson = m.get("concepts_per_lesson", 3)
    return {
        "modules": modules,
        "concepts_per_module": concepts_per_module,
        "lessons_total": lessons_total,
        "lessons_min": lessons_min,
        "lessons_max": lessons_max,
        "lessons_per_module": lessons_per_module,
        "lessons_per_module_min": lessons_per_module_min,
        "concepts_per_lesson": concepts_per_lesson,
        "total_concepts_approx": modules * lessons_per_module * concepts_per_lesson,
        "bloom_floor": bloom_floor,
        "bloom_ceiling": bloom_ceiling,
        "content_words": m["content_words"],
        "scope_label": s["label"],
        "scope_desc": s["description"],
        "mastery_label": m["label"],
        "mastery_writing": m["writing"],
        "mastery_vocab": m["vocabulary"],
        "starting_label": sf["label"],
        "starting_instruction": sf["instruction"],
    }


# Legacy single-depth compatibility: map depth 1-5 to scope=depth, mastery=depth, starting_from=1
DEPTH_PROFILES = {
    1: {
        "label": "Quick Overview",
        "academic_level": "Introductory",
        "target_modules": 2,
        "units_per_module": 1,
        "lessons_per_unit": 1,
        "concepts_per_lesson": 2,
        "content_words": 150,
        "vocabulary": "simple terms, everyday language, high-level intuition",
        "instruction": "Explain like a casual introduction. Use simple analogies. No jargon.",
        "hydration_sections": [
            "Core Definition",
            "Contextual Explanation",
            "Socratic Hook",
        ],
    },
    2: {
        "label": "Foundational",
        "academic_level": "Undergraduate",
        "target_modules": 3,
        "units_per_module": 2,
        "lessons_per_unit": 1,
        "concepts_per_lesson": 2,
        "content_words": 250,
        "vocabulary": "standard educational level, key technical terms introduced",
        "instruction": "Cover fundamentals with clear definitions. Introduce terminology.",
        "hydration_sections": [
            "Core Definition",
            "Component Breakdown",
            "Contextual Explanation",
            "Socratic Hook",
        ],
    },
    3: {
        "label": "Comprehensive",
        "academic_level": "Graduate",
        "target_modules": 3,
        "units_per_module": 2,
        "lessons_per_unit": 2,
        "concepts_per_lesson": 2,
        "content_words": 350,
        "vocabulary": "technical depth, precise mechanisms, named methods and properties, formal relationships",
        "instruction": "Thorough coverage with technical precision. Use established terminology. Explain mechanisms and formal relationships.",
        "hydration_sections": [
            "Core Definition",
            "Component Breakdown",
            "Contextual Explanation",
            "Misconceptions",
            "Socratic Hook",
        ],
    },
    4: {
        "label": "Advanced",
        "academic_level": "Postgraduate / Professional",
        "target_modules": 4,
        "units_per_module": 2,
        "lessons_per_unit": 2,
        "concepts_per_lesson": 3,
        "content_words": 450,
        "vocabulary": "formal definitions, named theorems and criteria, estimation methods, professional terminology, edge cases and assumptions",
        "instruction": "Professional-level detail. Name real methods, theorems, and criteria. Cover formal assumptions, edge cases, trade-offs, and practical application nuances.",
        "hydration_sections": [
            "Core Definition",
            "Component Breakdown",
            "Contextual Explanation",
            "Misconceptions",
            "Advanced Notes",
            "Socratic Hook",
        ],
    },
    5: {
        "label": "Expert / Research",
        "academic_level": "Doctoral / Research",
        "target_modules": 4,
        "units_per_module": 4,
        "lessons_per_unit": 3,
        "concepts_per_lesson": 5,
        "content_words": 600,
        "vocabulary": "formal proofs and derivations, named theorems with conditions, research methodologies, open problems, theoretical frameworks",
        "instruction": "Research-level depth. Name specific theorems, algorithms, and estimation procedures. Include theoretical frameworks, open problems, and frontier research.",
        "hydration_sections": [
            "Core Definition",
            "Component Breakdown",
            "Contextual Explanation",
            "Misconceptions",
            "Advanced Notes",
            "Research Frontiers",
            "Socratic Hook",
        ],
    },
}


def _curated_spine(topic):
    """A hand-transcribed chapter order for a subject, or None.

    Only consulted when research finds no sequenced source. These files record
    the ORDER of a published textbook's chapters -- a short factual sequence --
    for subjects where every machine-readable listing is an alphabetical index.
    """
    key = re.sub(r"[^a-z0-9 ]", " ", (topic or "").lower()).strip()
    key = re.sub(r"\s+", " ", key)
    if not key:
        return None
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "../.."))
    spine_dir = os.path.join(root, "tools", "references", "spines")
    if not os.path.isdir(spine_dir):
        return None
    for name in sorted(os.listdir(spine_dir)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(spine_dir, name)) as fh:
                data = json.load(fh)
        except Exception as e:
            logger.debug(f"curated spine {name} unreadable: {e}")
            continue
        aliases = [a.lower() for a in (data.get("aliases") or [])]
        aliases.append((data.get("subject") or "").lower())
        if key in aliases and data.get("chapters"):
            return data
    return None


def _generic_titles_in(units_data):
    """Titles in a subtree that name nothing, at any level."""
    try:
        from tools.structure_quality import _GENERIC
    except ImportError:
        return []
    out = []
    for u in (units_data or []):
        if not isinstance(u, dict):
            continue
        for title in ([u.get("title")]
                      + [l.get("title") for l in (u.get("lessons") or [])
                         if isinstance(l, dict)]
                      + [c.get("title") for l in (u.get("lessons") or [])
                         if isinstance(l, dict)
                         for c in (l.get("concepts") or []) if isinstance(c, dict)]):
            t = (title or "").strip()
            if t and _GENERIC.match(t):
                out.append(t)
    return out


def _shape_lo(level, default):
    """Lower bound of a school-shape band, from the shared definition."""
    try:
        from tools.structure_quality import SCHOOL_SHAPE
    except ImportError:
        return default
    return int(SCHOOL_SHAPE.get(level, (default, default))[0])


def _shape_range(level, default_lo, default_hi):
    try:
        from tools.structure_quality import SCHOOL_SHAPE
    except ImportError:
        return default_lo, default_hi
    lo, hi = SCHOOL_SHAPE.get(level, (default_lo, default_hi))
    return int(lo), int(hi)


def _looks_alphabetical(titles, sample=25, threshold=0.9):
    """Is this list sorted rather than sequenced?

    Checked on a prefix and by PROPORTION of in-order adjacent pairs, not by
    exact equality with sorted(): a real syllabus occasionally has two adjacent
    chapters that happen to be alphabetical, and an index may have a stray entry
    out of place. The question is whether the dominant organising principle is
    the alphabet.
    """
    items = [t.strip().lower() for t in (titles or []) if isinstance(t, str)][:sample]
    if len(items) < 5:
        return False
    in_order = sum(1 for a, b in zip(items, items[1:]) if a <= b)
    return in_order / max(1, len(items) - 1) >= threshold


# The one-shot subtree prompt runs ~4200 tokens with syllabus evidence attached,
# and Ollama's default context is 4096. Anything below this silently degrades
# course structure rather than failing.
MIN_CONTEXT_TOKENS = 8192

# What the pipeline is actually written for. Above MIN it runs; below
# this it runs DEGRADED, which is the harder failure to see.
WANTED_CONTEXT_TOKENS = int(os.getenv('HELGA_WANTED_CONTEXT', '16384'))


def _detect_context_window():
    """The serving context window, or None if it cannot be determined.

    None means "unknown", never "too small" — refusing to build because a probe
    did not answer would be the absent-vs-zero error in the place that blocks
    every course.
    """
    try:
        import requests as _rq
        base = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
        model = os.getenv("OLLAMA_MODEL", "")
        if not model:
            return None
        r = _rq.post(f"{base}/api/show", json={"model": model}, timeout=8)
        if r.status_code != 200:
            return None
        info = r.json() or {}
        # An explicit num_ctx parameter wins; otherwise Ollama's default applies,
        # regardless of what the architecture could support.
        params = (info.get("parameters") or "")
        for line in params.splitlines():
            if line.strip().startswith("num_ctx"):
                return int(line.split()[-1])
        return 4096
    except Exception as e:
        logger.debug(f"context window probe failed: {e}")
        return None


class CourseCreationCancelled(Exception):
    """The learner pressed Cancel and the build stopped at a checkpoint.

    A distinct type so the pipeline can tell "the user changed their mind"
    from "the build broke" — they need opposite handling: one cleans up
    quietly, the other is an error the learner should see.
    """


class SkeletonBuilder:
    def __init__(
        self,
        db_path: str = None,
        providers: list = None,
        status_callback=None,
        should_cancel=None,
        learner_context: str = None,
        course_depth: int = 2,
        teaching_style: str = "",
        storage: StorageManager = None,
        scope: int = None,
        mastery: int = None,
        starting_from: int = None,
    ):
        self.db_path = db_path
        self.providers = []  # Content providers removed
        self.status_callback = status_callback
        # CANCELLATION WAS COSMETIC. `/api/cancel_creation` flipped
        # `creation_in_progress` and set phase="cancelled", and NOTHING in
        # this builder ever read it: the thread ran to completion, kept a
        # 13 GB model busy for hours, and wrote the course the learner
        # believed they had cancelled. Measured 2026-08-24 — a new 7,920-token
        # LLM call started SEVEN SECONDS after "cancelled by user" was logged.
        #
        # A predicate rather than a flag so the builder never has to know what
        # owns the state; it just asks, at the checkpoints below.
        self.should_cancel = should_cancel
        # WHAT THE LEARNER ACTUALLY WANTS, IN THEIR OWN WORDS.
        #
        # The whole structure was decided from a topic STRING. "SQL" is not a
        # course brief — it is a word covering an analytics engineer, a backend
        # developer, a DBA and someone revising for an interview, and the
        # builder had no way to tell them apart. Measured: a course on "SQL"
        # opened with modules on History and Interoperability and
        # Standardization — a fair reading of the word, and no use to anyone
        # learning it.
        #
        # `user_note` already existed at MODULE and CONCEPT level, from the
        # wizard, and reached hydration. Nothing carried intent at the level
        # where it decides most: the module plan.
        self.learner_context = (learner_context or "").strip()
        self.course_depth = course_depth
        self.teaching_style = teaching_style or ""
        # Three-slider system (falls back to depth for legacy callers)
        self.scope = scope if scope is not None else course_depth
        self.mastery = mastery if mastery is not None else course_depth
        self.starting_from = starting_from if starting_from is not None else 1
        self.course_params = compute_course_params(self.scope, self.mastery, self.starting_from)
        # Single backing set (legacy) plus per-level buckets. Concept dedup must
        # only look at sibling concepts — a concept sharing words with its
        # parent lesson title is NOT a duplicate. See QB-1 audit finding.
        self.used_titles = set()
        self.used_titles_by_level = {
            "module": set(),
            "unit": set(),
            "lesson": set(),
            "concept": set(),
        }
        self.failed_titles = set()
        self.hierarchy = []
        self.model = None
        self.fallback_count = 0  # WIZ-3: Track how many items used LLM fallback titles

        # Use provided storage or create one
        if storage:
            self.storage = storage
        else:
            data_dir = os.path.dirname(db_path) if db_path else DATA_ROOT
            self.storage = StorageManager(data_dir)

    def close(self):
        # No-op: on a 24GB host speculative gc.collect() only adds latency.
        # Kept for callers that invoke close() after a build.
        pass

    def _get_blacklist_str(self) -> str:
        hierarchy_str = " > ".join(self.hierarchy) if self.hierarchy else "Course Root"
        titles_list = sorted(list(self.used_titles))
        blacklist = "\n- ".join(titles_list) if titles_list else "None"
        return (
            f"\n\n### [CONTEXT] CURRENT HIERARCHY BRANCH: {hierarchy_str}\n"
            "### [FORBIDDEN] ALREADY USED TITLES (Do not repeat):\n"
            f"- {blacklist}\n"
        )

    #: A padded title is the parent's title with " Part N" or " Lesson N"
    #: stuck on the end. Nothing a model writes looks like that.
    _PADDED = __import__("re").compile(
        r"^(?P<parent>.+?)\s+(?:Part|Lesson)\s+\d+$", __import__("re").I)

    @staticmethod
    def is_placeholder_title(title, parent_title=None):
        """True when `title` is scaffolding rather than a taught idea.

        The builder pads empty lessons with "{lesson} Part N" so the structure
        is never empty, on the reasoning that a generic concept beats a black
        hole in the learning path. Measured on a real build, that reasoning is
        wrong: an "Advanced SQL" course shipped a whole unit of

            Set Operations and Deduplication Logic Part 2 Lesson 3 Part 1

        whose only objective was "Understand Set Operations and Deduplication
        Logic Part 2 Lesson 3". That is a black hole WITH A NAME, which is
        worse than an absent unit — the course advertises 108 concepts, the
        learner opens five dead ends, and hydration spends a minute of model
        time writing content for a title that means nothing.

        The trigger is dedup, not the model: in a module named "Set
        Operations", every honest concept contains "set operations", so dedup
        strips them as echoes of the parent and padding fills the hole.
        """
        t = (title or "").strip()
        m = SkeletonBuilder._PADDED.match(t)
        if not m:
            return False
        if parent_title is None:
            return True
        # Only scaffolding when the stem IS the parent — a real concept called
        # "Window Functions Part 2" in a differently-named lesson is content.
        return m.group("parent").strip().lower() == (parent_title or "").strip().lower()

    def prune_placeholder_scaffolding(self, course):
        """Drop lessons that are entirely padding, and units left empty.

        Returns a tally. Mutates `course` in place. A module is never emptied
        completely — if every one of its units is scaffolding, the module is
        kept and logged, because an absent module is a hole in the syllabus the
        learner can see, while a thin one is merely thin.
        """
        tally = {"concepts": 0, "lessons": 0, "units": 0}
        for module in (course.get("modules") or []):
            m_title = module.get("title", "")
            kept_units, plan = [], []
            dropped = {"concepts": 0, "lessons": 0, "units": 0}
            for unit in (module.get("units") or []):
                kept_lessons = []
                for lesson in (unit.get("lessons") or []):
                    l_title = lesson.get("title", "")
                    concepts = lesson.get("concepts") or []
                    padded = [c for c in concepts
                              if self.is_placeholder_title(c.get("title"), l_title)]
                    if concepts and len(padded) == len(concepts):
                        dropped["concepts"] += len(concepts)
                        dropped["lessons"] += 1
                        continue
                    kept_lessons.append(lesson)
                if kept_lessons:
                    # NOTHING IS MUTATED UNTIL THE WHOLE MODULE IS DECIDED.
                    #
                    # This assigned `unit["lessons"] = kept_lessons` here,
                    # before knowing whether the module would keep any unit at
                    # all. When every unit turned out to be scaffolding, the
                    # "never empty a module" branch below put the ORIGINAL unit
                    # list back — but those units had already been emptied in
                    # place, so the module kept units holding ZERO lessons.
                    # That is worse than either outcome the branch chooses
                    # between, and a structure test caught it:
                    # "Unit '... Part 1' has no lessons".
                    plan.append((unit, kept_lessons))
                    kept_units.append(unit)
                else:
                    dropped["units"] += 1
            if kept_units:
                for unit, kept_lessons in plan:
                    unit["lessons"] = kept_lessons
                module["units"] = kept_units
                for k in tally:
                    tally[k] += dropped[k]
            elif module.get("units"):
                # Left EXACTLY as it was — no unit above was touched.
                logger.warning(
                    f"  [PRUNE] every unit of '{m_title}' was scaffolding; "
                    f"keeping them rather than emptying the module")
        if any(tally.values()):
            logger.info(f"  [PRUNE] dropped {tally['concepts']} padded concepts, "
                        f"{tally['lessons']} lessons, {tally['units']} units")
        return tally

    def _checkpoint(self, where=""):
        """Raise if the learner has cancelled. Called between units of work.

        Deliberately BETWEEN steps rather than inside them: killing a build
        mid-write is how half a structure.json reaches disk. At a checkpoint
        the last completed step is durable and the next has not started.
        """
        try:
            cancelled = bool(self.should_cancel and self.should_cancel())
        except Exception:      # a broken predicate must not stop a good build
            return
        if cancelled:
            logger.info(f"[CANCEL] build stopping at checkpoint: {where}")
            raise CourseCreationCancelled(where or "cancelled")

    def _normalize_title(self, title: str) -> str:
        if not title:
            return ""

        prefixes = [
            r"^the\s+",
            r"^understanding\s+",
            r"^exploring\s+",
            r"^introduction\s+to\s+",
            r"^basics\s+of\s+",
            r"^learning\s+",
            r"^overview\s+of\s+",
            r"^guide\s+to\s+",
            r"^the\s+basics\s+of\s+",
            r"^role\s+of\s+",
            r"^significance\s+of\s+",
            r"^impact\s+of\s+",
            r"^influence\s+of\s+",
        ]
        suffixes = [
            r"core\s+concepts$",
            r"foundations$",
            r"basics$",
            r"essentials$",
            r"overview$",
            r"advanced\s+context$",
            r"context$",
            r"specifics$",
            r"analysis$",
            r"primary\s+elements$",
            r"logical\s+flow$",
            r"detailed\s+patterns$",
            r"systemic\s+view$",
            r"active\s+components$",
            r"structural\s+dynamics$",
            r"a\s+comparative$",
            r"implications\s+for\s+.*$",
            r"methods\s+and\s+techniques$",
            r"challenges\s+and\s+considerations$",
            r"applications\s+and\s+examples$",
            r"a\s+case\s+study$",
        ]

        cleaned = title.strip()
        last_cleaned = ""
        while cleaned != last_cleaned:
            last_cleaned = cleaned
            for p in prefixes:
                cleaned = re.sub(p, "", cleaned, flags=re.IGNORECASE).strip()
            for s in suffixes:
                cleaned = re.sub(s, "", cleaned, flags=re.IGNORECASE).strip()

        cleaned = re.sub(
            r"\s+(of|and|the|in|for|with|a|an|to|at)$", "", cleaned, flags=re.IGNORECASE
        )
        # Keep possessive apostrophes ("Plato's Republic") and straight/curly
        # variants. Stripping them produced "Platos" / "Aristotles" in QB-1.
        cleaned = re.sub(r"[^\w\s\-'\u2019]", "", cleaned)

        if cleaned.lower() in FORBIDDEN_TITLES:
            return ""

        word_count = len(cleaned.split())
        # Reject single-word titles unless they are acronyms (all caps) or very specific
        # (e.g., proper nouns with 8+ chars that are domain-specific like "Thermodynamics")
        if word_count < 2:
            # Allow all-caps acronyms between 3 and 6 chars (DNA, RNA, TCP,
            # API, CSS, JSON, XML). 2-char acronyms ("IV", "AI", "ML", "UI")
            # are rejected here because they're ambiguous AND the downstream
            # ContentHydrator rejects any title with len(strip()) < 3, which
            # would silently drop them from the course. Keep the two layers
            # in sync — see ContentHydrator.hydrate() for the mirror guard.
            if cleaned.upper() == cleaned and 3 <= len(cleaned) <= 6:
                pass  # Allow 3-6 char acronyms
            elif len(cleaned) < 8:
                # Reject short single words — too generic to be useful
                return ""
            # Even long single words get checked against forbidden list (already done above)

        # Absolute minimum length — must match ContentHydrator's guard.
        # Any title the skeleton builder keeps must be hydratable.
        if len(cleaned) < MIN_TITLE_LEN:
            return ""

        if cleaned:
            cleaned = cleaned[0].upper() + cleaned[1:]

        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip()

    def _sanitize_title(self, title: str) -> str:
        return self._normalize_title(title).lower()

    def _is_duplicate(
        self, new_title: str, course_topic: str = "", is_module: bool = False,
        level: str = None,
    ) -> bool:
        new_norm = self._sanitize_title(new_title)
        if not new_norm or len(new_norm.split()) < 1:
            return True

        # Fast checks first (O(1))
        if new_norm in FORBIDDEN_TITLES:
            logger.warning(f"Rejected generic title: '{new_title}'")
            return True

        if course_topic and new_norm == self._sanitize_title(course_topic):
            return True

        # Pre-compute word set for new title, EXCLUDING common topic words
        # This prevents false positives like "Causal Graphs" vs "Causal Models"
        # from being flagged just because they share the domain word "causal"
        topic_words = set()
        if course_topic:
            topic_words = {w.lower() for w in course_topic.split() if len(w) > 3}

        # For word overlap, exclude topic words since they naturally repeat across titles
        new_words = {w for w in new_norm.split() if len(w) > 4 and w not in topic_words}
        SIMILARITY_THRESHOLD = 0.90  # Raised from 0.85 — less aggressive

        # QB-1 FIX: When checking a concept, only compare against other concepts.
        # A concept like "Socratic Irony" under lesson "Cross-Examination Techniques
        # and Socratic Irony" is NOT a duplicate — it's the natural child. Otherwise
        # whole modules can end up with zero concepts because every candidate
        # word-overlaps with its parent lesson/unit title.
        if is_module:
            level = "module"
        if level == "concept":
            candidates = self.used_titles_by_level.get("concept", set())
        elif level in ("module", "unit", "lesson"):
            candidates = self.used_titles_by_level.get(level, set())
        else:
            candidates = self.used_titles

        # PERF-2: reuse one matcher with new_norm cached as seq2, and gate the
        # expensive ratio() behind difflib's O(1)/O(n) upper-bound estimators —
        # identical results, far fewer full comparisons than building a fresh
        # SequenceMatcher per candidate (mirrors difflib.get_close_matches).
        matcher = difflib.SequenceMatcher()
        matcher.set_seq2(new_norm)

        for used in candidates:
            used_clean = self._sanitize_title(used)

            # 1. Exact match (fastest)
            if new_norm == used_clean:
                return True

            # 2. Substring check — only reject if one title is essentially contained in the other
            # and they're nearly the same length (catches "X" vs "X Overview" but not "Causal" vs "Causal Models")
            if len(new_norm) > 8 and len(used_clean) > 8:
                if (
                    new_norm in used_clean and len(new_norm) > 0.85 * len(used_clean)
                ) or (
                    used_clean in new_norm and len(used_clean) > 0.85 * len(new_norm)
                ):
                    logger.warning(
                        f"Substring collision: '{new_norm}' in relation to '{used_clean}'"
                    )
                    self.failed_titles.add(new_title)
                    return True

            # 3. Word overlap check — only on NON-topic words
            if len(new_words) >= 2:
                used_words = {
                    w for w in used_clean.split() if len(w) > 4 and w not in topic_words
                }
                common = new_words & used_words
                # Reject only if ALL non-topic words overlap
                if (
                    len(common) >= 2
                    and len(new_words) > 0
                    and len(common) / len(new_words) >= 1.0
                ):
                    logger.warning(
                        f"Word overlap collision: '{new_norm}' shares {common} with '{used_clean}'"
                    )
                    self.failed_titles.add(new_title)
                    return True

            # 4. Similarity ratio (most expensive — gated by cheap upper bounds)
            matcher.set_seq1(used_clean)
            if (
                matcher.real_quick_ratio() > SIMILARITY_THRESHOLD
                and matcher.quick_ratio() > SIMILARITY_THRESHOLD
                and matcher.ratio() > SIMILARITY_THRESHOLD
            ):
                logger.warning(
                    f"Similarity collision: '{new_norm}' vs '{used_clean}' "
                    f"(ratio {matcher.ratio():.2f})"
                )
                self.failed_titles.add(new_title)
                return True
        return False

    def _generate_context_stopwords(self, topic: str) -> str:
        ignore = {
            "and",
            "the",
            "of",
            "in",
            "for",
            "a",
            "an",
            "with",
            "basics",
            "history",
            "principles",
        }
        words = set(re.findall(r"\b[a-z]{3,}\b", topic.lower()))
        return ", ".join([w for w in words if w not in ignore])

    def _get_fallback_title(self, parent_title: str, level: str = "sub") -> str:
        """Generates a contextual fallback title when LLM fails to produce a valid one.

        Instead of generic suffixes like 'Core Systems', uses numbered sub-sections
        that clearly indicate they are subdivisions of the parent topic.
        This prevents polluting the course with meaningless filler titles.
        """
        # Simple numbered subdivision approach — clear and honest
        for i in range(1, 10):
            candidate = f"{parent_title} Part {i}"
            if not self._is_duplicate(candidate):
                norm_cand = self._normalize_title(candidate)
                if norm_cand and norm_cand != self._normalize_title(parent_title):
                    return candidate

        # Ultimate fallback with UUID to guarantee uniqueness
        return f"{parent_title} Section {uuid.uuid4().hex[:4]}"

    def _get_domain_constraints(self, topic: str) -> Dict[str, Any]:
        """Centralized domain/topic logic for historical, stem, and creative courses."""
        topic_l = topic.lower()

        stem_keywords = [
            "science",
            "engineering",
            "math",
            "physics",
            "coding",
            "data",
            "biology",
            "inference",
            "calculus",
            "statistics",
            "causal",
            "analysis",
            "logic",
            "numeral",
            "algorithm",
        ]
        is_stem = any(x in topic_l for x in stem_keywords)

        historical_keywords = [
            "history",
            "ancient",
            "rome",
            "greek",
            "egypt",
            "medieval",
            "war",
            "civilization",
            "archaeology",
            "mythology",
        ]
        is_historical = any(k in topic_l for k in historical_keywords)

        creative_keywords = [
            "creative",
            "thinking",
            "arts",
            "design",
            "writing",
            "composition",
            "ideation",
            "imagination",
        ]
        is_creative = any(k in topic_l for k in creative_keywords)

        temporal_constraint = ""
        if is_historical:
            temporal_constraint = (
                "CRITICAL TEMPORAL LOCK: You are generating content for a specific HISTORICAL era. "
                "Strictly BAN all references to modern technology or concepts such as: "
                "'Digital', 'AI', 'Computer', 'Software', 'Modern', 'Global Economy', 'Internet'. "
                "Focus ONLY on tools, systems, and concepts available in that era."
            )

        category_constraint = ""
        if is_creative:
            category_constraint = (
                "CREATIVE FOCUS: This is a course on human creativity and critical thinking. "
                "Focus on cognitive processes, methodology, and cross-disciplinary synthesis. "
                "Avoid overly dry technical jargon if possible; use evocative, conceptual titles."
            )

        # IMPORTANT: Examples use CLEARLY ABSTRACT placeholders to demonstrate JSON FORMAT only.
        # Never use domain-realistic terms in examples — LLMs will copy them verbatim.
        example_json = """[
    {"title": "[Specific Subtopic Area 1]", "level": 1, "rationale": "[Why this comes first]", "scope": ["[Named method/concept A]", "[Named method/concept B]", "[Named method/concept C]"]},
    {"title": "[Specific Subtopic Area 2]", "level": 2, "rationale": "[Why this builds on module 1]", "scope": ["[Named method/concept D]", "[Named method/concept E]", "[Named method/concept F]"]},
    {"title": "[Specific Subtopic Area 3]", "level": 3, "rationale": "[Why this is advanced]", "scope": ["[Named method/concept G]", "[Named method/concept H]", "[Named method/concept I]"]}
]"""
        if is_stem:
            # STEM example: abstract format only — NO realistic terms that could bleed into output
            example_json = """[
    {"title": "[Foundational Theory Area]", "level": 1, "rationale": "[Why foundational]", "scope": ["[Real named theorem/method 1]", "[Real named theorem/method 2]", "[Real named framework 3]"]},
    {"title": "[Core Methods Area]", "level": 2, "rationale": "[Why this builds on foundations]", "scope": ["[Real named technique 1]", "[Real named technique 2]", "[Real named technique 3]"]},
    {"title": "[Advanced Applications Area]", "level": 3, "rationale": "[Why this is advanced]", "scope": ["[Real named advanced method 1]", "[Real named advanced method 2]", "[Real named advanced method 3]"]}
]"""
        elif is_historical:
            example_json = """[
    {"title": "[Specific Historical Period/Theme 1]", "level": 1, "rationale": "[Why this comes first chronologically/thematically]", "scope": ["[Specific event/figure/concept A]", "[Specific event/figure/concept B]", "[Specific event/figure/concept C]"]},
    {"title": "[Specific Historical Period/Theme 2]", "level": 2, "rationale": "[Why this follows]", "scope": ["[Specific event/figure/concept D]", "[Specific event/figure/concept E]", "[Specific event/figure/concept F]"]},
    {"title": "[Specific Historical Period/Theme 3]", "level": 3, "rationale": "[Why this is the culmination]", "scope": ["[Specific event/figure/concept G]", "[Specific event/figure/concept H]", "[Specific event/figure/concept I]"]}
]"""

        return {
            "is_stem": is_stem,
            "is_historical": is_historical,
            "is_creative": is_creative,
            "temporal_constraint": temporal_constraint,
            "category_constraint": category_constraint,
            "example_json": example_json,
        }

    def _run_preflight_checks(self, topic: str, max_depth: int) -> bool:
        """Runs preflight validation checks before any LLM calls."""
        all_passed = True

        def log_and_emit(status: str, msg: str):
            """status is "✓" pass, "✗" fail, "!" warn.

            WARN IS A THIRD STATE, not a quiet failure. This used to treat
            anything that was not "✓" as FAIL, so a degraded-but-usable
            condition — web search returning nothing, say — would have been
            reported to the learner as a broken build and, worse, read by the
            UI as one.
            """
            logger.info(f"[PREFLIGHT] {status} {msg}")
            if not self.status_callback:
                return
            if status == "✓":
                self.status_callback(f"CHECK:PREFLIGHT:PASS:{msg}")
            elif status == "!":
                self.status_callback(f"CHECK:PREFLIGHT:WARN:{msg}")
            else:
                self.status_callback(f"CHECK:PREFLIGHT:FAIL:{msg}")

        # 1. Topic Validation
        if not topic or len(topic.strip()) < 3 or len(topic.strip()) > 200:
            log_and_emit("✗", "Topic must be between 3 and 200 characters")
            all_passed = False
        else:
            log_and_emit("✓", "Topic valid")

        # 1b. CONTEXT WINDOW.
        #
        # Ollama serves a model at 4096 tokens unless its Modelfile says
        # otherwise. The one-shot subtree prompt is ~4200, so on a default-context
        # model it 400s for most modules, falls back to the chunked path, and
        # produces a course a third shorter than its calendar — while reporting
        # success. That cost four wrong hypotheses and several full rebuilds to
        # find, because the failure is silent by construction: a fallback path is
        # supposed to be quiet.
        #
        # It is checked here, once, loudly, so it can never be diagnosed twice.
        ctx = _detect_context_window()
        if ctx is None:
            log_and_emit("✓", "Context window not reported — proceeding")
        elif ctx < MIN_CONTEXT_TOKENS:
            log_and_emit("✗", f"Model context is {ctx} tokens; this pipeline "
                              f"needs at least {MIN_CONTEXT_TOKENS}. Add "
                              f"'PARAMETER num_ctx {MIN_CONTEXT_TOKENS}' to the "
                              f"Modelfile — course structure will silently "
                              f"degrade otherwise")
            all_passed = False
        elif ctx < WANTED_CONTEXT_TOKENS:
            # ABOVE THE FLOOR IS NOT THE SAME AS RIGHT.
            #
            # The floor stops a build that cannot work at all. It said nothing
            # about a model served at HALF the context the pipeline is written
            # for, which does not fail — it quietly truncates the research and
            # ledger material injected into each concept, and the course comes
            # out thinner with every check still green.
            #
            # Measured 2026-08-25: the `-ctx` tag on this machine was 8192, not
            # the 16384 deploy.sh builds and docs/MODEL.md documents. It passed
            # preflight with a tick. deploy.sh only creates that tag when it is
            # MISSING, so a tag that drifts once stays wrong forever, and this
            # was the only place that could have noticed.
            log_and_emit("!", f"Context window is {ctx} tokens, not the "
                              f"{WANTED_CONTEXT_TOKENS} this pipeline is built "
                              f"for. It will run, but research and prior-concept "
                              f"context get truncated per concept. Rebuild the "
                              f"model tag: ollama create $OLLAMA_MODEL with "
                              f"'PARAMETER num_ctx {WANTED_CONTEXT_TOKENS}'")
        else:
            log_and_emit("✓", f"Context window {ctx} tokens")

        # 2. Depth Validation
        if max_depth not in DEPTH_PROFILES:
            log_and_emit("✗", "Invalid depth rank. Must be 1-5")
            all_passed = False
        else:
            log_and_emit("✓", "Depth valid")

        # 3. Storage Writability
        try:
            test_path = os.path.join(self.storage.courses_dir, ".writetest")
            with open(test_path, "w") as f:
                f.write("test")
            os.remove(test_path)
            log_and_emit("✓", "Storage writable")
        except Exception as e:
            log_and_emit("✗", f"Storage unwritable ({e})")
            all_passed = False

        # 4. LLM Health Check
        #
        # Probe /v1/models — the standard OpenAI-compatible endpoint — before
        # falling back to the server root. Checking only "/" assumed Ollama,
        # which serves a root page; mlx_lm.server does not and returns 404, so
        # preflight aborted course creation against a perfectly healthy backend
        # with "LLM returned HTTP 404". MLX is a first-class backend under the
        # Apple-native-first decision, so the check has to be backend-agnostic.
        start_t = time.time()
        base = LLM_API_URL.replace("/v1/chat/completions", "")
        llm_ok, last = False, None
        for path in ("/v1/models", "/"):
            try:
                resp = requests.get(base + path, timeout=5)
                if 200 <= resp.status_code < 300:
                    llm_ok = True
                    break
                last = f"HTTP {resp.status_code} at {path}"
            except Exception as e:
                last = f"{type(e).__name__} at {path}"
        if llm_ok:
            lat = int((time.time() - start_t) * 1000)
            log_and_emit("✓", f"LLM Online (latency: {lat}ms)")
        else:
            log_and_emit("✗", f"LLM unreachable ({last})")
            all_passed = False

        # 5. THE MODEL CAN ACTUALLY GENERATE.
        #
        # This line used to be `log_and_emit("✓", "LLM content generation
        # ready")` with NOTHING above it — a leftover from when content
        # providers existed. It reported a green check unconditionally, every
        # single build, whatever the model was doing. And the health check
        # above it only pings /v1/models: it proves the SERVER answers, not
        # that a model is loaded or that it can emit a token. A server up with
        # a missing or wedged model passed preflight and failed later, per
        # concept, after the learner had been told everything was fine.
        if llm_ok:
            try:
                # A COLD MODEL IS NOT A BROKEN ONE.
                #
                # This probe asks for 8 tokens and gave up after one 90s
                # attempt, which is shorter than the time this hardware needs
                # to page a 13.7 GB model into memory. Measured 2026-08-25: a
                # build was refused at preflight — "the model is probably still
                # loading" — and the model was, in fact, still loading. The
                # check was right about the cause and wrong about the verdict.
                #
                # The first attempt therefore pays for the load. If it comes
                # back empty, the load is what we were waiting on, so try once
                # more now that it is resident rather than failing a build the
                # model could have run.
                # ASK WHETHER IT IS LOADING; DO NOT INFER IT FROM A TIMEOUT.
                #
                # Two 90-second probes still cannot outlast a cold load on this
                # machine — measured 2026-08-28: both attempts timed out, the
                # check announced "it is wedged, not merely loading", and the
                # build was refused while the weights were in fact still being
                # paged in. Every build on a cold model died at preflight.
                #
                # Ollama can be asked directly. While the model is absent from
                # /api/ps a load is genuinely in progress, so waiting is the
                # right answer however long it takes; once it is resident and
                # still emits nothing, "wedged" is a fair verdict.
                from llm_client import LLMClient as _Probe
                probe_client = _Probe()

                # ENOUGH BUDGET TO THINK BEFORE IT SPEAKS.
                #
                # This probe asked for 8 tokens. The configured model spends
                # roughly 200 on reasoning before it emits any content, so the
                # budget was gone before the first visible character and the
                # call returned an empty string — every time, forever. Measured
                # 2026-08-28 against a warm model: max_tokens=8 -> 8 tokens,
                # content ''; 64 -> 64 tokens, content ''; 256 -> 215 tokens,
                # content 'ready'. Preflight read that empty string as a wedged
                # model and refused to build. `think=False` does not suppress it
                # for this model, so the budget is the thing that has to change.
                def _try_probe():
                    return llm_generate(
                        prompt="Reply with the single word: ready",
                        sys_prompt="You reply with one word.",
                        max_tokens=320, retries=1)

                probe = _try_probe()
                attempts = 1
                while not (probe and probe.strip()) and attempts < 4:
                    try:
                        loading = probe_client._model_is_loading()
                    except Exception:
                        loading = False
                    if not loading:
                        break               # resident and mute — that is wedged
                    log_and_emit("…", f"Model is still loading — waiting "
                                      f"(attempt {attempts + 1})")
                    probe = _try_probe()
                    attempts += 1

                if probe and probe.strip():
                    log_and_emit("✓", "Model generated a test response")
                else:
                    log_and_emit("✗", "Model is loaded but generated nothing — "
                                      "it is wedged, not merely loading")
                    all_passed = False
            except Exception as e:
                log_and_emit("✗", f"Model could not generate ({type(e).__name__})")
                all_passed = False

        # 6. THE GROUNDING PIPELINE, which the build depends on per concept.
        #
        # Nothing checked this. Measured today: SearXNG's engines all
        # CAPTCHA a datacenter, so every web query returned zero results, the
        # documentation arm was dead, and a course was written entirely from
        # model memory — while preflight reported six green checks. A build
        # that cannot reach its sources should say so BEFORE spending an hour,
        # not through a depth contract that fails afterwards.
        research_url = os.getenv("RESEARCH_URL", "http://helga-research:5006")
        try:
            r = requests.get(f"{research_url}/health", timeout=8)
            if 200 <= r.status_code < 300:
                log_and_emit("✓", "Research service reachable")
            else:
                log_and_emit("✗", f"Research service unhealthy (HTTP "
                                  f"{r.status_code}) — concepts would be "
                                  f"written with no sources")
                all_passed = False
        except Exception as e:
            log_and_emit("✗", f"Research service unreachable ({type(e).__name__}) "
                              f"— concepts would be written with no sources")
            all_passed = False

        # 7. Search returning ANYTHING. A warning rather than a block: a course
        #    can still be built from encyclopaedias and open textbooks without
        #    web search, and the scope screen already reports grounding. But it
        #    is the difference between a cited course and a remembered one, so
        #    it is said out loud rather than discovered in the output.
        try:
            sx = os.getenv("SEARXNG_URL", "http://helga-searxng:8080")
            probe = requests.get(f"{sx}/search",
                                 params={"q": "test", "format": "json"},
                                 timeout=10)
            hits = len((probe.json() or {}).get("results") or []) if probe.ok else 0
            if hits:
                log_and_emit("✓", f"Web search returning results ({hits} for a probe)")
            else:
                log_and_emit("!", "Web search returned nothing — official "
                                  "documentation cannot be found, so concepts "
                                  "will lean on encyclopaedias and the model")
        except Exception as e:
            log_and_emit("!", f"Web search unavailable ({type(e).__name__}) — "
                              f"documentation cannot be found")

        return all_passed

    def _validate_phase(
        self, phase_name: str, items: list, min_count: int, parent_title: str
    ) -> Tuple[bool, List[str]]:
        """Validates that a generated array of items meets the minimum constraints."""
        issues = []
        if not items or not isinstance(items, list):
            issues.append(f"No {phase_name} generated for {parent_title}")
            return False, issues

        if len(items) < min_count:
            issues.append(
                f"Insufficient {phase_name} generated for {parent_title} (expected >= {min_count}, got {len(items)})"
            )
            return False, issues

        if phase_name == "concepts":
            for c in items:
                # Accept either 'objectives' or 'learning_objectives' from LLM output
                has_objectives = bool(
                    c.get("objectives") or c.get("learning_objectives")
                )
                if not has_objectives:
                    # Warn but don't block — small LLMs may omit objectives
                    logger.warning(
                        f"Concept '{c.get('title', 'Unknown')}' missing objectives, injecting placeholder."
                    )
                    c["objectives"] = [f"Understand {c.get('title', 'this concept')}"]

        return True, issues

    def build(
        self, topic: str, max_depth: int = 2, module_depths: Dict[str, int] = None
    ) -> str:
        """
        Main Entry Point: Generates the Course Skeleton with Enforced Progression.
        Now writes to JSON structure instead of KuzuDB.
        Protected by _build_lock to prevent concurrent course builds.
        """
        # EPISTEMIC GATE, before anything is written.
        #
        # Two outcomes, and the distinction is the whole design. A REFUSAL
        # stops the build and says why — a very short list, about operational
        # capability to hurt people, not about ideas. Everything else — a
        # fringe claim, a partisan title, a religious question — is REFRAMED
        # and still built, because a course EXAMINING the flat-earth argument
        # teaches more physics than a refusal ever could, and refusing hands a
        # believer the best evidence they could ask for that the answer cannot
        # survive being given.
        self.epistemic_frame = ""
        self.epistemic_stance = "ORDINARY"
        try:
            from services.common.epistemic_stance import course_frame, REFUSE
            _stance, _instr = course_frame(topic)
            if _stance == REFUSE:
                logger.warning(f"[EPISTEMIC] refused {topic!r}: {_instr}")
                if self.status_callback:
                    self.status_callback(f"ERROR: {_instr}")
                return None
            self.epistemic_stance = _stance
            self.epistemic_frame = _instr or ""
            if _instr:
                logger.info(f"[EPISTEMIC] {topic!r} framed as {_stance}")
        except Exception as e:                   # pragma: no cover - defensive
            logger.warning(f"[EPISTEMIC] stance check failed: {e}")

        # Durable record so the UI survives navigation and can lock itself.
        # Best-effort throughout: recording progress must never break a build.
        try:
            from services.common import build_state
        except Exception:
            build_state = None

        if build_state:
            build_state.start(topic, source=getattr(self, "build_source", "topic"))
            # Every status event now also lands in the durable record.
            _original_cb = self.status_callback

            def _tee(message):
                self._record_progress(message)
                if _original_cb:
                    _original_cb(message)
            self.status_callback = _tee

        if not _build_lock.acquire(blocking=False):
            msg = "Another course is already being built. Please wait for it to finish."
            logger.warning(msg)
            if self.status_callback:
                self.status_callback(f"ERROR: {msg}")
            return None

        try:
            return self._build_inner(topic, max_depth, module_depths)
        finally:
            _build_lock.release()
            if build_state:
                build_state.finish()

    def _classify_concepts_by_domain(self, course_dict, topic):
        """Set `concept_kind` on every concept, via the domain registry.

        Pattern-only: no book, no model call. Routed through the registry so a
        history course cannot inherit computer-science kinds — the separation
        rule `tests/domains/test_domain_separation.py` enforces.

        Never raises. A classification failure must cost the guidance, not the
        course.
        """
        try:
            from services.domains.registry import (
                for_subject, kind_for_concept, DOMAIN_KEY)
        except Exception as e:
            logger.debug(f"[DOMAIN] registry unavailable: {e}")
            return

        title = course_dict.get("title") or topic or ""

        # THE MATCHING STEP: name, description, and the shape it built.
        #
        # Keywords run first inside `for_subject` and are free when they hit.
        # This context is for when they do not — a bare title is often
        # genuinely ambiguous ("Vectors" is mathematics or biology, "Trees" is
        # computer science or botany) and the module list settles it at once.
        # Measured before this existed: eight of sixteen realistic topics got
        # no domain at all.
        modules = [m.get("title", "") for m in (course_dict.get("modules") or [])]
        context_lines = []
        if course_dict.get("overview"):
            context_lines.append(f"Description: {course_dict['overview'][:300]}")
        if modules:
            context_lines.append("Modules: " + "; ".join(
                t for t in modules[:8] if t))
        context = "\n".join(context_lines) or None

        try:
            ext = for_subject(f"{title} {topic or ''}",
                              llm_json_fn=llm_generate_json, context=context)
        except Exception as e:
            logger.debug(f"[DOMAIN] subject lookup failed: {e}")
            return
        if not ext or not hasattr(ext, "classify"):
            return

        dom = getattr(ext, "DOMAIN", None)
        tally = {"by_pattern": 0, "unknown": 0}
        for module in (course_dict.get("modules") or []):
            for unit in (module.get("units") or []):
                for lesson in (unit.get("lessons") or []):
                    for concept in (lesson.get("concepts") or []):
                        name = (concept.get("title") or "").strip()
                        if not name:
                            continue
                        # A CONCEPT MAY LEAVE ITS COURSE'S DOMAIN.
                        #
                        # Real syllabuses are not single-domain. Measured on a
                        # career checklist: "Data Science Foundations" routes
                        # to computer science, correctly, and its statistics
                        # and causal-inference concepts then came out UNKNOWN
                        # because the CS classifier has nothing to say about
                        # them. They are mathematics concepts inside a
                        # computing course, which is what that subject is.
                        #
                        # The course's own domain is tried first and its
                        # confident answer always wins — see
                        # `kind_for_concept`.
                        try:
                            cdom, kind = kind_for_concept(
                                name, dom, "",
                                concept.get("learning_objectives"))
                        except Exception:
                            cdom, kind = None, None
                        if not kind or kind == "UNKNOWN":
                            tally["unknown"] += 1
                            continue
                        concept["concept_kind"] = kind
                        # Record WHICH domain taught it when it differs, so
                        # `_domain_teaching` reads the right guidance rather
                        # than the course's.
                        if cdom and cdom != dom:
                            concept["concept_domain"] = cdom
                            tally.setdefault("borrowed", 0)
                            tally["borrowed"] += 1
                        tally["by_pattern"] += 1

        # THE LLM TAKES THE TAIL, and this is where classification actually
        # gets good.
        #
        # Patterns are free and exact when they hit, and they leak endlessly:
        # this session alone fixed plurals, singulars, ambiguous words and
        # match ordering in them and kept finding gaps. A model that has never
        # seen the chapter still knows what "Slowly changing dimensions" or
        # "Row-level security" IS.
        #
        # Batched per LESSON — one call for a lesson's whole concept list, not
        # one per concept — and only for what patterns left UNKNOWN, so a hit
        # never pays for a call.
        if tally["unknown"] and hasattr(ext, "classify_concepts"):
            try:
                # `topic` is forwarded, and it is not decoration: a concept
                # title does not carry its own subject. "Vectors" is a data
                # structure, a matrix column or a disease carrier depending
                # only on the course around it, and the classifier saw the
                # lesson title and the concept names with no way to tell.
                try:
                    sub = ext.classify_concepts(
                        course_dict, None, llm_json_fn=llm_generate_json,
                        status_callback=self.status_callback, topic=topic)
                except TypeError:
                    # A domain that has not grown the argument yet still runs.
                    sub = ext.classify_concepts(
                        course_dict, None, llm_json_fn=llm_generate_json,
                        status_callback=self.status_callback)
                if isinstance(sub, dict):
                    gained = int(sub.get("by_reading") or 0)
                    if gained:
                        tally["by_reading"] = gained
                        tally["unknown"] = max(
                            0, tally["unknown"] - gained)
                        logger.info(
                            f"[DOMAIN] the model classified {gained} concept(s) "
                            f"the patterns could not")
            except Exception as e:
                logger.warning(f"[DOMAIN] llm classification failed: {e}")

        if tally["by_pattern"] or tally["unknown"]:
            course_dict[DOMAIN_KEY] = getattr(ext, "DOMAIN", None)
            course_dict["concept_kinds"] = tally
            logger.info(
                f"[DOMAIN] {getattr(ext, 'DOMAIN', '?')}: classified "
                f"{tally['by_pattern']} concept(s) by pattern, "
                f"{tally['unknown']} unknown")
            if self.status_callback:
                self.status_callback(
                    f"DOMAIN:KINDS:{tally['by_pattern']}:{tally['unknown']}")

    def _record_progress(self, message):
        """Mirror a status event into the durable build record.

        The Socket.IO stream only reaches a browser that is currently on the
        page. A learner who navigates to Courses and back had no way to see
        what happened while they were away, even though the same build was
        still running server-side.
        """
        try:
            from services.common import build_state
            build_state.note(message)
            if str(message).startswith("STRUCT:MODULE:"):
                st = build_state.current() or {}
                build_state.update(modules=(st.get("modules") or 0) + 1)
        except Exception:
            pass

    def _build_inner(
        self, topic: str, max_depth: int = 2, module_depths: Dict[str, int] = None
    ) -> str:
        """The actual build logic, called under _build_lock."""
        _pipeline_start = time.perf_counter()
        course_uid = f"course_{uuid.uuid4().hex[:8]}"
        self.module_depths = module_depths or {}

        profile = DEPTH_PROFILES.get(max_depth, DEPTH_PROFILES[2])
        self.depth_profile = profile
        # Three-slider system overrides academic_context
        cp = self.course_params
        self.academic_context = f"{cp['mastery_label']} (mastery {self.mastery}/5, scope {self.scope}/5)"

        # Pre-flight checks
        if not self._run_preflight_checks(topic, max_depth):
            msg = "ABORTING COURSE CREATION: Pre-flight checks failed."
            logger.error(msg)
            if self.status_callback:
                self.status_callback(f"ERROR: {msg}")
            return None

        if self.status_callback:
            self.status_callback(f"LOG: Preflight checks completed successfully.")

        # Build course dict in-memory instead of KuzuDB
        course_dict = {
            "uid": course_uid,
            "title": topic,
            "teaching_style": self.teaching_style,
            # PERSISTED, not just used once. The brief shaped the modules
            # minutes ago; hydration runs for hours afterwards, and a resume
            # or a handback runs in a different process entirely. Without this
            # the course knows what it is called and not what it is for.
            "learner_context": self.learner_context,
            "status": "skeleton",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "scope": self.scope,
            "mastery": self.mastery,
            "starting_from": self.starting_from,
            # GAP 1: Persist Bloom boundaries for live tutoring
            "bloom_floor": cp["bloom_floor"],
            "bloom_ceiling": cp["bloom_ceiling"],
            "modules": [],
        }

        # Use three-slider parameters
        cp = self.course_params
        target_modules = cp["modules"]
        logger.info(f"Generating progressive skeleton for '{topic}' "
                     f"(scope={self.scope}, mastery={self.mastery}, start={self.starting_from})...")

        # Get domain-specific constraints and examples
        constraints = self._get_domain_constraints(topic)
        temporal_constraint = constraints["temporal_constraint"]
        category_constraint = constraints["category_constraint"]
        example_json = constraints["example_json"]

        self.used_titles = set()
        self.used_titles_by_level = {
            "module": set(),
            "unit": set(),
            "lesson": set(),
            "concept": set(),
        }

        # Build Bloom progression map — distribute levels across modules
        bloom_floor = cp["bloom_floor"]
        bloom_ceiling = cp["bloom_ceiling"]
        bloom_labels = {1: "Remember", 2: "Understand", 3: "Apply", 4: "Analyze", 5: "Evaluate", 6: "Create"}

        # Calculate per-module bloom targets (single shared formula — B1.1.4)
        module_bloom_targets = []
        for i in range(target_modules):
            bloom = progressive_bloom(i, target_modules, bloom_floor, bloom_ceiling)
            module_bloom_targets.append((bloom, bloom_labels.get(bloom, "Apply")))

        # Build the progression schedule string for the prompt
        progression_schedule = "\n".join([
            f"  Module {i+1}: Bloom {b} ({l}) — " + (
                "Beginner-friendly. Define key terms. Use analogies." if b <= 1 else
                "Build understanding. Explain how concepts connect." if b == 2 else
                "Apply concepts. Show methods and procedures." if b == 3 else
                "Analyze and compare. Formal methods and trade-offs." if b == 4 else
                "Evaluate and critique. Advanced techniques and limitations." if b == 5 else
                "Create and synthesize. Original application of frameworks."
            )
            for i, (b, l) in enumerate(module_bloom_targets)
        ])

        starting_guide = cp["starting_instruction"]

        # Real chapter lists for this subject, fetched before generation.
        _syllabus_evidence_block = self._syllabus_evidence(topic)
        # Retain it. The evidence used to shape MODULE titles and stop there,
        # while the syllabus check counts CONCEPTS — so the one stage that
        # determines coverage was the one stage generating blind. A 31-chapter
        # Geometry outline is worth far more to concept naming than to the four
        # module headings above it.
        self._evidence_block = _syllabus_evidence_block

        # Structured prompt with explicit progression schedule
        # THE FRAME LEADS. A course whose topic is a settled-against claim or a
        # partisan proposition has to be shaped as an examination BEFORE the
        # module plan exists — module titles are where "Why X is true" becomes
        # a curriculum, and no later pass can unpick that.
        _frame = getattr(self, "epistemic_frame", "") or ""
        prompt = (
            (f"{_frame}\n\n{'=' * 60}\n\n" if _frame else "")
            + f"Topic: {topic}\n"
            f"Scope: {self.scope}/5 ({cp['scope_label']} — {cp['scope_desc']})\n"
            f"Mastery Target: {self.mastery}/5 ({cp['mastery_label']}) — student wants to reach Bloom level {bloom_ceiling} ({bloom_labels.get(bloom_ceiling, 'Apply')})\n"
            f"Student Background: {self.starting_from}/5 ({cp['starting_label']}) — starting at Bloom level {bloom_floor} ({bloom_labels.get(bloom_floor, 'Remember')})\n"
            f"{temporal_constraint}\n"
            f"{category_constraint}\n\n"
            + (f"WHAT THIS LEARNER SAID THEY WANT, in their own words. It "
               f"outranks the topic word: let it decide what belongs in this "
               f"course and what does not, and which sense of an ambiguous "
               f"subject is meant.\n\"{self.learner_context}\"\n\n"
               if self.learner_context else "")
            + f"Create exactly {target_modules} PROGRESSIVE modules for a course on '{topic}'.\n\n"
            f"PROGRESSION SCHEDULE — Each module MUST match its assigned complexity level:\n"
            f"{progression_schedule}\n\n"
            f"THIS IS A JOURNEY from Bloom {bloom_floor} ({bloom_labels.get(bloom_floor)}) to Bloom {bloom_ceiling} ({bloom_labels.get(bloom_ceiling)}).\n"
            f"The FIRST modules must be genuinely accessible to someone with {cp['starting_label'].lower()} background.\n"
            f"The LAST modules should reach {cp['mastery_label'].lower()} level.\n"
            f"DO NOT start at the target level — BUILD UP TO IT gradually.\n\n"
            f"STUDENT BACKGROUND: {starting_guide}\n\n"
            "CRITICAL RULES:\n"
            "1. PROGRESSIVE BUILD ORDER — Module 1 is the SIMPLEST. Each module increases in sophistication. "
            "A student who skips Module 2 CANNOT understand Module 3. "
            "The first 1-2 modules must use plain language and concrete examples.\n"
            "2. NON-OVERLAPPING SCOPE — each module's 'scope' array covers DIFFERENT sub-topics.\n"
            "3. Specific multi-word titles naming REAL recognized sub-areas. "
            "No generic words (Basics/Overview/Advanced/Introduction/Fundamental).\n"
            "4. The 'scope' array concepts must MATCH THAT MODULE'S BLOOM LEVEL — "
            "Module 1 scope should be simple concepts, final module scope should be advanced.\n"
            "5. Each module's 'rationale' must explain HOW it builds on the previous module "
            "AND why this complexity level is appropriate at this point in the journey.\n\n"
            + (f"\n{_syllabus_evidence_block}\n\n"
               if _syllabus_evidence_block else "") +
            "ANTI-COPY WARNING: The example below shows JSON FORMAT ONLY. "
            "Replace ALL bracket placeholders with real content from " + topic + ".\n"
            "Do NOT use words from the example like 'Foundational', 'Core Methods', 'Advanced Applications'.\n\n"
            f"Return strict JSON array:\n{example_json}"
        )

        # COPY-SPINE. When a real textbook for this exact subject is in hand, its
        # chapter list IS the module spine and inventing another one is pure
        # downside.
        #
        # Measured against MIT 18.06: an invented spine covered 7-9 of 10
        # published topic areas and ran 38-59% LONGER than the real course. It
        # was never short of room — it simply never SELECTED least squares,
        # projections or Gram-Schmidt, and the same cluster went missing on
        # every run. Selection is where coverage is lost, and copying removes the
        # selection step entirely.
        spine = self._spine_from_syllabus(topic, target_modules)

        max_retries = 3
        modules = []
        correction_header = ""

        if spine:
            # Set the module list and skip generation entirely. NOT an early
            # return: this is inside _build_inner, which returns the course uid,
            # so returning the spine here aborted the build and handed a list
            # back where a uid was expected.
            modules = spine
            logger.info(f"[SPINE] using {len(spine)} modules copied from the real "
                        f"syllabus instead of generating them")
            if self.status_callback:
                self.status_callback(
                    f"STRUCT:SPINE:{len(spine)} modules from the published syllabus")
            max_retries = 0

        if self.status_callback:
            self.status_callback(
                f"LOG: Generating {target_modules} course modules for '{topic}'..."
            )

        for attempt in range(1, max_retries + 1):
            modules = []
            self.used_titles = set()  # Reset for each attempt
            self.used_titles_by_level = {
                "module": set(), "unit": set(), "lesson": set(), "concept": set(),
            }

            # Combine correction header with original prompt if needed
            current_prompt = (
                f"{correction_header}\n{prompt}" if correction_header else prompt
            )
            raw_sys = (
                f"Expert curriculum designer specializing in {topic}. "
                f"You are designing a {cp['mastery_label']}-level course (mastery {self.mastery}/5) "
                f"for a student with {cp['starting_label'].lower()} background (starting from {self.starting_from}/5). "
                f"The course scope is {cp['scope_label'].lower()} ({self.scope}/5). "
                f"MATCH CONCEPT COMPLEXITY TO THE MASTERY LEVEL — do not overshoot. "
                f"Response must be a JSON array of exactly {target_modules} objects. "
                f"Use ONLY real, established terminology from {topic} — never invent terms."
            )

            logger.info(
                f"Module generation attempt {attempt}/{max_retries} for '{topic}'..."
            )
            if self.status_callback:
                if attempt == 1:
                    self.status_callback(
                        f"LOG: Calling LLM for module structure (attempt {attempt}/{max_retries})..."
                    )
                else:
                    self.status_callback(
                        f"LOG: Retrying module generation (attempt {attempt}/{max_retries})..."
                    )
            new_batch = llm_generate_json(
                current_prompt,
                sys_prompt=raw_sys,
                max_tokens=800,
                progress_callback=self.status_callback,
            )

            if new_batch:
                for m in new_batch:
                    if not isinstance(m, dict):
                        continue
                    original_title = m.get("title", "")
                    title = self._normalize_title(original_title)
                    if not title or self._is_duplicate(
                        title, course_topic=topic, is_module=True
                    ):
                        logger.warning(
                            f"Skipping duplicate/invalid module title: '{original_title}'"
                        )
                        continue
                    m["title"] = title
                    scope = m.get("scope", [])
                    if isinstance(scope, str):
                        scope = [s.strip() for s in scope.split(",")]
                    m["scope"] = scope
                    m["level"] = len(modules) + 1
                    modules.append(m)
                    self.used_titles.add(title)
                    self.used_titles_by_level["module"].add(title)

            valid, issues = self._validate_phase(
                "modules", modules, target_modules, topic
            )
            if valid:
                break

            logger.warning(f"Attempt {attempt} failed validation: {issues}")
            existing_titles = (
                ", ".join([m.get("title", "?") for m in modules])
                if modules
                else "None generated"
            )
            correction_header = (
                f"### CRITICAL SELF-CORRECTION (Attempt {attempt + 1})\n"
                f"Your previous attempt generated {len(modules)} modules: [{existing_titles}]\n"
                f"REQUIRED: Exactly {target_modules} modules with NON-OVERLAPPING scopes.\n"
                f"ISSUES: {'; '.join(issues)}\n"
                "FIX: Add MORE modules covering DIFFERENT aspects of the topic. Each module must have a unique scope."
            )

            if attempt == max_retries:
                msg = f"ABORTING COURSE CREATION: LLM consistently failed to generate {target_modules} modules after {max_retries} attempts. Issues: {issues}"
                logger.error(msg)
                if self.status_callback:
                    self.status_callback(f"ERROR: {msg}")
                raise CourseCreationError(msg)

        # Abort if the primary generation failed entirely instead of using fallbacks
        if not modules and not spine:
            msg = "ABORTING COURSE CREATION: LLM failed to return a valid JSON structure for the course modules."
            logger.error(msg)
            if self.status_callback:
                self.status_callback(f"ERROR: {msg}")
            raise CourseCreationError(msg)

        modules.sort(key=lambda x: int(x.get("level", 99)))
        modules = modules[:target_modules]

        if self.status_callback:
            self.status_callback("SYLLABUS:PHASE:1_SKELETON")
            self.status_callback(
                f"LOG: Progressive Skeleton generated. Found {len(modules)} modules."
            )

        module_refs = []
        for i, mod in enumerate(modules, 1):
            m_uid = f"mod_{uuid.uuid4().hex[:8]}"
            m_title = mod["title"]
            m_scope = mod["scope"]

            if i == 1:
                role_desc = "foundational definitions and core theory"
            elif i == len(modules):
                role_desc = "advanced synthesis and applications"
            else:
                role_desc = "intermediate mechanisms and relationships"

            if self.status_callback:
                self.status_callback(f"STRUCT:MODULE:{m_title}")

            # Build module dict instead of Cypher CREATE
            # GAP 2: Persist per-module Bloom target for live tutoring
            _bt = module_bloom_targets[i - 1] if module_bloom_targets and (i - 1) < len(module_bloom_targets) else None
            module_dict = {
                "uid": m_uid,
                "title": m_title,
                "ordinal": i,
                "progression_role": role_desc,
                "scope": m_scope,
                "bloom_target": _bt[0] if _bt else cp.get("bloom_ceiling", 3),
                "bloom_label": _bt[1] if _bt else "Apply",
                "units": [],
            }
            course_dict["modules"].append(module_dict)
            module_refs.append(
                {
                    "uid": m_uid,
                    "title": m_title,
                    "role_desc": role_desc,
                    "scope": m_scope,
                    "dict": module_dict,
                }
            )

        # Generate Sub-Structures (Units -> Lessons -> Concepts)
        if self.status_callback:
            self.status_callback(
                f"LOG: Building sub-structures for {len(module_refs)} modules..."
            )
        self._build_substructures_progressive(
            module_refs, max_depth, topic, modules,
            module_bloom_targets=module_bloom_targets,
        )

        # WIZ-3: Record fallback count in course metadata
        if self.fallback_count > 0:
            course_dict["fallback_count"] = self.fallback_count
            logger.warning(
                f"Course '{topic}' skeleton built with {self.fallback_count} LLM fallback(s) — "
                f"some titles/content used hardcoded defaults instead of LLM-generated content."
            )
            if self.status_callback:
                self.status_callback(
                    f"LOG: Warning — {self.fallback_count} item(s) used fallback titles due to LLM failures."
                )

        # Normalise degenerate lessons before persisting.
        self._merge_degenerate_lessons(course_dict)
        self._drop_empty_units(course_dict)

        # Close the loop the syllabus check never closed. Until now the coverage
        # verdict was DIAGNOSTIC ONLY: it reported a hole and nothing acted on
        # it. Measured against MIT 18.06, a generated Linear Algebra course
        # covered 7 of 10 published topic areas while running 59% LONGER than
        # the real course — it was not short of room, it simply never selected
        # the orthogonality cluster. Reporting that after the fact helps nobody;
        # the outline is still in hand and cheap to extend.
        _sf = getattr(self, "_scope_fit", None)
        if _sf:
            course_dict["scope_fit"] = _sf
        _rl = getattr(self, "_research_loop_result", None)
        if _rl:
            course_dict["research_loop"] = _rl

        self._backfill_uncovered_chapters(course_dict, topic)

        # Gate criterion 6 — syllabus realism. Runs here, on the skeleton,
        # BEFORE the expensive hydration: a curriculum hole is an outline
        # defect, and finding it after 40 minutes of hydration teaches nothing
        # that finding it now does not.
        self._record_syllabus_check(course_dict)

        # Hand supplementary sources forward to hydration, labelled, having kept
        # them out of every structural decision. Recorded on the course rather
        # than passed in memory so a hydration run that happens later, or in
        # another process, sees the same classification this build made.
        _supp = getattr(self, "_supplementary_sources", None)
        if _supp:
            course_dict["supplementary_sources"] = [
                {"source": o.get("source"), "book": o.get("book"),
                 "url": o.get("url"), "relevance": o.get("relevance"),
                 "chapters": o.get("chapters") or []}
                for o in _supp
            ]
            course_dict["supplementary_policy"] = {
                "grounding_bar": GROUNDING_RELEVANCE,
                "max_share": SUPPLEMENTARY_MAX_SHARE,
                # MEASURED IN CLAIMS, NOT SOURCES.
                #
                # The research reviewing this policy caught the unit being
                # wrong: one supplementary book can dominate a course's content
                # while being a small minority of the source LIST, so a cap
                # counted per source bounds nothing that matters. The share that
                # needs bounding is of claims grounded only in supplementary
                # material — and those claims are also the priority targets for
                # fact-checking, precisely because their source is weaker.
                "share_unit": "claims grounded only in supplementary sources",
                "usable_for": "content hydration only",
                "excluded_from": ["structure", "scope assessment",
                                  "coverage checklist", "coverage measurement"],
                "why": ("below the grounding bar these sources do not speak for "
                        "the subject, but may still serve an individual concept "
                        "— a judgement that can only be made per concept"),
            }

        # DOMAIN CLASSIFICATION ON THE TOPIC-TYPED PATH.
        #
        # This file had NO reference to the domain registry at all, which meant
        # every domain module — all of them — ran only for courses built from
        # an uploaded book. Type "teach me calculus" into the box and none of
        # it fired: no per-kind guidance, no NEVER_SOLVE, no NEVER_QUIZ, no
        # "never demand an observation". The prohibitions that define each
        # domain reached the tutor only by upload.
        #
        # `book_skeleton` does this by READING the source, which is better and
        # is not available here — there is no book on this path. Titles and
        # objectives are, and the pattern classifiers work on exactly those.
        # An UNKNOWN concept still gets its domain's standing rule, so the
        # floor is safe; what this recovers is the per-kind ceiling.
        # BEFORE classification, not after: typing a padded title wastes a
        # model call and puts a kind on a concept that is about to be dropped.
        self.prune_placeholder_scaffolding(course_dict)

        self._classify_concepts_by_domain(course_dict, topic)

        # Write course structure to JSON
        self.storage.courses.create_course(course_dict)
        _skeleton_elapsed = time.perf_counter() - _pipeline_start
        logger.info(
            f"[TIMING] Skeleton build completed in {_skeleton_elapsed:.1f}s: {course_uid}"
        )

        return course_uid

    def _syllabus_evidence(self, topic):
        """PHASE-1 RESEARCH: gather and analyse material BEFORE any structure.

        The research node used to run only during hydration — Phase 2, once the
        skeleton already existed. So the most consequential decision in the
        pipeline, *what this course is made of*, was taken with no evidence at
        all: one LLM call, from recall. The measured result was a course
        covering 42% of its own subject that passed every structural detector,
        because it was structurally clean and substantively hollow.

        Research now runs in BOTH phases, and this is Phase 1. It returns
        several INDEPENDENT accounts of how the subject is organised — open
        textbook syllabi, course sequences, and the titles of books written at
        the requested level — framed as material to synthesise, not a template
        to copy. A copied table of contents is somebody else's course, at
        somebody else's level, in somebody else's order.

        The preset steers the search rather than filtering afterwards:
        "Quick Overview" and "Graduate Seminar" on one topic should not
        retrieve the same material, and fetching graduate texts for a beginner
        then asking the model to simplify them produces the worst of both.

        Returns "" when nothing is found; the caller then generates unguided
        and says so, because falling back silently would hide exactly the
        condition that caused the original defect.
        """
        if os.getenv("HELGA_SYLLABUS_EVIDENCE", "1") == "0":
            return ""
        try:
            from services.research.curriculum_research import (
                curriculum_brief, format_brief)
        except Exception as e:
            logger.info(f"[SKELETON] phase-1 research unavailable: {e}")
            return ""

        # A narrow topic has no book of its own — "the pythagorean theorem"
        # matches no Wikibook — which is the case that produced the 42% course.
        # One cheap call names the parent discipline so real syllabi are found.
        # MEASURED FAILURE (Task 0): this call gates the whole grounding chain,
        # and on a cold model it exceeded the 90 s timeout three times — Nail's
        # cold load is ~142 s, so the budget cannot be met from cold by
        # construction. The empty result was then read as "this topic HAS no
        # parent discipline", only the narrow topic was tried, no Wikibook
        # matched, and the build went UNGUIDED while reporting success.
        # Absent-vs-zero, gating everything downstream.
        #
        # Three defences, in order: a cache (this answer is deterministic and
        # identical across rebuilds — docs/CACHING.md candidate 1), a
        # source-based fallback that needs no LLM at all, and an explicit
        # degraded flag so "we could not look" never masquerades as "nothing
        # exists".
        broader, self._broadening_degraded = self._parent_subjects(topic)

        # ONE broadened lookup, not one full sweep per candidate.
        #
        # This loop used to call curriculum_brief() once per candidate, and each
        # call is a complete Wikibooks + Wikiversity + Wikipedia + Internet
        # Archive sweep. Wikimedia throttles bursts, so by the third candidate —
        # reliably the discipline-level one that actually has a syllabus — the
        # API returned empty and the build proceeded UNGUIDED. Measured: a
        # standalone curriculum_brief('Geometry') finds 31 chapters, while the
        # same call as the third in a burst found 0.
        #
        # subject_outline() already accepts broader_subjects and tries them
        # internally; passing them collapses three sweeps into one.
        brief = None
        try:
            brief = curriculum_brief(
                topic, mastery=self.mastery, scope=self.scope,
                starting_from=self.starting_from,
                preset_label=getattr(self, "preset_label", None),
                broader_subjects=broader)
        except Exception as e:
            logger.warning(f"[SKELETON] curriculum_brief failed for {topic!r}: {e}")
        if brief and brief.get("found") and broader:
            logger.info(f"[SKELETON] evidence for {topic!r} (broadened via {broader})")

        # SPLIT THE EVIDENCE BEFORE ANYTHING CONSUMES IT.
        #
        # Every consumer below asks a different question of the same brief, and
        # only some of those questions a weak source has earned the right to
        # answer. Partitioning here — once, at the source — is what makes the
        # distinction hold everywhere instead of at the one call site that
        # remembered to check.
        brief, supplementary = self._partition_brief(brief, topic)

        if not brief or not brief.get("found"):
            logger.warning(
                f"[SKELETON] no curriculum evidence for {topic!r} "
                f"(tried: {[topic] + broader}). Falling back to iterative "
                f"research rather than generating UNGUIDED.")
            if self.status_callback:
                self.status_callback("CHECK:SYLLABUS_EVIDENCE:NONE")
            # THE SOURCELESS PATH RUNS *HERE*, NOT ONLY BELOW.
            #
            # Measured: with the evidence partition in place, a Dungeon
            # Mastering build correctly reported itself sourceless — and then
            # returned through this branch, which predates the loop and exits
            # before reaching its trigger further down. So the change that was
            # supposed to guarantee the loop ran for sourceless subjects
            # guaranteed it never ran: partition sets found=False, this returns,
            # and the trigger below is unreachable.
            #
            # Before the partition this branch only fired when the sweep found
            # literally nothing, which was rare; now it is the normal sourceless
            # route, so the fallback has to live on it.
            try:
                self._research_loop_result = self._run_sourceless_research(topic)
            except Exception as e:
                logger.warning(f"[LOOP] sourceless research failed: {e}")
                self._research_loop_result = None
            return ""

        n = (sum(len(x["chapters"]) for x in brief.get("syllabi", []))
             + sum(len(c["sections"]) for c in brief.get("courses", [])))
        srcs = len(brief.get("syllabi", [])) + len(brief.get("courses", [])) \
            + len(brief.get("canonical_texts", []))
        logger.info(f"[SKELETON] phase-1 research for {topic!r}: {n} chapters "
                    f"across {srcs} sources, level={brief['level']}")

        # Name what was actually found. "12 sources" tells a learner nothing;
        # "Wikibooks: Geometry (31 chapters)" tells them the course is being
        # built from a real book they could go and read. This IS the product's
        # claim, so it belongs on screen rather than only in a log file.
        if self.status_callback:
            self.status_callback(f"RESEARCH:LEVEL:{brief['level']}")
            for x in brief.get("syllabi", []):
                self.status_callback(
                    f"RESEARCH:SYLLABUS:{x['source']}|{x['book']}|{len(x['chapters'])}")
            for c in brief.get("courses", []):
                self.status_callback(
                    f"RESEARCH:COURSE:Wikiversity|{c['course']}|{len(c['sections'])}")
            for t in brief.get("canonical_texts", [])[:6]:
                yr = f" ({t['year']})" if t.get("year") else ""
                self.status_callback(f"RESEARCH:BOOK:{t['title']}{yr}")
            self.status_callback(
                f"CHECK:SYLLABUS_EVIDENCE:{n} chapters / {srcs} sources")
        # Is there enough subject here to fill what was asked for? Compared
        # BEFORE generation, because the honest options — build the smaller
        # version, broaden the subject, continue anyway — are only offerable
        # while nothing has been built yet.
        try:
            from services.core.scope_fit import assess_scope, describe
        except ImportError:
            from scope_fit import assess_scope, describe
        try:
            _requested = int(self.course_params.get("total_concepts_approx") or 0)

            # LOOK HARDER BEFORE SHRINKING.
            #
            # This was a single `assess_scope` on whatever the first sweep
            # returned, so a subject whose syllabus is merely hard to find was
            # treated exactly like one that has none. `deepen_scope` escalates
            # the search when the arithmetic says thin, re-assesses after each
            # tier, and stops at the first of: sufficiency, saturation, the
            # tier ceiling, or the time budget. On a sufficient or degraded
            # brief it does nothing at all and costs nothing.
            try:
                from services.core.scope_deepen import (
                    deepen_scope, describe_deepening)
            except ImportError:
                from scope_deepen import deepen_scope, describe_deepening

            def _widen(tier, current_brief):
                terms = self._widen_terms(tier, topic)
                if not terms:
                    return None
                logger.info(f"[DEEPEN] {tier['name']}: retrying via {terms}")
                return curriculum_brief(
                    topic, mastery=self.mastery, scope=self.scope,
                    starting_from=self.starting_from,
                    preset_label=getattr(self, "preset_label", None),
                    broader_subjects=terms)

            self._scope_fit = deepen_scope(
                brief, _requested, _widen, requested_courses=1,
                status_callback=self.status_callback)

            # The deepening may have found a better brief — use it downstream
            # rather than the thin one that triggered the search.
            _deepened = self._scope_fit.pop("brief", None)
            if isinstance(_deepened, dict) and _deepened:
                brief = _deepened
            self._deepening_note = describe_deepening(self._scope_fit)
            if self._deepening_note:
                logger.info(f"[DEEPEN] {self._deepening_note}")
                if self.status_callback:
                    self.status_callback(
                        f"SCOPE:DEEPENED:{self._scope_fit['deepening']['stopped']}"
                        f":{self._deepening_note}")

            # EVIDENCE SETS THE LENGTH, within the range.
            #
            # The range exists so a course can be as long as its material
            # warrants, but until now nothing connected the two: the model chose
            # freely inside it, and a subject with 77 chapters of real syllabus
            # still came out at the bottom. The range was permitting variation
            # rather than expressing evidence.
            try:
                from services.core.scope_fit import position_in_range
            except ImportError:
                from scope_fit import position_in_range
            _lo = self.course_params.get("lessons_min")
            _hi = self.course_params.get("lessons_max")
            if _lo and _hi:
                _target, _why = position_in_range(self._scope_fit, _lo, _hi)
                self.course_params["lessons_total"] = _target
                self.course_params["lessons_per_module"] = max(
                    1, round(_target / max(1, self.course_params.get("modules", 6))))
                self._length_steer = _why
                logger.info(f"[VOLUME] lesson target {_target} of {_lo}-{_hi} — "
                            f"{_why}")
                if self.status_callback:
                    self.status_callback(f"STRUCT:LENGTH:{_target}:{_why}")

            # SOURCELESS SUBJECTS GET AN ITERATIVE SEARCH, NOT A SINGLE SWEEP.
            #
            # With a published syllabus the structure comes from the book and one
            # sweep is enough. Without one, a single pass of fixed queries is all
            # a subject like "Dungeon Mastering" ever gets — precisely the case
            # where the pipeline has least to work with.
            #
            # The model proposes the checklist and the queries, because knowing
            # what a course on a subject should cover is world knowledge. The
            # EXIT is measured coverage of that checklist, never the model's
            # satisfaction — see services/research/research_loop.py for why that
            # distinction is load-bearing.
            #
            # Keyed on GROUNDING, not on whether the sweep returned anything.
            # `found` used to mean "some book matched", so a sociology text
            # matching "Dungeon Mastering" counted as evidence and switched this
            # fallback off — the subject with the least real material got the
            # least research. A subject is sourceless when nothing SPEAKS FOR
            # IT, however many adjacent books exist.
            if not getattr(self, "_grounded", False):
                try:
                    self._research_loop_result = self._run_sourceless_research(
                        topic)
                except Exception as e:
                    logger.warning(f"[LOOP] sourceless research failed: {e}")
                    self._research_loop_result = None

            _msg = describe(self._scope_fit)
            if _msg:
                logger.warning(f"[SCOPE] {self._scope_fit['verdict']}: "
                               f"{self._scope_fit['reason']}")
                if self.status_callback:
                    self.status_callback(
                        f"CHECK:SCOPE:{self._scope_fit['verdict']}:{_msg}")
        except Exception as e:
            logger.debug(f"scope assessment failed: {e}")
            self._scope_fit = None

        # Keep the raw chapter list, not just the rendered text: coverage
        # backfill needs to compare titles against titles, and re-parsing prose
        # to recover a list we already had is how detail gets lost.
        #
        # Only the BEST-matching syllabus may drive backfill. Pooling every
        # matched source sounds more thorough and is not: for "Linear Algebra"
        # the brief also matched OpenStax *College Algebra*, and pooling pulled
        # Exponential and Logarithmic Functions, Analytic Geometry and
        # Probability into a linear algebra course. A weaker source is useful as
        # corroboration and dangerous as a checklist -- backfill treats its input
        # as "material this course MUST reach", which is a claim only the primary
        # source has earned.
        try:
            self._syllabus_outlines = [o for o in (brief.get("syllabi") or [])
                                       if o.get("chapters")]
            ranked = sorted(
                [o for o in (brief.get("syllabi") or []) if o.get("chapters")],
                key=lambda o: o.get("relevance", 0), reverse=True)
            if ranked:
                best = ranked[0]
                # A RELATIVE MARGIN NEEDS AN ABSOLUTE FLOOR UNDER IT.
                #
                # 0.75x-the-best is a sensible way to drop the weaker of several
                # good matches, and no protection at all when the best match is
                # itself wrong: a lone sociology text scored 0.75 of its own
                # score and became the checklist for a Dungeon Mastering course.
                # `ranked` is already filtered to grounding-quality sources by
                # _partition_brief; the floor here is belt-and-braces because
                # this list has been assembled from the raw brief before.
                margin = max(float(best.get("relevance", 0)) * 0.75,
                             GROUNDING_RELEVANCE)
                chosen = [o for o in ranked if float(o.get("relevance", 0)) >= margin]
                self._syllabus_chapters = [c for o in chosen
                                           for c in (o.get("chapters") or [])]
                if len(chosen) < len(ranked):
                    logger.info(
                        f"[BACKFILL] using {len(chosen)} of {len(ranked)} syllabi "
                        f"(best: {best.get('book')!r} @ {best.get('relevance')}); "
                        f"weaker sources excluded from the coverage checklist")
            else:
                self._syllabus_chapters = []
        except Exception as e:
            logger.debug(f"chapter retention failed: {e}")
            self._syllabus_chapters = []
        return format_brief(brief)

    def _widen_terms(self, tier, topic):
        """Search terms for one escalation tier. Never raises.

        The tiers widen in the order most likely to find a REAL syllabus first:
        the same subject under its formal name, then the discipline containing
        it, then the courses that teach it as a component. Late tiers are the
        ones that bring back plausible non-answers, which is why the ladder
        above them is bounded rather than exhaustive.

        `parent` reuses `_parent_subjects`, which already existed and was
        consulted exactly once — for the FIRST sweep. The whole point of the
        ladder is that one sweep is not a measurement of a subject.
        """
        name = (tier or {}).get("name")
        if name == "parent":
            try:
                subjects, _degraded = self._parent_subjects(topic)
                return list(subjects or [])[:3]
            except Exception as e:
                logger.debug(f"[DEEPEN] parent lookup failed: {e}")
                return []

        asks = {
            "adjacent": (f"What is '{topic}' called in a university catalogue "
                         f"or textbook? Give 1-3 alternative names for the SAME "
                         f"subject, comma separated, no explanation."),
            "applied": (f"Which established courses or subjects teach '{topic}' "
                        f"as one of their components? Name 1-3, comma "
                        f"separated, no explanation."),
        }
        prompt = asks.get(name)
        if not prompt:
            return []
        try:
            raw = llm_generate(
                prompt=prompt,
                sys_prompt="You name academic subjects. Answer tersely.",
                max_tokens=320)   # covers ~200 reasoning tokens; see the preflight probe
            return [t.strip() for t in (raw or "").split(",")
                    if t.strip() and len(t.strip()) < 60][:3]
        except Exception as e:
            logger.debug(f"[DEEPEN] {name} terms failed: {e}")
            return []

    _PARENT_CACHE = {}

    def _parent_subjects(self, topic):
        """(broader_subjects, degraded). Never raises.

        `degraded` means we could not determine the parent discipline — which is
        NOT the same as the topic having none, and the caller must not report
        "no evidence exists" on the strength of it.
        """
        key = (topic or "").strip().lower()
        if key in SkeletonBuilder._PARENT_CACHE:
            return SkeletonBuilder._PARENT_CACHE[key], False

        subjects = []
        try:
            raw = llm_generate(
                prompt=(f"What academic subject or discipline is '{topic}' part of? "
                        f"Answer with 1-3 subject names only, comma separated, "
                        f"no explanation. Example: Geometry, Trigonometry"),
                sys_prompt="You name academic disciplines. Answer tersely.",
                max_tokens=320,   # covers ~200 reasoning tokens; see the preflight probe
            )
            subjects = [b.strip() for b in (raw or "").split(",")
                        if b.strip() and len(b.strip()) < 60][:3]
        except Exception as e:
            logger.warning(f"[SKELETON] parent-subject lookup failed for {topic!r}: {e}")

        if not subjects:
            # No LLM needed: Wikipedia's own categories name the discipline, and
            # this path is cached and rate-limited like every other lookup.
            try:
                try:
                    from syllabus_sources import wikipedia_parent_subjects
                except ImportError:
                    from services.research.syllabus_sources import (
                        wikipedia_parent_subjects)
                subjects = wikipedia_parent_subjects(topic)[:3]
                if subjects:
                    logger.info(f"[SKELETON] parent subjects via Wikipedia "
                                f"categories: {subjects}")
            except Exception as e:
                logger.warning(f"[SKELETON] category fallback failed: {e}")

        if subjects:
            SkeletonBuilder._PARENT_CACHE[key] = subjects
            return subjects, False
        logger.warning(
            f"[SKELETON] could not determine a parent discipline for {topic!r}. "
            f"Grounding will be attempted on the narrow topic alone; a miss here "
            f"is DEGRADED, not evidence that no syllabus exists.")
        return [], True

    def _add_keyword_coverage(self, course_dict, judge_result):
        """Attach judge-free coverage to the criterion-6 record.

        Both numbers are kept and clearly labelled. A gate that silently swapped
        instruments would repeat the helgabench a0/a1 mistake of comparing across
        a changed judge without saying so.
        """
        judge_result = judge_result if isinstance(judge_result, dict) else {}
        chapters = [c for c in (getattr(self, "_syllabus_chapters", None) or [])
                    if isinstance(c, str) and c.strip()]
        if not chapters:
            judge_result["keyword_coverage"] = {
                "status": "no external syllabus — not measured"}
            try:
                from tools.coverage_check import sequencing_check
                judge_result["sequencing"] = sequencing_check(course_dict)
            except ImportError:
                pass
            return judge_result
        try:
            from tools.coverage_check import check_coverage, sequencing_check
        except ImportError:
            return judge_result

        # Recorded on every build, independent of whether a reference exists.
        # A course can cover its whole syllabus and still be unteachable: the
        # copy-spine run scored 100% coverage with modules running Addition...,
        # Cofactors..., Diagonal Matrix, Identity Matrix. Coverage cannot see
        # ordering, so the two questions need two instruments.
        judge_result["sequencing"] = sequencing_check(course_dict)
        if judge_result["sequencing"].get("alphabetical"):
            logger.warning("[SYLLABUS] modules are in ALPHABETICAL order — an "
                           "index, not a teaching sequence. Coverage is not a "
                           "quality signal for this course.")
            if self.status_callback:
                self.status_callback("CHECK:SEQUENCING:INDEX_ORDER")

        # Each real chapter is its own "area", identified by its distinctive
        # words — the same rule the backfill uses, so the gate and the fix agree
        # on what counts as covered.
        reference = {}
        for ch in chapters:
            words = [w for w in re.sub(r"[^a-z0-9 ]", " ", ch.lower()).split()
                     if len(w) > 3 and w not in (
                         "introduction", "overview", "review", "chapter", "part",
                         "basic", "basics", "advanced", "further")]
            if words:
                reference[ch] = words
        if not reference:
            return judge_result

        kw = check_coverage(course_dict, reference)
        judge_result["keyword_coverage"] = {
            "coverage_pct": kw.get("coverage_pct"),
            "covered": kw.get("areas_covered"),
            "checked": kw.get("areas_checked"),
            "missing": (kw.get("missing") or [])[:12],
            "instrument": "keyword (no model)",
            "authoritative": True,
        }
        judge_result["judge_coverage_pct"] = judge_result.get("coverage_pct")
        judge_result["coverage_pct"] = kw.get("coverage_pct")
        judge_result["coverage_source"] = "keyword"
        # Internal coherence runs in BOTH configurations, and is the replacement
        # for the source criteria when there is no textbook. N/A must not mean
        # easier, or "no source" becomes the way to pass — so a sourceless course
        # still has to answer the strongest question available without an
        # external reference: is it coherent on its own terms?
        try:
            from services.core.coherence import check_coherence, gate_summary
        except ImportError:
            from coherence import check_coherence, gate_summary
        _coh = check_coherence(course_dict)
        judge_result["internal_coherence"] = _coh
        _titles = self._flag_generic_titles(course_dict)
        if _titles:
            judge_result["titles"] = _titles
        if _coh.get("verdict") == "INCOHERENT":
            logger.warning(
                f"[COHERENCE] {_coh['forward_references']} forward reference(s) "
                f"in {_coh['concepts']} concepts — the course uses ideas before "
                f"it teaches them; e.g. "
                + "; ".join(f"{e['concept'][:40]!r} needs {e['term']!r}"
                            for e in _coh.get("examples", [])[:2]))
            if self.status_callback:
                self.status_callback(f"CHECK:COHERENCE:INCOHERENT:"
                                     f"{_coh['forward_references']}")

        _has_source = bool(getattr(self, "_syllabus_chapters", None))
        judge_result["gate_configuration"] = gate_summary({
            "internal_coherence": _coh.get("verdict") == "ok",
            "sequencing": (judge_result["sequencing"] or {}).get("verdict") == "ok",
        }, has_source=_has_source)

        logger.info(f"[SYLLABUS] keyword coverage {kw.get('coverage_pct')}% "
                    f"({kw.get('areas_covered')}/{kw.get('areas_checked')} chapters); "
                    f"judge said {judge_result.get('judge_coverage_pct')}%")
        if self.status_callback:
            self.status_callback(
                f"CHECK:COVERAGE:{kw.get('coverage_pct')}")
        return judge_result

    def _spine_from_syllabus(self, topic, target_modules):
        """Module list copied from a real textbook, or None to generate instead.

        Deliberately conservative. Copying the WRONG book's structure is worse
        than inventing one, so this only fires when the evidence is strong:

          * the matched book is about this exact subject (relevance gate), and
          * it has enough chapters to fill the course without padding, and
          * copy-spine has not been disabled

        Everything else falls through to generation, which is the existing,
        tested path. Failing toward the slower, safer route is the right default
        for a step this consequential.
        """
        if os.getenv("HELGA_COPY_SPINE", "1").lower() in ("0", "false", "no"):
            return None
        # No early return on an empty list: "research found nothing" is exactly
        # when a curated spine is most useful, and returning here skipped it.
        outlines = getattr(self, "_syllabus_outlines", None) or []

        # PREFER A SEQUENCED SOURCE OVER A HIGHER-SCORING INDEX.
        #
        # Relevance measures whether a book is about the subject; it says
        # nothing about whether its chapter list is in teaching order. The
        # Wikibooks Linear Algebra listing scores highest AND is alphabetical,
        # so picking by relevance alone selected an index and produced a course
        # whose modules ran Addition..., Cofactors..., Diagonal Matrix. A
        # slightly less-relevant book that is actually sequenced is the better
        # spine, because ordering is the part we cannot reconstruct.
        ordered = [o for o in outlines
                   if not _looks_alphabetical(o.get("chapters") or [])]
        if len(ordered) < len(outlines):
            dropped = [o.get("book") for o in outlines if o not in ordered]
            logger.info(f"[SPINE] ignoring alphabetical listing(s) {dropped} — "
                        f"an index has coverage but no teaching order")

        # A research source qualifies only if it is BOTH sequenced and actually
        # about this subject. The bar is the shared GROUNDING_RELEVANCE — this
        # check used to hold the only copy of it, which is precisely why every
        # other consumer went unprotected.
        best = max(ordered, key=lambda o: o.get("relevance", 0)) if ordered else None
        if best is not None and float(best.get("relevance", 0)) < GROUNDING_RELEVANCE:
            logger.info(f"[SPINE] best sequenced syllabus {best.get('book')!r} "
                        f"scores {best.get('relevance')} — not this subject")
            best = None

        if best is None:
            # CURATED FALLBACK. Both refusal paths land here: every listing was
            # an index, or the only sequenced book was about something else.
            # Linear algebra hits both — no OpenStax title, and the Wikibooks
            # entry is alphabetical — so research returns complete coverage with
            # no sequence. A chapter list transcribed from a published textbook
            # supplies exactly the part an index cannot.
            curated = _curated_spine(topic)
            if not curated:
                logger.info("[SPINE] no sequenced source for this subject and no "
                            "curated spine — generating instead")
                return None
            logger.info(f"[SPINE] using the curated spine for "
                        f"{curated['subject']!r} ({curated['source']})")
            best = {"book": curated["source"], "source": "curated",
                    "url": curated.get("source_url", ""),
                    "relevance": 10.0, "chapters": curated["chapters"],
                    # Section titles are where the specifics live. "Eigenvalues
                    # and Eigenvectors" as a module title says nothing about
                    # symmetric or positive-definite matrices; its SECTIONS name
                    # both, and that was the last area still missing coverage.
                    "sections": curated.get("sections") or {}}

        chapters = [c for c in (best.get("chapters") or [])
                    if isinstance(c, str) and c.strip()]

        # AN ALPHABETICAL LIST IS AN INDEX, NOT A SYLLABUS.
        #
        # Wikibooks stores a book as sub-pages and the API returns them sorted,
        # so "Linear Algebra" comes back as Addition..., Any Matrix..., Augmented
        # Matrices, Basis, ... That list has complete COVERAGE and no pedagogical
        # ORDER, and copying it produced a course whose modules were
        # alphabetically-ordered sub-topics — "Identity Matrix" as a module.
        #
        # It scored 100% on the keyword instrument, which is exactly the blind
        # spot that instrument documents about itself: presence is not sequence.
        # Ordering IS the pedagogy, so a source that has none cannot be a spine.
        # It remains perfectly good as a coverage CHECKLIST for backfill, which
        # is order-independent.

        if len(chapters) < target_modules:
            logger.info(f"[SPINE] {best.get('book')!r} has {len(chapters)} "
                        f"chapters for {target_modules} modules — too few to "
                        f"copy without padding")
            return None

        # SCOPE-ADAPT BY GROUPING, NOT SAMPLING.
        #
        # A 154-chapter book is not a 6-module course, so the list has to be
        # reduced — but taking every Nth chapter DISCARDS the ones in between.
        # Measured: Strang's 12 chapters sampled into 6 modules gave Introduction
        # to Vectors, Vector Spaces, Determinants, SVD, Complex Vectors,
        # Numerical LA — dropping Solving Linear Equations, ORTHOGONALITY,
        # Eigenvalues and Applications. Half the book, including the exact
        # cluster (least squares, projections, Gram-Schmidt) whose absence
        # started this whole line of work.
        #
        # Grouping consecutive chapters into each module keeps every chapter and
        # still preserves order, which is the whole point of copying a spine.
        groups, n = [], len(chapters)
        for i in range(target_modules):
            lo = (i * n) // target_modules
            hi = ((i + 1) * n) // target_modules
            group = [c.strip() for c in chapters[lo:hi] if c.strip()]
            if group:
                groups.append(group)

        def _name_group(group):
            """Name a module after everything in it, not just its first chapter.

            MEASURED: grouping Determinants + Eigenvalues named the module
            "Determinants", so "Eigenvalues and Diagonalization" appeared as a
            lesson under a heading that excluded it — and the model invented
            vague filler ("Core Determinant Mechanics", "Advanced Eigenvalue
            Topics") to bridge the gap between the title and the real content.
            A heading that hides half its material invites exactly that.
            """
            if len(group) == 1:
                return group[0]
            # Two chapters join; three or more would make an unreadable title, so
            # the first and last bracket the span the way a syllabus does.
            if len(group) == 2:
                a, b_ = group
                # "Determinants and Eigenvalues and Eigenvectors" reads as three
                # things joined badly. When either half already contains an
                # "and", a comma carries the join instead.
                sep = ", " if (" and " in a.lower() or " and " in b_.lower()) else " and "
                return f"{a}{sep}{b_}"
            return f"{group[0]} through {group[-1]}"

        picked, seen = [], set()
        for group in groups:
            title = _name_group(group)
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            picked.append((title, group))
        if len(picked) < max(2, target_modules // 2):
            return None

        self._spine_source = {"book": best.get("book"), "url": best.get("url"),
                              "source": best.get("source"),
                              "chapters_available": len(chapters),
                              "modules_taken": len(picked),
                              "chapters_covered": sum(len(g) for _, g in picked)}
        sections = best.get("sections") or {}
        out = []
        for title, group in picked:
            detail = []
            for chapter in group:
                subs = sections.get(chapter) or []
                detail.append(f"{chapter}" + (f" ({'; '.join(subs)})" if subs
                                              else ""))
            out.append({
                "title": title[:120],
                "scope": (f"Covers, as sequenced in {best.get('book')}: "
                          + " | ".join(detail))[:1200],
                "from_syllabus": True,
            })
        return out

    def _partition_brief(self, brief, topic):
        """Split evidence into what may ground the course and what may enrich it.

        Returns `(grounding_brief, supplementary)`. The grounding brief carries
        only syllabi at or above GROUNDING_RELEVANCE, and `found` is recomputed
        from what survives — so a brief holding nothing but adjacent material
        reports itself as sourceless, which is the honest answer and the one
        that routes the build to the research loop.

        Wikiversity `courses` are not filtered: they carry no relevance score
        because they are fetched by direct topic search rather than matched out
        of a catalogue, so the failure this guards against cannot arise there.
        Filtering them on a score they do not have would drop all of them.
        """
        if not brief:
            return brief, []

        syllabi = [o for o in (brief.get("syllabi") or []) if o.get("chapters")]
        primary, supplementary = [], []
        for o in syllabi:
            try:
                score = float(o.get("relevance", 0))
            except (TypeError, ValueError):
                score = 0.0
            (primary if score >= GROUNDING_RELEVANCE else supplementary).append(o)

        if supplementary:
            logger.warning(
                f"[EVIDENCE] {len(supplementary)} source(s) below the grounding "
                f"bar for {topic!r} — supplementary only: "
                + ", ".join(f"{o.get('book')!r} @ {o.get('relevance')}"
                            for o in supplementary[:3]))
            if self.status_callback:
                self.status_callback(
                    f"CHECK:EVIDENCE_SUPPLEMENTARY:{len(supplementary)}")

        out = dict(brief)
        out["syllabi"] = primary
        # `found` gated the research loop, so recomputing it is the whole point:
        # a wrong-subject match used to suppress the fallback that exists for
        # exactly that situation.
        out["found"] = bool(primary or brief.get("courses"))
        out["structural_sources"] = len(primary) + len(brief.get("courses") or [])

        if syllabi and not primary:
            logger.warning(
                f"[EVIDENCE] no source clears {GROUNDING_RELEVANCE} for "
                f"{topic!r} — treating as SOURCELESS despite "
                f"{len(syllabi)} match(es). A closer book is not the same "
                f"subject.")
            if self.status_callback:
                self.status_callback("CHECK:GROUNDING:NONE")

        self._grounded = bool(primary)
        # HELD FOR HYDRATION, NOT DISCARDED.
        #
        # These sources are excluded from every structural decision above and
        # kept for content, because the question changes between the two stages
        # and gets EASIER. At skeleton time the question is "does this book
        # speak for the subject?", which adjacent material always fails. At
        # hydration it is "does this passage serve THIS concept?", which the
        # same material can legitimately pass — a sociology chapter is wrong as
        # a checklist for a Dungeon Mastering course and may be exactly right
        # for one concept about political structures in worldbuilding.
        #
        # The stages also fail differently. A poisoned skeleton is inherited by
        # everything downstream and cannot be walked back; a bad hydration
        # source is one concept, attributable, and re-runnable.
        self._supplementary_sources = supplementary
        return out, supplementary

    def _run_sourceless_research(self, topic):
        """Iterative research for a subject with no published syllabus.

        Returns the loop's record, or None. Never raises into the build: a
        subject with no syllabus is already the degraded case, and failing the
        whole build because the fallback failed would be worse than proceeding
        with what the single sweep found.
        """
        try:
            from services.research.research_loop import (
                checklist_from, run_research_loop)
        except ImportError:
            from research_loop import checklist_from, run_research_loop

        # 1. The model proposes what a course on this must cover. World
        #    knowledge, which is what it is good at.
        proposed = []
        try:
            data = llm_generate_json(
                prompt=(f"A course on \"{topic}\" is being built and no published "
                        f"syllabus exists for it. List the 8-12 topics such a "
                        f"course must cover to be taken seriously by someone who "
                        f"knows the subject.\n\n"
                        f"Each entry must be a SHORT TOPIC NAME of 2-6 words — "
                        f"the kind of phrase that would appear in a syllabus. No "
                        f"colons, no explanations, no sentences. These are used "
                        f"as search terms, and a sentence cannot be searched "
                        f"for."),
                sys_prompt="You design curricula. Answer only with JSON.",
                schema={"type": "object", "properties": {"topics": {
                    "type": "array", "items": {"type": "string"}}},
                    "required": ["topics"]},
                max_tokens=500,
            )
            # SHAPE DRIFT, third occurrence. The schema asks for an object and
            # the model returns [{"topics": [...]}] — a list wrapping it. The
            # earlier two cost a whole build each ('str' has no attribute 'get'
            # in the lesson path, 'list' has no attribute 'get' in the subtree).
            # Unwrap rather than reject: the content was correct both times.
            if isinstance(data, list):
                data = next((d for d in data if isinstance(d, dict)), None)
            if isinstance(data, dict):
                topics = data.get("topics")
                # And the object itself may be a bare list of strings.
                if not topics and len(data) == 1:
                    only = next(iter(data.values()))
                    topics = only if isinstance(only, list) else None
                proposed = [t for t in (topics or [])
                            if isinstance(t, str) and t.strip()][:12]
            elif isinstance(data, list):
                proposed = [t for t in data if isinstance(t, str) and t.strip()][:12]
        except Exception as e:
            logger.warning(f"[LOOP] checklist proposal failed: {e}")

        checklist = checklist_from({"syllabi": []}, model_fallback=proposed)
        if not checklist["items"]:
            return None
        logger.info(f"[LOOP] {len(checklist['items'])} checklist items "
                    f"({checklist['source']}) for {topic!r}")

        # 2. Search each outstanding item through the research service. The
        #    queries are the model's items; the COVERAGE test is deterministic.
        import requests as _rq
        base = os.getenv("RESEARCH_URL", "http://helga-research:5006")

        def _search(query):
            try:
                r = _rq.post(f"{base}/api/research_concept",   # NOT /api/research/concept — 404
                             json={"title": query, "module_title": topic,
                                   "course_title": topic, "mastery": self.mastery},
                             timeout=90)
                if r.status_code != 200:
                    return []
                payload = r.json() or {}
                # A DEGRADED result is not a covered topic. Returning its sources
                # would let a throttled search retire a checklist item.
                if payload.get("search_degraded") and not payload.get("sources"):
                    return []
                return payload.get("sources") or []
            except Exception as e:
                logger.debug(f"[LOOP] search failed for {query!r}: {e}")
                return []

        result = run_research_loop(checklist["items"], search_fn=_search)
        result["checklist_source"] = checklist["source"]
        result["authoritative"] = checklist["authoritative"]
        if not checklist["authoritative"]:
            result["note"] = checklist.get("note", "")
        logger.info(f"[LOOP] {result.get('covered')}/{result.get('items')} "
                    f"covered in {result.get('rounds')} round(s); stopped "
                    f"because {result.get('stopped_because')}")
        if self.status_callback:
            self.status_callback(
                f"CHECK:RESEARCH_LOOP:{result.get('coverage_pct')}:"
                f"{result.get('stopped_because')}")
        return result

    def _flag_generic_titles(self, course_dict):
        """Record titles that name nothing. Measured in nearly every build.

        "Applications" appeared as a unit AND a lesson in the same course;
        "Advanced Topics" in another. A generic title is the model declining to
        decide what a section is about, and it is invisible to coverage — the
        course still "reaches" the material, in a box labelled nothing.

        Recorded rather than rewritten: renaming a section without regenerating
        its contents would paper over the decision the model declined to make.
        """
        try:
            from tools.structure_quality import check_titles
        except ImportError:
            return None
        result = check_titles(course_dict)
        if result.get("checked") and not result.get("specific"):
            logger.warning(
                f"[TITLES] {result['generic']} of {result['titles']} titles name "
                f"nothing ({result['rate']:.1%}): {result['examples'][:4]}")
            if self.status_callback:
                self.status_callback(
                    f"CHECK:TITLES:GENERIC:{result['generic']}")
        return result

    def _backfill_uncovered_chapters(self, course_dict, topic, cap=6):
        """Add lessons for real syllabus chapters the outline never reached.

        Same shape as the depth contract's named-element retry: name what is
        missing and regenerate against that name, rather than asking for
        "better" and hoping. Keyword matching decides coverage — no judge, so it
        cannot invent a hole or miss an obvious hit.
        """
        chapters = [c for c in (getattr(self, "_syllabus_chapters", None) or [])
                    if isinstance(c, str) and c.strip()]
        if not chapters:
            return
        try:
            from tools.coverage_check import course_title_blob, _normalise
        except ImportError:
            return

        blob, _ = course_title_blob(course_dict)
        norm = _normalise(blob)
        # A chapter counts as covered if its distinctive words appear. Stopwords
        # and one-word generics ("Introduction") would match everything, so they
        # are not evidence either way.
        missing = []
        for ch in chapters:
            key = _normalise(ch)
            if not key or len(key) < 4:
                continue
            words = [w for w in key.split() if len(w) > 3
                     and w not in ("introduction", "overview", "review", "chapter",
                                   "part", "basic", "basics", "advanced", "further")]
            if not words:
                continue
            if not any(w in norm for w in words):
                missing.append(ch)
        if not missing:
            logger.info("[BACKFILL] outline already covers every syllabus chapter")
            return

        # NO SHARE CAP HERE, DELIBERATELY.
        #
        # A proportional cap was tried and removed. Chapters reaching this point
        # have already cleared GROUNDING_RELEVANCE in _partition_brief, so they
        # come from a source that genuinely speaks for the subject — this is the
        # course's own material, not adjacent material leaking in. Capping it at
        # a share of the course fights the coverage goal directly: the case this
        # function exists for is MIT 18.06's orthogonality cluster, three
        # chapters of core linear algebra that the outline simply never selected.
        #
        # SUPPLEMENTARY_MAX_SHARE bounds weak sources, and it does that where
        # they are actually used — at hydration. Applying it twice would bound
        # the wrong thing in the wrong place.
        missing = missing[:cap]
        logger.warning(f"[BACKFILL] {len(missing)} syllabus chapter(s) uncovered: "
                       f"{missing}")
        if self.status_callback:
            self.status_callback(
                f"STRUCT:BACKFILL:{len(missing)} uncovered syllabus topic(s)")

        modules = course_dict.get("modules") or []
        if not modules:
            return
        target = modules[-1]
        units = target.get("units") or []
        if not units:
            units = [{"uid": f"unit_{uuid.uuid4().hex[:8]}", "title": "Further Topics",
                      "lessons": []}]
            target["units"] = units
        unit = units[-1]
        unit.setdefault("lessons", [])

        cpl = max(1, self.course_params.get("concepts_per_lesson", 3))
        added = 0
        for ch in missing:
            concepts = self._concepts_for_backfill(ch, topic, cpl)
            if not concepts:
                continue
            unit["lessons"].append({
                "uid": f"less_{uuid.uuid4().hex[:8]}",
                "title": ch.strip()[:120],
                "backfilled": True,
                "concepts": concepts,
            })
            added += 1
        if added:
            course_dict["backfilled_lessons"] = added
            logger.info(f"[BACKFILL] added {added} lesson(s) from the real syllabus")

    def _concepts_for_backfill(self, chapter, topic, count):
        """Concepts for one uncovered chapter. Returns [] rather than stubs."""
        try:
            data = llm_generate_json(
                prompt=(f"Course: {topic}\n"
                        f"A real published syllabus for this subject includes the "
                        f"topic \"{chapter}\", which the course currently omits.\n"
                        f"List exactly {count} specific teachable concepts that "
                        f"cover it, ordered as they should be taught."),
                sys_prompt="You design university curricula. Answer only with JSON.",
                schema={"type": "object", "properties": {"concepts": {
                    "type": "array", "items": {"type": "string"}}},
                    "required": ["concepts"]},
                max_tokens=400,
            )
        except Exception as e:
            logger.warning(f"[BACKFILL] concept generation failed for {chapter!r}: {e}")
            return []
        titles = []
        if isinstance(data, dict):
            titles = [t for t in (data.get("concepts") or [])
                      if isinstance(t, str) and t.strip()]
        return [{"uid": f"con_{uuid.uuid4().hex[:8]}", "title": t.strip()[:120],
                 "backfilled": True} for t in titles[:count]]

    def _record_syllabus_check(self, course_dict):
        """Attach the criterion-6 verdict to the course. Never raises.

        WHY THIS IS RECORDED AND NOT ENFORCED BY DEFAULT
        ------------------------------------------------
        Every other gate criterion is self-referential — an LLM judging output
        an LLM produced. This is the only one with external ground truth, which
        is exactly why it was worth wiring in, and it was the only one still
        run by hand.

        It is non-blocking by default because the instrument is a documented
        UNDERCOUNT: with a 9B judge it scores a complete outline at ~71%,
        omitting topics that are plainly present ("Potential outcomes" declared
        missing from a module literally titled "Potential Outcomes"). The
        verdict discriminates; the percentage is a lower bound. Failing a build
        on a lower bound would reject good courses, so the number is recorded
        and surfaced, and the operator opts in to blocking with
        HELGA_SYLLABUS_GATE=1 once a larger judge makes the number tight.

        A judge outage degrades to "not measured", never to a failed build.
        """
        if os.getenv("HELGA_SYLLABUS_CHECK", "1") == "0":
            return
        try:
            from tools.syllabus_check import check_structure, MIN_COVERAGE
        except Exception as e:
            logger.info(f"[SYLLABUS] check unavailable, skipping: {e}")
            return

        # Criterion 6 is the gate's ONLY external anchor — "an LLM judging output
        # produced by an LLM" is what every other criterion does. Task 0 found it
        # was being called with no reference while the fetched outline sat in
        # this same object, so it graded coverage from model memory and returned
        # 0%, self-referentially, which is the one thing it exists not to do.
        _ref = (getattr(self, "_evidence_block", "") or "").strip() or None
        if not _ref:
            logger.info("[SYLLABUS] no external reference available — criterion 6 "
                        "will run on model knowledge (WEAK)")
        result = check_structure(course_dict, reference_text=_ref)

        # MEASURED, TWICE: the judge behind criterion 6 reports topics as missing
        # that are literally module titles. On a generated Linear Algebra course
        # it returned 0% INADEQUATE while listing 'Vector Spaces', 'Basis and
        # Dimension', 'Linear Maps' and 'Determinants' as absent — all four were
        # module titles. That is the defect syllabus_check.py documents about
        # itself, reproduced with the reference correctly wired in, so it is the
        # judge and not the plumbing.
        #
        # So the authoritative coverage number comes from a matcher with no model
        # in it, computed against the SAME chapter list the backfill uses. The
        # judge's verdict is retained beside it rather than deleted: it is the
        # only thing that reads sequencing, and its prose critique has been
        # useful even when its number is not.
        result = self._add_keyword_coverage(course_dict, result)
        course_dict["syllabus_check"] = result

        if result.get("error"):
            logger.info(f"[SYLLABUS] not measured: {result['error']}")
            if self.status_callback:
                self.status_callback("CHECK:SYLLABUS:SKIP:not measured")
            return

        pct, verdict = result.get("coverage_pct"), result.get("verdict")
        missing = result.get("missing") or []
        logger.info(
            f"[SYLLABUS] {verdict} — {pct}% of {result.get('topics_checked')} "
            f"core topics covered (floor {MIN_COVERAGE}%); "
            f"grounding={result.get('grounding')}"
        )
        if missing:
            # Name them. "78% covered" is not actionable; a list of absent
            # topics is the only form of this result anyone can act on.
            logger.warning(f"[SYLLABUS] not covered: {', '.join(missing[:12])}")
        for problem in result.get("sequencing") or []:
            logger.warning(f"[SYLLABUS] sequencing: {problem}")

        if self.status_callback:
            self.status_callback(f"CHECK:SYLLABUS:{verdict}:{pct}%")

        if verdict == "INADEQUATE" and os.getenv("HELGA_SYLLABUS_GATE") == "1":
            raise CourseCreationError(
                f"Syllabus coverage {pct}% is below the {MIN_COVERAGE}% floor. "
                f"Missing: {', '.join(missing[:8]) or 'n/a'}"
            )

    def _drop_empty_units(self, course_dict):
        """Remove units that hold no lessons.

        An empty unit is strictly worse than no unit: it renders as a step in the
        path, a learner clicks it, and there is nothing there. Measured in a real
        build — a unit called "Session Zero" with zero lessons, which had passed
        every other structural check.

        These survive because the lesson minimum is advisory on the endpoint the
        builder posts to (`minItems` is stripped from `response_format`, and /v1
        ignores the `format` field carrying it), so nothing stops a unit coming
        back empty. Pruning is the honest fix: the alternative is inventing a
        lesson to fill a heading the model never had material for.
        """
        dropped = []
        for module in (course_dict.get("modules") or []):
            units = module.get("units") or []
            keep = []
            for unit in units:
                if unit.get("lessons"):
                    keep.append(unit)
                else:
                    dropped.append(unit.get("title", "?"))
            # Never empty a module entirely — a module with no units at all is a
            # worse defect than the one being fixed.
            module["units"] = keep or units
        # A module that ends with no lessons at all teaches nothing. Pruning
        # cannot fix it — dropping every unit leaves a module with no units — so
        # it is recorded as a defect rather than passed off as structure.
        hollow = [m.get("title", "?") for m in (course_dict.get("modules") or [])
                  if not any(u.get("lessons") for u in (m.get("units") or []))]
        if hollow:
            course_dict["hollow_modules"] = hollow
            logger.warning(f"[STRUCTURE] {len(hollow)} module(s) contain no "
                           f"lessons at all: {hollow[:3]} — these teach nothing")
            if self.status_callback:
                self.status_callback(f"STRUCT:HOLLOW_MODULES:{len(hollow)}")

        if dropped:
            logger.info(f"[STRUCTURE] dropped {len(dropped)} unit(s) with no "
                        f"lessons: {dropped[:4]}")
            if self.status_callback:
                self.status_callback(f"STRUCT:EMPTY_UNITS:{len(dropped)}")
        return len(dropped)

    def _merge_degenerate_lessons(self, course_dict, min_concepts=2):
        """Fold single-concept lessons into a sibling.

        A lesson holding one concept is scaffolding, not a lesson — it makes the
        learner click through a heading to reach a single card. The original
        reference course had SEVEN of twenty-one such lessons (33%), and the
        distribution is stochastic: consecutive builds of the same course
        produced 0% and then 33% again, so relying on the LLM to balance them
        does not hold.

        Deterministic and structural: merge into the smallest sibling in the
        same unit (keeping units balanced), or into the previous unit's last
        lesson if it is the only lesson in its own unit. A lesson with no
        siblings anywhere is left alone — a one-lesson course is legitimate.
        """
        merged = 0
        for module in course_dict.get("modules", []) or []:
            for unit in module.get("units", []) or []:
                lessons = unit.get("lessons") or []
                if len(lessons) < 2:
                    continue
                keep = []
                for lesson in lessons:
                    concepts = lesson.get("concepts") or []
                    if len(concepts) >= min_concepts or not keep:
                        keep.append(lesson)
                        continue
                    # Fold into whichever kept sibling is currently smallest,
                    # so merging does not create a new oversized lesson.
                    target = min(keep, key=lambda l: len(l.get("concepts") or []))
                    target.setdefault("concepts", []).extend(concepts)
                    merged += 1
                    logger.info(
                        f"  [STRUCTURE] merged 1-concept lesson "
                        f"{lesson.get('title')!r} into {target.get('title')!r}")
                unit["lessons"] = keep
        if merged and self.status_callback:
            self.status_callback(
                f"LOG: merged {merged} single-concept lesson(s) into siblings")
        return merged


    # ---- Consolidated subtree generation (one call per module) -------------
    #
    # The nested path below generates units, then lessons per unit, then
    # concepts per lesson: 1 + U + U*L calls per module (~28 for a 4-module
    # course). That decomposition is a SMALL-MODEL pattern. Each call is blind
    # to its siblings, so uniqueness has to be enforced from outside by
    # injecting a blacklist of already-used titles into every prompt.
    #
    # With the project model (34.7B MoE, 256K context) the whole module subtree
    # fits in one generation, which is both fewer round trips AND better
    # structure: the model chooses lesson and concept names while it can see the
    # unit layout, instead of being told after the fact what not to repeat.
    #
    # Per-MODULE rather than per-COURSE on purpose:
    #   * each module has its own Bloom target
    #   * cross-module coverage context still feeds the next module's prompt
    #   * progress stays granular (a module is a visible unit of work)
    #   * a failure costs one module's retry, not the whole course
    #
    # Every validation from the nested path is preserved: title normalisation,
    # duplicate rejection, fallback synthesis + counting, Bloom stamping, uid
    # generation and the STRUCT:* progress events.

    @staticmethod
    def subtree_schema(min_units=1, min_lessons=1, min_concepts=1):
        """SUBTREE_SCHEMA with minimum counts baked in.

        The static schema below has NO minItems, so constrained decoding
        cheerfully accepted one unit per module. Measured: modules asked for 3
        units and 9 lessons returned ~1.7 units and ~5 lessons, and firming the
        PROMPT from "about N units" to "this is NOT approximate" changed nothing
        — because the grammar the model was decoding against still permitted the
        short answer.

        A count the schema does not enforce is a count the prompt is merely
        requesting. This is the lever; the wording was not.

        Units may still differ in size — `min_lessons` is a floor per unit, not a
        quota — so the topical grouping the design calls for is preserved.
        """
        import copy
        schema = copy.deepcopy(SkeletonBuilder.SUBTREE_SCHEMA)
        units = schema["properties"]["units"]
        units["minItems"] = max(1, int(min_units))
        lessons = units["items"]["properties"]["lessons"]
        lessons["minItems"] = max(1, int(min_lessons))
        concepts = lessons["items"]["properties"]["concepts"]
        concepts["minItems"] = max(1, int(min_concepts))
        return schema

    SUBTREE_SCHEMA = {
        "type": "object",
        "properties": {
            "units": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "lessons": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "concepts": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "title": {"type": "string"},
                                                "objectives": {
                                                    "type": "array",
                                                    "items": {"type": "string"},
                                                },
                                            },
                                            "required": ["title", "objectives"],
                                        },
                                    },
                                },
                                "required": ["title", "concepts"],
                            },
                        },
                    },
                    "required": ["title", "lessons"],
                },
            }
        },
        "required": ["units"],
    }

    def _build_module_subtree_oneshot(
        self, m_ref, topic, mastery_label, base_units, base_lessons,
        base_concepts, module_bloom_level, module_specific_depth,
        prev_context_str, mastery_constraint,
    ):
        """Generate one module's whole units->lessons->concepts tree in a single
        LLM call. Returns the list of summary lines for cross-module context."""

        # The lesson RANGE for this module. Computed here because the prompt
        # below quotes it — a first attempt derived it further down, next to the
        # shortfall check that also uses it, and every build died on
        # UnboundLocalError. The unit tests passed: they inspect source text,
        # not execution order.
        _nominal = max(1, base_units * base_lessons)
        _lesson_lo = max(1, int(round(_nominal * (1 - LESSON_TOLERANCE))))
        _lesson_hi = max(_lesson_lo, int(round(_nominal * (1 + LESSON_TOLERANCE))))
        _ulo, _uhi = _shape_range("units_per_module", 2, 4)

        # WHEN EVIDENCE SAYS "GO LONG", THE FLOOR MOVES WITH IT.
        #
        # The range let a thin subject shrink, which is its purpose — but the
        # floor stayed at the range MINIMUM even for subjects whose evidence
        # supports three courses' worth of material. Measured across two
        # median-of-3 runs: at ~50 lessons coverage was 100/100/100, at ~39 it
        # was 90/100/90. The missing 10% is lessons the subject had material for
        # and the floor did not ask for.
        #
        # So a rich subject raises its own floor toward the target, while a thin
        # one keeps the low floor. Flexibility for the subject that needs it,
        # not for the one that does not.
        _steer_txt = (getattr(self, "_length_steer", "") or "")
        if "carries the longer" in _steer_txt:
            _lesson_lo = max(_lesson_lo, _nominal)
        # A range on its own is only PERMISSION to vary, and the model reads it
        # as licence to stop early: evidence set a target of 56 lessons and the
        # build came back with 39, because nothing in the prompt said which end
        # this subject belonged at. The research verdict is the missing sentence.
        _ev = (getattr(self, "_length_steer", "") or "").strip()
        if _ev.startswith("evidence supports ~") and "carries the longer" in _ev:
            _steer = (f"The research for this subject says: {_ev}. Use the UPPER "
                      f"end — there is more than enough material, and stopping "
                      f"short leaves the course thinner than the subject.")
        elif "short end" in _ev or "only" in _ev:
            _steer = (f"The research for this subject says: {_ev}. Use the LOWER "
                      f"end — padding a thin module is worse than a short one.")
        elif _ev:
            _steer = f"The research for this subject says: {_ev}. Aim mid-range."
        else:
            _steer = ("Use the upper end when the material genuinely fills it and "
                      "the lower end when it does not; padding is worse than "
                      "being short.")

        m_title = m_ref["title"]
        m_role = m_ref["role_desc"]
        module_dict = m_ref["dict"]
        positive_scope_str = ", ".join(m_ref["scope"])
        _bloom_label = BLOOM_LABELS.get(int(module_bloom_level), "Understand")

        # Real curriculum evidence, if phase-1 research found any. Framed as
        # material to draw ON, not a table of contents to copy: a copied TOC is
        # somebody else's course at somebody else's level. Truncated because
        # this rides in every module call.
        _ev = (getattr(self, "_evidence_block", "") or "").strip()
        _evidence = (
            f"### HOW REAL TEXTBOOKS ORGANISE THIS SUBJECT (evidence, not a template):\n"
            f"{_ev[:1800]}\n"
            f"Use it to decide WHAT a learner at this level must meet. Cover the "
            f"parts that belong to THIS module's scope; do not copy the ordering "
            f"and do not stray outside the module scope above.\n\n"
        ) if _ev else ""

        if self.status_callback:
            self.status_callback(f"LOG: Generating full subtree for module: {m_title}")

        prompt = (
            f"Course: {topic}\n"
            f"Module: {m_title}\n"
            f"Module scope: {positive_scope_str}\n"
            f"Mastery: {mastery_label} ({self.mastery}/5)\n"
            f"Bloom target: {module_bloom_level} ({_bloom_label})\n\n"
            f"### ALREADY COVERED EARLIER IN THIS COURSE (do not repeat or paraphrase):\n"
            f"{prev_context_str}\n\n"
            f"{_evidence}"
            f"Design this module's COMPLETE structure in one pass:\n"
            f"  - between {_lesson_lo} and {_lesson_hi} lessons in this "
            f"module. A lesson is one ~50-minute class session.\n"
            f"    {_steer}\n"
            f"  - group those lessons into {_ulo}-{_uhi} units BY TOPIC. A unit "
            f"is about a week of study, so a module of one unit is a week "
            f"wearing a module's name. Within that range units may differ in "
            f"size where the material warrants it.\n"
            f"  - exactly {base_concepts} concept(s) per lesson\n"
            f"  - exactly 2 learning objectives per concept, written for Bloom "
            f"{module_bloom_level} ({_bloom_label})\n\n"
            f"Because you are designing the whole module at once, you can see every "
            f"sibling you create. Use that: no two units, lessons or concepts anywhere "
            f"in this module may cover the same ground, and none may restate a title "
            f"listed as already covered above.\n\n"
            f"TITLES — specific, real terminology from {topic}:\n"
            f"  - units 2-5 words, lessons 3-8 words, concepts 2-6 words\n"
            f"  - a reader must know exactly what is taught from the title alone\n"
            f"  - do NOT use: Introduction to X, Overview of X, Understanding X, X Part N,\n"
            f"    Fundamentals, Principles, Framework, Dynamics, Axioms, Modelling\n"
            f"  - BANNED as a WHOLE title, at every level: Applications, "
            f"Advanced Topics, Core Concepts, Key Ideas, Further Study, Other "
            f"Topics, Additional Material, Review, Summary. These name nothing "
            f"— a reader learns only that the section exists. If a section is "
            f"about applications, say WHICH: \"Markov Chains in Population "
            f"Models\", not \"Applications\".\n"
            f"{mastery_constraint}\n"
            f"Return JSON only, shaped exactly:\n"
            f'{{"units": [{{"title": "...", "description": "...", "lessons": '
            f'[{{"title": "...", "concepts": [{{"title": "...", "objectives": ["...", "..."]}}]}}]}}]}}'
        )
        sys_prompt = (
            f"Expert {topic} curriculum designer building a {mastery_label}-level module. "
            f"You are producing a complete, internally-consistent module tree in one "
            f"response. Match complexity to mastery {self.mastery}/5 and Bloom "
            f"{module_bloom_level}. Use only real, established terminology from {topic} — "
            f"never invent terms. Prefer fewer items over padding with synonyms. "
            f"Return strict JSON matching the requested shape."
        )

        # Budget scales with the tree size; a truncated tree is the one real
        # failure mode of consolidating, so give it room.
        # The budget has to cover what the SCHEMA now REQUIRES, not what the
        # model might volunteer. Adding minItems raised the floor on output size
        # while this estimate stayed put, and the result was measured: the
        # one-shot returned empty for 5 of 6 modules, fell back to the chunked
        # path, and the course came out at 30 lessons against a 45-lesson
        # calendar. A constraint the budget cannot pay for is a constraint that
        # silently disables the path enforcing it.
        #
        # 160 tokens per leaf assumed a bare title. A concept carries a title
        # plus two Bloom-levelled objectives, and every level adds JSON
        # scaffolding, so the real cost is closer to 260.
        est_leaves = max(1, base_units) * max(1, base_lessons) * max(1, base_concepts)
        max_tokens = min(9000, 900 + est_leaves * 260)

        data = llm_generate_json(
            prompt,
            sys_prompt=sys_prompt,
            max_tokens=max_tokens,
            expected_type="dict",
            # The LESSON count is the calendar and is enforced; the UNIT count
            # is a topical grouping and is not. Forcing >= N units splits
            # material that naturally forms fewer groups, which is the opposite
            # of "units may differ in size where the material warrants it".
            #
            # The floor that matters is lessons-per-module, so it is applied
            # where it belongs: at least one unit, and enough lessons inside
            # whatever units the model chooses.
            # The floor is the range MINIMUM, not the target. Enforcing the
            # target would make the range decorative — the model could never
            # settle at the low end for a thin subject, which is the whole point
            # of having a range.
            # The per-unit floor has to survive the model choosing FEWER units
            # than asked, because the unit count is deliberately flexible.
            #
            # Measured: dividing the module floor by base_units (3) gave 2
            # lessons per unit, the model returned 2 units, and modules came back
            # with 4-5 lessons against a floor of 7 — so a 34-56 range produced
            # 29-31 lessons and coverage fell from 100% to 90% across three runs.
            # The floor was not binding on the shape the model actually returns.
            #
            # Dividing by the OBSERVED unit count instead makes it bind. Slight
            # overshoot is the safer error: at ~50 lessons coverage measured
            # 100/100/100, at ~30 it measured 90/90/90.
            # RANGES, FROM THE SHARED DEFINITION OF A SCHOOL-SHAPED COURSE.
            #
            # tools/structure_quality.SCHOOL_SHAPE holds the bands once, so the
            # builder asks for the same shape the checker grades. Two copies
            # would drift and the checker would grade against a standard the
            # builder never heard.
            #
            # Only the LOWER bound is enforced, because that is where collapse
            # lives: relaxing the unit floor to 1 produced units-per-module
            # [3, 3, 1, 1, 1, 1] — four of six modules a single week — with
            # coverage falling 100% -> 90% and balance from spread 0.04 to a 3.0
            # taper. Nothing caps the upper end, so a module with more material
            # is free to carry more.
            schema=self.subtree_schema(
                min_units=_shape_lo("units_per_module", 2),
                min_lessons=max(_shape_lo("lessons_per_unit", 2),
                                -(-_lesson_lo // 2)),
                min_concepts=base_concepts),
            progress_callback=self.status_callback,
        )
        # Tolerate shape drift. A model (or a differently-configured server) may
        # return the units ARRAY directly instead of the wrapper object; an
        # earlier version assumed a dict and raised AttributeError on a list,
        # which crashed the build instead of degrading to the chunked path.
        if isinstance(data, dict):
            units_data = data.get("units") or []
        elif isinstance(data, list):
            units_data = data
        else:
            units_data = []
        # Every element must be a mapping — a list of strings is not a subtree.
        units_data = [u for u in units_data if isinstance(u, dict)]

        # THE SCHEMA MINIMUM NEVER REACHED THE DECODER ON THIS ENDPOINT.
        #
        # minItems is stripped from `response_format` for /v1 compatibility, and
        # /v1 reads `response_format` while ignoring the `format` field that
        # still carries it. Verified directly: minItems binds on /api/chat — 8
        # units without it, 10 with minItems=5 — and is silently dropped on /v1,
        # which is what the builder posts to.
        #
        # Measured consequence: zero fallbacks, a shape asking for 3 units, and
        # NINE unit events across six modules. The floor was never enforced at
        # all, so every earlier attempt to tune it was a no-op — including
        # raising it from 1 to 2 and making base_units respect it.
        #
        # Enforced here instead, where it demonstrably can be: one correction
        # round naming the shortfall, the same self-correction the module
        # generation already uses.
        _min_units = _shape_lo("units_per_module", 2)
        if 0 < len(units_data) < _min_units:
            logger.info(f"  [ONESHOT] {m_title!r} returned {len(units_data)} "
                        f"unit(s), needs {_min_units} — one correction round")
            try:
                fix = llm_generate_json(
                    prompt=(prompt + f"\n\n### CORRECTION\n"
                            f"Your previous answer had {len(units_data)} unit(s). "
                            f"This module spans {_min_units}+ weeks and one unit "
                            f"is one week, so it needs at least {_min_units} "
                            f"units. Split the material BY TOPIC into "
                            f"{base_units} units — do not simply rename the one "
                            f"you had."),
                    sys_prompt=sys_prompt,
                    max_tokens=max_tokens,
                    schema=self.subtree_schema(
                        min_units=_min_units,
                        min_lessons=max(_shape_lo("lessons_per_unit", 2),
                                        -(-_lesson_lo // 2)),
                        min_concepts=base_concepts),
                    progress_callback=self.status_callback,
                )
                if isinstance(fix, list):
                    fix = next((f for f in fix if isinstance(f, dict)), None)
                retry_units = [u for u in ((fix or {}).get("units") or [])
                               if isinstance(u, dict)]
                if len(retry_units) > len(units_data):
                    logger.info(f"  [ONESHOT] correction gave "
                                f"{len(retry_units)} units")
                    units_data = retry_units
            except Exception as e:
                logger.warning(f"  [ONESHOT] correction round failed: {e}")

        # GENERIC TITLES GET THE SAME TREATMENT, FOR THE SAME REASON.
        #
        # "Advanced Topics", "Applications" and "Advanced Applications" survived
        # an explicit prompt ban listing those exact words. Prompt-only
        # enforcement has now failed four times in this file — on unit counts, on
        # lesson counts twice, and here — while a correction round naming the
        # specific offender has worked every time.
        #
        # A generic title is the model declining to decide what a section is
        # about, and it is invisible to coverage: the course still "reaches" the
        # material, in a box labelled nothing.
        # UNITS WITH NO LESSONS ARE THE SAME CLASS OF DEFECT AS TOO FEW UNITS,
        # and the same fix applies. The lesson minimum is advisory on this
        # endpoint, so a unit can come back with an empty lesson list — and a
        # module whose units are ALL empty produces a module that teaches
        # nothing. Measured: lessons-per-module [6, 0, 9, 6, 5, 6], where module
        # 2 had three units and zero lessons.
        #
        # Pruning alone cannot fix that: dropping every unit would leave a module
        # with no units, which is a worse defect. The material has to be asked
        # for again.
        _empty_units = [u.get("title", "?") for u in units_data
                        if not (u.get("lessons") or [])]
        if _empty_units:
            logger.info(f"  [ONESHOT] {m_title!r} has {len(_empty_units)} unit(s) "
                        f"with no lessons: {_empty_units[:3]} — one correction round")
            try:
                fix = llm_generate_json(
                    prompt=(prompt + f"\n\n### CORRECTION\n"
                            f"These units came back with NO lessons: "
                            f"{_empty_units[:5]}. Every unit is about a week of "
                            f"study and must contain lessons. Either give each "
                            f"one real lessons, or drop it and put its material "
                            f"in the units that remain — an empty unit is a step "
                            f"a learner clicks with nothing behind it."),
                    sys_prompt=sys_prompt,
                    max_tokens=max_tokens,
                    schema=self.subtree_schema(
                        min_units=_min_units,
                        min_lessons=max(_shape_lo("lessons_per_unit", 2),
                                        -(-_lesson_lo // 2)),
                        min_concepts=base_concepts),
                    progress_callback=self.status_callback,
                )
                if isinstance(fix, list):
                    fix = next((f for f in fix if isinstance(f, dict)), None)
                retry_units = [u for u in ((fix or {}).get("units") or [])
                               if isinstance(u, dict)]
                retry_empty = [u for u in retry_units if not (u.get("lessons") or [])]
                # Accept only if it has fewer empty units AND has not shrunk the
                # lesson count overall.
                _lessons = lambda us: sum(len(u.get("lessons") or []) for u in us)
                if (len(retry_empty) < len(_empty_units)
                        and _lessons(retry_units) >= _lessons(units_data)):
                    logger.info(f"  [ONESHOT] correction filled "
                                f"{len(_empty_units) - len(retry_empty)} empty unit(s)")
                    units_data = retry_units
            except Exception as e:
                logger.warning(f"  [ONESHOT] empty-unit correction failed: {e}")

        _generic = _generic_titles_in(units_data)
        if _generic:
            logger.info(f"  [ONESHOT] {m_title!r} has {len(_generic)} title(s) "
                        f"that name nothing: {_generic[:3]} — one correction round")
            try:
                fix = llm_generate_json(
                    prompt=(prompt + f"\n\n### CORRECTION\n"
                            f"These titles name nothing: {_generic[:6]}. A reader "
                            f"learns only that the section exists. Replace each "
                            f"with the actual subject matter — if a section is "
                            f"about applications, say WHICH application. Keep "
                            f"every other title and the structure unchanged."),
                    sys_prompt=sys_prompt,
                    max_tokens=max_tokens,
                    schema=self.subtree_schema(
                        min_units=_min_units,
                        min_lessons=max(_shape_lo("lessons_per_unit", 2),
                                        -(-_lesson_lo // 2)),
                        min_concepts=base_concepts),
                    progress_callback=self.status_callback,
                )
                if isinstance(fix, list):
                    fix = next((f for f in fix if isinstance(f, dict)), None)
                retry_units = [u for u in ((fix or {}).get("units") or [])
                               if isinstance(u, dict)]
                # Accept only if it is at least as complete AND genuinely less
                # generic — a correction that trades specificity for structure is
                # not an improvement.
                if (len(retry_units) >= len(units_data)
                        and len(_generic_titles_in(retry_units)) < len(_generic)):
                    logger.info(f"  [ONESHOT] correction removed "
                                f"{len(_generic) - len(_generic_titles_in(retry_units))} "
                                f"generic title(s)")
                    units_data = retry_units
            except Exception as e:
                logger.warning(f"  [ONESHOT] title correction failed: {e}")
        if not units_data:
            logger.warning(
                f"  [ONESHOT] Empty subtree for module '{m_title}' — "
                f"falling back to the chunked path."
            )
            return None   # caller falls back to the nested path

        m_summary_lines = [f"Module: {m_title} (Scope: {positive_scope_str})"]

        # A surplus was truncated and a SHORTFALL was silently accepted, so a
        # module that came back consolidated simply made the course shorter.
        # Measured: adding section detail to the module scope dropped a build
        # from 42 lessons to 30 against a 45-lesson calendar, entirely through
        # modules returning fewer units than asked.
        #
        # Units may differ in size — that is deliberate — but the LESSON total is
        # the calendar and does not bend, so a shortfall is recorded rather than
        # absorbed.
        _wanted = _lesson_lo
        _got = sum(len(u.get("lessons") or []) for u in units_data
                   if isinstance(u, dict))
        if _got and _got < _wanted * 0.7:
            self.lesson_shortfall = getattr(self, "lesson_shortfall", 0) + (
                _wanted - _got)
            logger.warning(
                f"  [VOLUME] module {m_title!r} returned {_got} lesson(s) "
                f"against {_wanted} — the course will be shorter than its "
                f"calendar unless this is made up elsewhere")
            if self.status_callback:
                self.status_callback(f"STRUCT:VOLUME_SHORT:{m_title}:{_got}/{_wanted}")

        units_data = units_data[:base_units]

        for u_idx, unit_data in enumerate(units_data, 1):
            self._checkpoint("unit")
            u_title = self._normalize_title(unit_data.get("title", ""))
            unit_used_fallback = False
            if not u_title or self._is_duplicate(u_title, course_topic=topic, level="unit"):
                u_title = f"{m_title} Part {u_idx}"
                unit_used_fallback = True
                self.fallback_count += 1
                logger.warning(
                    f"  [FALLBACK] unit {u_idx} in '{m_title}' — duplicate or empty."
                )
            self.used_titles.add(u_title)
            self.used_titles_by_level["unit"].add(u_title)
            unit_dict = {
                "uid": f"unit_{uuid.uuid4().hex[:8]}",
                "title": u_title,
                "ordinal": u_idx,
                "lessons": [],
            }
            if unit_used_fallback:
                unit_dict["llm_fallback"] = True
            module_dict["units"].append(unit_dict)
            m_summary_lines.append(
                f"  Unit: {u_title} — {unit_data.get('description', '')}"
            )
            if self.status_callback:
                self.status_callback(f"STRUCT:UNIT:{u_title}")

            for l_idx, lesson_data in enumerate(
                (unit_data.get("lessons") or [])[:base_lessons], 1
            ):
                l_title = self._normalize_title(lesson_data.get("title", ""))
                lesson_used_fallback = False
                if not l_title or self._is_duplicate(
                    l_title, course_topic=topic, level="lesson"
                ):
                    l_title = f"{u_title} Lesson {l_idx}"
                    lesson_used_fallback = True
                    self.fallback_count += 1
                    logger.warning(
                        f"  [FALLBACK] lesson {l_idx} in unit '{u_title}' — duplicate or empty."
                    )
                self.used_titles.add(l_title)
                self.used_titles_by_level["lesson"].add(l_title)
                lesson_dict = {
                    "uid": f"less_{uuid.uuid4().hex[:8]}",
                    "title": l_title,
                    "ordinal": l_idx,
                    "concepts": [],
                }
                if lesson_used_fallback:
                    lesson_dict["llm_fallback"] = True
                unit_dict["lessons"].append(lesson_dict)
                m_summary_lines.append(f"    Lesson: {l_title}")
                if self.status_callback:
                    self.status_callback(f"STRUCT:LESSON:{l_title}")

                concept_titles = []
                for c_idx, concept_data in enumerate(
                    (lesson_data.get("concepts") or [])[:base_concepts], 1
                ):
                    c_title = self._normalize_title(concept_data.get("title", ""))
                    if not c_title or self._is_duplicate(
                        c_title, course_topic=topic, level="concept"
                    ):
                        continue
                    self.used_titles.add(c_title)
                    self.used_titles_by_level["concept"].add(c_title)
                    concept_titles.append(c_title)
                    c_uid = f"con_{uuid.uuid4().hex[:8]}"
                    lesson_dict["concepts"].append({
                        "uid": c_uid,
                        "title": c_title,
                        "learning_objectives": concept_data.get(
                            "objectives", [f"Understand {c_title}"]
                        ),
                        "complexity_role": m_role,
                        "depth_level": module_specific_depth,
                        "bloom_level": int(module_bloom_level),
                        "ordinal": c_idx,
                    })
                    if self.status_callback:
                        self.status_callback(f"STRUCT:CONCEPT:{c_uid}:{c_title}")

                # Same black-hole guard as the chunked path: never ship an
                # empty lesson.
                if not lesson_dict["concepts"]:
                    for pad_idx in range(1, base_concepts + 1):
                        pad_title = f"{l_title} Part {pad_idx}"
                        self.used_titles.add(pad_title)
                        self.used_titles_by_level["concept"].add(pad_title)
                        concept_titles.append(pad_title)
                        lesson_dict["concepts"].append({
                            "uid": f"con_{uuid.uuid4().hex[:8]}",
                            "title": pad_title,
                            "learning_objectives": [f"Understand {l_title}"],
                            "complexity_role": m_role,
                            "depth_level": module_specific_depth,
                            "bloom_level": int(module_bloom_level),
                            "ordinal": pad_idx,
                            "llm_fallback": True,
                        })
                        self.fallback_count += 1
                    logger.warning(
                        f"  [FALLBACK] Lesson '{l_title}' had 0 concepts after dedup — "
                        f"backfilled with {base_concepts} Part-N stubs."
                    )

                if concept_titles:
                    m_summary_lines.append(
                        f"      Concepts: {', '.join(concept_titles)}"
                    )

        return m_summary_lines

    def _build_substructures_progressive(
        self, module_refs, max_depth, topic, all_modules_metadata,
        module_bloom_targets=None,
    ):
        """
        Chunked hierarchical generation for reliable structure building.

        Strategy:
        1. Generate Units first (small, fast call)
        2. Generate Lessons per Unit (individual calls)
        3. Generate Concepts per Lesson (individual calls)

        This provides frequent progress updates and keeps each LLM call focused.
        """
        # QB-1 FIX: DEPTH_PROFILES base counts come from the legacy single-depth
        # system and explode when scope=5 is used with low mastery (e.g. 4×3×2 =
        # 24 concepts per module where mastery=1 only wants 3). Clamp base_units
        # and base_lessons so (units × lessons × 2) never exceeds the mastery
        # target — we still want a hierarchy, but only as deep as the concept
        # budget allows.
        target_concepts_per_module = self.course_params.get(
            "concepts_per_module",
            self.depth_profile.get("concepts_per_lesson", 2)
            * self.depth_profile.get("lessons_per_unit", 1)
            * self.depth_profile.get("units_per_module", 1),
        )

        # The lesson budget comes from the calendar and is not negotiable; the
        # UNIT shape is not fixed, because a unit is a topical grouping whose
        # size should follow the material — some units are one week, some three.
        # So we pass a lesson budget and a unit RANGE, and let the module's
        # content decide how those lessons group.
        lessons_per_module = self.course_params.get("lessons_per_module")
        if not lessons_per_module:
            lessons_per_module = max(
                1, round(WEEKS_PER_TERM * SESSIONS_PER_WEEK
                         / max(1, self.course_params.get("modules", 6))))
        base_concepts = max(1, self.course_params.get("concepts_per_lesson", 3))
        # 2-4 units per module is the range real syllabi occupy; within it the
        # model groups by topic rather than by arithmetic.
        # THE TARGET MUST RESPECT THE FLOOR, OR TRUNCATION UNDOES IT.
        #
        # The schema asked for >= 2 units and the model supplied them, but
        # `units_data[:base_units]` then cut them back — because base_units is
        # computed from the lesson budget and can round to 1. Measured:
        # units-per-module [3, 2, 1, 2, 2, 1] with zero one-shot fallbacks, so
        # the modules that collapsed were TRUNCATED, not under-generated.
        #
        # A floor enforced at generation and discarded at assembly is not a
        # floor.
        _u_lo, _u_hi = _shape_range("units_per_module", 2, 4)
        base_units = max(_u_lo, min(_u_hi, round(lessons_per_module / 3)))
        base_lessons = max(1, round(lessons_per_module / base_units))
        logger.info(
            f"Substructure shape: units={base_units}, lessons_per_unit={base_lessons}, "
            f"concepts_per_lesson={base_concepts} "
            f"(target concepts/module={target_concepts_per_module})"
        )
        mastery_constraint = self.course_params.get("mastery_writing", "")
        mastery_label = self.course_params.get("mastery_label", "Understanding")

        # Track full hierarchy for "mergy context" to avoid repetition
        planned_hierarchy_summary = []

        for m_idx, m_ref in enumerate(module_refs):
            m_title = m_ref["title"]
            m_role = m_ref["role_desc"]
            m_scope = m_ref["scope"]
            module_dict = m_ref["dict"]
            module_specific_depth = self.module_depths.get(m_title, max_depth)
            # Per-module Bloom target from the progression schedule. Default to
            # the mastery ceiling so any concept-level consumer (FSM, audit)
            # has a valid integer in [1, 6].
            if module_bloom_targets and m_idx < len(module_bloom_targets):
                module_bloom_level = module_bloom_targets[m_idx][0]
            else:
                module_bloom_level = self.course_params.get("bloom_ceiling", 2)

            constraints = self._get_domain_constraints(topic)
            # scope is a LIST from the LLM path but PROSE from the syllabus
            # spine ("Covers, as sequenced in <book>: ..."), and joining a str
            # joins its CHARACTERS — a 1200-char spine scope became ~3.6KB of
            # comma-spaced letters injected into the structure prompt, on
            # exactly the path with the best source material.
            positive_scope_str = (", ".join(m_scope)
                                  if isinstance(m_scope, (list, tuple))
                                  else str(m_scope))

            # Build previous coverage string for prompt grounding
            prev_context_str = (
                "\n".join(planned_hierarchy_summary[-10:])
                if planned_hierarchy_summary
                else "No modules covered yet."
            )

            # CONSOLIDATED PATH (default): one call for this module's whole
            # subtree instead of 1 + U + U*L calls. Falls through to the chunked
            # path below if it returns nothing, so a bad generation degrades to
            # the old behaviour rather than to an empty module.
            if os.getenv("HELGA_ONESHOT_SUBTREE", "1").lower() in ("1", "true", "yes"):
                _lines = self._build_module_subtree_oneshot(
                    m_ref, topic, mastery_label, base_units, base_lessons,
                    base_concepts, module_bloom_level, module_specific_depth,
                    prev_context_str, mastery_constraint,
                )
                if _lines:
                    planned_hierarchy_summary.append("\n".join(_lines))
                    continue
                # reset any partial subtree before retrying the chunked way
                module_dict["units"] = []

            # Bloom-progressive level constraints per module
            ordinal = module_dict.get("ordinal", 1)
            total_modules = len(module_refs)
            bloom_labels_map = {1: "Remember", 2: "Understand", 3: "Apply", 4: "Analyze", 5: "Evaluate", 6: "Create"}
            bloom_floor = self.course_params.get("bloom_floor", 1)
            bloom_ceiling = self.course_params.get("bloom_ceiling", 5)
            module_bloom = progressive_bloom(ordinal - 1, total_modules, bloom_floor, bloom_ceiling)
            module_bloom_label = bloom_labels_map.get(module_bloom, "Apply")

            bloom_descriptors = {
                1: "Use plain language. Define every term. Concrete everyday examples only. A complete beginner must understand everything.",
                2: "Explain concepts and relationships. 'Why' and 'how' questions. The student knows basic vocabulary now.",
                3: "Show how to apply methods. Step-by-step procedures. The student can now use what they've learned.",
                4: "Compare, contrast, and analyze. Formal methods and trade-offs. The student has working knowledge.",
                5: "Evaluate approaches critically. Limitations, assumptions, edge cases. The student can judge quality.",
                6: "Synthesize and create. Original applications, design decisions, research directions.",
            }

            level_constraint = (
                f"BLOOM LEVEL FOR THIS MODULE: {module_bloom} ({module_bloom_label})\n"
                f"{bloom_descriptors.get(module_bloom, bloom_descriptors[3])}\n"
                f"This is module {ordinal} of {total_modules}. "
            )
            if ordinal == 1:
                level_constraint += (
                    f"This is the FIRST module — it must be the SIMPLEST. "
                    f"Cover what the field IS, why it matters, and basic vocabulary. "
                    f"A student with NO background in {topic} must be able to follow every concept."
                )
            elif ordinal == total_modules:
                level_constraint += (
                    f"This is the FINAL module — it should be the MOST ADVANCED. "
                    f"The student has built up through {ordinal-1} prior modules. "
                    f"Cover the most sophisticated techniques and applications."
                )
            else:
                level_constraint += (
                    f"The student has completed {ordinal-1} simpler module(s). "
                    f"This module should be MORE complex than the previous but LESS complex than the next."
                )

            # Re-emit STRUCT:MODULE to reset the UI container pointer for this module.
            # The initial emission (during module generation) sends all modules at once;
            # this re-emission ensures the UI nests units/lessons/concepts under the
            # correct module as substructures are generated sequentially.
            if self.status_callback:
                self.status_callback(f"STRUCT:MODULE:{m_title}")
                # The only signal that says how far through the skeleton we
                # are. creation_status pins progress_pct at 10 for this entire
                # phase, and the phase is the long one -- on this hardware a
                # six-module skeleton runs for hours while the toast reads
                # "Building... 10%" the whole time, indistinguishable from a
                # wedged build. Both numbers are already in scope here.
                self.status_callback(
                    f"STRUCT:MODULE_PROGRESS:{ordinal}:{total_modules}")
                self.status_callback(f"LOG: Generating Units for module: {m_title}")

            # STEP 1: Generate Units only (fast, small call)
            # Feed ALL used titles (not just 10) and the full hierarchy summary
            used_titles_str = (
                ", ".join(sorted(self.used_titles)) if self.used_titles else "None yet"
            )

            units_prompt = (
                f"Course Topic: {topic}\n"
                f"Current Module: {m_title} (Module {module_dict.get('ordinal', 1)} of {len(module_refs)})\n"
                f"Module Scope (STAY WITHIN THIS): {positive_scope_str}\n"
                f"Mastery Level: {mastery_label} ({self.mastery}/5)\n"
                f"COMPLEXITY: {mastery_constraint}\n\n"
                f"### [COURSE HIERARCHY SO FAR — DO NOT REPEAT ANY OF THIS]\n"
                f"{prev_context_str}\n\n"
                f"### ALREADY USED TITLES — DO NOT REUSE OR PARAPHRASE:\n"
                f"{used_titles_str}\n\n"
                f"### TASK: Generate exactly {base_units} Units for this module.\n"
                f"### CONSTRAINTS:\n"
                f"- {level_constraint}\n"
                f"- SCOPE BOUNDARY: Every unit MUST fall within this module's scope: {positive_scope_str}. Do NOT cover topics from other modules.\n"
                f"- SCAFFOLDING: Each unit must introduce a DISTINCT sub-area. No two units should overlap in subject matter.\n"
                f"- TITLE QUALITY: 2-5 words naming SPECIFIC, REAL technical sub-areas of {topic}. "
                f"Use established terminology from the field.\n"
                f"- BANNED WORDS in titles: Axioms, Dynamics, Principles, Fundamentals, Modelling, Framework, Interactions, Overview, Introduction.\n\n"
                f"Return JSON array: [{{'title': 'Unit Name', 'description': 'One sentence defining this unit\\'s UNIQUE scope boundary'}}]"
            )

            sys_msg = (
                f"Expert curriculum designer for a {mastery_label}-level course on {topic}. "
                f"Match unit complexity to mastery level {self.mastery}/5. "
                f"Return strict JSON array only. Use real terminology from {topic} — never invent terms."
            )
            units_data = llm_generate_json(
                units_prompt,
                sys_prompt=sys_msg,
                max_tokens=1200,
                expected_type="list",
                progress_callback=self.status_callback,
            )

            if not units_data:
                units_data = [
                    {
                        "title": f"{m_title} Fundamentals",
                        "description": f"Core concepts of {m_title}",
                        "llm_fallback": True,
                    }
                ]
                self.fallback_count += 1
                logger.warning(f"  [FALLBACK] Using fallback unit title for module '{m_title}' — LLM returned empty.")

            units_data = units_data[:base_units]  # Enforce limit

            # Record this module's branch for future context
            m_summary_lines = [f"Module: {m_title} (Scope: {positive_scope_str})"]

            # Build unit titles list for cross-unit dedup in lesson prompts
            unit_titles_in_module = [
                self._normalize_title(u.get("title", "")) for u in units_data
            ]

            # STEP 2: Generate Lessons for each Unit
            lessons_generated_in_module = []  # Track all lesson titles within this module

            for u_idx, unit_data in enumerate(units_data, 1):
                u_title = self._normalize_title(unit_data.get("title", ""))
                u_description = unit_data.get("description", "")
                unit_used_fallback = unit_data.get("llm_fallback", False)
                if not u_title or self._is_duplicate(u_title, course_topic=topic, level="unit"):
                    u_title = f"{m_title} Part {u_idx}"
                    if not unit_used_fallback:
                        unit_used_fallback = True
                        self.fallback_count += 1
                        logger.warning(f"  [FALLBACK] Using fallback title for unit {u_idx} in module '{m_title}' — duplicate or empty.")

                self.used_titles.add(u_title)
                self.used_titles_by_level["unit"].add(u_title)
                u_uid = f"unit_{uuid.uuid4().hex[:8]}"
                unit_dict = {
                    "uid": u_uid,
                    "title": u_title,
                    "ordinal": u_idx,
                    "lessons": [],
                }
                if unit_used_fallback:
                    unit_dict["llm_fallback"] = True
                module_dict["units"].append(unit_dict)
                m_summary_lines.append(f"  Unit: {u_title} — {u_description}")

                if self.status_callback:
                    self.status_callback(f"STRUCT:UNIT:{u_title}")

                # Build sibling context: what OTHER units in this module cover
                sibling_units = [
                    t
                    for t in unit_titles_in_module
                    if t != self._normalize_title(u_title)
                ]
                sibling_units_str = (
                    ", ".join(sibling_units) if sibling_units else "None"
                )

                # Build already-used lessons context
                prev_lessons_str = (
                    ", ".join(lessons_generated_in_module[-15:])
                    if lessons_generated_in_module
                    else "None yet"
                )

                # Generate lessons for this unit
                if self.status_callback:
                    self.status_callback(f"LOG: Generating lessons for unit: {u_title}")
                lessons_prompt = (
                    f"Course: {topic}\n"
                    f"Module: {m_title} (Scope: {positive_scope_str})\n"
                    f"Unit: {u_title}\n"
                    f"Unit Scope: {u_description}\n"
                    f"Bloom Level: {mastery_label}\n\n"
                    f"### SIBLING UNITS (lessons must NOT overlap with these units' topics):\n{sibling_units_str}\n\n"
                    f"### ALL LESSONS ALREADY IN THIS COURSE (do NOT repeat or paraphrase):\n{prev_lessons_str}\n\n"
                    f"Generate exactly {base_lessons} lessons for '{u_title}'.\n"
                    f"Each lesson must cover a GENUINELY DIFFERENT aspect — different event, period, method, or perspective.\n"
                    f"TITLE RULES:\n"
                    f"- 3-8 words. Specific enough that a reader knows what the lesson covers.\n"
                    f"- BANNED: 'Introduction to X', 'Overview of X', 'Understanding X', 'X Part 2'.\n"
                    f"- A student should NOT be able to confuse one lesson for another.\n\n"
                    f"Return JSON array: [{{'title': 'Specific Lesson Name'}}]"
                )

                lessons_sys = (
                    f"Expert curriculum designer for a {mastery_label}-level course on {topic}. "
                    f"Match complexity to mastery level {self.mastery}/5. "
                    f"Return strict JSON array only."
                )
                lessons_data = llm_generate_json(
                    lessons_prompt,
                    sys_prompt=lessons_sys,
                    max_tokens=1200,
                    expected_type="list",
                    progress_callback=self.status_callback,
                )

                if not lessons_data:
                    lessons_data = [{"title": f"{u_title} Overview", "llm_fallback": True}]
                    self.fallback_count += 1
                    logger.warning(f"  [FALLBACK] Using fallback lesson title for unit '{u_title}' — LLM returned empty.")

                # SHAPE DRIFT KILLS A WHOLE BUILD. Measured: 1 run in 3 died
                # here with "'str' object has no attribute 'get'" because the
                # model returned ["Lesson one", "Lesson two"] where a list of
                # objects was asked for. Every other stage in this file already
                # tolerates that -- the one-shot subtree does it for units -- and
                # this one did not, so a stylistic choice by the model destroyed
                # a 20-minute build.
                #
                # Coerce rather than reject: a bare string IS the title, which is
                # the only field required here.
                lessons_data = [
                    ({"title": item} if isinstance(item, str) else item)
                    for item in (lessons_data or [])
                    if isinstance(item, (str, dict))
                ]
                lessons_data = lessons_data[:base_lessons]

                # Track all concepts within this unit for cross-lesson dedup
                concepts_generated_in_unit = []

                # STEP 3: Generate Concepts for each Lesson
                for l_idx, lesson_data in enumerate(lessons_data, 1):
                    l_title = self._normalize_title(lesson_data.get("title", ""))
                    lesson_used_fallback = lesson_data.get("llm_fallback", False)
                    if not l_title or self._is_duplicate(l_title, course_topic=topic, level="lesson"):
                        l_title = f"{u_title} Lesson {l_idx}"
                        if not lesson_used_fallback:
                            lesson_used_fallback = True
                            self.fallback_count += 1
                            logger.warning(f"  [FALLBACK] Using fallback title for lesson {l_idx} in unit '{u_title}' — duplicate or empty.")

                    self.used_titles.add(l_title)
                    self.used_titles_by_level["lesson"].add(l_title)
                    lessons_generated_in_module.append(l_title)
                    l_uid = f"less_{uuid.uuid4().hex[:8]}"
                    lesson_dict = {
                        "uid": l_uid,
                        "title": l_title,
                        "ordinal": l_idx,
                        "concepts": [],
                    }
                    if lesson_used_fallback:
                        lesson_dict["llm_fallback"] = True
                    unit_dict["lessons"].append(lesson_dict)
                    m_summary_lines.append(f"    Lesson: {l_title}")

                    if self.status_callback:
                        self.status_callback(f"STRUCT:LESSON:{l_title}")

                    # Build sibling lessons context
                    sibling_lessons = [
                        self._normalize_title(ld.get("title", ""))
                        for ld in lessons_data
                        if self._normalize_title(ld.get("title", "")) != l_title
                    ]
                    sibling_lessons_str = (
                        ", ".join(sibling_lessons) if sibling_lessons else "None"
                    )

                    # Build already-generated concepts context (cross-lesson within unit)
                    prev_concepts_str = (
                        ", ".join(concepts_generated_in_unit[-20:])
                        if concepts_generated_in_unit
                        else "None yet"
                    )

                    # Generate concepts for this lesson
                    if self.status_callback:
                        self.status_callback(
                            f"LOG: Generating concepts for lesson: {l_title}"
                        )
                    # Compute this module's bloom level for concept complexity
                    _bloom_labels = {1: "Remember", 2: "Understand", 3: "Apply", 4: "Analyze", 5: "Evaluate", 6: "Create"}
                    _bf = self.course_params.get("bloom_floor", 1)
                    _bc = self.course_params.get("bloom_ceiling", 5)
                    _prog = (module_dict.get("ordinal", 1) - 1) / max(len(module_refs) - 1, 1)
                    _mod_bloom = max(_bf, min(_bc, _bf + round(_prog * (_bc - _bf))))
                    _mod_bloom_label = _bloom_labels.get(_mod_bloom, "Apply")

                    # Bloom-appropriate concept naming guidance
                    naming_guide = {
                        1: (f"- Use SIMPLE, DESCRIPTIVE names a beginner would understand.\n"
                            f"- Good: 'What Causes What', 'Confusing Correlation with Causation', 'Controlled Experiments'\n"
                            f"- Bad: 'D-Separation', 'SUTVA', 'Propensity Score' (too technical for this level)\n"
                            f"- Each concept should be explainable in one simple sentence."),
                        2: (f"- Use clear names that introduce key ideas. Technical terms OK if standard vocabulary.\n"
                            f"- Good: 'Confounding Variables', 'Randomized Experiments', 'Correlation vs Causation'\n"
                            f"- Bad: 'Instrumental Variable Estimation', 'G-computation Algorithm' (too advanced)"),
                        3: (f"- Use proper technical names from the field.\n"
                            f"- Good: 'Propensity Score Matching', 'Randomized Controlled Trials', 'Back-Door Criterion'\n"
                            f"- Each concept should be a real, named method or framework."),
                        4: (f"- Use precise technical names: specific methods, theorems, estimators.\n"
                            f"- Good: 'SUTVA', 'D-Separation', 'Inverse Probability Weighting', 'Structural Causal Models'"),
                        5: (f"- Use research-level terminology: named theorems, algorithms, estimation procedures.\n"
                            f"- Good: 'Do-Calculus Rules', 'G-estimation', 'Structural Nested Mean Models'"),
                    }.get(_mod_bloom, "- Use appropriate technical names from the field.")

                    concepts_prompt = (
                        f"Course: {topic}\n"
                        f"Module: {m_title} (Module {module_dict.get('ordinal',1)}/{len(module_refs)})\n"
                        f"Unit: {u_title} | Lesson: {l_title}\n"
                        f"Bloom Level: {_mod_bloom} ({_mod_bloom_label})\n\n"
                        f"### SIBLING LESSONS (concepts must NOT overlap with these):\n{sibling_lessons_str}\n\n"
                        f"### ALL CONCEPTS ALREADY IN THIS COURSE (do NOT repeat, rephrase, or paraphrase ANY):\n{prev_concepts_str}\n\n"
                        f"Generate exactly {base_concepts} concepts for '{l_title}'.\n\n"
                        f"ZERO TOLERANCE FOR REDUNDANCY:\n"
                        f"- Before writing each concept, ask: 'Does this teach something GENUINELY NEW that no existing concept covers?'\n"
                        f"- Two concepts are duplicates if a student who mastered one would already know the other.\n"
                        f"- Each concept must cover a DIFFERENT aspect: different event, different person, different mechanism, different time period, or different technique.\n"
                        f"- NEVER generate two concepts about the same noun (e.g., two concepts about 'land rights' or two about 'codification').\n\n"
                        f"NAMING (Bloom {_mod_bloom} — {_mod_bloom_label}):\n"
                        f"{naming_guide}\n"
                        f"- 2-6 words. Specific enough that a reader knows exactly what will be taught.\n\n"
                        f"Each concept needs 2 learning objectives for Bloom {_mod_bloom}.\n"
                        f"Return JSON: [{{'title': 'Concept Name', 'objectives': ['Learn X', 'Understand Y']}}]"
                    )

                    concepts_sys = (
                        f"Expert {topic} curriculum designer. "
                        f"Bloom level {_mod_bloom} ({_mod_bloom_label}). "
                        f"Every concept must be UNIQUE — if you can't think of {base_concepts} truly distinct concepts for this lesson, generate fewer rather than padding with synonyms. "
                        f"Return strict JSON array only."
                    )
                    concepts_data = llm_generate_json(
                        concepts_prompt,
                        sys_prompt=concepts_sys,
                        max_tokens=1200,
                        expected_type="list",
                        progress_callback=self.status_callback,
                    )

                    if not concepts_data:
                        concepts_data = [
                            {
                                "title": f"{l_title} Fundamentals",
                                "objectives": [
                                    f"Understand {l_title}",
                                    f"Apply {l_title} concepts",
                                ],
                                "llm_fallback": True,
                            }
                        ]
                        self.fallback_count += 1
                        logger.warning(f"  [FALLBACK] Using fallback concept for lesson '{l_title}' — LLM returned empty.")

                    concepts_data = concepts_data[:base_concepts]

                    concept_titles = []
                    for c_idx, concept_data in enumerate(concepts_data, 1):
                        c_title = self._normalize_title(concept_data.get("title", ""))
                        if not c_title or self._is_duplicate(
                            c_title, course_topic=topic, level="concept"
                        ):
                            continue  # Skip bad duplicates

                        self.used_titles.add(c_title)
                        self.used_titles_by_level["concept"].add(c_title)
                        concept_titles.append(c_title)
                        concepts_generated_in_unit.append(c_title)
                        c_uid = f"con_{uuid.uuid4().hex[:8]}"
                        concept_dict = {
                            "uid": c_uid,
                            "title": c_title,
                            "learning_objectives": concept_data.get(
                                "objectives", [f"Understand {c_title}"]
                            ),
                            "complexity_role": m_role,
                            "depth_level": module_specific_depth,
                            # QB-1 FIX: Persist bloom_level so FSM/audit can read
                            # the progression target per concept.
                            "bloom_level": int(module_bloom_level),
                            "ordinal": c_idx,
                        }
                        if concept_data.get("llm_fallback"):
                            concept_dict["llm_fallback"] = True
                        lesson_dict["concepts"].append(concept_dict)

                        if self.status_callback:
                            self.status_callback(f"STRUCT:CONCEPT:{c_uid}:{c_title}")

                    # QB-1 FIX: If dedup left the lesson with zero concepts
                    # (common when the LLM echoes parent-lesson vocabulary),
                    # backfill with numbered "{lesson} Part N" stubs so the
                    # structure is never empty. Better a generic concept than
                    # a black hole in the learning path.
                    if len(lesson_dict["concepts"]) == 0:
                        for pad_idx in range(1, base_concepts + 1):
                            pad_title = f"{l_title} Part {pad_idx}"
                            pad_uid = f"con_{uuid.uuid4().hex[:8]}"
                            self.used_titles.add(pad_title)
                            self.used_titles_by_level["concept"].add(pad_title)
                            concept_titles.append(pad_title)
                            concepts_generated_in_unit.append(pad_title)
                            lesson_dict["concepts"].append({
                                "uid": pad_uid,
                                "title": pad_title,
                                "learning_objectives": [f"Understand {l_title}"],
                                "complexity_role": m_role,
                                "depth_level": module_specific_depth,
                                "bloom_level": int(module_bloom_level),
                                "ordinal": pad_idx,
                                "llm_fallback": True,
                            })
                            self.fallback_count += 1
                        logger.warning(
                            f"  [FALLBACK] Lesson '{l_title}' had 0 concepts after dedup — "
                            f"backfilled with {base_concepts} Part-N stubs."
                        )

                    if concept_titles:
                        m_summary_lines.append(
                            f"      Concepts: {', '.join(concept_titles)}"
                        )

            planned_hierarchy_summary.append("\n".join(m_summary_lines))

    def generate_preview_for_module(
        self, m_title: str, m_depth: int, topic: str, m_context: str = ""
    ) -> Dict[str, Any]:
        """Independent helper to generate substructures for a module (Custom Course Wizard)."""
        cp = self.course_params
        mastery_label = cp.get("mastery_label", "Understanding")
        self.academic_context = f"{mastery_label} (mastery {self.mastery}/5)"
        constraints = self._get_domain_constraints(topic)
        temporal_constraint = constraints["temporal_constraint"]
        category_constraint = constraints["category_constraint"]

        num_units = max(1, min(3, m_depth))
        num_lessons = max(1, min(3, m_depth))
        num_concepts = max(2, min(5, m_depth + 1))

        u_prompt = (
            f"Topic: {topic}\nModule: {m_title}\nContext: {m_context}\n"
            f"Create {num_units} Units. JSON Array: [{{'title': '...', 'description': '...'}}]"
        )
        sys_u = f"Expert {self.academic_context} curriculum designer."
        # Raised: list-of-objects responses were TRUNCATED mid-string at the
        # old limit, so extract_python_list failed on valid-looking JSON and
        # the builder fell back to hardcoded concepts. Observed live:
        #   'Failed to extract list from: [{"title": "Identify Sharp Corners"...'
        # three attempts in a row, then a fallback concept. Output budget is
        # cheap; a fabricated placeholder concept is not.
        units_list = llm_generate_json(u_prompt, sys_prompt=sys_u, max_tokens=1200) or []
        # WIZ-3: Track fallback usage in preview
        preview_fallback_count = 0
        units_used_fallback = False
        if not units_list:
            units_list = [{"title": f"{m_title} Fundamentals"}]
            units_used_fallback = True
            preview_fallback_count += 1
            logger.warning(f"  [FALLBACK] Preview: Using fallback unit for module '{m_title}' — LLM returned empty.")

        module_structure = {
            "title": m_title,
            "context": m_context,
            "depth": m_depth,
            "units": [],
        }

        for unit in units_list[:num_units]:
            u_title = unit.get("title", "Unit").strip()
            l_prompt = f"Topic: {topic}\nModule: {m_title}\nUnit: {u_title}\nCreate {num_lessons} Lessons. JSON Array: [{{'title': '...'}}]"
            lessons_list = (
                llm_generate_json(l_prompt, sys_prompt=sys_u, max_tokens=1200) or []
            )
            lessons_used_fallback = False
            if not lessons_list:
                lessons_list = [{"title": f"Intro to {u_title}"}]
                lessons_used_fallback = True
                preview_fallback_count += 1
                logger.warning(f"  [FALLBACK] Preview: Using fallback lesson for unit '{u_title}' — LLM returned empty.")

            unit_structure = {"title": u_title, "lessons": []}
            if units_used_fallback:
                unit_structure["llm_fallback"] = True
            for lesson in lessons_list[:num_lessons]:
                l_title = lesson.get("title", "Lesson").strip()
                c_prompt = f"Topic: {topic}\nLesson: {l_title}\nCreate {num_concepts} key concepts. JSON Array: [{{'title': '...'}}]"
                concepts_list = (
                    llm_generate_json(c_prompt, sys_prompt=sys_u, max_tokens=500) or []
                )

                concepts_used_fallback = not concepts_list
                if concepts_used_fallback:
                    preview_fallback_count += 1
                    logger.warning(f"  [FALLBACK] Preview: Using fallback concepts for lesson '{l_title}' — LLM returned empty.")

                lesson_entry = {
                    "title": l_title,
                    "concepts": [
                        c.get("title", "Concept").strip()
                        for c in (concepts_list or [{"title": "Overview"}])[
                            :num_concepts
                        ]
                    ],
                }
                if lessons_used_fallback:
                    lesson_entry["llm_fallback"] = True
                if concepts_used_fallback:
                    lesson_entry["concepts_llm_fallback"] = True
                unit_structure["lessons"].append(lesson_entry)
            module_structure["units"].append(unit_structure)

        # WIZ-3: Include fallback summary in the preview response
        if preview_fallback_count > 0:
            module_structure["llm_fallback"] = True
            module_structure["fallback_count"] = preview_fallback_count

        return module_structure


# HOW LONG RESEARCH IS ACTUALLY ALLOWED TO TAKE.
#
# This was 15s for the first attempt and 20s for the broaden. Measured against
# the live service on 2026-08-24, cold (uncached) concepts came back in 4s, 10s
# and 37s, and two concurrent ones took ~85s. So the ceiling sat below the real
# distribution and research was abandoned on exactly the concepts that needed
# it most — the obscure ones that take longest to find material for.
#
# What made it worse: the service does NOT stop working when the client gives
# up. It finished those requests and cached them. The hydrator threw away
# results that had already been paid for, wrote the concept from the model's
# own knowledge, and marked it llm-only — the "zero citations" outcome, caused
# by a timeout rather than by an absence of sources.
#
# Raising the ceiling does not slow the common case: a 4s call still takes 4s.
# It only stops discarding the slow ones.
RESEARCH_TIMEOUT_S = int(os.getenv("HELGA_RESEARCH_TIMEOUT", "90"))




# WHICH FAILURES MORE EVIDENCE CAN ACTUALLY FIX.
#
# A concept that misses its contract for want of a primary source, a citation,
# or specific material cannot be fixed by asking the model again — the material
# is not there, and the only way it can comply is to invent something. Those
# failures need more RESEARCH, not another generation.
#
# Failures of form are different. A missing heading, a section in the wrong
# order, prose over the word cap: the model has everything it needs and simply
# did not do it. Fetching more sources for those spends minutes to change
# nothing.
_EVIDENCE_SHAPED = (
    "primary_source", "citation", "source", "reference",
    "named_result", "worked_example", "derivation",
    "nothing specific to this concept", "teaches little",
    "contradicts what PostgreSQL actually does",
)


class _SkipTruth(Exception):
    """Raised to skip the truth tier without losing the reason it was skipped."""


def _needs_more_evidence(problems):
    """True when the named problems are ones more material could solve."""
    blob = " ".join(problems or []).lower()
    return any(marker.lower() in blob for marker in _EVIDENCE_SHAPED)


def _ground_truth_problems(markdown, title, course_title, domain=""):
    """Claims a real SQL engine contradicts, phrased for the retry prompt.

    Applies only where a SQL engine is the authority. For a history course the
    probes have nothing to say, and saying nothing is the correct output — an
    empty list here means "not applicable", not "verified".
    """
    haystack = f"{course_title} {title}".lower()
    if not any(k in haystack for k in ("sql", "database", "postgres", "query")):
        return []
    try:
        findings, checked = sql_ground_truth.check_markdown(markdown)
    except Exception as e:
        # A checker that cannot run must not silently become a pass, but it
        # also must not fail a build over a missing side-car file.
        logger.warning("SQL ground truth could not run for %r (%s) — the "
                       "concept is stored UNCHECKED", title, e)
        return []
    if not checked:
        logger.warning("no measured SQL ground truth available — %r stored "
                       "unchecked (run: python -m services.core.sql_ground_truth)",
                       title)
        return []
    problems = []
    for f in findings:
        problems.append(
            f"a factual claim contradicts what PostgreSQL actually does: "
            f"{f['engine_says']}. Correct the sentence \"{f['claim'][:120]}\"")
    return problems


class ContentHydrator:
    def __init__(
        self,
        db_path: str = None,
        providers: list = None,
        status_callback=None,
        course_depth: int = 2,
        storage: StorageManager = None,
        mastery: int = None,
        should_cancel=None,
        learner_context: str = None,
    ):
        self.db_path = db_path
        # What the learner said they wanted, in their own words. Usually left
        # None here and read off the course in hydrate() — see the note there.
        self.learner_context = (learner_context or "").strip()
        self.provider = None  # Content providers removed — LLM-only generation
        self.status_callback = status_callback
        self.course_depth = course_depth
        # Hydration is the long phase — hours on this hardware — so it is the
        # one a learner is most likely to cancel, and the one that most needs
        # to notice. Checked per concept: the concept just written is on disk
        # and the next has not started, so a resume picks up cleanly.
        self.should_cancel = should_cancel
        # Remembered so hydrate() can tell "the caller chose 3" from "nobody
        # said, so it defaulted to course_depth" — see the note in hydrate().
        self._mastery_was_given = mastery is not None
        self.mastery_level = mastery if mastery is not None else course_depth
        self.used_source_ids = set()
        self.model = None
        # A1 depth contract. Off via HELGA_ENFORCE_DEPTH=0 for callers that
        # need raw generation (e.g. reproducing a historical build).
        self.enforce_depth = os.getenv("HELGA_ENFORCE_DEPTH", "1").lower() not in (
            "0", "false", "no")
        self.max_depth_retries = int(os.getenv("HELGA_DEPTH_RETRIES", "2"))
        self.topic_domain = None  # set by hydrate(); beats guessing from a string
        self._contract_failures = []
        # A2: below this, grounding is too thin to present as verified. Set 0
        # to disable the retry+marker behaviour entirely.
        self.confidence_floor = float(os.getenv("HELGA_CONFIDENCE_FLOOR", "0.5"))
        # Stage 4. On by default: it is deterministic, costs ~0.3s on a
        # 95-concept course, and a build that skips its own audit is exactly
        # the situation the audit exists to make visible.
        self.audit_enabled = os.getenv("HELGA_AUDIT", "1").lower() not in (
            "0", "false", "no")
        # The truth pass needs a verifier that this process can reach — either
        # torch+transformers locally, or MINICHECK_URL pointing at the
        # host-side service. Absent both, it reports NOT MEASURED.
        self.truth_check_enabled = os.getenv(
            "HELGA_TRUTH_CHECK", "1").lower() not in ("0", "false", "no")
        # Pass 3. Model time at the end of a build the learner is waiting on,
        # so it is bounded — the deterministic audit has already named exactly
        # which concepts are worth spending it on.
        self.repair_enabled = os.getenv("HELGA_REPAIR", "1").lower() not in (
            "0", "false", "no")
        self.repair_budget = int(os.getenv("HELGA_REPAIR_BUDGET", "25"))
        self._low_confidence_concepts = []
        # A3: text of a user-supplied document (EPUB/markdown/text). When set,
        # concepts are grounded in the user's OWN material rather than only in
        # web research. Previously uploaded files were never read at all.
        self.source_document = ""
        # Set by the book pipeline so hydration can read the chapter a concept
        # came from. None for a researched course.
        self.book = None
        # A1: fact-check pass. Disable with HELGA_FACT_CHECK=0 (it costs an
        # extra LLM call per concept, plus a confirmation call per flagged
        # claim).
        self.fact_check_enabled = os.getenv(
            "HELGA_FACT_CHECK", "1").lower() not in ("0", "false", "no")
        # Fraction of concepts to fact-check. 1.0 = every concept (correct but
        # ~8-10 min/concept locally); 0.25 = a quarter, evenly spread. The
        # course records what was actually checked so a partial sweep is never
        # reported as full coverage.
        try:
            self.fact_check_sample = max(0.0, min(1.0, float(
                os.getenv("HELGA_FACT_CHECK_SAMPLE", "0.34"))))
        except ValueError:
            self.fact_check_sample = 0.34
        self._fact_checked_count = 0
        self._fact_failures = []
        # Gate criterion 2. Costs one LLM call per sampled concept.
        self.level_calibration_enabled = os.getenv(
            "HELGA_LEVEL_CALIBRATION", "1").lower() not in ("0", "false", "no")

        if storage:
            self.storage = storage
        else:
            data_dir = os.path.dirname(db_path) if db_path else DATA_ROOT
            self.storage = StorageManager(data_dir)

    def close(self):
        pass

    def hydrate(self, course_uid: str):
        """Hydrate all concepts in a course with content from sources + LLM."""
        course = self.storage.courses.get_course(course_uid)
        if not course:
            logger.error(f"Course {course_uid} not found for hydration")
            return

        course_title = course.get("title", "General Knowledge")

        # READ OFF THE COURSE, for the same reason the supplementary sources
        # below are: the builder that took the brief and the hydrator that
        # needs it are different objects, and hydration frequently runs later
        # and in another process — a resume, a handback, a rebuild of the
        # concepts an external author left behind. Threading it through every
        # ContentHydrator call site instead would mean seven places to forget,
        # and the one that mattered most is the handback, where the local
        # model is finishing somebody else's course and has the least context
        # of all. An explicit constructor argument still wins if given.
        # THE CONTRACT THE CONTENT IS WRITTEN TO MUST BE THE ONE IT IS JUDGED
        # BY. The resume path builds a hydrator with `course_depth=3` and no
        # mastery, so a course at mastery 2 was hydrated against the mastery-3
        # contract — 320-1500 words — and then failed at finalize against
        # mastery 2's 200-1300. Measured: a concept came back at 1306 words and
        # was reported "too long for Understanding", after a retry loop that
        # had been checking a different bar the whole time. The course carries
        # its own mastery; nobody should have to pass it in again.
        if not self._mastery_was_given and course.get("mastery") is not None:
            try:
                declared = int(course["mastery"])
            except (TypeError, ValueError):
                declared = None
            if declared and declared != self.mastery_level:
                logger.info("  [CONTRACT] hydrating at the course's own mastery "
                            "%d, not the default %d", declared, self.mastery_level)
                self.mastery_level = declared

        if not self.learner_context:
            self.learner_context = (course.get("learner_context") or "").strip()
        if self.learner_context:
            logger.info("  [BRIEF] hydrating to the learner's own brief "
                        "(%d chars)", len(self.learner_context))

        # Which sources were classified SUPPLEMENTARY at skeleton time.
        #
        # Read off the course rather than passed in memory: the builder that
        # made the classification and the hydrator that consumes it are
        # different objects, and hydration can run later or in another process.
        # Without this the retention path silently marked nothing as
        # supplementary and the claim-share measurement always read zero.
        self._supplementary_books = [
            (s or {}).get("book") for s in (course.get("supplementary_sources") or [])
            if isinstance(s, dict) and s.get("book")
        ]
        if self._supplementary_books:
            logger.info(f"  [SOURCES] {len(self._supplementary_books)} supplementary "
                        f"source(s) usable for content only: "
                        f"{self._supplementary_books[:3]}")

        # A1: resolve the domain once per course rather than guessing per
        # concept from a topic string. Keyword matching on a title is fragile
        # ("the french revolution" contains no history keyword), so this is a
        # best-effort default that an explicit caller can override.
        #
        # THE COURSE ALREADY KNOWS. The skeleton resolves a teaching_domain and
        # stores it; this then ignored it and re-guessed from the title, which
        # for "advanced sql" returns None — so the contract fell back to the
        # generic one and demanded a named theorem of every concept, the exact
        # requirement just calibrated away for computing. Measured: three
        # consecutive concepts failed on `named_result` while the course record
        # said teaching_domain='computer_science' the whole time.
        if self.topic_domain is None:
            self.topic_domain = (course.get("teaching_domain")
                                 or infer_domain(course_title))
            if course.get("teaching_domain"):
                logger.info("  [DOMAIN] %s, as recorded on the course",
                            self.topic_domain)
        self._contract_failures = []
        self._low_confidence_concepts = []
        self._fact_failures = []

        # Build hierarchy context, concept list, and prerequisite map from JSON
        concept_list = []
        skipped_already_hydrated = 0
        hierarchy_map = {}
        module_source_map = {}
        concept_ref_map = {}
        # Pre-compute prerequisites: for each concept, the titles of preceding concepts
        all_concept_titles_in_order = []
        prerequisite_map = {}

        for module in course.get("modules", []):
            source_file = module.get("source_file", "")
            for unit in module.get("units", []):
                for lesson in unit.get("lessons", []):
                    for concept in lesson.get("concepts", []):
                        uid = concept["uid"]
                        title = concept["title"]
                        # Build prerequisite list from prior concepts in syllabus order
                        prerequisite_map[uid] = list(all_concept_titles_in_order[-5:])
                        all_concept_titles_in_order.append(title)

                        objectives = json.dumps(concept.get("learning_objectives", []))
                        complexity_role = concept.get("complexity_role", "")
                        bloom_level = concept.get("bloom_level", self.mastery_level)
                        depth_level = concept.get("depth_level", self.mastery_level)

                        # Check if already hydrated
                        existing_content = self.storage.courses.get_concept_content(
                            course_uid, uid
                        )
                        if existing_content and len(existing_content) > 100:
                            # Resume: this concept was hydrated by an earlier
                            # run. Counted, because every total derived from
                            # concept_list otherwise describes only the
                            # REMAINDER — and the depth contract was stamping
                            # level_verified over a whole course from however
                            # few concepts the resume happened to touch.
                            skipped_already_hydrated += 1
                            continue

                        user_note = concept.get("user_note", "") or module.get("user_note", "")
                        concept_list.append((
                            uid, title, objectives, complexity_role, user_note,
                            bloom_level, depth_level, prerequisite_map.get(uid, []),
                            concept.get("learning_objectives", []),
                        ))
                        concept_ref_map[uid] = concept
                        hierarchy_map[uid] = {
                            "module": module["title"],
                            "module_uid": module["uid"],
                            "unit": unit["title"],
                            "lesson": lesson["title"],
                            "lesson_uid": lesson["uid"],
                        }
                        if source_file:
                            module_source_map[uid] = source_file

        logger.info(
            f"Starting hydration for {len(concept_list)} concepts in '{course_title}'"
        )

        hydrated_count = 0
        failed_count = 0
        hydration_fallback_count = 0  # WIZ-3: Track hydration stub/fallback content
        _counter_lock = threading.Lock()
        _course_lock = threading.Lock()
        hydration_start_time = time.perf_counter()

        # Phase 11A: Parallel concept hydration with ThreadPoolExecutor.
        # Each concept's research + LLM call + file save is independent.
        #
        # Worker count MUST NOT exceed the GPU gate's background capacity.
        # Course building is classified as background work, and the gate
        # reserves only `bg_slots` (default 1) concurrent slots for it while
        # the hydrator used a hardcoded 3. Three workers contending for one
        # slot means the third waits ~2x an LLM call; once that exceeds the
        # 55s admit timeout the call raises GpuOverloaded. Measured call
        # latency is 8-40s, so this failed routinely — observed as a hydration
        # run stalling on repeated "admit wait exceeded 55.0s".
        #
        # (The old "cap at 3 for Jetson 8GB" rationale is also stale — this
        # runs on a Mac Mini M4 Pro — but the gate, not RAM, is the real
        # constraint.)
        try:
            from services.core.gpu_gate import get_gpu_gate
            _bg_cap = max(1, getattr(get_gpu_gate(), "bg_slots", 1))
        except Exception:
            _bg_cap = 1
        max_workers = max(1, min(_bg_cap, len(concept_list)))
        research_url = os.getenv("RESEARCH_URL", "http://helga-research:5006")

        def _cancelled():
            try:
                return bool(self.should_cancel and self.should_cancel())
            except Exception:
                return False

        def _hydrate_one(idx, uid, title, objectives, complexity_role, user_note,
                         bloom_level, depth_level, prerequisite_titles, learning_objectives_list):
            """Hydrate a single concept (runs in thread pool)."""
            nonlocal hydrated_count, failed_count, hydration_fallback_count

            # CANCELLED WORK IS NOT FAILED WORK. Return before doing anything,
            # and do NOT touch failed_count: a cancelled build that counted its
            # unstarted concepts as failures would trip the >50% abort gate and
            # mark the course "failed", which is a different and worse claim
            # than "the learner stopped it". Concepts already written stay on
            # disk and a resume skips them.
            if _cancelled():
                return

            h_ctx = hierarchy_map.get(uid, {})

            # Validate title length
            if not title or len(title.strip()) < MIN_TITLE_LEN:
                logger.warning(f"  [SKIP] Concept '{title}' title too short. Skipping.")
                with _counter_lock:
                    failed_count += 1
                return

            if self.status_callback:
                self.status_callback(f"STRUCT:HYDRATING:{uid}:START:{title}")

            # Research service call (I/O-bound, benefits from parallelism)
            reference_material = ""
            research_sources = []
            # Kept apart from research_sources on purpose: this is material to
            # CHECK against, never material to cite. Merging the two would put
            # a source into the citation list for a concept whose text the
            # model never saw.
            research_evidence = []
            research_confidence = 0.0
            # ABSENT IS NOT THE SAME AS THIN, and the learner-facing marker
            # below used to say the same thing for both. "The grounding pass
            # found little corroborating material" is a claim about the
            # SUBJECT; when the research service was never reached, no pass
            # happened and that sentence is simply false.
            research_reached = False
            try:
                research_resp = requests.post(
                    f"{research_url}/api/research_concept",
                    json={
                        "title": title,
                        "module_title": h_ctx.get("module", ""),
                        "course_title": course_title,
                        # B12: pass mastery so deeper courses get deeper/academic
                        # search queries (this was defaulting to 1, so the
                        # mastery>=3/>=4 queries never fired during creation).
                        "mastery": self.mastery_level,
                    },
                    timeout=RESEARCH_TIMEOUT_S,
                )
                if research_resp.status_code == 200:
                    research_data = research_resp.json()
                    reference_material = research_data.get("combined_text", "")
                    research_sources = research_data.get("sources", [])
                    research_evidence = research_data.get("evidence_sources", []) or []
                    research_confidence = research_data.get("confidence", 0.0)
                    research_reached = True
                    # Name the sources that grounded this concept. "Hydrating
                    # concept 7/12" says nothing about quality; "grounded in
                    # Wikibooks + Crossref" is the product's actual claim.
                    if self.status_callback and research_sources:
                        kinds = {}
                        for _s in research_sources:
                            k = _s.get("type", "source")
                            kinds[k] = kinds.get(k, 0) + 1
                        summary = ", ".join(f"{v} {k}" for k, v in kinds.items())
                        self.status_callback(
                            f"HYDRATE:SOURCES:{title}|{summary}|{research_confidence:.2f}")
            except Exception as research_err:
                logger.warning(f"  [RESEARCH] Unavailable for '{title}': {research_err}")

            # A2: make source_confidence load-bearing. It was computed, stored
            # and displayed but nothing ever acted on it — in the sample course
            # 24 of 36 concepts scored below 0.5 and shipped unmarked. A weak
            # score means the grounding pass found little, so retry once with a
            # broadened query before accepting thin sourcing.
            if (self.confidence_floor > 0
                    and research_confidence < self.confidence_floor):
                logger.info(
                    f"  [RESEARCH] '{title}' confidence {research_confidence:.2f} "
                    f"< floor {self.confidence_floor:.2f} — retrying broader")
                if self.status_callback:
                    self.status_callback(f"STRUCT:RESEARCH_RETRY:{title}")
                try:
                    broad = requests.post(
                        f"{research_url}/api/research_concept",
                        json={
                            # Drop the narrow concept framing and search the
                            # parent topic, which is what a thin result usually
                            # needs.
                            "title": f"{title} {h_ctx.get('module', '')}".strip(),
                            "module_title": h_ctx.get("module", ""),
                            "course_title": course_title,
                            "mastery": self.mastery_level,
                            "broaden": True,
                        },
                        timeout=RESEARCH_TIMEOUT_S,
                    )
                    if broad.status_code == 200:
                        bd = broad.json()
                        if bd.get("confidence", 0.0) > research_confidence:
                            reference_material = bd.get("combined_text", "") or reference_material
                            research_sources = bd.get("sources", []) or research_sources
                            research_evidence = (bd.get("evidence_sources")
                                                 or research_evidence)
                            research_confidence = bd.get("confidence", 0.0)
                            logger.info(
                                f"  [RESEARCH] '{title}' improved to "
                                f"{research_confidence:.2f}")
                except Exception as e:
                    logger.warning(f"  [RESEARCH] broaden failed for '{title}': {e}")

            # A3: the user's own document takes precedence as source material —
            # that is the entire point of uploading it. Web research is
            # appended as supporting context, not as a replacement.
            user_excerpt = ""
            # THE BOOK IS READ BY STRUCTURE, NOT BY SEARCH.
            #
            # A concept built from an uploaded book carries the chapter it came
            # from, written when the skeleton was built. That link is exact, so
            # searching the whole book for the concept's words would be strictly
            # worse: it can land in the wrong chapter, and a passage from the
            # wrong chapter reads as authoritative while being about something
            # else.
            #
            # Without this, a book course has its concepts NAMED from the book
            # and their content written from the model's recollection of it —
            # which is the difference between a course built from a book and a
            # course about a book.
            _ch = (concept_ref_map.get(uid) or {}).get("book_chapter")
            if _ch and getattr(self, "book", None) is not None:
                try:
                    # THE CHAPTER IS PARTITIONED ACROSS ITS CONCEPTS, not
                    # picked over by each of them.
                    #
                    # passage_for chose the best 7,000 characters for THIS
                    # concept, and every concept chose independently — so on a
                    # 30,000-character chapter the sections matching no concept
                    # title were read by nobody, and nothing said so. Measured
                    # on a four-section chapter: one concept saw 15% of it, and
                    # two whole sections were invisible.
                    user_excerpt = self._book_passage(
                        _ch, uid, concept_ref_map) or ""
                    if user_excerpt:
                        source_type = "book"
                except Exception as e:
                    logger.debug(f"[BOOK] passage lookup failed for {title!r}: {e}")
            if not user_excerpt and self.source_document:
                user_excerpt = self._excerpt_for_concept(
                    self.source_document, title, h_ctx)

            if user_excerpt:
                content_to_use = user_excerpt
                if reference_material:
                    content_to_use += "\n\n---\n\n" + reference_material
                source_type = "user-document+research" if reference_material else "user-document"

                # THE BOOK IS THE SOURCE, SO RETAIN IT AS ONE.
                #
                # A book course is written FROM this passage, and it was the
                # one source never stored: `sources` held only what web
                # research returned, so a book-built concept had no retained
                # text at all and the audit's truth check could not verify a
                # single claim against the very chapter it came from. The most
                # authoritative material in the whole pipeline was the only
                # material with no record.
                #
                # Stored as EVIDENCE rather than a citation: a chapter has no
                # URL, and rendering "[Chapter 3]()" to a learner is a broken
                # link. It is for checking against, which is what was missing.
                _origin = (_ch if _ch else
                           (getattr(self, "source_document_name", None)
                            or "uploaded document"))
                research_evidence = list(research_evidence or []) + [{
                    "title": f"{course_title}: {_origin}" if course_title
                             else str(_origin),
                    "url": "",
                    "passage": user_excerpt[:4000],
                    "type": "book",
                    "domain_tier": 1,
                    "cited": False,
                }]
                # Material supplied by the learner is authoritative for them,
                # so it should not be penalised by the web-grounding floor.
                research_confidence = max(research_confidence, self.confidence_floor)
            else:
                content_to_use = reference_material if reference_material else ""
                source_type = "research+llm" if reference_material else "llm-only"

            # Still weak after the retry: say so in the artifact itself rather
            # than shipping thin content that looks identical to well-grounded
            # content. The learner sees this; it is not only a metric.
            low_confidence = research_confidence < self.confidence_floor
            if low_confidence:
                self._low_confidence_concepts.append(
                    {"uid": uid, "title": title,
                     "confidence": round(research_confidence, 2)})

            # Pre-structure research material into buckets for better LLM utilization
            research_structured = self._preprocess_research(content_to_use)

            if self.status_callback:
                self.status_callback(f"STRUCT:HYDRATING:{uid}:STRUCTURING:{title}")

            # What the COURSE has already taught — retrieved, not carried.
            #
            # A running summary would cost an LLM call per concept and drift; a
            # claim index does not drift and stays small at any course size,
            # which is what keeps this affordable inside the context budget.
            _ledger_ctx = self._ledger_context(course_uid, title,
                                               learning_objectives_list,
                                               ordinal=idx)

            # LLM structuring call (I/O-bound, benefits from parallelism)
            structured_md = self._condense_and_structure_content(
                title,
                content_to_use,
                course_title,
                self.mastery_level,
                complexity_role,
                source_type,
                hierarchy_context=h_ctx,
                previous_concepts=[],
                module_concepts=[],
                research_sources=research_sources,
                research_confidence=research_confidence,
                user_note=user_note,
                bloom_level=bloom_level,
                learning_objectives=learning_objectives_list,
                prerequisite_titles=prerequisite_titles,
                research_structured=research_structured,
                ledger_context=_ledger_ctx,
            )

            # WIZ-3: Detect fallback stubs
            is_fallback = "[Hydration failed]" in structured_md
            if is_fallback:
                logger.warning(f"  [FALLBACK] Stub for '{title}' ({uid}).")
                if self.status_callback:
                    self.status_callback(f"STRUCT:WARN:CONCEPT_STUB:{title}")

            # A1: enforce the depth contract. The mastery slider was previously
            # only a prompt hint, so output converged on one house style
            # regardless of level (measured: every concept 626-876 words, and a
            # mastery=4 course with worked examples in 0/36 concepts). Validate
            # what actually came back and regenerate against the NAMED
            # deficiency rather than retrying blindly.
            if not is_fallback and self.enforce_depth:
                structured_md, contract_detail = self._enforce_depth_contract(
                    structured_md, title, course_title, complexity_role,
                    source_type, h_ctx, research_sources, research_confidence,
                    user_note, bloom_level, learning_objectives_list,
                    prerequisite_titles, research_structured, content_to_use,
                )
                if contract_detail and not contract_detail.get("ok"):
                    self._contract_failures.append({
                        "uid": uid, "title": title,
                        "problems": contract_detail.get("problems", []),
                    })

            # REDUNDANCY CORRECTION — one round, naming the offender.
            #
            # The same enforcement shape as the depth contract above and the
            # skeleton builder's unit/title corrections, for the same measured
            # reason: a prompt instruction not to repeat changed nothing 5/5,
            # while a correction naming the exact duplicated claim worked 5/5.
            # The ledger context is already in the prompt; this is what happens
            # when the model ignores it.
            if not is_fallback:
                structured_md = self._correct_redundancy(
                    structured_md, course_uid, uid, title, idx,
                    course_title, complexity_role, source_type, h_ctx,
                    research_sources, research_confidence, user_note,
                    bloom_level, learning_objectives_list, prerequisite_titles,
                    research_structured, content_to_use,
                )

            # A1: fact-check. The depth contract checks that rigor is PRESENT;
            # substance_check showed the real defect is that it can be WRONG —
            # 50% of sampled concepts contained false technical claims that
            # every other measure passed (right level, real citations, fluent
            # prose). Fluency is not accuracy.
            #
            # SAMPLED, not exhaustive. Checking every concept costs 2-5 extra
            # LLM calls each (check + a confirmation per flagged claim +
            # regeneration + recheck), which measured at 8-10 MINUTES per
            # concept on a local 9B — over five hours for a 36-concept course.
            # That is not a slow run, it is an unusable pipeline.
            #
            # The gate spec calls criterion 3 a SPOT-CHECK; level calibration
            # already samples. The sampled fraction is recorded on the course so
            # the verdict never implies more coverage than was actually checked.
            if (not is_fallback and self.fact_check_enabled
                    and self._should_fact_check(idx)):
                structured_md = self._apply_fact_check(
                    structured_md, title, course_title, complexity_role,
                    source_type, h_ctx, research_sources, research_confidence,
                    user_note, bloom_level, learning_objectives_list,
                    prerequisite_titles, research_structured, content_to_use,
                    uid,
                )

            # A2: an honest marker on thin content — RECORDED, not narrated.
            #
            # These were appended to the lesson body, so a learner mid-concept
            # read a paragraph about the research service being unreachable.
            # It is true, it matters, and it is a fact about the BUILD: it
            # belongs to the concept's metadata, where the course page and the
            # depth verdict can surface it, not in the middle of teaching.
            #
            # Appending here also happened AFTER validation, so content_guards
            # never saw it — measured: 5 concepts carrying the banner while
            # every gate reported them clean.
            grounding_note = None
            if low_confidence and not research_reached:
                grounding_note = {
                    "state": "unreached",
                    "detail": ("The research service could not be reached while "
                               "this concept was written, so nothing was checked. "
                               "That is not the same as a subject with thin "
                               "coverage."),
                }
            elif low_confidence:
                grounding_note = {
                    "state": "thin",
                    "confidence": round(research_confidence, 2),
                    "detail": ("The grounding pass found little corroborating "
                               "material, so this concept leans more on the "
                               "model's own knowledge."),
                }
            if grounding_note:
                logger.warning("  [GROUNDING] %s: %s", title,
                               grounding_note["detail"])
                with _course_lock:
                    if uid in concept_ref_map:
                        concept_ref_map[uid]["grounding_note"] = grounding_note

            # Append research citations
            if research_sources:
                sources_md = "\n\n## Sources\n"
                for src in research_sources:
                    src_title = src.get("title", "Untitled")
                    src_url = src.get("url", "")
                    src_type_str = src.get("type", "web")
                    tier = src.get("domain_tier", "")
                    tier_str = f" (Tier {tier})" if tier else ""
                    sources_md += f"- [{src_title}]({src_url}) — {src_type_str}{tier_str}\n"
                sources_md += f"\n*Source confidence: {research_confidence:.2f}*\n"
                structured_md += sources_md

            # Task #51: Store source_confidence.
            #
            # Under _course_lock because this writes into the LIVE `course`
            # dict that another worker may be handing to `update_course` at the
            # same moment — which deepcopies it and json.dumps it. Adding a key
            # can resize the concept dict mid-walk ("dictionary changed size
            # during iteration"). Harmless while hydration ran one concept at a
            # time; a real race now that background concurrency is the gate's
            # capacity rather than 1.
            with _course_lock:
                if uid in concept_ref_map:
                    concept_ref_map[uid]["source_confidence"] = round(
                        research_confidence, 2)

            # Save to filesystem (thread-safe: each concept writes a different file)
            self.storage.courses.save_concept_content(course_uid, uid, structured_md)

            # B26.5: hydration provenance — durable record of which sources
            # and model produced this concept (legal/licensing posture).
            try:
                _prov_conn = getattr(self.storage, "progress", None)
                if _prov_conn is not None:
                    _prov_conn._get_db().execute(
                        "INSERT INTO hydration_provenance (course_uid, concept_uid, sources, model) "
                        "VALUES (?,?,?,?)",
                        (course_uid, uid,
                         json.dumps([{"title": s.get("title"), "url": s.get("url"),
                                      "type": s.get("type"),
                                      "tier": s.get("domain_tier")}
                                     for s in (research_sources or [])]),
                         os.getenv("OLLAMA_MODEL", "nail-35b-a3b-ctx")))
                    _prov_conn._get_db().commit()
            except Exception as _prov_err:
                logger.debug(f"provenance write skipped: {_prov_err}")

            # THE LEDGER IS WRITTEN AFTER VALIDATION, NEVER BEFORE.
            #
            # A concept that failed its depth contract and is about to be
            # regenerated must not teach the ledger anything, or the retry would
            # be told not to re-teach its own discarded draft.
            self._record_taught(course_uid, uid, title, structured_md,
                                ordinal=idx,
                                module=(h_ctx or {}).get("module", ""),
                                lesson=(h_ctx or {}).get("lesson", ""))
            self._retain_sources(course_uid, uid, structured_md,
                                 research_sources,
                                 supplementary_books=getattr(
                                     self, "_supplementary_books", None),
                                 grounding_text=reference_material,
                                 evidence_sources=research_evidence)

            if self.status_callback:
                self.status_callback(f"STRUCT:HYDRATED:{uid}:{source_type}:{title}")

            # Update counters atomically
            with _counter_lock:
                # A STUB IS A FAILED CONCEPT, NOT A HYDRATED ONE.
                #
                # This used to count a fallback in hydration_fallback_count and
                # then increment hydrated_count anyway, so failed_count stayed 0
                # no matter how many bodies came back reading "[Hydration
                # failed]". With the LLM unreachable for a whole build that made
                # every counter say success, the >50% abort gate below never
                # fired, and the course was published "ready" with not one real
                # sentence in it. A body the learner cannot read did not succeed;
                # it is counted where the gate can see it.
                if is_fallback:
                    hydration_fallback_count += 1
                    failed_count += 1
                    return

                hydrated_count += 1

                # A course is NOT enterable until the whole build has run.
                #
                # This used to flip status to "available" after a single concept
                # hydrated. That put a learner inside a course before ANY of the
                # verification passes had happened: the depth contract, the fact
                # check, level calibration, the grounding verdict and the
                # syllabus-coverage gate all run after hydration finishes, and
                # asset collection after that. "Available" meant "one concept
                # exists", which is not the same claim at all — it is exactly the
                # structurally-clean-but-substantively-hollow failure this
                # pipeline is built against.
                #
                # Progress is still reported every concept; only the ENTRY gate
                # moved to the end. Status becomes "ready" once, after the
                # checks and the assets.
                if hydrated_count == 1:
                    with _course_lock:
                        course["hydrated_count"] = 1
                        self.storage.courses.update_course(course_uid, course)
                    logger.info(
                        f"Course '{course_title}' first concept hydrated "
                        f"({len(concept_list)-1} remaining); not enterable until "
                        "checks and assets complete")
                else:
                    # ...AND EVERY CONCEPT AFTER THE FIRST.
                    #
                    # The branch above was the only place hydrated_count ever
                    # reached the database, so it was written once, as the
                    # literal 1, and not again until the phase ended. The course
                    # card renders "N of M concepts" from that column, so a
                    # 136-concept build sat on "0 of 136" for hours with 18
                    # markdown files already written -- the same defect as the
                    # skeleton bar frozen at 10%, one phase later.
                    #
                    # A single-column UPDATE, not update_course: rewriting
                    # structure.json per concept from a thread pool is exactly
                    # the lock contention this file already warns about.
                    self.storage.courses.set_hydrated_count(
                        course_uid, hydrated_count)

        # Execute parallel hydration
        if max_workers > 1:
            logger.info(f"  [PARALLEL] Hydrating {len(concept_list)} concepts with {max_workers} workers")
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for idx, entry in enumerate(concept_list):
                    uid, title = entry[0], entry[1]
                    f = executor.submit(_hydrate_one, idx, *entry)
                    futures[f] = (uid, title)

                for future in as_completed(futures):
                    uid, title = futures[future]
                    try:
                        future.result()
                    except Exception as concept_error:
                        with _counter_lock:
                            failed_count += 1
                        logger.error(
                            f"  [CONCEPT FAILED] '{title}' ({uid}): {concept_error}",
                            exc_info=True,
                        )
        else:
            # Single concept — run directly
            for idx, entry in enumerate(concept_list):
                uid, title = entry[0], entry[1]
                try:
                    _hydrate_one(idx, *entry)
                except Exception as concept_error:
                    with _counter_lock:
                        failed_count += 1
                    logger.error(
                        f"  [CONCEPT FAILED] '{title}' ({uid}): {concept_error}",
                        exc_info=True,
                    )

        hydration_elapsed = time.perf_counter() - hydration_start_time
        logger.info(f"  [TIMING] Hydration completed in {hydration_elapsed:.1f}s")

        total_concepts = len(concept_list)

        # AUTO-9: Abort if >50% of concepts failed hydration.
        #
        # failed_count now includes fallback stubs (see the counter block in
        # _hydrate_one), which is what this gate was always meant to catch: more
        # than half the course has no teachable body. Stubs used to be invisible
        # here, so an entire build against a dead LLM sailed through with
        # failed_count == 0.
        if total_concepts > 0 and failed_count > total_concepts * 0.5:
            course["status"] = "failed"
            self.storage.courses.update_course(course_uid, course)
            msg = (
                f"Hydration failed for {failed_count}/{total_concepts} concepts "
                f"({hydration_fallback_count} of them fallback stubs) — >50% "
                "failure rate. Course marked as failed."
            )
            logger.error(msg)
            if self.status_callback:
                self.status_callback(f"ERROR: {msg}")
            raise CourseCreationError(msg)

        # Post-hydration status.
        #
        # "ready" is a promise that a learner can open any concept and find
        # content. Below the abort threshold the course is still worth keeping —
        # most of it hydrated — but it is not that promise, so it gets the
        # "partial" status the wizard path already uses for retryable hydration
        # damage. Only a clean run earns "ready".
        if failed_count > 0:
            course["status"] = "partial"
            logger.warning(
                f"Course '{course_title}' marked 'partial': {failed_count}/"
                f"{total_concepts} concepts have no usable body "
                f"({hydration_fallback_count} fallback stubs)"
            )
        else:
            course["status"] = "ready"
        # WIZ-3: Record hydration fallback count in course metadata
        if hydration_fallback_count > 0:
            existing_fallbacks = course.get("fallback_count", 0)
            course["fallback_count"] = existing_fallbacks + hydration_fallback_count

        # A1: record whether the course actually met the level it claims.
        # A course whose concepts miss the depth contract must not present
        # itself as that level — that is precisely the "college-level setting
        # doesn't produce a college-level course" complaint. We record the
        # verdict rather than silently shipping, so the UI and the golden-course
        # gate can both see it.
        if self.enforce_depth and total_concepts > 0:
            # A resumed run verifies only what it hydrated. Asserting
            # level_verified over the whole course from a 2-of-36 remainder
            # would be the depth contract lying about its own coverage, so a
            # partial run records its scope and never newly asserts the flag.
            _partial_run = skipped_already_hydrated > 0
            missed = len(self._contract_failures)
            met_pct = round(100 * (total_concepts - missed) / total_concepts, 1)
            course["depth_contract"] = {
                "mastery": self.mastery_level,
                "domain": self.topic_domain,
                "concepts_total": total_concepts,
                "concepts_previously_hydrated": skipped_already_hydrated,
                "partial_run": _partial_run,
                "concepts_missing_contract": missed,
                "met_pct": met_pct,
                # Below this the course is not credibly at its stated level.
                "level_verified": met_pct >= 80.0 and not _partial_run,
                "failures": self._contract_failures[:25],
            }
            if missed:
                logger.warning(
                    f"[DEPTH] {missed}/{total_concepts} concepts missed the "
                    f"mastery-{self.mastery_level} contract ({met_pct}% met)")
                if self.status_callback:
                    self.status_callback(
                        f"STRUCT:WARN:DEPTH_SUMMARY:{missed}/{total_concepts} "
                        f"concepts below level {self.mastery_level}")

        # A1: course-level factual verdict. A course still carrying confirmed
        # false claims must not be presented as verified at its level.
        if self.fact_check_enabled and total_concepts > 0:
            bad = len(self._fact_failures)
            course["fact_check"] = {
                "concepts_total": total_concepts,
                "concepts_checked": self._fact_checked_count,
                "sample_fraction": getattr(self, "fact_check_sample", 1.0),
                "concepts_with_false_claims": bad,
                "clean_pct": round(100 * (total_concepts - bad) / total_concepts, 1),
                "failures": self._fact_failures[:25],
            }
            if bad:
                logger.warning(
                    f"[FACT] {bad}/{total_concepts} concepts still contain "
                    f"confirmed-false claims after regeneration")
                if self.status_callback:
                    self.status_callback(
                        f"STRUCT:WARN:FACT_SUMMARY:{bad}/{total_concepts}")

        # Gate criterion 2: does the course READ at the level it claims?
        # The depth contract checks markers, the fact-checker checks truth;
        # neither asks whether the material is actually pitched where it was
        # sold. Judged blind — level hints are stripped first.
        if total_concepts > 0 and self.level_calibration_enabled:
            try:
                from services.common.level_calibration import calibrate
                bodies = []
                for c in concept_list:
                    try:
                        b = self.storage.courses.get_concept_content(
                            course_uid, c[0] if isinstance(c, (list, tuple)) else c)
                        if b:
                            bodies.append(b)
                    except Exception:
                        continue
                verdict = calibrate(bodies, self.mastery_level)
                if verdict:
                    course["level_calibration"] = verdict
                    if not verdict["calibrated"]:
                        logger.warning(
                            f"[LEVEL] course reads at {verdict['judged']} but "
                            f"claims {verdict['claimed']} "
                            f"(gap {verdict['gap']:+})")
                        if self.status_callback:
                            self.status_callback(
                                f"STRUCT:WARN:LEVEL_GAP:{verdict['gap']:+}")
            except Exception as e:
                logger.warning(f"level calibration failed: {e}")

        # A2: course-level grounding verdict, so thin sourcing is visible at a
        # glance instead of only per-concept.
        if total_concepts > 0:
            weak = len(self._low_confidence_concepts)
            course["grounding"] = {
                "confidence_floor": self.confidence_floor,
                "concepts_total": total_concepts,
                "concepts_below_floor": weak,
                "well_grounded_pct": round(
                    100 * (total_concepts - weak) / total_concepts, 1),
                "low_confidence": self._low_confidence_concepts[:25],
            }
            if weak:
                logger.warning(
                    f"[GROUNDING] {weak}/{total_concepts} concepts below "
                    f"confidence floor {self.confidence_floor}")

        # ---- PHASE 3: ASSET COLLECTION -------------------------------------
        # Runs after the content and its verdicts, before the course is
        # enterable. Every diagram the course will use is drawn HERE, where a
        # retry is free and generation can be grammar-constrained — neither of
        # which is true inside a 30-second dialogue turn. A session then only
        # selects from what this produced.
        #
        # Strictly degradable: a course with no pictures is a course, so any
        # failure here is logged and the build continues.
        if total_concepts > 0:
            try:
                from services.core.asset_collector import AssetCollector
                if self.status_callback:
                    self.status_callback("ASSET:PHASE:START")
                # A course built from an uploaded book uses that book's own
                # figures and no external imagery — see asset_collector's
                # book mode.
                collector = AssetCollector(
                    self.storage, status_callback=self.status_callback,
                    document_path=getattr(self, "source_document_path", None))
                asset_manifest = collector.collect(course_uid)
                course["assets"] = {
                    "collected": True,
                    "diagrams": collector.stats["generated"],
                    "reused": collector.stats["reused"],
                    "images": collector.stats["images"],
                    "concepts_with_assets": len(asset_manifest.get("concepts", {})),
                    "concepts_total": total_concepts,
                    "seconds": collector.stats["seconds"],
                }
                logger.info(f"[TIMING] Asset collection: {collector.stats['seconds']}s")
            except Exception as e:
                logger.warning(f"Asset collection failed (course still usable): {e}")
                course["assets"] = {"collected": False, "error": str(e)[:200]}
                if self.status_callback:
                    self.status_callback(f"ASSET:ERROR:{str(e)[:120]}")

        # ---- STAGE 4: THE AUDIT --------------------------------------------
        #
        # The last thing that happens before a course may be taught, and the
        # only pass that reads the FINISHED course rather than one concept as
        # it is written.
        #
        # It runs here, after assets, for two reasons. It sees the course a
        # learner will actually get, aids included. And every check above it
        # is per-concept and blind to the rest of the course, so nothing until
        # now could notice two concepts teaching contradictory things, a
        # definition printed twice, or a course whose depth verdict covers a
        # seventh of it.
        #
        # Degradable by construction: an audit that cannot run must not destroy
        # a course that built successfully. It records what it found, and the
        # status it sets is the only thing it changes.
        if total_concepts > 0 and self.audit_enabled:
            try:
                course["audit"] = self._run_audit(course_uid, course)
                # PASS 3 — repair what it found, then re-audit so the
                # recorded verdict describes the course as it now stands
                # rather than as it was before the fixes.
                if self.repair_enabled:
                    course["repair"] = self._run_repair(
                        course_uid, course, course["audit"])
                    if (course["repair"].get("outcomes") or {}).get("fixed") \
                            or (course["repair"].get("outcomes") or {}).get("escalated"):
                        course["audit"] = self._run_audit(course_uid, course)
                        course["audit"]["after_repair"] = True

                # THE GATE. Everything above reports; this is what decides
                # whether a learner may open the course.
                gated, why = self._gate_status(course, course["audit"])
                if gated and gated != course.get("status"):
                    logger.warning("[GATE] %s -> %s: %s",
                                   course.get("status"), gated, why)
                    course["status"] = gated
                    course["gate_reason"] = why
                    if self.status_callback:
                        self.status_callback(f"AUDIT:GATE:{gated}:{why[:90]}")
            except Exception as e:
                logger.warning("Stage 4 audit failed (course still usable): %s", e)
                # NOT a pass. A course whose audit crashed is unaudited, and
                # the reader must be able to tell that from a clean one.
                course["audit"] = {"ran": False, "error": str(e)[:200]}

        # STAGE 5 — the review item bank.
        #
        # Extraction, not generation: the hydrator has already written Key
        # Facts, Belief/Correction pairs, Edge Cases and Bloom-banded Socratic
        # Hooks, which is an item bank in all but name. Doing it here means the
        # daily queue needs no model at review time, which on this hardware is
        # the difference between a usable review habit and a 47-second wait per
        # card. A failure here must not fail the build: a course with no items
        # is still a course you can be taught.
        try:
            from services.common.item_bank import build_for_course
            bank = build_for_course(
                course_uid, self.storage, data_root=DATA_ROOT,
                status_cb=self.status_callback)
            course["item_bank"] = bank
            if self.status_callback and bank.get("items"):
                self.status_callback(
                    f"ITEMS:{bank['items']} review items over "
                    f"{bank['concepts']} concepts")
        except Exception as e:
            logger.warning("Item bank build failed (course still usable): %s", e)
            course["item_bank"] = {"ran": False, "error": str(e)[:200]}

        self.storage.courses.update_course(course_uid, course)

        # COMPACT THE INDEX WHERE THE TOMBSTONES ARE MADE.
        #
        # index_concept is DELETE+INSERT, and in FTS5 a delete writes a
        # tombstone while the insert appends a segment — nothing is reclaimed.
        # A build, its retries, the asset pass and every repair all rewrite the
        # same concepts, so segment count grows monotonically and bm25() reads
        # all of them. `optimize` was never run anywhere in the repo; measured
        # on a copy of the live database it took the index from 218 blocks to
        # 159 and 2.54 MB to 2.26 MB.
        #
        # Here rather than in a nightly job because this is the moment the
        # tombstones exist, and the cost is paid once at the end of a build the
        # learner is already waiting on.
        #
        # Best-effort throughout: a search index is a convenience and must
        # never fail a build that produced real content.
        try:
            if hasattr(self.storage, "search"):
                self.storage.search.rebuild_search_index()
                if hasattr(self.storage.search, "optimize_index"):
                    self.storage.search.optimize_index()
        except Exception as e:
            logger.warning(f"Search reindex after hydration failed: {e}")

        content_dir = os.path.join(
            self.storage.courses.courses_dir, course_uid, "content"
        )
        md_files = (
            [f for f in os.listdir(content_dir) if f.endswith(".md")]
            if os.path.exists(content_dir)
            else []
        )
        summary_msg = f"Course hydration complete: {hydrated_count}/{total_concepts} succeeded, {failed_count} failed. {len(md_files)} .md files written."
        if hydration_fallback_count > 0:
            summary_msg += f" {hydration_fallback_count} concept(s) used fallback stub content."
        logger.info(summary_msg)
        if self.status_callback:
            self.status_callback(f"LOG: {summary_msg}")
            if failed_count > 0:
                self.status_callback(
                    "CHECK:HYDRATION:WARN:Some concepts failed to hydrate."
                )

    def _preprocess_research(self, combined_text):
        """Bucket research text into facts, examples, and edge cases for structured LLM injection.
        Pure Python heuristics — no LLM calls, thread-safe."""
        result = {"key_facts": "", "examples": "", "edge_cases": "", "remainder": ""}
        if not combined_text or len(combined_text.strip()) < 50:
            result["remainder"] = combined_text or ""
            return result

        sentences = re.split(r'(?<=[.!?])\s+', combined_text)
        facts, examples, edges, other = [], [], [], []

        fact_kw = {"defined as", "refers to", "is a", "consists of", "known as", "characterized by"}
        example_kw = {"for example", "such as", "in practice", "case study", "instance", "demonstrated"}
        edge_kw = {"however", "except", "limitation", "does not apply", "breaks down", "controversy", "although", "caveat"}

        for s in sentences:
            s_lower = s.lower()
            if any(kw in s_lower for kw in fact_kw) or (len(s) < 200 and any(c.isdigit() for c in s)):
                facts.append(s.strip())
            elif any(kw in s_lower for kw in example_kw):
                examples.append(s.strip())
            elif any(kw in s_lower for kw in edge_kw):
                edges.append(s.strip())
            else:
                other.append(s.strip())

        result["key_facts"] = " ".join(facts[:5])
        result["examples"] = " ".join(examples[:3])
        result["edge_cases"] = " ".join(edges[:3])
        result["remainder"] = " ".join(other)[:1500]
        return result

    def _should_fact_check(self, idx):
        """Evenly-spaced sample, so checked concepts are spread through the
        course rather than clustered in the (systematically easier) intro."""
        frac = getattr(self, "fact_check_sample", 1.0)
        if frac >= 1.0:
            return True
        if frac <= 0.0:
            return False
        step = max(1, int(round(1.0 / frac)))
        return (idx % step) == 0

    def _apply_fact_check(self, structured_md, title, course_title,
                          complexity_role, source_type, h_ctx, research_sources,
                          research_confidence, user_note, bloom_level,
                          learning_objectives, prerequisite_titles,
                          research_structured, content_to_use, uid):
        """Verify claims and regenerate around any that are confirmed false.

        Only claims that fail an independent second check are acted on. The
        first pass is primed with known failure modes, which makes it sensitive
        but prone to crying wolf — in self-test it flagged the TRUE statement
        "adjusting for a mediator removes part of the effect being estimated".
        Regenerating CORRECT content is worse than missing an error, because it
        invites the model to swap a true statement for a confident new one.
        """
        try:
            from services.common.fact_check import check_content, correction_hint
        except Exception as e:
            logger.debug(f"fact-check unavailable: {e}")
            return structured_md

        # Count BEFORE checking, so the recorded coverage reflects concepts we
        # attempted rather than only those that came back clean.
        self._fact_checked_count += 1
        result = check_content(structured_md, source_text=content_to_use or "",
                               concept_title=title)
        if not result.get("checked"):
            return structured_md
        false_claims = result.get("false") or []
        if not false_claims:
            return structured_md

        logger.warning(
            f"  [FACT] '{title}': {len(false_claims)} confirmed-false claim(s)")
        for c in false_claims[:3]:
            logger.warning(f"    - {str(c)[:160]}")
        if self.status_callback:
            self.status_callback(f"STRUCT:FACT_RETRY:{title}")

        hint = correction_hint(false_claims)
        try:
            candidate = self._condense_and_structure_content(
                title, content_to_use, course_title, self.mastery_level,
                complexity_role, source_type,
                hierarchy_context=h_ctx, previous_concepts=[],
                module_concepts=[], research_sources=research_sources,
                research_confidence=research_confidence,
                user_note=(user_note + "\n\n" + hint).strip(),
                bloom_level=bloom_level,
                learning_objectives=learning_objectives,
                prerequisite_titles=prerequisite_titles,
                research_structured=research_structured,
            )
        except Exception as e:
            logger.warning(f"  [FACT] regeneration failed for '{title}': {e}")
            candidate = None

        if candidate and "[Hydration failed]" not in candidate:
            recheck = check_content(candidate, source_text=content_to_use or "",
                                    concept_title=title)
            if recheck.get("checked") and not recheck.get("false"):
                logger.info(f"  [FACT] '{title}' corrected on retry")
                return candidate
            # Keep whichever draft carries fewer confirmed falsehoods.
            if len(recheck.get("false") or []) < len(false_claims):
                self._fact_failures.append(
                    {"uid": uid, "title": title,
                     "remaining": len(recheck.get("false") or [])})
                return candidate

        self._fact_failures.append(
            {"uid": uid, "title": title, "remaining": len(false_claims),
             "claims": [str(c)[:200] for c in false_claims[:3]]})
        if self.status_callback:
            self.status_callback(f"STRUCT:WARN:FACT_UNRESOLVED:{title}")
        return structured_md

    def _excerpt_for_concept(self, document, title, hierarchy_ctx=None,
                             window=6000):
        """Pull the most relevant slice of a user document for one concept.

        A whole book will not fit in context, so we locate the passages that
        mention the concept (and its parent module) and return a window around
        the best match. Deliberately simple lexical scoring: this runs once per
        concept during a build, and the alternative — embedding an entire book
        on a local 9B setup — is not worth the cost here.

        Returns "" when the document has nothing to say about the concept, so
        the caller can fall back to research rather than feeding in an
        arbitrary, unrelated chunk.
        """
        if not document or not title:
            return ""

        terms = [t for t in re.findall(r"[a-z0-9]+", title.lower()) if len(t) > 3]
        if hierarchy_ctx:
            for key in ("module", "unit", "lesson"):
                val = hierarchy_ctx.get(key) or ""
                terms += [t for t in re.findall(r"[a-z0-9]+", val.lower())
                          if len(t) > 3]
        if not terms:
            return ""

        low = document.lower()
        # Score fixed-size blocks by how many distinct concept terms they hit.
        block = max(1000, window // 2)
        best_score, best_pos = 0, -1
        for start in range(0, max(1, len(low) - 1), block):
            chunk = low[start:start + block]
            score = sum(1 for t in set(terms) if t in chunk)
            if score > best_score:
                best_score, best_pos = score, start

        # Require more than a single incidental term match.
        if best_pos < 0 or best_score < 2:
            return ""

        half = window // 2
        centre = best_pos + block // 2
        s = max(0, centre - half)
        return document[s:s + window].strip()

    def _enforce_depth_contract(self, structured_md, title, course_title,
                                complexity_role, source_type, h_ctx,
                                research_sources, research_confidence,
                                user_note, bloom_level, learning_objectives,
                                prerequisite_titles, research_structured,
                                content_to_use):
        """Validate a concept against its depth contract, regenerating on miss.

        Returns (best_markdown, detail). The contract check runs on the body
        BEFORE the Sources block is appended, so a citation list can't be what
        satisfies a rigor requirement.

        On failure we retry with a targeted instruction naming the missing
        element ("include a step-by-step derivation", "cite a primary source"),
        because a blind retry of the same prompt reproduces the same gap. If
        every attempt misses, we keep the LONGEST attempt and report the
        failure upward — the course-level gate then decides whether the course
        may keep claiming this level.
        """
        ok, problems, detail = validate_concept(
            structured_md, self.mastery_level, course_title, self.topic_domain,
            sources=research_sources)

        # WHAT MUST NEVER REACH A LEARNER, checked alongside how deep it is.
        #
        # The depth contract asks whether a concept is deep ENOUGH. It has
        # nothing to say about the model's own deliberation left in the text,
        # a section filled with boilerplate, or a build-time apology printed
        # mid-lesson — all of which an audit found in concepts that met their
        # contract. These problems join the same list, so the regeneration loop
        # below fixes them with no new machinery.
        try:
            guard_problems = content_guards.inspect(
                structured_md, title=title, course_title=course_title)
        except Exception as e:
            logger.error("content guards could not run for %r (%s) — refusing "
                         "rather than passing it unchecked", title, e)
            guard_problems = ["the content guards could not be evaluated"]
        if guard_problems:
            for gp in guard_problems:
                logger.warning("  [GUARD] %s: %s", title, gp)
            problems = list(problems) + guard_problems
            ok = False

        # AND WHETHER IT IS TRUE, where that can be settled by running it.
        truth_problems = _ground_truth_problems(
            structured_md, title, course_title, self.topic_domain)
        if truth_problems:
            for tp in truth_problems:
                logger.warning("  [FALSE] %s: %s", title, tp)
            problems = list(problems) + truth_problems
            ok = False

        if ok:
            return structured_md, {"ok": True, "problems": [], **detail}

        best_md, best_problems, best_detail = structured_md, problems, detail
        attempts_made = 0
        for attempt in range(self.max_depth_retries):
            attempts_made = attempt + 1
            hint = regeneration_hint(best_problems)
            logger.info(
                f"  [DEPTH] '{title}' missed contract "
                f"({', '.join(best_detail.get('missing') or []) or 'length'}); "
                f"retry {attempt + 1}/{self.max_depth_retries}")
            if self.status_callback:
                self.status_callback(f"STRUCT:DEPTH_RETRY:{title}")

            # GO AND LOOK AGAIN, BEFORE WRITING AGAIN.
            #
            # This loop re-prompted the model with the SAME research material
            # and a hint naming what was missing. When the thing missing was a
            # primary source or specific detail, that asks a model to produce
            # from evidence it does not have, and the only way to comply is to
            # invent — which is how a concept can fail a contract three times
            # and come back more confident each time.
            #
            # Research otherwise ran at most twice per concept: once at the
            # start, and once more only if research CONFIDENCE fell below the
            # floor. Neither is triggered by the content being wrong.
            #
            # One broadened fetch, on the first retry only, and only when the
            # problems are ones more material can solve.
            if attempt == 0 and _needs_more_evidence(best_problems):
                more = self._fetch_more_evidence(
                    title, h_ctx, course_title, research_sources)
                if more:
                    research_sources = more["sources"] or research_sources
                    content_to_use = more["text"] or content_to_use
                    research_confidence = max(research_confidence,
                                              more["confidence"])
                    logger.info(
                        "  [RESEARCH] refetched for '%s' — %d source(s), "
                        "confidence %.2f", title, len(more["sources"]),
                        more["confidence"])

            try:
                candidate = self._condense_and_structure_content(
                    title, content_to_use, course_title, self.mastery_level,
                    complexity_role, source_type,
                    hierarchy_context=h_ctx,
                    previous_concepts=[], module_concepts=[],
                    research_sources=research_sources,
                    research_confidence=research_confidence,
                    user_note=(user_note + "\n\n" + hint).strip(),
                    bloom_level=bloom_level,
                    learning_objectives=learning_objectives,
                    prerequisite_titles=prerequisite_titles,
                    research_structured=research_structured,
                )
            except Exception as e:
                logger.warning(f"  [DEPTH] retry failed for '{title}': {e}")
                break
            if not candidate or "[Hydration failed]" in candidate:
                break
            c_ok, c_problems, c_detail = validate_concept(
                candidate, self.mastery_level, course_title, self.topic_domain,
                sources=research_sources)
            try:
                c_guard = content_guards.inspect(candidate, title=title,
                                                 course_title=course_title)
            except Exception:
                c_guard = ["the content guards could not be evaluated"]
            c_truth = _ground_truth_problems(
                candidate, title, course_title, self.topic_domain)
            if c_guard or c_truth:
                c_problems = list(c_problems) + list(c_guard) + list(c_truth)
                c_ok = False
            if c_ok:
                logger.info(f"  [DEPTH] '{title}' met contract on retry {attempt + 1}")
                return candidate, {"ok": True, "problems": [], **c_detail}
            # An identical failure means the next attempt is identical work.
            #
            # `hint` comes from `best_problems`, so when a retry reproduces the
            # same problem set the following attempt sends a byte-identical
            # prompt at the same temperature and regenerates the whole ~900-token
            # document again. The problems that repeat are the ones the model
            # structurally CANNOT fix from here — "cite a primary source" when
            # the research pass returned none — not ones it randomly missed.
            #
            # Measured: on a 12-concept mastery-4 build where every concept
            # missed, the retry stage was 64% of the entire build. Half of that
            # was this second identical attempt.
            if set(c_problems) == set(best_problems):
                logger.info(
                    f"  [DEPTH] '{title}' retry {attempt + 1} reproduced the same "
                    f"deficiencies; further attempts would send an identical "
                    f"prompt — stopping")
                break
            # Keep whichever attempt is closer to the contract.
            if len(c_problems) < len(best_problems):
                best_md, best_problems, best_detail = candidate, c_problems, c_detail

        logger.warning(
            f"  [DEPTH] '{title}' still below contract after "
            f"{attempts_made} retr{'y' if attempts_made == 1 else 'ies'}: "
            f"{'; '.join(best_problems)}")
        if self.status_callback:
            self.status_callback(f"STRUCT:WARN:DEPTH_MISS:{title}")
        return best_md, {"ok": False, "problems": best_problems, **best_detail}

    # --- taught-concepts ledger ---------------------------------------------
    #
    # Retrieval, not stuffing. See services/core/taught_ledger.py for why the
    # index is structured rather than a rolling summary, and why the gate is
    # "introduces from scratch a claim already introduced" rather than a
    # similarity threshold.
    #
    # Every method here is best-effort: the ledger improves a course and must
    # never fail one. Hydration failing means no course; the ledger failing
    # means a course that might repeat itself.

    def _ledger_conn(self):
        prog = getattr(self.storage, "progress", None)
        if prog is None:
            return None
        try:
            return prog._get_db()
        except Exception as e:
            logger.debug(f"[LEDGER] no connection: {e}")
            return None

    def _ledger_context(self, course_uid, title, objectives, ordinal):
        """Already-taught neighbours, rendered for the prompt. '' when unusable."""
        conn = self._ledger_conn()
        if conn is None:
            return ""
        try:
            from services.core.taught_ledger import neighbours, format_context
        except ImportError:
            try:
                from taught_ledger import neighbours, format_context
            except ImportError:
                return ""
        try:
            obj = " ".join(objectives or []) if isinstance(objectives, list) else str(objectives or "")
            return format_context(neighbours(conn, course_uid, title, obj,
                                             before_ordinal=ordinal))
        except Exception as e:
            logger.debug(f"[LEDGER] context lookup failed: {e}")
            return ""

    def _store_teaching_object(self, conn, course_uid, concept_uid, title,
                               markdown, ordinal=None):
        """Parse the concept into addressable structure and store it beside the
        prose. Deterministic, no model, so it cannot fail a build.

        `prerequisites` are filled from the LEDGER rather than left empty: the
        concepts this one was actually shown as already-taught neighbours are
        the ones it was written to build on, which is a better answer than the
        preceding-five-titles heuristic and the field the "cite, don't
        re-teach" rule needs.
        """
        try:
            from services.core.teaching_object import build, completeness, to_json
        except ImportError:
            try:
                from teaching_object import build, completeness, to_json
            except ImportError:
                return
        try:
            prereqs = []
            if ordinal is not None:
                try:
                    from services.core.taught_ledger import neighbours
                    prereqs = [n["concept_uid"] for n in neighbours(
                        conn, course_uid, title, "", k=4,
                        before_ordinal=ordinal)]
                except Exception:
                    prereqs = []
            obj = build(markdown, concept_uid, title, prerequisites=prereqs)
            c = completeness(obj)
            conn.execute(
                "INSERT OR REPLACE INTO teaching_objects "
                "(course_uid, concept_uid, obj, completeness) VALUES (?,?,?,?)",
                (course_uid, concept_uid, to_json(obj), c["score"]))
            # The text the generator actually read, and a hash of what it
            # wrote. Separately guarded: a failure to store either must not
            # lose the sources, which are the more important record.
            try:
                self._retain_grounding(conn, course_uid, concept_uid,
                                       markdown, grounding_text, now)
            except Exception as e:
                logger.debug("grounding context not stored for %s: %s",
                             concept_uid, e)
            conn.commit()
            if c["score"] < 0.5:
                logger.warning(f"  [HOLLOW] {title!r} filled only "
                               f"{c['present']}/{c['of']} fields — missing "
                               f"{c['missing']}")
        except Exception as e:
            logger.debug(f"[TEACHING_OBJECT] store failed for {title!r}: {e}")

    def _store_math_speech(self, course_uid, concept_uid, title, markdown):
        """Pre-generate spoken forms for every formula. Deterministic, no model.

        Done at hydration so a tutoring turn reads a stored string instead of
        parsing LaTeX on the critical path of a reply that already costs ~30 s.
        """
        try:
            from services.core.math_speech import speech_for
        except ImportError:
            try:
                from math_speech import speech_for
            except ImportError:
                return
        try:
            spans = speech_for(markdown)
            if not spans:
                return
            n = self.storage.courses.save_concept_math(course_uid, concept_uid, spans)
            leftover = [s for _, _, u in spans for s in u]
            if leftover:
                # Surfaced rather than swallowed: a speech string still carrying
                # control sequences is non-empty, passes every "did it produce
                # output" check, and is useless to a listener.
                logger.warning(f"  [MATH] {title!r}: {len(leftover)} LaTeX "
                               f"sequence(s) unspoken, e.g. {sorted(set(leftover))[:3]}")
            elif n:
                logger.debug(f"  [MATH] {title!r}: {n} formula(s) spoken")
        except Exception as e:
            logger.debug(f"[MATH] speech generation skipped for {title!r}: {e}")

    def _correct_redundancy(self, markdown, course_uid, concept_uid, title,
                            ordinal, course_title, complexity_role, source_type,
                            h_ctx, research_sources, research_confidence,
                            user_note, bloom_level, learning_objectives,
                            prerequisite_titles, research_structured, raw_text):
        """One regeneration when a concept re-teaches. Returns the better draft.

        Accepted only if the correction actually corrected — a retry that
        removes the repetition by removing the substance is not an improvement,
        so the replacement must both repeat less and remain a real document.
        """
        conn = self._ledger_conn()
        if conn is None:
            return markdown
        try:
            from services.core.taught_ledger import (
                check_redundancy, correction_hint)
        except ImportError:
            try:
                from taught_ledger import check_redundancy, correction_hint
            except ImportError:
                return markdown
        try:
            before = check_redundancy(conn, course_uid, concept_uid,
                                      markdown, ordinal)
            if before.get("ok"):
                return markdown
            hint = correction_hint(before)
            if not hint:
                return markdown

            logger.info(f"  [LEDGER] correcting {title!r}: "
                        f"{len(before.get('reintroduced') or [])} repeated claim(s)")
            retry = self._condense_and_structure_content(
                title, raw_text, course_title, self.mastery_level,
                complexity_role, source_type,
                hierarchy_context=h_ctx, previous_concepts=[],
                module_concepts=[], research_sources=research_sources,
                research_confidence=research_confidence, user_note=user_note,
                bloom_level=bloom_level,
                learning_objectives=learning_objectives,
                prerequisite_titles=prerequisite_titles,
                research_structured=research_structured,
                ledger_context=hint,
            )
            if not retry or "[Hydration failed]" in retry:
                return markdown
            after = check_redundancy(conn, course_uid, concept_uid, retry, ordinal)
            # Fewer repeated claims AND not a hollowed-out shell. Without the
            # second test the cheapest way to pass is to say less.
            better = (len(after.get("reintroduced") or [])
                      < len(before.get("reintroduced") or [])
                      and len(retry) > len(markdown) * 0.7)
            if better:
                logger.info(f"  [LEDGER] correction reduced repeats "
                            f"{len(before.get('reintroduced') or [])} -> "
                            f"{len(after.get('reintroduced') or [])}")
                return retry
            logger.info("  [LEDGER] correction was not an improvement — keeping original")
        except Exception as e:
            logger.debug(f"[LEDGER] redundancy correction skipped: {e}")
        return markdown




    def _run_audit(self, course_uid, course):
        """Stage 4 over the finished course. Returns the verdict to record.

        Deliberately reads what is ON DISK rather than what this run produced:
        a resumed build knows only its own segment, and the question here is
        whether the whole course is fit to teach.
        """
        import time as _time
        from services.core.course_audit import audit_course, walk_concepts
        from services.core import course_qa

        t0 = _time.time()
        if self.status_callback:
            self.status_callback("AUDIT:PHASE:START")

        # Everything the audit reads is held at once, because the whole point
        # is questions that need the whole course. A 200-concept course is a
        # few megabytes of markdown, which is affordable — but only because
        # concepts are bounded by the depth contract's word cap. If that ever
        # stops being true this is where it will show.
        contents, sources_by_uid = {}, {}
        for concept, _path in walk_concepts(course):
            uid = concept.get("uid")
            if not uid:
                continue
            try:
                body = self.storage.courses.get_concept_content(course_uid, uid)
            except Exception:
                body = None
            if body:
                contents[uid] = body

        conn = self._ledger_conn()
        if conn is not None:
            try:
                for row in conn.execute(
                        "SELECT concept_uid, title, url, passage, source_type "
                        "FROM sources WHERE course_uid=?", (course_uid,)):
                    sources_by_uid.setdefault(row[0], []).append(
                        {"title": row[1], "url": row[2], "passage": row[3],
                         "type": row[4]})
            except Exception as e:
                logger.debug("audit could not read sources: %s", e)

        # THE DOMAIN COMES OFF THE COURSE, NEVER RE-INFERRED.
        #
        # Re-inferring is what made hydration demand a named theorem of every
        # SQL concept when 0 of 16 known-good ones had one. The course already
        # records what it is.
        report = audit_course(
            course, contents, sources_by_uid=sources_by_uid,
            mastery=self.mastery_level,
            course_title=course.get("title") or "",
            domain=course.get("teaching_domain") or self.topic_domain)

        # The ledger half: questions answerable only from what the build
        # recorded, which the file-level pass cannot see.
        ledger = {}
        if conn is not None:
            for name, fn in (("substance", course_qa.check_substance),
                             ("hollowness", course_qa.check_hollowness),
                             ("grounding", course_qa.check_grounding),
                             ("supplementary", course_qa.check_supplementary)):
                try:
                    ledger[name] = fn(conn, course_uid)
                except Exception as e:
                    ledger[name] = {"checked": False, "reason": str(e)[:120]}
        # DEPTH MEASURED NOW, NOT READ FROM THE BUILD RECORD.
        #
        # check_depth reads course["depth_contract"], which is stamped during
        # hydration and never updated afterwards. So a course could have nine
        # concepts repaired and still report the depth it had before any of
        # them — measured on the first live repair run: 12 concepts attempted,
        # 9 fixed, depth still 0.394 to four decimal places.
        #
        # This pass already re-ran validate_concept over every stored concept,
        # so the current answer is in `report` and was being thrown away.
        try:
            audited = report.get("concepts_audited") or 0
            missed = len({f.get("concept_uid")
                          for f in (report.get("findings") or [])
                          if f.get("check") == "depth_contract"})
            for sysf in report.get("systemic") or []:
                if sysf.get("check") == "depth_contract":
                    missed = max(missed, sysf.get("concepts", 0))
            if audited:
                share = round((audited - missed) / audited, 3)
                ledger["depth"] = {
                    "checked": True, "concepts": audited, "missed": missed,
                    "share": share, "ok": share >= 0.8,
                    "measured": "now",
                }
            else:
                ledger["depth"] = {"checked": False,
                                   "reason": "no concept content was read"}
        except Exception as e:
            ledger["depth"] = {"checked": False, "reason": str(e)[:120]}

        # TRUTH — the only check here with a model in it, and the only one
        # that is ADVISORY rather than binding.
        #
        # Measured on its own seeded set, reproduced 2026-08-25: it caught 3 of
        # 3 false claims and also flagged 2 of 3 TRUE ones, both needing a
        # single inference step from the passage. High recall on falsehood,
        # poor precision on truth — so an unsupported verdict is a question,
        # not a defect, and it never decides a course's verdict on its own.
        #
        # Both of its false flags were COMPUTABLE claims, which is why the
        # deterministic execution tier runs first: it settles that exact class
        # before this model ever sees it.
        if conn is not None and self.truth_check_enabled:
            try:
                # MEMORY GUARD. The deterministic tiers above are arithmetic
                # over text and cost about 0.3s on a 95-concept course, so they
                # always run. This one calls a model holding roughly 3 GB
                # resident, and it is batch work at the end of a build — the
                # exact thing `allow_background` exists to hold back.
                #
                # It degrades rather than waits. Truth is advisory: a course
                # that ships with it unmeasured is honest, and blocking a
                # finished build behind memory headroom is not a trade worth
                # making for an advisory number.
                from services.common import memory_guard as _mg
                if not _mg.allow_background():
                    ledger["truth"] = {
                        "checked": False,
                        "reason": "skipped under memory pressure: %s"
                                  % (_mg.pressure_reason() or "low memory"),
                    }
                    raise _SkipTruth()
                from services.core import claim_verifier
                verifier = claim_verifier.get_any_verifier()
                ledger["truth"] = course_qa.check_truth(
                    conn, course_uid, verifier=verifier)
            except _SkipTruth:
                pass          # already recorded, with its reason
            except Exception as e:
                ledger["truth"] = {"checked": False, "reason": str(e)[:120]}
        else:
            ledger["truth"] = {"checked": False,
                               "reason": "truth check disabled"}

        report["ledger"] = ledger
        report["ran"] = True
        report["seconds"] = round(_time.time() - t0, 2)

        blocking = report.get("by_severity", {}).get("blocking", 0)
        serious = report.get("by_severity", {}).get("serious", 0)
        # `truth` is left out of the binding set on purpose — see above. It is
        # reported in full and counted separately so nobody has to read this
        # code to know it was measured.
        ledger_failed = sorted(k for k, v in ledger.items()
                               if k != "truth"
                               and v.get("checked") and not v.get("ok"))
        _truth = ledger.get("truth") or {}
        if _truth.get("checked") and not _truth.get("ok"):
            report["truth_advisory"] = {
                "claims": _truth.get("claims"),
                "unsupported": _truth.get("unsupported"),
                "share": _truth.get("share"),
                "note": "advisory — this model flags true claims that need one "
                        "inference step from the passage; treat as a question",
            }
        # UNCHECKED IS NOT CLEAN, and it is recorded as its own state.
        ledger_unrun = sorted(k for k, v in ledger.items()
                              if not v.get("checked"))
        report["ledger_failed"] = ledger_failed
        report["ledger_not_run"] = ledger_unrun

        if blocking:
            report["verdict"] = "blocking_findings"
        elif serious or ledger_failed:
            report["verdict"] = "needs_review"
        elif ledger_unrun or report.get("concepts_not_audited"):
            report["verdict"] = "incomplete"
        else:
            report["verdict"] = "clean"

        logger.info(
            "[AUDIT] %s: %s — %d blocking, %d serious, %d concept(s) with "
            "findings, %.1fs", course.get("title") or course_uid,
            report["verdict"], blocking, serious,
            report.get("concepts_with_findings", 0), report["seconds"])
        if self.status_callback:
            self.status_callback(
                f"AUDIT:DONE:{report['verdict']}:{blocking}:{serious}")

        # Findings are the actionable part; the full list can run to hundreds
        # on a bad course and this rides inside structure.json.
        report["findings"] = report.get("findings", [])[:60]
        return report


    def _repair_one(self, uid, title, course_title, markdown, findings,
                    mastery, domain, sources, escalate=False):
        """One repair attempt, re-checked before it is allowed to count.

        Returns (text, outcome, remaining_findings). `text` is the ORIGINAL
        unless the re-check says the candidate is better — a repair that does
        not verify is not a repair, and storing it on the model's say-so is
        precisely the self-correction failure this design is built around.
        """
        from services.core import course_repair
        from services.core.course_audit import audit_concept

        evidence = course_repair.evidence_for(findings)
        prompt = course_repair.build_prompt(
            markdown, findings, title, course_title, evidence=evidence,
            domain_guidance=self._domain_guidance(domain))

        try:
            raw = llm_generate(
                prompt,
                sys_prompt=course_repair.REPAIR_SYSTEM,
                # Room for the whole document back, not just the fixed lines.
                max_tokens=max(1200, int(len(markdown.split()) * 2.2)),
                # Low temperature: this is a correction, not composition.
                role="build",
            )
        except Exception as e:
            logger.warning("  [REPAIR] '%s' generation failed: %s", title, e)
            return markdown, "failed", findings

        candidate = course_repair.clean_output(raw)
        ok, why = course_repair.is_plausible_repair(markdown, candidate)
        if not ok:
            logger.info("  [REPAIR] '%s' rejected: %s", title, why)
            return markdown, "rejected", findings

        # RE-CHECK WITH THE SAME GATES THAT RAISED THE FINDING.
        #
        # This is the whole safety argument for letting a model rewrite taught
        # content: a wrong repair is caught by the same external checks, so the
        # worst case is a wasted generation rather than a new falsehood in a
        # lesson.
        concept = {"uid": uid, "title": title}
        new_findings, _ran = audit_concept(
            candidate, concept, course_title, mastery, domain, sources=sources)
        before = len(course_repair.repairable(
            [f.as_dict() if hasattr(f, "as_dict") else f for f in findings]))
        after_list = [f.as_dict() for f in new_findings]
        after = len(course_repair.repairable(after_list))

        blocking_after = sum(1 for f in after_list
                             if f.get("severity") == "blocking")
        if blocking_after:
            # A repair that leaves a false claim standing is not progress,
            # whatever else it improved.
            logger.info("  [REPAIR] '%s' still states something false", title)
            return markdown, "still_false", after_list
        if after >= before:
            logger.info("  [REPAIR] '%s' no better (%d -> %d)", title,
                        before, after)
            return markdown, "unchanged", after_list

        logger.info("  [REPAIR] '%s' %d -> %d finding(s)%s", title, before,
                    after, " (escalated)" if escalate else "")
        return candidate, ("escalated" if escalate else "fixed"), after_list

    def _domain_guidance(self, domain):
        """The domain's own voice, so a repaired maths concept does not come
        back written like a history one."""
        if not domain:
            return ""
        try:
            from services.domains import registry
            pack = registry.for_domain(domain)
            line = getattr(pack, "prompt_line", None)
            return line() if callable(line) else (line or "")
        except Exception:
            return ""



    def _book_passage(self, chapter_order, concept_uid, concept_ref_map):
        """This concept's share of its chapter, from a partition of the whole.

        Computed once per chapter and cached: the assignment needs every
        concept of the chapter at once, which is exactly why per-concept
        selection could not see what it was leaving out.
        """
        from services.core.book_source import partition_chapter, passage_for

        cache = getattr(self, "_chapter_partitions", None)
        if cache is None:
            cache = self._chapter_partitions = {}
        if chapter_order not in cache:
            siblings = [
                {"uid": u,
                 "title": (ref or {}).get("title", ""),
                 "learning_objectives": (ref or {}).get(
                     "learning_objectives", [])}
                for u, ref in (concept_ref_map or {}).items()
                if (ref or {}).get("book_chapter") == chapter_order
            ]
            try:
                passages, report = partition_chapter(
                    self.book, chapter_order, siblings)
            except Exception as e:
                logger.warning("[BOOK] partition failed for chapter %s: %s — "
                               "falling back to per-concept selection",
                               chapter_order, e)
                passages, report = {}, None
            cache[chapter_order] = passages
            if report:
                self._chapter_coverage = getattr(self, "_chapter_coverage", {})
                self._chapter_coverage[chapter_order] = report
                logger.info(
                    "[BOOK] chapter %s: %d concept(s) read %.0f%% of %d chars"
                    "%s", chapter_order, report["concepts"],
                    report["coverage"] * 100, report["total_chars"],
                    " (CAPPED, %d chunk(s) unread)" % report["chunks_unread"]
                    if report.get("chunks_unread") else "")

        got = cache.get(chapter_order, {}).get(concept_uid)
        if got:
            return got
        # No partition (missing chapter, or this concept was not in the map):
        # the old behaviour is still better than nothing.
        return passage_for(self.book, chapter_order,
                           (concept_ref_map.get(concept_uid) or {}).get("title", ""),
                           (concept_ref_map.get(concept_uid) or {}).get(
                               "learning_objectives", []))

    _MIN_CONCEPTS_FOR_SHARE = 4

    def _gate_status(self, course, audit):
        """Pass 4.2 — may this course be taught, and under what name.

        Until this existed the audit REPORTED and the course opened anyway.
        "Reading a Query Plan" was status=ready, badged "Passed its build
        checks", with all four concepts carrying nothing but a title and a
        worked example — no Core Explanation, no Misconceptions, no Analogies —
        and a learner was 25% through it.

        WHAT THIS DOES NOT DO IS LOCK A COURSE OVER ONE BAD CONCEPT.
        A false claim is already handled better than a lock: the concept is
        withheld, so it cannot reach anybody, and the other ninety-four are
        untouched. Blocking the whole course would cost the learner far more
        than the defect does.

        So it gates on the two things withholding cannot fix:

          * a blocking finding on a concept that is NOT withheld — the course
            still says something a real database contradicts, to somebody
          * most of the course missing the sections the tutor reads — there is
            no teaching here to do, whatever the status claims

        Returns the status the course should carry, and why.
        """
        if not isinstance(audit, dict) or not audit.get("ran"):
            # An audit that did not run cannot clear a course. It also must not
            # condemn one, so the existing status stands.
            return course.get("status"), "audit did not run"

        # AN AUDIT THAT READ NOTHING MEASURED NOTHING.
        #
        # If not a single concept could be read, every one of them is reported
        # as `missing_content` and the gate condemns the whole course — on the
        # strength of a storage layer that answered nothing, not on the
        # content. That is the same situation as the audit not running, and it
        # gets the same treatment: it can neither clear a course nor condemn
        # one.
        if not audit.get("concepts_audited"):
            return course.get("status"), "audit could read no content"

        withheld = {c.get("uid") for c, _ in self._walk(course)
                    if c.get("withheld")}
        # BLOCKING IS NOT ONE THING, and the gate has to say which.
        #
        # `missing_content` is also blocking, so treating severity alone as
        # "states something false" made the gate report "4 concepts state
        # something a database contradicts" about a course whose concepts had
        # no content at all. A gate that misnames what it found is worse than
        # one that stays quiet: the reader goes looking for a falsehood that
        # was never there.
        false_claims = [f for f in (audit.get("findings") or [])
                        if f.get("severity") == "blocking"
                        and f.get("check") == "executable_claims"
                        and f.get("concept_uid") not in withheld]
        if false_claims:
            return "needs_review", (
                "%d concept(s) state something a database contradicts and are "
                "still being served" % len({f.get("concept_uid")
                                            for f in false_claims}))

        other_blocking = [f for f in (audit.get("findings") or [])
                          if f.get("severity") == "blocking"
                          and f.get("check") != "executable_claims"
                          and f.get("concept_uid") not in withheld]
        if other_blocking:
            checks = sorted({f.get("check") for f in other_blocking})
            return "needs_review", (
                "%d concept(s) have a blocking problem (%s)"
                % (len({f.get("concept_uid") for f in other_blocking}),
                   ", ".join(checks)))

        total = audit.get("concepts_total") or 0
        missing_sections = 0
        for sysf in audit.get("systemic") or []:
            if sysf.get("check") == "tutor_sections":
                missing_sections = max(missing_sections, sysf.get("concepts", 0))
        # A SHARE NEEDS A DENOMINATOR WORTH DIVIDING BY.
        #
        # With one concept, "more than half" is that one concept, and a course
        # too small to have a majority gets condemned by arithmetic rather than
        # by evidence. _fold_systemic already refuses to call anything systemic
        # below four concepts, for the same reason; this uses the same floor.
        if total >= self._MIN_CONCEPTS_FOR_SHARE and missing_sections / total > 0.5:
            return "needs_review", (
                "%d of %d concepts are missing sections the tutor reads — "
                "there is no lesson to teach" % (missing_sections, total))

        if audit.get("concepts_not_audited"):
            return "needs_review", (
                "%d concept(s) have no content"
                % audit["concepts_not_audited"])

        return course.get("status"), ""

    def _run_repair(self, course_uid, course, report):
        """Pass 3 over everything the audit flagged as fixable.

        Bounded on purpose: this is model time at the end of a build the
        learner is already waiting on, and the deterministic half of the audit
        has already told us exactly which concepts are worth spending it on.
        """
        import time as _time
        from services.core import course_repair

        t0 = _time.time()
        by_concept = {}
        for f in report.get("findings") or []:
            if f.get("check") in course_repair.REPAIRABLE_CHECKS:
                by_concept.setdefault(f.get("concept_uid"), []).append(f)
        if not by_concept:
            return {"ran": True, "attempted": 0, "seconds": 0.0}

        # Worst first. If the budget runs out, it runs out on minor findings.
        order = sorted(
            by_concept.items(),
            key=lambda kv: -sum(3 if f.get("severity") == "blocking"
                                else 2 if f.get("severity") == "serious" else 1
                                for f in kv[1]))
        budget = self.repair_budget
        if budget and len(order) > budget:
            logger.info("[REPAIR] %d concept(s) flagged, repairing the worst "
                        "%d — the rest are reported, not hidden",
                        len(order), budget)
            order = order[:budget]

        titles = {c.get("uid"): c.get("title", "")
                  for c, _ in self._walk(course)}
        course_title = course.get("title") or ""
        mastery = self.mastery_level
        domain = course.get("teaching_domain") or self.topic_domain
        outcomes = {"fixed": 0, "escalated": 0, "unchanged": 0,
                    "rejected": 0, "failed": 0, "still_false": 0,
                    "withheld": 0}
        withheld = []

        for uid, findings in order:
            # MEMORY AND FAIRNESS. Every attempt is a model call, so this asks
            # the same question the truth tier does — and stops rather than
            # degrades, because a partial repair pass is a normal outcome and
            # the audit already recorded what was found.
            try:
                from services.common import memory_guard as _mg
                if not _mg.allow_background():
                    logger.info("[REPAIR] stopping under memory pressure: %s",
                                _mg.pressure_reason() or "low memory")
                    break
            except Exception:
                pass

            title = titles.get(uid) or ""
            try:
                markdown = self.storage.courses.get_concept_content(
                    course_uid, uid)
            except Exception:
                markdown = None
            if not markdown:
                continue

            sources = self._sources_for(course_uid, uid)
            if self.status_callback:
                self.status_callback(f"REPAIR:CONCEPT:{title}")

            text, outcome, remaining = self._repair_one(
                uid, title, course_title, markdown, findings, mastery,
                domain, sources)

            # ESCALATE. The small model failed; ask the builder model, with the
            # same evidence, once.
            if outcome in ("unchanged", "rejected", "still_false", "failed"):
                text, outcome, remaining = self._repair_one(
                    uid, title, course_title, markdown, remaining or findings,
                    mastery, domain, sources, escalate=True)

            if outcome in ("fixed", "escalated"):
                try:
                    self.storage.courses.save_concept_content(
                        course_uid, uid, text)
                except Exception as e:
                    logger.warning("  [REPAIR] could not store '%s': %s",
                                   title, e)
                    outcome = "failed"

            outcomes[outcome] = outcomes.get(outcome, 0) + 1

            # WITHHELD. A concept that still states something a database
            # contradicts is not served. A gap in a course is a worse course;
            # a false claim is a lie told to someone who trusted it.
            blocking_left = [f for f in (remaining or [])
                             if f.get("severity") == "blocking"]
            if blocking_left:
                # NAME THE FINDING THAT CAUSED THIS, not whichever came first.
                #
                # It took remaining[0], so a concept withheld for a false claim
                # was reported as withheld because "the text contains your own
                # deliberation" — the reason a reader would act on, and the
                # wrong one. Observed on the first live run.
                withheld.append({"concept_uid": uid, "title": title,
                                 "why": blocking_left[0].get("detail"),
                                 "check": blocking_left[0].get("check")})
                outcomes["withheld"] += 1

        if withheld:
            self._mark_withheld(course, withheld)

        result = {
            "ran": True,
            "attempted": len(order),
            "outcomes": outcomes,
            "withheld": withheld[:25],
            "seconds": round(_time.time() - t0, 2),
        }
        logger.info("[REPAIR] %d attempted: %s (%.0fs)", len(order),
                    ", ".join(f"{k}={v}" for k, v in outcomes.items() if v),
                    result["seconds"])
        if self.status_callback:
            self.status_callback(
                f"REPAIR:DONE:{outcomes.get('fixed', 0)}:"
                f"{outcomes.get('escalated', 0)}:{outcomes.get('withheld', 0)}")
        return result

    def _walk(self, course):
        from services.core.course_audit import walk_concepts
        return walk_concepts(course)

    def _sources_for(self, course_uid, concept_uid):
        conn = self._ledger_conn()
        if conn is None:
            return []
        try:
            return [{"title": r[0], "url": r[1], "passage": r[2], "type": r[3]}
                    for r in conn.execute(
                        "SELECT title, url, passage, source_type FROM sources "
                        "WHERE course_uid=? AND concept_uid=?",
                        (course_uid, concept_uid))]
        except Exception:
            return []

    def _mark_withheld(self, course, withheld):
        """Flag the concepts the learner must not be shown.

        Written onto the concept node so every reader sees it — the path view,
        the session, and search — rather than only whoever remembers to open
        the audit report.
        """
        flagged = {w["concept_uid"] for w in withheld}
        for concept, _path in self._walk(course):
            if concept.get("uid") in flagged:
                concept["withheld"] = True
                concept["withheld_reason"] = next(
                    (w.get("why") for w in withheld
                     if w["concept_uid"] == concept.get("uid")), "")

    def _fetch_more_evidence(self, title, h_ctx, course_title, existing):
        """One broadened research pass for a concept whose content fell short.

        Returns None rather than raising: failing to find more evidence is a
        normal outcome and must not end a build. The caller keeps what it had.
        """
        research_url = os.getenv("RESEARCH_URL", "http://helga-research:5006")
        try:
            resp = requests.post(
                f"{research_url}/api/research_concept",
                json={
                    # The parent topic, not the narrow concept framing — a
                    # concept-specific query is what returned too little the
                    # first time.
                    "title": f"{title} {h_ctx.get('module', '')}".strip(),
                    "module_title": h_ctx.get("module", ""),
                    "course_title": course_title,
                    "mastery": self.mastery_level,
                    "broaden": True,
                },
                timeout=RESEARCH_TIMEOUT_S,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
        except Exception as e:
            logger.warning("  [RESEARCH] refetch failed for %r: %s", title, e)
            return None

        sources = data.get("sources") or []
        # Only worth using if it actually brought something new. Comparing by
        # url keeps a re-fetch that returned the same page from looking like
        # progress.
        seen = {(s or {}).get("url") for s in (existing or []) if isinstance(s, dict)}
        fresh = [s for s in sources if isinstance(s, dict) and s.get("url") not in seen]
        if not fresh:
            return None
        return {
            "sources": (existing or []) + fresh,
            "text": data.get("combined_text") or "",
            "confidence": float(data.get("confidence") or 0.0),
        }

    def _retain_grounding(self, conn, course_uid, concept_uid, markdown,
                          grounding_text, now):
        """Store what the model was shown, and a fingerprint of what it wrote.

        These answer two questions nothing else can. The grounding text
        separates "the source was wrong" from "the model invented it" — without
        it a false claim gives no clue which. The hash says whether a verdict
        still describes the file it was written about, which matters the moment
        anything repairs or edits a concept.
        """
        import hashlib
        import zlib

        cur = conn.cursor()
        if grounding_text:
            blob = zlib.compress(grounding_text.encode("utf-8", "replace"), 6)
            cur.execute(
                "INSERT OR REPLACE INTO grounding_context "
                "(course_uid, concept_uid, text_z, chars, recorded_at) "
                "VALUES (?,?,?,?,?)",
                (course_uid, concept_uid, blob, len(grounding_text), now))
        if markdown:
            digest = hashlib.sha256(markdown.encode("utf-8", "replace")).hexdigest()
            cur.execute(
                "INSERT OR REPLACE INTO concept_content_hash "
                "(course_uid, concept_uid, sha256, chars, recorded_at) "
                "VALUES (?,?,?,?,?)",
                (course_uid, concept_uid, digest, len(markdown), now))

    def _retain_sources(self, course_uid, concept_uid, markdown,
                        research_sources, supplementary_books=None,
                        grounding_text=None, evidence_sources=None):
        """Keep the passages a concept was built from, and link them to claims.

        The research cache is a SPEED layer with a 24h/7d TTL; a claim cannot be
        verified against a passage that has expired, so the originals need a
        durable home. `degraded` carries the absent-vs-zero distinction through:
        a retained row with no text is a source we fetched and got nothing from,
        which is not the same as a source we never fetched.

        The claim->source links are also what turn the supplementary policy from
        an assertion into a measurement: the share that matters is of claims
        resting only on below-bar material, not of sources in a list.
        """
        conn = self._ledger_conn()
        if conn is None or not (research_sources or evidence_sources):
            return
        try:
            from services.core.taught_ledger import extract_claims
        except ImportError:
            try:
                from taught_ledger import extract_claims
            except ImportError:
                return
        try:
            supp = {(b or "").lower() for b in (supplementary_books or [])}
            from datetime import datetime as _dt
            now = _dt.now().isoformat()
            cur = conn.cursor()
            cur.execute("DELETE FROM sources WHERE course_uid=? AND concept_uid=?",
                        (course_uid, concept_uid))
            cur.execute("DELETE FROM claim_sources WHERE course_uid=? AND concept_uid=?",
                        (course_uid, concept_uid))
            ids = []
            # Evidence rides through the same loop and is separated by its
            # `cited` flag, so there is one write path and one place for the
            # passage-truncation rule to live.
            for s in list(research_sources or []) + list(evidence_sources or []):
                if not isinstance(s, dict):
                    continue
                text = s.get("snippet") or s.get("text") or s.get("passage") or ""
                cur.execute(
                    "INSERT INTO sources (course_uid, concept_uid, title, url, "
                    "passage, source_type, domain_tier, grounding, degraded, "
                    "retrieved_at, cited) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (course_uid, concept_uid, s.get("title"), s.get("url"),
                     text[:4000], s.get("type"), s.get("domain_tier"),
                     s.get("grounding"), 1 if s.get("search_degraded") else 0,
                     now, 0 if s.get("cited") is False else 1))
                if s.get("cited") is not False:
                    ids.append((cur.lastrowid,
                                (s.get("title") or "").lower() in supp))
            # Attribution here is coarse on purpose: it records THAT a concept's
            # claims rest on this source set, not which sentence came from which
            # passage. Span-level attribution needs the generator to cite as it
            # writes, which is a later change; this is enough to measure the
            # supplementary share and to give a verifier something to check
            # against.
            only_supp = bool(ids) and all(is_supp for _, is_supp in ids)
            for c in extract_claims(markdown):
                cur.execute(
                    "INSERT INTO claim_sources (course_uid, concept_uid, claim, "
                    "source_id, supplementary) VALUES (?,?,?,?,?)",
                    (course_uid, concept_uid, c[:400],
                     ids[0][0] if ids else None, 1 if only_supp else 0))
            conn.commit()
        except Exception as e:
            logger.debug(f"[SOURCES] retention skipped for {concept_uid}: {e}")

    def supplementary_claim_share(self, course_uid):
        """Share of claims resting ONLY on below-bar sources. None if unknown.

        The unit the policy should have used from the start: one weak book can
        dominate a course's content while being a small minority of its source
        list.
        """
        conn = self._ledger_conn()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT COUNT(*), SUM(supplementary) FROM claim_sources "
                "WHERE course_uid=?", (course_uid,)).fetchone()
            total, supp = (row or (0, 0))
            if not total:
                return None
            return {"claims": total, "supplementary_only": supp or 0,
                    "share": round((supp or 0) / total, 3),
                    "cap": SUPPLEMENTARY_MAX_SHARE,
                    "within_cap": (supp or 0) / total <= SUPPLEMENTARY_MAX_SHARE}
        except Exception as e:
            logger.debug(f"[SOURCES] share query failed: {e}")
            return None

    def _record_taught(self, course_uid, concept_uid, title, markdown,
                       ordinal, module="", lesson=""):
        """Add a validated concept to the ledger, and report if it repeated."""
        conn = self._ledger_conn()
        if conn is None:
            return None
        try:
            from services.core.taught_ledger import (
                record_concept, check_redundancy)
        except ImportError:
            try:
                from taught_ledger import record_concept, check_redundancy
            except ImportError:
                return None
        try:
            red = check_redundancy(conn, course_uid, concept_uid, markdown, ordinal)
            if not red.get("ok"):
                # Recorded as a defect rather than silently accepted. The count
                # is what a later QA pass reads; a build that quietly repeats
                # itself is the failure mode this whole mechanism exists for.
                self.redundant_concepts = getattr(self, "redundant_concepts", [])
                self.redundant_concepts.append({
                    "concept_uid": concept_uid, "title": title,
                    "reintroduced": len(red.get("reintroduced") or []),
                    "share": red.get("reintroduced_share"),
                })
                logger.warning(
                    f"  [LEDGER] {title!r} re-introduces "
                    f"{len(red.get('reintroduced') or [])} claim(s) already "
                    f"taught (share {red.get('reintroduced_share')})")
                if self.status_callback:
                    self.status_callback(f"STRUCT:REDUNDANT:{title}")
            record_concept(conn, course_uid, concept_uid, title, markdown,
                           ordinal, module=module, lesson=lesson)
            self._store_teaching_object(conn, course_uid, concept_uid, title,
                                        markdown, ordinal=ordinal)
            self._store_math_speech(course_uid, concept_uid, title, markdown)
            return red
        except Exception as e:
            logger.debug(f"[LEDGER] record failed for {title!r}: {e}")
            return None

    def _condense_and_structure_content(
        self,
        title: str,
        raw_text: str,
        course_title: str,
        depth: int,
        complexity_role: str,
        source: str,
        hierarchy_context: dict = None,
        previous_concepts: list = None,
        module_concepts: list = None,
        research_sources: list = None,
        research_confidence: float = 0.0,
        user_note: str = "",
        bloom_level: int = 3,
        learning_objectives: list = None,
        prerequisite_titles: list = None,
        research_structured: dict = None,
        ledger_context: str = "",
    ) -> str:
        """Transforms raw crawl data into structured Markdown for Socratic tutoring, Flashcards, and Review.

        `ledger_context` carries what the COURSE has already taught, from
        `taught_ledger`. The existing deduplication block below is titles only
        and scoped to the lesson and module; it cannot see that a concept four
        modules back already explained the same thing, which is the measured
        failure this parameter exists to close.
        """
        logger.info(f"  [MARKDOWN] Structuring {title} (Depth: {depth})...")

        h_str = ""
        context_path = course_title
        if hierarchy_context:
            h_str = f"Module: {hierarchy_context.get('module')}\nUnit: {hierarchy_context.get('unit')}\nLesson: {hierarchy_context.get('lesson')}"
            context_path = f"{course_title} > {hierarchy_context.get('module', '')} > {hierarchy_context.get('unit', '')} > {hierarchy_context.get('lesson', '')}"

        prev_str = ", ".join(previous_concepts[-5:]) if previous_concepts else "None"
        module_prev_str = (
            ", ".join(module_concepts[-15:]) if module_concepts else "None"
        )
        profile = DEPTH_PROFILES.get(depth, DEPTH_PROFILES[2])
        # Use three-slider mastery if available, fall back to depth
        mastery_level = getattr(self, 'mastery_level', depth)
        mastery_profile = MASTERY_PROFILES.get(mastery_level, MASTERY_PROFILES.get(depth, MASTERY_PROFILES[2]))
        depth_desc = f"{mastery_profile['label']} ({mastery_profile['vocabulary']})"
        writing_guide = mastery_profile['writing']

        # Bloom-level labels for calibrated content
        bloom_labels = {1: "Remember", 2: "Understand", 3: "Apply", 4: "Analyze", 5: "Evaluate", 6: "Create"}
        bloom_label = bloom_labels.get(bloom_level, "Apply")

        brief = getattr(self, "learner_context", "")
        sys_prompt = (
            f"Expert Educational Content Architect specializing in {course_title}. "
            + (f"The learner said what they want from this course, in their own "
               f"words: \"{brief}\" Let it decide which sense of the subject "
               f"this concept is taught in, which examples are worth the space, "
               f"and what to leave out. It does not lower the bar and it does "
               f"not change the required depth. "
               if brief else "")
            + f"Writing level: {depth_desc}. {writing_guide} "
            f"Target cognitive level: Bloom {bloom_level} ({bloom_label}). "
            "Calibrate all content to this level. "
            "Build a Teaching Guide using ONLY real, established knowledge. "
            "Never invent theorems, axioms, or method names. Output Markdown directly."
        )

        # Build structured research injection (replaces raw blob)
        research_input = ""
        rs = research_structured or {}
        if rs.get("key_facts"):
            research_input += f"\n### KEY FACTS (from verified sources — synthesize into Key Facts section):\n{rs['key_facts']}\n"
        if rs.get("examples"):
            research_input += f"\n### REAL-WORLD EXAMPLES (from sources — synthesize into Examples section):\n{rs['examples']}\n"
        if rs.get("edge_cases"):
            research_input += f"\n### EDGE CASES (from sources — use in Edge Cases section):\n{rs['edge_cases']}\n"
        remainder = rs.get("remainder", raw_text or "")
        source_material = remainder[:3000] if remainder else "Use your internal knowledge."
        if not research_input and raw_text:
            source_material = raw_text[:5000]

        # Build static sections (prepended in Python, not LLM-generated)
        obj_lines = ""
        if learning_objectives:
            obj_lines = "\n".join(f"- {o}" for o in learning_objectives[:5])
        else:
            obj_lines = f"- Understand the core principles of {title}"

        prereq_str = ""
        if prerequisite_titles:
            prereq_str = ", ".join(prerequisite_titles[-5:])
        else:
            prereq_str = "None (first concept)"

        static_header = f"""# {title}

## Metadata
- **Bloom Target**: {bloom_level} ({bloom_label})
- **Depth**: {depth}
- **Path**: {context_path}
- **Complexity**: {complexity_role}
- **Source**: {source}

## Learning Objectives
{obj_lines}

## Prerequisites
Prior concepts: {prereq_str}
"""

        # Bloom-calibrated mastery criteria template
        mastery_templates = {
            1: "- Recalls the definition accurately\n- Identifies key terms related to the concept",
            2: "- Explains the concept in their own words\n- Gives a correct example",
            3: "- Applies the concept to solve a new problem\n- Explains WHY the method works in their scenario",
            4: "- Breaks down the concept into components and explains relationships\n- Compares with related concepts and identifies trade-offs",
            5: "- Evaluates when the concept works and when it fails\n- Defends a position with evidence and reasoning",
            6: "- Proposes a novel application or extension\n- Synthesizes multiple concepts into an original framework",
        }
        mastery_criteria_hint = mastery_templates.get(bloom_level, mastery_templates[3])

        # Bloom-calibrated core explanation instruction
        core_instructions = {
            1: "1-2 sentence simple definition in plain language. No jargon.",
            2: "1-2 sentence precise definition with key terms explained.",
            3: "Precise definition that distinguishes this from related concepts. Include method/procedure.",
            4: "Formal definition with standard notation or criteria. Include assumptions and conditions.",
            5: "Formal definition with notation. Include theoretical foundations and boundary conditions.",
        }
        core_inst = core_instructions.get(depth, core_instructions[3])

        # Word budget, taken from the DEPTH CONTRACT so the instruction and the
        # validator cannot disagree.
        #
        # Two bugs lived here:
        #  1. This was a private table {1:150, 2:200, 3:250, 4:300, 5:400} that
        #     conflicted with MASTERY_PROFILES["content_words"]
        #     (150/250/400/600/800) — and content_words was never read into any
        #     prompt at all, so the mastery slider's length setting had NEVER
        #     reached the model.
        #  2. It budgeted only the "Core Explanation" SECTION while the contract
        #     measures the WHOLE document. ~200 words of core explanation plus
        #     eight other sections produced a 951-word median against a 200-550
        #     band, failing "too long" on 10 of 12 concepts.
        # So the model is now given the total-document band it is judged on.
        try:
            from services.core.depth_contract import contract_for
            _c = contract_for(depth, course_title)
            _wmin, _wmax = _c["word_min"], _c["word_max"]
        except Exception:
            _wmin, _wmax = 200, 550
        word_target = max(80, int((_wmin + _wmax) / 2 * 0.35))  # core section share
        total_budget_note = (
            f"\n\nLENGTH BUDGET (HARD): the COMPLETE document — every section "
            f"combined — must be between {_wmin} and {_wmax} words. This is "
            f"checked automatically and a document outside the band is "
            f"rejected. Keep every section terse to stay inside it; do not pad."
        )

        # Sections the DEPTH CONTRACT requires at higher levels but the base
        # template never asked for.
        #
        # A tier probe showed mastery 4 failing on named_result and
        # derivation_or_proof, and mastery 5 on exercise — not because the model
        # could not produce them, but because nothing requested them. Exactly
        # the bug already found with worked_example: the validator demanded an
        # element the prompt never mentioned, so the level was unreachable by
        # construction and its preset was advertising falsely.
        #
        # Kept OUT of the base template deliberately: a beginner lesson should
        # not carry a proof and an exercise set. The sections appear only where
        # the contract requires them.
        advanced_sections = ""
        if depth >= 4:
            advanced_sections += """

## Governing Result
[Name the theorem, law, or principle this rests on, then STATE it precisely.
Name it — "the spectral theorem", "Bayes' rule" — do not merely allude to it.]

## Derivation
[Derive the key result step by step from stated premises. Each step must follow
from the one before. If a full proof does not fit, derive the central step and
say what is being assumed.]"""
        if depth >= 5:
            advanced_sections += """

## Exercise
[One non-trivial problem the learner should attempt, with enough setup to be
answerable. Do NOT include the solution.]"""

        # ASK FOR WHAT WE ARE ABOUT TO JUDGE.
        #
        # The depth contract requires `formal_definition` from mastery 3 up,
        # and "## Core Explanation" never mentioned one. Measured on a live
        # build: 8 of 8 consecutive concepts missed formal_definition on the
        # first attempt and paid for a full regeneration — a 5600-token prompt
        # and ~90s each — to be told something the first prompt could have
        # asked for. That is half the build time spent discovering a
        # requirement we already knew.
        #
        # The instruction names the form the detector recognises, because
        # "state a definition" and "write **Definition.**" are the same
        # request to a person and different requests to a regex.
        definition_line = ""
        try:
            from services.core.depth_contract import contract_for as _contract_for
            _c = _contract_for(self.mastery_level, course_title, self.topic_domain)
            _req = _c.get("required") or []
            if "formal_definition" in _req:
                definition_line = (
                    "\nOpen with a one-sentence formal definition on its own "
                    "line, in exactly this form:\n"
                    "**Definition.** <the term> is <the precise definition>.\n"
                    "It must be a definition, not a restatement of the title.\n")
            # Same reasoning as the definition hint, for the elements the
            # higher levels add. Measured before adding these: across 12
            # concepts of a mastery-3 build, derivation_or_proof appeared in 3
            # — not because the subject cannot support one, but because
            # nothing asked. A proficiency-level concept that explains WHY a
            # behaviour follows is better teaching as well as a passing one.
            if "derivation_or_proof" in _req:
                definition_line += (
                    "\nInclude a short derivation: take the behaviour you have "
                    "just defined and show, step by step, why it follows — "
                    "'Step 1 ... Step 2 ... therefore ...'. Reason from stated "
                    "premises to the result; do not assert the result twice.\n")
            if "primary_source" in _req:
                definition_line += (
                    "\nCite the NORMATIVE source for this behaviour — the "
                    "language standard or the implementation's own reference "
                    "documentation, not a blog post or a general encyclopaedia "
                    "entry. GIVE THE URL, not just the name: write "
                    "https://www.postgresql.org/docs/current/<page>.html . "
                    "Naming 'the PostgreSQL documentation' in prose is not a "
                    "citation a reader can follow.\n")
        except Exception as e:
            logger.debug("contract lookup for the definition hint failed: %s", e)

        # Build the LLM-generated section template
        section_template = f"""## Mastery Criteria
At Bloom {bloom_level} ({bloom_label}), the student demonstrates mastery by:
{mastery_criteria_hint}
Grade 3 requires: [Write one sentence describing the specific threshold for THIS concept]

## Core Explanation
[{core_inst} ~{word_target} words.]{definition_line}

## Key Facts
[3-5 bullet points of verified facts. Use the KEY FACTS input if available, otherwise use your knowledge.]

## Real-World Examples
[ONE WORKED example, carried through to a result — not a description of where
this gets used. State concrete values and show the steps: "Step 1: let a = 3,
b = 4. Step 2: we compute a² + b² = 9 + 16 = 25. Step 3: c = 5." Naming a field
where the idea applies is NOT a worked example and will be rejected. Then, if
useful, one short real-world context sentence.]

## Misconceptions
- **Belief**: [A common misconception about this concept]
  **Correction**: [Why it's wrong and what's correct]

## Edge Cases & Limitations
[When does this concept break down? Use EDGE CASES input if available. 2-3 bullet points.]

## Socratic Hooks
- Bloom 1-2: [A simple scenario question for beginners]
- Bloom 3-4: [An application or analysis question]
- Bloom 5-6: [An evaluation or synthesis question]

## Analogies
- **Simple**: [An everyday analogy accessible to anyone]
- **Technical**: [A domain-specific analogy for advanced learners]{advanced_sections}"""

        # PROMPT ORDER IS INVARIANT-FIRST, FOR PREFIX CACHING.
        #
        # Ollama reuses the KV cache across requests that share a BYTE-IDENTICAL
        # leading prefix. This prompt used to open with the concept title and
        # close with the section template — exactly backwards, so the largest
        # fixed block (the template, several hundred tokens) was re-prefilled
        # for every one of a course's ~104-135 concepts.
        #
        # Inverted, the writing level, length budget and section template are
        # shared by every concept at the same depth and bloom level, so they are
        # prefilled once per bucket instead of once per concept. Measured
        # prefill on this machine is 247 tok/s, so this is real time on a
        # 3-hour hydration.
        #
        # The cache is byte-exact: any variation in this block silently costs
        # the hit with no error and no log line, so nothing per-concept may be
        # interpolated above the THIS CONCEPT marker. `bloom_level` does appear
        # in the template, which fragments the cache into one bucket per level
        # rather than defeating it — concepts within a module share a level.
        user_prompt = f"""### WRITING LEVEL: {writing_guide}

### OUTPUT FORMAT
Generate ONLY the sections below, in this order. Do NOT generate Metadata,
Learning Objectives, or Prerequisites — those are pre-filled.
{total_budget_note}

{section_template}

### THIS CONCEPT
Topic: {title} | Course: {course_title} | Depth: {depth}/5 ({depth_desc})
{h_str}

### DEDUPLICATION — DO NOT REPEAT content already covered:
- Lesson concepts: {prev_str}
- Module concepts: {module_prev_str}
Focus ONLY on what makes "{title}" DISTINCT.
{ledger_context}
{("You may ASSUME the material above and refer to it, or ask the learner to "
  "recall it. Re-explaining it from scratch is the defect to avoid — building "
  "on it is not." if ledger_context else "")}
{f"### USER NOTE:{chr(10)}{user_note}{chr(10)}" if user_note else ""}{research_input}
Source Material: {source_material}

Now write the sections for "{title}".
"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Scale the output budget with the TEMPLATE, not a constant.
                # At mastery 5 the template carries 11 sections (Governing
                # Result, Derivation and Exercise are added on top of the base
                # nine); a flat 2500 truncated it so badly the model emitted
                # only 4 headings and the rest were injected as stubs — which
                # then failed the contract for `exercise`, blaming the model for
                # our own ceiling. Same class as the reasoning-mode and
                # concept-list truncation bugs found earlier.
                _sections = section_template.count("\n## ") + 1
                _budget = max(2500, 400 * _sections + int(_wmax * 1.6))
                llm_output = llm_generate(
                    user_prompt,
                    sys_prompt=sys_prompt,
                    max_tokens=_budget,
                    progress_callback=self.status_callback,
                )
                # Accept any substantive response and let the validator repair
                # gaps, rather than discarding it wholesale.
                #
                # This previously required the literal "## Mastery Criteria" or
                # "## Core" and threw everything else away — silently, with no
                # log line. Two of nine concepts in a real run produced usable
                # prose that simply lacked those exact headings and were
                # replaced by a 154-word stub, which was then the ONLY thing
                # failing the quality gate. _validate_markdown_structure already
                # injects missing sections, so throwing the draft away discarded
                # work the repair path was built to handle.
                if llm_output and len(llm_output.split()) >= 40:
                    if not ("## Mastery Criteria" in llm_output
                            or "## Core" in llm_output):
                        logger.warning(
                            f"  [MARKDOWN] {title}: response lacked expected "
                            f"headings; repairing rather than discarding "
                            f"({len(llm_output.split())} words)")
                    full_md = static_header + "\n" + llm_output
                    return self._validate_markdown_structure(
                        full_md,
                        title,
                        course_title,
                        depth,
                        source,
                        raw_text,
                        context_path,
                    )
                logger.warning(
                    f"  [MARKDOWN] {title} (att {attempt + 1}): unusable output "
                    f"({len((llm_output or '').split())} words)")
            except Exception as e:
                logger.warning(f"  [MARKDOWN] Failed {title} (att {attempt + 1}): {e}")

        return static_header + "\n## Core Explanation\n[Hydration failed]\n"

    def _chunk_text(self, text: str, chunk_size: int = 300) -> List[str]:
        """Split text into chunks of approximately chunk_size words."""
        if not text:
            return []
        words = text.split()
        chunks = []
        for i in range(0, len(words), chunk_size):
            chunks.append(" ".join(words[i : i + chunk_size]))
        return chunks

    def _validate_markdown_structure(
        self,
        content: str,
        title: str,
        course_title: str,
        depth: int,
        source: str,
        raw_text: str,
        context_path: str,
    ) -> str:
        """Validate that LLM output contains all required Markdown sections. Inject stubs for missing ones."""
        # THE PIPELINE WAS FIGHTING ITSELF.
        #
        # Every stub here is content-free by construction — the title restated
        # as an objective, "can be found in everyday applications", "See
        # further reading on". content_guards rejects exactly that wording, so
        # the loop became: model omits a section -> pipeline injects
        # boilerplate -> guard flags the boilerplate -> retry -> model omits it
        # again -> inject again -> store it flagged. Measured on the live
        # build: 10 of 39 concepts carrying injected placeholder wording.
        #
        # A missing section is a GENERATION problem and the retry loop is where
        # it belongs. What is left here is the structural minimum the readers
        # need to not crash on a missing heading, with no invented content in
        # it: an empty section is honest and a fabricated one is not.
        required_sections = {
            # NO STUB FOR THE SECTION THAT IS THE LESSON.
            #
            # This injected "{title} is a key concept in {course_title}." when
            # the model omitted the heading, and the concept then passed every
            # structural check while teaching nothing — six of nine concepts in
            # one course, all of them scoring as complete. A missing Core
            # Explanation is a failed generation, and content_guards reports it
            # so the existing retry loop regenerates instead.
            # Likewise: "Belief: None identified / Correction: N/A" is a
            # misconception section with no misconception in it, and the tutor
            # reads this section before every turn.
        }


        # Report what is missing so the caller's retry can name it; do not
        # paper over it. The guards will fail this body if a section the tutor
        # reads is absent, which is the correct outcome.
        wanted = ["## Mastery Criteria", "## Core Explanation", "## Misconceptions",
                  "## Socratic Hooks"]
        if depth >= 2:
            wanted += ["## Key Facts", "## Real-World Examples"]
        if depth >= 3:
            wanted += ["## Edge Cases & Limitations"]
        missing = [h for h in wanted if h not in content]
        if missing:
            logger.warning(
                "  [MARKDOWN] %s is missing %s — regenerating rather than "
                "filling it with boilerplate", title, ", ".join(missing))
        return content


class SyllabusAuditor:
    """
    Second-pass LLM Auditor to prune, rename, and reorder course structure
    before expensive content hydration begins.
    Now operates on JSON structure instead of KuzuDB.
    """

    def __init__(
        self, db_path: str = None, status_callback=None, storage: StorageManager = None
    ):
        self.db_path = db_path
        self.status_callback = status_callback
        if storage:
            self.storage = storage
        else:
            data_dir = os.path.dirname(db_path) if db_path else DATA_ROOT
            self.storage = StorageManager(data_dir)

    def close(self):
        pass

    # Stopwords excluded from semantic comparison of concept titles
    DEDUP_STOPWORDS = frozenset({
        "in", "of", "for", "the", "and", "via", "with", "a", "an", "to", "by",
        "on", "at", "is", "are", "as", "or", "its", "their", "how", "what",
        "using", "through", "between", "from",
    })

    def _stem(self, word: str) -> str:
        """Crude suffix stripping for dedup comparison (not linguistic stemming)."""
        if word.endswith("tion"):
            return word[:-4] or word
        if word.endswith("ing") and len(word) > 5:
            return word[:-3]
        if word.endswith("ment") and len(word) > 6:
            return word[:-4]
        if word.endswith("ness") and len(word) > 6:
            return word[:-4]
        if word.endswith("ies"):
            return word[:-3] + "y"
        if word.endswith("es") and len(word) > 4:
            return word[:-2]
        if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
            return word[:-1]
        return word

    def _tokenize_title(self, title: str) -> set:
        """Extract meaningful stemmed non-stopword tokens from a title."""
        words = re.findall(r'[a-z]+', title.lower())
        return {self._stem(w) for w in words if w not in self.DEDUP_STOPWORDS and len(w) > 1}

    # Tokens that name no concept of their own — packaging around a topic, not
    # a topic. If the ONLY difference between two titles is one of these, the
    # two titles are the same concept ("Photosynthesis" / "Introduction to
    # Photosynthesis"). Stored ALREADY STEMMED, because that is what
    # _tokenize_title produces ("introduction" -> "introduc").
    DEDUP_FILLER_TOKENS = frozenset({
        "introduc", "intro", "overview", "basic", "fundamental", "essential",
        "concept", "principle", "understand", "explained", "explana", "defini",
        "definition", "primer", "review", "summary", "topic", "study", "part",
        "section", "further", "detail", "more",
    })

    # A pair must be this alike (Jaccard) before dedup will touch it. Two
    # three-token titles that differ in a single token score 0.5; the real
    # near-identical pairs this pass exists for score 1.0.
    DEDUP_SIMILARITY_THRESHOLD = 0.75

    # Hard ceiling on what this heuristic may remove from one module. Dedup
    # deleting a third of a module is not a module full of duplicates, it is the
    # heuristic misfiring — and it misfires silently, before hydration, where
    # nothing downstream can tell the syllabus was quietly cut.
    DEDUP_MAX_MODULE_REMOVAL_RATIO = 0.25

    def _word_overlap_ratio(self, tokens_a: set, tokens_b: set) -> float:
        """Jaccard similarity between two title token sets: |A ∩ B| / |A ∪ B|.

        Dividing by the UNION, not by the smaller set. The old ratio divided by
        the smaller set, which made every pair of two-word titles sharing one
        word score 0.50 — and at the old 0.4 threshold "Linear Regression" and
        "Logistic Regression" were 'duplicates', as were "Ordinary Differential
        Equations" and "Partial Differential Equations" (0.67). Distinct
        technical terms sharing a head noun are what a real syllabus looks like;
        the shared noun is the subject, and the differing word is the concept.
        Jaccard scores those pairs 0.33 and 0.50 — below the threshold, kept.
        """
        if not tokens_a or not tokens_b:
            return 0.0
        union = tokens_a | tokens_b
        return len(tokens_a & tokens_b) / len(union) if union else 0.0

    def _titles_are_duplicates(self, tokens_a: set, tokens_b: set) -> bool:
        """True only when two titles name the same concept.

        Two ways to qualify, and a title needs just one:
          * identical token sets, or a symmetric difference made up entirely of
            filler ("Pythagorean Theorem" vs "The Pythagorean Theorem"), or
          * Jaccard similarity at or above DEDUP_SIMILARITY_THRESHOLD.
        Anything else — including a pair whose differing tokens are themselves
        meaningful words — is two concepts that happen to share vocabulary.
        """
        if not tokens_a or not tokens_b:
            return False
        if tokens_a == tokens_b:
            return True
        difference = tokens_a ^ tokens_b
        if difference and all(t in self.DEDUP_FILLER_TOKENS for t in difference):
            return True
        return self._word_overlap_ratio(tokens_a, tokens_b) >= self.DEDUP_SIMILARITY_THRESHOLD

    def _programmatic_dedup(self, course: dict) -> int:
        """Pass 1: Remove semantic duplicates within each module using word overlap.

        Conservative by construction: a pair must pass _titles_are_duplicates,
        and no module may lose more than DEDUP_MAX_MODULE_REMOVAL_RATIO of its
        concepts to the heuristic. Returns the number of concepts deleted.
        """
        total_deleted = 0

        for module in course.get("modules", []):
            # Collect all concepts across all units/lessons in this module
            all_concepts = []  # list of (lesson_concepts_list, index, concept_dict, tokens)
            for unit in module.get("units", []):
                for lesson in unit.get("lessons", []):
                    concepts_list = lesson.get("concepts", [])
                    for idx, concept in enumerate(concepts_list):
                        tokens = self._tokenize_title(concept.get("title", ""))
                        all_concepts.append((concepts_list, idx, concept, tokens))

            # Compare every pair; the later occurrence is the candidate for removal.
            # Candidates are collected rather than deleted on sight, so the
            # module-wide budget below can be applied to the set as a whole.
            candidates = []  # (ratio, dup_uid, dup_title, orig_title)
            candidate_uids = set()
            for i in range(len(all_concepts)):
                if all_concepts[i][2]["uid"] in candidate_uids:
                    continue
                for j in range(i + 1, len(all_concepts)):
                    if all_concepts[j][2]["uid"] in candidate_uids:
                        continue
                    tokens_i, tokens_j = all_concepts[i][3], all_concepts[j][3]
                    if self._titles_are_duplicates(tokens_i, tokens_j):
                        ratio = self._word_overlap_ratio(tokens_i, tokens_j)
                        dup_concept = all_concepts[j][2]
                        orig_concept = all_concepts[i][2]
                        candidates.append((
                            ratio, dup_concept["uid"],
                            dup_concept.get("title", "?"),
                            orig_concept.get("title", "?"),
                        ))
                        candidate_uids.add(dup_concept["uid"])

            # BUDGET: dedup may not gut a module.
            #
            # Every removal here is a syllabus item deleted before hydration on
            # the strength of a token overlap, with nothing downstream able to
            # notice the gap. One misfire costs a concept; a systematic misfire
            # (a module whose titles all share a subject noun) used to cost half
            # the module. Past the budget we keep only the most-similar
            # candidates and say out loud which ones we let stand.
            uids_to_delete = set()
            removed_titles = []
            if candidates:
                budget = max(1, int(len(all_concepts) * self.DEDUP_MAX_MODULE_REMOVAL_RATIO))
                candidates.sort(key=lambda c: c[0], reverse=True)
                if len(candidates) > budget:
                    skipped = [c[2] for c in candidates[budget:]]
                    logger.warning(
                        f"Audit DEDUP: {len(candidates)} duplicate candidates in "
                        f"module '{module.get('title', '?')}' exceeds the "
                        f"{budget}-concept budget ({len(all_concepts)} concepts). "
                        f"Keeping: {skipped}"
                    )
                for ratio, uid, dup_title, orig_title in candidates[:budget]:
                    logger.info(
                        f"Audit DEDUP: removing '{dup_title}' — duplicates "
                        f"'{orig_title}' ({ratio:.0%} similar) in module "
                        f"'{module.get('title', '?')}'"
                    )
                    uids_to_delete.add(uid)
                    removed_titles.append(dup_title)

            # Also check for exact normalized title matches across the module.
            # These are certain, not heuristic, so the budget above does not
            # apply to them.
            seen_normalized = {}
            for _, _, concept, _ in all_concepts:
                if concept["uid"] in uids_to_delete:
                    continue
                norm = concept.get("title", "").lower().strip()
                if norm in seen_normalized:
                    logger.info(
                        f"Audit DEDUP: Exact duplicate title '{concept['title']}' "
                        f"in module '{module.get('title', '?')}' — removing"
                    )
                    uids_to_delete.add(concept["uid"])
                    removed_titles.append(concept.get("title", "?"))
                else:
                    seen_normalized[norm] = concept["uid"]

            # Delete flagged concepts from their respective lessons
            if uids_to_delete:
                for unit in module.get("units", []):
                    for lesson in unit.get("lessons", []):
                        before_count = len(lesson.get("concepts", []))
                        lesson["concepts"] = [
                            c for c in lesson.get("concepts", [])
                            if c["uid"] not in uids_to_delete
                        ]
                        removed = before_count - len(lesson["concepts"])
                        if removed > 0:
                            logger.info(
                                f"  Removed {removed} duplicate(s) from lesson "
                                f"'{lesson.get('title', '?')}'"
                            )
                logger.info(
                    f"Audit DEDUP: module '{module.get('title', '?')}' lost "
                    f"{len(uids_to_delete)}/{len(all_concepts)} concepts: {removed_titles}"
                )
                total_deleted += len(uids_to_delete)

        # Also do a cross-module exact-title dedup pass
        seen_global = {}  # normalized_title -> (module_title, concept_uid)
        cross_module_deletes = set()
        for module in course.get("modules", []):
            for unit in module.get("units", []):
                for lesson in unit.get("lessons", []):
                    for concept in lesson.get("concepts", []):
                        norm = concept.get("title", "").lower().strip()
                        if norm in seen_global:
                            orig_mod = seen_global[norm][0]
                            logger.info(
                                f"Audit DEDUP (cross-module): '{concept['title']}' "
                                f"in '{module['title']}' duplicates concept in "
                                f"'{orig_mod}' — removing"
                            )
                            cross_module_deletes.add(concept["uid"])
                        else:
                            seen_global[norm] = (module.get("title", ""), concept["uid"])

        if cross_module_deletes:
            for module in course.get("modules", []):
                for unit in module.get("units", []):
                    for lesson in unit.get("lessons", []):
                        lesson["concepts"] = [
                            c for c in lesson.get("concepts", [])
                            if c["uid"] not in cross_module_deletes
                        ]
            total_deleted += len(cross_module_deletes)

        return total_deleted

    def _count_structure(self, course: dict) -> tuple:
        """Count total modules, units, lessons, and concepts in a course structure."""
        total_modules = len(course.get("modules", []))
        total_units = 0
        total_lessons = 0
        total_concepts = 0
        for module in course.get("modules", []):
            units = module.get("units", [])
            total_units += len(units)
            for unit in units:
                lessons = unit.get("lessons", [])
                total_lessons += len(lessons)
                for lesson in lessons:
                    total_concepts += len(lesson.get("concepts", []))
        return total_modules, total_units, total_lessons, total_concepts

    def audit(self, course_uid: str, target_depth: int = 2):
        if self.status_callback:
            self.status_callback("SYLLABUS:AUDIT:STARTING")
        logger.info(
            f"Starting Syllabus Audit for course {course_uid} at depth {target_depth}"
        )

        course = self.storage.courses.get_course(course_uid)
        if not course:
            logger.warning("Course not found for audit.")
            return

        topic = course.get("title", "Unknown Topic")

        # Count before audit for comparison
        before_mods, before_units, before_lessons, before_concepts = self._count_structure(course)
        logger.info(
            f"Pre-audit structure: {before_mods} modules, {before_units} units, "
            f"{before_lessons} lessons, {before_concepts} concepts"
        )

        # ── PASS 1: Programmatic deduplication (no LLM needed) ──
        if self.status_callback:
            self.status_callback("AUDIT:PASS1:DEDUP:STARTING")
        logger.info("=== PASS 1: Programmatic semantic deduplication ===")

        dedup_count = self._programmatic_dedup(course)

        if dedup_count > 0:
            # Renumber ordinals after deletions
            self._renumber_ordinals(course)
            # Save intermediate result
            self.storage.courses.update_course(course_uid, course)
            logger.info(f"Pass 1 complete: removed {dedup_count} duplicate concepts")
        else:
            logger.info("Pass 1 complete: no duplicates found")

        if self.status_callback:
            self.status_callback(f"AUDIT:DEDUP:{dedup_count} duplicates removed")

        # ── PASS 1.5: Short-title rescue ──
        # Any node (module/unit/lesson/concept) whose title is shorter than
        # MIN_TITLE_LEN gets renamed here, BEFORE hydration runs. Without
        # this, bare 2-char acronyms like "IV" or "ML" leaked through the
        # skeleton and were silently dropped by ContentHydrator's length
        # guard — the course would end up with fewer .md files than concepts.
        logger.info(f"=== PASS 1.5: Short-title rescue (min={MIN_TITLE_LEN}) ===")
        short_renames = self._rescue_short_titles(course)
        if short_renames > 0:
            self.storage.courses.update_course(course_uid, course)
            logger.info(
                f"Pass 1.5 complete: renamed {short_renames} node(s) "
                f"with titles shorter than {MIN_TITLE_LEN} chars"
            )
            if self.status_callback:
                self.status_callback(
                    f"AUDIT:SHORT_TITLE:{short_renames} node(s) renamed"
                )
        else:
            logger.info("Pass 1.5 complete: all titles meet minimum length")

        # ── PASS 2: LLM quality review ──
        logger.info("=== PASS 2: LLM quality review ===")
        if self.status_callback:
            self.status_callback("AUDIT:PASS2:LLM_REVIEW:STARTING")

        profile = DEPTH_PROFILES.get(target_depth, DEPTH_PROFILES[2])
        target_context = profile["academic_level"]

        # Build compact hierarchy from the now-deduped structure
        hierarchy = self._get_compact_hierarchy(course)

        # Collect all existing titles for collision checking during fix application
        self._all_titles = set()
        for module in course.get("modules", []):
            self._all_titles.add(module["title"].lower().strip())
            for unit in module.get("units", []):
                self._all_titles.add(unit["title"].lower().strip())
                for lesson in unit.get("lessons", []):
                    self._all_titles.add(lesson["title"].lower().strip())
                    for concept in lesson.get("concepts", []):
                        self._all_titles.add(concept["title"].lower().strip())

        # Build module scope summary so audit can check scope violations
        scope_summary = ""
        for module in course.get("modules", []):
            scope = module.get("scope", [])
            if scope:
                scope_str = (", ".join(scope)
                             if isinstance(scope, (list, tuple)) else str(scope))
                scope_summary += f"  {module['title']}: {scope_str}\n"

        # Build module-level bloom/complexity annotations
        bloom_summary = ""
        for m_idx, module in enumerate(course.get("modules", []), 1):
            level = module.get("level", m_idx)
            bloom_summary += f"  Module {m_idx} '{module['title']}': complexity_level={level}\n"

        prompt = (
            f"Topic: {topic}\nDepth: {target_depth}/5 ({target_context})\n\n"
            f"### MODULE SCOPE BOUNDARIES:\n{scope_summary}\n"
            f"### MODULE COMPLEXITY LEVELS (lower=simpler, higher=advanced):\n{bloom_summary}\n"
            f"### HIERARCHY (after automated dedup — {dedup_count} duplicates already removed):\n{hierarchy}\n\n"
            "### AUDIT TASKS — check ALL carefully:\n\n"
            "1. GENERIC / VAGUE TITLES: Any title that is just a gerund phrase ('Identifying X', "
            "'Assessing Y', 'Understanding Z') or ends with 'Basics', 'Overview', 'Context', "
            "'Introduction', 'Understanding', 'Fundamentals' → RENAME to a specific technical "
            "term or property name. Example: 'Identifying Confounders' → 'Confounder Detection Methods'.\n\n"
            "2. SCOPE VIOLATIONS: Concepts that clearly belong in a DIFFERENT module based on "
            "the scope boundaries above → DELETE them. Be conservative — only delete clear violations.\n\n"
            "3. BLOOM PROGRESSION: Early modules (level 1-2) should cover definitional/conceptual "
            "material. Later modules (level 3+) should cover application, analysis, and synthesis. "
            "If a high-complexity concept appears in a low-level module, RENAME it to match that "
            "module's level, or DELETE if it truly doesn't fit.\n\n"
            "4. REMAINING SEMANTIC DUPLICATES: If you spot concepts with different titles that "
            "teach the same thing (e.g. 'Causal Graphs' and 'Directed Acyclic Graphs in Causation') "
            "→ DELETE the less specific one.\n\n"
            "5. EMPTY OR STUB LESSONS: Lessons with 0 or 1 concepts after our dedup pass may "
            "need their remaining concept moved to a neighboring lesson → use MOVE action.\n\n"
            "### OUTPUT FORMAT: JSON Array of fix objects. Return [] if structure is clean.\n"
            "Valid actions: 'rename', 'delete'\n"
            "Each fix: {\"action\": \"rename\"|\"delete\", \"type\": \"module\"|\"unit\"|\"lesson\"|\"concept\", "
            "\"uid\": \"the_uid\", \"new_title\": \"...\" (for rename only), \"reason\": \"brief explanation\"}\n\n"
            "Be thorough. Check EVERY concept title. Return ALL fixes needed.\n"
        )

        raw_fixes = llm_generate(
            prompt,
            sys_prompt=(
                "You are a Senior Curriculum Editor performing a rigorous quality audit. "
                "Return ONLY a valid JSON array of fix objects. No commentary outside the JSON. "
                "Be aggressive about renaming vague titles — every concept should have a specific, "
                "teachable name that a student could look up in a textbook."
            ),
            max_tokens=2000,
        )
        fixes = extract_python_list(raw_fixes)

        rename_count = 0
        llm_delete_count = 0

        if fixes:
            if self.status_callback:
                self.status_callback(f"AUDIT:PASS2:FIXING:{len(fixes)}_ISSUES")

            # Count by action type for reporting
            for fix in fixes:
                action = fix.get("action", "")
                reason = fix.get("reason", "")
                if reason:
                    logger.info(f"  LLM fix: {action} {fix.get('type','')} "
                                f"{fix.get('uid','')} — {reason}")

            applied_count = self._apply_fixes(course_uid, course, fixes)

            # Count renames vs deletes from what was applied
            for fix in fixes:
                action = fix.get("action", "")
                if action == "rename":
                    rename_count += 1
                elif action == "delete":
                    llm_delete_count += 1

            logger.info(f"Pass 2 complete: applied {applied_count}/{len(fixes)} LLM fixes "
                        f"({rename_count} renames, {llm_delete_count} deletes)")
        else:
            logger.info("Pass 2 complete: LLM found no additional issues")

        if self.status_callback:
            self.status_callback(f"AUDIT:RENAME:{rename_count} items renamed")

        # ── Final summary ──
        # Re-read course in case _apply_fixes saved it
        course = self.storage.courses.get_course(course_uid)
        total_modules, total_units, total_lessons, total_concepts = self._count_structure(course)

        total_deleted = dedup_count + llm_delete_count
        logger.info(
            f"Audit complete: {total_deleted} total deletions, {rename_count} renames. "
            f"Final structure: {total_modules} modules, {total_concepts} concepts "
            f"(was {before_concepts})"
        )

        if self.status_callback:
            self.status_callback(f"AUDIT:COMPLETE:{total_modules}:{total_concepts}")

    def _get_compact_hierarchy(self, course: dict) -> str:
        """Build a compact text hierarchy for audit — more token-efficient than nested JSON.
        Includes complexity/bloom level annotations where available."""
        lines = [f"Course: {course.get('title', '')}"]
        for module in course.get("modules", []):
            level = module.get("level", "?")
            role = module.get("complexity_role", "")
            level_tag = f" [level={level}]" if level != "?" else ""
            role_tag = f" ({role})" if role else ""
            lines.append(f"  M[{module['uid']}]: {module['title']}{level_tag}{role_tag}")
            for unit in module.get("units", []):
                lines.append(f"    U[{unit['uid']}]: {unit['title']}")
                for lesson in unit.get("lessons", []):
                    concept_count = len(lesson.get("concepts", []))
                    lines.append(f"      L[{lesson['uid']}]: {lesson['title']} ({concept_count} concepts)")
                    for concept in lesson.get("concepts", []):
                        depth = concept.get("depth_level", "")
                        c_role = concept.get("complexity_role", "")
                        tags = []
                        if depth:
                            tags.append(f"depth={depth}")
                        if c_role:
                            tags.append(c_role)
                        tag_str = f" [{', '.join(tags)}]" if tags else ""
                        lines.append(f"        C[{concept['uid']}]: {concept['title']}{tag_str}")
        return "\n".join(lines)

    def _get_hierarchy(self, course: dict) -> dict:
        """Build hierarchy dict from course JSON for audit prompt."""
        hierarchy = {"title": course.get("title", ""), "modules": {}}
        for module in course.get("modules", []):
            m_uid = module["uid"]
            hierarchy["modules"][m_uid] = {
                "title": module["title"],
                "ordinal": module.get("ordinal", 0),
                "units": {},
            }
            for unit in module.get("units", []):
                u_uid = unit["uid"]
                hierarchy["modules"][m_uid]["units"][u_uid] = {
                    "title": unit["title"],
                    "ordinal": unit.get("ordinal", 0),
                    "lessons": {},
                }
                for lesson in unit.get("lessons", []):
                    l_uid = lesson["uid"]
                    hierarchy["modules"][m_uid]["units"][u_uid]["lessons"][l_uid] = {
                        "title": lesson["title"],
                        "ordinal": lesson.get("ordinal", 0),
                        "concepts": {},
                    }
                    for concept in lesson.get("concepts", []):
                        c_uid = concept["uid"]
                        hierarchy["modules"][m_uid]["units"][u_uid]["lessons"][l_uid][
                            "concepts"
                        ][c_uid] = {
                            "title": concept["title"],
                            "ordinal": concept.get("ordinal", 0),
                        }
        return hierarchy

    def _apply_fixes(self, course_uid: str, course: dict, fixes: List[Dict]) -> int:
        """Apply audit fixes directly to JSON structure. Returns count of applied fixes."""
        VALID_TYPES = {"module", "unit", "lesson", "concept"}
        applied = 0
        had_deletes = False

        for fix in fixes:
            action = fix.get("action")
            f_type = fix.get("type", "").lower()
            uid = fix.get("uid")

            if f_type not in VALID_TYPES:
                logger.warning(
                    f"Auditor: Skipping fix with invalid type '{f_type}' (uid: {uid})"
                )
                continue

            if action == "rename":
                new_title = fix.get("new_title")
                if uid and new_title:
                    # Check that the rename doesn't introduce a new collision
                    new_lower = new_title.lower().strip()
                    if hasattr(self, "_all_titles") and new_lower in self._all_titles:
                        logger.warning(
                            f"Auditor: Skipping rename to '{new_title}' — would create duplicate."
                        )
                        continue
                    logger.info(f"Auditor: Renaming {f_type} {uid} -> {new_title}")
                    self._rename_node(course, f_type, uid, new_title)
                    if hasattr(self, "_all_titles"):
                        self._all_titles.add(new_lower)
                    applied += 1
                    if self.status_callback:
                        self.status_callback(
                            f"LOG: Audit renamed {f_type}: {new_title}"
                        )

            elif action == "delete":
                if uid:
                    logger.info(f"Auditor: Deleting {f_type} {uid}")
                    self._delete_node(course, f_type, uid)
                    had_deletes = True
                    applied += 1
                    if self.status_callback:
                        self.status_callback(f"LOG: Audit deleted {f_type} {uid}")

            elif action == "reorder":
                new_ord = fix.get("new_ordinal")
                if uid and new_ord is not None:
                    logger.info(f"Auditor: Reordering {f_type} {uid} to {new_ord}")
                    self._reorder_node(course, f_type, uid, new_ord)
                    applied += 1

        # Renumber ordinals after any deletes to avoid gaps (e.g., [1, 3] → [1, 2])
        if had_deletes:
            self._renumber_ordinals(course)

        # Save updated course
        self.storage.courses.update_course(course_uid, course)
        return applied

    def _rescue_short_titles(self, course: dict) -> int:
        """Find and rename any node whose title is shorter than MIN_TITLE_LEN.

        Walks modules → units → lessons → concepts. For each too-short title,
        calls the LLM with parent-context (course + module + unit + lesson)
        to get an expanded name. Falls back to a deterministic heuristic
        ("{short_title} Fundamentals", or "{parent_title} Part N") if the
        LLM returns another too-short value or fails entirely.

        Returns the number of nodes renamed.
        """
        topic = course.get("title", "course")
        renamed = 0
        # Track titles we assign in this pass so we don't introduce collisions.
        claimed = set()
        for m in course.get("modules", []):
            claimed.add(m["title"].lower().strip())
            for u in m.get("units", []):
                claimed.add(u["title"].lower().strip())
                for l in u.get("lessons", []):
                    claimed.add(l["title"].lower().strip())
                    for c in l.get("concepts", []):
                        claimed.add(c["title"].lower().strip())

        def _is_short(t: str) -> bool:
            return not t or len(t.strip()) < MIN_TITLE_LEN

        def _expand(short_title: str, level: str, context: dict) -> str:
            """Ask the LLM for an expanded, teachable version of a too-short title."""
            parts = []
            if context.get("course"):
                parts.append(f"Course: {context['course']}")
            if context.get("module"):
                parts.append(f"Module: {context['module']}")
            if context.get("unit"):
                parts.append(f"Unit: {context['unit']}")
            if context.get("lesson"):
                parts.append(f"Lesson: {context['lesson']}")
            ctx_block = "\n".join(parts)
            prompt = (
                f"{ctx_block}\n\n"
                f"The following {level} title is too short to be teachable "
                f"on its own: '{short_title}'.\n\n"
                f"Return a single specific, textbook-style {level} title "
                f"(4-8 words) that preserves the original meaning but is "
                f"unambiguous in context. If '{short_title}' is an acronym, "
                f"expand it. No quotes, no commentary — just the title.\n"
            )
            try:
                raw = llm_generate(
                    prompt,
                    sys_prompt=(
                        "You are a curriculum editor. Rename short or "
                        "abbreviated titles into specific, teachable names. "
                        "Output only the new title on a single line."
                    ),
                    max_tokens=320,   # covers ~200 reasoning tokens; see the preflight probe
                )
                if raw:
                    candidate = raw.strip().strip('"\'').splitlines()[0].strip()
                    # Strip common prefixes the LLM adds
                    for p in ("title:", "new title:", "answer:"):
                        if candidate.lower().startswith(p):
                            candidate = candidate[len(p):].strip()
                    if candidate and len(candidate) >= MIN_TITLE_LEN:
                        return candidate
            except Exception as e:
                logger.debug(f"Short-title LLM rename failed for '{short_title}': {e}")
            return ""

        def _fallback_name(short_title: str, level: str, context: dict) -> str:
            """Deterministic fallback when the LLM can't expand the title."""
            parent = (
                context.get("lesson")
                or context.get("unit")
                or context.get("module")
                or context.get("course")
                or "Concept"
            )
            # Prefer "{short} Fundamentals" if it's not already claimed,
            # otherwise fall back to "{parent} — {short}".
            if short_title:
                attempt = f"{short_title.strip()} Fundamentals"
                if attempt.lower() not in claimed and len(attempt) >= MIN_TITLE_LEN:
                    return attempt
            return f"{parent} — {short_title}".strip(" —")

        def _unique(base: str) -> str:
            """Avoid collisions with existing titles in this course."""
            key = base.lower().strip()
            if key not in claimed:
                return base
            n = 2
            while f"{base} {n}".lower() in claimed:
                n += 1
            return f"{base} {n}"

        def _rename_if_short(node: dict, level: str, context: dict) -> bool:
            nonlocal renamed
            title = node.get("title", "")
            if not _is_short(title):
                return False
            old_key = title.lower().strip() if title else ""
            # Remove the old entry from `claimed` so the replacement can take it
            if old_key in claimed:
                claimed.discard(old_key)
            new_title = _expand(title, level, context)
            if not new_title:
                new_title = _fallback_name(title, level, context)
            new_title = _unique(new_title)
            claimed.add(new_title.lower().strip())
            logger.info(
                f"Short-title rescue: {level} {node.get('uid', '')} "
                f"'{title}' -> '{new_title}'"
            )
            if self.status_callback:
                self.status_callback(
                    f"LOG: Audit expanded short {level} title: "
                    f"'{title}' -> '{new_title}'"
                )
            node["title"] = new_title
            renamed += 1
            return True

        # Walk the hierarchy top-down so parent contexts are available when
        # renaming child nodes.
        for module in course.get("modules", []):
            m_ctx = {"course": topic}
            _rename_if_short(module, "module", m_ctx)
            for unit in module.get("units", []):
                u_ctx = {"course": topic, "module": module.get("title", "")}
                _rename_if_short(unit, "unit", u_ctx)
                for lesson in unit.get("lessons", []):
                    l_ctx = {
                        "course": topic,
                        "module": module.get("title", ""),
                        "unit": unit.get("title", ""),
                    }
                    _rename_if_short(lesson, "lesson", l_ctx)
                    for concept in lesson.get("concepts", []):
                        c_ctx = {
                            "course": topic,
                            "module": module.get("title", ""),
                            "unit": unit.get("title", ""),
                            "lesson": lesson.get("title", ""),
                        }
                        _rename_if_short(concept, "concept", c_ctx)

        return renamed

    def _rename_node(self, course: dict, node_type: str, uid: str, new_title: str):
        """Find and rename a node in the JSON structure."""
        for module in course.get("modules", []):
            if node_type == "module" and module["uid"] == uid:
                module["title"] = new_title
                return
            for unit in module.get("units", []):
                if node_type == "unit" and unit["uid"] == uid:
                    unit["title"] = new_title
                    return
                for lesson in unit.get("lessons", []):
                    if node_type == "lesson" and lesson["uid"] == uid:
                        lesson["title"] = new_title
                        return
                    for concept in lesson.get("concepts", []):
                        if node_type == "concept" and concept["uid"] == uid:
                            concept["title"] = new_title
                            return

    def _delete_node(self, course: dict, node_type: str, uid: str):
        """Find and delete a node from the JSON structure."""
        if node_type == "module":
            course["modules"] = [
                m for m in course.get("modules", []) if m["uid"] != uid
            ]
            return
        for module in course.get("modules", []):
            if node_type == "unit":
                module["units"] = [
                    u for u in module.get("units", []) if u["uid"] != uid
                ]
            for unit in module.get("units", []):
                if node_type == "lesson":
                    unit["lessons"] = [
                        l for l in unit.get("lessons", []) if l["uid"] != uid
                    ]
                for lesson in unit.get("lessons", []):
                    if node_type == "concept":
                        lesson["concepts"] = [
                            c for c in lesson.get("concepts", []) if c["uid"] != uid
                        ]

    def _reorder_node(self, course: dict, node_type: str, uid: str, new_ordinal: int):
        """Find and reorder a node in the JSON structure."""
        for module in course.get("modules", []):
            if node_type == "module" and module["uid"] == uid:
                module["ordinal"] = new_ordinal
                return
            for unit in module.get("units", []):
                if node_type == "unit" and unit["uid"] == uid:
                    unit["ordinal"] = new_ordinal
                    return
                for lesson in unit.get("lessons", []):
                    if node_type == "lesson" and lesson["uid"] == uid:
                        lesson["ordinal"] = new_ordinal
                        return
                    for concept in lesson.get("concepts", []):
                        if node_type == "concept" and concept["uid"] == uid:
                            concept["ordinal"] = new_ordinal
                            return

    def _renumber_ordinals(self, course: dict):
        """Re-sequence ordinals after deletions to avoid gaps like [1, 3] → [1, 2]."""
        for m_idx, module in enumerate(course.get("modules", []), 1):
            module["ordinal"] = m_idx
            for u_idx, unit in enumerate(module.get("units", []), 1):
                unit["ordinal"] = u_idx
                for l_idx, lesson in enumerate(unit.get("lessons", []), 1):
                    lesson["ordinal"] = l_idx
                    for c_idx, concept in enumerate(lesson.get("concepts", []), 1):
                        concept["ordinal"] = c_idx
