"""Pass 4.2 — the audit has to DECIDE something, not just report.

Until this existed the audit produced a verdict and the course opened anyway.
"Reading a Query Plan" was status=ready, badged "Passed its build checks", with
all four concepts carrying nothing but a title and a worked example — no Core
Explanation, no Misconceptions, no Analogies — and a learner was 25% through it.

WHAT THE GATE DELIBERATELY DOES NOT DO IS LOCK A COURSE OVER ONE BAD CONCEPT.
A false claim already has a better remedy: the concept is withheld, so it
cannot reach anybody, and the other ninety-four are untouched. Blocking the
whole course would cost the learner far more than the defect does.

So it gates on the two things withholding cannot fix: a blocking finding still
being SERVED, and a course with no teachable lesson in it.
"""
import pytest

from services.core.course_builder import ContentHydrator


def _h():
    return ContentHydrator.__new__(ContentHydrator)


def _course(concepts, status="ready"):
    return {"title": "T", "status": status, "modules": [
        {"uid": "m", "title": "M", "units": [
            {"uid": "u", "title": "U", "lessons": [
                {"uid": "l", "title": "L", "concepts": concepts}]}]}]}


def _audit(findings=(), systemic=(), total=4, not_audited=0, audited=None):
    return {"ran": True, "findings": list(findings), "systemic": list(systemic),
            "concepts_total": total, "concepts_not_audited": not_audited,
            "concepts_audited": total - not_audited if audited is None
                                else audited}


def test_a_served_false_claim_stops_the_course_being_ready():
    c = _course([{"uid": "con_1", "title": "A"}])
    st, why = _h()._gate_status(c, _audit(findings=[
        {"concept_uid": "con_1", "severity": "blocking",
         "check": "executable_claims",
         "detail": "says NULLs sort FIRST under ASC"}]))
    assert st == "needs_review"
    assert "contradicts" in why


def test_a_withheld_false_claim_does_not_stop_the_course():
    """Withholding is the better remedy — it removes the falsehood without
    taking the other ninety-four concepts with it."""
    c = _course([{"uid": "con_1", "title": "A", "withheld": True}])
    st, _ = _h()._gate_status(c, _audit(findings=[
        {"concept_uid": "con_1", "severity": "blocking",
         "check": "executable_claims",
         "detail": "says NULLs sort FIRST under ASC"}]))
    assert st == "ready"


def test_a_course_with_no_teachable_lesson_is_gated():
    """The live case: every concept missing every section the tutor reads."""
    c = _course([{"uid": f"con_{i}", "title": str(i)} for i in range(4)])
    st, why = _h()._gate_status(c, _audit(systemic=[
        {"check": "tutor_sections", "concepts": 4, "severity": "serious"}]))
    assert st == "needs_review"
    assert "tutor reads" in why


def test_a_minority_missing_sections_does_not_gate():
    """One bad concept in a hundred must not lock the course — that is the
    defect that made 'partial' mean 'permanently unopenable'."""
    c = _course([{"uid": f"con_{i}", "title": str(i)} for i in range(20)])
    st, _ = _h()._gate_status(c, _audit(
        systemic=[{"check": "tutor_sections", "concepts": 3,
                   "severity": "serious"}], total=20))
    assert st == "ready"


def test_a_concept_with_no_content_gates():
    c = _course([{"uid": "con_1", "title": "A"}])
    st, why = _h()._gate_status(c, _audit(not_audited=1))
    assert st == "needs_review" and "no content" in why


def test_an_audit_that_did_not_run_neither_clears_nor_condemns():
    """It cannot pass a course, and it must not fail one either."""
    c = _course([{"uid": "con_1", "title": "A"}], status="ready")
    st, why = _h()._gate_status(c, {"ran": False, "error": "boom"})
    assert st == "ready"
    assert "did not run" in why


def test_serious_findings_alone_do_not_gate():
    """SQL carries 114 serious findings and is teachable. Gating on those
    would make every course needs_review, which tells a learner nothing."""
    c = _course([{"uid": "con_1", "title": "A"}])
    st, _ = _h()._gate_status(c, _audit(findings=[
        {"concept_uid": "con_1", "severity": "serious",
         "check": "thin_content", "detail": "thin"}]))
    assert st == "ready"


def test_a_blocking_problem_that_is_not_a_false_claim_is_named_correctly():
    """`missing_content` is blocking too. Reporting it as "states something a
    database contradicts" sends the reader looking for a falsehood that was
    never there — which is what this gate did on its first run."""
    c = _course([{"uid": "con_1", "title": "A"}])
    st, why = _h()._gate_status(c, _audit(findings=[
        {"concept_uid": "con_1", "severity": "blocking",
         "check": "missing_content", "detail": "concept has no content file"}]))
    assert st == "needs_review"
    assert "contradicts" not in why, f"misnamed the defect: {why}"
    assert "missing_content" in why


def test_an_audit_that_read_no_content_neither_clears_nor_condemns():
    """Every concept then reports `missing_content` and the gate condemns the
    course on the strength of a storage layer that answered nothing. Same
    situation as the audit not running, same treatment."""
    c = _course([{"uid": "con_1", "title": "A"}], status="ready")
    st, why = _h()._gate_status(c, _audit(audited=0, findings=[
        {"concept_uid": "con_1", "severity": "blocking",
         "check": "missing_content", "detail": "concept has no content file"}]))
    assert st == "ready"
    assert "read no content" in why
