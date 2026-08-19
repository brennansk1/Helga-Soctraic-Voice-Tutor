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
    lessons_per_module = max(1, round(lessons_total / max(1, modules)))
    concepts_per_lesson = m.get("concepts_per_lesson", 3)
    return {
        "modules": modules,
        "concepts_per_module": concepts_per_module,
        "lessons_total": lessons_total,
        "lessons_per_module": lessons_per_module,
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


class SkeletonBuilder:
    def __init__(
        self,
        db_path: str = None,
        providers: list = None,
        status_callback=None,
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
            logger.info(f"[PREFLIGHT] {status} {msg}")
            if self.status_callback:
                prefix = "PASS" if status == "✓" else f"FAIL:{msg.replace(' ', '_')}"
                # For basic matching to what is requested
                if status == "✓":
                    self.status_callback(f"CHECK:PREFLIGHT:PASS:{msg}")
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

        # Content providers removed — LLM-only content generation
        log_and_emit("✓", "LLM content generation ready")

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
        prompt = (
            f"Topic: {topic}\n"
            f"Scope: {self.scope}/5 ({cp['scope_label']} — {cp['scope_desc']})\n"
            f"Mastery Target: {self.mastery}/5 ({cp['mastery_label']}) — student wants to reach Bloom level {bloom_ceiling} ({bloom_labels.get(bloom_ceiling, 'Apply')})\n"
            f"Student Background: {self.starting_from}/5 ({cp['starting_label']}) — starting at Bloom level {bloom_floor} ({bloom_labels.get(bloom_floor, 'Remember')})\n"
            f"{temporal_constraint}\n"
            f"{category_constraint}\n\n"
            f"Create exactly {target_modules} PROGRESSIVE modules for a course on '{topic}'.\n\n"
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

        self._backfill_uncovered_chapters(course_dict, topic)

        # Gate criterion 6 — syllabus realism. Runs here, on the skeleton,
        # BEFORE the expensive hydration: a curriculum hole is an outline
        # defect, and finding it after 40 minutes of hydration teaches nothing
        # that finding it now does not.
        self._record_syllabus_check(course_dict)

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

        if not brief or not brief.get("found"):
            logger.warning(
                f"[SKELETON] no curriculum evidence for {topic!r} "
                f"(tried: {[topic] + broader}). Generating UNGUIDED — expect "
                f"weaker coverage.")
            if self.status_callback:
                self.status_callback("CHECK:SYLLABUS_EVIDENCE:NONE")
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
            self._scope_fit = assess_scope(brief, _requested,
                                           requested_courses=1)
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
                margin = float(best.get("relevance", 0)) * 0.75
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
                max_tokens=60,
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
        # about this subject. 6.0 is the exact-title-match bonus in _relevance:
        # below it the book merely overlaps the subject rather than being it.
        best = max(ordered, key=lambda o: o.get("relevance", 0)) if ordered else None
        if best is not None and float(best.get("relevance", 0)) < 6.0:
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

        picked, seen = [], set()
        for group in groups:
            # The first chapter names the module; the rest become its declared
            # scope so the substructure builder still knows what belongs in it.
            title = group[0]
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
            f"  - {base_units * base_lessons} lessons in this module. This is "
            f"NOT approximate: a lesson is one ~50-minute class session and the "
            f"course has a fixed number of them, so a module with fewer is a "
            f"module that will not fill its weeks.\n"
            f"  - group those lessons into about {base_units} unit(s) BY TOPIC. "
            f"The unit count is flexible and units may differ in size where the "
            f"material warrants it — the lesson total is what is fixed.\n"
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
            schema=self.subtree_schema(min_units=base_units,
                                       min_lessons=base_lessons,
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
        _wanted = max(1, base_units * base_lessons)
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
        base_units = max(1, min(4, round(lessons_per_module / 3)))
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
            positive_scope_str = ", ".join(m_scope)

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


class ContentHydrator:
    def __init__(
        self,
        db_path: str = None,
        providers: list = None,
        status_callback=None,
        course_depth: int = 2,
        storage: StorageManager = None,
        mastery: int = None,
    ):
        self.db_path = db_path
        self.provider = None  # Content providers removed — LLM-only generation
        self.status_callback = status_callback
        self.course_depth = course_depth
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
        self._low_confidence_concepts = []
        # A3: text of a user-supplied document (EPUB/markdown/text). When set,
        # concepts are grounded in the user's OWN material rather than only in
        # web research. Previously uploaded files were never read at all.
        self.source_document = ""
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

        # A1: resolve the domain once per course rather than guessing per
        # concept from a topic string. Keyword matching on a title is fragile
        # ("the french revolution" contains no history keyword), so this is a
        # best-effort default that an explicit caller can override.
        if self.topic_domain is None:
            self.topic_domain = infer_domain(course_title)
        self._contract_failures = []
        self._low_confidence_concepts = []
        self._fact_failures = []

        # Build hierarchy context, concept list, and prerequisite map from JSON
        concept_list = []
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

        def _hydrate_one(idx, uid, title, objectives, complexity_role, user_note,
                         bloom_level, depth_level, prerequisite_titles, learning_objectives_list):
            """Hydrate a single concept (runs in thread pool)."""
            nonlocal hydrated_count, failed_count, hydration_fallback_count

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
            research_confidence = 0.0
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
                    timeout=15,
                )
                if research_resp.status_code == 200:
                    research_data = research_resp.json()
                    reference_material = research_data.get("combined_text", "")
                    research_sources = research_data.get("sources", [])
                    research_confidence = research_data.get("confidence", 0.0)
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
                        timeout=20,
                    )
                    if broad.status_code == 200:
                        bd = broad.json()
                        if bd.get("confidence", 0.0) > research_confidence:
                            reference_material = bd.get("combined_text", "") or reference_material
                            research_sources = bd.get("sources", []) or research_sources
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
            if self.source_document:
                user_excerpt = self._excerpt_for_concept(
                    self.source_document, title, h_ctx)

            if user_excerpt:
                content_to_use = user_excerpt
                if reference_material:
                    content_to_use += "\n\n---\n\n" + reference_material
                source_type = "user-document+research" if reference_material else "user-document"
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

            # A2: an honest marker on thin content. Previously a 0.0-confidence
            # concept was visually identical to a well-sourced one.
            if low_confidence:
                structured_md += (
                    "\n\n> **Limited sources.** The grounding pass found little "
                    "corroborating material for this concept "
                    f"(confidence {research_confidence:.2f}), so it leans more "
                    "on the model's own knowledge. Treat specifics with extra "
                    "care and verify before relying on them.\n")

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
                         os.getenv("OLLAMA_MODEL", "qwen3.5:9b")))
                    _prov_conn._get_db().commit()
            except Exception as _prov_err:
                logger.debug(f"provenance write skipped: {_prov_err}")

            if self.status_callback:
                self.status_callback(f"STRUCT:HYDRATED:{uid}:{source_type}:{title}")

            # Update counters atomically
            with _counter_lock:
                if is_fallback:
                    hydration_fallback_count += 1
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

        # AUTO-9: Abort if >50% of concepts failed hydration
        if total_concepts > 0 and failed_count > total_concepts * 0.5:
            course["status"] = "failed"
            self.storage.courses.update_course(course_uid, course)
            msg = f"Hydration failed for {failed_count}/{total_concepts} concepts (>50% failure rate). Course marked as failed."
            logger.error(msg)
            if self.status_callback:
                self.status_callback(f"ERROR: {msg}")
            raise CourseCreationError(msg)

        # Post-hydration: mark course as ready
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
            missed = len(self._contract_failures)
            met_pct = round(100 * (total_concepts - missed) / total_concepts, 1)
            course["depth_contract"] = {
                "mastery": self.mastery_level,
                "domain": self.topic_domain,
                "concepts_total": total_concepts,
                "concepts_missing_contract": missed,
                "met_pct": met_pct,
                # Below this the course is not credibly at its stated level.
                "level_verified": met_pct >= 80.0,
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

        self.storage.courses.update_course(course_uid, course)

        # Refresh the FTS5 search index now that this course's content exists, so
        # a newly built course is immediately searchable (the index otherwise only
        # builds lazily when empty). Best-effort — never fail creation on reindex.
        try:
            if hasattr(self.storage, "search"):
                self.storage.search.rebuild_search_index()
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
    ) -> str:
        """Transforms raw crawl data into structured Markdown for Socratic tutoring, Flashcards, and Review."""
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

        sys_prompt = (
            f"Expert Educational Content Architect specializing in {course_title}. "
            f"Writing level: {depth_desc}. {writing_guide} "
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

        # Build the LLM-generated section template
        section_template = f"""## Mastery Criteria
At Bloom {bloom_level} ({bloom_label}), the student demonstrates mastery by:
{mastery_criteria_hint}
Grade 3 requires: [Write one sentence describing the specific threshold for THIS concept]

## Core Explanation
[{core_inst} ~{word_target} words.]

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

        user_prompt = f"""Topic: {title} | Course: {course_title} | Depth: {depth}/5 ({depth_desc})
{h_str}

### WRITING LEVEL: {writing_guide}

### DEDUPLICATION — DO NOT REPEAT content already covered:
- Lesson concepts: {prev_str}
- Module concepts: {module_prev_str}
Focus ONLY on what makes "{title}" DISTINCT.
{f"### USER NOTE:{chr(10)}{user_note}{chr(10)}" if user_note else ""}{research_input}
Source Material: {source_material}

Generate ONLY the sections below. Do NOT generate Metadata, Learning Objectives, or Prerequisites — those are pre-filled.
{total_budget_note}

{section_template}
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
        required_sections = {
            "## Mastery Criteria": f"## Mastery Criteria\nStudent should demonstrate understanding of {title}.\n",
            "## Core Explanation": f"## Core Explanation\n{title} is a key concept in {course_title}.\n",
            "## Misconceptions": "## Misconceptions\n- **Belief**: None identified.\n- **Correction**: N/A\n",
            "## Socratic Hooks": f"## Socratic Hooks\n- Bloom 1-2: What do you think {title} means?\n- Bloom 3-4: How would you apply {title}?\n- Bloom 5-6: When does {title} break down?\n",
        }
        if depth >= 2:
            required_sections["## Key Facts"] = f"## Key Facts\n- {title} is a fundamental concept in {course_title}.\n"
            required_sections["## Real-World Examples"] = f"## Real-World Examples\nExamples of {title} can be found in everyday applications.\n"
        if depth >= 3:
            required_sections["## Edge Cases & Limitations"] = f"## Edge Cases & Limitations\n- See further reading on {title}.\n"

        for section_header, stub in required_sections.items():
            if section_header not in content:
                logger.warning(
                    f"  [MARKDOWN] Missing '{section_header}' in LLM output for {title}. Injecting stub."
                )
                content += f"\n{stub}"

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

    def _word_overlap_ratio(self, tokens_a: set, tokens_b: set) -> float:
        """Compute symmetric word overlap ratio between two token sets.
        Returns 0.0 if either set is empty, else |intersection| / |smaller set|."""
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = tokens_a & tokens_b
        smaller = min(len(tokens_a), len(tokens_b))
        return len(intersection) / smaller if smaller > 0 else 0.0

    def _programmatic_dedup(self, course: dict) -> int:
        """Pass 1: Remove semantic duplicates within each module using word overlap.
        Returns the number of concepts deleted."""
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

            # Compare every pair; mark later occurrence as duplicate
            uids_to_delete = set()
            for i in range(len(all_concepts)):
                if all_concepts[i][2]["uid"] in uids_to_delete:
                    continue
                for j in range(i + 1, len(all_concepts)):
                    if all_concepts[j][2]["uid"] in uids_to_delete:
                        continue
                    ratio = self._word_overlap_ratio(all_concepts[i][3], all_concepts[j][3])
                    if ratio > 0.4:
                        dup_concept = all_concepts[j][2]
                        orig_concept = all_concepts[i][2]
                        logger.info(
                            f"Audit DEDUP: '{dup_concept['title']}' overlaps "
                            f"'{orig_concept['title']}' ({ratio:.0%}) in module "
                            f"'{module['title']}' — removing duplicate"
                        )
                        uids_to_delete.add(dup_concept["uid"])

            # Also check for exact normalized title matches across the module
            seen_normalized = {}
            for _, _, concept, _ in all_concepts:
                if concept["uid"] in uids_to_delete:
                    continue
                norm = concept.get("title", "").lower().strip()
                if norm in seen_normalized:
                    logger.info(
                        f"Audit DEDUP: Exact duplicate title '{concept['title']}' "
                        f"in module '{module['title']}' — removing"
                    )
                    uids_to_delete.add(concept["uid"])
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
                scope_summary += f"  {module['title']}: {', '.join(scope)}\n"

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
                    max_tokens=60,
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
