"""A subject word is not a brief — and a brief that stops halfway is worse.

Given only "SQL", the builder produced modules on the *history of the standard*
and on *interoperability*. The fix is a context box; the risk is this project's
signature defect — the box exists, the value is captured, and somewhere between
the form and the prompt it is dropped, so the learner types a paragraph that
changes nothing.

These tests walk the real path rather than the pieces: form field → posted
payload → FSM → SkeletonBuilder → the text of the prompt the model is given.
"""
import ast
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------- the form

def test_the_create_page_has_a_context_box():
    html = _read("services", "web-ui", "templates", "create.html")
    assert 'id="context"' in html, "no context field on the create page"


def test_the_carousel_posts_what_the_learner_typed():
    js = _read("services", "web-ui", "static", "js", "create.js")
    assert "state.context" in js, "the box is never read"
    # Both create routes — a single course and a degree — must carry it.
    assert re.search(r"context:\s*state\.context", js), \
        "context is read but never posted"
    assert js.count("context: state.context.trim()") >= 2, (
        "only one of the two create paths posts the context; the user asked "
        "for courses AND degrees")


# ---------------------------------------------------------------- the FSM

def test_the_fsm_reads_the_creation_payload():
    """The carousel's own payload used to be parsed for a sentence and the
    rest thrown away, so mastery and context both died here."""
    src = _read("services", "core", "fsm_logic.py")
    i = src.find('elif "create" in text_lc:')
    assert i > 0
    branch = src[i:i + 1800]
    assert "learner_context" in branch, "context never leaves the payload"
    for dial in ("scope", "mastery", "starting_from"):
        assert dial in branch, f"{dial} is still dropped on the UI path"


def test_every_builder_is_constructed_with_the_context():
    """Two SkeletonBuilder call sites exist; one carrying the brief and one
    not is indistinguishable from working, because both build a course."""
    src = _read("services", "core", "fsm_logic.py")
    tree = ast.parse(src)
    sites = [(n.lineno, {k.arg for k in n.keywords})
             for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "SkeletonBuilder"]
    assert sites, "no SkeletonBuilder call found — this test is looking at the wrong thing"
    missing = [ln for ln, kw in sites if "learner_context" not in kw]
    assert not missing, f"SkeletonBuilder built without the brief at line(s) {missing}"


def test_the_context_survives_the_pending_params_delete():
    """It did not. The params were deleted at the top of the pipeline and the
    builder further down re-read the attribute that had just been removed, so
    the researched path — every UI-built course — got None."""
    src = _read("services", "core", "fsm_logic.py")
    tree = ast.parse(src)
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        deletes = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Delete)
                   for t in n.targets
                   if isinstance(t, ast.Attribute)
                   and t.attr == "_pending_course_params"]
        if not deletes:
            continue
        gone_after = min(deletes)
        reads = [n.lineno for n in ast.walk(fn)
                 if isinstance(n, ast.Constant)
                 and n.value == "_pending_course_params"
                 and n.lineno > gone_after]
        assert not reads, (
            f"{fn.name}() reads _pending_course_params at line(s) {reads}, "
            f"after deleting it at {gone_after} — it will be empty")


# ------------------------------------------------------------- the prompt

def test_the_module_prompt_actually_contains_it():
    from services.core.course_builder import SkeletonBuilder
    sb = SkeletonBuilder(providers=[],
                         learner_context="only window functions, for analytics")
    assert sb.learner_context == "only window functions, for analytics"

    src = _read("services", "core", "course_builder.py")
    i = src.find("PROGRESSIVE modules for a course")
    assert i > 0, "the module prompt moved; this test no longer proves anything"
    assert "self.learner_context" in src[i - 1200:i + 200], (
        "the context is stored on the builder but never reaches the prompt")


def test_a_blank_context_adds_nothing_to_the_prompt():
    """An empty box must not inject an empty quotation for the model to
    interpret — '""' reads as 'the learner wants nothing'."""
    from services.core.course_builder import SkeletonBuilder
    for blank in (None, "", "   "):
        assert SkeletonBuilder(providers=[], learner_context=blank).learner_context == ""


# ------------------------------------------------------------- the degree

def test_degree_planning_takes_and_keeps_the_brief():
    import inspect
    from services.core import program

    for fn in (program.plan_degree, program.source_degree_slots,
               program.propose_slot_subjects):
        assert "context" in inspect.signature(fn).parameters, \
            f"{fn.__name__} cannot be told what the learner wants"

    src = _read("services", "core", "program.py")
    i = src.find("Name the actual courses in a real")
    assert i > 0
    assert "context" in src[i - 600:i], \
        "the degree course-list prompt never sees the brief"
    assert 'plan["learner_context"]' in src, (
        "the brief is used once at planning time and lost — every course in "
        "the degree is then built from its title alone")


def test_a_programme_course_build_inherits_the_brief():
    """/choose is where a degree's course actually starts building."""
    src = _read("services", "core", "fsm_logic.py")
    i = src.find('"scope": 3, "mastery": 3, "starting_from": 1,')
    assert i > 0, "the programme handback moved"
    assert "learner_context" in src[i:i + 600], \
        "a course built from a degree slot does not inherit the degree's brief"
