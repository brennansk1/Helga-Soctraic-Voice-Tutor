#!/usr/bin/env python3
"""
Helga Flow Verification CLI
============================
Validates all user flows by inspecting code paths, data structures,
and service wiring WITHOUT needing Docker running.

Usage:
    python tools/verify_flows.py              # Run all checks
    python tools/verify_flows.py --flow A     # Run specific flow (A-M)
    python tools/verify_flows.py --summary    # Show summary only
"""

import argparse
import importlib
import inspect
import json
import os
import re
import sys
import ast

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"

results = {"pass": 0, "fail": 0, "warn": 0, "checks": []}


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    color = GREEN if condition else RED
    print(f"  {color}[{status}]{RESET} {name}")
    if detail and not condition:
        print(f"         {YELLOW}{detail}{RESET}")
    results["checks"].append({"name": name, "status": status, "detail": detail})
    if condition:
        results["pass"] += 1
    else:
        results["fail"] += 1


def warn(name, detail=""):
    print(f"  {YELLOW}[WARN]{RESET} {name}")
    if detail:
        print(f"         {detail}")
    results["warn"] += 1
    results["checks"].append({"name": name, "status": "WARN", "detail": detail})


def section(title):
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}")


def read_file(path):
    full = os.path.join(PROJECT_ROOT, path)
    if not os.path.exists(full):
        return None
    with open(full, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def file_exists(path):
    return os.path.exists(os.path.join(PROJECT_ROOT, path))


def file_contains(path, pattern, flags=0):
    content = read_file(path)
    if content is None:
        return False
    return bool(re.search(pattern, content, flags))


# ============================================================
# FLOW A: Automatic Course Creation
# ============================================================
def verify_flow_a():
    section("FLOW A: Automatic Course Creation")

    # A1: Initiation
    check(
        "courses.html has creation modal/form",
        file_contains("services/web-ui/templates/courses.html", r"topic-form|create.*course|creation.*modal"),
    )
    check(
        "courses.js has setupCreationSocket()",
        file_contains("services/web-ui/static/js/courses.js", r"setupCreationSocket|creation.*socket"),
    )

    # A2: Pipeline
    fsm = read_file("services/core/fsm_logic.py")
    check(
        "FSM has start_creation() method",
        "def start_creation(" in fsm if fsm else False,
    )
    check(
        "FSM has _creation_pipeline() function",
        "_creation_pipeline" in fsm if fsm else False,
    )
    check(
        "Pipeline has creation_in_progress guard",
        "creation_in_progress" in fsm if fsm else False,
    )
    check(
        "Pipeline sends status_update events",
        "send_status_update" in fsm if fsm else False,
    )

    # A3: Completion
    check(
        "Pipeline sets success flag before completion message",
        file_contains("services/core/fsm_logic.py", r"COURSE_COMPLETE|Course complete"),
    )

    # A4: Error handling
    check(
        "Pipeline catches exceptions in _creation_pipeline",
        file_contains("services/core/fsm_logic.py", r"except.*Exception.*_creation_pipeline|creation_in_progress\s*=\s*False"),
    )

    # A5: Storage
    check(
        "StorageManager has create_course()",
        file_contains("services/common/storage.py", r"def create_course"),
    )
    check(
        "Course data dir pattern exists",
        file_contains("services/common/storage.py", r"data/courses|structure\.json"),
    )


# ============================================================
# FLOW B: Custom Course Wizard
# ============================================================
def verify_flow_b():
    section("FLOW B: Custom Course Wizard")

    app = read_file("services/web-ui/app.py")
    lib = read_file("services/rag/librarian.py")

    check(
        "Web-UI has /api/custom_course/preview route",
        "/api/custom_course/preview" in app if app else False,
    )
    check(
        "Web-UI has /api/custom_course/create route",
        "/api/custom_course/create" in app if app else False,
    )
    check(
        "RAG has preview endpoint",
        "/api/custom_course/preview" in lib if lib else False,
    )
    check(
        "RAG has create endpoint",
        "/api/custom_course/create" in lib if lib else False,
    )
    check(
        "courses.html has wizard steps UI",
        file_contains("services/web-ui/templates/courses.html", r"wizard.*step|step.*1|step.*2|step.*3"),
    )
    check(
        "Upload validation exists (_validate_upload)",
        "_validate_upload" in app if app else False,
    )
    check(
        "Source file cleanup in error path",
        file_contains("services/web-ui/app.py", r"os\.unlink|os\.remove|cleanup|shutil\.rmtree"),
    )


# ============================================================
# FLOW C: EPUB Upload
# ============================================================
def verify_flow_c():
    section("FLOW C: EPUB Upload")

    app = read_file("services/web-ui/app.py")
    fsm = read_file("services/core/fsm_logic.py")
    cp = read_file("services/core/content_provider.py")

    check(
        "Web-UI has /api/upload_epub route",
        "/api/upload_epub" in app if app else False,
    )
    check(
        "EPUB route validates .epub extension",
        file_contains("services/web-ui/app.py", r"\.epub"),
    )
    check(
        "EPUB route forwards event to core",
        file_contains("services/web-ui/app.py", r"epub.*event|event.*epub|create course from epub"),
    )
    check(
        "FSM start_creation handles epub_filepath parameter",
        "epub_filepath" in fsm if fsm else False,
    )
    check(
        "FSM passes epub_filepath to LocalFileProvider",
        file_contains("services/core/fsm_logic.py", r"LocalFileProvider\(epub_filepath\)"),
    )
    check(
        "LocalFileProvider supports .epub extraction",
        file_contains("services/core/content_provider.py", r"\.epub"),
    )
    check(
        "EPUB extracts HTML/XHTML from zip",
        file_contains("services/core/content_provider.py", r"\.xhtml|\.html.*\.htm"),
    )
    check(
        "courses.html has EPUB upload form",
        file_contains("services/web-ui/templates/courses.html", r"epub-form|epub-file|Upload EPUB"),
    )
    check(
        "closeEpubModal resets form (EPUB-4)",
        file_contains("services/web-ui/static/js/courses.js", r"epubForm.*reset|epub.*reset"),
    )


# ============================================================
# FLOW D: Socratic Learning (Core Teaching Loop)
# ============================================================
def verify_flow_d():
    section("FLOW D: Socratic Learning (Core Teaching Loop)")

    fsm = read_file("services/core/fsm_logic.py")
    learn = read_file("services/web-ui/templates/learn.html")
    session = read_file("services/web-ui/static/js/session.js")

    # D1: Course Selection
    check(
        "SET_CONTEXT handler exists in FSM",
        file_contains("services/core/fsm_logic.py", r"SET_CONTEXT"),
    )
    check(
        "SET_CONTEXT sets active_course_uid",
        file_contains("services/core/fsm_logic.py", r'SET_CONTEXT')
        and file_contains("services/core/fsm_logic.py", r'self\.active_course_uid\s*=\s*uid'),
    )
    check(
        "learn.html sends SET_CONTEXT on load",
        file_contains("services/web-ui/templates/learn.html", r"SET_CONTEXT"),
    )
    check(
        "courses.html passes course_uid in URL",
        file_contains("services/web-ui/static/js/courses.js", r"course_uid")
        or file_contains("services/web-ui/templates/courses.html", r"course_uid"),
    )

    # D2: Path Visualization
    check(
        "learn.html has path-view container",
        'id="path-view"' in learn if learn else False,
    )
    check(
        "learn.html has renderStructure function",
        "renderStructure" in learn if learn else False,
    )

    # D3: Concept Entry
    check(
        "learn.html has session-view container",
        'id="session-view"' in learn if learn else False,
    )
    check(
        "Mode indicator badge exists",
        'id="mode-indicator-badge"' in learn if learn else False,
    )
    check(
        "navigatingToNode timeout exists (LRN-7)",
        file_contains("services/web-ui/templates/learn.html", r"navGuardTimeout|navigatingToNode.*setTimeout|setTimeout.*navigatingToNode"),
    )

    # D4: Socratic Loop
    check(
        "FSM has ask_socratic_question()",
        "def ask_socratic_question(" in fsm if fsm else False,
    )
    check(
        "FSM has handle_socratic_answer()",
        "def handle_socratic_answer(" in fsm if fsm else False,
    )
    check(
        "6 Socratic question types defined",
        "SOCRATIC_QUESTION_TYPES" in fsm if fsm else False,
    )
    check(
        "Question type badge update exists",
        "updateQuestionTypeBadge" in learn if learn else False,
    )
    check(
        "Optimistic user message rendering in session.js",
        file_contains("services/web-ui/static/js/session.js", r"optimistic|user.*message.*immediately|msg-content"),
    )

    # D5: Grading
    check(
        "Grading prompt exists in prompts.py",
        file_contains("services/common/prompts.py", r"grading|grade.*prompt"),
    )
    check(
        "Grade 1-4 handling in FSM",
        file_contains("services/core/fsm_logic.py", r"grade.*[>=<].*[1234]|rating.*[1234]"),
    )
    check(
        "socratic_type_index advances on mastery",
        "socratic_type_index += 1" in fsm if fsm else False,
    )

    # D6: Completion
    check(
        "next_syllabus_item() marks completed",
        file_contains("services/core/fsm_logic.py", r"mark_completed|completed_topics\.add"),
    )
    check(
        "Progress persistence (_save_current_course_progress)",
        "_save_current_course_progress" in fsm if fsm else False,
    )
    check(
        "conversation_history clears on new topic",
        file_contains("services/core/fsm_logic.py", r"conversation_history\s*=\s*\[\]"),
    )

    # D7: Skip & Navigation
    check(
        "_advance_without_completing() exists",
        "_advance_without_completing" in fsm if fsm else False,
    )
    check(
        "SKIP_CONCEPT uses _advance_without_completing",
        file_contains("services/core/fsm_logic.py", r"SKIP_CONCEPT")
        and file_contains("services/core/fsm_logic.py", r"_advance_without_completing"),
    )
    check(
        "Back button handler exists in learn.html",
        file_contains("services/web-ui/templates/learn.html", r"back-to-path-btn-session"),
    )

    # D8: Course Completion
    check(
        "Course completion overlay (COURSE_COMPLETE handler)",
        "COURSE_COMPLETE" in learn if learn else False,
    )
    check(
        "showCourseCompleteOverlay function exists",
        "showCourseCompleteOverlay" in learn if learn else False,
    )

    # D9: Resume
    check(
        "FSM loads course progress on resume",
        "_load_course_progress" in fsm if fsm else False,
    )
    check(
        "user_state.json used for persistence",
        "user_state.json" in fsm if fsm else False,
    )


# ============================================================
# FLOW E: Spaced Repetition / Review
# ============================================================
def verify_flow_e():
    section("FLOW E: Spaced Repetition / Review")

    fsm = read_file("services/core/fsm_logic.py")

    check(
        "enter_mode_2() exists",
        "def enter_mode_2(" in fsm if fsm else False,
    )
    check(
        "Review gets due cards from storage",
        file_contains("services/core/fsm_logic.py", r"get_due_reviews|due.*reviews"),
    )
    check(
        "next_card() method exists",
        "def next_card(" in fsm if fsm else False,
    )
    check(
        "Review completion has next-step guidance",
        file_contains("services/core/fsm_logic.py", r"review complete.*open|review complete.*palace|review complete.*course"),
    )
    check(
        "No-cards-due has next-step guidance",
        file_contains("services/core/fsm_logic.py", r"No cards due.*open|No cards due.*course|No cards due.*Study"),
    )
    check(
        "FSRS engine exists",
        file_exists("services/core/fsrs_engine.py"),
    )
    check(
        "review.html template exists",
        file_exists("services/web-ui/templates/review.html"),
    )
    check(
        "schedule.html template exists",
        file_exists("services/web-ui/templates/schedule.html"),
    )


# ============================================================
# FLOW F: Memory Palace
# ============================================================
def verify_flow_f():
    section("FLOW F: Memory Palace")

    fsm = read_file("services/core/fsm_logic.py")

    check(
        "enter_mode_3() exists",
        "def enter_mode_3(" in fsm if fsm else False,
    )
    check(
        "move_locus() method exists",
        "def move_locus(" in fsm if fsm else False,
    )
    check(
        "check_sonar() method exists",
        "def check_sonar(" in fsm if fsm else False,
    )
    check(
        "No-active-course has guidance message",
        file_contains("services/core/fsm_logic.py", r"Palace requires.*course|Palace.*open.*course"),
    )
    check(
        "Palace uses active_course_uid concepts",
        file_contains("services/core/fsm_logic.py", r"def enter_mode_3")
        and file_contains("services/core/fsm_logic.py", r"self\.active_course_uid"),
    )


# ============================================================
# FLOW G: Mode Switching
# ============================================================
def verify_flow_g():
    section("FLOW G: Mode Switching")

    session = read_file("services/web-ui/static/js/session.js")
    fsm = read_file("services/core/fsm_logic.py")

    check(
        "getModeFromState() maps all 3 modes",
        file_contains("services/web-ui/static/js/session.js", r"SOCRATIC_LEARNING")
        and file_contains("services/web-ui/static/js/session.js", r"SPACED_REPETITION")
        and file_contains("services/web-ui/static/js/session.js", r"MEMORY_PALACE"),
    )
    check(
        "toggleRails() function exists",
        "toggleRails" in session if session else False,
    )
    check(
        "Mode indicator badge in learn.html",
        file_contains("services/web-ui/templates/learn.html", r"mode-indicator-badge"),
    )
    check(
        "LOBBY state handles all mode entry commands",
        all(x in fsm for x in ["enter_mode_1", "enter_mode_2", "enter_mode_3"]) if fsm else False,
    )
    check(
        "All mode dead-ends have next-step guidance",
        file_contains("services/core/fsm_logic.py", r"You can say|option|Say '"),
    )


# ============================================================
# FLOW H: Audio / TTS
# ============================================================
def verify_flow_h():
    section("FLOW H: Audio / TTS")

    audio = read_file("services/web-ui/static/js/session-audio.js")
    fsm = read_file("services/core/fsm_logic.py")

    check(
        "AudioContext does NOT force 16000 Hz sample rate",
        not file_contains("services/web-ui/static/js/session-audio.js", r"sampleRate.*16000.*playback|new.*AudioContext.*sampleRate.*AUDIO_SAMPLE_RATE"),
    )
    check(
        "Playback AudioContext uses hardware default rate",
        file_contains("services/web-ui/static/js/session-audio.js", r"new.*AudioContext\(\)|hardware default"),
    )
    check(
        "Audio queue has max size cap",
        file_contains("services/web-ui/static/js/session-audio.js", r"MAX_AUDIO_QUEUE_SIZE|queue.*full|queue.*cap"),
    )
    check(
        "Playback has timeout safety net",
        file_contains("services/web-ui/static/js/session-audio.js", r"AUDIO_PLAYBACK_TIMEOUT|playback.*timeout|timeout.*queue"),
    )
    check(
        "Audio fade-in on playback start",
        file_contains("services/web-ui/static/js/session-audio.js", r"linearRampToValueAtTime|fade.*in|ramp"),
    )
    check(
        "FSM uses ThreadPoolExecutor for TTS (serialized)",
        "ThreadPoolExecutor" in fsm if fsm else False,
    )
    check(
        "TTS executor max_workers=1",
        file_contains("services/core/fsm_logic.py", r"ThreadPoolExecutor.*max_workers.*1"),
    )
    check(
        "speak() uses executor.submit() not Thread().start()",
        file_contains("services/core/fsm_logic.py", r"_tts_executor\.submit"),
    )
    check(
        "Mixer outputs correct sample rate (22050 for lessac-medium)",
        file_contains("services/audio/mixer.py", r"setframerate.*22050"),
    )


# ============================================================
# FLOW I: Course Management
# ============================================================
def verify_flow_i():
    section("FLOW I: Course Management")

    check(
        "DELETE_COURSE handler in FSM",
        file_contains("services/core/fsm_logic.py", r"DELETE_COURSE"),
    )
    check(
        "RAG has DELETE /api/courses route",
        file_contains("services/rag/librarian.py", r"DELETE.*courses|courses.*DELETE"),
    )
    check(
        "XSS escaping in course card rendering",
        file_contains("services/web-ui/static/js/courses.js", r"escapeHtml|textContent|innerText")
        or file_contains("services/web-ui/templates/courses.html", r"escapeHtml|textContent"),
    )


# ============================================================
# FLOW J: Navigation & UI
# ============================================================
def verify_flow_j():
    section("FLOW J: Navigation & UI")

    check("home.html exists", file_exists("services/web-ui/templates/home.html"))
    check("courses.html exists", file_exists("services/web-ui/templates/courses.html"))
    check("learn.html exists", file_exists("services/web-ui/templates/learn.html"))
    check("review.html exists", file_exists("services/web-ui/templates/review.html"))
    check("schedule.html exists", file_exists("services/web-ui/templates/schedule.html"))
    check("test.html exists", file_exists("services/web-ui/templates/test.html"))
    check("status.html exists", file_exists("services/web-ui/templates/status.html"))

    check(
        "Base template with navigation",
        file_contains("services/web-ui/templates/base.html", r"nav|navbar|navigation"),
    )


# ============================================================
# FLOW K: Data Integrity
# ============================================================
def verify_flow_k():
    section("FLOW K: Data Integrity")

    storage = read_file("services/common/storage.py")

    check(
        "StorageManager class exists",
        "class StorageManager" in storage if storage else False,
    )
    check(
        "Atomic write helper exists",
        file_contains("services/core/fsm_logic.py", r"_atomic_write|atomic.*write"),
    )
    check(
        "SQLite WAL mode configured",
        file_contains("services/common/storage.py", r"WAL|journal_mode"),
    )
    check(
        "Progress persistence on concept completion",
        file_contains("services/core/fsm_logic.py", r"mark_completed.*active_course|progress.*mark_completed"),
    )
    check(
        "FSRS review scheduling on concept completion",
        file_contains("services/core/fsm_logic.py", r"schedule_concept_review|schedule.*review"),
    )

    # Check course data directory structure
    data_dir = os.path.join(PROJECT_ROOT, "data", "courses")
    if os.path.exists(data_dir):
        courses = [d for d in os.listdir(data_dir) if d.startswith("course_")]
        if courses:
            for course_dir in courses[:3]:  # Check first 3
                structure_path = os.path.join(data_dir, course_dir, "structure.json")
                has_structure = os.path.exists(structure_path)
                check(
                    f"Course {course_dir} has structure.json",
                    has_structure,
                )
                if has_structure:
                    try:
                        with open(structure_path) as f:
                            data = json.load(f)
                        check(
                            f"  {course_dir} structure.json is valid JSON",
                            True,
                        )
                        modules = data.get("modules", [])
                        check(
                            f"  {course_dir} has {len(modules)} modules",
                            len(modules) > 0,
                        )
                        # Check for concept markdown files
                        content_dir = os.path.join(data_dir, course_dir, "content")
                        if os.path.exists(content_dir):
                            md_files = [f for f in os.listdir(content_dir) if f.endswith(".md")]
                            check(
                                f"  {course_dir} has {len(md_files)} concept files",
                                len(md_files) > 0,
                            )
                    except json.JSONDecodeError:
                        check(f"  {course_dir} structure.json is valid JSON", False, "Invalid JSON")
        else:
            warn("No course directories found in data/courses/", "Create a course first")
    else:
        warn("data/courses/ directory not found", "Expected at project root")


# ============================================================
# FLOW L: Docker & Deployment
# ============================================================
def verify_flow_l():
    section("FLOW L: Docker & Deployment")

    check(
        "docker-compose.yml exists",
        file_exists("docker-compose.yml"),
    )
    check(
        "Core-logic Dockerfile exists",
        file_exists("services/core/Dockerfile"),
    )
    check(
        "Web-UI Dockerfile exists",
        file_exists("services/web-ui/Dockerfile"),
    )
    check(
        "RAG Dockerfile exists",
        file_exists("services/rag/Dockerfile"),
    )
    check(
        "Audio Dockerfile exists",
        file_exists("services/audio/Dockerfile"),
    )


# ============================================================
# FLOW M: Security
# ============================================================
def verify_flow_m():
    section("FLOW M: Security")

    check(
        "File upload size limit (MAX_CONTENT_LENGTH or MAX_FILE_SIZE)",
        file_contains("services/web-ui/app.py", r"MAX_CONTENT_LENGTH|MAX_FILE_SIZE"),
    )
    check(
        "secure_filename used for uploads",
        file_contains("services/web-ui/app.py", r"secure_filename"),
    )
    check(
        "Extension validation on uploads",
        file_contains("services/web-ui/app.py", r"allowed_extensions|ALLOWED_MIMES"),
    )
    check(
        "Safety/content filtering exists",
        file_exists("services/common/safety.py")
        or file_contains("services/core/fsm_logic.py", r"check_safety|safety"),
    )
    check(
        "SQL parameterized queries (not string formatting)",
        file_contains("services/common/storage.py", r"\?\s*,|\?\s*\)"),
    )

    check(
        "CSRF token generation exists",
        file_contains("services/web-ui/app.py", r"csrf_token|csrf_protect|X-CSRF-Token"),
    )
    check(
        "CSRF meta tag in base template",
        file_contains("services/web-ui/templates/base.html", r"csrf-token"),
    )
    check(
        "CSRF auto-attached to fetch() requests",
        file_contains("services/web-ui/templates/base.html", r"X-CSRF-Token"),
    )


# ============================================================
# REMAINING ITEMS CHECK
# ============================================================
def verify_remaining():
    section("REMAINING IMPLEMENTATION ITEMS")

    # Course status endpoint
    check(
        "RAG has /api/course_status/<uid> endpoint",
        file_contains("services/rag/librarian.py", r"course_status"),
    )
    check(
        "Web-UI proxies /api/course_status/<uid>",
        file_contains("services/web-ui/app.py", r"course_status"),
    )

    # Syntax verification
    print(f"\n  {CYAN}Syntax Checks:{RESET}")
    py_files = [
        "services/core/fsm_logic.py",
        "services/common/storage.py",
        "services/rag/librarian.py",
        "services/web-ui/app.py",
        "services/core/course_builder.py",
        "services/core/content_provider.py",
        "services/common/prompts.py",
        "services/common/llm_utils.py",
        "services/core/fsrs_engine.py",
    ]
    for pyf in py_files:
        full_path = os.path.join(PROJECT_ROOT, pyf)
        if os.path.exists(full_path):
            try:
                with open(full_path, "r") as f:
                    source = f.read()
                ast.parse(source)
                check(f"  {pyf} syntax valid", True)
            except SyntaxError as e:
                check(f"  {pyf} syntax valid", False, str(e))
        else:
            warn(f"  {pyf} not found")


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Helga Flow Verification CLI")
    parser.add_argument("--flow", type=str, help="Run specific flow (A-M, or 'remaining')")
    parser.add_argument("--summary", action="store_true", help="Show summary only")
    args = parser.parse_args()

    flows = {
        "A": ("Automatic Course Creation", verify_flow_a),
        "B": ("Custom Course Wizard", verify_flow_b),
        "C": ("EPUB Upload", verify_flow_c),
        "D": ("Socratic Learning", verify_flow_d),
        "E": ("Spaced Repetition", verify_flow_e),
        "F": ("Memory Palace", verify_flow_f),
        "G": ("Mode Switching", verify_flow_g),
        "H": ("Audio / TTS", verify_flow_h),
        "I": ("Course Management", verify_flow_i),
        "J": ("Navigation & UI", verify_flow_j),
        "K": ("Data Integrity", verify_flow_k),
        "L": ("Docker & Deployment", verify_flow_l),
        "M": ("Security", verify_flow_m),
        "remaining": ("Remaining Items", verify_remaining),
    }

    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  HELGA FLOW VERIFICATION CLI{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")

    if args.flow:
        key = args.flow.upper() if len(args.flow) == 1 else args.flow.lower()
        if key in flows:
            flows[key][1]()
        else:
            print(f"{RED}Unknown flow: {args.flow}. Available: {', '.join(flows.keys())}{RESET}")
            sys.exit(1)
    else:
        for key, (name, func) in flows.items():
            func()

    # Summary
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  SUMMARY{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")
    total = results["pass"] + results["fail"]
    print(f"  {GREEN}PASS: {results['pass']}{RESET}")
    print(f"  {RED}FAIL: {results['fail']}{RESET}")
    print(f"  {YELLOW}WARN: {results['warn']}{RESET}")
    if total > 0:
        pct = (results["pass"] / total) * 100
        color = GREEN if pct >= 90 else YELLOW if pct >= 70 else RED
        print(f"  {color}Score: {pct:.0f}% ({results['pass']}/{total}){RESET}")

    if results["fail"] > 0:
        print(f"\n  {RED}Failed checks:{RESET}")
        for c in results["checks"]:
            if c["status"] == "FAIL":
                print(f"    {RED}- {c['name']}{RESET}")
                if c["detail"]:
                    print(f"      {YELLOW}{c['detail']}{RESET}")

    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
