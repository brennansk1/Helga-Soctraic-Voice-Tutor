"""Over-stretch detection: is there enough subject to fill the requested shape?

Calibrated against real briefs 2026-08-18:

    Linear Algebra      77 chapters  ratio 3.42  ok
    Biology             98 chapters  ratio 4.36  ok
    D&D  (1 course)     10 chapters  ratio 0.44  stretched   -> suggests 60 concepts
    D&D  (40 courses)   10 chapters  ratio 0.01  unsupported -> suggests 1 course

A detector that fires on Calculus is broken; one that never fires is decoration.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.core.scope_fit import assess_scope, describe  # noqa: E402


def _brief(chapters, sources=1, degraded=False, found=True):
    return {"chapter_count": chapters, "structural_sources": sources,
            "degraded": degraded, "found": found}


class TestCalibration(unittest.TestCase):
    def test_a_real_subject_at_course_scope_does_not_fire(self):
        """Linear Algebra: 77 chapters against 135 concepts."""
        assert assess_scope(_brief(77), 135)["verdict"] == "ok"

    def test_biology_does_not_fire(self):
        assert assess_scope(_brief(98), 135)["verdict"] == "ok"

    def test_thin_subject_at_course_scope_is_stretched(self):
        a = assess_scope(_brief(10), 135)
        assert a["verdict"] == "stretched"
        assert a["suggested_concepts"] == 60

    def test_thin_subject_at_degree_scope_is_unsupported(self):
        """The D&D master's — the case this exists for."""
        a = assess_scope(_brief(10), 5400, requested_courses=40)
        assert a["verdict"] == "unsupported"
        assert a["suggested_courses"] == 1

    def test_thin_subject_at_small_scope_passes(self):
        """Sourceless or thin is NOT the same as over-stretched. A short
        certificate in a niche practice is a legitimate thing to build."""
        assert assess_scope(_brief(10), 60)["verdict"] == "ok"


class TestDegradedSuppression(unittest.TestCase):
    """The rule that matters most. Thin evidence from a degraded brief means
    'we could not look', not 'the subject is thin' — and telling a learner their
    subject is too small when Wikimedia was throttling is the absent-vs-zero
    error delivered straight to a user."""

    def test_degraded_brief_never_warns(self):
        a = assess_scope(_brief(0, sources=0, degraded=True), 5400,
                         requested_courses=40)
        assert a["verdict"] == "unknown"
        assert "could not look" in a["reason"]

    def test_degraded_produces_no_learner_facing_message(self):
        a = assess_scope(_brief(0, sources=0, degraded=True), 5400, 40)
        assert describe(a) == ""

    def test_no_brief_at_all_is_unknown_not_unsupported(self):
        assert assess_scope(None, 135)["verdict"] == "unknown"
        assert assess_scope({}, 135)["verdict"] == "unknown"


class TestSourcelessHandling(unittest.TestCase):
    def test_no_syllabus_at_course_scope_is_not_an_accusation(self):
        """Plenty of real practices have no open syllabus."""
        a = assess_scope(_brief(0, sources=0), 135, requested_courses=1)
        assert a["verdict"] == "unknown"

    def test_no_syllabus_at_degree_scope_is_unsupported(self):
        a = assess_scope(_brief(0, sources=0), 5400, requested_courses=40)
        assert a["verdict"] == "unsupported"


class TestMessage(unittest.TestCase):
    def test_ok_says_nothing(self):
        assert describe(assess_scope(_brief(77), 135)) == ""

    def test_message_offers_a_concrete_alternative(self):
        """A warning that can only be accepted or cancelled trains people to
        accept it. The right-sized option is the actionable part."""
        msg = describe(assess_scope(_brief(10), 5400, requested_courses=40))
        assert "40 courses" in msg and "roughly 1" in msg
        assert "broaden" in msg and "continue as asked" in msg


if __name__ == "__main__":
    unittest.main()


class TestProgrammeScopeAsksThePerCourseQuestion(unittest.TestCase):
    """A sweep across real subjects exposed a mis-framed question, not a broken
    detector: Linear Algebra and Chemistry both read "unsupported" at degree
    scope, which is nonsense for two of the best-documented subjects there are.

    A bachelor's in Biology is not forty courses of biology chapters — it spans
    gen-ed, core and electives, each with its own subject and its own evidence.
    One subject's brief must be compared against what is asked of THAT subject.
    """

    def test_a_well_documented_subject_supports_several_courses(self):
        from services.core.scope_fit import supportable_courses
        # Linear Algebra measured at 77 chapters
        assert supportable_courses(_brief(77)) >= 3.0

    def test_a_thin_subject_supports_less_than_one(self):
        from services.core.scope_fit import supportable_courses
        assert supportable_courses(_brief(10)) < 1.0

    def test_real_subjects_pass_at_realistic_course_counts(self):
        """The calibration that matters: these must not fire."""
        for chapters in (77, 98, 117, 62):        # LinAlg, Bio, Chem, History
            assert assess_scope(_brief(chapters), 144, 1)["verdict"] == "ok"
            assert assess_scope(_brief(chapters), 3 * 144, 3)["verdict"] == "ok"

    def test_a_thin_subject_fires_at_a_sequence_but_is_only_stretched_at_one(self):
        assert assess_scope(_brief(10), 144, 1)["verdict"] == "stretched"
        assert assess_scope(_brief(10), 3 * 144, 3)["verdict"] == "unsupported"

    def test_no_evidence_is_unknown_not_an_accusation(self):
        """Competitive Yo-Yo returned 0 chapters. That is "we found nothing",
        not "this subject is too small to teach" — a real practice with no open
        syllabus must not be told it is not a subject."""
        assert assess_scope(_brief(0, sources=0), 144, 1)["verdict"] == "unknown"

    def test_degraded_research_never_reports_a_course_count(self):
        from services.core.scope_fit import supportable_courses
        assert supportable_courses(_brief(0, degraded=True)) is None
