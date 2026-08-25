"""What the local model is given when it finishes somebody else's course.

A handback is the case with the LEAST context: the concepts left behind are
titles in a structure someone else designed. Two things have to reach that
write or it is guesswork — the learner's brief, and the research service.
"""
import inspect
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------------ the brief

def test_the_hydrator_reads_the_brief_off_the_course():
    """Derived, not threaded. There are seven ContentHydrator call sites and
    the one that matters most — the resume — is in another service."""
    src = _read("services", "core", "course_builder.py")
    i = src.find("def hydrate(self, course_uid")
    assert i > 0
    body = src[i:i + 3000]
    assert 'course.get("learner_context")' in body, \
        "hydrate() never looks for a brief on the course"


def test_an_explicit_brief_still_wins():
    from services.core.course_builder import ContentHydrator
    assert "learner_context" in inspect.signature(ContentHydrator.__init__).parameters


def test_both_writers_persist_it_onto_the_course():
    builder = _read("services", "core", "course_builder.py")
    i = builder.find('"teaching_style": self.teaching_style,')
    assert i > 0 and '"learner_context"' in builder[i:i + 500], \
        "the local builder uses the brief and never stores it"

    pipeline = _read("services", "rag", "pipeline_api.py")
    i = pipeline.find('"teaching_domain": data.get("teaching_domain")')
    assert i > 0 and '"learner_context"' in pipeline[i:i + 700], \
        "an externally authored course cannot carry a brief into its handback"


def test_the_brief_reaches_the_prompt_that_writes_the_body():
    src = _read("services", "core", "course_builder.py")
    i = src.find("Expert Educational Content Architect specializing in")
    assert i > 0
    window = src[max(0, i - 400):i + 700]
    assert "learner_context" in window or "brief" in window, \
        "the body prompt never sees the brief"


# --------------------------------------------------------------- the research

def test_the_research_timeout_is_one_measured_constant():
    """It was 15s and 20s at two call sites. Measured cold latency on the live
    service was 4s, 10s and 37s, and ~85s for two concurrent — so research was
    abandoned on exactly the concepts that needed it most, while the service
    finished and cached the answer nobody collected."""
    src = _read("services", "core", "course_builder.py")
    assert "RESEARCH_TIMEOUT_S" in src
    assert src.count("timeout=RESEARCH_TIMEOUT_S") >= 2, \
        "a research call still carries a hardcoded timeout"
    assert "timeout=15,\n                )" not in src

    from services.core.course_builder import RESEARCH_TIMEOUT_S
    assert RESEARCH_TIMEOUT_S >= 60, (
        f"{RESEARCH_TIMEOUT_S}s is below the measured cold distribution; "
        f"research will be abandoned and the concept written llm-only")


def test_the_rag_service_logs_at_info():
    """The whole handback path runs in rag, which configured no logging at
    all — so the root default of WARNING applied and every progress line from
    the hydrator was discarded. A hydration that wrote nothing was
    indistinguishable from one that never started."""
    src = _read("services", "rag", "librarian.py")
    assert "basicConfig" in src, "the rag service configures no logging"
    i = src.find("basicConfig")
    assert "LOG_LEVEL" in src[i:i + 400]


# ---------------------------------------------------- the contract it is judged by

def test_hydration_uses_the_courses_own_mastery():
    """Written to one bar, judged by another.

    The resume path builds `ContentHydrator(course_depth=3)` with no mastery,
    so a course at mastery 2 was hydrated against the mastery-3 contract
    (320-1500 words) and then failed at finalize against mastery 2's 200-1300.
    Measured: a concept came back at 1306 words, reported "too long for
    Understanding" — after a retry loop that had been checking a different bar
    the whole time.
    """
    src = _read("services", "core", "course_builder.py")
    i = src.find("def hydrate(self, course_uid")
    assert i > 0
    body = src[i:i + 4000]
    assert 'course.get("mastery")' in body or 'course["mastery"]' in body, \
        "hydrate() never consults the mastery the course declares"


def test_a_caller_that_states_mastery_still_wins():
    from services.core.course_builder import ContentHydrator
    h = ContentHydrator(mastery=5, course_depth=2)
    assert h.mastery_level == 5
    assert h._mastery_was_given is True
    # and one that says nothing is marked as not having said it
    assert ContentHydrator(course_depth=2)._mastery_was_given is False
