# # from gevent import monkey; monkey.patch_all()
import requests
import time
import logging
import sys
import os
import uuid
import yaml
import subprocess
import threading
# ThreadPoolExecutor removed — TTS thread pool no longer needed
import json
import re
from services.common.storage import StorageManager
import psutil
import signal
from flask import Flask, request, jsonify

# Ensure services/common is in path for prompts
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from services.common.prompts import (
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


from safety import check_safety, check_safety_detailed, get_safety_redirect_message
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


class MnemosyneFSM:
    def __init__(self):
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
        self.socratic_type_index = 0  # Index into SOCRATIC_QUESTION_TYPES
        self.socratic_retry_count = 0  # Consecutive low-grade attempts on current type
        self._last_socratic_grade = 3  # Last grade for rule-based mode selection

        # Multi-question mastery tracking
        self.concept_correct_streak = 0  # Consecutive correct answers (grade >= 3), reset on grade < 3
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

        # Service URLs
        self.rag_url = os.environ.get("RAG_URL", "http://rag-engine:5002" if not self.dev_mode else "http://localhost:5002")
        self.web_ui_url = os.environ.get("WEB_UI_URL", "http://web-ui:5000" if not self.dev_mode else "http://localhost:5000")

        logging.info(f"Dev mode: {self.dev_mode}")
        logging.info(f"Service URLs: RAG={self.rag_url}, WebUI={self.web_ui_url}")

        # Initialize StorageManager (replaces KuzuDB)
        self.storage = StorageManager(self.data_root)
        self.conn = None  # Legacy compat

        self.timer_thread = threading.Thread(target=self.check_timers, daemon=True)
        self.timer_thread.start()

        # Setup signal handlers for maintenance mode
        signal.signal(signal.SIGUSR1, self._handle_maintenance_pause)
        signal.signal(signal.SIGUSR2, self._handle_maintenance_resume)

        # Restore State
        self._load_state_from_disk()

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

    def _call_llm(self, messages, max_tokens=1000, timeout=60):
        """Call the LLM via Ollama client.

        Args:
            messages: List of message dicts [{"role": "system", "content": "..."}]
            max_tokens: Maximum tokens to generate
            timeout: Request timeout in seconds

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
        )
        return result if result else None

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

    def _send_stream_token(self, token, done=False):
        """Send a single stream token to the web-ui for real-time display."""
        data = {
            "type": "stream_token",
            "token": token,
            "done": done,
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

    def send_status_update(self, message, log=None, progress=None):
        """Sends real-time status update to the Web UI for progress feedback."""
        data = {"message": message}
        if log:
            data["log"] = log
        if progress is not None:
            data["progress"] = progress
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

    def speak(self, text, record=True):
        """Add a tutor message to the transcript (text-only, no TTS)."""
        self.add_message(text, record=record)

    def add_message(self, text, record=True, grade=None):
        """Add a tutor message to the transcript."""
        if record:
            entry = {"sender": "helga", "text": text}
            if grade is not None:
                entry["grade"] = grade
            self.transcript.append(entry)
            if len(self.transcript) > 50:
                self.transcript = self.transcript[-50:]
            logging.info(f"TRANSCRIPT (AI): {text[:80]}... (Size: {len(self.transcript)})")

    def play_sound(self, sound_id):
        """No-op — sound effects removed in text-only mode."""
        pass

    def stop_audio(self):
        """No-op — audio removed in text-only mode."""
        pass

    def check_timers(self):
        while True:
            time.sleep(1)
            # Skip timers if in maintenance mode
            if self.state == "PAUSED" or self.maintenance_paused:
                continue
            now = time.time()
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
            safety_result = check_safety_detailed(text, node_title)
            if not safety_result.is_safe:
                redirect_msg = get_safety_redirect_message(safety_result)
                logging.warning(
                    f"Safety block [{safety_result.category}]: '{text[:60]}...' confidence={safety_result.confidence:.2f}"
                )
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
            elif event_type == "DELETE_COURSE":
                uid = event.get("payload", {}).get("uid")
                self.delete_course_state(uid)

        elif self.state == "SOCRATIC_LEARNING":
            if event_type == "SKIP_CONCEPT":
                self.speak("Skipping this concept.")
                self._advance_without_completing()
                return
            if event_type == "TEXT_INPUT":
                if self.handle_nav_commands(text):
                    return
                self.handle_socratic_answer(text)

        elif self.state == "SPACED_REPETITION":
            if event_type == "TEXT_INPUT":
                if self.handle_nav_commands(text):
                    return
                self.handle_flashcard_answer(text)

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

        # Voice-related events are no-ops in text-only mode
        if event_type in ("TOGGLE_MIC", "TOGGLE_TTS", "TOGGLE_TEXT_ONLY"):
            return True

        if "pause" in text or event_type == "PAUSE":
            if self.state != "PAUSED":
                self.previous_state = self.state
                self.state = "PAUSED"
            return True
        if "resume" in text or event_type == "RESUME":
            if self.state == "PAUSED":
                self.state = self.previous_state if self.previous_state else "LOBBY"
            return True
        if "stop" in text or "reset" in text or "go to lobby" in text:
            self.state = "LOBBY"
            self.speak("Returned to lobby.")
            self.current_lesson_node = None
            self.syllabus_queue = []
            return True
        if "end session" in text or "shutdown" in text:
            self.shutdown()
            return True
        return False

    def handle_nav_commands(self, text):
        if "next" in text:
            if self.state == "SOCRATIC_LEARNING":
                self._advance_without_completing()
                return True
            self.play_sound("STEP_FORWARD")
            return False
        if "go back" in text or "previous" in text:
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
        logging.info(f"Course Bloom bounds: floor={self.course_bloom_floor}, ceiling={self.course_bloom_ceiling}")

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
        """GAP 6: Strip answer-key sections from context to prevent LLM leakage.
        Keep pedagogical sections (Mastery Criteria, Prerequisites, Misconceptions,
        Edge Cases, Socratic Hooks, Analogies)."""
        if not context_text:
            return context_text
        import re as _re
        text = context_text
        for section in ("Core Definition", "Core Explanation", "Contextual Explanation",
                        "Component Breakdown", "Key Facts", "Real-World Examples"):
            text = _re.sub(
                rf"## {section}\s*\n.*?(?=\n## |\Z)", "", text, flags=_re.DOTALL
            )
        return text.strip()

    def _extract_mastery_criteria(self, content):
        """Extract the Mastery Criteria section from concept markdown for grading."""
        if not content:
            return ""
        import re as _re
        match = _re.search(r"## Mastery Criteria\s*\n(.*?)(?=\n## |\Z)", content, _re.DOTALL)
        return match.group(1).strip() if match else ""

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

    def _check_mastery_gate(self):
        """GAP 4: Strengthened mastery gate — checks streak, bloom target, and question diversity."""
        streak_met = self.concept_correct_streak >= 2 and self.concept_question_count >= 3
        bloom_met = self.current_bloom_level >= (self.concept_bloom_target or 1)
        diversity_met = len(self.passed_question_types) >= 3
        if streak_met and bloom_met and diversity_met:
            return True
        reasons = []
        if not streak_met:
            reasons.append(f"streak={self.concept_correct_streak}/2, count={self.concept_question_count}/3")
        if not bloom_met:
            reasons.append(f"bloom={self.current_bloom_level}/{self.concept_bloom_target}")
        if not diversity_met:
            reasons.append(f"types={len(self.passed_question_types)}/3")
        logging.info(f"Mastery gate not met ({', '.join(reasons)}): recycling question types")
        return False

    def _next_unpassed_type_index(self):
        """Fix 6: Find the first question type not yet passed by the student."""
        for i, qt in enumerate(SOCRATIC_QUESTION_TYPES):
            if qt["key"] not in self.passed_question_types:
                return i
        return 0  # All passed — start from beginning

    def report_status(self):
        self.play_sound("RETENTION_HISS")
        self.speak(f"All systems nominal. Battery at {self.battery_level} percent.")

    def get_concept_details(self, uid):
        """Helper to fetch concept details from local storage.
        GAP 3: Now returns bloom metadata alongside content."""
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
                    self.syllabus_queue.append({
                        "uid": c["uid"], "title": c["title"], "text": content,
                        "bloom_level": c.get("bloom_level"),
                        "learning_objectives": c.get("learning_objectives", []),
                        "complexity_role": c.get("complexity_role", ""),
                        "module_bloom_target": c.get("module_bloom_target"),
                    })
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
                self.syllabus_queue.append({
                    "uid": c["uid"], "title": c["title"], "text": content,
                    "bloom_level": c.get("bloom_level"),
                    "learning_objectives": c.get("learning_objectives", []),
                    "complexity_role": c.get("complexity_role", ""),
                    "module_bloom_target": c.get("module_bloom_target"),
                })

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

    def _save_current_course_progress(self):
        if not self.active_course_uid:
            return

        try:
            # Load existing full state to not overwrite other courses
            if os.path.exists(self.state_file):
                with open(self.state_file, "r") as f:
                    full_state = json.load(f)
            else:
                full_state = {"courses": {}}

            course_state = {
                "current_node": self.current_lesson_node,
                "syllabus_queue": self.syllabus_queue,
                "completed_topics": list(self.completed_topics),
                "transcript": self.transcript[-20:],
                "conversation_history": self.conversation_history[-10:],
                "socratic_type_index": self.socratic_type_index,
                "concept_correct_streak": self.concept_correct_streak,
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

            # LRN-8: Use atomic write to prevent corruption from concurrent requests
            self._atomic_write(self.state_file, json.dumps(full_state, indent=2))
            logging.info(f"Saved progress for course {self.active_course_uid}")
        except Exception as e:
            logging.error(f"Failed to save state: {e}")

    def _load_course_progress(self, course_uid):
        if not os.path.exists(self.state_file):
            return False
        try:
            with open(self.state_file, "r") as f:
                full_state = json.load(f)

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

    def _load_state_from_disk(self):
        """Initial load on startup"""
        if not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, "r") as f:
                full_state = json.load(f)
            last_uid = full_state.get("last_active_uid")
            if last_uid:
                self.active_course_uid = last_uid
                # Optional: Auto-load the last course?
                # For now, we stay in LOBBY but user can say "Resume" commands later.
                # Or we could pre-load variables but stay in LOBBY until user says "Start".
                # Let's just track the UID for now.
                logging.info(f"Found last active course: {last_uid}")
        except Exception as e:
            logging.error(f"Failed to load global state: {e}")

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
        if not self.syllabus_queue:
            # Try auto-populate like next_syllabus_item does
            if self.active_course_uid:
                try:
                    all_concepts = self.storage.courses.get_flat_concepts(self.active_course_uid)
                    for c in all_concepts:
                        if c["uid"] not in self.completed_topics:
                            content = self.storage.courses.get_concept_content(
                                self.active_course_uid, c["uid"]
                            )
                            self.syllabus_queue.append({
                                "uid": c["uid"], "title": c["title"], "text": content or "",
                                "bloom_level": c.get("bloom_level"),
                                "learning_objectives": c.get("learning_objectives", []),
                                "complexity_role": c.get("complexity_role", ""),
                                "module_bloom_target": c.get("module_bloom_target"),
                            })
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
                            self.syllabus_queue.append({
                                "uid": c["uid"], "title": c["title"], "text": content or "",
                                "bloom_level": c.get("bloom_level"),
                                "learning_objectives": c.get("learning_objectives", []),
                                "complexity_role": c.get("complexity_role", ""),
                                "module_bloom_target": c.get("module_bloom_target"),
                            })
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
        self.concept_question_count = 0
        self.current_bloom_level = 1  # Reset Bloom's level for new concept
        self.bloom_correct_streak = 0

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

        logging.info(f"Teaching Mode Selected: {teaching_mode}")
        self.send_status_update(f"Mode: {teaching_mode}...", progress=70)

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
        q_type_idx = min(self.socratic_type_index, len(SOCRATIC_QUESTION_TYPES) - 1)
        current_q_type = SOCRATIC_QUESTION_TYPES[q_type_idx]

        # Broadcast question type to UI
        self.send_status_update(
            f"QTYPE:{current_q_type['key']}:{q_type_idx}:{len(SOCRATIC_QUESTION_TYPES)}"
        )

        # GAP 6: Redact answer-key sections from tutor context to prevent leakage
        redacted_context = self._redact_context_for_tutor(self.current_context)

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

        self.last_question = question
        self.conversation_history.append((context_trigger, question))

        # FIX: The tutor must speak the question!
        self.speak(question)
        self.question_start_time = time.time()

    # NOTE: Dead duplicate _detect_ignorance removed during audit.
    # The canonical version is below (line ~1045).

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

        # Direct match or phrase match
        if text_lower in idk_phrases:
            return True
        if any(phrase in text_lower for phrase in idk_phrases):
            return True

        # Short question check (e.g. "What?", "Huh?")
        if len(text_lower) < 5 and "?" in raw_lower:
            return True

        return False

    def handle_socratic_answer(self, text):
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
            # GAP 5: Pass Bloom level, objectives, and mastery criteria to grading
            prompt = get_socratic_grading_prompt(
                self.current_lesson_node["title"],
                self.last_question,
                text,
                context_text=self.current_context,
                bloom_level=self.current_bloom_level,
                learning_objectives=self.current_lesson_node.get("learning_objectives", []),
                mastery_criteria=getattr(self, 'current_mastery_criteria', ''),
            )
            try:
                logging.info(f"LLM Grading Request for: {self.current_lesson_node['title']}")
                content = self._call_llm(prompt, max_tokens=500, timeout=45)
                if content:
                    logging.info(f"LLM Grading Content: {content[:200]}...")
                    # Robust JSON extraction
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0].strip()
                    elif "```" in content:
                        content = content.split("```")[1].split("```")[0].strip()

                    # Try to find JSON object in the response
                    json_match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
                    if json_match:
                        content = json_match.group(0)

                    try:
                        content = re.sub(
                            r'"grade":\s*"Grade\s*(\d)"', r'"grade": \1', content
                        )
                        result = json.loads(content)
                    except Exception as e:
                        grade_match = re.search(r'"grade":\s*(\d)', content)
                        if grade_match:
                            result = {"grade": int(grade_match.group(1))}
                        else:
                            # Last resort: look for any digit that could be a grade
                            any_grade = re.search(r'\bgrade\b.*?(\d)', content, re.IGNORECASE)
                            if any_grade:
                                result = {"grade": int(any_grade.group(1))}
                            else:
                                raise e
                    grade = int(result.get("grade", 3))
                    missing_concepts = result.get("missing_concepts", [])
                    feedback = result.get("feedback", "")

                    # Log to Session Notes
                    if self.current_lesson_node:
                        self.append_session_note(
                            self.current_lesson_node["uid"],
                            f"Question: {self.last_question} | Answer: {text[:50]}... | Grade: {grade} | Reasoning: {result.get('reason', 'N/A')}",
                        )
                else:
                    logging.error("LLM Grading returned None")
                    grade = 3
            except Exception as e:
                logging.warning(f"Grading parse error: {e}")
                grade = 3

        # Store grade for rule-based mode selection (replaces LLM classifier)
        self._last_socratic_grade = grade

        # Multi-question mastery tracking
        self.concept_question_count += 1
        if grade >= 3:
            self.concept_correct_streak += 1
            # GAP 4: Track which question type categories were passed
            q_idx = min(self.socratic_type_index, len(SOCRATIC_QUESTION_TYPES) - 1)
            self.passed_question_types.add(SOCRATIC_QUESTION_TYPES[q_idx]["key"])
        else:
            self.concept_correct_streak = 0

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
                current_type_name = SOCRATIC_QUESTION_TYPES[
                    min(self.socratic_type_index, len(SOCRATIC_QUESTION_TYPES) - 1)
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
                current_type_name = SOCRATIC_QUESTION_TYPES[
                    min(self.socratic_type_index, len(SOCRATIC_QUESTION_TYPES) - 1)
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
            prev_type_name = SOCRATIC_QUESTION_TYPES[
                min(self.socratic_type_index, len(SOCRATIC_QUESTION_TYPES) - 1)
            ]["name"].lower()
            self.socratic_type_index += 1
            if self.socratic_type_index >= len(SOCRATIC_QUESTION_TYPES):
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
                next_type = SOCRATIC_QUESTION_TYPES[self.socratic_type_index]
                feedback_note = f"[SYSTEM NOTE: Student gave an excellent answer. Their feedback: '{feedback}'. " if feedback else "[SYSTEM NOTE: Student answered excellently. "
                self.ask_socratic_question(
                    feedback_note + f"Briefly affirm what they demonstrated about {prev_type_name} — specifically what they got right. Then build on that by asking a {next_type['name'].lower()} question that extends their reasoning.]"
                )

        else:  # Grade == 3 (Good)
            self.play_sound("SUCCESS_CHORD")
            self.socratic_retry_count = 0
            # Fix 9: Capture previous type name for transition bridge
            prev_type_name = SOCRATIC_QUESTION_TYPES[
                min(self.socratic_type_index, len(SOCRATIC_QUESTION_TYPES) - 1)
            ]["name"].lower()
            self.socratic_type_index += 1
            if self.socratic_type_index >= len(SOCRATIC_QUESTION_TYPES):
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
                next_type = SOCRATIC_QUESTION_TYPES[self.socratic_type_index]
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
        try:
            if self.current_lesson_node and self.current_lesson_node.get("uid"):
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
        messages = get_examiner_question_prompt(self.current_card.get("text", ""))
        try:
            raw = self._call_llm(messages, max_tokens=500, timeout=45)
            if raw:
                question = clean_llm_response(raw)
                logging.info(f"LLM Examiner Question: {question[:100]}...")
            else:
                question = f"What can you tell me about {self.current_card['title']}?"
            self.speak(question)
        except Exception as e:
            logging.error(f"LLM Examiner Exception: {e}")
            self.speak(f"Describe {self.current_card['title']}.")
        self.question_start_time = time.time()

    def handle_flashcard_answer(self, text):
        response_time = time.time() - self.question_start_time
        self.question_start_time = 0

        self.send_status_update("Grading...")
        messages = get_examiner_grade_prompt(
            self.last_question, text, self.current_card.get("text", "")
        )
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
                is_correct = str(result.get("grade", "")).upper() == "PASS"
            else:
                logging.error("LLM Flashcard Grading returned None")
                is_correct = True
        except Exception as e:
            logging.error(f"LLM Flashcard Grading Exception: {e}")
            is_correct = True

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
            for act in reversed(activities):
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

        # Handle EPUB upload: extract filepath and derive topic from filename
        if "from epub" in topic and not epub_filepath:
            parts = topic.split("from epub", 1)
            epub_filepath = parts[1].strip()
            # Derive topic from EPUB filename (e.g., "biology_101.epub" → "biology 101")
            epub_name = os.path.basename(epub_filepath).rsplit('.', 1)[0]
            topic = epub_name.replace('_', ' ').replace('-', ' ').strip()
            if not topic:
                topic = "Uploaded Course"

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
                try:
                    hydrator.hydrate(course_uid)
                finally:
                    hydrator.close()

                # BUG-4: Set course status to "ready" after successful hydration
                try:
                    course = self.storage.courses.get_course(course_uid)
                    if course:
                        course["status"] = "ready"
                        self.storage.courses.update_course(course_uid, course)
                        logging.info(f"[PIPELINE] Course {course_uid} status set to 'ready'")
                except Exception as e:
                    logging.warning(f"[PIPELINE] Failed to set course status: {e}")

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
                self.state = "LOBBY"
                self.speak("Course deleted. Returning to lobby.")

            # Update Disk State — use atomic write to prevent corruption
            if os.path.exists(self.state_file):
                with open(self.state_file, "r") as f:
                    full_state = json.load(f)

                courses = full_state.get("courses", {})
                if uid in courses:
                    del courses[uid]

                if full_state.get("last_active_uid") == uid:
                    full_state["last_active_uid"] = None

                self._atomic_write(self.state_file, json.dumps(full_state, indent=2))
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


fsm = MnemosyneFSM()


@app.route("/event", methods=["POST"])
def handle_event():
    event = request.json
    fsm.transition(event)
    return {"status": "ok"}


# SocketIO handler removed (core-logic is REST-only)


@app.route("/state", methods=["GET"])
def get_state():
    return fsm.get_state()


@app.route("/api/schedule/stats", methods=["GET"])
def get_schedule_stats():
    try:
        fsm.storage.schedule.mark_overdue()
        upcoming = fsm.storage.schedule.get_upcoming_count(days=7)
        all_reviews = fsm.storage.schedule.get_scheduled_reviews()
        overdue_curr = len([r for r in all_reviews if r.get("status") == "overdue"])
        completed_curr = len([r for r in all_reviews if r.get("status") == "completed"])
        retention = 100
        if completed_curr + overdue_curr > 0:
            retention = int((completed_curr / (completed_curr + overdue_curr)) * 100)

        streak = 0
        if hasattr(fsm.storage, "activity"):
            streak = fsm.storage.activity.get_streak()

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
    try:
        if month and year:
            import calendar

            start_date = f"{year}-{int(month):02d}-01"
            last_day = calendar.monthrange(int(year), int(month))[1]
            end_date = f"{year}-{int(month):02d}-{last_day}"
            reviews = fsm.storage.schedule.get_scheduled_reviews(
                start_date=start_date, end_date=end_date
            )
        else:
            reviews = fsm.storage.schedule.get_scheduled_reviews()
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
        fsm.storage.schedule.complete_review(review_id)
        return {"status": "ok"}
    except Exception as e:
        logging.error(f"Schedule complete error: {e}")
        return {"error": str(e)}, 500


@app.route("/health", methods=["GET"])
def health():
    try:
        # Check resource usage
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        memory_used_gb = memory.used / (1024**3)

        # Latency: time since last interaction
        latency = time.time() - fsm.last_interaction_time

        return {
            "status": "healthy",
            "timestamp": time.time(),
            "latency_seconds": latency,
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "memory_used_gb": round(memory_used_gb, 2),
            "state": fsm.state,
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
        result = fsm.llm_client.chat_json(prompt_sys, prompt_user, max_tokens=500)
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
        result = fsm.llm_client.chat_json(prompt_sys, prompt_user, max_tokens=500)
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
        result = fsm.llm_client.chat_json(prompt_sys, prompt_user, max_tokens=600)
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
    return jsonify(fsm.creation_status)


@app.route("/api/cancel_creation", methods=["POST"])
def api_cancel_creation():
    """Cancel an in-progress course creation."""
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
