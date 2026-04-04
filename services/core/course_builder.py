import os
import gc
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
from typing import List, Dict, Optional, Tuple, Any
# Content providers removed — all content is LLM-generated
from services.common.storage import StorageManager
from services.common.llm_utils import (
    llm_generate,
    extract_python_list,
    llm_generate_json,
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


def compute_course_params(scope=2, mastery=2, starting_from=1):
    """Compute course structure parameters from three sliders."""
    s = SCOPE_PROFILES.get(scope, SCOPE_PROFILES[2])
    m = MASTERY_PROFILES.get(mastery, MASTERY_PROFILES[2])
    sf = STARTING_FROM_PROFILES.get(starting_from, STARTING_FROM_PROFILES[1])
    modules = max(2, s["module_base"] - sf["skip_factor"])
    concepts_per_module = m["concepts_per_module"]
    bloom_floor = sf["bloom_floor"]
    bloom_ceiling = m["bloom_ceiling"]
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
        self.used_titles = set()
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
        gc.collect()

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
        cleaned = re.sub(r"[^\w\s-]", "", cleaned)

        if cleaned.lower() in FORBIDDEN_TITLES:
            return ""

        word_count = len(cleaned.split())
        # Reject single-word titles unless they are acronyms (all caps) or very specific
        # (e.g., proper nouns with 8+ chars that are domain-specific like "Thermodynamics")
        if word_count < 2:
            # Allow all-caps acronyms like "DNA", "RNA", "TCP"
            if cleaned.upper() == cleaned and len(cleaned) <= 6:
                pass  # Allow acronyms
            elif len(cleaned) < 8:
                # Reject short single words — too generic to be useful
                return ""
            # Even long single words get checked against forbidden list (already done above)

        if cleaned:
            cleaned = cleaned[0].upper() + cleaned[1:]

        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip()

    def _sanitize_title(self, title: str) -> str:
        return self._normalize_title(title).lower()

    def _is_duplicate(
        self, new_title: str, course_topic: str = "", is_module: bool = False
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

        for used in self.used_titles:
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

            # 4. Similarity ratio (most expensive, only as last resort)
            ratio = difflib.SequenceMatcher(None, new_norm, used_clean).ratio()
            if ratio > SIMILARITY_THRESHOLD:
                logger.warning(
                    f"Similarity collision: '{new_norm}' vs '{used_clean}' (ratio {ratio:.2f})"
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
        start_t = time.time()
        try:
            health_url = LLM_API_URL.replace("/v1/chat/completions", "/")
            resp = requests.get(health_url, timeout=5)
            if resp.status_code == 200:
                lat = int((time.time() - start_t) * 1000)
                log_and_emit("✓", f"LLM Online (latency: {lat}ms)")
            else:
                log_and_emit("✗", f"LLM returned HTTP {resp.status_code}")
                all_passed = False
        except Exception as e:
            log_and_emit("✗", f"LLM Unreachable ({e})")
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

    def _build_inner(
        self, topic: str, max_depth: int = 2, module_depths: Dict[str, int] = None
    ) -> str:
        """The actual build logic, called under _build_lock."""
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

        # Build Bloom progression map — distribute levels across modules
        bloom_floor = cp["bloom_floor"]
        bloom_ceiling = cp["bloom_ceiling"]
        bloom_labels = {1: "Remember", 2: "Understand", 3: "Apply", 4: "Analyze", 5: "Evaluate", 6: "Create"}

        # Calculate per-module bloom targets
        bloom_range = bloom_ceiling - bloom_floor
        module_bloom_targets = []
        for i in range(target_modules):
            progress = i / max(target_modules - 1, 1)  # 0.0 to 1.0
            bloom = bloom_floor + round(progress * bloom_range)
            bloom = max(bloom_floor, min(bloom_ceiling, bloom))
            label = bloom_labels.get(bloom, "Apply")
            module_bloom_targets.append((bloom, label))

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
            module_dict = {
                "uid": m_uid,
                "title": m_title,
                "ordinal": i,
                "progression_role": role_desc,
                "scope": m_scope,
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
        self._build_substructures_progressive(module_refs, max_depth, topic, modules)

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

        # Write course structure to JSON
        self.storage.courses.create_course(course_dict)
        logger.info(f"Course structure written to JSON: {course_uid}")

        return course_uid

    def _build_substructures_progressive(
        self, module_refs, max_depth, topic, all_modules_metadata
    ):
        """
        Chunked hierarchical generation for reliable structure building.

        Strategy:
        1. Generate Units first (small, fast call)
        2. Generate Lessons per Unit (individual calls)
        3. Generate Concepts per Lesson (individual calls)

        This provides frequent progress updates and keeps each LLM call focused.
        """
        base_units = self.depth_profile.get("units_per_module", 1)
        base_lessons = self.depth_profile.get("lessons_per_unit", 1)
        # Use three-slider concepts_per_module divided across units/lessons
        target_concepts_per_module = self.course_params.get("concepts_per_module", self.depth_profile.get("concepts_per_lesson", 2) * base_lessons * base_units)
        base_concepts = max(2, target_concepts_per_module // max(1, base_units * base_lessons))
        mastery_constraint = self.course_params.get("mastery_writing", "")
        mastery_label = self.course_params.get("mastery_label", "Understanding")

        # Track full hierarchy for "mergy context" to avoid repetition
        planned_hierarchy_summary = []

        for m_ref in module_refs:
            m_title = m_ref["title"]
            m_role = m_ref["role_desc"]
            m_scope = m_ref["scope"]
            module_dict = m_ref["dict"]
            module_specific_depth = self.module_depths.get(m_title, max_depth)

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
            bloom_range = bloom_ceiling - bloom_floor
            progress = (ordinal - 1) / max(total_modules - 1, 1)
            module_bloom = bloom_floor + round(progress * bloom_range)
            module_bloom = max(bloom_floor, min(bloom_ceiling, module_bloom))
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
                max_tokens=600,
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
                if not u_title or self._is_duplicate(u_title, course_topic=topic):
                    u_title = f"{m_title} Part {u_idx}"
                    if not unit_used_fallback:
                        unit_used_fallback = True
                        self.fallback_count += 1
                        logger.warning(f"  [FALLBACK] Using fallback title for unit {u_idx} in module '{m_title}' — duplicate or empty.")

                self.used_titles.add(u_title)
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
                    f"Mastery Level: {mastery_label} ({self.mastery}/5)\n\n"
                    f"### SIBLING UNITS IN THIS MODULE (do NOT overlap with these):\n{sibling_units_str}\n\n"
                    f"### ALREADY GENERATED LESSONS (do NOT repeat or paraphrase):\n{prev_lessons_str}\n\n"
                    f"Generate exactly {base_lessons} lessons for this unit.\n"
                    f"Each lesson must cover a DISTINCT aspect within this unit's scope.\n"
                    f"TITLE RULES:\n"
                    f"- 3-8 words, complexity appropriate for {mastery_label} level.\n"
                    f"- Use real terminology from {topic}.\n"
                    f"- BANNED patterns: 'Introduction to X', 'Overview of X', 'Understanding X', "
                    f"'Role of X in Y', 'Significance of X', 'Impact of X on Y'.\n\n"
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
                    max_tokens=500,
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
                    if not l_title or self._is_duplicate(l_title, course_topic=topic):
                        l_title = f"{u_title} Lesson {l_idx}"
                        if not lesson_used_fallback:
                            lesson_used_fallback = True
                            self.fallback_count += 1
                            logger.warning(f"  [FALLBACK] Using fallback title for lesson {l_idx} in unit '{u_title}' — duplicate or empty.")

                    self.used_titles.add(l_title)
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
                        f"THIS MODULE'S Bloom Level: {_mod_bloom} ({_mod_bloom_label})\n\n"
                        f"### SIBLING LESSONS IN THIS UNIT (concepts must NOT overlap):\n{sibling_lessons_str}\n\n"
                        f"### CONCEPTS ALREADY GENERATED (do NOT repeat):\n{prev_concepts_str}\n\n"
                        f"Generate exactly {base_concepts} key concepts for this lesson on '{l_title}'.\n\n"
                        f"CONCEPT NAMING RULES (Bloom {_mod_bloom} — {_mod_bloom_label}):\n"
                        f"{naming_guide}\n"
                        f"- 1-6 words. Each concept must be narrower than the lesson title.\n"
                        f"- NEVER invent fake theorem or axiom names.\n\n"
                        f"Each concept needs 2 learning objectives appropriate for Bloom level {_mod_bloom} ({_mod_bloom_label}).\n"
                        f"Return JSON: [{{'title': 'Concept Name', 'objectives': ['Learn X', 'Understand Y']}}]"
                    )

                    concepts_sys = (
                        f"Expert curriculum designer for {topic}. "
                        f"This module is at Bloom level {_mod_bloom} ({_mod_bloom_label}). "
                        f"Match concept complexity to THIS module's level, not the course maximum. "
                        f"Return strict JSON array only. Never invent fake theorem or axiom names."
                    )
                    concepts_data = llm_generate_json(
                        concepts_prompt,
                        sys_prompt=concepts_sys,
                        max_tokens=600,
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
                            c_title, course_topic=topic
                        ):
                            continue  # Skip bad duplicates

                        self.used_titles.add(c_title)
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
                            "ordinal": c_idx,
                        }
                        if concept_data.get("llm_fallback"):
                            concept_dict["llm_fallback"] = True
                        lesson_dict["concepts"].append(concept_dict)

                        if self.status_callback:
                            self.status_callback(f"STRUCT:CONCEPT:{c_uid}:{c_title}")

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
        units_list = llm_generate_json(u_prompt, sys_prompt=sys_u, max_tokens=400) or []
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
                llm_generate_json(l_prompt, sys_prompt=sys_u, max_tokens=400) or []
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

        # Build hierarchy context and concept list from JSON
        concept_list = []
        hierarchy_map = {}
        module_source_map = {}
        concept_ref_map = {}  # Task #51: Map uid -> concept dict for in-place updates

        for module in course.get("modules", []):
            source_file = module.get("source_file", "")
            for unit in module.get("units", []):
                for lesson in unit.get("lessons", []):
                    for concept in lesson.get("concepts", []):
                        uid = concept["uid"]
                        title = concept["title"]
                        objectives = json.dumps(concept.get("learning_objectives", []))
                        complexity_role = concept.get("complexity_role", "")

                        # Check if already hydrated
                        existing_content = self.storage.courses.get_concept_content(
                            course_uid, uid
                        )
                        if existing_content and len(existing_content) > 100:
                            continue

                        user_note = concept.get("user_note", "") or module.get("user_note", "")
                        concept_list.append((uid, title, objectives, complexity_role, user_note))
                        concept_ref_map[uid] = concept  # Task #51: Store reference for source_confidence
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
        current_module_uid = None
        current_lesson_uid = None
        module_concepts_covered = []  # Track ALL concepts within a module for cross-lesson dedup
        lesson_concepts_covered = []  # Track concepts within current lesson
        local_providers_cache = {}

        for uid, title, objectives, complexity_role, user_note in concept_list:
            h_ctx = hierarchy_map.get(uid, {})
            l_uid = h_ctx.get("lesson_uid")
            m_uid = h_ctx.get("module_uid", None)

            # Reset module-level tracking when module changes
            if m_uid != current_module_uid:
                current_module_uid = m_uid
                module_concepts_covered = []

            # Reset lesson-level tracking when lesson changes
            if l_uid != current_lesson_uid:
                current_lesson_uid = l_uid
                lesson_concepts_covered = []

            try:
                logger.info(
                    f"Hydrating Concept [{hydrated_count + failed_count + 1}/{len(concept_list)}]: {title}"
                )

                # 2B: Hydration Pipeline Checks - Verify inputs
                if not title or len(title.strip()) < 3:
                    logger.warning(
                        f"  [SKIP] Concept missing valid title. Skipping hydration."
                    )
                    failed_count += 1
                    continue

                if not objectives or len(objectives) < 5:
                    logger.warning(
                        f"  [WARN] Concept '{title}' missing objectives. Proceeding with limited context."
                    )
                    if self.status_callback:
                        self.status_callback(
                            f"CHECK:HYDRATION:WARN:No objectives for {title}"
                        )

                if self.status_callback:
                    self.status_callback(f"STRUCT:HYDRATING:{uid}:START:{title}")
                text = ""

                # Multi-Source Collection
                search_queries = [f"{title} {course_title}", title]
                try:
                    obj_list = json.loads(objectives)
                    if isinstance(obj_list, list) and obj_list:
                        search_queries.append(obj_list[0])
                except:
                    pass

                # Content providers removed — LLM generates all content
                collected_snippets = []

                source_text = "\n\n".join(collected_snippets)
                # Decision: Use source text if found, otherwise let the structuring call handle it
                source_type = "multi-source" if collected_snippets else "llm-only"
                content_to_use = source_text if source_text else ""

                # Research service: fetch external reference material for this concept
                reference_material = ""
                research_sources = []
                research_confidence = 0.0
                research_url = os.getenv("RESEARCH_URL", "http://helga-research:5006")
                try:
                    research_resp = requests.post(
                        f"{research_url}/api/research_concept",
                        json={
                            "title": title,
                            "module_title": h_ctx.get("module", ""),
                            "course_title": course_title,
                        },
                        timeout=15,
                    )
                    if research_resp.status_code == 200:
                        research_data = research_resp.json()
                        reference_material = research_data.get("combined_text", "")
                        research_sources = research_data.get("sources", [])
                        research_confidence = research_data.get("confidence", 0.0)
                        if reference_material:
                            source_type = "research+llm"
                            logger.info(
                                f"  [RESEARCH] Got reference material for '{title}' "
                                f"(confidence={research_confidence:.2f}, {len(research_sources)} sources)"
                            )
                    else:
                        logger.debug(
                            f"  [RESEARCH] Non-200 response for '{title}': {research_resp.status_code}"
                        )
                except Exception as research_err:
                    logger.warning(f"  [RESEARCH] Research service unavailable for '{title}': {research_err}")

                # Merge research material into content_to_use
                if reference_material:
                    content_to_use = (content_to_use + "\n\n" + reference_material).strip()

                # Consolidated Hydration: Pedagogy + Structure in a single LLM call.
                # Falls back to LLM knowledge if no source text available.
                if self.status_callback:
                    self.status_callback(f"STRUCT:HYDRATING:{uid}:STRUCTURING:{title}")

                structured_md = self._condense_and_structure_content(
                    title,
                    content_to_use,
                    course_title,
                    self.mastery_level,  # Use mastery level, not raw depth
                    complexity_role,
                    source_type,
                    hierarchy_context=h_ctx,
                    previous_concepts=lesson_concepts_covered,
                    module_concepts=module_concepts_covered,
                    research_sources=research_sources,
                    research_confidence=research_confidence,
                    user_note=user_note,
                )

                # WIZ-3: Detect if hydration returned a fallback stub
                if "[Hydration failed]" in structured_md:
                    hydration_fallback_count += 1
                    logger.warning(f"  [FALLBACK] Hydration used fallback stub for concept '{title}' ({uid}).")
                    if self.status_callback:
                        self.status_callback(f"STRUCT:WARN:CONCEPT_STUB:{title}")

                # Append research source citations to the markdown if available
                if research_sources:
                    sources_md = "\n\n## Sources\n"
                    for src in research_sources:
                        src_title = src.get("title", "Untitled")
                        src_url = src.get("url", "")
                        src_type = src.get("type", "web")
                        tier = src.get("domain_tier", "")
                        tier_str = f" (Tier {tier})" if tier else ""
                        sources_md += f"- [{src_title}]({src_url}) — {src_type}{tier_str}\n"
                    sources_md += f"\n*Source confidence: {research_confidence:.2f}*\n"
                    structured_md += sources_md

                lesson_concepts_covered.append(title)
                module_concepts_covered.append(title)

                # Task #51: Store source_confidence on the concept dict in structure
                if uid in concept_ref_map:
                    concept_ref_map[uid]["source_confidence"] = round(research_confidence, 2)

                # Save to filesystem via StorageManager
                self.storage.courses.save_concept_content(
                    course_uid, uid, structured_md
                )

                if self.status_callback:
                    self.status_callback(f"STRUCT:HYDRATED:{uid}:{source_type}:{title}")

                hydrated_count += 1

                # Progressive availability: mark course "available" after first module completes
                if hydrated_count == 1:
                    course["status"] = "available"
                    course["hydrated_count"] = hydrated_count
                    self.storage.courses.update_course(course_uid, course)
                    if self.status_callback:
                        self.status_callback("COURSE_AVAILABLE")
                    logger.info(f"Course '{course_title}' now available for learning (1 concept hydrated, {len(concept_list)-1} remaining)")

            except Exception as concept_error:
                failed_count += 1
                logger.error(
                    f"  [CONCEPT FAILED] Hydration failed for concept '{title}' ({uid}): {concept_error}",
                    exc_info=True,
                )

            # Batch GC every 10 concepts instead of every single one (significant speedup)
            if (hydrated_count + failed_count) % 10 == 0:
                gc.collect()

        # Final GC after hydration loop
        gc.collect()

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
        self.storage.courses.update_course(course_uid, course)

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

        sys_prompt = (
            f"Expert Educational Content Architect specializing in {course_title}. "
            f"Writing level: {depth_desc}. "
            f"{writing_guide} "
            "Build a Teaching Guide using ONLY real, established knowledge. "
            "MATCH YOUR LANGUAGE AND COMPLEXITY TO THE SPECIFIED WRITING LEVEL. "
            "Never invent theorems, axioms, or method names. Output Markdown directly."
        )

        # Truncate source material to keep prompt focused
        source_material = (
            raw_text[:5000] if raw_text else "Use your internal knowledge of this topic."
        )

        # Build reference material section if research sources are available
        reference_section = ""
        if research_sources and research_confidence > 0:
            ref_source_list = ", ".join(
                s.get("title", "Unknown") for s in research_sources[:5]
            )
            reference_section = (
                f"\n### REFERENCE MATERIAL (confidence: {research_confidence:.2f}):\n"
                f"The following vetted reference material has been gathered from: {ref_source_list}.\n"
                f"Prioritize facts and explanations from this material. "
                f"Do not copy verbatim — synthesize into your own pedagogical explanation.\n"
            )

        # Build depth-appropriate section list
        sections_by_depth = {
            1: (
                f"## Core Definition\n[1-2 sentence simple definition in plain language]\n\n"
                f"## Contextual Explanation\n[~150 word intuitive explanation with an everyday analogy]\n\n"
                f"## Socratic Hook\n[One simple open-ended question using a concrete scenario]"
            ),
            2: (
                f"## Core Definition\n[1-2 sentence precise definition with key technical terms bolded]\n\n"
                f"## Component Breakdown\n- **[Part]**: [Description]\n\n"
                f"## Contextual Explanation\n[~200 word explanation with concrete examples]\n\n"
                f"## Socratic Hook\n[One open-ended question]"
            ),
            3: (
                f"## Core Definition\n[1-2 sentence precise definition that distinguishes this from related concepts]\n\n"
                f"## Component Breakdown\n- **[Part]**: [Description]\n\n"
                f"## Contextual Explanation\n[~250 word technical explanation. Focus on what is UNIQUE to this concept vs sibling concepts.]\n\n"
                f"## Misconceptions\n- **Belief**: ...\n- **Correction**: ...\n\n"
                f"## Socratic Hook\n[One open-ended question]"
            ),
            4: (
                f"## Core Definition\n[1-2 sentence formal definition. Include any standard notation or formal criteria.]\n\n"
                f"## Component Breakdown\n- **[Part]**: [Description with formal detail]\n\n"
                f"## Contextual Explanation\n[~300 word professional-level explanation. Cover formal conditions, key assumptions, and when/why this method is used. Include relationships to related methods.]\n\n"
                f"## Misconceptions\n- **Belief**: ...\n- **Correction**: ...\n\n"
                f"## Advanced Notes\n[Key edge cases, limitations, or conditions under which this breaks down]\n\n"
                f"## Socratic Hook\n[One probing question about assumptions or edge cases]"
            ),
            5: (
                f"## Core Definition\n[Formal definition with standard notation]\n\n"
                f"## Component Breakdown\n- **[Part]**: [Description with formal detail]\n\n"
                f"## Contextual Explanation\n[~400 word research-level explanation. Cover theoretical foundations, key results, and connections to related work.]\n\n"
                f"## Misconceptions\n- **Belief**: ...\n- **Correction**: ...\n\n"
                f"## Advanced Notes\n[Theoretical implications and boundary conditions]\n\n"
                f"## Research Frontiers\n[Open problems and active research directions]\n\n"
                f"## Socratic Hook\n[One research-level question]"
            ),
        }
        section_template = sections_by_depth.get(depth, sections_by_depth[3])

        user_prompt = f"""Topic: {title} | Course: {course_title} | Depth: {depth}/5 ({depth_desc})
{h_str}

### WRITING LEVEL: {writing_guide}

### DEDUPLICATION CONTEXT — DO NOT REPEAT content already covered:
- Concepts already covered in this lesson: {prev_str}
- Concepts already covered in this module: {module_prev_str}
Focus ONLY on what makes "{title}" DISTINCT from the above concepts.
{f"### USER NOTE (the learner specifically requested this focus):{chr(10)}{user_note}{chr(10)}" if user_note else ""}{reference_section}
Source Material: {source_material}

Output this EXACT Markdown structure:

# {title}

## Metadata
- **Depth**: {depth}
- **Path**: {context_path}
- **Source**: {source}

{section_template}

## Analogies
- [One helpful analogy that makes this concept intuitive]
"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                structured_md = llm_generate(
                    user_prompt,
                    sys_prompt=sys_prompt,
                    max_tokens=2000,
                    progress_callback=self.status_callback,
                )
                if structured_md and "## Core Definition" in structured_md:
                    return self._validate_markdown_structure(
                        structured_md,
                        title,
                        course_title,
                        depth,
                        source,
                        raw_text,
                        context_path,
                    )
            except Exception as e:
                logger.warning(f"  [MARKDOWN] Failed {title} (att {attempt + 1}): {e}")

        return f"# {title}\n\n## Metadata\n- **Depth**: {depth}\n- **Context**: {context_path}\n\n## Core Definition\n[Hydration failed]"

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
        # Base sections required at all depths
        required_sections = {
            "## Metadata": f"## Metadata\n- **Target Depth**: {depth}\n- **Context Path**: {context_path}\n- **Source**: {source}\n",
            "## Core Definition": f"## Core Definition\n{title} is a key concept in {course_title}.\n",
            "## Contextual Explanation": f"## Contextual Explanation\n{(raw_text or '')[:500]}\n",
            "## Socratic Hook": f"## Socratic Hook\nWhat do you think is the most important aspect of {title}?\n",
        }
        # Add depth-dependent sections
        if depth >= 3:
            required_sections["## Misconceptions"] = "## Misconceptions\n- **Belief**: None identified.\n- **Correction**: N/A\n"
        if depth >= 4:
            required_sections["## Advanced Notes"] = f"## Advanced Notes\nSee further reading on {title}.\n"

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

        profile = DEPTH_PROFILES.get(target_depth, DEPTH_PROFILES[2])
        target_context = profile["academic_level"]

        # Build compact text hierarchy for LLM
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

        prompt = (
            f"Topic: {topic}\nDepth: {target_depth}/5 ({target_context})\n\n"
            f"### MODULE SCOPE BOUNDARIES:\n{scope_summary}\n"
            f"### HIERARCHY:\n{hierarchy}\n\n"
            "### AUDIT TASKS (check ALL of these):\n"
            "1. DUPLICATE TITLES: Find titles that are identical or near-identical (e.g., 'Causal Sufficiency' appearing in two different modules). → RENAME the later occurrence to be more specific.\n"
            "2. SCOPE VIOLATIONS: Find concepts/lessons that belong in a DIFFERENT module's scope. → DELETE them.\n"
            "3. GENERIC TITLES: Fix titles ending in 'Basics','Overview','Context','Introduction','Understanding' → RENAME with specific technical content.\n"
            "4. VERB-ONLY TITLES: Fix titles that are just 'Identifying X' or 'Assessing Y' → RENAME to name the actual technique or property.\n\n"
            "### OUTPUT: JSON Array of fixes. Return [] if perfect.\n"
            '[{"action": "rename", "type": "module|unit|lesson|concept", "uid": "...", "new_title": "..."}, '
            '{"action": "delete", "type": "concept", "uid": "..."}]\n'
        )

        raw_fixes = llm_generate(
            prompt,
            sys_prompt="Senior Curriculum Editor. Return ONLY a JSON list. Be thorough — check every node.",
            max_tokens=1000,
        )
        fixes = extract_python_list(raw_fixes)

        if not fixes:
            logger.info("Syllabus Auditor found no issues.")
            if self.status_callback:
                self.status_callback("SYLLABUS:AUDIT:CLEAN")
            return

        if self.status_callback:
            self.status_callback(f"SYLLABUS:AUDIT:FIXING:{len(fixes)}_ISSUES")
        applied_count = self._apply_fixes(course_uid, course, fixes)
        logger.info(f"Applied {applied_count}/{len(fixes)} fixes to syllabus.")
        if self.status_callback:
            self.status_callback(f"LOG: Audit applied {applied_count} fixes.")

    def _get_compact_hierarchy(self, course: dict) -> str:
        """Build a compact text hierarchy for audit — more token-efficient than nested JSON."""
        lines = [f"Course: {course.get('title', '')}"]
        for module in course.get("modules", []):
            lines.append(f"  M[{module['uid']}]: {module['title']}")
            for unit in module.get("units", []):
                lines.append(f"    U[{unit['uid']}]: {unit['title']}")
                for lesson in unit.get("lessons", []):
                    lines.append(f"      L[{lesson['uid']}]: {lesson['title']}")
                    for concept in lesson.get("concepts", []):
                        lines.append(f"        C[{concept['uid']}]: {concept['title']}")
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
