"""General education is a choice, not an imposition.

A real degree makes you take composition and a lab science whether or not they
touch your major. Copying that wholesale gives a Dungeon Mastering associate
seven courses of English and Calculus -- nobody's reason for being here -- and
asks a learner who already holds a degree to sit through them a second time.

Silently dropping them is the other wrong answer: counting credit hours is only
worth doing because it compares with a real programme, and a 28-course
"bachelor's" is not a bachelor's. So there are three answers, the plan records
which was chosen, and the page says so.
"""
import pytest

from services.core.program import (
    GEN_ED_DONE, GEN_ED_INCLUDE, GEN_ED_MODES, GEN_ED_SKIP, TEMPLATES,
    ProgramError, available_courses, plan_from_template, programme_size,
    template_for,
)


def _plan(mode, template="bachelors", subject="Dungeon Mastering"):
    p = plan_from_template(subject, template, general_education=mode)
    p["general_education"] = mode
    p["template"] = template
    if mode == GEN_ED_DONE:
        for c in p["courses"]:
            if c.get("slot") == "gen_ed":
                c["completed"] = True
                c["transferred"] = True
    return p


def _slots(plan):
    out = {}
    for c in plan["courses"]:
        out[c["slot"]] = out.get(c["slot"], 0) + 1
    return out


@pytest.mark.parametrize("template", ["associate", "bachelors"])
def test_include_is_unchanged(template):
    """The default must not move: everyone who expressed no preference."""
    assert _slots(_plan(GEN_ED_INCLUDE, template)) == TEMPLATES[template]["slots"]


@pytest.mark.parametrize("template", ["associate", "bachelors"])
def test_skip_removes_exactly_the_general_education(template):
    plan = _plan(GEN_ED_SKIP, template)
    slots = _slots(plan)
    assert "gen_ed" not in slots
    expected = {k: v for k, v in TEMPLATES[template]["slots"].items()
                if k != "gen_ed"}
    assert slots == expected, "skip must not disturb the other slots"
    assert len(plan["courses"]) == (
        TEMPLATES[template]["courses"] - TEMPLATES[template]["slots"]["gen_ed"])


@pytest.mark.parametrize("template", ["associate", "bachelors"])
def test_transferred_keeps_them_and_marks_them_done(template):
    """'Already done' is a different claim from 'not part of this degree'."""
    plan = _plan(GEN_ED_DONE, template)
    assert _slots(plan) == TEMPLATES[template]["slots"]
    gen = [c for c in plan["courses"] if c["slot"] == "gen_ed"]
    assert gen and all(c["completed"] for c in gen)
    assert len(plan["courses"]) == TEMPLATES[template]["courses"]


def test_transferred_does_not_leave_them_to_be_studied():
    """They must not show up as work still to do."""
    plan = _plan(GEN_ED_DONE)
    titles = {c["title"] for c in available_courses(plan)}
    gen = {c["title"] for c in plan["courses"] if c["slot"] == "gen_ed"}
    assert not (titles & gen)


def test_skip_reports_the_smaller_total_AND_what_it_is_smaller_than():
    """The number alone would look like a bug or overstate the degree."""
    size = programme_size(_plan(GEN_ED_SKIP))
    assert size["courses_total"] == 28
    assert size["credits_total"] == 84.0
    # ...and the context that makes 84 honest rather than confusing:
    assert size["general_education"] == GEN_ED_SKIP
    assert size["full_courses"] == 40
    assert size["full_credits"] == 120.0


def test_transferred_keeps_the_full_credit_comparison():
    size = programme_size(_plan(GEN_ED_DONE))
    assert size["credits_total"] == 120.0
    assert size["courses_complete"] == 12
    assert size["transferred_courses"] == 12


def test_include_carries_no_caveat():
    size = programme_size(_plan(GEN_ED_INCLUDE))
    assert size["general_education"] == GEN_ED_INCLUDE
    assert "full_credits" not in size and "transferred_courses" not in size


def test_a_single_course_has_no_general_education_to_decline():
    """Templates without a gen_ed slot are untouched by any mode."""
    for mode in GEN_ED_MODES:
        assert template_for("course", mode) is TEMPLATES["course"]
        assert template_for("sequence", mode) is TEMPLATES["sequence"]


def test_an_unknown_mode_is_refused_rather_than_guessed():
    with pytest.raises(ProgramError):
        template_for("bachelors", "sure why not")


def test_the_transferred_count_survives_storage(tmp_path):
    """The in-memory plan is not what the page reads.

    `programme_size` counted transferred courses from a `transferred` flag set
    when the plan is built. `program_courses` has no column for it, so the flag
    was gone by the time the degree page asked -- the page showed the right
    credit total (21 of 60) and silently dropped the sentence explaining why 7
    courses were already complete. Every test above passed, because they all
    worked on the dict rather than through the database.

    Counting from the SLOT is round-trip safe: in this mode the
    general-education courses ARE the transferred ones.
    """
    from services.common.storage import StorageManager

    plan = plan_from_template("Nursing", "associate",
                              general_education=GEN_ED_DONE)
    plan.update(general_education=GEN_ED_DONE, template="associate",
                subject="Nursing", terms=4)

    sm = StorageManager(data_dir=str(tmp_path))
    sm.programs.create("prog_done", plan)
    for c in plan["courses"]:
        if c["slot"] == "gen_ed":
            sm.programs.mark_completed("prog_done", c["title"])

    loaded = sm.programs.get("prog_done")
    size = programme_size(loaded)
    assert size["general_education"] == GEN_ED_DONE
    assert size["transferred_courses"] == TEMPLATES["associate"]["slots"]["gen_ed"]
    assert size["courses_complete"] == size["transferred_courses"]
