import os
import time
import logging
import re
import ast
import json
import hashlib
import uuid
import random
import requests
import difflib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional, Tuple, Any
# Content providers removed — all content is LLM-generated
from services.common.storage import StorageManager
from services.common import scaffolding as _scaf
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

# Per-concept research timeouts.
#
# 15s was sized for a path that was effectively cached: lookups were keyed on
# the MODULE title, so every concept after the first in a module hit a warm
# disk cache — which is also why grounding came back byte-identical across a
# module. Research is now concept-specific with a fallback ladder, so the first
# call for each concept is a genuine uncached lookup and 15s is tight. A
# timeout here is not free: it silently degrades the concept to llm-only.
RESEARCH_TIMEOUT = float(os.getenv("HELGA_RESEARCH_TIMEOUT", "25"))
# The broadened retry searches a wider query, so it has more to do.
RESEARCH_BROADEN_TIMEOUT = float(
    os.getenv("HELGA_RESEARCH_BROADEN_TIMEOUT", "30"))

# --- free-text reasoning before formatted output -----------------------------
# HYPOTHESIS. NOT VALIDATED ON THIS PIPELINE. Default OFF, and it must stay off
# until `tools/model_gate.py` has been run both ways on the same model.
#
# What was observed: an accreditation review pulled
# data/courses/course_2b9df59e/content/con_4c467f98.md:29-32 — a "worked
# example" that carries **Step 1/2/3**, states a = 3, b = 4, computes 9 + 16 =
# 25 and concludes c = 5, all inside a concept about *partial* squares that the
# arithmetic never touches. Every structural check passed: the headings are
# there, the steps are numbered, the numbers are internally consistent. The
# REASONING does not follow, and nothing we measure can see that.
#
# The suspected mechanism is "Let Me Speak Freely? A Study on the Impact of
# Format Restrictions on Performance" (arXiv:2408.02442), which reports that
# forcing output into a fixed format consumes reasoning capacity, more so on
# smaller models. Our concept generator is format-restricted in exactly that
# way: a 9-11 section template with named headings, plus a hard word band the
# validator rejects on. The model must satisfy the shape while it works out the
# mathematics, and the shape is what survives.
#
# The proposed remedy is the paper's: let the model reason in free prose FIRST,
# then write the formatted answer. Here that is a leading "Working Notes"
# section which is stripped before anything else sees the document.
#
# Reasons to distrust this until measured:
#   * one paper, no rebuttal literature found, and it evaluates JSON-mode and
#     format-restricting instructions on benchmark tasks, not markdown lesson
#     templates on a local 9B;
#   * it costs decode tokens on the single most expensive call in the build
#     (~380s/concept today), and the notes are thrown away;
#   * a model that spends its budget on notes and truncates the lesson makes
#     things strictly worse — the generator's 40-word floor is applied to the
#     STRIPPED text so that failure at least shows up as a retry.
SCRATCHPAD_ENABLED = os.getenv(
    "HELGA_SCRATCHPAD", "0").strip().lower() in ("1", "true", "yes", "on")

# Deliberately not in services/common/scaffolding.py. That module is scoped to
# helpers built for a 1.5B model in a 4k window and now being turned OFF because
# their premises are gone; this is a new experiment being turned ON. Same
# convention (one env var, an individual switch, the reason recorded next to
# it), different question.
SCRATCHPAD_HEADING = "Working Notes"

# The notes may be emitted with any heading level — the model already drifts
# between `#` and `##` in real output (see the evidence file above, which uses
# `#`) — and may be repeated, so the substitution is global.
_SCRATCHPAD_RE = re.compile(
    r"^[ \t]*#{1,6}[ \t]*(?:working notes|scratchpad|reasoning)\b"
    r".*?(?=^[ \t]*#{1,6}[ \t]+|\Z)",
    re.IGNORECASE | re.DOTALL | re.MULTILINE)

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
SCOPE_PROFILES = {
    1: {"label": "Focused", "module_base": 3, "description": "A single narrow subtopic"},
    2: {"label": "Targeted", "module_base": 4, "description": "One specific area within the field"},
    3: {"label": "Standard", "module_base": 6, "description": "A subject area with context"},
    4: {"label": "Broad", "module_base": 8, "description": "A substantial field"},
    5: {"label": "Comprehensive", "module_base": 11, "description": "Full discipline survey"},
}

MASTERY_PROFILES = {
    1: {"label": "Awareness", "concepts_per_module": 3, "bloom_ceiling": 2, "content_words": 150,
        "vocabulary": "simple terms, everyday language, high-level intuition",
        "writing": "Write for a curious beginner. Use everyday language, analogies, and intuitive explanations. Avoid jargon."},
    2: {"label": "Understanding", "concepts_per_module": 4, "bloom_ceiling": 3, "content_words": 250,
        "vocabulary": "standard educational level, key technical terms introduced",
        "writing": "Write for an interested learner. Introduce technical terms with clear definitions. Use concrete examples."},
    3: {"label": "Application", "concepts_per_module": 5, "bloom_ceiling": 4, "content_words": 400,
        "vocabulary": "technical depth, precise mechanisms, named methods and properties",
        "writing": "Write for an undergraduate student. Use precise technical language. Explain mechanisms and formal relationships."},
    4: {"label": "Proficiency", "concepts_per_module": 7, "bloom_ceiling": 5, "content_words": 600,
        "vocabulary": "formal definitions, named theorems and criteria, professional terminology",
        "writing": "Write for a postgraduate/professional. Use full technical precision. Include formal definitions and edge cases."},
    5: {"label": "Expertise", "concepts_per_module": 10, "bloom_ceiling": 6, "content_words": 800,
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

_MINUTES_PER_CONCEPT = 2.0

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
        required = DEPTH_CONTRACTS.get(p.get("mastery", 1), {}).get("required", [])
    except Exception as e:
        logger.warning(f"Failed to resolve depth contract for preset '{key}': {e}")
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
    return {
        "modules": modules,
        "concepts_per_module": concepts_per_module,
        "total_concepts_approx": modules * concepts_per_module,
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


# The inference window the builder is writing into. The runner starts the model
# with `-c 16384` (see services/common/scaffolding.py, which documents the same
# number and the caps that were sized for the old 4,096). Nothing here SETS the
# window — this is only the figure prompts are measured against, so that a
# prompt which has grown past the context is reported instead of being silently
# truncated at the tail (where the output-format instruction lives, which is
# exactly the part whose loss is hardest to diagnose).
CONTEXT_TOKENS = int(os.getenv("HELGA_CONTEXT_TOKENS", "16384"))


def _log_prompt_budget(label: str, prompt: str, max_output_tokens: int = 0):
    """Record what a generation prompt actually costs, and warn before it bites.

    A prompt that overflows the window does not error — the server drops the
    tail and the model answers a question it only partly received. Measuring is
    cheap and the alternative is diagnosing a silent truncation from its
    symptoms, so every structured builder call reports its size.

    ~4 characters per token is the usual English-prose approximation; it is
    close enough to catch an overflow, which is the only thing this is for.
    """
    est = len(prompt) // 4
    budget = CONTEXT_TOKENS - max(0, max_output_tokens)
    pct = (100.0 * est / CONTEXT_TOKENS) if CONTEXT_TOKENS else 0.0
    msg = (f"[PROMPT] {label}: {len(prompt)} chars ~= {est} tok "
           f"({pct:.0f}% of {CONTEXT_TOKENS}); output reserve {max_output_tokens}")
    if est >= budget:
        logger.warning(msg + " — OVER BUDGET, the tail will be truncated")
    elif est >= budget * 0.75:
        logger.info(msg + " — approaching the window")
    else:
        logger.debug(msg)
    return est


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
        # Claim token for the durable build record (services/common/build_state).
        # None means this builder owns no record — every build_state call then
        # runs unowned, which the module treats as "no claim".
        self._build_id = None
        self._callback_teed = False

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
            # "quantum computing" matched NONE of the above, so a quantum
            # course was classified non-STEM and got the generic example JSON
            # ("[Named method/concept A]") instead of the STEM one
            # ("[Real named theorem/method 1]") — losing the one template that
            # asks for REAL named results.
            "quantum",
            "comput",          # computing / computation / computer
            "chemistry",
            "cryptograph",
            "geometry",
            "algebra",
            "mechanic",        # quantum/classical mechanics
            "theorem",
            "circuit",
            "network",
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
    {"title": "[Specific Subtopic Area 1]", "level": 1, "scope": ["[Named method/concept A]", "[Named method/concept B]", "[Named method/concept C]"]},
    {"title": "[Specific Subtopic Area 2]", "level": 2, "scope": ["[Named method/concept D]", "[Named method/concept E]", "[Named method/concept F]"]},
    {"title": "[Specific Subtopic Area 3]", "level": 3, "scope": ["[Named method/concept G]", "[Named method/concept H]", "[Named method/concept I]"]}
]"""
        if is_stem:
            # STEM example: abstract format only — NO realistic terms that could bleed into output
            example_json = """[
    {"title": "[Foundational Theory Area]", "level": 1, "scope": ["[Real named theorem/method 1]", "[Real named theorem/method 2]", "[Real named framework 3]"]},
    {"title": "[Core Methods Area]", "level": 2, "scope": ["[Real named technique 1]", "[Real named technique 2]", "[Real named technique 3]"]},
    {"title": "[Advanced Applications Area]", "level": 3, "scope": ["[Real named advanced method 1]", "[Real named advanced method 2]", "[Real named advanced method 3]"]}
]"""
        elif is_historical:
            example_json = """[
    {"title": "[Specific Historical Period/Theme 1]", "level": 1, "scope": ["[Specific event/figure/concept A]", "[Specific event/figure/concept B]", "[Specific event/figure/concept C]"]},
    {"title": "[Specific Historical Period/Theme 2]", "level": 2, "scope": ["[Specific event/figure/concept D]", "[Specific event/figure/concept E]", "[Specific event/figure/concept F]"]},
    {"title": "[Specific Historical Period/Theme 3]", "level": 3, "scope": ["[Specific event/figure/concept G]", "[Specific event/figure/concept H]", "[Specific event/figure/concept I]"]}
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

        if phase_name == "modules":
            # CATCH TRUNCATION THAT repair_json ALREADY PAPERED OVER.
            #
            # A module list cut off mid-array is closed by repair_json, parses
            # cleanly, and arrives here with the right NUMBER of modules and a
            # wrecked tail. Observed on a real build (course_56ddfe61):
            #
            #     M1-M4  scope: 3 topics each
            #     M5     scope: ["Bell measurement"]
            #     M6     scope: ["Grover's algorithm", "]"]
            #
            # That "]" is the repair scar — a closing bracket parsed as a topic
            # name. Nothing downstream noticed: `len(items) >= min_count` passed
            # because six modules were returned, and scope was never inspected.
            # The advanced topics that belonged in that truncated tail — Shor,
            # Deutsch-Jozsa, error correction, decoherence — never entered the
            # course, and the coverage check that should have caught it scored
            # the build at 0% for an unrelated reason.
            #
            # Raising the token budget is NOT the fix and would not have been:
            # HELGA_LEAN defaults on, so MODULE_JSON_TOKENS was already 2400
            # when this happened, not the 800 the comment there describes.
            for m in items:
                scope = m.get("scope")
                if not isinstance(scope, list) or not scope:
                    issues.append(
                        f"Module '{m.get('title', '?')}' has no scope list — "
                        f"output was probably truncated")
                    continue
                junk = [s for s in scope
                        if not isinstance(s, str) or len(s.strip()) < 3
                        or s.strip() in ("]", "[", "}", "{", ",", '"')]
                if junk:
                    issues.append(
                        f"Module '{m.get('title', '?')}' scope contains repair "
                        f"artefacts {junk!r} — output was truncated mid-array")
            if issues:
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

        # THE LOCK COMES FIRST. start() used to run before it, and the early
        # `return None` below skips the `finally` — so a build that _build_lock
        # rejected still overwrote the running build's record on its way out
        # (wrong topic in the banner, timer reset), and the FIRST build's
        # eventual finish() then marked the SECOND one complete.
        if not _build_lock.acquire(blocking=False):
            msg = "Another course is already being built. Please wait for it to finish."
            logger.warning(msg)
            if self.status_callback:
                self.status_callback(f"ERROR: {msg}")
            return None

        if build_state:
            self._build_id = build_state.start(
                topic, source=getattr(self, "build_source", "topic"))
            if not self._build_id:
                # The record is already claimed. _build_lock — which we hold —
                # remains the authority on whether to build, so this is
                # informational: we proceed unowned rather than refusing work
                # the lock has already permitted.
                logger.warning(
                    "build_state.start refused for %r; proceeding without a "
                    "durable record", topic)
            # Every status event this builder emits also lands on disk. The
            # counting that _record_progress did by hand now happens inside
            # build_state.note(), under that module's lock — the manual
            # current()-then-update() was an unlocked read-modify-write.
            #
            # Once per instance: build() on a reused builder would otherwise
            # stack a second tee on the first and record every message twice,
            # inflating the module and concept counters the UI displays.
            if not getattr(self, "_callback_teed", False):
                self.status_callback = build_state.tee(
                    self.status_callback, build_id=self._build_id)
                self._callback_teed = True

        try:
            uid = self._build_inner(topic, max_depth, module_depths)
        except Exception as e:
            # The skeleton is ~5% of the pipeline, so it does NOT get to call
            # finish() — but a skeleton that RAISED ends the whole build, and
            # the owner upstream can only learn that from the exception it is
            # about to receive. Record the failure here so the record never
            # reads "still building" for a build that has already died.
            if build_state:
                build_state.fail(e, build_id=getattr(self, "_build_id", None))
            raise
        finally:
            _build_lock.release()

        if build_state:
            if uid:
                # NOT finish(): the pipeline owner calls that, once, after
                # hydration and assets. Announcing completion here is what made
                # the UI toast "Your course is ready." at 5%.
                build_state.update(course_uid=uid,
                                   build_id=getattr(self, "_build_id", None))
                build_state.stage("audit", pct=30,
                                  build_id=getattr(self, "_build_id", None))
            else:
                build_state.fail("skeleton generation failed",
                                 build_id=getattr(self, "_build_id", None))
        return uid

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

        # TOPIC-FIRST: name the curriculum BEFORE choosing the modules.
        #
        # The skeleton allocated by COGNITIVE LEVEL and let the model discover
        # topics along the way. With a Bloom ramp of 1,2,2,3,3,4 for mastery 3,
        # five of six modules are Remember/Understand/Apply — so every named
        # algorithm in the subject competed for a single Analyze slot. Coverage
        # was lost at module-allocation time, before any naming decision was
        # made, which is why prompt fixes alone could not recover it.
        #
        # The same instrument that GRADES coverage can supply the checklist,
        # from the same phase-1 syllabi. Grading a course against topics the
        # generator was never shown measures a gap we chose to create.
        #
        # Advisory, not a schema: the module count is fixed by scope, so a
        # 15-topic checklist cannot force 15 modules. It tells the model what
        # must be covered SOMEWHERE, and Bloom then orders it.
        _canonical_topics = []
        try:
            from tools.syllabus_check import core_topics as _core_topics
            _canonical_topics = _core_topics(
                _syllabus_evidence_block or None, topic, self.mastery) or []
        except Exception as e:
            logger.info(f"[SKELETON] canonical topic list unavailable: {e}")
        if _canonical_topics:
            logger.info(f"[SKELETON] required topics: "
                        f"{', '.join(_canonical_topics[:12])}")
            if self.status_callback:
                self.status_callback(
                    f"RESEARCH:TOPICS:{len(_canonical_topics)} core topics identified")
        _topic_requirement = ("" if not _canonical_topics else (
            "REQUIRED COVERAGE — a real course on this subject teaches these. "
            "Every one must appear in some module's 'scope', under the name "
            "given here (these are the field's own names; do not paraphrase "
            "them). Distribute them across modules by difficulty:\n"
            + "\n".join(f"  - {t}" for t in _canonical_topics[:15]) + "\n\n"))

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
            # Rule 5 used to mandate a per-module 'rationale' paragraph. It was
            # never read and never stored — `module_dict` has no such key and a
            # grep for .get("rationale") across services/ and tools/ returns
            # nothing. It was pure output cost on the one call whose tail was
            # being truncated, so the budget it consumed came straight out of
            # the advanced modules that went missing.
            "5. Every module's 'scope' must list at least 3 SPECIFIC named topics — "
            "the names the field actually uses, including eponyms "
            "(e.g. \"Grover's algorithm\", not \"amplitude amplification method\").\n\n"
            + _topic_requirement
            + (f"\n{_syllabus_evidence_block}\n\n"
               if _syllabus_evidence_block else "") +
            "ANTI-COPY WARNING: The example below shows JSON FORMAT ONLY. "
            "Replace ALL bracket placeholders with real content from " + topic + ".\n"
            "Do NOT use words from the example like 'Foundational', 'Core Methods', 'Advanced Applications'.\n\n"
            f"Return strict JSON array:\n{example_json}"
        )

        max_retries = 3
        modules = []
        correction_header = ""

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
            _log_prompt_budget("modules", current_prompt, _scaf.MODULE_JSON_TOKENS)
            # SCHEMA-CONSTRAIN THE ONE CALL THAT DEMONSTRABLY TRUNCATES.
            #
            # A real build returned six modules whose scope arrays degraded
            # 3,3,3,3,1,["Grover's algorithm","]"] — the trailing "]" being a
            # repair_json scar from an array cut mid-flight. It parsed, so
            # nothing downstream objected, and the advanced topics that lived in
            # that tail never entered the course.
            #
            # llm_utils already supports this and the skeleton never used it
            # (grep: zero `schema=` in this file before now). Note the
            # distinction documented at llm_utils.py:582 — a SCHEMA constrains
            # shape and is safe; Ollama's generic format:"json" changes the
            # shape the model picks and broke course creation outright.
            _module_schema = {
                "type": "array",
                "minItems": target_modules,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "level": {"type": "integer"},
                        "scope": {
                            "type": "array",
                            "minItems": 3,
                            "items": {"type": "string", "minLength": 3},
                        },
                    },
                    "required": ["title", "scope"],
                },
            }
            new_batch = llm_generate_json(
                current_prompt,
                sys_prompt=raw_sys,
                max_tokens=_scaf.MODULE_JSON_TOKENS,
                schema=_module_schema,
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
        if not modules:
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
            # The same evidence that shaped the modules must reach the leaves.
            # It used to stop here, one level above where hollowness is decided.
            curriculum_evidence=_syllabus_evidence_block,
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

        # Gate criterion 6 — syllabus realism. Runs here, on the skeleton,
        # BEFORE the expensive hydration: a curriculum hole is an outline
        # defect, and finding it after 40 minutes of hydration teaches nothing
        # that finding it now does not.
        self._record_syllabus_check(course_dict, _syllabus_evidence_block)

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
        broader = []
        try:
            raw = llm_generate(
                prompt=(f"What academic subject or discipline is '{topic}' part of? "
                        f"Answer with 1-3 subject names only, comma separated, "
                        f"no explanation. Example: Geometry, Trigonometry"),
                sys_prompt="You name academic disciplines. Answer tersely.",
                max_tokens=60,
            )
            broader = [b.strip() for b in (raw or "").split(",")
                       if b.strip() and len(b.strip()) < 60][:3]
        except Exception as e:
            logger.debug(f"[SKELETON] parent-subject lookup failed: {e}")

        brief = None
        for candidate in [topic] + broader:
            try:
                brief = curriculum_brief(
                    candidate, mastery=self.mastery, scope=self.scope,
                    starting_from=self.starting_from,
                    preset_label=getattr(self, "preset_label", None))
            except Exception as e:
                logger.warning(f"[SKELETON] curriculum_brief failed for {candidate!r}: {e}")
                continue
            if brief.get("found"):
                if candidate != topic:
                    logger.info(f"[SKELETON] {topic!r} had no syllabus; used {candidate!r}")
                break

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
        return format_brief(brief)

    # How many characters of BRIEF LINES reach each level below the module
    # prompt (the header and the framing sentence are ~250-350 chars on top).
    #
    # Sized against the 16,384-token window, measured rather than assumed. On a
    # deliberately large brief (2 syllabi x 30 chapters, a Wikiversity course
    # and 8 canonical texts = 3,304 chars) with worst-case surrounding context
    # — a scope-5 course, ~110 accumulated titles, ten modules of hierarchy
    # summary — the resulting prompts measure:
    #     units     12,284 chars ~= 3,071 tok   19% of the window
    #     lessons    3,982 chars ~=   995 tok    6%
    #     concepts   4,439 chars ~= 1,109 tok    7%
    # against a 1,200-token output reserve. There is room; the constraint that
    # bites first is the units prompt's own accumulated-title list, not this.
    # Every call logs its real size through _log_prompt_budget(), so a prompt
    # that grows past the window is reported instead of silently truncated.
    EVIDENCE_CHARS = {"unit": 3000, "lesson": 2000, "concept": 1500}

    # The brief's own closing instructions ("HOW TO USE THIS — SYNTHESISE, DO
    # NOT COPY") argue about the SHAPE OF THE WHOLE COURSE — cut to scope,
    # re-sequence, pitch at a level. Those decisions are taken in the module
    # prompt and are already made by the time a lesson is being written, so
    # below that level they are ~700 chars of instruction the model cannot act
    # on. They are replaced with a per-level framing instead.
    _EVIDENCE_FRAMING = {
        "unit": ("USE AS EVIDENCE, NOT AS A TEMPLATE. Units must carve this "
                 "module's scope into sub-areas the sources show are REAL parts "
                 "of the subject. Select and re-sequence; never lift a chapter "
                 "list."),
        # "Do not reproduce chapter titles verbatim" was written to stop the
        # model copying somebody else's course SEQUENCE. The model applied it
        # to TOPIC NAMES: shown a Wikibooks chapter called "Grover's
        # algorithm", it dutifully produced "Quadratic Amplitude
        # Amplification" and "Oracle Query Cost Partitioning" instead. A
        # learner finishing that course cannot name what they studied.
        "lesson": ("USE AS EVIDENCE, NOT AS A TEMPLATE. Lessons should teach "
                   "material these sources treat as load-bearing, narrowed to "
                   "this unit. Choose and re-sequence freely, but KEEP THE "
                   "FIELD'S OWN NAME for any topic that has one — eponyms "
                   "included. Renaming a standard topic hides it."),
        "concept": ("USE AS EVIDENCE, NOT AS A TEMPLATE. Prefer concepts these "
                    "sources show are genuinely part of the subject over ones "
                    "invented to fill the lesson. Use the STANDARD NAME the "
                    "field uses for each concept, including eponyms "
                    "(\"Shor's algorithm\", \"Bell state\"). Invent a phrasing "
                    "only when the concept genuinely has no established name."),
    }

    def _evidence_digest(self, block: str, level: str) -> str:
        """Trim the phase-1 curriculum brief for a sub-structure prompt.

        WHY THIS EXISTS
        ---------------
        The brief was reaching exactly ONE prompt — the module prompt — and the
        research apparatus it comes from was built to stop courses being
        substantively hollow. Hollowness is decided at the LEAF: concepts are
        what gets hydrated, and concepts are what syllabus coverage measures.
        Generating them from title + lesson title + Bloom level alone meant the
        evidence stopped one level above the decision it was gathered for.

        The digest is not a summary — no LLM is involved. It keeps the source
        lines (which book, which chapters) and drops the whole-course framing,
        then truncates on a "; " boundary so a chapter title is never halved: a
        half-title reads as a real topic name and can be adopted as one.

        Returns "" when there is no evidence, so callers concatenate nothing.
        """
        if not block:
            return ""
        cap = self.EVIDENCE_CHARS.get(level, 1500)
        kept, used = [], 0
        for line in block.splitlines():
            if line.startswith("HOW TO USE THIS"):
                break
            if not line.strip():
                continue
            room = cap - used
            if room <= 0:
                break
            if len(line) > room:
                cut = line[:room].rsplit("; ", 1)[0]
                # A stub of a line is worse than no line: it truncates mid-list
                # and the remainder reads as the complete list.
                if len(cut) < 60:
                    break
                line = cut + "; ..."
            kept.append(line)
            used += len(line) + 1
        if not kept:
            return ""
        framing = self._EVIDENCE_FRAMING.get(level, self._EVIDENCE_FRAMING["concept"])
        return ("### CURRICULUM EVIDENCE — how this subject is really organised:\n"
                + "\n".join(kept) + "\n" + framing)

    def _record_syllabus_check(self, course_dict, reference_text=None):
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

        # GRADE AGAINST THE SYLLABI WE ALREADY FETCHED.
        #
        # This was called with no reference_text, so grounding fell back to
        # "model-knowledge (WEAK — same model family wrote the course)": the
        # model that wrote the course also decided what a course on the subject
        # ought to contain. The real syllabi gathered in phase 1 were sitting in
        # a local variable one call away and were never handed over.
        result = check_structure(course_dict, reference_text=reference_text)
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

    def _build_substructures_progressive(
        self, module_refs, max_depth, topic, all_modules_metadata,
        module_bloom_targets=None, curriculum_evidence="",
    ):
        """
        Chunked hierarchical generation for reliable structure building.

        Strategy:
        1. Generate Units first (small, fast call)
        2. Generate Lessons per Unit (individual calls)
        3. Generate Concepts per Lesson (individual calls)

        This provides frequent progress updates and keeps each LLM call focused.

        `curriculum_evidence` is the phase-1 research brief. It is threaded all
        the way to the concepts prompt on purpose: concepts are the leaves that
        get hydrated and the unit syllabus coverage is scored against, so
        evidence that reaches only the module prompt cannot affect the number
        it was gathered to move.
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

        profile_units = self.depth_profile.get("units_per_module", 1)
        profile_lessons = self.depth_profile.get("lessons_per_unit", 1)
        # Choose the smallest (units, lessons) that can hold at least
        # target_concepts_per_module/2 leaves without going under 2 concepts
        # per lesson. Preference is fewer units → more concepts per lesson,
        # which mirrors how a real syllabus scales with mastery.
        if target_concepts_per_module <= 3:
            base_units, base_lessons = 1, 1
        elif target_concepts_per_module <= 5:
            base_units, base_lessons = 1, min(profile_lessons, 2)
        elif target_concepts_per_module <= 7:
            base_units, base_lessons = min(profile_units, 2), min(profile_lessons, 2)
        elif target_concepts_per_module <= 10:
            base_units, base_lessons = min(profile_units, 2), min(profile_lessons, 3)
        else:
            base_units, base_lessons = min(profile_units, 3), min(profile_lessons, 3)
        base_units = max(1, base_units)
        base_lessons = max(1, base_lessons)
        base_concepts = max(2, target_concepts_per_module // max(1, base_units * base_lessons))
        logger.info(
            f"Substructure shape: units={base_units}, lessons_per_unit={base_lessons}, "
            f"concepts_per_lesson={base_concepts} "
            f"(target concepts/module={target_concepts_per_module})"
        )
        mastery_constraint = self.course_params.get("mastery_writing", "")
        mastery_label = self.course_params.get("mastery_label", "Understanding")

        # Digest the brief ONCE per build rather than per lesson — the trimming
        # is deterministic and the same three strings are reused for every
        # module, unit and lesson.
        ev_unit = self._evidence_digest(curriculum_evidence, "unit")
        ev_lesson = self._evidence_digest(curriculum_evidence, "lesson")
        ev_concept = self._evidence_digest(curriculum_evidence, "concept")
        if curriculum_evidence:
            logger.info(
                f"[SKELETON] curriculum evidence threaded to sub-structures: "
                f"brief={len(curriculum_evidence)} chars -> unit={len(ev_unit)}, "
                f"lesson={len(ev_lesson)}, concept={len(ev_concept)}")
        else:
            logger.warning(
                "[SKELETON] no curriculum evidence — units, lessons and concepts "
                "are being generated UNGUIDED, from recall alone")

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
                + (f"{ev_unit}\n\n" if ev_unit else "") +
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
            _log_prompt_budget(f"units[{m_title[:30]}]", units_prompt, 1200)
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
                    + (f"{ev_lesson}\n\n" if ev_lesson else "") +
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
                _log_prompt_budget(f"lessons[{u_title[:30]}]", lessons_prompt, 1200)
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
                        1: (f"- Keep names PLAIN, but keep the field's real term when one exists.\n"
                            f"- Good: 'Correlation vs Causation', 'Controlled Experiments', 'Qubit'\n"
                            f"- If a topic has a standard name, USE IT and explain it simply —\n"
                            f"  a beginner who never hears the word cannot look it up later.\n"
                            f"- Each concept should be explainable in one simple sentence."),
                        2: (f"- Use the standard name for each idea; introduce it plainly.\n"
                            f"- Good: 'Confounding Variables', 'Superposition', 'Bell State'\n"
                            f"- Pitch the EXPLANATION to this level, not the NAME. Simplifying a\n"
                            f"  name away is how a course teaches a topic without naming it."),
                        3: (f"- Use proper technical names from the field.\n"
                            f"- Good: 'Propensity Score Matching', 'Randomized Controlled Trials', 'Back-Door Criterion'\n"
                            f"- Each concept should be a real, named method or framework."),
                        4: (f"- Use precise technical names: specific methods, theorems, estimators.\n"
                            f"- Good: 'SUTVA', 'D-Separation', 'Inverse Probability Weighting', 'Structural Causal Models'"),
                        5: (f"- Use research-level terminology: named theorems, algorithms, estimation procedures.\n"
                            f"- Good: 'Do-Calculus Rules', 'G-estimation', 'Structural Nested Mean Models'"),
                    }.get(_mod_bloom, "- Use appropriate technical names from the field.")

                    # The concepts prompt used to be the ONLY level with neither
                    # the module's scope nor any curriculum evidence: a concept
                    # was generated from its own title, its lesson's title and a
                    # Bloom number. Concepts are the leaves that get hydrated
                    # and the unit syllabus coverage is measured in, so this was
                    # precisely where the evidence was missing and precisely
                    # where hollowness is decided.
                    concepts_prompt = (
                        f"Course: {topic}\n"
                        f"Module: {m_title} (Module {module_dict.get('ordinal',1)}/{len(module_refs)})\n"
                        f"Module Scope (STAY WITHIN THIS): {positive_scope_str}\n"
                        f"Unit: {u_title} | Lesson: {l_title}\n"
                        f"Bloom Level: {_mod_bloom} ({_mod_bloom_label})\n\n"
                        f"### SIBLING LESSONS (concepts must NOT overlap with these):\n{sibling_lessons_str}\n\n"
                        f"### ALL CONCEPTS ALREADY IN THIS COURSE (do NOT repeat, rephrase, or paraphrase ANY):\n{prev_concepts_str}\n\n"
                        + (f"{ev_concept}\n\n" if ev_concept else "") +
                        f"Generate exactly {base_concepts} concepts for '{l_title}'.\n\n"
                        f"SCOPE BOUNDARY: every concept must fall inside this module's scope "
                        f"({positive_scope_str}) — material belonging to another module is a defect here, "
                        f"not a bonus.\n\n"
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
                    _log_prompt_budget(f"concepts[{l_title[:30]}]", concepts_prompt, 1200)
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

                    # FLATTEN ONE LEVEL, AND SKIP WHAT IS NOT A DICT.
                    #
                    # The concept call is unconstrained, and a model asked for
                    # "a JSON array" sometimes returns [[{...}, {...}]] — an
                    # array containing an array. The loop below assumed every
                    # element was a dict and died on `.get`, taking the whole
                    # build down after the skeleton had already been paid for:
                    #     AttributeError: 'list' object has no attribute 'get'
                    # A malformed batch should cost its own concepts, not the
                    # course.
                    _flat = []
                    for _item in (concepts_data or []):
                        if isinstance(_item, list):
                            _flat.extend(x for x in _item if isinstance(x, dict))
                        elif isinstance(_item, dict):
                            _flat.append(_item)
                        else:
                            logger.warning(
                                f"  [CONCEPTS] discarding non-object entry "
                                f"{type(_item).__name__} in '{l_title}'")
                    concepts_data = _flat[:base_concepts]

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
        # Hydration is the bulk of a build. Teeing here is what makes the
        # durable record span the whole pipeline instead of stopping at the
        # skeleton (~5%), which is what let the UI announce completion while
        # hydration had not started. note() is a no-op when no build record is
        # active, so the wizard/librarian hydration path is unaffected.
        try:
            from services.common import build_state
            self.status_callback = build_state.tee(self.status_callback)
        except Exception as e:
            logger.debug(f"build_state tee unavailable: {e}")
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
        # The verdict lists are appended from every hydration worker, and
        # _revalidate_after_fact_check also REMOVES from them, which a plain
        # append cannot be relied on to survive.
        self._verdict_lock = threading.Lock()
        # A2: below this, grounding is too thin to present as verified. Set 0
        # to disable the retry+marker behaviour entirely.
        self.confidence_floor = float(os.getenv("HELGA_CONFIDENCE_FLOOR", "0.5"))
        self._low_confidence_concepts = []
        # Concepts whose research call never completed — distinct from concepts
        # the research found nothing for. See the comment at the call site.
        self._research_errors = []
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
        # Sections the model never wrote, per concept that shipped. Stub
        # injection is off by default now (scaffolding.STUB_MISSING_SECTIONS),
        # which stopped a 45-word response from LOOKING like a complete
        # document — but it left a document with three missing sections and one
        # with none still indistinguishable to everything downstream. This is
        # the verdict that separates them; see the roll-up in hydrate().
        self._missing_sections = []
        self._doc_missing = {}
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

    def _build_stage(self, name, pct):
        """Announce a pipeline phase to the durable build record.

        Best-effort and silent when no build is active — the wizard and the
        librarian both call hydrate() outside a recorded build.
        """
        try:
            from services.common import build_state
            build_state.stage(name, pct=pct)
        except Exception as e:
            logger.debug(f"build_state stage({name}) skipped: {e}")

    def hydrate(self, course_uid: str):
        """Hydrate all concepts in a course with content from sources + LLM."""
        # Declare the stage BEFORE the work starts. build_state picks its
        # staleness budget per stage, and hydration's is 20 minutes precisely
        # because one concept (research + generate + fact-check retry + depth
        # retry) legitimately takes minutes. Left unmarked, a slow-but-healthy
        # hydration gets reaped as dead and the UI unlocks mid-build.
        self._build_stage("hydrate", 40)

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
        self._research_errors = []
        self._missing_sections = []
        self._doc_missing = {}

        # Build hierarchy context, concept list, and prerequisite map from JSON
        concept_list = []
        hierarchy_map = {}
        module_source_map = {}
        concept_ref_map = {}
        # Pre-compute prerequisites: for each concept, the titles of preceding concepts
        all_concept_titles_in_order = []
        prerequisite_map = {}
        # Every concept in the course, whether or not THIS run touches it. The
        # verdicts below are claims about the course, so this — not the size of
        # one run's work queue — is what they are measured against.
        course_total_concepts = 0
        already_hydrated = 0
        # Every concept uid in the course, in syllabus order. The course-level
        # verdicts read the WHOLE course's content; `concept_list` is only what
        # this run has to do.
        all_concept_uids_in_order = []

        for module in course.get("modules", []):
            source_file = module.get("source_file", "")
            for unit in module.get("units", []):
                for lesson in unit.get("lessons", []):
                    for concept in lesson.get("concepts", []):
                        uid = concept["uid"]
                        title = concept["title"]
                        course_total_concepts += 1
                        all_concept_uids_in_order.append(uid)
                        # Build prerequisite list from prior concepts in syllabus order
                        prerequisite_map[uid] = list(all_concept_titles_in_order[-5:])
                        all_concept_titles_in_order.append(title)

                        objectives = json.dumps(concept.get("learning_objectives", []))
                        complexity_role = concept.get("complexity_role", "")
                        bloom_level = concept.get("bloom_level", self.mastery_level)
                        depth_level = concept.get("depth_level", self.mastery_level)

                        # Check if already hydrated.
                        #
                        # A "[Hydration failed]" stub is longer than 100 chars,
                        # so a length test alone counted every stub as finished
                        # work: a re-run skipped exactly the concepts that need
                        # redoing, and the run then measured its verdicts
                        # against the handful of concepts left — a resume with
                        # one concept remaining reported "100% met,
                        # level_verified: true" for a course that was mostly
                        # stubs. A stub is a failure to retry, not content.
                        existing_content = self.storage.courses.get_concept_content(
                            course_uid, uid
                        )
                        if (existing_content and len(existing_content) > 100
                                and "[Hydration failed]" not in existing_content):
                            already_hydrated += 1
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
            f"Starting hydration for {len(concept_list)} concepts in "
            f"'{course_title}' ({already_hydrated}/{course_total_concepts} "
            f"already have real content)"
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
            # A FAILED LOOKUP AND AN EMPTY ONE ARE NOT THE SAME THING, and this
            # is where they became indistinguishable: on any exception the three
            # variables above keep their empty defaults, which is byte-identical
            # to a successful search that found nothing. Downstream, both render
            # as "Limited sources — the grounding pass found little", which is
            # true of one and false of the other. A concept whose research
            # NEVER RAN must not be reported as a concept with no sources
            # available; the first is our failure, the second is the subject's.
            research_failed = None
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
                    timeout=RESEARCH_TIMEOUT,
                )
                if research_resp.status_code != 200:
                    research_failed = f"HTTP {research_resp.status_code}"
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
                research_failed = f"{type(research_err).__name__}: {research_err}"
                logger.warning(f"  [RESEARCH] Unavailable for '{title}': {research_err}")

            if research_failed:
                with self._verdict_lock:
                    self._research_errors.append(
                        {"uid": uid, "title": title, "error": research_failed[:200]})
                if self.status_callback:
                    self.status_callback(
                        f"STRUCT:WARN:RESEARCH_UNREACHABLE:{title}")

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
                        timeout=RESEARCH_BROADEN_TIMEOUT,
                    )
                    if broad.status_code == 200:
                        bd = broad.json()
                        # The broadened search reaching the service at all
                        # settles the "unreachable vs nothing found" question,
                        # whatever it returns.
                        research_failed = None
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
                _pre_fact_check = structured_md
                structured_md = self._apply_fact_check(
                    structured_md, title, course_title, complexity_role,
                    source_type, h_ctx, research_sources, research_confidence,
                    user_note, bloom_level, learning_objectives_list,
                    prerequisite_titles, research_structured, content_to_use,
                    uid,
                )
                # The fact-check can return a WHOLLY REGENERATED document, and
                # it regenerates against the false claim, not against the depth
                # contract. So a concept that had just been validated (or
                # repaired through up to two depth retries) could be replaced by
                # one that no longer meets its level, and nothing looked again —
                # the recorded verdict described a document that had been
                # thrown away. Re-validate the replacement.
                if self.enforce_depth and structured_md != _pre_fact_check:
                    self._revalidate_after_fact_check(
                        structured_md, uid, title, course_title,
                        research_sources)

            # Which required sections the model never wrote, for the draft that
            # actually shipped. Read HERE — after every regeneration path has
            # chosen its winner, and BEFORE the confidence marker and the
            # Sources block mutate the text and invalidate the lookup key.
            #
            # A "[Hydration failed]" stub never went through the validator, so
            # it has no entry; it is already counted as a failure above.
            _missing_here = self._missing_for_doc(structured_md)
            if _missing_here:
                with self._verdict_lock:
                    self._missing_sections.append({
                        "uid": uid, "title": title,
                        "missing": [h.lstrip("# ").strip() for h in _missing_here],
                    })

            # A2: an honest marker on thin content. Previously a 0.0-confidence
            # concept was visually identical to a well-sourced one.
            if low_confidence:
                # Say which of the two happened. "The grounding pass found
                # little" is false when the grounding pass never ran, and a
                # learner reading it would conclude the subject is thinly
                # documented rather than that our service was down.
                if research_failed:
                    structured_md += (
                        "\n\n> **Unverified — grounding unavailable.** The "
                        "research pass could not be reached for this concept "
                        f"({research_failed.split(':')[0]}), so nothing was "
                        "checked against outside sources and this rests "
                        "entirely on the model's own knowledge. This is a gap "
                        "on our side, not a sign that sources are scarce.\n")
                else:
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

            # Update counters atomically.
            #
            # A STUB IS A FAILURE. This used to increment hydrated_count for a
            # "[Hydration failed]" document and never touch failed_count, so a
            # course where every single concept stubbed arrived at the abort
            # gate below with failed_count == 0, sailed past the >50% test, and
            # was written out as status "ready" — a course of placeholders
            # presented as a finished one. That is the exact
            # structurally-clean-but-substantively-empty failure this pipeline
            # exists to catch, and the pipeline was producing it itself.
            with _counter_lock:
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

        # THREE DIFFERENT DENOMINATORS, and conflating them is what produced
        # false verdicts:
        #   total_concepts        what THIS run attempted
        #   verified              what THIS run actually validated
        #   course_total_concepts every concept in the course
        # The failure rate is a property of this run. The level/fact/grounding
        # verdicts are claims about the COURSE, so they must say how much of the
        # course they cover — otherwise a resume with one concept left reports
        # "100% met, level_verified: true" on a course that is mostly stubs.
        total_concepts = len(concept_list)
        verified = hydrated_count
        # Whether THIS RUN validated the whole course — not whether the course
        # is now fully populated. The concepts a resume skipped were validated
        # by an earlier run, and this run is about to OVERWRITE that run's
        # verdict with one computed from its own handful of concepts. Crediting
        # the skipped ones here is what let a resume with one concept left
        # write "100% met, level_verified: true" over a verdict that had
        # covered all 36.
        full_course_run = verified >= course_total_concepts

        # A course with no concepts at all was never verified by anything: the
        # abort gate, all four verdicts and the asset phase were skipped by
        # their `> 0` guards, and it still reached status "ready". An empty
        # course is a failed build, not a finished one.
        if course_total_concepts == 0:
            course["status"] = "failed"
            self.storage.courses.update_course(course_uid, course)
            msg = (f"Course '{course_title}' contains no concepts — nothing to "
                   f"hydrate and nothing verified. Marked as failed.")
            logger.error(msg)
            if self.status_callback:
                self.status_callback(f"ERROR: {msg}")
            raise CourseCreationError(msg)

        # AUTO-9: Abort if >50% of concepts failed hydration. Stubs now count as
        # failures (see the counter block above), so a fully-stubbed run trips
        # this instead of shipping.
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
        #
        # `concepts_total` is the WHOLE COURSE, not this run: the number is read
        # as a claim about the course, and it used to be len(concept_list),
        # which on a resume is only the leftovers. `met_pct` is the share of
        # what was actually validated, and `level_verified` additionally
        # requires that the validation covered the whole course — a course is
        # not "verified at level 4" because the single concept a resume happened
        # to redo passed.
        if self.enforce_depth and verified > 0:
            missed = len(self._contract_failures)
            met_pct = round(100 * (verified - missed) / verified, 1)
            course["depth_contract"] = {
                "mastery": self.mastery_level,
                "domain": self.topic_domain,
                "concepts_total": course_total_concepts,
                "concepts_verified": verified,
                "concepts_unverified": max(0, course_total_concepts - verified),
                "verified_pct": round(100 * verified / course_total_concepts, 1),
                "concepts_missing_contract": missed,
                # Of what was verified this run.
                "met_pct": met_pct,
                # Below this the course is not credibly at its stated level —
                # and a partial run cannot make the claim at all.
                "level_verified": bool(met_pct >= 80.0 and full_course_run),
                "partial_verification": not full_course_run,
                "failures": self._contract_failures[:25],
            }
            if missed:
                logger.warning(
                    f"[DEPTH] {missed}/{verified} verified concepts missed the "
                    f"mastery-{self.mastery_level} contract ({met_pct}% met; "
                    f"{verified}/{course_total_concepts} of the course verified)")
                if self.status_callback:
                    self.status_callback(
                        f"STRUCT:WARN:DEPTH_SUMMARY:{missed}/{verified} "
                        f"concepts below level {self.mastery_level}")
            if not full_course_run:
                logger.warning(
                    f"[DEPTH] partial run: only {verified}/{course_total_concepts} "
                    f"concepts were validated this run — level_verified withheld")

        # A1: course-level factual verdict. A course still carrying confirmed
        # false claims must not be presented as verified at its level.
        #
        # clean_pct is the share of CHECKED concepts that came back clean. It
        # used to divide by this run's concept count while `bad` came from a
        # sampled subset, which mixed two populations and always flattered the
        # course; on a resume it divided by the leftovers as well.
        if self.fact_check_enabled and self._fact_checked_count > 0:
            bad = len(self._fact_failures)
            checked = self._fact_checked_count
            course["fact_check"] = {
                "concepts_total": course_total_concepts,
                "concepts_checked": checked,
                "sample_fraction": getattr(self, "fact_check_sample", 1.0),
                "coverage_pct": round(100 * checked / course_total_concepts, 1),
                "concepts_with_false_claims": bad,
                "clean_pct": round(100 * (checked - bad) / checked, 1),
                "partial_verification": not full_course_run,
                "failures": self._fact_failures[:25],
            }
            if bad:
                logger.warning(
                    f"[FACT] {bad}/{checked} checked concepts still contain "
                    f"confirmed-false claims after regeneration "
                    f"({checked}/{course_total_concepts} of the course checked)")
                if self.status_callback:
                    self.status_callback(
                        f"STRUCT:WARN:FACT_SUMMARY:{bad}/{checked}")

        # Gate criterion 2: does the course READ at the level it claims?
        # The depth contract checks markers, the fact-checker checks truth;
        # neither asks whether the material is actually pitched where it was
        # sold. Judged blind — level hints are stripped first.
        #
        # Judged on the WHOLE course's bodies, not this run's. Reading a
        # resume's three leftover concepts and pronouncing on the course was the
        # same partial-run error as above, and here it was invisible because the
        # verdict carries no denominator of its own.
        if course_total_concepts > 0 and self.level_calibration_enabled:
            try:
                from services.common.level_calibration import calibrate
                bodies = []
                for _uid in all_concept_uids_in_order:
                    try:
                        b = self.storage.courses.get_concept_content(
                            course_uid, _uid)
                        if b and "[Hydration failed]" not in b:
                            bodies.append(b)
                    except Exception:
                        continue
                verdict = calibrate(bodies, self.mastery_level)
                if verdict:
                    verdict["concepts_judged"] = len(bodies)
                    verdict["concepts_total"] = course_total_concepts
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
        #
        # Grounding is only observed while a concept is being hydrated, so this
        # can only speak for the concepts this run hydrated. It says so, instead
        # of dividing by a run-sized number and reading as a whole-course claim.
        if verified > 0:
            weak = len(self._low_confidence_concepts)
            unreachable = len(self._research_errors)
            course["grounding"] = {
                "confidence_floor": self.confidence_floor,
                "concepts_total": course_total_concepts,
                "concepts_measured": verified,
                "concepts_below_floor": weak,
                # Reported separately from `concepts_below_floor` on purpose:
                # a concept nobody could look up is our outage, and a concept
                # with genuinely thin sources is a fact about the subject.
                # Collapsed into one number they are indistinguishable, and the
                # first one silently disappears into "this topic is obscure".
                "concepts_research_unreachable": unreachable,
                "well_grounded_pct": round(100 * (verified - weak) / verified, 1),
                "partial_verification": not full_course_run,
                "low_confidence": self._low_confidence_concepts[:25],
                "research_errors": self._research_errors[:25],
            }
            if weak:
                logger.warning(
                    f"[GROUNDING] {weak}/{verified} concepts hydrated this run "
                    f"are below confidence floor {self.confidence_floor} "
                    f"({verified}/{course_total_concepts} of the course)")
            if unreachable:
                logger.error(
                    f"[GROUNDING] the research service was unreachable for "
                    f"{unreachable}/{verified} concepts — those are ungrounded "
                    f"because of an outage, not because sources are scarce")
                if self.status_callback:
                    self.status_callback(
                        f"CHECK:RESEARCH:WARN:unreachable for {unreachable}/{verified} concepts")

        # Structural completeness: sections the MODEL never wrote.
        #
        # This used to be recorded only in `_last_injected_sections`, an
        # attribute with no reader anywhere in the tree — so a run in which the
        # model omitted 36 sections and a run in which it omitted none produced
        # identical course records. Turning stub injection off by default
        # (scaffolding.STUB_MISSING_SECTIONS) stopped us MANUFACTURING the
        # missing sections; it did not make their absence legible. This does.
        #
        # Measured on one 6-concept gate run: Ministral needed 36 injections and
        # Mistral-Small needed 1, and both shipped courses that read as
        # "complete" everywhere downstream.
        #
        # Only concepts this run hydrated can be counted — a resume does not
        # regenerate the rest, so their misses are unknown here rather than
        # zero. `partial_verification` says so, exactly as the other verdicts do.
        if verified > 0:
            by_concept = {}
            sections_missing_total = 0
            with self._verdict_lock:
                for rec in self._missing_sections:
                    by_concept[rec["uid"]] = {
                        "title": rec["title"],
                        "missing": rec["missing"],
                        "count": len(rec["missing"]),
                    }
                    sections_missing_total += len(rec["missing"])
            incomplete = len(by_concept)
            course["missing_sections"] = {
                # Whether the gaps were papered over with placeholder text. A
                # reader of this verdict needs to know which of the two
                # documents they are looking at.
                "stub_injection": bool(_scaf.STUB_MISSING_SECTIONS),
                "concepts_total": course_total_concepts,
                "concepts_measured": verified,
                "concepts_incomplete": incomplete,
                "sections_missing_total": sections_missing_total,
                "complete_pct": round(100 * (verified - incomplete) / verified, 1),
                "partial_verification": not full_course_run,
                # Keyed by concept uid, and ONLY concepts with at least one
                # missing section — absence from this map means the model wrote
                # every required heading. Capped like the other verdicts'
                # detail lists so a bad run cannot bloat structure.json;
                # `concepts_incomplete` is always the true count.
                "by_concept": dict(list(by_concept.items())[:50]),
            }
            if incomplete:
                logger.warning(
                    f"[SECTIONS] {incomplete}/{verified} hydrated concepts are "
                    f"missing {sections_missing_total} required section(s) the "
                    f"model never wrote"
                    + (" (placeholders were injected)"
                       if _scaf.STUB_MISSING_SECTIONS else ""))
                if self.status_callback:
                    self.status_callback(
                        f"STRUCT:WARN:SECTIONS_SUMMARY:{incomplete}/{verified} "
                        f"concepts incomplete")

        # ---- PHASE 3: ASSET COLLECTION -------------------------------------
        # Runs after the content and its verdicts, before the course is
        # enterable. Every diagram the course will use is drawn HERE, where a
        # retry is free and generation can be grammar-constrained — neither of
        # which is true inside a 30-second dialogue turn. A session then only
        # selects from what this produced.
        #
        # Strictly degradable: a course with no pictures is a course, so any
        # failure here is logged and the build continues.
        if course_total_concepts > 0:
            # Asset collection is quiet for long stretches (one constrained
            # generation per diagram), so it needs its own staleness budget.
            self._build_stage("assets", 80)
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
        summary_msg = (
            f"Course hydration complete: {hydrated_count}/{total_concepts} "
            f"succeeded, {failed_count} failed. "
            f"{verified + already_hydrated}/{course_total_concepts} concepts in "
            f"the course now have real content. {len(md_files)} .md files written.")
        if hydration_fallback_count > 0:
            summary_msg += (
                f" {hydration_fallback_count} concept(s) fell back to stub content "
                f"and are counted as failures, not as hydrated.")
        logger.info(summary_msg)
        if self.status_callback:
            self.status_callback(f"LOG: {summary_msg}")
            if failed_count > 0:
                self.status_callback(
                    "CHECK:HYDRATION:WARN:Some concepts failed to hydrate."
                )

    def _revalidate_after_fact_check(self, md, uid, title, course_title,
                                     research_sources):
        """Re-check the depth contract on a document the fact-check replaced.

        The correction is KEPT either way. Reverting to the pre-correction draft
        would trade a confirmed-false claim for a structural marker, which is a
        bad trade: a wrong statement at the right level is worse than a right
        statement at the wrong one. What changes is the RECORD — the contract
        verdict must describe the document that actually shipped, and it was
        describing one that had been discarded.

        Also clears an earlier failure when the regenerated document now meets
        the contract, so a repair is not still reported as a miss.
        """
        try:
            ok, problems, _ = validate_concept(
                md, self.mastery_level, course_title, self.topic_domain,
                sources=research_sources)
        except Exception as e:
            logger.warning(f"  [DEPTH] post-fact-check validation failed for "
                           f"'{title}': {e}")
            return
        with self._verdict_lock:
            existing = next(
                (f for f in self._contract_failures if f.get("uid") == uid), None)
            if ok:
                if existing:
                    self._contract_failures.remove(existing)
                    logger.info(
                        f"  [DEPTH] '{title}' now meets the contract after "
                        f"fact-check regeneration")
                return
            if existing:
                existing["problems"] = problems
                existing["after_fact_check"] = True
                return
            self._contract_failures.append({
                "uid": uid, "title": title, "problems": problems,
                # Distinguishable in the failures list: this concept DID meet
                # its contract, and the accuracy repair cost it.
                "after_fact_check": True,
            })
        logger.warning(
            f"  [DEPTH] '{title}' met its contract before the fact-check and "
            f"misses it after regeneration ({'; '.join(problems)}) — the "
            f"corrected text is kept, the verdict records the loss")
        if self.status_callback:
            self.status_callback(f"STRUCT:WARN:DEPTH_MISS_AFTER_FACT:{title}")

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
        result["remainder"] = " ".join(other)[:_scaf.RESEARCH_REMAINDER_CHARS]
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
        source_material = (remainder[:_scaf.SOURCE_MATERIAL_CHARS] if remainder
                               else "Use your internal knowledge.")
        if not research_input and raw_text:
            source_material = raw_text[:_scaf.RAW_TEXT_CHARS]

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
        if SCRATCHPAD_ENABLED:
            # The band is enforced AFTER the notes are stripped, so counting
            # them against it would make the budget unmeetable in a way the
            # model cannot see.
            total_budget_note += (
                f" The '{SCRATCHPAD_HEADING}' section is removed before this "
                f"is measured and does NOT count toward the budget.")

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

        # Free-text reasoning ahead of the formatted answer. See
        # SCRATCHPAD_ENABLED at module level for the evidence, the mechanism and
        # why this is off by default. Order matters: it is FIRST so it is
        # decoded first, which is the entire point — reasoning that comes after
        # the answer cannot change the answer.
        scratchpad_section = ""
        if SCRATCHPAD_ENABLED:
            scratchpad_section = f"""## {SCRATCHPAD_HEADING}
[NOT part of the lesson. This section is deleted before anyone reads the
document and does not count toward the length budget. Work here FIRST, in
ordinary prose, before writing a single heading below.
If this concept involves a calculation, a derivation or a worked example: choose
the numbers here, carry the work through to the answer, and check that each step
follows from the one before AND that the quantities are the ones "{title}" is
actually about. If the check fails, fix it here — then write the corrected
version into the sections below.
If it involves no calculation, state in one or two sentences what distinguishes
"{title}" from the concepts next to it, and write to that.]

"""

        # Build the LLM-generated section template
        section_template = f"""{scratchpad_section}## Mastery Criteria
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
                if SCRATCHPAD_ENABLED:
                    # The section count already bought ~400 tokens for it; free
                    # reasoning is wordier than a templated section, and the one
                    # way this change can do real damage is by starving the
                    # lesson to pay for notes we then delete.
                    _budget += 300
                llm_output = llm_generate(
                    user_prompt,
                    sys_prompt=sys_prompt,
                    max_tokens=_budget,
                    progress_callback=self.status_callback,
                )
                # Strip BEFORE anything measures this text. The notes must not
                # reach the 40-word usability floor (a document that is all
                # notes is a failed generation and should retry), the missing-
                # section check (notes that quote "## Key Facts" would satisfy
                # it without the section existing), the depth contract's word
                # band, the fact-checker, or the learner.
                llm_output = self._strip_scratchpad(llm_output, title)
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

    def _strip_scratchpad(self, llm_output: str, title: str) -> str:
        """Remove the free-text reasoning section from a generated concept.

        The notes exist so the model can work the problem before it formats the
        answer (see SCRATCHPAD_ENABLED at module level). They are working-out,
        not teaching material, and they are the only part of this document that
        was never checked by anything — so they must not survive to storage.

        A no-op when the switch is off, so the A/B is a single env var.
        """
        if not SCRATCHPAD_ENABLED or not llm_output:
            return llm_output

        stripped = _SCRATCHPAD_RE.sub("", llm_output).lstrip()
        if stripped == llm_output.lstrip():
            # The model ignored the instruction, or wrote the notes under a
            # heading we do not recognise. Either way this concept got the token
            # cost of the experiment and none of its benefit, which is exactly
            # what a gate run has to be able to see.
            logger.warning(
                f"  [SCRATCHPAD] '{title}': no '{SCRATCHPAD_HEADING}' section "
                f"found — reasoning-first had no effect on this concept")
            return llm_output

        # Belt and braces: the heading regex only catches ATX headings. If the
        # marker still appears as bold text or a list item, working-out is about
        # to be published as lesson content.
        if SCRATCHPAD_HEADING.lower() in stripped.lower():
            logger.warning(
                f"  [SCRATCHPAD] '{title}': '{SCRATCHPAD_HEADING}' still "
                f"present after stripping — check the stored document")
        logger.debug(
            f"  [SCRATCHPAD] '{title}': removed "
            f"{len(llm_output.split()) - len(stripped.split())} words of notes")
        return stripped

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

        missing = [h for h in required_sections if h not in content]
        for section_header in missing:
            logger.warning(
                f"  [MARKDOWN] Missing '{section_header}' in LLM output for {title}."
                + (" Injecting stub." if _scaf.STUB_MISSING_SECTIONS else " NOT injecting (lean mode).")
            )

        # Record what the MODEL failed to produce, separately from what we
        # patched. Without this a document that needed 36 injections and one
        # that needed none are indistinguishable downstream — which is exactly
        # how a weak model scored like a strong one on the gate.
        #
        # `_last_injected_sections` is the last attempt only, and for two years
        # it was the ONLY record — a write with no reader anywhere in the tree,
        # so it made nothing visible. It is kept as a debugging convenience; the
        # durable record is the doc-keyed map below.
        self._last_injected_sections = list(missing)

        if _scaf.STUB_MISSING_SECTIONS:
            for section_header in missing:
                content += f"\n{required_sections[section_header]}"

        # Attach the miss to THIS document rather than to the concept.
        #
        # One concept can be generated up to five times (three generate
        # attempts, then depth-contract retries, then a fact-check
        # regeneration), and the document that ships is not necessarily the last
        # one produced — _enforce_depth_contract keeps whichever attempt had the
        # fewest problems. A per-concept "last write wins" field would therefore
        # describe a draft that was thrown away. Keying on the returned document
        # means _hydrate_one can look up the miss for the draft it actually
        # kept, whichever that turns out to be.
        self._note_missing_for_doc(content, missing)

        return content

    @staticmethod
    def _doc_key(md: str) -> str:
        """Short stable key for a generated document. Hashed rather than using
        the markdown itself so the map does not hold a copy of every draft."""
        return hashlib.sha1((md or "").encode("utf-8", "replace")).hexdigest()

    def _note_missing_for_doc(self, md: str, missing: list):
        with self._verdict_lock:
            # Bounded: a build generates at most a few hundred drafts, but this
            # object outlives a single hydrate() on the wizard path.
            if len(self._doc_missing) > 2000:
                self._doc_missing.clear()
            self._doc_missing[self._doc_key(md)] = list(missing)

    def _missing_for_doc(self, md: str) -> list:
        with self._verdict_lock:
            return list(self._doc_missing.get(self._doc_key(md), []))


class SyllabusAuditor:
    """
    Second-pass LLM Auditor to prune and rename course structure before
    expensive content hydration begins.
    Now operates on JSON structure instead of KuzuDB.

    WHAT THE AUDIT IS ALLOWED TO DO
    -------------------------------
    Prune and rename. Not rebuild. The audit is a single unconstrained-ish LLM
    call handed 100+ uids and asked to echo the ones it objects to, and it was
    trusted absolutely: no uid was checked for existence, no ceiling limited how
    much one call could remove, and `module` was a deletable type — so one
    hallucinated uid silently removed an entire module and every unit, lesson
    and concept under it, and the audit then REPORTED that deletion as an
    applied fix whether or not anything had been removed.

    Three guards now sit between the model and the structure:
      * a uid enum in the JSON schema, so a uid that is not in this course
        cannot be generated in the first place;
      * an existence check at apply time, so what is reported is what happened;
      * a deletion cap measured in concepts, so no single audit can gut a
        course, and modules are not deletable at all.
    """

    # A structural audit prunes at the margins. Anything larger is the audit
    # disagreeing with the course rather than correcting it, and the course was
    # built by the module/unit/lesson/concept phases against the scope, the
    # Bloom ramp and the curriculum evidence — the audit sees none of that.
    AUDIT_MAX_DELETE_FRACTION = float(os.getenv("HELGA_AUDIT_MAX_DELETE", "0.25"))
    # Floor: a course must still be a course afterwards.
    AUDIT_MIN_CONCEPTS = int(os.getenv("HELGA_AUDIT_MIN_CONCEPTS", "3"))
    # Modules are renameable but NOT deletable. Deleting one discards a whole
    # branch of the Bloom progression, which nothing downstream can rebuild.
    DELETABLE_TYPES = frozenset({"unit", "lesson", "concept"})

    def __init__(
        self, db_path: str = None, status_callback=None, storage: StorageManager = None
    ):
        self.db_path = db_path
        self.status_callback = status_callback
        # Mirror the audit into the durable build record — see the note in
        # ContentHydrator.__init__. A no-op when no build is active.
        try:
            from services.common import build_state
            self.status_callback = build_state.tee(self.status_callback)
        except Exception as e:
            logger.debug(f"build_state tee unavailable: {e}")
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
                    if ratio > _scaf.TITLE_DEDUP_RATIO:
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

    def audit(self, course_uid: str, target_depth: int = 2, mastery: int = None):
        """Audit a course skeleton.

        `mastery` is the ladder the audit judges complexity against, and it is
        preferred over `target_depth` — falling back to the course's own stored
        mastery, then to target_depth for legacy callers.

        WHY THEY ARE NOT THE SAME NUMBER: scope, mastery and starting_from are
        three INDEPENDENT sliders (compute_course_params). The only caller,
        fsm_logic, passes `target_depth=depth`, and `depth` is derived from
        scope — so on any course where the learner set a broad scope and a
        modest mastery (or the reverse) the audit was reading
        DEPTH_PROFILES[scope]["academic_level"] and DELETING or RENAMING
        concepts for "complexity mismatch" against a ladder the course was never
        built to. The hydrator, the depth contract and level calibration all use
        mastery; the auditor was the odd one out, and it is the only one of them
        that destroys structure.
        """
        if self.status_callback:
            self.status_callback("SYLLABUS:AUDIT:STARTING")

        course = self.storage.courses.get_course(course_uid)
        if not course:
            logger.warning("Course not found for audit.")
            return

        topic = course.get("title", "Unknown Topic")

        if mastery is None:
            mastery = course.get("mastery")
        try:
            audit_level = int(mastery) if mastery is not None else int(target_depth)
        except (TypeError, ValueError):
            audit_level = int(target_depth)
        audit_level = max(1, min(5, audit_level))
        logger.info(
            f"Starting Syllabus Audit for course {course_uid} at mastery "
            f"{audit_level}/5 (caller passed target_depth={target_depth}, "
            f"course records mastery={course.get('mastery')})"
        )

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

        # Judge against the MASTERY ladder — the same one the hydrator, the
        # depth contract and level calibration use. DEPTH_PROFILES is indexed by
        # the legacy single-depth number and its "academic_level" answers a
        # different question; see the docstring on audit().
        m_profile = MASTERY_PROFILES.get(audit_level, MASTERY_PROFILES[2])
        target_context = m_profile["label"]
        level_guidance = m_profile["vocabulary"]

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

        # The deletion budget, computed BEFORE the call so it can be stated in
        # the prompt. A model told the ceiling proposes fewer over-budget fixes
        # than one that discovers it by having them refused.
        delete_budget = self._delete_budget(before_concepts - dedup_count)

        prompt = (
            f"Topic: {topic}\n"
            f"Mastery: {audit_level}/5 ({target_context}) — {level_guidance}\n\n"
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
            f"3. COMPLEXITY MATCH: this course is pitched at mastery {audit_level}/5 "
            f"({target_context}: {level_guidance}). Early modules (level 1-2) should cover "
            "definitional/conceptual material and later modules (level 3+) application, analysis "
            "and synthesis. If a concept is far off the module's level, RENAME it to match; "
            "DELETE only if it genuinely does not belong in this course at all.\n\n"
            "4. REMAINING SEMANTIC DUPLICATES: If you spot concepts with different titles that "
            "teach the same thing (e.g. 'Causal Graphs' and 'Directed Acyclic Graphs in Causation') "
            "→ DELETE the less specific one. Titles that differ only by an ordinal or a side "
            "('World War I' / 'World War II', 'Left Ventricle' / 'Right Ventricle') are NOT "
            "duplicates.\n\n"
            "5. EMPTY LESSONS: a lesson showing (0 concepts) is a hole in the learning path → "
            "DELETE that lesson. A lesson with 1 concept is thin but valid — leave it alone. "
            "There is no move and no reorder action; do not ask for one.\n\n"
            "### OUTPUT FORMAT: JSON Array of fix objects. Return [] if structure is clean.\n"
            "Valid actions: 'rename', 'delete'.\n"
            "'rename' may target a module, unit, lesson or concept. "
            "'delete' may target ONLY a unit, lesson or concept — a module is never deletable.\n"
            f"HARD LIMIT: your deletions may remove at most {delete_budget} concepts in total "
            "(deleting a lesson or unit removes everything inside it, and all of that counts). "
            "Fixes past that limit are refused, so spend the budget on the clearest defects.\n"
            "Every 'uid' MUST be copied exactly from the hierarchy above. A uid that is not in "
            "the hierarchy is discarded.\n"
            "Each fix: {\"action\": \"rename\"|\"delete\", \"type\": \"module\"|\"unit\"|\"lesson\"|\"concept\", "
            "\"uid\": \"the_uid\", \"new_title\": \"...\" (for rename only), \"reason\": \"brief explanation\"}\n\n"
            "Be thorough. Check EVERY concept title. Return ALL fixes needed.\n"
        )

        # Constrain the uid to the uids that actually exist.
        #
        # This call hands the model 100+ uids and asks it to echo back the ones
        # it objects to; echoing a long hex string is exactly what a model gets
        # wrong, and an invented `mod_xxxxxxxx` used to delete a whole module.
        # An enum in the schema means a uid that is not in this course cannot be
        # DECODED, let alone applied — a guard at the generator rather than a
        # check afterwards. (llm_generate_json passes the schema to Ollama's
        # `format`; see services/common/llm_utils.py.)
        known_uids = self._all_uids(course)
        fix_schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["rename", "delete"]},
                    "type": {"type": "string",
                             "enum": ["module", "unit", "lesson", "concept"]},
                    "uid": {"type": "string", "enum": known_uids},
                    "new_title": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["action", "type", "uid", "reason"],
            },
        }
        _log_prompt_budget("audit", prompt, 2000)
        fixes = llm_generate_json(
            prompt,
            sys_prompt=(
                "You are a Senior Curriculum Editor performing a rigorous quality audit. "
                "Return ONLY a valid JSON array of fix objects. No commentary outside the JSON. "
                "Be aggressive about renaming vague titles — every concept should have a specific, "
                "teachable name that a student could look up in a textbook. "
                "Be conservative about deleting: renaming is reversible, deleting is not."
            ),
            max_tokens=2000,
            expected_type="list",
            schema=fix_schema,
        )

        rename_count = 0
        llm_delete_count = 0

        if fixes:
            if self.status_callback:
                self.status_callback(f"AUDIT:PASS2:FIXING:{len(fixes)}_ISSUES")

            for fix in fixes:
                if not isinstance(fix, dict):
                    continue
                reason = fix.get("reason", "")
                if reason:
                    logger.info(f"  LLM fix: {fix.get('action','')} "
                                f"{fix.get('type','')} {fix.get('uid','')} — {reason}")

            # Counted from what was APPLIED, not from what was proposed. The
            # counts used to come from a second walk over `fixes`, so a fix that
            # was skipped — invalid type, missing node, over budget — was still
            # reported to the learner as a rename or a deletion that happened.
            report = self._apply_fixes(course_uid, course, fixes,
                                       delete_budget=delete_budget)
            rename_count = report["renamed"]
            llm_delete_count = report["deleted_concepts"]

            logger.info(
                f"Pass 2 complete: applied {report['applied']}/{len(fixes)} LLM fixes "
                f"({rename_count} renames, {report['deleted_nodes']} node deletions "
                f"removing {llm_delete_count} concepts). "
                f"Refused: {report['skipped_missing']} hallucinated uid(s), "
                f"{report['skipped_module_delete']} module deletion(s), "
                f"{report['skipped_over_budget']} over the {delete_budget}-concept cap, "
                f"{report['skipped_other']} other.")
            if report["skipped_missing"]:
                if self.status_callback:
                    self.status_callback(
                        f"AUDIT:WARN:UNKNOWN_UIDS:{report['skipped_missing']}")
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
        # The two numbers must agree. They disagreed for as long as deletions
        # were counted from what the model PROPOSED, and the reported figure was
        # the one nobody could check against the structure.
        if before_concepts - total_concepts != total_deleted:
            logger.warning(
                f"Audit accounting mismatch: reported {total_deleted} deletions "
                f"but the structure lost {before_concepts - total_concepts} "
                f"concepts")

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

    def _delete_budget(self, concepts_now: int) -> int:
        """How many concepts one audit may remove.

        Without a ceiling a single LLM call could take the course apart: every
        proposed deletion was applied, `module` was deletable, and one
        hallucinated module uid removes every unit, lesson and concept beneath
        it. The cap is expressed in CONCEPTS because that is what a learner
        loses, whatever node the delete names.
        """
        if concepts_now <= self.AUDIT_MIN_CONCEPTS:
            return 0
        by_fraction = int(concepts_now * self.AUDIT_MAX_DELETE_FRACTION)
        by_floor = concepts_now - self.AUDIT_MIN_CONCEPTS
        return max(0, min(by_fraction, by_floor))

    def _all_uids(self, course: dict) -> List[str]:
        """Every uid in the course, for the audit call's uid enum."""
        uids = []
        for module in course.get("modules", []):
            uids.append(module.get("uid", ""))
            for unit in module.get("units", []):
                uids.append(unit.get("uid", ""))
                for lesson in unit.get("lessons", []):
                    uids.append(lesson.get("uid", ""))
                    for concept in lesson.get("concepts", []):
                        uids.append(concept.get("uid", ""))
        return [u for u in uids if u]

    def _find_node(self, course: dict, node_type: str, uid: str):
        """The node with this uid AND this type, or None."""
        for module in course.get("modules", []):
            if node_type == "module" and module.get("uid") == uid:
                return module
            for unit in module.get("units", []):
                if node_type == "unit" and unit.get("uid") == uid:
                    return unit
                for lesson in unit.get("lessons", []):
                    if node_type == "lesson" and lesson.get("uid") == uid:
                        return lesson
                    for concept in lesson.get("concepts", []):
                        if node_type == "concept" and concept.get("uid") == uid:
                            return concept
        return None

    def _concept_weight(self, node_type: str, node: dict) -> int:
        """How many concepts deleting this node would remove.

        A delete addressed at a lesson or a unit is not one deletion — it is
        every leaf underneath. Charging only the named node against the budget
        would let "delete this unit" slip through a cap sized for concepts.
        """
        if node_type == "concept":
            return 1
        if node_type == "lesson":
            return len(node.get("concepts", []))
        if node_type == "unit":
            return sum(len(l.get("concepts", []))
                       for l in node.get("lessons", []))
        return sum(len(l.get("concepts", []))
                   for u in node.get("units", [])
                   for l in u.get("lessons", []))

    def _apply_fixes(self, course_uid: str, course: dict, fixes: List[Dict],
                     delete_budget: int = None) -> dict:
        """Apply audit fixes to the JSON structure.

        Returns a REPORT, not a bare count: {"applied", "renamed",
        "deleted_nodes", "deleted_concepts", "skipped_*"}. The caller used to
        count renames and deletes by walking the model's proposals again, which
        counted fixes that were never applied — the audit reported deletions
        that had not happened and there was no way to notice from the outside.
        """
        VALID_TYPES = {"module", "unit", "lesson", "concept"}
        report = {"applied": 0, "renamed": 0, "deleted_nodes": 0,
                  "deleted_concepts": 0, "skipped_missing": 0,
                  "skipped_module_delete": 0, "skipped_over_budget": 0,
                  "skipped_other": 0}
        had_deletes = False

        if delete_budget is None:
            delete_budget = self._delete_budget(self._count_structure(course)[3])

        for fix in fixes:
            if not isinstance(fix, dict):
                report["skipped_other"] += 1
                continue
            action = fix.get("action")
            f_type = (fix.get("type") or "").lower()
            uid = fix.get("uid")

            if f_type not in VALID_TYPES:
                logger.warning(
                    f"Auditor: Skipping fix with invalid type '{f_type}' (uid: {uid})"
                )
                report["skipped_other"] += 1
                continue

            if action == "rename":
                new_title = fix.get("new_title")
                if not (uid and new_title):
                    report["skipped_other"] += 1
                    continue
                # Check that the rename doesn't introduce a new collision
                new_lower = new_title.lower().strip()
                if hasattr(self, "_all_titles") and new_lower in self._all_titles:
                    logger.warning(
                        f"Auditor: Skipping rename to '{new_title}' — would create duplicate."
                    )
                    report["skipped_other"] += 1
                    continue
                # Rename only what exists. This was a filter with no existence
                # check and no return value, so a rename of a hallucinated uid
                # was a silent no-op that still incremented the applied count.
                if not self._rename_node(course, f_type, uid, new_title):
                    logger.warning(
                        f"Auditor: rename refused — no {f_type} with uid {uid} "
                        f"exists in this course")
                    report["skipped_missing"] += 1
                    continue
                logger.info(f"Auditor: Renamed {f_type} {uid} -> {new_title}")
                if hasattr(self, "_all_titles"):
                    self._all_titles.add(new_lower)
                report["applied"] += 1
                report["renamed"] += 1
                if self.status_callback:
                    self.status_callback(
                        f"LOG: Audit renamed {f_type}: {new_title}"
                    )

            elif action == "delete":
                if not uid:
                    report["skipped_other"] += 1
                    continue
                if f_type not in self.DELETABLE_TYPES:
                    logger.warning(
                        f"Auditor: delete refused — {f_type} {uid} is not a "
                        f"deletable type. Deleting a module discards a whole "
                        f"branch of the Bloom progression on the strength of one "
                        f"echoed uid.")
                    report["skipped_module_delete"] += 1
                    continue
                node = self._find_node(course, f_type, uid)
                if node is None:
                    logger.warning(
                        f"Auditor: delete refused — no {f_type} with uid {uid} "
                        f"exists in this course (hallucinated uid)")
                    report["skipped_missing"] += 1
                    continue
                weight = self._concept_weight(f_type, node)
                if report["deleted_concepts"] + weight > delete_budget:
                    logger.warning(
                        f"Auditor: delete refused — removing {f_type} "
                        f"'{node.get('title', uid)}' would cost {weight} concept(s) "
                        f"and the audit has already spent "
                        f"{report['deleted_concepts']}/{delete_budget}")
                    report["skipped_over_budget"] += 1
                    continue
                if not self._delete_node(course, f_type, uid):
                    logger.warning(
                        f"Auditor: delete of {f_type} {uid} found the node but "
                        f"removed nothing — not counted as applied")
                    report["skipped_other"] += 1
                    continue
                logger.info(
                    f"Auditor: Deleted {f_type} {uid} ({weight} concept(s))")
                had_deletes = True
                report["applied"] += 1
                report["deleted_nodes"] += 1
                report["deleted_concepts"] += weight
                if self.status_callback:
                    self.status_callback(f"LOG: Audit deleted {f_type} {uid}")

            elif action == "reorder":
                # NOT offered by the audit prompt and not in the response
                # schema, so nothing the model returns reaches here. Kept for
                # programmatic callers that build a fix list themselves.
                new_ord = fix.get("new_ordinal")
                if uid and new_ord is not None and self._reorder_node(
                        course, f_type, uid, new_ord):
                    logger.info(f"Auditor: Reordered {f_type} {uid} to {new_ord}")
                    report["applied"] += 1
                else:
                    report["skipped_other"] += 1
            else:
                logger.warning(f"Auditor: unknown action '{action}' — skipped")
                report["skipped_other"] += 1

        # Renumber ordinals after any deletes to avoid gaps (e.g., [1, 3] → [1, 2])
        if had_deletes:
            self._renumber_ordinals(course)

        # Save updated course
        self.storage.courses.update_course(course_uid, course)
        return report

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

    def _rename_node(self, course: dict, node_type: str, uid: str,
                     new_title: str) -> bool:
        """Rename a node. True if the node existed and was renamed.

        The return value is the point: this used to walk the tree and return
        None whether or not it found anything, and the caller counted the fix as
        applied regardless.
        """
        node = self._find_node(course, node_type, uid)
        if node is None:
            return False
        node["title"] = new_title
        return True

    def _delete_node(self, course: dict, node_type: str, uid: str) -> bool:
        """Delete a node. True if the node existed and was removed.

        This used to be a bare list filter with no existence check and no
        result. Deleting a uid that is not in the course removed nothing and
        reported success, so the audit log recorded deletions the structure
        never received — and there was no way to tell that from the outside,
        because the reported count came from the model's proposals rather than
        from the tree.
        """
        if node_type == "module":
            before = len(course.get("modules", []))
            course["modules"] = [
                m for m in course.get("modules", []) if m.get("uid") != uid
            ]
            return len(course["modules"]) < before
        for module in course.get("modules", []):
            if node_type == "unit":
                before = len(module.get("units", []))
                module["units"] = [
                    u for u in module.get("units", []) if u.get("uid") != uid
                ]
                if len(module["units"]) < before:
                    return True
            for unit in module.get("units", []):
                if node_type == "lesson":
                    before = len(unit.get("lessons", []))
                    unit["lessons"] = [
                        l for l in unit.get("lessons", []) if l.get("uid") != uid
                    ]
                    if len(unit["lessons"]) < before:
                        return True
                for lesson in unit.get("lessons", []):
                    if node_type == "concept":
                        before = len(lesson.get("concepts", []))
                        lesson["concepts"] = [
                            c for c in lesson.get("concepts", [])
                            if c.get("uid") != uid
                        ]
                        if len(lesson["concepts"]) < before:
                            return True
        return False

    def _reorder_node(self, course: dict, node_type: str, uid: str,
                      new_ordinal: int) -> bool:
        """Set a node's ordinal. True if the node existed."""
        node = self._find_node(course, node_type, uid)
        if node is None:
            return False
        node["ordinal"] = new_ordinal
        return True

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
