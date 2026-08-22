# # from gevent import monkey; monkey.patch_all()
import requests
import time
import logging
import sys
import os
import uuid
import yaml
import subprocess
import platform
import shutil
import threading
# ThreadPoolExecutor removed — TTS thread pool no longer needed
import json
import re
from services.common.storage import StorageManager, DEFAULT_STUDENT_ID
from services.common.visual_aids import AidStore, extract_aids, descriptor as aid_descriptor
from services.common.concept_doc import (
    tutor_context as build_tutor_context,
    section as concept_section,
)

try:
    from gpu_gate import LLMContext, INTERACTIVE
except ImportError:
    from services.core.gpu_gate import LLMContext, INTERACTIVE
import psutil
import signal
from flask import Flask, request, jsonify

# Ensure services/common is in path for prompts
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from services.common.learner_behaviour import (
    classify as _classify_behaviour, describe as _describe_behaviour)
from services.common.teaching_move import from_turn_state
from services.common.turn_state import TurnState
from services.common.prompts import (
    DEFAULT_GRADE_BAND,
    _concept_is_arbitrary,
    is_young_band,
    get_socratic_tutor_prompt,
    get_bridge_prompt,
    get_socratic_grading_prompt,
    get_examiner_question_prompt,
    get_hint_prompt,
    get_examiner_grade_prompt,
    generate_security_token,
    get_micro_lecture_prompt,
    get_typed_socratic_prompt,
    SOCRATIC_QUESTION_TYPES,
    GRADE_JSON_SCHEMA,
    get_band_profile,
)
from fsrs_engine import FSRSEngine


def _repair_mojibake(text):
    """Detect and repair UTF-8-as-latin-1 mojibake (e.g. 'Thatâs' → 'That's').

    Caused by UTF-8 bytes being decoded as latin-1/cp1252 somewhere upstream.
    Only runs when telltale sequences appear to avoid corrupting legitimate
    extended-latin text.
    """
    if not text:
        return text
    telltales = ("â", "Ã©", "Ã¨", "Ã ", "Ã§", "Ã¼", "Ã¶", "Ã¤", "Â ", "Â·", "â")
    if not any(t in text for t in telltales):
        return text
    try:
        repaired = text.encode("latin-1", errors="strict").decode("utf-8", errors="strict")
        return repaired
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def clean_llm_response(text):
    """Remove LLM artifacts and extract clean conversational text."""
    if not text:
        return ""

    # Repair any UTF-8/latin-1 mojibake before further processing
    text = _repair_mojibake(text)

    # --- Phase 1: Strip known LLM/training artifacts ---

    # Remove all <|...|> and <|...> style tags
    text = re.sub(r"<\|[^>]*\|?>", "", text)
    # Remove </s>, </s>:, <s> tokens
    text = re.sub(r"</?\s*s\s*>:?", "", text)
    # Remove ||...| style artifacts
    text = re.sub(r"\|\|[^|]*\|", "", text)
    # Remove |<|... (malformed tags at end)
    text = re.sub(r"\|?<\|[^>]*$", "", text)
    # Remove |tag| pipe patterns
    text = re.sub(r"\|[a-z_]+\|", "", text)
    # Remove isolated pipes
    text = re.sub(r"\|>", "", text)
    text = re.sub(r"<\|", "", text)
    # Remove context/metadata leakage
    text = re.sub(r'context\s*=\s*"[^"]*"', "", text)
    text = re.sub(r'metadata\s*=\s*"[^"]*"', "", text)
    # Remove lines that are just pipes
    text = re.sub(r"^\s*\|.*$", "", text, flags=re.MULTILINE)

    # --- Phase 1b: Strip markdown formatting (speech should be plain text) ---

    # Remove bold/italic markers: **text** → text, *text* → text, __text__ → text
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    # Remove markdown headers: # Header → Header
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove markdown list bullets: - item → item, * item → item
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    # Remove numbered list prefixes: 1. item → item
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Remove role-label prefixes like "Lecturer:" or "Tutor:"
    text = re.sub(r"^\s*(Lecturer|Tutor|Teacher|Professor|Instructor)\s*:\s*", "", text, flags=re.IGNORECASE)

    # --- Phase 2: Remove training/role-play artifacts ---

    # Remove BEGIN/END SOLUTION blocks
    text = re.sub(r"BEGIN\s+SOLUTION\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"END\s+SOLUTION\s*", "", text, flags=re.IGNORECASE)
    # Remove role headers (system>, assistant>, user>, etc.)
    text = re.sub(
        r"\b(system|assistant|user|human|tutor|student)\s*>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Remove role-name echoes at start of response ("SOCRATIC TUTOR", "FRIENDLY TUTOR", etc.)
    text = re.sub(
        r"^\s*(SOCRATIC|FRIENDLY|RIGOROUS|CREATIVE|DRILL SERGEANT|ACADEMIC)?\s*TUTOR\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Remove instruction label prefixes like "OPENING QUESTION (1 sentence):"
    text = re.sub(
        r"(OPENING|FOLLOW-?UP|NEXT|GUIDING)?\s*QUESTION\s*\([^)]*\)\s*:?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Remove other label prefixes like "Response:", "Answer:"
    text = re.sub(
        r"^\s*(Response|Answer|Question|Output)\s*:\s*", "", text, flags=re.IGNORECASE
    )
    # Remove instruction-like meta-text
    text = re.sub(
        r"(?i)(praise the student|continue to guide|you\'re now ready|your answer is)[^.?!]*[.?!]?",
        "",
        text,
    )
    # Remove "Let's dive into", "Let me know", etc. filler phrases
    text = re.sub(
        r"(?i)let'?s\s+(dive into|explore|begin|start with)[^.?!]*[.?!]?", "", text
    )

    # --- Phase 3: Truncate at first sign of garbage ---

    # If we see repeated artifacts, cut everything from there
    for marker in [
        "BEGIN SOLUTION",
        "END SOLUTION",
        "</s>",
        "system>",
        "Praise ",
        "You're now ready",
    ]:
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx]

    # --- Phase 4: Clean up whitespace ---
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip()

    # --- Phase 5: Extract the first meaningful question ---
    # Find sentences ending with ? and take up to the first 2-3 sentences
    if text:
        sentences = re.split(r"(?<=[.?!])\s+", text)
        # Filter out empty sentences
        sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 5]

        if sentences:
            # Take sentences up to and including the first question
            result = []
            for s in sentences[:4]:  # Max 4 sentences
                result.append(s)
                if "?" in s:
                    break  # Stop after the first question
            text = " ".join(result)

    return text.strip()


from safety import (check_safety, check_safety_detailed, get_safety_redirect_message,
                    check_output_safety)
from service_manager import ServiceManager
from services.core.course_builder import SkeletonBuilder, ContentHydrator, SyllabusAuditor, CourseCreationError
from services.core.llm_client import get_llm_client
# Logger setup

# Logging Setup
log_dir = "/app/data/logs"
try:
    os.makedirs(log_dir, exist_ok=True)
except (PermissionError, OSError):
    log_dir = "data/logs"
    os.makedirs(log_dir, exist_ok=True)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"{log_dir}/core.log", mode="a", delay=False),
    ],
)

app = Flask(__name__)

# B27.1: opt-in structured JSON logs (HELGA_JSON_LOGS=true)
try:
    from services.common.logging_utils import configure_json_logging
    configure_json_logging("core-logic")
except Exception:
    pass



# A lesson has to end for EVERY learner, not only for progressing ones.
# Measured: without these, an adult session ran 25 turns on one concept and never
# completed. Ease first, then offer the exit -- and let FSRS bring the concept
# back rather than grinding on it now.
ADULT_EASE_AFTER = 2          # consecutive misses before changing approach
ADULT_OFFER_PARK_AFTER = 4    # consecutive misses before offering to move on
CONCEPT_TURN_CAP = 20         # hard bound so a lesson always terminates


class MnemosyneFSM:
    def __init__(self, student_id=None, storage=None):
        # B15.6: the isolation key. Every storage call passes it; every
        # outbound status/token payload is stamped with it (B15.5).
        # Defaults to the legacy student until real auth lands (B15.4).
        self.student_id = student_id or DEFAULT_STUDENT_ID
        self.last_touch = time.time()

        # Use DEV_MODE for laptop if environment variable set
        self.dev_mode = (
            os.getenv("DEV_MODE", "False").lower() == "true" or sys.platform == "win32"
        )

        try:
            config_path = (
                "/configs/helga_config.yaml"
                if not self.dev_mode
                else "configs/helga_config.yaml"
            )
            logging.info(f"Loading configuration from: {config_path}")
            with open(config_path, "r") as f:
                self.config = yaml.safe_load(f)
        except FileNotFoundError:
            logging.error(f"Configuration file not found at {config_path}.")
            self.config = {}
        except yaml.YAMLError as e:
            logging.error(f"Error parsing YAML configuration: {e}", exc_info=True)
            self.config = {}
        except Exception as e:
            logging.error(f"Failed to load configuration: {e}", exc_info=True)
            self.config = {}

        self.state = "LOBBY"
        self.fsrs = FSRSEngine()
        self.security_token = generate_security_token()
        self.data_root = os.getenv("DATA_ROOT", "/app/data")
        self.state_file = os.path.join(self.data_root, "user_state.json")

        # State Variables
        self.active_course_uid = None
        self.creation_in_progress = False  # Guard for concurrent generation
        self._creation_thread = None  # AUTO-5: Track creation thread for cleanup
        self.creation_status = {
            "active": False,
            "topic": None,
            "phase": None,  # skeleton, audit, hydration, complete, error
            "started_at": None,
            "course_uid": None,
            "progress_pct": 0,
            "last_update": None,
        }

        # State Variables
        self.user_level = 5
        self.current_context = ""
        self.syllabus_queue = []
        self.current_lesson_node = None
        self.completed_topics = set()  # Track completed topics by UID
        self.last_lesson_title = None
        self.conversation_history = []
        self.transcript = []
        self.last_question = ""
        self.turn_state = TurnState()          # A.2, reset per concept

        self.review_queue = []
        self.previous_state = None
        self.current_card = None
        self.card_attempts = 0

        self.current_locus_uid = None
        self.current_locus_desc = ""
        self.temp_anchor_concept = None

        self.last_interaction_time = time.time()
        self.question_start_time = 0
        self.battery_level = 100

        # Socratic Question Type Progression
        # Index into the BAND-FILTERED question types (see _question_types()),
        # not into the full six. This value is persisted in the session blob,
        # so the filtered list must keep the canonical ORDER -- otherwise a
        # learner resumes on a different question type than they left on.
        self.socratic_type_index = 0
        self.socratic_retry_count = 0  # Consecutive low-grade attempts on current type
        self._last_socratic_grade = 3  # Last grade for rule-based mode selection

        # Multi-question mastery tracking
        self.concept_correct_streak = 0  # Consecutive correct answers (grade >= 3), reset on grade < 3
        self.concept_miss_streak = 0  # B17.7: consecutive misses — drives affect handling for young bands
        self.concept_question_count = 0  # Total questions asked for current concept

        # Bloom's Taxonomy Level Tracking (1-6)
        # 1=Remember, 2=Understand, 3=Apply, 4=Analyze, 5=Evaluate, 6=Create
        self.current_bloom_level = 1
        self.bloom_correct_streak = 0  # Consecutive correct at current bloom level
        # Course-level Bloom boundaries (loaded from structure.json)
        self.course_bloom_floor = 1
        self.course_bloom_ceiling = 6
        # Per-concept target Bloom level (from structure.json)
        self.concept_bloom_target = None
        # GAP 4: Track which question type categories were passed (Grade >= 3)
        self.passed_question_types = set()
        # GAP 7: Prior concepts summary for inter-concept continuity
        self.prior_concepts_summary = []
        # Mastery criteria extracted from concept markdown for grading
        self.current_mastery_criteria = ""

        # Adaptive Learning State
        self.current_misconceptions = []
        self.current_analogies = []
        self.current_teaching_style = ""  # Dynamic Persona from course metadata
        self.user_profile = (
            self._load_user_profile()
        )  # User profile for personalized teaching

        # Draft Board State (for interactive course creation)
        self.draft_course_structure = None  # Holds draft modules during creation
        self.draft_course_topic = ""
        self.draft_course_depth = 3
        self.draft_teaching_style = ""

        # Pre-Assessment State
        self.pre_assessment_questions = []
        self.pre_assessment_answers = {}
        self.pre_assessment_module_depths = {}

        # Maintenance Mode State
        self.maintenance_mode = False
        self.maintenance_paused = False

        # LLM client (Ollama)
        self.llm_client = get_llm_client()
        # Say so at startup if the model will idle out between turns. A cold
        # 9B load is several seconds paid by whoever is waiting, and the fix
        # lives on the host — outside this repo — so silence here means nobody
        # ever finds out why the first answer after a pause is slow.
        try:
            self.llm_client.warn_if_not_pinned()
        except Exception as e:
            logging.debug(f"residency probe skipped: {e}")

        # Service URLs
        self.rag_url = os.environ.get("RAG_URL", "http://rag-engine:5002" if not self.dev_mode else "http://localhost:5002")
        self.web_ui_url = os.environ.get("WEB_UI_URL", "http://web-ui:5000" if not self.dev_mode else "http://localhost:5000")

        logging.info(f"Dev mode: {self.dev_mode}")
        logging.info(f"Service URLs: RAG={self.rag_url}, WebUI={self.web_ui_url}")

        # Initialize StorageManager. When constructed via the FSMRegistry a
        # single shared instance is injected (B15.6); standalone construction
        # (tests, scripts) builds its own.
        self.storage = storage or StorageManager(self.data_root)
        self.conn = None  # Legacy compat

        # B17.1: grade band resolved from the students row, drives prompts/Bloom
        # Must match prompts.DEFAULT_GRADE_BAND. It said "9-12" here, so an
        # adult with no student row got the 110-word rigorous-mentor
        # register instead of the 6-8 default the spec states.
        self.grade_band = DEFAULT_GRADE_BAND
        try:
            student_row = self.storage.accounts.get_student(self.student_id)
            if student_row and student_row.get("grade_band"):
                self.grade_band = student_row["grade_band"]
        except Exception as e:
            logging.warning(f"Could not resolve grade_band for {self.student_id}: {e}")

        # B14: MCP-style tutor tool layer. OFF by default — flip
        # HELGA_ENABLE_TUTOR_TOOLS=true once tool-call reliability is validated
        # on the M4/Ollama. When off, grading/hint paths are entirely unchanged.
        self._tutor_tools_enabled = os.getenv("HELGA_ENABLE_TUTOR_TOOLS", "false").lower() == "true"
        self._tutor_tools_registry = None

        # B13: visual teaching aids. Independent of the tool flag on purpose —
        # the inline ```aid fence needs no tool-calling support at all, so aids
        # can ship while B14 tool reliability is still being validated. The
        # store holds specs OUT of the transcript so the 2-second /state poll
        # stays small; the transcript carries only ~200-byte descriptors.
        self._visual_aids_enabled = os.getenv("HELGA_ENABLE_VISUAL_AIDS", "true").lower() == "true"
        self.aid_store = AidStore(capacity=64)
        # Aids produced by a TOOL call land here and attach to the next tutor
        # message, since the tool runs mid-generation, before the text exists.
        self._pending_aids = []
        # Policy bookkeeping (B13.11). These are what stop a diagram appearing
        # every turn and stop the same diagram appearing twice; they reset on
        # every concept change, because a budget is per concept.
        self._turns_since_aid = 99
        self._aid_kinds_this_concept = []
        self._aid_ids_this_concept = set()
        self._concept_aids = {}          # slot -> aid, precomputed at build time
        self._last_aid_decision = None
        # Session scope — spans concepts, so the per-concept budget cannot see
        # it. This is what stops an eight-concept session becoming a slideshow.
        self._session_aids_shown = 0
        self._session_recent_kinds = []
        self._asset_manifest = None
        self._asset_manifest_course = None

        # B15.6: the per-FSM timer thread and per-instance signal handlers are
        # gone — one registry-level sweeper calls tick(now) for every resident
        # FSM, and maintenance signals are process-level (module tail).

        # Restore state from this student's fsm_sessions row (B15.7); imports
        # the legacy user_state.json once for the legacy student.
        self._hydrate_from_row()

        self.add_message("Helga online. Ready to learn.")

    def _load_user_profile(self):
        """Load user profile from shared JSON file for prompt personalization."""
        profile_path = os.path.join(self.data_root, "user_space/profile.json")
        try:
            if os.path.exists(profile_path):
                with open(profile_path, "r") as f:
                    return json.load(f)
        except Exception as e:
            logging.warning(f"Could not load user profile: {e}")
        return None

    def _handle_maintenance_pause(self, signum, frame):
        """Signal handler for SIGUSR1 - pause database operations."""
        logging.info("SIGUSR1 received: Entering maintenance mode (pause)")
        self.maintenance_mode = True
        self.maintenance_paused = True

    def _handle_maintenance_resume(self, signum, frame):
        """Signal handler for SIGUSR2 - resume database operations."""
        logging.info("SIGUSR2 received: Exiting maintenance mode (resume)")
        self.maintenance_mode = False
        self.maintenance_paused = False

    def broadcast_state(self):
        """Pushes the current state to the Web UI for real-time updates."""
        try:
            # Fire and forget state update to avoid blocking core logic
            # The web-ui polls the /state endpoint, so no direct push needed from here.
            pass
        except Exception as e:
            logging.warning(f"Failed to broadcast state: {e}")

    def _call_llm(self, messages, max_tokens=1000, timeout=60, json_schema=None, images=None):
        """Call the LLM via Ollama client.

        Args:
            messages: List of message dicts [{"role": "system", "content": "..."}]
            max_tokens: Maximum tokens to generate
            timeout: Request timeout in seconds
            json_schema: Optional JSON Schema dict to grammar-constrain the output
                (Ollama >= 0.5). Use for structured responses like grading.
            images: Optional list of images (data URIs / base64) for the multimodal
                model (qwen3.5:9b) — lets the Socratic loop see a diagram or a photo
                of the student's work and discuss it.

        Returns:
            str: The LLM response text, or None on failure
        """
        # Extract system and user messages from the messages list
        system_prompt = ""
        user_message = ""
        for msg in messages:
            if msg["role"] == "system":
                system_prompt += msg["content"] + "\n"
            elif msg["role"] == "user":
                user_message += msg["content"] + "\n"
            elif msg["role"] == "assistant":
                # Include assistant messages in user context for history
                user_message += f"[Previous tutor response: {msg['content'][:200]}]\n"

        result = self.llm_client.chat(
            system_prompt.strip(),
            user_message.strip() or "Continue the conversation.",
            max_tokens=max_tokens,
            timeout=timeout,
            json_schema=json_schema,
            images=images,
            ctx=self._llm_ctx(),
        )
        return result if result else None

    def _llm_ctx(self):
        """B23.2: tag live tutoring turns INTERACTIVE for the GPU gate; a
        queued turn past the busy threshold pushes a friendly status to this
        student instead of a frozen spinner."""
        return LLMContext(
            INTERACTIVE,
            self.student_id,
            on_busy=lambda info: self.send_status_update(
                info.get("msg", "One moment…"),
                event={"type": "GPU_BUSY", "queue_depth": info.get("queue_depth")},
            ),
        )

    def _call_llm_stream(self, messages, max_tokens=1000, timeout=120):
        """Call the LLM via Ollama streaming API, forwarding tokens to web-ui.

        Yields text chunks as they arrive from the LLM and simultaneously
        sends each chunk to the browser via send_status_update with
        type=stream_token.

        Args:
            messages: List of message dicts [{"role": "system", "content": "..."}]
            max_tokens: Maximum tokens to generate
            timeout: Request timeout in seconds

        Returns:
            str: The full accumulated LLM response text, or None on failure
        """
        system_prompt = ""
        user_message = ""
        for msg in messages:
            if msg["role"] == "system":
                system_prompt += msg["content"] + "\n"
            elif msg["role"] == "user":
                user_message += msg["content"] + "\n"
            elif msg["role"] == "assistant":
                user_message += f"[Previous tutor response: {msg['content'][:200]}]\n"

        accumulated = []
        try:
            for chunk in self.llm_client.chat_stream(
                system_prompt.strip(),
                user_message.strip() or "Continue the conversation.",
                max_tokens=max_tokens,
                timeout=timeout,
                ctx=self._llm_ctx(),
            ):
                if chunk:
                    accumulated.append(chunk)
                    # Forward each token to the browser via status update channel
                    self._send_stream_token(chunk)

            full_text = "".join(accumulated)
            if full_text:
                # Signal end of stream
                self._send_stream_token("", done=True)
                return full_text
            return None
        except Exception as e:
            logging.error(f"Streaming LLM call failed: {e}")
            # Signal stream error so browser can clean up
            self._send_stream_token("", done=True)
            return None

    # ------------------------------------------------------------------ #
    # B14 — MCP-style tutor tools (tier-gated, failsafed). All paths here  #
    # are no-ops when self._tutor_tools_enabled is False (the default), so #
    # the live grading/hint behavior is unchanged until validated on M4.   #
    # ------------------------------------------------------------------ #
    def _tool_search_concepts(self, query, course_uid=None):
        """Data-pull tool: search the course knowledge base (FTS5)."""
        try:
            rows = self.storage.search.search(
                query, course_uid=course_uid or self.active_course_uid, limit=5
            )
            return [{"title": r.get("title", ""),
                     "concept_uid": r.get("concept_uid", ""),
                     "snippet": (r.get("content", "") or "")[:240]} for r in rows]
        except Exception as e:
            logging.warning(f"tool search_concepts failed: {e}")
            return []

    def _tool_concept_content(self, concept_uid, course_uid=None):
        """Data-pull tool: fetch a concept's markdown content."""
        try:
            cu = course_uid or self.active_course_uid
            if cu:
                return self.storage.courses.get_concept_content(cu, concept_uid) or ""
            found = self.storage.courses.find_concept_across_courses(concept_uid)
            if found:
                return self.storage.courses.get_concept_content(
                    found.get("course_uid", ""), concept_uid) or ""
            return ""
        except Exception as e:
            logging.warning(f"tool get_concept_content failed: {e}")
            return ""

    def _tool_get_mastery(self, concept_uid):
        """Data-pull tool: student's mastery/progress for a concept."""
        try:
            return self.storage.progress.get_progress(concept_uid) or {}
        except Exception as e:
            logging.warning(f"tool get_mastery failed: {e}")
            return {}

    def _get_tutor_tools(self):
        """Lazily build the tier-gated tool registry bound to this FSM's data.
        Returns None when disabled so callers stay on the unchanged default path."""
        if not getattr(self, "_tutor_tools_enabled", False):
            return None
        if getattr(self, "_tutor_tools_registry", None) is not None:
            return self._tutor_tools_registry
        try:
            from services.common.tutor_tools import build_default_registry
            self._tutor_tools_registry = build_default_registry(
                search_fn=self._tool_search_concepts,
                content_fn=self._tool_concept_content,
                mastery_fn=self._tool_get_mastery,
                wiki_fn=None,          # online lookups stay in the hydration pipeline
                enable_code=False,     # code-exec never enabled in the live tutor
                # Registers show_visual / visualize_function / visualize_data only
                # when aids are on; otherwise the model never sees those tools.
                # getattr, matching the other flags above: bare FSM instances
                # built via __new__ (tests, recovery paths) have no __init__.
                aid_sink=(self._aid_sink
                          if getattr(self, "_visual_aids_enabled", False) else None),
            )
        except Exception as e:
            logging.warning(f"Tutor tool registry unavailable: {e}")
            self._tutor_tools_registry = None
        return self._tutor_tools_registry

    def _verify_answer_objectively(self, question, answer):
        """Optional objective-verification pass (B14.8). For math/factual answers,
        let the model use compute/data tools to ground the upcoming grade. Returns
        a short '[TOOL CHECK: ...]' note to inject into the grading prompt, or ''
        when tools are disabled, no tool was used, or anything goes wrong. NEVER
        raises and never blocks grading — grading proceeds regardless."""
        registry = self._get_tutor_tools()
        if registry is None:
            return ""
        try:
            sys_prompt = (
                "You verify a student's answer using tools when (and only when) a "
                "tool can objectively check it (math, units, statistics, a fact in "
                "the course/knowledge base). Call at most one or two tools. Then "
                "reply in ONE sentence stating what the tool confirmed or "
                "contradicted. If no tool applies, reply exactly: NO_TOOL."
            )
            user = f"Question: {question}\nStudent answer: {answer}"
            out = self.llm_client.chat_with_tools(
                sys_prompt, user, registry,
                max_rounds=2, max_tool_calls=2, timeout=30,
                ctx=self._llm_ctx(),
            )
            if not out.get("tool_calls"):
                return ""
            text = (out.get("text") or "").strip()
            if not text or text.upper().startswith("NO_TOOL"):
                return ""
            return f"[TOOL CHECK (objective): {text[:300]}]"
        except Exception as e:
            logging.warning(f"objective verification skipped: {e}")
            return ""

    def _send_stream_token(self, token, done=False):
        """Send a single stream token to the web-ui for real-time display."""
        data = {
            "type": "stream_token",
            "token": token,
            "done": done,
            "student_id": self.student_id,  # B15.5: room-scoped delivery
        }
        try:
            requests.post(
                f"{self.web_ui_url}/api/update_thinking_status",
                json=data,
                timeout=5,
            )
        except Exception:
            # Non-critical: if a single token fails to deliver, streaming continues
            pass

    def send_status_update(self, message, log=None, progress=None, event=None):
        """Sends real-time status update to the Web UI for progress feedback.

        `event` (B6.4) is an optional structured envelope, e.g.
        {"type": "PIPELINE_STAGE", "stage": "HYDRATE", "pct": 40}. The web-ui
        forwards the whole payload, so the browser can drive progress UI from
        structured fields instead of parsing the free-text `message`. The message
        is kept for human display and legacy string-matching handlers.
        """
        # B15.5: stamp ownership at the source — web-ui emits to this
        # student's Socket.IO room and never broadcasts.
        data = {"message": message, "student_id": self.student_id}
        if log:
            data["log"] = log
        if progress is not None:
            data["progress"] = progress
        if event is not None:
            data["event"] = event
        for attempt in range(2):  # AUTO-15: 1 retry
            try:
                requests.post(
                    f"{self.web_ui_url}/api/update_thinking_status",
                    json=data,
                    timeout=15,
                )
                return
            except Exception as e:
                if attempt == 0:
                    logging.warning(f"Status update failed (retrying): {e}")
                else:
                    logging.warning(f"Status update failed after retry: {e}")

    # Canonical pipeline stages for the course-creation progress UI (B6.4).
    PIPELINE_STAGES = ("PREFLIGHT", "SKELETON", "AUDIT", "HYDRATE", "FINALIZE", "DONE", "ERROR")

    def send_pipeline_stage(self, stage, pct=None, message=None, **fields):
        """Emit a structured course-creation progress event (B6.4) plus a human
        message. The browser drives the progress bar from {stage, pct} instead of
        substring-matching free text. `fields` carries extras (e.g. title, uid,
        completed, total). Falls back to `message` for legacy handlers."""
        event = {"type": "PIPELINE_STAGE", "stage": stage}
        if pct is not None:
            event["pct"] = max(0, min(100, int(pct)))
        event.update(fields)
        self.send_status_update(message or stage.replace("_", " ").title(),
                                progress=event.get("pct"), event=event)

    def speak(self, text, record=True):
        """Add a tutor message to the transcript (text-only, no TTS)."""
        self.add_message(text, record=record)

    def add_message(self, text, record=True, grade=None):
        """Add a tutor message to the transcript. B21.5: every tutor-visible
        message passes output moderation first — a flagged model response is
        suppressed and replaced with a safe fallback, never delivered raw.

        B13: also the one choke point where visual aids are attached. Every
        tutor message in the system arrives here, so aid handling lives here
        rather than being duplicated across the socratic/lecture/review paths.
        """
        aids = []
        if record and text and getattr(self, "_visual_aids_enabled", False):
            # Lift ```aid fences out BEFORE moderation, for two reasons: the
            # moderator should judge the prose a learner actually reads, not a
            # JSON blob; and the JSON must never reach the chat even when the
            # aid is rejected. extract_aids never raises.
            try:
                text, aids, aid_errors = extract_aids(text)
                for err in aid_errors:
                    logging.info(f"Visual aid rejected: {err}")
            except Exception as e:
                logging.error(f"Aid extraction failed (message delivered without it): {e}")
                aids = []
            aids = self._drain_pending_aids() + aids
            # Never the same diagram twice in one concept (ids are content
            # hashes, so an identical redraw collapses to the same id).
            aids = self._drop_repeat_aids(aids)

        if record and text:
            node_title = (self.current_lesson_node.get("title")
                          if self.current_lesson_node else None)
            try:
                out = check_output_safety(text, node_title, self.grade_band)
                if not out.is_safe:
                    logging.warning(
                        f"OUTPUT suppressed [{out.category}] on '{node_title}' "
                        f"(confidence={out.confidence:.2f}, len={len(text)})")
                    self._log_safety_incident("output_" + out.category, node_title)
                    text = out.message
                    # A suppressed message keeps no diagram. The aid was drawn to
                    # support prose that is no longer being delivered, and an
                    # orphaned figure under a safety fallback is worse than none.
                    aids = []
            except Exception as e:
                logging.error(f"Output safety check failed open: {e}")
        if record:
            entry = {"sender": "helga", "text": text}
            if grade is not None:
                entry["grade"] = grade
            # Every recorded tutor message ages the cooldown by one turn.
            self._turns_since_aid = getattr(self, "_turns_since_aid", 99) + 1
            if aids:
                self._note_aids_shown(aids)
                for aid in aids:
                    self.aid_store.put(aid)
                # Only the slim descriptor rides in the transcript — the spec is
                # fetched once from /api/aid/<id> and cached in the browser.
                entry["aids"] = [aid_descriptor(a) for a in aids]
                logging.info(f"Attached {len(aids)} visual aid(s): "
                             f"{[a['kind'] for a in aids]}")
            self.transcript.append(entry)
            if len(self.transcript) > 50:
                self.transcript = self.transcript[-50:]
            logging.info(f"TRANSCRIPT (AI): {text[:80]}... (Size: {len(self.transcript)})")

    # ------------------------------------------------------------------ #
    # B13.11 — WHEN to show a diagram. The policy is deterministic and lives #
    # in services/common/aid_policy.py; these methods only gather the moment #
    # and act on the verdict.                                                #
    # ------------------------------------------------------------------ #
    def load_course_assets(self, course_uid):
        """Session start: learn in ONE read what this course had built for it.

        The manifest is written by Phase 3 (asset collection). Reading it here
        rather than parsing every concept's markdown means a session knows its
        asset coverage immediately, and the per-concept parse still happens
        lazily in _reset_aid_budget when a concept is actually entered.

        A course built before Phase 3 existed simply has no manifest — coverage
        is reported as unknown and every diagram takes the generate path, which
        is the pre-existing behaviour.
        """
        # getattr throughout: bare FSM instances built via __new__ (tests,
        # recovery paths) never ran __init__ — the same convention the rest of
        # the aid bookkeeping follows.
        if not course_uid or course_uid == getattr(self, "_asset_manifest_course", None):
            return getattr(self, "_asset_manifest", None)
        self._asset_manifest = None
        self._asset_manifest_course = course_uid
        self._session_aids_shown = 0
        self._session_recent_kinds = []
        if not getattr(self, "_visual_aids_enabled", False):
            return None
        try:
            from services.core.asset_collector import load_manifest
            manifest = load_manifest(self.storage, course_uid)
        except Exception as e:
            logging.warning(f"Asset manifest unavailable for {course_uid}: {e}")
            return None
        if not manifest:
            logging.info(f"No asset manifest for {course_uid} — diagrams will be "
                         "generated live (course predates the asset phase)")
            return None
        self._asset_manifest = manifest
        stats = manifest.get("stats", {})
        covered = len(manifest.get("concepts", {}))
        logging.info(
            f"Assets for {course_uid}: {covered} concept(s) covered, "
            f"{stats.get('generated', 0)} diagram(s), {stats.get('images', 0)} image(s)")
        self.send_status_update(
            f"ASSETS:READY:{covered}:{stats.get('generated', 0)}:{stats.get('images', 0)}")
        return manifest

    def _reset_aid_budget(self):
        """A new concept gets a fresh diagram budget and a clean slate of
        already-shown kinds. Also loads whatever the course build drew for this
        concept, so the policy can prefer a checked diagram over a fresh one."""
        self._turns_since_aid = 99
        self._aid_kinds_this_concept = []
        self._aid_ids_this_concept = set()
        self._concept_aids = {}
        if not getattr(self, "_visual_aids_enabled", False):
            return
        try:
            from services.common.visual_aids import parse_concept_aids
            # Parse from the FULL concept text, not `current_context`.
            #
            # `current_context` is `text[:10000]`, a slice taken to bound the
            # prompt. The `## Visual Aids` section is written at the END of a
            # concept document, so on a long concept the slice cuts the aids
            # off entirely: the policy then sees no available slots, can never
            # return `reuse`, and every diagram built and checked at
            # course-creation time is invisible to that learner.
            #
            # Measured on real course data 2026-08-21: 1 of the 24 concepts
            # that HAVE aids lost them this way. The proportion grows with
            # concept length, so the deeper tiers this product sells are the
            # ones that lose most.
            full_text = ""
            if isinstance(self.current_lesson_node, dict):
                full_text = self.current_lesson_node.get("text") or ""
            self._concept_aids = parse_concept_aids(
                full_text or self.current_context or "")
            # THE DOMAIN'S OWN CODE AID.
            #
            # `parse_concept_aids` reads the concept's MARKDOWN. A domain code
            # example is attached to the concept in structure.json instead, so
            # it is invisible here — built, vetted, deduplicated course-wide at
            # build time, and never shown to anybody.
            #
            # It belongs in the `worked_example` slot: a real snippet from the
            # source IS a worked example, and that slot is also what the policy
            # reaches for when the learner is stuck — the moment prose has
            # failed and showing code is the whole point.
            #
            # Only fills an EMPTY slot: an aid authored for this concept was
            # written deliberately and outranks a mined one.
            self._load_domain_code_aid()

            if self._concept_aids:
                logging.info(f"Loaded {len(self._concept_aids)} pre-built visual aid(s): "
                             f"{sorted(self._concept_aids)}")
        except Exception as e:
            logging.warning(f"Could not load pre-built aids for this concept: {e}")

    def _load_domain_code_aid(self):
        """Put the concept's build-time code example in the worked-example slot.

        Never raises: a missing aid must cost a diagram, never the lesson.
        """
        try:
            node = self.current_lesson_node
            if not isinstance(node, dict):
                return
            example = node.get("code_example")
            if not example or self._concept_aids.get("worked_example"):
                return
            from services.common.visual_aids import normalize_aid
            aid, err = normalize_aid(dict(example), default_tier="authored")
            if not aid:
                logging.info(f"Domain code aid rejected: {err}")
                return
            aid["slot"] = "worked_example"
            aid.setdefault("id", f"code:{node.get('uid') or 'concept'}")
            self._concept_aids["worked_example"] = aid
            logging.info("[DOMAIN] code example loaded into worked_example slot")
        except Exception as e:
            logging.warning(f"Domain code aid unavailable: {e}")

    def _aid_moment(self, teaching_mode):
        """Snapshot the state the policy reasons about."""
        from services.common.aid_policy import AidMoment
        node = self.current_lesson_node or {}
        # The misconception the student is currently displaying, if the FSM has
        # identified one — a diagram drawn for THAT error beats a generic one.
        active = getattr(self, "_active_misconception_index", None)
        return AidMoment(
            teaching_mode=teaching_mode,
            is_concept_opening=(self.concept_question_count == 0),
            last_grade=self._last_socratic_grade or 0,
            retry_count=self.socratic_retry_count,
            miss_streak=getattr(self, "concept_miss_streak", 0),
            correct_streak=self.concept_correct_streak,
            bloom_level=self.current_bloom_level,
            question_count=self.concept_question_count,
            grade_band=self.grade_band,
            concept_title=node.get("title", ""),
            concept_text=(self.current_context or "")[:4000],
            turns_since_aid=self._turns_since_aid,
            aids_shown_this_concept=len(self._aid_kinds_this_concept),
            kinds_shown=tuple(self._aid_kinds_this_concept),
            available_slots=tuple(k for k in self._concept_aids
                                  if k not in self._aid_ids_this_concept),
            active_misconception=active,
            session_aids_shown=getattr(self, "_session_aids_shown", 0),
            recent_kinds=tuple(getattr(self, "_session_recent_kinds", [])),
            enabled=getattr(self, "_visual_aids_enabled", False),
        )

    def _decide_visual_aid(self, teaching_mode):
        """Decide, and act on a `reuse` verdict immediately.

        A reuse costs NO model involvement at all: the diagram was drawn and
        checked at course-creation time, so it is queued straight onto the next
        message. Only `generate` reaches the prompt.
        """
        if not getattr(self, "_visual_aids_enabled", False):
            return None
        try:
            from services.common.aid_policy import decide
            decision = decide(self._aid_moment(teaching_mode))
        except Exception as e:
            logging.warning(f"Aid policy unavailable this turn: {e}")
            return None
        self._last_aid_decision = decision
        logging.info(f"AID POLICY: {decision.action} — {decision.reason}")

        if decision.action == "reuse":
            aid = self._concept_aids.get(decision.slot)
            if aid:
                self._aid_sink(dict(aid))
                self._aid_ids_this_concept.add(decision.slot)
            else:
                logging.warning(f"Aid policy chose slot '{decision.slot}' but it "
                                "is not loaded; falling back to no diagram")
        return decision

    def _note_aids_shown(self, aids):
        """Record what went on screen so cooldown and budget mean something.
        getattr throughout: bare FSM instances built via __new__ (tests,
        recovery paths) never ran __init__, matching the convention above."""
        self._turns_since_aid = 0
        if not hasattr(self, "_aid_kinds_this_concept"):
            self._aid_kinds_this_concept = []
        if not hasattr(self, "_aid_ids_this_concept"):
            self._aid_ids_this_concept = set()
        if not hasattr(self, "_session_recent_kinds"):
            self._session_recent_kinds = []
        for aid in aids:
            kind = aid.get("kind", "?")
            self._aid_kinds_this_concept.append(kind)
            self._aid_ids_this_concept.add(aid["id"])
            self._session_aids_shown = getattr(self, "_session_aids_shown", 0) + 1
            # Most recent first; only the last few matter for variety.
            self._session_recent_kinds.insert(0, kind)
            del self._session_recent_kinds[6:]

    def _drop_repeat_aids(self, aids):
        """Never show the same diagram twice in one concept.

        Aid ids are content hashes, so an identical figure re-emitted by the
        model collapses to the same id and is dropped here. The original card is
        still on screen — redrawing it below a new message would just push the
        conversation down and teach nothing.
        """
        shown = getattr(self, "_aid_ids_this_concept", None)
        if shown is None:
            return list(aids)
        kept = []
        for aid in aids:
            if aid["id"] in shown:
                logging.info(f"Dropping repeat visual aid {aid['id']} "
                             f"({aid.get('kind')}) — already shown this concept")
                continue
            kept.append(aid)
        return kept

    def _drain_pending_aids(self):
        """Take aids produced by tool calls during this turn. Tools run mid-
        generation, before the message text exists, so they queue here and are
        claimed by the next message recorded."""
        pending = getattr(self, "_pending_aids", [])
        self._pending_aids = []
        return pending

    def _aid_sink(self, aid):
        """Callback handed to the aid tools. Bounded so a model stuck in a loop
        of show_visual calls cannot grow the queue without limit."""
        if not hasattr(self, "_pending_aids"):
            self._pending_aids = []
        if len(self._pending_aids) < 4:
            self._pending_aids.append(aid)
        else:
            logging.warning("Discarding visual aid — too many queued for one turn")

    def reveal_aid(self, aid_id, stage=None):
        """Advance a progressive aid to its next layer (B13).

        The staged reveal is what keeps a diagram Socratic: the setup is shown,
        the learner commits to an answer, and only then does the next layer
        appear. Updates both the store (source of truth for the spec) and the
        transcript descriptor (what the 2-second poll carries), or the browser
        would re-render the old stage on its next poll and undo the reveal.
        """
        store = getattr(self, "aid_store", None)
        item = store.get(aid_id) if (store and aid_id) else None
        if item is None:
            logging.info(f"REVEAL_AID: unknown aid '{aid_id}' (likely evicted)")
            return False
        target = item.get("stage", 0) + 1 if stage is None else int(stage)
        updated = store.set_stage(aid_id, target)
        if updated is None:
            return False
        for entry in self.transcript:
            for desc in entry.get("aids", []) or []:
                if desc.get("id") == aid_id:
                    desc["stage"] = updated["stage"]
        logging.info(f"REVEAL_AID: {aid_id} -> stage {updated['stage']}"
                     f"/{updated.get('stages_total', 0)}")
        return True

    def play_sound(self, sound_id):
        """No-op — sound effects removed in text-only mode."""
        pass

    def stop_audio(self):
        """No-op — audio removed in text-only mode."""
        pass

    def tick(self, now=None):
        """One timer pass (B15.6). Called by the registry sweeper for every
        resident FSM — replaces the per-FSM `while True: sleep(1)` thread."""
        # Skip timers if in maintenance mode
        if self.state == "PAUSED" or self.maintenance_paused:
            return
        now = now or time.time()
        elapsed = now - self.last_interaction_time

        if (
            self.state == "SOCRATIC_LEARNING"
            and elapsed > 20
            and self.question_start_time > 0
        ):
            # Silent nudge - just reset timer, don't spam the chat
            self.last_interaction_time = now

        if (
            self.state == "SPACED_REPETITION"
            and elapsed > 15
            and self.question_start_time > 0
        ):
            self.play_sound("FRICTION_GRIND")
            name = (
                self.current_card.get("title", "unknown")
                if self.current_card
                else "the concept"
            )
            self.speak(f"The answer involves {name}. Should I reveal it?")
            self.last_interaction_time = now

    def transition(self, event):
        logging.info(f"Transition: {self.state} with {event}")
        event_type = event.get("type")  # Use 'type' for consistency with web-ui events

        # VG-10: Alias short event names to canonical FSM event types
        if event_type == "PAUSE":
            event_type = "PAUSE_SESSION"
        elif event_type == "RESUME":
            event_type = "RESUME_COURSE"

        text = event.get("payload", {}).get("text", "").lower()
        topic_uid = event.get("payload", {}).get("topic_id")  # For NAVIGATE_TO_TOPIC

        # All events should update last interaction time for inactivity timeout
        self.last_interaction_time = time.time()

        # Handle global commands that might occur in any state
        if self.handle_global_commands(event_type, text):
            return

        # Handle transcript interaction
        if event_type == "EDIT_MESSAGE":
            index = event.get("payload", {}).get("index")
            new_text = event.get("payload", {}).get("text", "")
            if index is not None and 0 <= index < len(self.transcript):
                msg = self.transcript[index]
                if msg["sender"] == "user":
                    msg["text"] = new_text
                    # Reprocess the edited input
                    if self.state == "SOCRATIC_LEARNING":
                        self.handle_socratic_answer(new_text)
            return
        elif event_type == "REPLAY_TTS":
            replay_text = event.get("payload", {}).get("text", "")
            if replay_text:
                self.speak(replay_text, record=False)
            return
        # LRN-2: Handle SET_CONTEXT to set active course from learn tab.
        # ANTI-LEAK: when the user switches to a DIFFERENT course from the
        # one currently active, wipe per-session state (transcript, history,
        # syllabus queue, current concept). Without this, the old course's
        # chat messages bleed into the new course's view.
        elif event_type == "SET_CONTEXT":
            uid = event.get("payload", {}).get("course_uid")
            if uid:
                # WARM THE MODEL WHEN A LESSON OPENS, NOT ON THE FIRST QUESTION.
                #
                # `llm_client.warm_up()` exists precisely for this and its
                # docstring records the cascade it prevents — a cold load takes
                # ~142s warm and ~9 MINUTES after the Mac sleeps, while every
                # tutoring call uses a 60s timeout. Two timeouts open the
                # breaker and fast-fail everything for 15s.
                #
                # It was called in exactly ONE place: tools/helgabench.py. The
                # fix was written for the benchmark after it lost a night of
                # measurement, and the PRODUCT never got it — so a learner
                # returning after a break had their first question fail while
                # the benchmark was protected. Measured tonight: three runs of
                # five turns each produced nothing but empty responses.
                #
                # SET_CONTEXT fires when the learn tab opens a course, which is
                # seconds before the first question and the right moment to pay
                # a load nobody is waiting on yet. Backgrounded so opening a
                # course never blocks on it.
                self._warm_model_async()
                prev_uid = self.active_course_uid
                self.active_course_uid = uid
                if prev_uid and prev_uid != uid:
                    logging.info(
                        f"SET_CONTEXT: switching course {prev_uid} → {uid}, "
                        f"clearing transcript/history/queue"
                    )
                    self.transcript = []
                    self.conversation_history = []
                    self.syllabus_queue = []
                    self.current_lesson_node = None
                    self.last_lesson_title = None
                    self.current_context = ""
                    self.concept_correct_streak = 0
                    self.concept_question_count = 0
                    self.current_bloom_level = 1
                    self.bloom_correct_streak = 0
                    self.socratic_type_index = 0
                try:
                    course = self.storage.courses.get_course(uid)
                    self.current_teaching_style = (
                        course.get("teaching_style", "") if course else ""
                    )
                except Exception as e:
                    logging.warning(f"SET_CONTEXT meta load failed: {e}")
                # B13.12: a session begins here — find out what this course had
                # built for it before the first question is asked.
                self.load_course_assets(uid)
            return
        elif event_type == "REVEAL_AID":
            # B13: uncover the next layer of a progressive diagram. Global, like
            # NAVIGATE_TO_TOPIC — a learner may reveal a figure from any state,
            # and it must never disturb the FSM's teaching state.
            payload = event.get("payload", {}) or {}
            self.reveal_aid(payload.get("aid_id"), payload.get("stage"))
            return
        elif event_type == "NAVIGATE_TO_TOPIC" and topic_uid:
            # ANTI-LEAK: allow the frontend to pin the course_uid in the same
            # event so we don't race SET_CONTEXT. If a course_uid is supplied
            # and differs from the current active course, switch context
            # first and wipe stale state before loading the new concept.
            payload = event.get("payload", {})
            payload_course_uid = payload.get("course_uid")
            if payload_course_uid and payload_course_uid != self.active_course_uid:
                logging.info(
                    f"NAVIGATE_TO_TOPIC: switching course "
                    f"{self.active_course_uid} → {payload_course_uid}"
                )
                self.active_course_uid = payload_course_uid
                self.load_course_assets(payload_course_uid)
                self.transcript = []
                self.conversation_history = []
                self.syllabus_queue = []
                self.last_lesson_title = None
                self.current_context = ""
                try:
                    course = self.storage.courses.get_course(payload_course_uid)
                    self.current_teaching_style = (
                        course.get("teaching_style", "") if course else ""
                    )
                except Exception as e:
                    logging.warning(f"NAVIGATE_TO_TOPIC course load failed: {e}")
            if not self.active_course_uid:
                logging.warning(
                    f"NAVIGATE_TO_TOPIC with no active_course_uid for {topic_uid}"
                )
                self.speak("Please select a course first.")
                return
            self.navigate_to_topic(topic_uid)
            return
        # LRN-4: Make RESUME_COURSE a global handler (not LOBBY-only)
        elif event_type == "RESUME_COURSE":
            payload = event.get("payload", {})
            uid = payload.get("course_uid") or payload.get("uid")
            title = payload.get("title")
            if self.state == "SOCRATIC_LEARNING":
                self._save_current_course_progress()
            self.resume_course(uid, title)
            return
        # Deleting a course has to work from wherever you are standing.
        # This lived inside `if self.state == "LOBBY"`, so deleting the course
        # you were mid-session in removed the rows and the markdown and left
        # the FSM still holding it -- same LOBBY-only shape as LRN-4.
        elif event_type == "DELETE_COURSE":
            self.delete_course_state(event.get("payload", {}).get("uid"))
            return
        # Handle PAUSE_SESSION from learn tab back button (LRN-9)
        elif event_type == "PAUSE_SESSION":
            if self.state == "SOCRATIC_LEARNING":
                self._save_current_course_progress()
                self.stop_audio()
                logging.info("Session paused via PAUSE_SESSION event")
            return

        # State-specific transitions
        if event_type == "TEXT_INPUT":
            self.transcript.append({"sender": "user", "text": text})
            # PERF-5: Cap transcript at 50 entries
            if len(self.transcript) > 50:
                self.transcript = self.transcript[-50:]
            # Safety gate: check all user text input
            node_title = (
                self.current_lesson_node.get("title")
                if self.current_lesson_node
                else None
            )
            safety_result = check_safety_detailed(text, node_title,
                                                  grade_band=self.grade_band)
            if not safety_result.is_safe:
                redirect_msg = get_safety_redirect_message(safety_result)
                logging.warning(
                    f"Safety block [{safety_result.category}]: '{text[:60]}...' confidence={safety_result.confidence:.2f}"
                )
                # B21.5: self-harm / abuse signals pause the lesson, surface
                # crisis resources, and alert the parent immediately — never a
                # silent redirect back into the material.
                if safety_result.category in ("self_harm", "abuse_disclosure"):
                    self._escalate_safety(safety_result.category)
                self.speak(redirect_msg)
                return

        if self.state == "LOBBY":
            if (
                event_type == "TEXT_INPUT" or event_type == "user_speech"
            ):  # Allow user_speech
                if "open" in text or "start course" in text:
                    self.enter_mode_1(text)
                elif "review" in text:
                    self.enter_mode_2(text)
                elif "enter" in text or "palace" in text:
                    self.enter_mode_3(text)
                elif "create" in text:
                    epub_path = event.get("payload", {}).get("filepath")
                    self.start_creation(text, epub_filepath=epub_path)
                elif "list" in text:
                    self.list_courses()
                elif "status" in text:
                    self.report_status()
        elif self.state == "SOCRATIC_LEARNING":
            if event_type == "SKIP_CONCEPT":
                self.speak("Skipping this concept.")
                self._advance_without_completing()
                return
            if event_type == "TEXT_INPUT":
                if self.handle_nav_commands(text):
                    return
                # B13/#12: an attached image (base64 data URI) is passed to the
                # multimodal grader so the tutor can see a diagram or the
                # student's worked answer.
                image = event.get("payload", {}).get("image")
                self.handle_socratic_answer(text, image=image)

        elif self.state == "SPACED_REPETITION":
            if event_type == "TEXT_INPUT":
                if self.handle_nav_commands(text):
                    return
                self.handle_flashcard_answer(text)

        elif self.state == "PAUSED":
            # PAUSED had no branch here at all, so anything a paused student
            # typed that was not literally "resume" fell through this dispatch
            # and vanished -- no reply, no state change, a session that looked
            # dead. A paused session should answer with the one fact that
            # matters: how to get out of it.
            if event_type == "TEXT_INPUT":
                self.speak("The session is paused. Say resume to continue, "
                           "or stop to return to the lobby.")

        elif self.state == "DRAFTING_COURSE":
            if event_type == "TEXT_INPUT":
                self.handle_drafting_input(text)

        elif self.state == "GAP_ANALYSIS":
            if event_type == "TEXT_INPUT":
                self.handle_gap_analysis_input(text)

        elif self.state == "PRE_ASSESSMENT":
            if event_type == "TEXT_INPUT":
                self.handle_pre_assessment_input(text)

        elif self.state == "TEACHING_STYLE_SELECT":
            if event_type == "TEXT_INPUT":
                self.handle_teaching_style_input(text)

        elif self.state == "MEMORY_PALACE":
            if event_type == "TEXT_INPUT":
                lower = text.lower().strip()
                if any(w in lower for w in ["exit", "leave", "back", "quit", "done"]):
                    self.temp_anchor_concept = None
                    self.state = "LOBBY"
                    self.speak("Leaving Memory Palace. You're back in the lobby.")
                elif self.temp_anchor_concept:
                    self.handle_vividness_response(text)
                elif "walk" in lower or "next" in lower or "move" in lower:
                    self.move_locus()
                elif "look" in lower:
                    self.inspect_anchor()
                elif "place" in lower:
                    self.place_concept(text)

    # --- COMMAND HANDLERS ---
    def handle_global_commands(self, event_type, text):
        if event_type == "SPEECH_DETECTED":
            return True

        if not text and event_type != "PAUSE" and event_type != "RESUME":
            return False

        # A3: TOGGLE_MIC / TOGGLE_TTS / TOGGLE_TEXT_ONLY were removed rather than
        # implemented. They are vestiges of the old always-on-voice architecture
        # and had no effect here: the FSM does not own audio. TTS is per-message
        # and client-side (a play button per chat message -> POST /api/tts), and
        # mic capture is push-to-talk in session.js -> POST /api/stt. Nothing in
        # the UI has sent these events since that migration. Swallowing them
        # returned True, which reported success for a state change that never
        # happened. If a global mute is ever wanted, it belongs in the client
        # next to the playback it controls, not in the FSM.
        # A command is what the student SAID, not a word that happens to appear
        # inside an answer. Substring matching here was destructive rather than
        # merely wrong: a genetics student typing "the stop codon terminates
        # translation" hit `"stop" in text`, which cleared current_lesson_node
        # and emptied syllabus_queue -- their answer ungraded and their session
        # queue gone. "A caesura is a pause" moved the FSM to PAUSED, which has
        # no handler in transition(), so every later message was silently
        # dropped until they happened to type a word containing "resume".
        #
        # `_is_command` requires the utterance to BE the command, allowing only
        # trivial politeness around it. Explicit events still route as before,
        # and they are how the UI actually sends these.
        if self._is_command(text, ("pause",)) or event_type == "PAUSE":
            if self.state != "PAUSED":
                self.previous_state = self.state
                self.state = "PAUSED"
            self.speak("Paused. Say resume when you are ready.")
            return True
        if self._is_command(text, ("resume", "continue")) or event_type == "RESUME":
            if self.state == "PAUSED":
                self.state = self.previous_state if self.previous_state else "LOBBY"
                self.speak("Resuming.")
            return True
        if self._is_command(text, ("stop", "reset", "go to lobby", "exit")):
            self.state = "LOBBY"
            self.speak("Returned to lobby.")
            self.current_lesson_node = None
            self.syllabus_queue = []
            return True
        if self._is_command(text, ("end session", "shutdown")):
            self.shutdown()
            return True
        return False

    # Politeness that may wrap a bare command without changing that it is one.
    _COMMAND_FILLER = {"please", "ok", "okay", "now", "helga", "can", "you",
                       "we", "lets", "let's", "just", "i", "want", "to", "the"}

    def _is_command(self, text, commands):
        """True only when the utterance IS one of `commands`.

        Deliberately strict. The cost of a false positive here is a destroyed
        session; the cost of a false negative is that a student types the word
        again on its own. Those are not close.
        """
        cleaned = (text or "").lower().strip().strip(".,!?")
        if not cleaned:
            return False
        for c in commands:
            if cleaned == c:
                return True
            # Allow "please stop", "ok pause now" -- but nothing carrying
            # content of its own.
            extra = [w for w in cleaned.replace(c, " ").split()
                     if w.strip(".,!?")]
            if c in cleaned and extra and all(
                    w.strip(".,!?") in self._COMMAND_FILLER for w in extra):
                return True
        return False

    def handle_nav_commands(self, text):
        # Same reasoning as handle_global_commands. "The next step is to divide
        # both sides by 3" was skipping the concept and abandoning the answer
        # ungraded; "in the previous chapter we saw" was leaving the session.
        if self._is_command(text, ("next", "skip", "move on")):
            if self.state == "SOCRATIC_LEARNING":
                self._advance_without_completing()
                return True
            self.play_sound("STEP_FORWARD")
            return False
        if self._is_command(text, ("go back", "previous", "back")):
            # Save progress and return to path view
            self._save_current_course_progress()
            self.stop_audio()
            self.speak("Returning to course overview.")
            return True
        if "where am i" in text:
            current_topic_title = (
                self.current_lesson_node["title"]
                if self.current_lesson_node
                else "no specific topic"
            )
            self.speak(f"You are in {self.state}, currently on {current_topic_title}.")
            return True
        return False

    def _load_course_bloom_bounds(self, course):
        """Load Bloom floor/ceiling from course dict, with fallback for old courses."""
        bf = course.get("bloom_floor")
        bc = course.get("bloom_ceiling")
        if bf is not None and bc is not None:
            self.course_bloom_floor = bf
            self.course_bloom_ceiling = bc
        else:
            # Fallback: recompute from sliders for old courses
            from services.core.course_builder import compute_course_params
            _cp = compute_course_params(
                scope=course.get("scope", 2),
                mastery=course.get("mastery", 2),
                starting_from=course.get("starting_from", 1),
            )
            self.course_bloom_floor = _cp["bloom_floor"]
            self.course_bloom_ceiling = _cp["bloom_ceiling"]
        # B17.3: the student's grade band bounds whatever the course asks for —
        # a K-2 learner is never pushed past Bloom 3 even by a ceiling-6 course.
        band = get_band_profile(self.grade_band)
        self.course_bloom_ceiling = min(self.course_bloom_ceiling or 6, band["bloom_ceiling"])
        self.course_bloom_floor = max(self.course_bloom_floor or 1, band["bloom_floor"])
        if self.course_bloom_floor > self.course_bloom_ceiling:
            self.course_bloom_floor = self.course_bloom_ceiling
        logging.info(f"Course Bloom bounds (band {self.grade_band}): floor={self.course_bloom_floor}, ceiling={self.course_bloom_ceiling}")

    def _seed_bloom_for_concept(self):
        """Seed bloom level from course floor and set concept target from metadata."""
        self.current_bloom_level = self.course_bloom_floor or 1
        self.concept_bloom_target = (
            self.current_lesson_node.get("bloom_level")
            or self.current_lesson_node.get("module_bloom_target")
            or self.course_bloom_ceiling
            or 6
        )
        self.bloom_correct_streak = 0
        self.passed_question_types = set()

    def _redact_context_for_tutor(self, context_text):
        """GAP 6, as a section SELECTION for the questioner.

        Superseded by `build_tutor_context(text, mode)`; kept because it is a
        stable seam that tests and older call sites use. The behaviour changed
        on purpose: the delete list also removed Core Explanation and Key Facts,
        which are not spoilers — they are what stops the tutor teaching an
        error. Only the worked example is withheld now.
        """
        if not context_text:
            return context_text
        return build_tutor_context(context_text, "socratic")

    @staticmethod
    def _queue_entry(concept, content):
        """One syllabus-queue entry from a flat concept + its markdown.

        This field list was copy-pasted at four call sites (resume, navigate,
        skip, advance). Adding a field meant editing four places and the fifth
        site that got missed simply taught without it — which is how
        `source_confidence` and `llm_fallback` came to be written by the
        builder and read by nobody.
        """
        return {
            "uid": concept["uid"],
            "title": concept["title"],
            "text": content or "",
            "bloom_level": concept.get("bloom_level"),
            "learning_objectives": concept.get("learning_objectives", []),
            "complexity_role": concept.get("complexity_role", ""),
            "module_bloom_target": concept.get("module_bloom_target"),
            "source_confidence": concept.get("source_confidence"),
            "llm_fallback": bool(concept.get("llm_fallback")),
        }

    def _figure_facts_note(self):
        """The tutor's own stated figure values, or "".

        WHY. Measured 2026-08-21 on mathematics, `misconception_holder`, partial
        derivatives: the tutor drew a surface labelling Peak (0,0) z=10 and
        Point A (2,0) z=6, then argued in prose that moving toward A increased
        z. The learner is asked to trust a figure the tutor then contradicts.

        `services/common/figure_facts.py` was written for exactly that and,
        like the model warm-up before it, only ever ran inside the benchmark:
        it recovers aids by parsing JSON out of transcript text, and the
        product keeps specs in `AidStore` with only a slim descriptor in the
        transcript. So the benchmark was protected and learners were not.

        Reads the specs actually shown for this concept. Never raises.
        """
        try:
            ids = getattr(self, "_aid_ids_this_concept", None)
            if not ids:
                return ""
            store = getattr(self, "aid_store", None)
            # `_aid_ids_this_concept` holds BOTH aid ids (generated aids, via
            # _note_aids_shown) and SLOT names (the reuse path adds
            # `decision.slot`). A slot is not a store key, so a reused
            # build-time diagram — the checked one, the one most worth holding
            # the tutor to — would be missed by an id lookup alone.
            prebuilt = getattr(self, "_concept_aids", None) or {}
            specs = []
            for key in ids:
                spec = store.get(key) if store is not None else None
                if spec is None:
                    spec = prebuilt.get(key)
                if spec:
                    specs.append(spec)
            if not specs:
                return ""            # evicted: normal, not an error
            from services.common.figure_facts import facts_from_aids
            return facts_from_aids(specs) or ""
        except Exception as e:
            logging.debug(f"figure facts unavailable: {e}")
            return ""

    def _domain_teaching(self):
        """(concept_kind, pair_block) for the current concept, or (None, None).

        WHY THIS EXISTS
        The domain layer decides HOW a concept is taught: a syntax concept and
        a mechanism concept need opposite turns, and a mined error/fix pair is
        the strongest Socratic move available without a sandbox. All of it is
        computed at build time and stored on the concept.

        None of it reached a learner before this: the prompt call site passed
        neither argument, so every CS course was taught generically while the
        build faithfully computed guidance nothing read. The kinds were
        measured working only because the test harness called the prompt
        function directly — which is not the path a learner takes.

        Returns (None, None) for every non-CS course, which is most of them.
        """
        node = self.current_lesson_node or {}
        kind = node.get("concept_kind")
        pair = node.get("teaching_pair")
        if not kind and not pair:
            return None, None

        # Through the REGISTRY only. The core builds any course and must not
        # name a domain: an import of `domains.computer_science` here is what
        # stops a second domain being added later, and tests/domains asserts
        # its absence.
        domain, module = None, None
        try:
            from services.domains.registry import domain_of, for_domain
            course = self.storage.courses.get_course(self.active_course_uid)
            domain = domain_of(course or {})
            module = for_domain(domain) if domain else None
        except Exception as e:
            logging.debug(f"domain lookup failed: {e}")
        if not domain:
            return None, None

        kind_arg = (domain, kind) if kind else None

        block = None
        if pair and module is not None:
            fn = getattr(module, "pair_block", None)   # optional contract
            if fn:
                try:
                    block = fn(pair) or None
                except Exception as e:
                    # A pair is a bonus. Losing it must never cost the turn.
                    logging.debug(f"pair block failed: {e}")
        return kind_arg, block

    def _grounding_note(self):
        """Tell the tutor how well-sourced THIS concept actually is.

        The build already knows. `source_confidence` is computed per concept by
        the grounding pass (and re-tried once against a broadened query when it
        comes back thin), and `llm_fallback` marks a concept whose title was
        generated to pad an empty lesson. Both were written to structure.json
        and then read by nobody: the tutor taught a 0.12-confidence concept in
        exactly the same voice as a 0.9 one.

        This does not hide the concept or refuse to teach it — it tells the
        model to teach the shape of the idea and stop short of specifics it
        cannot stand behind, which is what a careful human tutor does with a
        topic they half-remember.
        """
        node = getattr(self, "current_lesson_node", None) or {}
        if node.get("llm_fallback"):
            return ("GROUNDING: this concept was generated to fill a gap in the "
                    "syllabus and has no researched source material. Teach the "
                    "general idea, keep to what is uncontroversial, and do not "
                    "state specific figures, dates, names or results.")
        confidence = node.get("source_confidence")
        if confidence is not None and confidence < 0.5:
            return (f"GROUNDING: the research pass found little corroborating "
                    f"material for this concept (confidence {confidence:.2f}), "
                    f"so the notes below lean on the model's own knowledge. "
                    f"Stay with the core idea; do not assert specific figures, "
                    f"dates or named results you are not sure of, and say plainly "
                    f"when something is uncertain rather than guessing fluently.")
        return ""

    def _extract_mastery_criteria(self, content):
        """Extract the Mastery Criteria section from concept markdown for grading."""
        if not content:
            return ""
        return concept_section(content, "Mastery Criteria")

    def _extract_bloom_hook(self, content, bloom_level):
        """Extract the Socratic Hook matching the current Bloom band."""
        if not content:
            return ""
        import re as _re
        # Try new multi-hook format first
        match = _re.search(r"## Socratic Hooks?\s*\n(.*?)(?=\n## |\Z)", content, _re.DOTALL)
        if not match:
            return ""
        hooks_text = match.group(1)
        if bloom_level <= 2:
            band = "1-2"
        elif bloom_level <= 4:
            band = "3-4"
        else:
            band = "5-6"
        band_match = _re.search(rf"Bloom {band}:\s*(.+?)(?:\n|$)", hooks_text)
        if band_match:
            return band_match.group(1).strip().strip('"\'')
        # Fallback: return first hook line
        lines = [l.strip().lstrip("- ") for l in hooks_text.split("\n") if l.strip()]
        return lines[0] if lines else ""

    def _extract_bloom_analogies(self, content, bloom_level):
        """Extract analogies matching the current Bloom band."""
        if not content:
            return []
        import re as _re
        match = _re.search(r"## Analogies\s*\n(.*?)(?=\n## |\Z)", content, _re.DOTALL)
        if not match:
            return []
        text = match.group(1)
        analogies = []
        if bloom_level <= 2:
            # Prefer Simple
            simple = _re.search(r"\*\*Simple\*\*:\s*(.+?)(?:\n|$)", text)
            if simple:
                analogies.append(simple.group(1).strip())
        else:
            # Prefer Technical, fallback to Simple
            tech = _re.search(r"\*\*Technical\*\*:\s*(.+?)(?:\n|$)", text)
            if tech:
                analogies.append(tech.group(1).strip())
            simple = _re.search(r"\*\*Simple\*\*:\s*(.+?)(?:\n|$)", text)
            if simple:
                analogies.append(simple.group(1).strip())
        if not analogies:
            analogies = [l.strip().lstrip("- ") for l in text.split("\n") if l.strip() and not l.strip().startswith("#")]
        return analogies

    def _should_park_concept(self):
        """Has this concept absorbed a whole lesson without being learned?

        Deliberately NOT part of the mastery gate. Letting a turn cap satisfy
        the gate would credit mastery nobody demonstrated — the same mistake the
        fallback grade avoids by never being a passing grade. Parking is the
        opposite move: the concept is explicitly NOT completed, it goes back to
        FSRS, and the learner gets to keep going.

        Measured: without this, adult sessions ran to a 25-turn cap on a single
        concept and never ended. A lesson has to terminate for every learner.
        """
        if self.concept_question_count < CONCEPT_TURN_CAP:
            return False
        logging.warning(
            f"Concept turn cap reached ({self.concept_question_count} questions, "
            f"streak={self.concept_correct_streak}) — parking this concept for "
            f"spaced review rather than grinding on it")
        return True

    def _get_turn_state(self):
        """The A.2 turn state, created on demand.

        Lazily rather than relying on __init__: several construction paths
        (and every test that builds a partial FSM) never run it, and a missing
        bookkeeping attribute must not be able to raise inside the grading
        path. The worst case is a fresh, empty state — which renders nothing,
        exactly as it does on the first turn of any concept.
        """
        ts = getattr(self, "turn_state", None)
        if ts is None:
            ts = TurnState()
            self.turn_state = ts
        return ts

    def _question_types(self):
        """The question types this learner's band can actually answer.

        Mode B: a five-year-old cannot answer a Mechanism or Synthesis
        question — those need several elements held in working memory at once,
        and a 5-year-old holds about four items total. Cycling them through
        question forms they cannot answer made the mastery gate unreachable.

        Mode A carries no band and gets all six, unchanged.
        """
        from services.common.prompts import question_types_for_band
        return question_types_for_band(getattr(self, "grade_band", None))

    def _check_mastery_gate(self):
        """GAP 4 + B17.3: mastery gate — streak, bloom target, and question
        diversity, with thresholds from the student's grade band (a K-2 kid
        passes with 2 correct over 2 questions and 2 distinct types; 9-12
        needs 3 over 4 and 3 types)."""
        band = get_band_profile(self.grade_band)
        need_streak = band["gate_streak"]
        need_questions = band["gate_questions"]
        # Bounded by what this BAND offers, not by all six: a K-1 learner is
        # offered three concrete types, so a gate of three would demand every
        # one of them.
        need_types = min(band["gate_types"], len(self._question_types()))
        streak_met = (self.concept_correct_streak >= need_streak
                      and self.concept_question_count >= need_questions)
        bloom_met = self.current_bloom_level >= min(
            self.concept_bloom_target or 1, self.course_bloom_ceiling or 6)
        diversity_met = len(self.passed_question_types) >= need_types
        if streak_met and bloom_met and diversity_met:
            return True
        reasons = []
        if not streak_met:
            reasons.append(f"streak={self.concept_correct_streak}/{need_streak}, "
                           f"count={self.concept_question_count}/{need_questions}")
        if not bloom_met:
            reasons.append(f"bloom={self.current_bloom_level}/{self.concept_bloom_target}")
        if not diversity_met:
            reasons.append(f"types={len(self.passed_question_types)}/{need_types}")
        logging.info(f"Mastery gate not met ({', '.join(reasons)}): recycling question types")
        return False

    def _next_unpassed_type_index(self):
        """Fix 6: Find the first question type not yet passed by the student."""
        for i, qt in enumerate(self._question_types()):
            if qt["key"] not in self.passed_question_types:
                return i
        return 0  # All passed — start from beginning

    def report_status(self):
        self.play_sound("RETENTION_HISS")
        self.speak(f"All systems nominal. Battery at {self.battery_level} percent.")

    def _current_concept_is_hd(self):
        """HD framing flag for the live tutor prompt (B21.4 §4.3)."""
        try:
            uid = (self.current_lesson_node or {}).get("uid")
            return bool(uid) and self.storage.standards.concept_is_health_strand6(uid)
        except Exception:
            return False

    def _log_safety_incident(self, category, node_title=None):
        """Audit trail for suppressed outputs / crisis signals — never the raw
        text, only category + context (B21.5)."""
        try:
            self.storage.audit.record(
                "safety_incident", actor_id=self.student_id, actor_role="student",
                subject_student_id=self.student_id,
                detail={"category": category, "concept": node_title})
        except Exception as e:
            logging.warning(f"safety incident log failed: {e}")

    def _escalate_safety(self, category):
        """B21.5 §5.4: immediate parent alert on self-harm/abuse signals. The
        alert never transcribes the child's words — it says a safety concern
        was detected and to check in. Never raises."""
        try:
            student = self.storage.accounts.get_student(self.student_id) or {}
            parent_id = student.get("parent_id")
            if parent_id:
                self.storage.notifications.create(
                    parent_id, "parent", "struggle_alert",
                    title="Please check in with your learner",
                    body=(f"Helga detected a possible safety concern during "
                          f"{student.get('display_name', 'your learner')}'s "
                          "session and paused the lesson. No details are stored. "
                          "Please check in with them soon."),
                    ref_uid=self.student_id)
            self._log_safety_incident(category)
        except Exception as e:
            logging.error(f"safety escalation failed: {e}")

    def _hd_consent_blocked(self, concept_uid):
        """B21.4 render backstop: True when the concept is Health Strand 6
        gated and this student's parent has no current consent. Fails closed
        for gated concepts, open for lookup errors on non-gated paths."""
        try:
            if not self.storage.standards.concept_is_health_strand6(concept_uid):
                return False
            student = self.storage.accounts.get_student(self.student_id) or {}
            parent_id = student.get("parent_id")
            if not parent_id:
                return True   # gated content with no resolvable guardian: block
            return not self.storage.consent.has_consent(
                parent_id, "health_strand6", student_id=self.student_id)
        except Exception as e:
            logging.error(f"HD gate check failed (blocking): {e}")
            return True

    def get_concept_details(self, uid):
        """Helper to fetch concept details from local storage.
        GAP 3: Now returns bloom metadata alongside content."""
        # B21.4: no HD content is delivered the moment consent lapses
        if self._hd_consent_blocked(uid):
            self.send_status_update(
                "This lesson needs a parent's OK first.",
                event={"type": "CONSENT_REQUIRED", "consent": "health_strand6",
                       "concept_uid": uid})
            self.speak("This lesson needs a parent's permission before we can "
                       "start it. Ask your parent to approve it from their "
                       "dashboard!")
            return None
        try:
            if self.active_course_uid:
                concept = self.storage.courses.get_concept_by_uid(
                    self.active_course_uid, uid
                )
                if concept:
                    content = self.storage.courses.get_concept_content(
                        self.active_course_uid, uid
                    )
                    return {
                        "uid": uid,
                        "title": concept["title"],
                        "text": content,
                        "resource_text": content,
                        "bloom_level": concept.get("bloom_level"),
                        "learning_objectives": concept.get("learning_objectives", []),
                        "complexity_role": concept.get("complexity_role", ""),
                        "depth_level": concept.get("depth_level"),
                    }
            # Fallback: search across all courses
            result = self.storage.courses.find_concept_across_courses(uid)
            if result:
                content = self.storage.courses.get_concept_content(
                    result["course_uid"], uid
                )
                return {
                    "uid": uid,
                    "title": result["title"],
                    "text": content,
                    "resource_text": content,
                }
            return None
        except Exception as e:
            logging.error(f"Failed to fetch concept details for {uid}: {e}")
            return None

    def load_topic_context(self, topic_uid):
        """Loads lesson context from StorageManager."""
        if self.active_course_uid:
            content = self.storage.courses.get_concept_content(
                self.active_course_uid, topic_uid
            )
            if content:
                logging.info(f"Loaded context from storage for {topic_uid}")
                return content

        # Fallback to DB (handled by caller if this returns None or we can fetch here)
        details = self.get_concept_details(topic_uid)
        return details.get("resource_text", "") if details else ""

    def _atomic_write(self, filepath, content):
        """
        Atomically writes content to a file to prevent corruption.
        Writes to a temp file first, then renames.
        """
        dir_name = os.path.dirname(filepath)
        os.makedirs(dir_name, exist_ok=True)

        tmp_path = filepath + f".tmp.{uuid.uuid4()}"
        try:
            with open(tmp_path, "w") as f:
                if isinstance(content, list):
                    f.writelines(content)
                else:
                    f.write(content)
                f.flush()
                os.fsync(f.fileno())  # Ensure write to disk

            os.replace(tmp_path, filepath)  # Atomic move
            logging.info(f"Atomically wrote to {filepath}")
            return True
        except Exception as e:
            logging.error(f"Failed atomic write to {filepath}: {e}")
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            return False

    def update_topic_context(self, topic_uid, new_content):
        """Saves updated context back to Markdown file."""
        if not self.active_course_uid:
            logging.error("Cannot update context: No active course UID set.")
            return False

        try:
            self.storage.courses.save_concept_content(
                self.active_course_uid, topic_uid, new_content
            )
            return True
        except Exception as e:
            logging.error(f"Failed to update topic context: {e}")
            return False

    def append_session_note(self, topic_uid, note):
        """Appends a timestamped note to the Session Notes section of the Markdown file."""
        if not self.active_course_uid:
            return

        md_path = os.path.join(
            self.data_root, f"courses/{self.active_course_uid}/content/{topic_uid}.md"
        )
        if not os.path.exists(md_path):
            logging.warning(
                f"Markdown file {md_path} not found. Creating basic version."
            )
            self.update_topic_context(
                topic_uid, f"# Topic {topic_uid}\n\n## Session Notes\n"
            )

        try:
            with open(md_path, "r") as f:
                lines = f.readlines()

            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            formatted_note = f"\n- [{timestamp}] {note}\n"

            # Find the Session Notes section
            notes_idx = -1
            for i, line in enumerate(lines):
                if "## Session Notes" in line:
                    notes_idx = i
                    break

            if notes_idx != -1:
                # Insert after the header
                lines.insert(notes_idx + 1, formatted_note)
            else:
                # Add section at the end
                if lines and not lines[-1].endswith("\n"):
                    lines.append("\n")
                lines.append("\n## Session Notes\n" + formatted_note)

            self._atomic_write(md_path, lines)
        except Exception as e:
            logging.error(f"Error appending session note: {e}")

    def navigate_to_topic(self, topic_uid):
        """Allows direct navigation to a topic via its UID."""
        self.stop_audio()
        self.play_sound("MODE_SWITCH_CLICK")  # Indicate navigation
        concept_details = self.get_concept_details(topic_uid)

        if concept_details:
            self.state = "SOCRATIC_LEARNING"

            # Load from Markdown preferentially
            file_context = self.load_topic_context(topic_uid)

            self.current_lesson_node = {
                "uid": concept_details["uid"],
                "title": concept_details["title"],
                "text": file_context or concept_details.get("resource_text", ""),
                "bloom_level": concept_details.get("bloom_level"),
                "learning_objectives": concept_details.get("learning_objectives", []),
                "complexity_role": concept_details.get("complexity_role", ""),
                # Domain teaching data, attached at BUILD time. Carried here
                # because this dict is all the tutor sees at teaching time —
                # dropping these silently downgrades a CS concept to generic
                # teaching, with nothing failing to show it.
                "concept_kind": concept_details.get("concept_kind"),
                "teaching_pair": concept_details.get("teaching_pair"),
                "code_example": concept_details.get("code_example"),
            }
            self.current_context = self.current_lesson_node["text"][:10000]
            self.syllabus_queue = []  # Clear existing queue when navigating
            self.last_lesson_title = None  # Reset bridge
            self.transcript = []  # Clear chat history for new session
            self.conversation_history = []  # Clear stale LLM context for new topic

            # Progress: 20% - Context Loaded
            self.send_status_update("Analyzing Context...", progress=20)

            logging.info(f"Navigating to topic: {self.current_lesson_node['title']}")

            # Progress: 40% - Initiating Socratic Logic
            self.send_status_update("Preparing Pedagogical Strategy...", progress=40)

            # Reset teaching state and seed Bloom from course/concept metadata
            self.socratic_type_index = 0
            self.concept_correct_streak = 0
            self.concept_question_count = 0
            self._seed_bloom_for_concept()
            self.ask_socratic_question(
                "Initiate concept exploration."
            )
        else:
            self.speak("I couldn't find information for that topic.")
            self.state = "LOBBY"  # Return to lobby if navigation fails

    # --- MODE 1: SOCRATIC LEARNING ---
    def resume_course(self, uid, title=None):
        """Directly resume a course by UID, bypassing search."""
        self.play_sound("MODE_SWITCH_CLICK")

        # Switch Context Logic
        if self.active_course_uid and self.active_course_uid != uid:
            self._save_current_course_progress()
            self.speak(f"Saving progress for current course.")

        self.state = "SOCRATIC_LEARNING"
        self.active_course_uid = uid
        # Resuming is a new session too — reload assets and reset session pacing.
        self.load_course_assets(uid)

        # Fetch teaching_style from local storage for Dynamic Persona
        try:
            course = self.storage.courses.get_course(uid)
            if course:
                self._load_course_bloom_bounds(course)
                self.current_teaching_style = course.get("teaching_style", "")
                if self.current_teaching_style:
                    logging.info(
                        f"Teaching style loaded: {self.current_teaching_style}"
                    )
        except Exception as e:
            logging.warning(f"Could not fetch teaching style: {e}")

        # Try to restore
        if self._load_course_progress(uid):
            self.speak(f"Resuming {title or 'course'}.")
            if self.current_lesson_node:
                self.speak(f"We were discussing {self.current_lesson_node['title']}.")
                self.ask_socratic_question("Resume discussion.")
            else:
                self.next_syllabus_item()
            return

        # If no saved state, treat as new start (fetch syllabus)
        # Progressive availability: only queue concepts that have hydrated content
        self.speak(f"Starting {title or 'course'}.")
        try:
            concepts = self.storage.courses.get_flat_concepts(uid)
            self.syllabus_queue = []
            skipped = 0
            for c in concepts:
                content = self.storage.courses.get_concept_content(uid, c["uid"])
                if content and len(content.strip()) > 50:
                    self.syllabus_queue.append(self._queue_entry(c, content))
                else:
                    skipped += 1
            if skipped > 0:
                logging.info(f"Progressive availability: {len(self.syllabus_queue)} concepts ready, {skipped} still hydrating")
            if not self.syllabus_queue:
                self.syllabus_queue = [{"uid": uid, "title": title, "text": "Intro"}]
            self.next_syllabus_item()
        except Exception as e:
            logging.error(f"Resume Error: {e}")
            self.speak("Technical error loading course.")
            self.state = "LOBBY"

    def enter_mode_1(self, text):
        self.play_sound("MODE_SWITCH_CLICK")
        topic = (
            text.replace("open course", "")
            .replace("open", "")
            .replace("start course", "")
            .strip()
        )

        self.send_status_update("Researching...")
        try:
            # Search courses locally
            results = []
            for course in self.storage.courses.list_courses():
                if topic.lower() in course.get("title", "").lower():
                    results.append(course)

            if not results:
                self.speak(
                    f"I couldn't find a course on {topic}. You can say 'create a course on {topic}' to build one, or say 'list courses' to see what's available."
                )
                self.state = "LOBBY"
                return

            target_course = results[0]
            target_uid = target_course["uid"]

            # Switch Context Logic
            if self.active_course_uid and self.active_course_uid != target_uid:
                self._save_current_course_progress()
                self.speak(f"Saving progress for current course.")

            self.state = "SOCRATIC_LEARNING"
            self.active_course_uid = target_uid
            self._load_course_bloom_bounds(target_course)

            # Try to restore progress for this course
            restored = self._load_course_progress(target_uid)
            if restored:
                self.speak(f"Resuming {target_course['title']}.")
                if self.current_lesson_node:
                    self.speak(
                        f"We were discussing {self.current_lesson_node['title']}."
                    )
                    self.ask_socratic_question("Resume discussion.")
                else:
                    self.next_syllabus_item()
                return

            # If no progress, load fresh from local storage
            concepts = self.storage.courses.get_flat_concepts(target_uid)
            self.syllabus_queue = []
            for c in concepts:
                content = self.storage.courses.get_concept_content(target_uid, c["uid"])
                self.syllabus_queue.append(self._queue_entry(c, content))

            if not self.syllabus_queue:
                self.syllabus_queue = [target_course]

            self.speak(
                f"Opening {target_course['title']}. {len(self.syllabus_queue)} steps remaining."
            )
            self.next_syllabus_item()
        except Exception as e:
            logging.error(f"Mode 1 Error: {e}", exc_info=True)
            self.speak("Technical error loading course.")
            self.state = "LOBBY"

    def _read_session_blob(self):
        """Read this student's fsm_sessions blob (B15.7). Returns the parsed
        dict or a fresh skeleton. One-time legacy import: if the student has
        no row yet but the old user_state.json exists, adopt its contents so
        the pre-multi-tenant user's position survives the migration."""
        try:
            row = self.storage.fsm.get(self.student_id)
            if row and row.get("blob"):
                return json.loads(row["blob"])
        except Exception as e:
            logging.error(f"Failed to read fsm_sessions blob: {e}")
        # Legacy import (only meaningful for the legacy student's first load)
        try:
            if self.student_id == DEFAULT_STUDENT_ID and os.path.exists(self.state_file):
                with open(self.state_file, "r") as f:
                    legacy = json.load(f)
                if isinstance(legacy, dict) and legacy.get("courses"):
                    legacy["schema"] = 1
                    logging.info("Imported legacy user_state.json into fsm_sessions")
                    return legacy
        except Exception as e:
            logging.warning(f"Legacy user_state.json import failed: {e}")
        return {"schema": 1, "courses": {}}


    def _warm_model_async(self):
        """Pay the model's cold load off the critical path. Never raises."""
        try:
            import threading

            def _warm():
                try:
                    if getattr(self, "llm_client", None):
                        self.llm_client.warm_up()
                        logging.info("[WARM] model ready for this lesson")
                except Exception as e:
                    logging.debug(f"[WARM] warm-up failed: {e}")

            t = threading.Thread(target=_warm, daemon=True,
                                 name="helga-model-warmup")
            t.start()
        except Exception as e:                # pragma: no cover - defensive
            logging.debug(f"[WARM] could not start warm-up: {e}")

    def _save_current_course_progress(self):
        if not self.active_course_uid:
            return

        try:
            # Load existing blob to not overwrite other courses' progress
            full_state = self._read_session_blob()

            course_state = {
                "current_node": self.current_lesson_node,
                "syllabus_queue": self.syllabus_queue,
                "completed_topics": list(self.completed_topics),
                "transcript": self.transcript[-20:],
                "conversation_history": self.conversation_history[-10:],
                "socratic_type_index": self.socratic_type_index,
                "concept_correct_streak": self.concept_correct_streak,
                "concept_miss_streak": getattr(self, "concept_miss_streak", 0),
                "concept_question_count": self.concept_question_count,
                # Bloom's Taxonomy level persistence
                "bloom_level": self.current_bloom_level,
                "bloom_correct_streak": self.bloom_correct_streak,
                # Memory Palace persistence
                "palace_index": getattr(self, '_palace_index', 0),
                "palace_locus_uid": self.current_locus_uid,
                "palace_locus_desc": self.current_locus_desc,
                # New progression state
                "concept_bloom_target": self.concept_bloom_target,
                "passed_question_types": list(self.passed_question_types),
                "prior_concepts_summary": self.prior_concepts_summary,
                "course_bloom_floor": self.course_bloom_floor,
                "course_bloom_ceiling": self.course_bloom_ceiling,
            }

            if "courses" not in full_state:
                full_state["courses"] = {}
            full_state["courses"][self.active_course_uid] = course_state
            full_state["last_active_uid"] = self.active_course_uid
            full_state["schema"] = 1
            full_state["state"] = self.state
            full_state["grade_band"] = self.grade_band

            # B15.7: single-row upsert in WAL — atomic by construction, so the
            # old LRN-8 atomic-file-write concern disappears.
            self.storage.fsm.upsert(self.student_id, json.dumps(full_state))
            logging.info(f"Saved progress for course {self.active_course_uid} "
                         f"(student {self.student_id})")
        except Exception as e:
            logging.error(f"Failed to save state: {e}")

    def _load_course_progress(self, course_uid):
        try:
            full_state = self._read_session_blob()
            courses = full_state.get("courses", {})
            if course_uid in courses:
                data = courses[course_uid]
                self.current_lesson_node = data.get("current_node")
                self.syllabus_queue = data.get("syllabus_queue", [])
                self.completed_topics = set(data.get("completed_topics", []))
                self.transcript = data.get("transcript", [])
                self.conversation_history = data.get("conversation_history", [])
                self.socratic_type_index = data.get("socratic_type_index", 0)
                self.concept_correct_streak = data.get("concept_correct_streak", 0)
                self.concept_miss_streak = data.get("concept_miss_streak", 0)
                self.concept_question_count = data.get("concept_question_count", 0)
                # Restore Bloom's Taxonomy level
                self.current_bloom_level = data.get("bloom_level", 1)
                self.bloom_correct_streak = data.get("bloom_correct_streak", 0)
                # Restore Memory Palace state
                self._palace_index = data.get("palace_index", 0)
                self.current_locus_uid = data.get("palace_locus_uid")
                self.current_locus_desc = data.get("palace_locus_desc", "")
                # Restore new progression state
                self.concept_bloom_target = data.get("concept_bloom_target")
                self.passed_question_types = set(data.get("passed_question_types", []))
                self.prior_concepts_summary = data.get("prior_concepts_summary", [])
                self.course_bloom_floor = data.get("course_bloom_floor", self.course_bloom_floor)
                self.course_bloom_ceiling = data.get("course_bloom_ceiling", self.course_bloom_ceiling)
                # Restore context
                if self.current_lesson_node:
                    self.current_context = self.current_lesson_node.get("text", "")[
                        :10000
                    ]
                logging.info(f"Restored progress for course {course_uid}")
                return True
        except Exception as e:
            logging.error(f"Failed to load course progress: {e}")
        return False

    def _hydrate_from_row(self):
        """Initial load on construction (B15.7) — replaces _load_state_from_disk.
        We stay in LOBBY but remember the last active course so RESUME works."""
        try:
            full_state = self._read_session_blob()
            last_uid = full_state.get("last_active_uid")
            if last_uid:
                self.active_course_uid = last_uid
                logging.info(f"Found last active course for {self.student_id}: {last_uid}")
        except Exception as e:
            logging.error(f"Failed to hydrate FSM session: {e}")

    def shutdown(self):
        self.state = "SHUTDOWN"
        logging.info("Initiating shutdown sequence.")

        # AUTO-5: Mark creation as aborted if in progress
        if self.creation_in_progress:
            self.creation_status["phase"] = "aborted"
            self.creation_status["active"] = False
            self.creation_in_progress = False
            logging.warning("Course creation aborted due to shutdown")

        # Save state before exit
        self._save_current_course_progress()

        logging.info("Shutdown complete. Exiting process.")
        sys.exit(0)

    def _schedule_unit_reviews_if_complete(self):
        """Schedule spaced reviews when all concepts in a unit are completed."""
        if not self.active_course_uid:
            return
        try:
            from datetime import date as _date

            today = _date.today().isoformat()
            # Use completed topics as a proxy for unit title
            unit_title = f"Review: {len(self.completed_topics)} concepts"
            # Generate a uid from completed set
            unit_uid = f"unit-review-{hash(frozenset(self.completed_topics)) % 100000}"
            self.storage.schedule.schedule_unit_reviews(
                self.active_course_uid, unit_uid, unit_title, today
            )
            logging.info(f"Scheduled reviews for completed unit: {unit_title}")
        except Exception as e:
            logging.warning(f"Failed to schedule unit reviews: {e}")

    def _advance_without_completing(self):
        """Advance to the next concept WITHOUT marking the current one as completed.
        Used by SKIP_CONCEPT and 'next' voice command."""
        # The concept being skipped is, by definition, not completed — so a
        # repopulate that only excludes completed_topics puts it straight back
        # at the head of the queue, and pop(0) hands the student the exact
        # concept they just asked to leave. On the last concept of a course
        # this was an unbreakable loop: SKIP re-taught the skipped concept
        # forever.
        skipped_uid = (self.current_lesson_node or {}).get("uid")
        if not self.syllabus_queue:
            # Try auto-populate like next_syllabus_item does
            if self.active_course_uid:
                try:
                    all_concepts = self.storage.courses.get_flat_concepts(self.active_course_uid)
                    for c in all_concepts:
                        if c["uid"] in self.completed_topics or c["uid"] == skipped_uid:
                            continue
                        content = self.storage.courses.get_concept_content(
                            self.active_course_uid, c["uid"]
                        )
                        self.syllabus_queue.append(self._queue_entry(c, content))
                except Exception as e:
                    logging.warning(f"Failed to auto-populate syllabus on skip: {e}")

            if not self.syllabus_queue:
                self.speak("No more concepts in this course.")
                self.state = "LOBBY"
                self.current_lesson_node = None
                return

        # GAP 7: Capture prior concept summary before transitioning
        if self.current_lesson_node:
            self.prior_concepts_summary.append({
                "title": self.current_lesson_node.get("title", ""),
                "bloom_achieved": self.current_bloom_level,
            })
            self.prior_concepts_summary = self.prior_concepts_summary[-5:]

        # Pop next concept and start it
        self.current_lesson_node = self.syllabus_queue.pop(0)
        self.current_context = self.current_lesson_node.get("text", "")[:10000]

        # Reset teaching state for new concept
        self.socratic_type_index = 0
        self.socratic_retry_count = 0
        self.concept_correct_streak = 0
        self.concept_question_count = 0
        # GAP 1+3: Seed Bloom from course floor and set concept target
        self._seed_bloom_for_concept()
        self.conversation_history = []

        # Fetch pedagogy context for the new concept
        self.current_misconceptions = []
        self.current_analogies = []
        self.current_mastery_criteria = ""
        try:
            if self.active_course_uid:
                content = self.storage.courses.get_concept_content(
                    self.active_course_uid, self.current_lesson_node["uid"]
                )
                if content:
                    import re as _re
                    misc_match = _re.search(
                        r"## Misconceptions\s*\n(.*?)(?=\n##\s|\Z)", content, _re.DOTALL
                    )
                    if misc_match:
                        self.current_misconceptions = [
                            l.strip().lstrip("- ")
                            for l in misc_match.group(1).split("\n")
                            if l.strip() and not l.strip().startswith("#")
                        ]
                    # Use Bloom-aware extraction for analogies and hooks
                    self.current_analogies = self._extract_bloom_analogies(
                        content, self.current_bloom_level
                    )
                    # Extract mastery criteria for grading
                    self.current_mastery_criteria = self._extract_mastery_criteria(content)
        except Exception as e:
            logging.warning(f"Failed to fetch pedagogy context: {e}")

        self.speak(f"Moving to {self.current_lesson_node['title']}.")
        self.conversation_history.append((None, f"Moving to {self.current_lesson_node['title']}."))
        self.last_lesson_title = self.current_lesson_node["title"]

        # Broadcast progress
        completed_count = len(self.completed_topics)
        total_count = completed_count + len(self.syllabus_queue) + 1
        self.send_status_update(
            f"CPROG:{self.current_lesson_node['title']}:{completed_count}:{total_count}"
        )

        self.ask_socratic_question("Initiate concept exploration.")

    def next_syllabus_item(self):
        if self.current_lesson_node and self.current_lesson_node.get("uid"):
            self.completed_topics.add(self.current_lesson_node["uid"])
            # Mark concept completed in progress store (so path view shows green nodes)
            # Also persist final Bloom's taxonomy level achieved for this concept
            try:
                self.storage.progress.update_progress(
                    self.current_lesson_node["uid"],
                    self.active_course_uid or "unknown",
                    status="completed",
                    bloom_level=self.current_bloom_level,
                )
            except Exception as e:
                logging.warning(f"Failed to mark concept completed in progress store: {e}")
            # Log completion to activity store
            try:
                self.storage.activity.log_activity(
                    self.active_course_uid or "unknown",
                    self.current_lesson_node["uid"],
                    "concept_completed",
                    {"title": self.current_lesson_node.get("title", "")},
                )
            except Exception as e:
                logging.warning(f"Failed to log completion activity: {e}")

            # BUG-9 FIX: Schedule FSRS review based on Socratic performance
            try:
                self.storage.schedule.schedule_concept_review(
                    self.active_course_uid or "unknown",
                    self.current_lesson_node["uid"],
                    self.current_lesson_node.get("title", ""),
                    rating=self._last_socratic_grade,
                )
            except Exception as e:
                logging.warning(f"Failed to schedule concept review: {e}")

            # ANKI: Auto-generate flashcards on concept completion (fire-and-forget)
            try:
                import threading
                def _generate_flashcards_async():
                    try:
                        import requests as req
                        req.post(
                            f"{self.rag_url}/api/auto_generate_flashcards",
                            json={
                                "course_uid": self.active_course_uid or "unknown",
                                "concept_uid": self.current_lesson_node["uid"],
                                "concept_title": self.current_lesson_node.get("title", ""),
                                "grade": self._last_socratic_grade,
                            },
                            timeout=60,
                        )
                        logging.info(f"[ANKI] Flashcards auto-generated for concept: {self.current_lesson_node.get('title', '')}")
                    except Exception as e:
                        logging.warning(f"[ANKI] Auto flashcard generation failed: {e}")
                threading.Thread(target=_generate_flashcards_async, daemon=True).start()
            except Exception as e:
                logging.warning(f"[ANKI] Failed to start flashcard generation thread: {e}")

            # Auto-save progress after each concept completion
            self._save_current_course_progress()

        if not self.syllabus_queue:
            # BUG-8 FIX: Auto-populate with remaining uncompleted concepts
            if self.active_course_uid:
                try:
                    all_concepts = self.storage.courses.get_flat_concepts(self.active_course_uid)
                    for c in all_concepts:
                        if c["uid"] not in self.completed_topics:
                            content = self.storage.courses.get_concept_content(
                                self.active_course_uid, c["uid"]
                            )
                            self.syllabus_queue.append(self._queue_entry(c, content))
                    if self.syllabus_queue:
                        logging.info(f"Auto-populated syllabus with {len(self.syllabus_queue)} remaining concepts")
                except Exception as e:
                    logging.warning(f"Failed to auto-populate syllabus: {e}")

            # If still empty, course is truly complete
            if not self.syllabus_queue:
                self.play_sound("SUCCESS_CHORD")
                self.speak("Course complete! Great work. You can say 'review' to practice with flashcards, 'enter palace' to explore the memory palace, or 'open' followed by another course name to keep learning.")
                self._schedule_unit_reviews_if_complete()
                self.send_status_update("COURSE_COMPLETE")
                self.state = "LOBBY"
                self.current_lesson_node = None
                return

        self.current_lesson_node = self.syllabus_queue.pop(0)
        self.current_context = self.current_lesson_node.get("text", "")[:10000]

        # Phase 5: Fetch Teaching Context (Pedagogy)
        self.current_misconceptions = []
        self.current_analogies = []
        try:
            self.send_status_update("Fetching Pedagogical Context...")
            # Load pedagogy from markdown content directly
            if self.active_course_uid:
                content = self.storage.courses.get_concept_content(
                    self.active_course_uid, self.current_lesson_node["uid"]
                )
                if content:
                    import re as _re

                    # Extract misconceptions section
                    misc_match = _re.search(
                        r"## Misconceptions\s*\n(.*?)(?=\n##\s|\Z)", content, _re.DOTALL
                    )
                    if misc_match:
                        self.current_misconceptions = [
                            l.strip().lstrip("- ")
                            for l in misc_match.group(1).split("\n")
                            if l.strip() and not l.strip().startswith("#")
                        ]
                    # Extract analogies section (##+ matches both ## and ###)
                    ana_match = _re.search(
                        r"##+ Analogies\s*\n(.*?)(?=\n##\s|\Z)",
                        content,
                        _re.DOTALL,
                    )
                    if ana_match:
                        self.current_analogies = [
                            l.strip().lstrip("- ")
                            for l in ana_match.group(1).split("\n")
                            if l.strip() and not l.strip().startswith("#")
                        ]
                    logging.info(
                        f"Retrieved pedagogy for {self.current_lesson_node['title']}: {len(self.current_misconceptions)} misc, {len(self.current_analogies)} analogies"
                    )
                    # Broadcast pedagogy context to UI sidebar
                    if self.current_misconceptions or self.current_analogies:
                        self.send_status_update(
                            f"PEDAGOGY:{json.dumps({'misconceptions': self.current_misconceptions[:5], 'analogies': self.current_analogies[:5]})}"
                        )
        except Exception as e:
            logging.warning(f"Failed to fetch teaching context: {e}")

        # Hook / Bridge
        intro = f"Moving to {self.current_lesson_node['title']}."
        if self.last_lesson_title:
            self.send_status_update("Generating Bridge...")
            prompt = get_bridge_prompt(
                self.last_lesson_title, self.current_lesson_node["title"]
            )
            try:
                logging.info(f"LLM Bridge Request for: {self.last_lesson_title} -> {self.current_lesson_node['title']}")
                raw = self._call_llm(prompt, max_tokens=200, timeout=45)
                if raw:
                    intro = clean_llm_response(raw)
                    if not intro:
                        intro = f"Moving to {self.current_lesson_node['title']}."
                    logging.info(f"LLM Bridge Content: {intro[:100]}...")
            except Exception as e:
                logging.error(f"LLM Bridge Exception: {e}")

        self.speak(intro)
        self.conversation_history.append(
            (None, intro)
        )  # Add intro to history so it appears in chat
        self.last_lesson_title = self.current_lesson_node["title"]
        self.socratic_type_index = 0  # Reset question type for new concept
        self.socratic_retry_count = 0  # Reset retry counter for new concept
        self.concept_correct_streak = 0  # Reset mastery tracking for new concept
        self.concept_miss_streak = 0
        self.concept_question_count = 0
        self.current_bloom_level = 1  # Reset Bloom's level for new concept
        self.bloom_correct_streak = 0
        self._reset_aid_budget()

        # Broadcast concept progress to UI
        completed_count = len(self.completed_topics)
        total_count = completed_count + len(self.syllabus_queue) + 1  # +1 for current
        self.send_status_update(
            f"CPROG:{self.current_lesson_node['title']}:{completed_count}:{total_count}"
        )

        self.ask_socratic_question(
            "Initiate concept exploration."
        )

    def ask_socratic_question(self, context_trigger, initial_mode=None):
        self.send_status_update("Reviewing History...", progress=60)

        # Prepare structured conversation history
        structured_history = []
        system_note = None

        # Determine Teaching Mode
        teaching_mode = "QUESTION"  # Default

        if initial_mode:
            teaching_mode = initial_mode
        elif self.conversation_history:
            last_entry = self.conversation_history[-1]
            if last_entry[0]:
                last_text = str(last_entry[0]).strip()
                # BUG-10 FIX: Rule-based mode selection (replaces slow LLM classifier)
                # This saves one full LLM round-trip (~15s on Jetson) per question cycle
                if self._detect_ignorance(last_text.lower()):
                    teaching_mode = "LECTURE"
                elif self._last_socratic_grade <= 1:
                    teaching_mode = "LECTURE"
                # Fix 5: Grade 2 with 2+ consecutive partials → scaffolding lecture
                elif self._last_socratic_grade == 2 and self.socratic_retry_count >= 2:
                    teaching_mode = "LECTURE"

        # B17.7: affect handling for young learners — after 2+ consecutive
        # misses, a K-2/3-5 student gets encouragement plus an EASIER, more
        # concrete next step instead of being pressed harder. Bloom eases
        # toward the floor so the next question genuinely gets simpler.
        affect_note = None
        if (is_young_band(self.grade_band)
                and getattr(self, "concept_miss_streak", 0) >= 2):
            floor = self.course_bloom_floor or 1
            if self.current_bloom_level > floor:
                self.current_bloom_level -= 1
                self.bloom_correct_streak = 0
            affect_note = (
                "AFFECT NOTE: The student has missed several questions in a row "
                "and may be feeling discouraged. START by warmly reassuring them "
                "that tricky things take practice and they are doing fine. Then "
                "make this turn EASIER and more concrete than the last one: use "
                "a smaller, touchable example, or break the idea into one tiny "
                "step. Do not press harder, do not point out the string of "
                "misses, and never sound disappointed.")
            logging.info(f"Affect scaffold engaged (miss_streak="
                         f"{self.concept_miss_streak}, band={self.grade_band})")
        # ADULT BANDS GET A BOUNDED RESPONSE TOO (measured 2026-08-18).
        #
        # The scaffold above fires only for K-2 and 3-5. Driving real sessions
        # showed what that leaves: every adult session hit a 25-turn cap on a
        # SINGLE concept and never completed, because completion needs a streak
        # of grade >= 3 and a stalled learner never builds one. The tutor was
        # behaving correctly -- it declines to advance someone who has not
        # understood -- but nothing bounded the session, so a 50-minute lesson
        # never ends and the whole lesson-is-a-class-session model fails for the
        # learner who most needs it to hold.
        #
        # Two stages, escalating, and no warmth theatre: an adult who has missed
        # four times knows they are stuck, and being told "tricky things take
        # practice" reads as condescension. Change the EXPLANATION, then offer
        # the exit.
        elif getattr(self, "concept_miss_streak", 0) >= ADULT_EASE_AFTER:
            floor = self.course_bloom_floor or 1
            if self.current_bloom_level > floor:
                self.current_bloom_level -= 1
                self.bloom_correct_streak = 0
            if self.concept_miss_streak >= ADULT_OFFER_PARK_AFTER:
                affect_note = (
                    "PACING NOTE: This learner has missed several questions in a "
                    "row on this concept. Do NOT ask another question of the same "
                    "kind. Explain the idea a DIFFERENT way — a worked example, a "
                    "concrete case, or a different angle entirely — and then tell "
                    "them plainly that they can stay with this or move on and meet "
                    "it again later. Offer the choice without judgement.")
                self._offered_park = True
            else:
                affect_note = (
                    "PACING NOTE: The learner has missed this more than once. Do "
                    "not press harder. Approach the idea from a different "
                    "direction and make the next step smaller and more concrete. "
                    "No reassurance and no commentary on the misses — just a "
                    "clearer route in.")
            logging.info(f"Adult pacing scaffold engaged (miss_streak="
                         f"{self.concept_miss_streak}, band={self.grade_band})")

        if affect_note:
            system_note = f"{system_note}\n{affect_note}" if system_note else affect_note

        logging.info(f"Teaching Mode Selected: {teaching_mode}")
        self.send_status_update(f"Mode: {teaching_mode}...", progress=70)

        # B13.11: decide ONCE per turn whether a diagram belongs here. A `reuse`
        # verdict queues a course-built diagram immediately and never reaches
        # the model; only `generate` puts the aid grammar in the prompt.
        aid_decision = self._decide_visual_aid(teaching_mode)

        # Build clean history list
        for h in self.conversation_history:
            u_text = h[0] if h[0] is not None else ""
            a_text = h[1] if h[1] is not None else ""
            if u_text or a_text:
                structured_history.append((u_text, a_text))

        # FIX: If history is empty (first turn), inject a dummy user message
        if not structured_history:
            start_concept = self.current_lesson_node.get("title", "this topic")
            structured_history.append(
                (f"I'm ready to learn about {start_concept}.", "Great! let's begin.")
            )

        self.send_status_update("Formulating Response...", progress=80)

        self.user_profile = self._load_user_profile()

        # Get current question type
        _types = self._question_types()
        q_type_idx = min(self.socratic_type_index, len(_types) - 1)
        current_q_type = _types[q_type_idx]

        # Broadcast question type to UI
        self.send_status_update(
            f"QTYPE:{current_q_type['key']}:{q_type_idx}:{len(_types)}"
        )

        # GAP 6 kept, but as a SELECTION rather than a delete list. See
        # services/common/concept_doc.py: the old version stripped Core
        # Explanation, Key Facts and Real-World Examples from BOTH modes, so
        # the lecturer — whose only job is to explain — was handed hooks and
        # misconceptions and then told to fill in the gaps from its own
        # knowledge. The researched, depth-contracted, fact-checked substance
        # never reached the model that teaches from it.
        #
        # Now each mode gets what it needs on the same token budget: the
        # lecturer gets the explanation and the facts; the questioner gets the
        # same ground truth MINUS the worked example, which is the one section
        # that is genuinely a spoiler.
        redacted_context = build_tutor_context(
            self.current_context,
            "lecture" if teaching_mode == "LECTURE" else "socratic",
        )
        grounding = self._grounding_note()
        if grounding:
            redacted_context = f"{grounding}\n\n{redacted_context}"

        # Domain teaching guidance and any mined code pair for THIS concept.
        # (None, None) for non-CS courses.
        _domain_kind, _domain_pair = self._domain_teaching()
        if _domain_kind or _domain_pair:
            logging.info(f"[DOMAIN] kind={_domain_kind} "
                         f"pair={'yes' if _domain_pair else 'no'}")

        # Both ride the `figure_facts` slot, and both are constraints on the
        # turn, so they COMPOSE rather than one replacing the other: the pair
        # says what to show, the facts say what the tutor has already committed
        # to and must not contradict.
        _fig_facts = self._figure_facts_note()
        _extra = "\n\n".join(x for x in (_domain_pair, _fig_facts) if x) or None
        if _fig_facts:
            logging.info("[FIGURE] holding the tutor to its own figure values")

        # SELECT PROMPT BASED ON MODE
        if teaching_mode == "LECTURE":
            prompt = get_micro_lecture_prompt(
                self.current_lesson_node["title"],
                redacted_context,
                structured_history,
                style_modifier=self.current_teaching_style,
                missing_concepts=self.current_misconceptions or None,
                next_question_type=current_q_type["key"],
                bloom_level=self.current_bloom_level,
                prior_concepts=self.prior_concepts_summary,
                grade_band=self.grade_band,
                aid_policy=aid_decision,
            )
        else:
            prompt = get_typed_socratic_prompt(
                current_q_type["key"],
                redacted_context,
                structured_history,
                system_note=system_note,
                misconceptions=self.current_misconceptions,
                analogies=self.current_analogies,
                style_modifier=self.current_teaching_style,
                user_profile=self.user_profile,
                bloom_level=self.current_bloom_level,
                prior_concepts=self.prior_concepts_summary,
                grade_band=self.grade_band,
                health_strand6=self._current_concept_is_hd(),
                aid_policy=aid_decision,
                # A4.1b — what THIS learner has struggled with, from their own
                # past sessions. None when the record is too thin to mean
                # anything, which is a real answer: an invented struggle would
                # have the tutor open by correcting a mistake never made.
                learner_history=self._learner_history_note(),
                # A.2 — what has been ESTABLISHED this session, from graded
                # answers rather than re-derived from the transcript. Renders
                # to nothing until something has actually been graded.
                turn_state=self._get_turn_state(),
                # A.6 — the move, decided in code from state the system already
                # tracks. The model writes the turn; it does not choose the
                # pedagogy while writing it.
                teaching_move=None,   # A.6 reverted: measured -0.53 on adaptation
                # A.7 — what kind of learner this is right now, read from how
                # they write. A bluffer and a silent struggler earn the same
                # grade and need opposite turns.
                learner_behaviour=_describe_behaviour(
                    [p[0] for p in (self.conversation_history or []) if p[0]],
                    grades=[self._last_socratic_grade or 0]),
                # The domain layer, finally on the path a learner takes. Both
                # are None for non-CS courses, which leaves this turn exactly
                # as it was.
                concept_kind=_domain_kind,
                figure_facts=_extra,
            )
        # Tune max_tokens: lectures need more room for explanations, questions are shorter
        token_limit = 500 if teaching_mode == "LECTURE" else 400

        question = None
        for attempt in range(2):
            try:
                logging.info(f"LLM Socratic Request (attempt {attempt + 1}, mode={teaching_mode})")

                # Attempt streaming first for real-time token delivery to browser.
                # On streaming failure, fall back to the buffered chat() call.
                raw_text = None
                if attempt == 0:
                    try:
                        raw_text = self._call_llm_stream(prompt, max_tokens=token_limit, timeout=120)
                    except Exception as stream_err:
                        logging.warning(f"Streaming LLM failed, falling back to buffered: {stream_err}")
                        raw_text = None

                if not raw_text:
                    raw_text = self._call_llm(prompt, max_tokens=token_limit, timeout=60)

                if raw_text:
                    logging.info(f"LLM Socratic RAW Response: {raw_text[:300]}")
                    question = clean_llm_response(raw_text)
                    if not question:
                        logging.warning("clean_llm_response produced empty string, using basic clean")
                        question = raw_text.strip()
                        sents = re.split(r"(?<=[.?!])\s+", question)
                        sents = [s for s in sents if s.strip()]
                        question = " ".join(sents[:2]) if sents else ""
                    logging.info(f"LLM Socratic Question: {question[:200]}")
                    break
                else:
                    logging.error(f"LLM Socratic Error (attempt {attempt + 1})")
            except Exception as e:
                logging.error(f"LLM Socratic Exception (attempt {attempt + 1}): {e}")
            if attempt == 0:
                time.sleep(1)

        if not question:
            fallback_topic = self.current_lesson_node.get("title", "this topic")
            question = f"I'm having trouble formulating a specific question. Let's start with the basics of {fallback_topic}. What do you already know about it?"

        # Ensure every response ends with a question so the user always knows
        # what action to take. If the LLM forgot, append a type-appropriate question.
        if not question.rstrip().endswith("?"):
            topic_title = self.current_lesson_node.get("title", "this topic")
            fallback_questions = {
                "SCENARIO": f"Imagine you encountered {topic_title} in everyday life. What do you think would happen?",
                "MECHANISM": f"Why do you think {topic_title} works the way it does?",
                "CONTRAST": f"How would you distinguish {topic_title} from a closely related concept?",
                "APPLICATION": f"How would you apply {topic_title} to solve a real-world problem?",
                "EDGE_CASE": f"Can you think of a situation where {topic_title} might not work as expected?",
                "SYNTHESIS": f"How does {topic_title} connect to other things you have learned so far?",
            }
            fallback_q = fallback_questions.get(current_q_type["key"], fallback_questions["SCENARIO"])
            question = question.rstrip() + "\n\n" + fallback_q
            logging.info(f"[FLOW] Appended {current_q_type['key']} fallback question — LLM response did not end with '?'")

        # A4.1a — the dialogue contract. Checking is free; regeneration costs
        # ONE extra call and happens only when a rule actually trips, so the
        # common case is unchanged. An interactive turn is ~4.5s, so a second
        # call still lands well inside the budget.
        question = self._enforce_dialogue_contract(
            question, prompt, token_limit, context_trigger)

        self.last_question = question
        self._get_turn_state().ask(question)
        self.conversation_history.append((context_trigger, question))

        # FIX: The tutor must speak the question!
        self.speak(question)
        self.question_start_time = time.time()

    # NOTE: Dead duplicate _detect_ignorance removed during audit.
    # The canonical version is below (line ~1045).


    # ------------------------------------------------------------------ #
    # A4.1a / A4.1b — the dialogue contract and the learner's own record. #
    # ------------------------------------------------------------------ #
    def _concept_terms(self):
        """The subject's vocabulary, for the one-new-idea rule.

        Scoped to the current concept so ordinary English the tutor happens to
        use for the first time is not mistaken for a new technical idea.
        """
        node = self.current_lesson_node or {}
        terms = set()
        for key in ("title", "complexity_role"):
            for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", str(node.get(key) or "")):
                if len(w) > 3:
                    terms.add(w.lower())
        for obj in (node.get("learning_objectives") or []):
            for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", str(obj)):
                if len(w) > 3:
                    terms.add(w.lower())
        return terms

    def _seen_terms(self):
        """Every content word the dialogue has already used."""
        seen = set()
        for pair in (self.conversation_history or [])[-8:]:
            for part in pair:
                for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", str(part or "")):
                    seen.add(w.lower())
        return seen

    def _enforce_dialogue_contract(self, question, prompt, token_limit,
                                   learner_said=""):
        """Regenerate once against NAMED violations, and only if any trip.

        Returns the better of the two turns. A retry that fixes nothing is not
        shipped just because it is newer -- prompt-only enforcement measured
        0/5 in this repo, and an unchecked retry is prompt-only enforcement
        wearing a second call.
        """
        try:
            from services.common import dialogue_contract as dc
        except Exception:
            return question

        kw = {"learner_said": learner_said or "",
              "concept_terms": self._concept_terms(),
              "already_seen": self._seen_terms(),
              "is_opening": not (self.conversation_history or []),
              # A tutor may fairly refer to something said two turns ago, so
              # the grounded_claim rule looks back further than the last
              # message before calling an attribution invented.
              "recent_learner": [p[0] for p in
                                 (self.conversation_history or [])[-3:] if p[0]],
              # A.3 — every earlier tutor turn on this concept, so a turn that
              # merely re-asks one of them is refused. conversation_history
              # holds (student_text, tutor_text) pairs; the tutor half is [1].
              "previous_turns": [p[1] for p in
                                 (self.conversation_history or []) if p[1]],
              # A.8 — the grader named what the last answer left out. Saying
              # "Correct." and moving on is what a 2/5 adaptation turn does.
              "missing_concepts": list(
                  getattr(self, "_last_missing_concepts", []) or [])}
        try:
            violations = dc.check(question, **kw)
        except Exception as e:
            logging.warning(f"[CONTRACT] check failed, shipping as-is: {e}")
            return question
        if not violations:
            return question

        names = ", ".join(f"{v.rule}({v.detail})" for v in violations)
        logging.info(f"[CONTRACT] {len(violations)} violation(s): {names}")

        try:
            retry_prompt = list(prompt) + [
                {"role": "assistant", "content": question},
                {"role": "user", "content": dc.correction_note(violations)},
            ]
            raw = self._call_llm(retry_prompt, max_tokens=token_limit, timeout=60)
            candidate = clean_llm_response(raw) if raw else ""
            if candidate and dc.is_better(candidate, question, **kw):
                logging.info("[CONTRACT] regeneration accepted")
                return candidate
            logging.info("[CONTRACT] regeneration did not improve; keeping original")
        except Exception as e:
            logging.warning(f"[CONTRACT] regeneration failed: {e}")
        return question

    def _learner_history_note(self):
        """A4.1b — this learner's record for the concepts in play, or None."""
        try:
            from services.common import learner_history as lh
        except Exception:
            return None
        node = self.current_lesson_node or {}
        uid = node.get("uid")
        if not uid:
            return None
        titles = {uid: node.get("title") or ""}
        related = []
        for item in (self.syllabus_queue or [])[:8]:
            ruid = item.get("uid") if isinstance(item, dict) else None
            if ruid:
                related.append(ruid)
                titles[ruid] = (item.get("title") or "") if isinstance(item, dict) else ""
        return lh.for_concept(self.storage, uid,
                              course_uid=self.active_course_uid,
                              student_id=getattr(self, "student_id", None),
                              related_uids=related, titles=titles)

    def _detect_ignorance(self, text):
        """
        Detects if the user doesn't know the answer.
        """
        idk_phrases = [
            "i don't know",
            "idk",
            "dunno",
            "unsure",
            "not sure",
            "no idea",
            "what?",
            "huh",
            "help",
            "stuck",
            "give up",
            "tell me",
            "unknown",
            "no clue",
            "beats me",
            "pass",
            "skip",
            # Fix 7: Additional confusion/ignorance signals
            "i'm confused",
            "confused",
            "can you explain",
            "what does that mean",
            "what do you mean",
            "i have no idea",
            "i'm lost",
            "lost",
            "explain please",
            "explain it",
            "i need help",
            "don't understand",
            "don't get it",
        ]
        raw_lower = text.lower().strip()
        text_lower = raw_lower.strip(".,!?")

        # Whether a student is admitting ignorance is a property of the WHOLE
        # utterance, never of a substring inside it. Matching substrings meant
        # this fired on correct answers constantly, because the list contains
        # ordinary content words: "ions pass through the membrane" (pass),
        # "energy is lost as heat" (lost), "solve for the unknown" (unknown),
        # "the enzyme helps catalyse" (help), "skip connections" (skip). Every
        # one skipped the grader entirely and was hard-coded to grade 1, so a
        # right answer reset the streak and dropped the Bloom level. On real
        # STEM content this was not an edge case.
        if text_lower in idk_phrases:
            return True

        # Multi-word admissions are unambiguous enough to find inside a
        # sentence, but only a SHORT one. "I'm not sure, but I think the
        # derivative is 2x" is an attempt with a hedge on it, and grading it as
        # a refusal to answer would be worse than grading it wrong.
        words = text_lower.split()
        if len(words) <= 8:
            for phrase in idk_phrases:
                if " " not in phrase:
                    continue          # single words: exact utterance only, above
                if re.search(r"\b" + re.escape(phrase) + r"\b", text_lower):
                    return True

        # Short question check (e.g. "What?", "Huh?")
        if len(text_lower) < 5 and "?" in raw_lower:
            return True

        return False

    def _parse_grade_response(self, content):
        """Parse a grading LLM response into {grade, missing_concepts, feedback,
        reason}. Tolerant of code fences, stray prose, and "Grade N" strings. On
        None or unparseable content, grade defaults to 2 (partial) — never a
        passing grade, so a grading failure can't silently credit mastery (B3.3)."""
        # `graded` marks this as a real assessment. The grade-2 default is the
        # right fail-safe -- it never credits mastery -- but it is currently
        # INDISTINGUISHABLE downstream from a learner who genuinely earned a 2.
        # FSRS scheduling, mastery gates and (under the programme design) a
        # course pass all consume these, and a grade fabricated during an LLM
        # outage is data about the infrastructure, not about the learner.
        fail = {"grade": 2, "missing_concepts": [], "feedback": "", "reason": "",
                "graded": False, "grade_source": "fallback"}
        if not content:
            logging.error("LLM Grading returned None")
            return fail
        try:
            text = content
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            # Extraction must respect nesting. The old r"\{[^{}]*\}" regex
            # cannot match a brace inside a brace, so a perfectly valid
            # response like {"grade":1,"detail":{"objective":"x"}} had its
            # INNER object extracted -- no grade key -- and the old default
            # then turned a scored 1 into a 3. raw_decode from the first "{"
            # parses a complete object however deep it nests.
            text = re.sub(r'"grade":\s*"Grade\s*(\d)"', r'"grade": \1', text)
            try:
                result = json.loads(text)
            except Exception:
                result = None
                start = text.find("{")
                if start != -1:
                    try:
                        result, _ = json.JSONDecoder().raw_decode(text[start:])
                    except Exception:
                        result = None
                if not isinstance(result, dict):
                    grade_match = re.search(r'"grade":\s*(\d)', text)
                    if grade_match:
                        result = {"grade": int(grade_match.group(1))}
                    else:
                        any_grade = re.search(r'\bgrade\b.*?(\d)', text, re.IGNORECASE)
                        if any_grade:
                            result = {"grade": int(any_grade.group(1))}
                        else:
                            raise ValueError("no grade found in response")

            if not isinstance(result, dict) or "grade" not in result:
                # A response with no verdict is not an assessment. The old
                # default here was 3 -- a PASSING grade invented out of
                # silence, contradicting the fail-safe this docstring
                # promises. Fall back exactly like unparseable content.
                logging.warning("Grading response parsed but carried no grade")
                return fail
            # Clamp: a model that answers 7, 0 or -1 is out of contract, and
            # letting it through would move the mastery gate arbitrarily.
            grade_val = max(1, min(5, int(result["grade"])))
            return {"graded": True, "grade_source": "llm", "grade": grade_val,
                "missing_concepts": result.get("missing_concepts", []),
                "feedback": result.get("feedback", ""),
                "reason": result.get("reason", ""),
            }
        except Exception as e:
            logging.warning(f"Grading parse error: {e}")
            return fail

    def handle_socratic_answer(self, text, image=None):
        latency = time.time() - self.question_start_time
        self.question_start_time = 0

        # BUG-2 FIX: Record student answer in conversation history for LLM context
        self.conversation_history.append((text, None))
        if len(self.conversation_history) > 20:
            self.conversation_history = self.conversation_history[-20:]

        # Initialize feedback for all paths (ignorance bypass won't have LLM feedback)
        feedback = ""
        missing_concepts = []

        # Check for ignorance first (Subjective: "I don't know")
        if self._detect_ignorance(text):
            logging.info("Ignorance detected: Bypassing grader (Grade 1)")
            grade = 1
            result = {"grade": 1, "reason": "User admitted ignorance."}
        else:
            # Socratic Grading
            self.send_status_update("Grading...")
            # B14.8: optional objective tool-check (math/units/stats/facts). No-op
            # unless HELGA_ENABLE_TUTOR_TOOLS=true; grounds the grade with a
            # deterministic computation rather than the model's arithmetic alone.
            tool_note = self._verify_answer_objectively(self.last_question, text)
            # Grading judges one answer against one standard. It was being
            # handed `self.current_context` — the whole concept document, up to
            # the 10,000-char slice taken when the concept loaded — as "Source
            # Truth Context", which measured at ~2,780 prefill tokens on EVERY
            # student answer to produce a ~90-token verdict. Socratic hooks and
            # analogies cannot change a grade.
            grading_context = build_tutor_context(self.current_context, "grading")
            if tool_note:
                grading_context = f"{grading_context}\n\n{tool_note}"
                logging.info(f"Objective tool-check appended to grading context: {tool_note[:120]}")
            # GAP 5: Pass Bloom level, objectives, and mastery criteria to grading
            prompt = get_socratic_grading_prompt(
                self.current_lesson_node["title"],
                self.last_question,
                text,
                context_text=grading_context,
                bloom_level=self.current_bloom_level,
                learning_objectives=self.current_lesson_node.get("learning_objectives", []),
                mastery_criteria=getattr(self, 'current_mastery_criteria', ''),
                grade_band=self.grade_band,
            )
            logging.info(f"LLM Grading Request for: {self.current_lesson_node['title']}")
            # Grammar-constrain the grade to valid JSON (Ollama >= 0.5); the
            # tolerant parser below is the fallback for older Ollama. A grading
            # failure resolves to grade 2 (partial), never a passing grade (B3.3).
            content = self._call_llm(prompt, max_tokens=500, timeout=45,
                                     json_schema=GRADE_JSON_SCHEMA,
                                     images=[image] if image else None)
            result = self._parse_grade_response(content)
            grade = result["grade"]
            missing_concepts = result["missing_concepts"]
            feedback = result["feedback"]

            # Log to Session Notes only when the grader actually responded.
            if content and self.current_lesson_node:
                self.append_session_note(
                    self.current_lesson_node["uid"],
                    f"Question: {self.last_question} | Answer: {text[:50]}... | Grade: {grade} | Reasoning: {result.get('reason', 'N/A')}",
                )

        # A.8 — remember what the grader said was missing, so the contract can
        # require the next turn to engage with it.
        try:
            self._last_missing_concepts = list(missing_concepts or [])
        except Exception:
            self._last_missing_concepts = []

        # A.2 — fold the grader's own verdict into the structured state the
        # next tutor turn will read. `record` ignores a fallback grade, so an
        # LLM outage cannot invent a history of half-understanding.
        self._get_turn_state().record(text, result)

        # Store grade for rule-based mode selection (replaces LLM classifier)
        self._last_socratic_grade = grade

        # Multi-question mastery tracking
        self.concept_question_count += 1

        # Evaluated on EVERY answer, deliberately. A first attempt nested this
        # behind the question-type-cycle branch and it never fired: a real
        # session hit 25 turns without that branch being reached, which is the
        # same unbounded session the cap exists to prevent.
        if self._should_park_concept():
            self.speak(
                "We've spent a while on this one. Let's move on and come back to "
                "it later — it'll return in your review queue, which is usually a "
                "better way to make something stick than pushing through now.")
            self._advance_without_completing()
            return
        if grade >= 3:
            self.concept_correct_streak += 1
            self.concept_miss_streak = 0
            # GAP 4: Track which question type categories were passed
            _types = self._question_types()
            q_idx = min(self.socratic_type_index, len(_types) - 1)
            self.passed_question_types.add(_types[q_idx]["key"])
        else:
            self.concept_correct_streak = 0
            self.concept_miss_streak += 1

        # Bloom's Taxonomy Level Progression
        # Grade 3+ (correct): increment bloom_correct_streak; after 2 consecutive, advance level (max 6)
        # Grade <= 1 (wrong): drop bloom_level by 1 (min 1), reset streak
        # Grade == 2 (partial): stay at current level, reset streak
        BLOOM_LABELS = {1: "Remember", 2: "Understand", 3: "Apply", 4: "Analyze", 5: "Evaluate", 6: "Create"}
        if grade >= 3:
            self.bloom_correct_streak += 1
            # GAP 1: Cap Bloom advancement at course ceiling
            effective_ceiling = self.course_bloom_ceiling or 6
            if self.bloom_correct_streak >= 2 and self.current_bloom_level < effective_ceiling:
                self.current_bloom_level += 1
                self.bloom_correct_streak = 0
                logging.info(
                    f"Bloom level advanced to {self.current_bloom_level} "
                    f"({BLOOM_LABELS.get(self.current_bloom_level, '?')})"
                )
        elif grade <= 1:
            if self.current_bloom_level > 1:
                self.current_bloom_level -= 1
                logging.info(
                    f"Bloom level dropped to {self.current_bloom_level} "
                    f"({BLOOM_LABELS.get(self.current_bloom_level, '?')})"
                )
            self.bloom_correct_streak = 0
        else:
            # Grade == 2: stay at current level
            self.bloom_correct_streak = 0

        # Decision Matrix with Question Type Progression
        #
        # FLOW DESIGN: Each grade path produces exactly ONE speak() call
        # (either via ask_socratic_question or a direct speak). This prevents
        # overlapping TTS and ensures the user always sees one coherent message.

        if grade <= 1:
            self.socratic_retry_count += 1
            if self.socratic_retry_count >= 3:
                # Fix 2+3: Micro-lecture + verification on SAME type (don't advance)
                self.socratic_retry_count = 0
                _t = self._question_types()
                current_type_name = _t[
                    min(self.socratic_type_index, len(_t) - 1)
                ]["name"].lower()
                logging.info(f"Retry limit reached (grade 1): micro-lecture + verification on {current_type_name}")
                self.ask_socratic_question(
                    f"[SYSTEM NOTE: Student failed 3 times on this question type. "
                    f"Give a clear, simple explanation of the concept (2-3 sentences), "
                    f"then ask a SIMPLER verification question — still a {current_type_name} question — "
                    f"to check they understood your explanation.]",
                    initial_mode="LECTURE",
                )
            else:
                self.play_sound("FRICTION_GRIND")
                feedback_prefix = f"[SYSTEM NOTE: Student's answer was incorrect. Their feedback: '{feedback}'. " if feedback else "[SYSTEM NOTE: Student answered incorrectly. "
                self.ask_socratic_question(
                    feedback_prefix + "Briefly acknowledge what went wrong, then re-ask a simpler version of the same question type to guide them.]"
                )

        elif grade == 2:
            self.socratic_retry_count += 1
            if self.socratic_retry_count >= 3:
                # Fix 2+3: Clarify + verification on SAME type (don't advance)
                self.socratic_retry_count = 0
                _t = self._question_types()
                current_type_name = _t[
                    min(self.socratic_type_index, len(_t) - 1)
                ]["name"].lower()
                logging.info(f"Retry limit reached (grade 2): clarify + verification on {current_type_name}")
                self.ask_socratic_question(
                    f"[SYSTEM NOTE: Student struggled 3 times with partial answers on this {current_type_name} question. "
                    f"Briefly clarify the key point they kept missing, "
                    f"then ask a simplified version of the same {current_type_name} question focusing on what they missed.]",
                    initial_mode="LECTURE",
                )
            else:
                self.play_sound("FRICTION_GRIND")
                if missing_concepts:
                    concepts_str = ", ".join(missing_concepts)
                    self.ask_socratic_question(
                        f"[SYSTEM NOTE: Student's answer was partially correct but missed: {concepts_str}. "
                        f"Briefly acknowledge what they got right, point out what's missing, "
                        f"then ask a more specific version of the same question type to guide them.]"
                    )
                else:
                    self.ask_socratic_question(
                        "[SYSTEM NOTE: Student gave a vague or partial answer. "
                        "Briefly acknowledge their attempt, then ask a more targeted version "
                        "of the same question type that narrows the focus.]"
                    )

        elif grade >= 4:
            self.play_sound("SUCCESS_CHORD")
            self.socratic_retry_count = 0
            # Fix 4: Advance +1, not +2. Student must prove each type.
            # Fix 9: Capture previous type name for transition bridge
            _t = self._question_types()
            prev_type_name = _t[
                min(self.socratic_type_index, len(_t) - 1)
            ]["name"].lower()
            self.socratic_type_index += 1
            if self.socratic_type_index >= len(self._question_types()):
                if self._check_mastery_gate():
                    completion_msg = (feedback or "Excellent work.") + " You've mastered this concept. Let's move on to the next one."
                    self.speak(completion_msg)
                    if self.transcript and self.transcript[-1].get("sender") == "helga":
                        self.transcript[-1]["grade"] = grade
                    if self.current_lesson_node and self.current_lesson_node.get("uid"):
                        self.completed_topics.add(self.current_lesson_node["uid"])
                    self.next_syllabus_item()
                else:
                    # Fix 6: Continue from next unpassed type, not reset to 0
                    self.socratic_type_index = self._next_unpassed_type_index()
                    self.ask_socratic_question(
                        "[SYSTEM NOTE: Student is doing well but needs a bit more practice. "
                        "Ask another question to confirm their understanding.]"
                    )
            else:
                # Fix 9: Transition bridge references what student demonstrated
                next_type = self._question_types()[self.socratic_type_index]
                feedback_note = f"[SYSTEM NOTE: Student gave an excellent answer. Their feedback: '{feedback}'. " if feedback else "[SYSTEM NOTE: Student answered excellently. "
                self.ask_socratic_question(
                    feedback_note + f"Briefly affirm what they demonstrated about {prev_type_name} — specifically what they got right. Then build on that by asking a {next_type['name'].lower()} question that extends their reasoning.]"
                )

        else:  # Grade == 3 (Good)
            self.play_sound("SUCCESS_CHORD")
            self.socratic_retry_count = 0
            # Fix 9: Capture previous type name for transition bridge
            _t = self._question_types()
            prev_type_name = _t[
                min(self.socratic_type_index, len(_t) - 1)
            ]["name"].lower()
            self.socratic_type_index += 1
            if self.socratic_type_index >= len(self._question_types()):
                if self._check_mastery_gate():
                    completion_msg = (feedback or "Well done.") + " You've demonstrated solid understanding of this concept. Let's move on."
                    self.speak(completion_msg)
                    if self.transcript and self.transcript[-1].get("sender") == "helga":
                        self.transcript[-1]["grade"] = grade
                    if self.current_lesson_node and self.current_lesson_node.get("uid"):
                        self.completed_topics.add(self.current_lesson_node["uid"])
                    self.next_syllabus_item()
                else:
                    # Fix 6: Continue from next unpassed type, not reset to 0
                    self.socratic_type_index = self._next_unpassed_type_index()
                    self.ask_socratic_question(
                        "[SYSTEM NOTE: Student is doing well but needs a bit more practice. "
                        "Ask another question to confirm their understanding.]"
                    )
            else:
                # Fix 9: Transition bridge references what student demonstrated
                next_type = self._question_types()[self.socratic_type_index]
                feedback_note = f"[SYSTEM NOTE: Student answered correctly. Their feedback: '{feedback}'. " if feedback else "[SYSTEM NOTE: Student answered correctly. "
                self.ask_socratic_question(
                    feedback_note + f"Briefly affirm what they demonstrated about {prev_type_name} — specifically what they got right. Then build on that by asking a {next_type['name'].lower()} question that extends their reasoning.]"
                )

        # Tag the most recent tutor response with the grade for badge display.
        # Every grade branch above guarantees exactly one speak()/ask_socratic_question()
        # call, so transcript[-1] is the grading/feedback message we want to badge.
        if self.transcript and self.transcript[-1].get("sender") in ("helga", "ai"):
            self.transcript[-1]["grade"] = grade

        # Award XP via gamification API (fire-and-forget). Only grade >= 3 earns XP;
        # the API enforces this, but short-circuit here to avoid a pointless HTTP call.
        if grade >= 3:
            try:
                import threading as _t
                def _award_xp_async():
                    try:
                        import requests as _req
                        _req.post(
                            f"{self.rag_url}/api/gamification/award_xp",
                            json={
                                "grade": int(grade),
                                "bloom_level": int(self.current_bloom_level),
                                "action": "answer",
                                "first_try": int(self.socratic_retry_count) == 0,
                            },
                            timeout=5,
                        )
                    except Exception as _e:
                        logging.debug(f"award_xp call failed: {_e}")
                _t.Thread(target=_award_xp_async, daemon=True).start()
            except Exception as _e:
                logging.debug(f"award_xp thread start failed: {_e}")

        # Also schedule FSRS review on each answer (not just concept completion)
        # so that partial progress still creates review cards.
        #
        # -- unless nothing was actually assessed. graded=False marks the
        # grade-2 outage fallback: data about the infrastructure, not the
        # learner, and the one consumer that must not eat it is the scheduler,
        # which would otherwise record a review that never happened and move
        # the interval off a fabricated rating.
        try:
            if (result.get("graded", True)
                    and self.current_lesson_node and self.current_lesson_node.get("uid")):
                self.storage.schedule.schedule_concept_review(
                    self.active_course_uid or "unknown",
                    self.current_lesson_node["uid"],
                    self.current_lesson_node.get("title", ""),
                    rating=int(grade),
                )
        except Exception as _e:
            logging.debug(f"FSRS per-answer schedule failed: {_e}")

    # --- MODE 2: SPACED REPETITION ---
    def enter_mode_2(self, text):
        self.state = "SPACED_REPETITION"
        self.play_sound("MODE_SWITCH_CLICK")
        topic = text.replace("review", "").strip()

        if topic:
            self.speak(f"Reviewing {topic}.")
        else:
            self.speak("Starting daily review.")

        try:
            # Get due reviews from local storage
            due_reviews = self.storage.progress.get_due_reviews()
            self.review_queue = []
            for review in due_reviews:
                concept_uid = review.get("concept_uid", "")
                details = self.get_concept_details(concept_uid)
                if details:
                    self.review_queue.append(
                        {
                            "uid": concept_uid,
                            "title": details.get("title", "Unknown"),
                            "front": details.get("title", "Unknown"),
                            "back": (details.get("text", "") or "")[:200],
                        }
                    )
            if not self.review_queue:
                self.speak("No cards due for review right now. Study a course first to schedule reviews. Say 'open' followed by a course name, or 'list courses' to see your options.")
                self.state = "LOBBY"
                return
            self.next_card()
        except requests.exceptions.RequestException as e:
            logging.error(
                f"Failed to retrieve due cards from RAG service: {e}", exc_info=True
            )
            self.speak("Database error in review.")
            self.state = "LOBBY"
        except Exception as e:
            logging.error(
                f"An unexpected error occurred in enter_mode_2: {e}", exc_info=True
            )
            self.speak("An unexpected error occurred while starting the review.")
            self.state = "LOBBY"

    def next_card(self):
        if not self.review_queue:
            self.play_sound("SUCCESS_CHORD")
            self.speak("Daily review complete! You can say 'open' followed by a course name to continue studying, or 'enter palace' to explore the memory palace.")
            self.state = "LOBBY"
            return

        self.current_card = self.review_queue.pop(0)
        self.card_attempts = 0

        self.send_status_update("Generating Question...")
        # Cards carry front/back (enter_mode_2 builds them that way); "text"
        # was a key that never existed, so questions were being generated from
        # an empty string — the model inventing an exam from nothing and then
        # confidently grading against nothing.
        card_content = self.current_card.get("back", "") or self.current_card.get("front", "")
        messages = get_examiner_question_prompt(card_content)
        question = f"What can you tell me about {self.current_card['title']}?"
        try:
            raw = self._call_llm(messages, max_tokens=500, timeout=45)
            if raw:
                question = clean_llm_response(raw) or question
                logging.info(f"LLM Examiner Question: {question[:100]}...")
        except Exception as e:
            logging.error(f"LLM Examiner Exception: {e}")
            question = f"Describe {self.current_card['title']}."
        # The grader must see the question that was actually asked. This was
        # never set here, so review answers were graded against whatever
        # Socratic question the student last saw — or "" on a fresh process.
        self.last_question = question
        self.speak(question)
        self.question_start_time = time.time()

    def handle_flashcard_answer(self, text):
        response_time = time.time() - self.question_start_time
        self.question_start_time = 0

        self.send_status_update("Grading...")
        messages = get_examiner_grade_prompt(
            self.last_question, text,
            self.current_card.get("back", "") or self.current_card.get("front", "")
        )
        graded = False
        is_correct = False
        try:
            logging.info("LLM Flashcard Grading Request")
            content = self._call_llm(messages, max_tokens=500, timeout=45)
            if content:
                logging.info(f"LLM Flashcard Grading Content: {content[:200]}")
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()
                json_match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
                if json_match:
                    content = json_match.group(0)
                try:
                    result = json.loads(content)
                except Exception as e:
                    grade_match = re.search(r'"grade":\s*"?(PASS|FAIL)"?', content, re.IGNORECASE)
                    if grade_match:
                        result = {"grade": grade_match.group(1)}
                    else:
                        raise e
                verdict = str(result.get("grade", "")).upper()
                if verdict in ("PASS", "FAIL"):
                    graded = True
                    is_correct = verdict == "PASS"
        except Exception as e:
            logging.error(f"LLM Flashcard Grading Exception: {e}")

        # An outage is not an assessment. This used to set is_correct = True,
        # so a dead model marked every review answer correct, said "Correct.",
        # and advanced the FSRS schedule — the mirror image of the quiz bug
        # where an outage counted as FAIL and reset cards to "Again". Both
        # directions corrupt the one thing this mode exists to maintain.
        # Keep the card, tell the student what actually happened, touch nothing.
        if not graded:
            self.review_queue.insert(0, self.current_card)
            self.speak(
                "I could not grade that answer — the model did not respond. "
                "Nothing was recorded against this card. Say resume review to "
                "try it again."
            )
            self.state = "LOBBY"
            return

        if is_correct:
            self.play_sound("SUCCESS_CHORD")
            # Calculate FSRS grade (Mock interaction)
            # 2=Hard, 3=Good, 4=Easy
            grade = 3
            if response_time < 3:
                grade = 4
            elif response_time > 10:
                grade = 2

            try:
                self.storage.progress.update_progress(
                    self.current_card["uid"],
                    course_uid=self.active_course_uid or "",
                    grade=grade,
                    status="reviewed",
                )
            except Exception as e:
                logging.warning(f"Failed to update flashcard progress: {e}")

            self.speak("Correct.")
            self.next_card()
        else:
            self.card_attempts += 1
            if self.card_attempts < 2:
                self.play_sound("FRICTION_GRIND")
                self.send_status_update("Generating Hint...")
                messages = get_hint_prompt(
                    self.current_card["title"],
                    self.current_card.get("text", ""),
                    self.card_attempts,
                    grade_band=self.grade_band,
                )
                try:
                    raw = self._call_llm(messages, max_tokens=200, timeout=45)
                    hint = clean_llm_response(raw) if raw else "Think about its primary function."
                    if not hint:
                        hint = "Think about its primary function."
                    self.speak(hint)
                    self.question_start_time = time.time()
                except Exception as e:
                    logging.error(f"LLM Hint Exception: {e}")
                    self.speak("Try again.")
            else:
                self.play_sound("FRICTION_GRIND")
                self.speak(
                    f"The answer was related to {self.current_card['title']}. We will review this again soon."
                )
                self.next_card()

    # --- MODE 3: MIND PALACE ---
    def enter_mode_3(self, text):
        self.state = "MEMORY_PALACE"
        self.play_sound("MODE_SWITCH_CLICK")

        try:
            # Memory Palace: iterate through course concepts as "loci"
            if self.active_course_uid:
                concepts = self.storage.courses.get_flat_concepts(
                    self.active_course_uid
                )
                if concepts:
                    self._palace_concepts = concepts
                    self._palace_index = 0
                    first = concepts[0]
                    self.current_locus_uid = first["uid"]
                    self.current_locus_desc = first["title"]
                    self.speak(
                        f"Entering Memory Palace. Current location: {self.current_locus_desc}"
                    )
                    self.check_sonar({"has_concept": True})
                    return
            self.speak("Memory Palace requires an active course. Say 'open' followed by a course name to start one first, then try entering the palace again.")
            self.state = "LOBBY"
        except Exception as e:
            logging.error(f"Memory Palace error: {e}", exc_info=True)
            self.speak("Memory Palace unavailable.")
            self.state = "LOBBY"

    def move_locus(self):
        self.play_sound("FOOTSTEPS")
        try:
            if not hasattr(self, "_palace_concepts") or not self._palace_concepts:
                self.speak("You have reached the end of this path.")
                return
            self._palace_index = (self._palace_index + 1) % len(self._palace_concepts)
            concept = self._palace_concepts[self._palace_index]
            self.current_locus_uid = concept["uid"]
            self.current_locus_desc = concept["title"]
            self.speak(f"You are now at {self.current_locus_desc}")
            self.check_sonar({"has_concept": True})
        except Exception as e:
            logging.error(f"move_locus error: {e}", exc_info=True)
            self.speak("An unexpected error occurred while navigating.")

    def check_sonar(self, data):
        if not data.get("has_concept"):
            self.play_sound("HOLLOW_WIND")  # Empty locus
        elif data.get("concept_stability", 5.0) < 3.0:
            self.play_sound("STATIC_HISS")  # Fading concept

    def inspect_anchor(self):
        self.play_sound("FOCUS_ZOOM")
        # Check for previously anchored concept at this locus
        try:
            activities = self.storage.activity.get_activities(
                course_uid=self.active_course_uid,
                activity_type="palace_anchor",
            )
            # get_activities is newest-first, so take the FIRST match. This
            # iterated reversed(activities) and so spoke the oldest anchor ever
            # placed here — the learner heard their discarded first image back,
            # not the one they replaced it with. Same defect as librarian's
            # _get_anchor_for_locus; both halves of the palace had it.
            for act in activities:
                details = act.get("details", {})
                if isinstance(details, str):
                    import json as _json
                    details = _json.loads(details)
                if details.get("locus_uid") == self.current_locus_uid:
                    concept = details.get("concept_text", "")
                    sensory = details.get("sensory_text", "")
                    self.speak(
                        f"At {self.current_locus_desc}, you anchored: {concept}. "
                        f"Your visualization: {sensory}"
                    )
                    return
        except Exception as e:
            logging.warning(f"Anchor lookup failed: {e}")
        self.speak(
            f"Observing: {self.current_locus_desc}. No concept anchored here yet. Say 'place' to anchor one."
        )

    def place_concept(self, text):
        concept = (
            text.replace("place", "").replace("concept", "").replace("here", "").strip()
        )
        if not concept:
            self.speak("What concept should I place here?")
            return

        self.temp_anchor_concept = concept
        self.send_status_update("Generating Question...")
        messages = get_vividness_prompt(concept, self.current_locus_desc)
        try:
            raw = self._call_llm(messages, max_tokens=200, timeout=45)
            if raw:
                question = clean_llm_response(raw)
            else:
                question = f"How do you visualize {concept} in this {self.current_locus_desc}?"
            self.speak(question)
        except Exception as e:
            logging.error(f"LLM Vividness Exception: {e}")
            self.speak(f"Describe the sensory connection for {concept} here.")

    def handle_vividness_response(self, text):
        try:
            # Store anchor as activity log entry
            self.storage.activity.log_activity(
                course_uid=self.active_course_uid or "",
                activity_type="palace_anchor",
                concept_uid=self.current_locus_uid,
                details={
                    "concept_text": self.temp_anchor_concept,
                    "sensory_text": text,
                    "locus_uid": self.current_locus_uid,
                },
            )
            self.play_sound("VAULT_LOCK")
            self.speak("concept anchored to locus.")
        except Exception as e:
            logging.error(f"handle_vividness_response error: {e}", exc_info=True)
            self.speak("Failed to save anchor.")

        self.temp_anchor_concept = None

    # --- INTERACTIVE COURSE DESIGNER STATES ---

    def handle_drafting_input(self, text):
        """Handle user input during the DRAFTING_COURSE state.
        User can say 'check my work' for gap analysis, 'finish' to proceed,
        'add <module>' to add a module, or 'remove <module>' to remove one.
        """
        logging.info(
            f"[DRAFTING] Input: '{text}' | Modules: {len(self.draft_course_structure or [])}"
        )
        text_lower = text.lower().strip()

        if not self.draft_course_structure:
            self.speak("No draft in progress. Say 'create course' to start.")
            self.state = "LOBBY"
            return

        if "check" in text_lower or "gap" in text_lower or "missing" in text_lower:
            # Trigger Gap Analysis
            self.enter_gap_analysis()
        elif "finish" in text_lower or "done" in text_lower or "commit" in text_lower:
            # Proceed to teaching style selection
            self.state = "TEACHING_STYLE_SELECT"
            self.speak(
                "Course structure confirmed. What teaching style should I use? Options: 'Explain it like I'm five', 'Strict academic drill', 'Use lots of analogies', or describe your own."
            )
        elif "add" in text_lower:
            module_name = (
                text_lower.replace("add module", "").replace("add", "").strip()
            )
            if module_name:
                new_module = {
                    "title": module_name.title(),
                    "description": f"Module on {module_name}",
                }
                self.draft_course_structure.append(new_module)
                self.speak(
                    f"Added module '{module_name.title()}'. You now have {len(self.draft_course_structure)} modules. Say 'finish' when ready or 'check my work' for gap analysis."
                )
            else:
                self.speak("What module should I add?")
        elif "remove" in text_lower:
            module_name = (
                text_lower.replace("remove module", "").replace("remove", "").strip()
            )
            if module_name:
                before_count = len(self.draft_course_structure)
                self.draft_course_structure = [
                    m
                    for m in self.draft_course_structure
                    if module_name.lower() not in m.get("title", "").lower()
                ]
                removed = before_count - len(self.draft_course_structure)
                if removed > 0:
                    self.speak(
                        f"Removed {removed} module(s). {len(self.draft_course_structure)} modules remaining."
                    )
                else:
                    self.speak(f"No module matching '{module_name}' found.")
            else:
                self.speak("Which module should I remove?")
        elif (
            "quiz" in text_lower
            or "pre-assess" in text_lower
            or "test me" in text_lower
        ):
            # Trigger Pre-Assessment
            self.enter_pre_assessment()
        else:
            # List current modules
            if self.draft_course_structure:
                module_list = ", ".join(
                    [m.get("title", "?") for m in self.draft_course_structure]
                )
                self.speak(
                    f"Current modules: {module_list}. Say 'add', 'remove', 'check my work', 'test me', or 'finish'."
                )
            else:
                self.speak("No modules yet. Say 'add <module name>' to add one.")

    def enter_gap_analysis(self):
        """AI Structural Audit - analyze the draft for missing topics."""
        logging.info(
            f"[GAP_ANALYSIS] Starting for topic '{self.draft_course_topic}' with {len(self.draft_course_structure or [])} modules"
        )
        self.state = "GAP_ANALYSIS"
        self.send_status_update("Running Gap Analysis...")

        module_titles = [
            m.get("title", "") for m in (self.draft_course_structure or [])
        ]
        syllabus_json = json.dumps(self.draft_course_structure or [], indent=2)

        prompt = (
            f"Analyze this syllabus for a course on '{self.draft_course_topic}':\n"
            f"{syllabus_json}\n\n"
            f"Identify 2-3 critical missing topics that would make this course comprehensive. "
            f'Return a JSON array of objects: [{{"title": "...", "reason": "..."}}]'
        )

        try:
            from services.core.course_builder import llm_generate, extract_python_list

            raw = llm_generate(
                prompt,
                sys_prompt="Expert curriculum consultant. Identify gaps in course syllabi. Output JSON only.",
                max_tokens=800,
            )
            suggestions = extract_python_list(raw)

            if suggestions:
                self.gap_suggestions = suggestions
                suggestion_text = ". ".join(
                    [
                        f"'{s.get('title', '?')}' because {s.get('reason', 'it is important')}"
                        for s in suggestions[:3]
                    ]
                )
                self.speak(
                    f"Gap Analysis complete. I suggest adding: {suggestion_text}. Say 'add' followed by a topic name, or 'skip' to continue."
                )
            else:
                self.speak(
                    "Your syllabus looks comprehensive. Say 'finish' to proceed or continue editing."
                )
                self.state = "DRAFTING_COURSE"
        except Exception as e:
            logging.error(f"Gap analysis failed: {e}")
            self.speak("I couldn't complete the analysis. Say 'finish' to proceed.")
            self.state = "DRAFTING_COURSE"

    def handle_gap_analysis_input(self, text):
        """Handle user response to gap analysis suggestions."""
        text_lower = text.lower().strip()

        if "skip" in text_lower or "no" in text_lower or "finish" in text_lower:
            self.state = "DRAFTING_COURSE"
            self.speak("Okay, returning to draft. Say 'finish' when ready.")
            return

        if "add" in text_lower:
            # Try to match against suggestions
            added = False
            for suggestion in getattr(self, "gap_suggestions", []):
                title = suggestion.get("title", "")
                if (
                    title.lower() in text_lower
                    or text_lower.replace("add", "").strip() in title.lower()
                ):
                    new_module = {
                        "title": title,
                        "description": suggestion.get("reason", ""),
                    }
                    self.draft_course_structure.append(new_module)
                    self.speak(
                        f"Added '{title}'. Say 'add' for more or 'finish' to proceed."
                    )
                    added = True
                    break

            if not added:
                # Add custom module
                module_name = text_lower.replace("add", "").strip()
                if module_name:
                    self.draft_course_structure.append(
                        {"title": module_name.title(), "description": ""}
                    )
                    self.speak(f"Added '{module_name.title()}'.")
        elif "all" in text_lower or "yes" in text_lower:
            # Add all suggestions
            for suggestion in getattr(self, "gap_suggestions", []):
                self.draft_course_structure.append(
                    {
                        "title": suggestion.get("title", ""),
                        "description": suggestion.get("reason", ""),
                    }
                )
            count = len(getattr(self, "gap_suggestions", []))
            self.speak(f"Added all {count} suggested modules. Say 'finish' to proceed.")

        self.state = "DRAFTING_COURSE"

    def enter_pre_assessment(self):
        """Smart Pre-Assessment - quiz the user to determine module depths."""
        logging.info(f"[PRE_ASSESSMENT] Starting for topic '{self.draft_course_topic}'")
        self.state = "PRE_ASSESSMENT"
        self.pre_assessment_answers = {}
        self.send_status_update("Generating Pre-Assessment Quiz...")

        module_titles = [
            m.get("title", "") for m in (self.draft_course_structure or [])
        ]

        prompt = (
            f"Generate 3-5 broad diagnostic questions for a course on '{self.draft_course_topic}' "
            f"covering these modules: {', '.join(module_titles)}. "
            f"Each question should test basic knowledge of one module. "
            f'Return a JSON array: [{{"question": "...", "module": "...", "expected_keyword": "..."}}]'
        )

        try:
            from services.core.course_builder import llm_generate, extract_python_list

            raw = llm_generate(
                prompt,
                sys_prompt="Expert assessment designer. Create diagnostic questions. Output JSON only.",
                max_tokens=1000,
            )
            questions = extract_python_list(raw)

            if questions:
                self.pre_assessment_questions = questions
                self.pre_assessment_index = 0
                self.speak(
                    "Let me see where you stand. I'll ask a few quick questions."
                )
                self._ask_next_pre_assessment()
            else:
                self.speak(
                    "I couldn't generate assessment questions. Proceeding with default depth."
                )
                self.state = "DRAFTING_COURSE"
        except Exception as e:
            logging.error(f"Pre-assessment generation failed: {e}")
            self.speak("Assessment generation failed. Proceeding with default depth.")
            self.state = "DRAFTING_COURSE"

    def _ask_next_pre_assessment(self):
        """Ask the next pre-assessment question."""
        if self.pre_assessment_index >= len(self.pre_assessment_questions):
            # All questions answered - compute module depths
            self._compute_module_depths()
            return

        q = self.pre_assessment_questions[self.pre_assessment_index]
        self.speak(q.get("question", "What do you know about this topic?"))
        self.question_start_time = time.time()

    def handle_pre_assessment_input(self, text):
        """Grade the pre-assessment answer and move to next question."""
        if self.pre_assessment_index >= len(self.pre_assessment_questions):
            self._compute_module_depths()
            return

        q = self.pre_assessment_questions[self.pre_assessment_index]
        module_name = q.get("module", "")
        expected = q.get("expected_keyword", "")

        # Simple grading: check if expected keyword is in the answer
        text_lower = text.lower()
        if expected and expected.lower() in text_lower:
            # Correct - set shallow depth for this module
            self.pre_assessment_answers[module_name] = "correct"
            self.play_sound("SUCCESS_CHORD")
            self.speak("Correct.")
        elif (
            "don't know" in text_lower
            or "no idea" in text_lower
            or "skip" in text_lower
            or len(text.strip()) < 5
        ):
            # Don't know - set deep depth
            self.pre_assessment_answers[module_name] = "unknown"
            self.speak("No worries, we'll cover that in depth.")
        else:
            # Partial - moderate depth
            self.pre_assessment_answers[module_name] = "partial"
            self.speak("Okay, we'll review that.")

        self.pre_assessment_index += 1
        self._ask_next_pre_assessment()

    def _compute_module_depths(self):
        """Convert pre-assessment answers into per-module depth settings."""
        self.pre_assessment_module_depths = {}

        for module_name, result in self.pre_assessment_answers.items():
            if result == "correct":
                self.pre_assessment_module_depths[module_name] = 1  # Review only
            elif result == "partial":
                self.pre_assessment_module_depths[module_name] = 2  # Moderate
            else:  # 'unknown'
                self.pre_assessment_module_depths[module_name] = 4  # Deep dive

        depth_summary = ", ".join(
            [f"'{k}': depth {v}" for k, v in self.pre_assessment_module_depths.items()]
        )
        logging.info(
            f"[PRE_ASSESSMENT] Computed module depths: {self.pre_assessment_module_depths}"
        )
        self.speak(
            f"Assessment complete. Personalized depths: {depth_summary}. Returning to draft. Say 'finish' when ready."
        )
        self.state = "DRAFTING_COURSE"

    def handle_teaching_style_input(self, text):
        """Handle teaching style selection during course creation."""
        text_lower = text.lower().strip()

        if "skip" in text_lower or "default" in text_lower:
            self.draft_teaching_style = ""
            self.speak("Using default Socratic style.")
        else:
            self.draft_teaching_style = text.strip()
            self.speak(f"Teaching style set to: '{self.draft_teaching_style}'.")

        # Now proceed to actual course creation
        self.speak("Starting course creation. This may take a moment.")
        self.state = "LOBBY"
        self._execute_course_creation()

    def _execute_course_creation(self):
        """Execute the actual course creation pipeline with draft settings."""
        topic = self.draft_course_topic
        depth = self.draft_course_depth
        teaching_style = self.draft_teaching_style
        module_depths = self.pre_assessment_module_depths

        self.send_status_update(f"Starting creation: {topic} (Depth {depth})")

        def _creation_pipeline():
            sm = ServiceManager(compose_cmd=["docker"])
            course_uid = None

            try:
                sm.stop_for_ingestion()

                self.send_status_update("Preparing storage...")
                self.send_status_update("LOG: Preparing Workspace...")

                self.send_status_update("Architecting Course Skeleton...")
                self.send_status_update("LOG: Architecting Course Skeleton...")
                # ZIM/Kolibri providers removed — all content is LLM-generated
                providers = []

                sb = SkeletonBuilder(
                    providers=providers,
                    status_callback=self.send_status_update,
                    course_depth=depth,
                    teaching_style=teaching_style,
                    storage=self.storage,
                )
                try:
                    course_uid = sb.build(
                        topic, max_depth=depth, module_depths=module_depths
                    )
                finally:
                    sb.close()

                if not course_uid:
                    self.speak("Failed to build course skeleton.")
                    return

                # Audit syllabus before hydration (same as web-ui pipeline)
                self.send_status_update("Auditing Syllabus Quality...")
                auditor = SyllabusAuditor(
                    status_callback=self.send_status_update, storage=self.storage
                )
                try:
                    auditor.audit(course_uid, target_depth=depth)
                finally:
                    auditor.close()

                self.send_status_update("Hydrating Content & Pedagogy...")
                self.send_status_update("LOG: Hydrating Content & Pedagogy...")
                hydrator = ContentHydrator(
                    providers=providers,
                    status_callback=self.send_status_update,
                    course_depth=depth,
                    storage=self.storage,
                )
                try:
                    hydrator.hydrate(course_uid)
                finally:
                    hydrator.close()

                self.send_status_update("Course built successfully!")
                self.speak("Course creation successful.")

            except Exception as e:
                logging.error(f"Creation pipeline failed: {e}", exc_info=True)
                # AUTO-12: Log full error, send user-friendly message
                self.send_status_update(
                    f"Error creating course. Check logs for details."
                )
                self.speak(
                    f"An error occurred during course creation. Please check logs."
                )
            finally:
                # AUTO-6: Only cleanup in finally, don't send misleading completion
                self.send_status_update("Restarting Systems...")
                sm.restart_after_ingestion()

        # AUTO-5: Track thread reference for cleanup on shutdown
        t = threading.Thread(target=_creation_pipeline, daemon=True)
        self._creation_thread = t
        t.start()

    def start_creation(self, text, epub_filepath=None):
        # LOG-1: Use debug level for diagnostic messages
        logging.debug(f"start_creation called with text='{text}', epub={epub_filepath}")

        # Extract topic and depth from command
        depth = 3
        topic = text.lower()
        interactive = False

        # Handle uploaded document: extract filepath and derive topic
        if "from epub" in topic and not epub_filepath:
            parts = topic.split("from epub", 1)
            epub_filepath = parts[1].strip()
            # Derive topic from EPUB filename (e.g., "biology_101.epub" → "biology 101")
            epub_name = os.path.basename(epub_filepath).rsplit('.', 1)[0]
            topic = epub_name.replace('_', ' ').replace('-', ' ').strip()
            if not topic:
                topic = "Uploaded Course"

        # A3: actually READ the uploaded document. Until now the filename was
        # the only thing used — uploading "organic_chemistry.epub" produced a
        # generic course about the words "organic chemistry" and the book was
        # never opened. Extraction failures are surfaced to the user rather
        # than silently falling back to a filename-derived course, because a
        # course that ignores the supplied material while appearing to use it
        # is worse than a clear error.
        source_text = ""
        if epub_filepath:
            try:
                from services.common.document_extract import (
                    extract, summarize_source, UnsupportedDocument,
                    ExtractionFailed,
                )
                source_text = extract(epub_filepath)
                logging.info(
                    f"[SOURCE] {os.path.basename(epub_filepath)}: "
                    f"{summarize_source(source_text)}")
                self.send_status_update(
                    f"Read {os.path.basename(epub_filepath)} — "
                    f"{len(source_text.split()):,} words")
            except (UnsupportedDocument, ExtractionFailed) as e:
                msg = f"Could not read {os.path.basename(epub_filepath)}: {e}"
                logging.error(f"[SOURCE] {msg}")
                self.send_status_update(msg)
                self.speak(msg)
                return
            except Exception as e:
                logging.error(f"[SOURCE] Unexpected extraction error: {e}")
                self.send_status_update(f"Could not read the uploaded file: {e}")
                self.speak("I couldn't read that file, so I've stopped rather "
                           "than build a course that ignores it.")
                return

        for prefix in ["create course on ", "create course ", "create "]:
            if topic.startswith(prefix):
                topic = topic[len(prefix) :]
                break
        topic = topic.strip()

        # Check for interactive mode flag
        if "interactive" in topic.lower():
            interactive = True
            topic = topic.replace("interactive", "").strip()

        # Check for content source override
        content_source = "zim"
        if (
            "using only ai" in text.lower()
            or "with ai" in text.lower()
            or "using ai" in text.lower()
        ):
            content_source = "llm"
            topic = (
                topic.replace("using only ai", "")
                .replace("with ai", "")
                .replace("using ai", "")
                .strip()
            )

        # Extract teaching style if present (e.g., "style Academic" or "style ELI5")
        teaching_style = ""
        if " style " in topic.lower():
            parts = topic.lower().split(" style ", 1)
            topic = parts[0].strip()
            style_str = parts[1].strip().split()[0] if parts[1].strip() else ""
            if style_str:
                teaching_style = style_str
                # Remove style token from remaining topic text
                topic = topic.replace(style_str, "").strip()

        # Try to extract depth if present
        if "with depth" in topic.lower():
            parts = topic.lower().split("with depth")
            topic = parts[0].strip()
            try:
                depth_str = parts[1].strip().split()[0]
                depth = int(depth_str)
                depth = max(1, min(5, depth))
            except (ValueError, IndexError):
                pass

        logging.info(
            f"DEBUG: Parsed topic='{topic}', depth={depth}, interactive={interactive}, source={content_source}, style='{teaching_style}'"
        )
        if not topic:
            self.speak("What topic should I research?")
            return

        if self.creation_in_progress:
            self.speak("A course is already being created. Please wait for it to finish.")
            logging.warning(
                f"[GUARD] Blocked concurrent creation request for '{topic}'"
            )
            return

        # Interactive mode: generate draft and enter DRAFTING_COURSE state
        if interactive:
            self.draft_course_topic = topic
            self.draft_course_depth = depth
            self.draft_teaching_style = teaching_style
            self.pre_assessment_module_depths = {}

            self.speak(f"Generating draft syllabus for {topic}...")
            self.send_status_update(f"Generating draft: {topic}")

            # Generate draft modules using LLM
            try:
                from services.core.course_builder import (
                    llm_generate,
                    extract_python_list,
                )

                prompt = f'Create 4 distinct modules for a course on \'{topic}\' organized logically. Return strict JSON Array of objects: [{{"title": "...", "description": "..."}}].'
                raw = llm_generate(
                    prompt,
                    sys_prompt="Expert curriculum designer. Output JSON only.",
                    max_tokens=800,
                )
                items = extract_python_list(raw)

                if items:
                    self.draft_course_structure = items
                    module_list = ", ".join([m.get("title", "?") for m in items])
                    self.state = "DRAFTING_COURSE"
                    self.speak(
                        f"Draft ready with {len(items)} modules: {module_list}. You can say 'add', 'remove', 'check my work', 'test me', or 'finish'."
                    )
                else:
                    self.speak(
                        "Failed to generate draft. Falling back to direct creation."
                    )
                    self.draft_course_structure = None
                    # Fall through to direct creation below
            except Exception as e:
                logging.error(f"Draft generation failed: {e}")
                self.speak("Draft generation failed. Proceeding with direct creation.")

            if self.state == "DRAFTING_COURSE":
                return  # Stay in drafting mode

        self.speak(
            f"Initializing creation sequence for {topic}. This may take a moment."
        )
        self.send_status_update(f"Starting creation: {topic} (Depth {depth})")

        # Run in background thread
        def _creation_pipeline():
            self.creation_in_progress = True
            self.creation_status.update({
                "active": True, "topic": topic, "phase": "initializing",
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "course_uid": None, "progress_pct": 0,
                "last_update": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            logging.info(
                f"[PIPELINE] Starting creation pipeline for '{topic}' depth={depth} source={content_source}"
            )
            sm = ServiceManager(compose_cmd=["docker"])
            course_uid = None
            pipeline_start = time.time()

            try:
                # 1. Stop Services (Safety Lock)
                self.send_status_update("Preparing storage...")
                logging.info("[PIPELINE] Step 1: Stopping services for ingestion")
                sm.stop_for_ingestion()

                elapsed = time.time() - pipeline_start
                self.send_status_update(f"Storage ready ({elapsed:.0f}s)")

                # 2. Build Skeleton
                self.creation_status.update({"phase": "skeleton", "progress_pct": 10, "last_update": time.strftime("%Y-%m-%dT%H:%M:%S")})
                self.send_status_update("Architecting Course Skeleton...")
                logging.info(f"[PIPELINE] Step 2: Building skeleton for '{topic}'")
                # ZIM/Kolibri providers removed — all content is LLM-generated
                providers = []
                logging.info("Using AI-only content source for skeleton.")

                # Pick up three-slider params if set by /api/create_course
                _cp = getattr(self, '_pending_course_params', {})
                _scope = _cp.get('scope', depth)
                _mastery = _cp.get('mastery', depth)
                _start = _cp.get('starting_from', 1)
                if hasattr(self, '_pending_course_params'):
                    del self._pending_course_params

                # AN UPLOADED BOOK'S STRUCTURE DOMINATES.
                #
                # Two pipelines that make opposite decisions. The researched
                # path INVENTS structure and sizes it to a calendar; when the
                # user supplies the file, the author already divided the
                # subject with the whole of it in view, and the course conforms
                # to their divisions: a textbook's chapters become modules and
                # its sections lessons, a novel's chapters become lessons with
                # no invented modules at all.
                #
                # Previously an upload was flattened to text and handed to the
                # researched builder as a SUPPLEMENT — the advertised feature
                # ("build a course from your book") routed through a path where
                # the book merely seasoned an invented skeleton.
                uploaded_book = None
                if epub_filepath:
                    try:
                        from services.research.book_reader import open_book
                        from services.core.book_skeleton import (
                            build_from_book, summarise as book_summarise)
                        from services.common.llm_utils import llm_generate_json
                        self.send_status_update(
                            f"Reading {os.path.basename(epub_filepath)} — "
                            f"structure first, then every chapter...")
                        uploaded_book = open_book(epub_filepath)
                    except Exception as e:
                        logging.error(f"[BOOK] reader unavailable: {e}")

                if uploaded_book:
                    shape_course = build_from_book(
                        epub_filepath, self.storage,
                        course_title=topic or None,
                        llm_json_fn=llm_generate_json,
                        status_callback=self.send_status_update)
                    if not shape_course:
                        msg = (f"Could not build a course from "
                               f"{os.path.basename(epub_filepath)}.")
                        self.send_status_update(msg)
                        self.speak(msg)
                        return
                    course_uid = shape_course["uid"]
                    _summary = book_summarise(shape_course)
                    logging.info(f"[PIPELINE] book course {course_uid}: {_summary}")
                    self.send_status_update(
                        f"STRUCT:BOOK:{_summary['shape']}:{_summary['modules']}"
                        f":{_summary['lessons']}:{_summary['concepts']}")
                    # THE GATE. A book course's quality criterion is fidelity to
                    # its book — linkage, order, naming, density — which the
                    # researched course's school-shape bands would wrongly fail
                    # (a 59-lesson novel is CORRECT for a novel). Recorded on
                    # the course either way; a failed gate warns rather than
                    # aborts, because a course with two bare titles is a course
                    # with two bare titles, not no course.
                    try:
                        sys.path.insert(0, os.path.join(
                            os.path.dirname(os.path.dirname(
                                os.path.dirname(os.path.abspath(__file__)))),
                            "tools"))
                        from book_course_qa import run as book_qa
                        _qa = book_qa(shape_course,
                                      book_chapters=len(uploaded_book.chapters))
                        shape_course["book_qa"] = {
                            "verdict": _qa["verdict"], "failed": _qa["failed"]}
                        self.storage.courses.create_course(shape_course)
                        logging.info(f"[PIPELINE] book QA: {_qa['verdict']}"
                                     + (f" failed={_qa['failed']}"
                                        if _qa["failed"] else ""))
                        self.send_status_update(
                            f"CHECK:BOOK_QA:{_qa['verdict']}")
                        if _qa["failed"]:
                            self.send_status_update(
                                f"CHECK:BOOK_QA_FAILED:{','.join(_qa['failed'])}")
                    except Exception as e:
                        logging.warning(f"[PIPELINE] book QA skipped: {e}")
                else:
                    sb = SkeletonBuilder(
                        providers=providers,
                        status_callback=self.send_status_update,
                        course_depth=depth,
                        teaching_style=teaching_style,
                        storage=self.storage,
                        scope=_scope,
                        mastery=_mastery,
                        starting_from=_start,
                    )
                    try:
                        course_uid = sb.build(topic, max_depth=depth)
                    finally:
                        sb.close()

                if not course_uid:
                    self.send_status_update("Skeleton generation failed.")
                    self.speak("Failed to build course skeleton.")
                    return

                elapsed = time.time() - pipeline_start
                self.send_status_update(
                    f"Skeleton complete ({elapsed:.0f}s). Starting Syllabus Audit..."
                )

                # 3. Audit Syllabus
                self.creation_status.update({"phase": "audit", "progress_pct": 30, "course_uid": course_uid, "last_update": time.strftime("%Y-%m-%dT%H:%M:%S")})
                auditor = SyllabusAuditor(
                    status_callback=self.send_status_update, storage=self.storage
                )
                try:
                    auditor.audit(course_uid, target_depth=depth)
                finally:
                    auditor.close()

                elapsed_audit = time.time() - pipeline_start
                self.send_status_update(
                    f"Audit complete ({elapsed_audit:.0f}s). Starting hydration..."
                )

                # 4. Hydrate Content
                self.creation_status.update({"phase": "hydration", "progress_pct": 40, "last_update": time.strftime("%Y-%m-%dT%H:%M:%S")})
                self.send_status_update("Hydrating Content & Pedagogy...")
                logging.info(
                    f"[PIPELINE] Step 3: Hydrating content for course {course_uid}"
                )
                # ZIM/Kolibri providers removed — all content is LLM-generated
                providers = []

                hydrator = ContentHydrator(
                    providers=providers,
                    status_callback=self.send_status_update,
                    course_depth=depth,
                    storage=self.storage,
                    mastery=_mastery,
                )
                # A3: if the user supplied a document, teach from IT. Previously
                # the uploaded file was only used to guess a topic from its
                # filename and its contents were never read.
                # The hydrator READS the uploaded book chapter by chapter —
                # each concept carries the chapter it came from, so its content
                # is written from that chapter's text rather than from the
                # model's recollection of the book.
                if uploaded_book is not None:
                    hydrator.book = uploaded_book
                if source_text:
                    hydrator.source_document = source_text
                    # B13.13: a book-sourced course illustrates itself. Handing
                    # the file to the hydrator makes Phase 3 run in BOOK MODE —
                    # figures come from this document and the external archives
                    # are switched off entirely.
                    hydrator.source_document_path = epub_filepath
                try:
                    hydrator.hydrate(course_uid)
                finally:
                    hydrator.close()

                # BUG-4: Set course status to "ready" after successful hydration
                # -- unless the hydrator just recorded "partial": some concepts
                # are stubs, and overwriting that verdict here was how a course
                # of [Hydration failed] bodies advertised itself as ready.
                try:
                    course = self.storage.courses.get_course(course_uid)
                    if course and course.get("status") not in ("partial", "failed"):
                        course["status"] = "ready"
                        self.storage.courses.update_course(course_uid, course)
                        logging.info(f"[PIPELINE] Course {course_uid} status set to 'ready'")
                    elif course:
                        logging.warning(f"[PIPELINE] Course {course_uid} kept "
                                        f"status={course.get('status')!r} from hydration")
                except Exception as e:
                    logging.warning(f"[PIPELINE] Failed to set course status: {e}")

                # ATTACH THE FINISHED COURSE BACK TO ITS PROGRAMME SLOT.
                # A degree course is built by the same pipeline as any other,
                # so without this the course exists, the programme still shows
                # the slot as unbuilt, and choosing it again would rebuild it
                # from scratch. `built`/`course_uid` are exactly what the
                # degree page reads to tell "Ready to start now" from "Built
                # when you choose it".
                _prog = getattr(self, "_pending_program", None)
                if _prog:
                    try:
                        ok = self.storage.programs.mark_built(
                            _prog["program_uid"], _prog["course_title"], course_uid)
                        if ok:
                            logging.info("[PIPELINE] attached %s to programme "
                                         "%s slot %r", course_uid,
                                         _prog["program_uid"], _prog["course_title"])
                        else:
                            # Named rather than swallowed: a course that built
                            # fine but never joined its programme is invisible
                            # to the learner who asked for it.
                            logging.error("[PIPELINE] built %s but could not "
                                          "attach it to programme %s slot %r",
                                          course_uid, _prog["program_uid"],
                                          _prog["course_title"])
                    except Exception as e:
                        logging.error("[PIPELINE] programme attach failed: %s", e)
                    finally:
                        self._pending_program = None

                elapsed = time.time() - pipeline_start
                self.creation_status.update({"phase": "complete", "progress_pct": 100, "last_update": time.strftime("%Y-%m-%dT%H:%M:%S")})
                self.send_status_update(f"Course built successfully! ({elapsed:.0f}s)")
                logging.info(f"[PIPELINE] Course creation complete in {elapsed:.0f}s")
                self.speak("Course creation successful.")

            except Exception as e:
                logging.error(
                    f"[PIPELINE] Creation pipeline failed: {e}", exc_info=True
                )
                self.creation_status.update({"phase": "error", "progress_pct": 0, "last_update": time.strftime("%Y-%m-%dT%H:%M:%S")})
                self.send_status_update(
                    f"Error creating course. Check logs for details."
                )
                self.speak(
                    f"An error occurred during course creation. Please check logs."
                )
            finally:
                # AUTO-6: Only cleanup in finally, don't send misleading completion
                self.send_status_update("Restarting Systems...")
                logging.info("[PIPELINE] Step 5: Restarting services")
                self.creation_in_progress = False
                self.creation_status["active"] = False
                sm.restart_after_ingestion()

        # AUTO-5: Track thread reference for cleanup on shutdown
        t = threading.Thread(target=_creation_pipeline, daemon=True)
        self._creation_thread = t
        t.start()

        self.speak(f"I am researching {topic}. This may take a moment.")

        # Async ingestion with service management (LEGACY - DISABLED in favor of _creation_pipeline)
        # logging.info("DEBUG: Starting ingestion thread...")
        # try:
        #     t = threading.Thread(target=self.run_ingestion_with_service_management, args=(topic, depth))
        #     t.start()
        #     logging.info(f"DEBUG: Ingestion thread started: {t.is_alive()}")
        # except Exception as e:
        #     logging.error(f"DEBUG: Failed to start thread: {e}", exc_info=True)

    def run_ingestion_with_service_management(self, topic, depth=3):
        """Run ingestion with proper service lifecycle management."""
        logging.info("Initializing ServiceManager for ingestion")

        # ServiceManager no longer requires sudo - uses Docker SDK
        service_mgr = ServiceManager(dev_mode=self.dev_mode)

        try:
            # Phase 1: Stop services that hold database locks
            logging.info(f"Starting course creation for topic: {topic}")
            self.send_status_update("Preparing database...")

            if not service_mgr.stop_for_ingestion():
                logging.error("Failed to stop services before ingestion")
                self.send_status_update(
                    "Failed to prepare database. Attempting recovery..."
                )
                # Attempt to restart services even on failure
                try:
                    service_mgr.restart_after_ingestion()
                except Exception as e:
                    logging.error(f"Failed to restart services during recovery: {e}")
                self.speak("Failed to prepare the database. Please try again.")
                return

            # Add delay to ensure database locks are fully released
            logging.info("Waiting for database locks to be released...")
            time.sleep(2)

            # Phase 2: Run ingestion against main database
            logging.info("Services stopped successfully, starting ingestion")
            self.send_status_update("Scraping ZIM files...")
            success = self.run_ingestion(topic)

            # Phase 3: Restart services
            logging.info("Ingestion complete, restarting services")
            self.send_status_update("Restarting services...")

            if not service_mgr.restart_after_ingestion():
                logging.error("Failed to restart services after ingestion")
                self.send_status_update(
                    "Services failed to restart. Manual intervention may be required."
                )
                self.speak(
                    "Services failed to restart. Please check the system status."
                )
                return

            # Phase 4: Report success or failure
            if success:
                self.send_status_update("Course ready to start!")
                self.speak(
                    f"Course on {topic} is ready. Say 'start course {topic}' to begin."
                )
                logging.info(
                    f"Course creation completed successfully for topic: {topic}"
                )
            else:
                self.send_status_update("Ingestion failed.")
                self.speak("Failed to create the course. Please try again.")
                logging.error(f"Ingestion failed for topic: {topic}")

        except Exception as e:
            logging.error(
                f"Ingestion with service management failed: {e}", exc_info=True
            )
            self.send_status_update("Ingestion error.")
            self.speak("An error occurred while creating the course.")

            # Attempt to restart services even on failure
            try:
                logging.info("Attempting to restart services after error")
                service_mgr.restart_after_ingestion()
            except Exception as restart_error:
                logging.error(
                    f"Failed to restart services during error recovery: {restart_error}"
                )

    def run_ingestion(self, topic, depth=3):
        """LEGACY — Run the ingestion subprocess. Deprecated in favor of _creation_pipeline."""
        logging.warning("run_ingestion is deprecated. Use _creation_pipeline instead.")
        return False

    def list_courses(self):
        try:
            logging.info("Requesting course list from StorageManager.")
            courses = self.storage.courses.list_courses()
            if courses:
                course_list = ", ".join(c.get("title", "Untitled") for c in courses[:3])
                self.speak(f"I have courses on {course_list}.")
                if len(courses) > 3:
                    self.speak(f"And {len(courses) - 3} more.")
            else:
                self.speak("I don't have any courses created yet.")
        except Exception as e:
            logging.error(f"list_courses error: {e}")
            self.speak("I'm having trouble retrieving the course list right now.")

    # Shutdown moved above to be near save logic for cohesion
    # def shutdown(self): ... (removed duplicate)

    def delete_course_state(self, uid):
        """Removes course progress from state file."""
        if not uid:
            return

        try:
            # Update Runtime State
            if self.active_course_uid == uid:
                self.active_course_uid = None
                self.current_lesson_node = None
                self.syllabus_queue = []
                # The conversation belonged to the course. Leaving it on screen
                # after the course is gone means the next thing the learner
                # reads is a dialogue about material that no longer exists.
                self.transcript = []
                self.conversation_history = []
                self.state = "LOBBY"
                self.speak("Course deleted. Returning to lobby.")

            # Update the persisted session blob (B15.7)
            full_state = self._read_session_blob()
            courses = full_state.get("courses", {})
            if uid in courses:
                del courses[uid]
            if full_state.get("last_active_uid") == uid:
                full_state["last_active_uid"] = None
            self.storage.fsm.upsert(self.student_id, json.dumps(full_state))
            logging.info(f"Deleted state for course {uid}")
        except Exception as e:
            logging.error(f"Failed to delete course state: {e}")

    def get_state(self):
        logging.debug(f"GET_STATE: transcript size={len(self.transcript)}")
        current_lesson_uid = (
            self.current_lesson_node["uid"] if self.current_lesson_node else None
        )
        current_lesson_title = (
            self.current_lesson_node["title"] if self.current_lesson_node else None
        )

        state_dict = {
            "state": self.state,
            "active_course_uid": self.active_course_uid,
            "last_question": self.last_question,
            "conversation_history": self.conversation_history,
            "transcript": self.transcript,
            "battery_level": self.battery_level,
            "current_context": self.current_context,
            "syllabus_length": len(self.syllabus_queue),
            "current_lesson_uid": current_lesson_uid,
            "current_lesson_title": current_lesson_title,
            "graph_node": {
                **(self.current_lesson_node or {}),
                "analogies": self.current_analogies,
                "misconceptions": self.current_misconceptions
            } if self.current_lesson_node else None,
            "completed_topics": list(self.completed_topics),
            "current_card": self.current_card["title"] if self.current_card else None,
            "current_card_text": self.current_card.get("text", "")
            if self.current_card
            else None,
            "locus": self.current_locus_desc,
            "teaching_style": self.current_teaching_style,
            "socratic_type_index": self.socratic_type_index,
            "socratic_retry_count": self.socratic_retry_count,
            "last_socratic_grade": self._last_socratic_grade,
            "concept_correct_streak": self.concept_correct_streak,
            "concept_question_count": self.concept_question_count,
            "bloom_level": self.current_bloom_level,
            "bloom_correct_streak": self.bloom_correct_streak,
            "concept_bloom_target": getattr(self, 'concept_bloom_target', None),
            "passed_question_types": list(getattr(self, 'passed_question_types', set())),
            "course_bloom_floor": getattr(self, 'course_bloom_floor', 1),
            "course_bloom_ceiling": getattr(self, 'course_bloom_ceiling', 6),
        }

        # Include draft state if in drafting mode
        if self.state in (
            "DRAFTING_COURSE",
            "GAP_ANALYSIS",
            "PRE_ASSESSMENT",
            "TEACHING_STYLE_SELECT",
        ):
            state_dict["draft_course_structure"] = self.draft_course_structure
            state_dict["draft_course_topic"] = self.draft_course_topic
            state_dict["pre_assessment_module_depths"] = (
                self.pre_assessment_module_depths
            )

        return state_dict


# B15.6: per-student FSM registry replaces the global singleton. The shared
# StorageManager is thread-safe (_ThreadLocalDB, WAL) and injected into every
# FSM. `registry.get(sid)` is the ONLY place that decides resident-vs-fresh,
# which is the seam for the stateless multi-worker Option A (spec 03 §7).
try:
    from fsm_registry import FSMRegistry            # container layout (flat dir)
except ImportError:
    from services.core.fsm_registry import FSMRegistry  # repo/package layout

_shared_storage = StorageManager(os.getenv("DATA_ROOT", "/app/data"))
registry = FSMRegistry(
    lambda sid, storage: MnemosyneFSM(sid, storage=storage),
    _shared_storage,
    max_size=int(os.getenv("FSM_REGISTRY_MAX", "64")),
    idle_ttl=int(os.getenv("FSM_IDLE_TTL", "1800")),
    sweep_interval=int(os.getenv("FSM_SWEEP_INTERVAL", "60")),
)


def _student_id_from_request():
    """Resolve the acting student. web-ui injects student_id from the
    authenticated session (spec 03 §5); core trusts it because web-ui is the
    sole caller inside the docker network. Falls back to the legacy student
    until B15.4 auth lands (the R0→R1 cutover swaps this for abort(400))."""
    body = request.get_json(silent=True) or {}
    return body.get("student_id") or request.args.get("student_id") or DEFAULT_STUDENT_ID


# Process-level maintenance signal handlers (were per-FSM instance handlers,
# which cannot be installed once FSMs are built on request threads).
def _maintenance_pause(signum, frame):
    logging.info("SIGUSR1 received: Entering maintenance mode (pause)")
    registry.set_maintenance(True)


def _maintenance_resume(signum, frame):
    logging.info("SIGUSR2 received: Exiting maintenance mode (resume)")
    registry.set_maintenance(False)


try:
    signal.signal(signal.SIGUSR1, _maintenance_pause)
    signal.signal(signal.SIGUSR2, _maintenance_resume)
except ValueError:
    # Not on the main thread (e.g. test import under a runner) — skip.
    logging.warning("Skipping signal-handler install (not main thread)")


@app.route("/event", methods=["POST"])
def handle_event():
    event = request.json
    sid = _student_id_from_request()
    fsm = registry.get(sid)
    # Serialize one student's rapid events (double-tap, two tabs); different
    # students take different locks and run fully concurrently (spec 03 §4.7).
    with registry.lock_for(sid):
        fsm.transition(event)
    return {"status": "ok"}


# SocketIO handler removed (core-logic is REST-only)


@app.route("/scope_check", methods=["POST"])
def scope_check_endpoint():
    """Pre-build scope check for the creation UI.

    The same instruments the build uses — curriculum evidence plus scope_fit —
    so the carousel's warning is the real verdict rather than a mock. Kept
    lightweight: one evidence sweep, no LLM generation, and the sweep itself is
    cached for 7 days so repeated checks on the same topic are free.
    """
    from flask import request as _rq, jsonify as _js
    data = _rq.get_json(silent=True) or {}
    topic = (data.get("topic") or "").strip()
    if len(topic) < 3:
        return _js({"available": False, "error": "topic too short"}), 400
    try:
        from services.research.curriculum_research import curriculum_brief
        from services.core.scope_fit import assess_scope, practice_tier
    except ImportError:
        try:
            from curriculum_research import curriculum_brief
            from scope_fit import assess_scope, practice_tier
        except ImportError:
            return _js({"available": False}), 200
    try:
        template = data.get("template") or "course"
        requested = {"course": 135, "sequence": 270, "seminar": 90,
                     "overview": 40, "associate": 2700,
                     "bachelors": 5400}.get(template, 135)
        brief = curriculum_brief(topic)
        fit = assess_scope(brief, requested, requested_courses=1)
        out = {"available": True,
               "verdict": fit.get("verdict", "ok"),
               "reason": fit.get("reason", ""),
               "chapters": fit.get("chapter_count", 0),
               "sources": fit.get("structural_sources", 0)}
        tier = practice_tier(topic)
        if tier:
            out["practice_tier"] = tier["message"]
        return _js(out), 200
    except Exception as e:
        logging.warning(f"scope_check failed for {topic!r}: {e}")
        return _js({"available": False}), 200


@app.route("/state", methods=["GET"])
def get_state():
    return registry.get(_student_id_from_request()).get_state()


@app.route("/api/aid/<aid_id>", methods=["GET"])
def get_visual_aid(aid_id):
    """Full spec for one visual aid (B13).

    Deliberately out of band from /state: the transcript carries a ~200-byte
    descriptor and this is fetched once per aid and cached in the browser, so a
    session full of diagrams does not re-send them on every 2-second poll.

    Scoped to the requesting student's FSM — aid ids are content hashes, so
    without this scoping one student could read another's diagram by guessing a
    hash of the same spec. A 404 is normal (LRU eviction), and the client
    already holds the alt-text to fall back on.
    """
    fsm = registry.get(_student_id_from_request())
    aid = fsm.aid_store.get(aid_id)
    if aid is None:
        return jsonify({"error": "aid not found", "aid_id": aid_id}), 404
    resp = jsonify(aid)
    # Content-addressed and immutable apart from `stage`, which the client
    # already knows from the transcript descriptor — safe to cache privately.
    resp.headers["Cache-Control"] = "private, max-age=600"
    return resp


# --- System resources --------------------------------------------------------
#
# memory_guard has measured pressure since it was written -- psutil with a
# vm_stat fallback, the kernel's own pressure level, WARN/CRITICAL thresholds
# -- and only gpu_gate ever asked it anything. On an appliance running a 12 GB
# model on a 24 GB desktop, "is there room right now" is something the person
# at the keyboard needs to see, not only something the scheduler consults.
# This is the read side of that, plus where the disk went.

def _cpu_brand():
    """The marketing name ("Apple M4") rather than the bare architecture.
    platform.processor() returns "arm" on macOS, which tells a user nothing."""
    try:
        r = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                           capture_output=True, text=True, timeout=2)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return platform.processor() or platform.machine()


def _dir_size(path):
    """Bytes under `path`. Skips what it cannot stat rather than failing the
    whole report over one unreadable file."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                continue
    return total


@app.route("/api/system/resources", methods=["GET"])
def system_resources():
    """Memory, disk and hardware in one call.

    Each section fails independently: a broken disk walk must not cost the
    caller the memory reading, because the memory reading is the one the
    safeguard card depends on.
    """
    out = {}

    try:
        from services.common import memory_guard as mg
        snap = mg.snapshot(force=True)
        out["memory"] = {
            "total_gb": round(snap.total_gb, 2),
            "available_gb": round(snap.available_gb, 2),
            "used_pct": round(snap.used_pct, 1),
            "swap_used_gb": round(snap.swap_used_gb, 2),
            "swap_used_frac": round(snap.swap_used_frac, 3),
            "pressure_level": mg.macos_pressure_level(),
            "under_pressure": bool(mg.under_pressure(snap)),
            "allow_background": bool(mg.allow_background(snap)),
            # None when there is nothing wrong: the card appears only when the
            # guard has something to say, and says exactly what it said.
            "reason": mg.pressure_reason(snap),
            "source": snap.source,
        }
    except Exception as e:
        logging.warning("memory snapshot unavailable: %s", e)
        out["memory"] = {"error": str(e)}

    try:
        data_dir = _shared_storage.data_dir
        courses_dir = _shared_storage.courses_dir
        per_course = []
        listing = sorted(os.listdir(courses_dir)) if os.path.isdir(courses_dir) else []
        for uid in listing:
            d = os.path.join(courses_dir, uid)
            if not os.path.isdir(d):
                continue
            try:
                meta = _shared_storage.courses.get_course(uid)
            except Exception:
                meta = None
            per_course.append({"uid": uid,
                               "title": (meta or {}).get("title") or uid,
                               "bytes": _dir_size(d)})
        per_course.sort(key=lambda c: -c["bytes"])

        db_bytes = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                db_bytes += os.path.getsize(_shared_storage.db_path + suffix)
            except OSError:
                pass

        total = _dir_size(data_dir)
        try:
            usage = shutil.disk_usage(data_dir)
            disk = {"total_bytes": usage.total, "free_bytes": usage.free}
        except Exception:
            disk = None

        out["storage"] = {
            "total_bytes": total,
            "database_bytes": db_bytes,
            # Uploads, assets, logs. Named rather than left as an unexplained
            # gap between the bar segments and the total.
            "other_bytes": max(0, total - db_bytes
                               - sum(c["bytes"] for c in per_course)),
            "courses": per_course,
            "disk": disk,
        }
    except Exception as e:
        logging.warning("storage report unavailable: %s", e)
        out["storage"] = {"error": str(e)}

    try:
        out["hardware"] = {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": _cpu_brand(),
            "cpu_count": os.cpu_count(),
            "python": platform.python_version(),
            "model": os.environ.get("OLLAMA_MODEL", ""),
        }
    except Exception as e:
        out["hardware"] = {"error": str(e)}

    return out


# --- Degree programmes -------------------------------------------------------
#
# plan_degree() has been able to produce a real programme -- sourced course
# list, inferred prerequisites, topologically laid-out terms, validated as
# teachable -- for as long as it has existed, and nothing could ask it for one.
# It was reachable from the test suite and a QA script and from nowhere else,
# so the degree tier was complete except for the part where a learner has a
# degree. These four routes are that part.

@app.route("/api/programs", methods=["GET"])
def list_programs():
    try:
        return {"programs": _shared_storage.programs.list()}
    except Exception as e:
        logging.error("list_programs failed: %s", e)
        return {"error": str(e)}, 500


@app.route("/api/program/<uid>", methods=["GET"])
def get_program(uid):
    try:
        plan = _shared_storage.programs.get(uid)
        if not plan:
            return {"error": "no such programme"}, 404
        # Size in CONCEPTS — this product's honest equivalent of credit hours,
        # because it is the unit actually counted, taught and reviewed. Sent
        # with the plan so the degree page can say how much degree this is
        # without inventing a duration nobody measured.
        try:
            from services.core.program import programme_size, course_size
            plan["size"] = programme_size(plan)
            for c in plan.get("courses", []):
                c["size"] = course_size(c)
        except Exception as e:
            logging.debug("programme sizing unavailable: %s", e)
        return plan
    except Exception as e:
        logging.error("get_program %s failed: %s", uid, e)
        return {"error": str(e)}, 500


@app.route("/api/program", methods=["POST"])
def create_program():
    """Plan a degree and persist it.

    Synchronous on purpose: planning consults the curriculum sources and the
    model, but it produces a STRUCTURE rather than content, so it finishes in
    seconds rather than the minutes a build takes. The courses inside it are
    built later, one ahead of the learner.
    """
    data = request.get_json(silent=True) or {}
    subject = (data.get("subject") or "").strip()
    template = (data.get("template") or "associate").strip()
    # Whether this learner wants the general-education half of a real degree.
    # Defaulting to "include" keeps the credit-hour comparison intact for
    # anyone who does not express a preference.
    gen_ed = (data.get("general_education") or "include").strip()
    if not subject:
        return {"error": "subject is required"}, 400
    try:
        from services.core.program import (plan_degree, ProgramError,
                                           GEN_ED_MODES)
        from services.common.llm_utils import llm_generate_json
    except ImportError as e:
        logging.error("program module unavailable: %s", e)
        return {"error": "degree planning unavailable"}, 500
    if gen_ed not in GEN_ED_MODES:
        return {"error": f"general_education must be one of "
                         f"{', '.join(GEN_ED_MODES)}"}, 400
    try:
        # Same helper the course builder hands the planner elsewhere, so a
        # programme is planned against the same model and repair path.
        plan = plan_degree(subject, template, llm_json_fn=llm_generate_json,
                           general_education=gen_ed)
    except ProgramError as e:
        # An unteachable plan is a real answer, not a server fault: the subject
        # could not carry a programme of this size.
        return {"error": str(e), "reason": "unteachable"}, 422
    except Exception as e:
        logging.exception("plan_degree failed for %r/%r", subject, template)
        return {"error": str(e)}, 500

    # Shape gate. validate() catches what makes a programme UNTEACHABLE —
    # cycles, missing prerequisites, duplicates — and says nothing about
    # whether the result looks like a degree. A plan_degree() run with no
    # model falls back to placeholder titles ("Economics: gen_ed 1"), passes
    # validate() cleanly, and was persisted and rendered on the degree map as
    # a real programme. Measured on exactly that plan: NOT_DEGREE_SHAPED on
    # breadth, capstone and titles.
    #
    # The check is arithmetic on the plan — no model, no latency — so there is
    # no reason not to run it before writing. A misshapen plan is reported
    # rather than stored: the learner sees why, instead of a course list of
    # numbered placeholders.
    try:
        from tools.degree_quality import assess
        shape = assess(plan)
        if shape.get("verdict") != "DEGREE_SHAPED":
            failed = ", ".join(shape.get("failed", []))
            logging.warning("programme for %r rejected: %s", subject, failed)
            return {"error": f"the planner produced a programme that does not "
                             f"look like a degree ({failed}) — nothing was "
                             f"saved", "reason": "not_degree_shaped",
                    "checks": shape.get("checks", {})}, 422
    except ImportError:
        # The harness is a dev tool; its absence must not block a build.
        logging.info("degree_quality unavailable — shape gate skipped")

    uid = "prog_" + uuid.uuid4().hex[:8]
    _shared_storage.programs.create(uid, plan)
    plan["uid"] = uid
    return plan, 201


@app.route("/api/program/<uid>/choose", methods=["POST"])
def choose_program_elective(uid):
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return {"error": "title is required"}, 400
    try:
        plan = _shared_storage.programs.get(uid)
        if not plan:
            return {"error": "no such programme"}, 404
        course = next((c for c in plan.get("courses", [])
                       if c.get("title") == title), None)
        if not course:
            return {"error": "no such course in this programme"}, 404

        # Already built: nothing to start, just hand back where it lives.
        if course.get("built") and course.get("course_uid"):
            _shared_storage.programs.choose(uid, title)
            return {"status": "ok", "chosen": title, "building": False,
                    "course_uid": course["course_uid"]}

        # A course whose prerequisites are unmet must not be startable. The
        # UI greys those out, but a UI is not a gate — this is.
        from services.core.program import available_courses
        if title not in {c["title"] for c in available_courses(plan)}:
            unmet = [r for r in (course.get("requires") or [])
                     if not any(c.get("completed") and c["title"] == r
                                for c in plan.get("courses", []))]
            return {"error": f"{title} is not available yet",
                    "reason": "prerequisites_unmet", "requires": unmet}, 409

        fsm = registry.get(_student_id_from_request())
        if fsm.creation_in_progress:
            return {"error": "A course is already being built. One at a time —"
                             " that is the whole model.",
                    "reason": "build_in_progress"}, 409

        _shared_storage.programs.choose(uid, title)

        # CHOOSING IS WHAT STARTS THE BUILD. Until now /choose recorded the
        # decision and started nothing, so a learner picked a course, the page
        # reloaded, and the course they picked stayed unbuilt forever. The
        # programme's whole promise — "pick one and Helga builds it before you
        # arrive" — depended on a build nobody was launching.
        fsm._pending_course_params = {
            "scope": 3, "mastery": 3, "starting_from": 1,
        }
        # Remembered so the pipeline can attach the finished course back to
        # the programme slot it was built for.
        fsm._pending_program = {"program_uid": uid, "course_title": title}
        fsm.start_creation(f"create course {title}")
        return {"status": "ok", "chosen": title, "building": True,
                "program_uid": uid}
    except Exception as e:
        logging.error("choosing %r in programme %s failed: %s", title, uid, e)
        return {"error": str(e)}, 500


@app.route("/api/schedule/stats", methods=["GET"])
def get_schedule_stats():
    sid = _student_id_from_request()
    try:
        _shared_storage.schedule.mark_overdue(student_id=sid)
        upcoming = _shared_storage.schedule.get_upcoming_count(days=7, student_id=sid)
        all_reviews = _shared_storage.schedule.get_scheduled_reviews(student_id=sid)
        overdue_curr = len([r for r in all_reviews if r.get("status") == "overdue"])
        completed_curr = len([r for r in all_reviews if r.get("status") == "completed"])
        retention = 100
        if completed_curr + overdue_curr > 0:
            retention = int((completed_curr / (completed_curr + overdue_curr)) * 100)

        streak = _shared_storage.activity.get_streak(student_id=sid)

        return {
            "streak": streak,
            "upcoming": upcoming,
            "overdue": overdue_curr,
            "retention": retention,
        }
    except Exception as e:
        logging.error(f"Schedule stats error: {e}")
        return {"error": str(e)}, 500


@app.route("/api/schedule", methods=["GET"])
def get_schedule():
    month = request.args.get("month")
    year = request.args.get("year")
    sid = _student_id_from_request()
    try:
        if month and year:
            import calendar

            start_date = f"{year}-{int(month):02d}-01"
            last_day = calendar.monthrange(int(year), int(month))[1]
            end_date = f"{year}-{int(month):02d}-{last_day}"
            reviews = _shared_storage.schedule.get_scheduled_reviews(
                start_date=start_date, end_date=end_date, student_id=sid
            )
        else:
            reviews = _shared_storage.schedule.get_scheduled_reviews(student_id=sid)
        return jsonify(reviews)
    except Exception as e:
        logging.error(f"Schedule list error: {e}")
        return {"error": str(e)}, 500


@app.route("/api/schedule/complete", methods=["POST"])
def complete_schedule_review():
    data = request.json or {}
    review_id = data.get("review_id")
    if not review_id:
        return {"error": "missing review_id"}, 400
    try:
        _shared_storage.schedule.complete_review(review_id, student_id=_student_id_from_request())
        return {"status": "ok"}
    except Exception as e:
        logging.error(f"Schedule complete error: {e}")
        return {"error": str(e)}, 500


@app.route("/metrics", methods=["GET"])
def metrics():
    """B27.2-lite: ops counters in Prometheus text format — GPU gate,
    Ollama breaker, FSM registry. Scrape-ready; a full prometheus_client
    integration can replace this without changing the endpoint."""
    try:
        from gpu_gate import get_gpu_gate, get_breaker
    except ImportError:
        from services.core.gpu_gate import get_gpu_gate, get_breaker
    gate = get_gpu_gate().stats()
    breaker = get_breaker().stats()
    reg = registry.stats()
    lines = [
        f"helga_gpu_inflight {gate['inflight']}",
        f"helga_gpu_cap {gate['cap']}",
        f"helga_gpu_waiting_interactive {gate['waiting_interactive']}",
        f"helga_gpu_waiting_background {gate['waiting_background']}",
        f"helga_gpu_granted_total {gate['granted_total']}",
        f"helga_gpu_busy_emits_total {gate['busy_emits']}",
        f"helga_gpu_overloads_total {gate['overloads']}",
        f"helga_breaker_open {1 if breaker['state'] == 'open' else 0}",
        f"helga_breaker_state_changes_total {breaker['state_changes']}",
        f"helga_fsm_resident {reg['resident']}",
        f"helga_fsm_max {reg['max_size']}",
    ]
    try:
        from usage_tracker import totals as _usage_totals
    except ImportError:
        from services.core.usage_tracker import totals as _usage_totals
    u = _usage_totals()
    lines += [
        f"helga_llm_calls_total {u['calls']}",
        f"helga_llm_prompt_tokens_total {u['prompt_tokens']}",
        f"helga_llm_completion_tokens_total {u['completion_tokens']}",
        f"helga_llm_gpu_seconds_total {round(u['gpu_seconds'], 2)}",
        f"helga_llm_students_seen {u['students']}",
    ]
    return "\n".join(lines) + "\n", 200, {"Content-Type": "text/plain; version=0.0.4"}


@app.route("/api/usage", methods=["GET"])
def api_usage():
    """B27.4: per-student token/GPU-second usage since process start."""
    try:
        from usage_tracker import snapshot, totals
    except ImportError:
        from services.core.usage_tracker import snapshot, totals
    return jsonify({"totals": totals(), "per_student": snapshot()})


@app.route("/health", methods=["GET"])
def health():
    try:
        # Check resource usage
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        memory_used_gb = memory.used / (1024**3)

        reg = registry.stats()
        # Legacy fields keyed to the legacy student's FSM when resident
        legacy_fsm = registry.peek(DEFAULT_STUDENT_ID)
        latency = (time.time() - legacy_fsm.last_interaction_time) if legacy_fsm else 0
        state = legacy_fsm.state if legacy_fsm else "LOBBY"

        return {
            "status": "healthy",
            "timestamp": time.time(),
            "latency_seconds": latency,
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "memory_used_gb": round(memory_used_gb, 2),
            "state": state,
            "fsm_registry": {"resident": reg["resident"], "max_size": reg["max_size"]},
        }
    except Exception as e:
        logging.error(f"Health check failed: {e}", exc_info=True)
        return {"status": "unhealthy", "error": str(e)}, 500


# --- Wizard API Endpoints (Custom Course Builder) ---

@app.route("/api/suggest_modules", methods=["POST"])
def suggest_modules():
    """LLM suggests modules for the custom course wizard."""
    data = request.json or {}
    title = data.get("title", "")
    description = data.get("description", "")
    prior_knowledge = data.get("prior_knowledge", "new")

    prompt_sys = "You are a curriculum designer. Return JSON only, no explanation."
    prompt_user = f"""Design modules for a course.

Course: {title}
Student's goal: {description}
Student's background: {prior_knowledge}

Suggest 3-5 modules forming a logical learning progression, from foundational to advanced.

Return JSON only:
[{{"title": "Module Name", "description": "What this module covers (1 sentence)"}}]"""

    try:
        result = get_llm_client().chat_json(prompt_sys, prompt_user, max_tokens=500)
        if isinstance(result, list):
            return {"modules": result}
        elif isinstance(result, dict) and "modules" in result:
            return {"modules": result["modules"]}
        return {"modules": []}
    except Exception as e:
        logging.error(f"suggest_modules error: {e}")
        return {"modules": [], "error": str(e)}, 500


@app.route("/api/suggest_concepts", methods=["POST"])
def suggest_concepts():
    """LLM suggests concepts for a module in the wizard."""
    data = request.json or {}

    prompt_sys = "You are a curriculum designer. Return JSON only."
    prompt_user = f"""Design concepts for a course module.

Course: {data.get('title', '')}
Course goal: {data.get('description', '')}
Student background: {data.get('prior_knowledge', 'new')}
Module: {data.get('module_title', '')}
Module guidance: {data.get('module_note', '')}
Already defined concepts: {', '.join(data.get('existing_concepts', []))}

Suggest 3-5 additional concepts that would complete this module.
Order from foundational to advanced. Don't duplicate existing concepts.

Return JSON only:
[{{"title": "Concept Name", "description": "One sentence about what this covers"}}]"""

    try:
        result = get_llm_client().chat_json(prompt_sys, prompt_user, max_tokens=500)
        if isinstance(result, list):
            return {"concepts": result}
        elif isinstance(result, dict) and "concepts" in result:
            return {"concepts": result["concepts"]}
        return {"concepts": []}
    except Exception as e:
        logging.error(f"suggest_concepts error: {e}")
        return {"concepts": [], "error": str(e)}, 500


@app.route("/api/clarify_course", methods=["POST"])
def clarify_course():
    """LLM generates clarifying questions before course generation."""
    data = request.json or {}

    modules_desc = ""
    for mod in data.get("modules", []):
        concepts_str = ", ".join(c.get("title", "") for c in mod.get("concepts", []))
        modules_desc += f"\n  - {mod.get('title', '')} (note: {mod.get('note', 'none')})"
        if concepts_str:
            modules_desc += f"\n    Concepts: {concepts_str}"
        else:
            modules_desc += "\n    Concepts: LLM will generate"

    prompt_sys = "You are preparing to build a custom educational course. Return JSON only."
    prompt_user = f"""Review what the student provided and ask 3-5 clarifying questions.

Course title: {data.get('title', '')}
Student's goal: {data.get('description', '')}
Student's background: {data.get('prior_knowledge', 'new')}
Modules:{modules_desc}

Rules:
1. Ask about SCOPE boundaries — what to include/exclude
2. Ask about DEPTH preferences — math level, theoretical vs practical
3. Ask about SPECIFIC STRUGGLES mentioned in notes
4. Don't ask about things already clearly stated

Return JSON only:
[{{"question": "Your question text", "context": "Why this matters for course quality"}}]"""

    try:
        result = get_llm_client().chat_json(prompt_sys, prompt_user, max_tokens=600)
        if isinstance(result, list):
            return {"questions": result}
        elif isinstance(result, dict) and "questions" in result:
            return {"questions": result["questions"]}
        return {"questions": []}
    except Exception as e:
        logging.error(f"clarify_course error: {e}")
        return {"questions": [], "error": str(e)}, 500


@app.route("/api/create_course_custom", methods=["POST"])
def create_course_custom():
    """Trigger custom course generation from wizard payload."""
    data = request.json or {}
    title = data.get("title", "").strip()
    if not title:
        return {"error": "Title required"}, 400

    fsm = registry.get(_student_id_from_request())
    if fsm.creation_in_progress:
        return {"error": "Course creation already in progress"}, 409

    import threading

    def _custom_pipeline():
        try:
            fsm.creation_in_progress = True
            fsm.send_status_update(f"Creating custom course: {title}")

            # Build the course creation command
            topic = title
            depth = 3
            teaching_style = data.get("teaching_style", "")
            fsm.start_creation(
                f"create course {topic} depth {depth}",
                epub_filepath=None
            )
        except Exception as e:
            logging.error(f"Custom course creation failed: {e}", exc_info=True)
            fsm.send_status_update(f"Error: {str(e)[:200]}")
        finally:
            fsm.creation_in_progress = False

    threading.Thread(target=_custom_pipeline, daemon=True).start()
    return {"status": "building", "course_uid": "pending"}


# --- Verification Guide Routes (VG-01, VG-04, VG-08) ---

@app.route("/api/create_course", methods=["POST"])
def api_create_course():
    """Quick-create endpoint. Accepts {topic, depth} or {topic, scope, mastery, starting_from}."""
    data = request.get_json(force=True)
    topic = data.get("topic", "").strip()
    depth = data.get("depth", 2)
    scope = data.get("scope", depth)
    mastery = data.get("mastery", depth)
    starting_from = data.get("starting_from", 1)
    if not topic:
        return jsonify({"error": "topic required"}), 400

    fsm = registry.get(_student_id_from_request())
    if fsm.creation_in_progress:
        return jsonify({"error": "Course creation already in progress"}), 409

    # Store slider params for the creation pipeline to pick up
    fsm._pending_course_params = {
        "scope": int(scope), "mastery": int(mastery), "starting_from": int(starting_from)
    }
    fsm.start_creation(f"create course {topic} with depth {depth}")
    return jsonify({"course_uid": "pending", "status": "building",
                     "params": {"scope": scope, "mastery": mastery, "starting_from": starting_from}})


@app.route("/api/set_active_course", methods=["POST"])
def api_set_active_course():
    """VG-04: Set the active course context on the FSM."""
    data = request.get_json(force=True)
    uid = data.get("uid", "")
    title = data.get("title", "")
    fsm = registry.get(_student_id_from_request())
    if uid:
        fsm.active_course_uid = uid
        try:
            course = fsm.storage.courses.get_course(uid)
            fsm.current_teaching_style = (
                course.get("teaching_style", "") if course else ""
            )
        except Exception as e:
            logging.warning(f"set_active_course meta load failed: {e}")
    return jsonify({"status": "ok", "active_course_uid": fsm.active_course_uid})


@app.route("/api/reset_state", methods=["POST"])
def api_reset_state():
    """VG-08: Reset FSM state for test isolation."""
    fsm = registry.get(_student_id_from_request())
    fsm.state = "LOBBY"
    fsm.active_course_uid = None
    fsm.transcript = []
    fsm.conversation_history = []
    fsm.syllabus_queue = []
    fsm.current_lesson_node = None
    fsm.current_teaching_style = ""
    fsm.creation_in_progress = False
    return jsonify({"status": "reset", "state": "LOBBY"})


@app.route("/api/creation_status", methods=["GET"])
def api_creation_status():
    """Monitor course creation progress. Returns current phase, topic, and progress."""
    return jsonify(registry.get(_student_id_from_request()).creation_status)


@app.route("/api/cancel_creation", methods=["POST"])
def api_cancel_creation():
    """Cancel an in-progress course creation."""
    fsm = registry.get(_student_id_from_request())
    if not fsm.creation_in_progress:
        return jsonify({"status": "no_creation_active"})
    fsm.creation_in_progress = False
    fsm.creation_status.update({
        "active": False,
        "phase": "cancelled",
        "last_update": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    logging.info("Course creation cancelled by user")
    return jsonify({"status": "cancelled"})


if __name__ == "__main__":
    logging.info("Starting core-logic service...")
    app.run(host="0.0.0.0", port=5003)
