"""The item extractor turns concept markdown into what FSRS schedules."""
import pytest

from services.common.review_items import (
    RECALL, DISCRIMINATE, APPLY, SOCRATIC,
    extract, mix_summary, section, bullets, bloom_of, title_of,
)

CONCEPT = """# NULLIF for Logical Equivalence

## Metadata
- **Bloom Target**: 4 (Analyze)

## Learning Objectives
- Construct queries using NULLIF.

## Prerequisites
Prior concepts: Three-Valued Logic, COALESCE

## Mastery Criteria
Grade 3 requires: predicting the output of `WHERE NULLIF(a, b) IS NULL`.

## Key Facts
- `NULLIF(a, b)` is syntactic sugar for `CASE WHEN a = b THEN NULL ELSE a END`.
- **Grouping**: Quantifiers apply to the preceding atom in the pattern.
- Lookaheads are zero-width assertions and do not consume characters at all.

## Misconceptions
- **Belief**: `NULLIF(a, b)` returns `TRUE` if `a` and `b` are equal.
  **Correction**: It returns `NULL`, so you must wrap it in `IS NULL`.

## Edge Cases & Limitations
- **Type Mismatch:** PostgreSQL attempts implicit casting between the operands.

## Socratic Hooks
- Bloom 1-2: What does `SELECT NULLIF(5, 5);` return?
- Bloom 3-4: Why does `WHERE col1 = col2` exclude rows where both are NULL?
- Bloom 5-6: Evaluate the trade-off between NULLIF and a COALESCE sentinel.
"""


@pytest.fixture()
def items():
    return extract(CONCEPT, "con_test0001", "course_test0001")


def test_every_tier_is_produced_from_one_concept(items):
    """Routing a concept to a single modality by its Bloom target is the thing
    the evidence rules out: mixed factual + higher-order practice beats either
    pure form on higher-order outcomes."""
    mix = mix_summary(items)
    for kind in (RECALL, DISCRIMINATE, APPLY, SOCRATIC):
        assert mix[kind] > 0, f"a Bloom-4 concept produced no {kind} items: {mix}"


def test_cloze_blanks_the_longest_code_span_and_hides_it(items):
    cloze = [i for i in items if "[ ... ]" in i.front]
    assert cloze, "no cloze item was produced from a fact full of code spans"
    for it in cloze:
        assert "[ ... ]" not in it.back
        answer = it.back.strip("`")
        assert answer not in it.front, "the answer is still visible in the prompt"


def test_labelled_fact_becomes_a_question_not_a_statement(items):
    q = [i for i in items if "Grouping" in i.front and i.kind == RECALL]
    assert q, "a **Label**: fact did not become a question"
    assert q[0].front.rstrip().endswith("?")
    assert "preceding atom" in q[0].back


def test_discrimination_is_class_balanced():
    """Every Belief is false by construction. A queue built only from them
    teaches "always false" rather than the content."""
    disc = [i for i in extract(CONCEPT, "c", "k") if i.kind == DISCRIMINATE]
    truths = {i.payload["truth"] for i in disc}
    assert truths == {True, False}, f"discrimination items are one-sided: {truths}"


def test_socratic_item_carries_the_authors_own_rubric(items):
    soc = [i for i in items if i.kind == SOCRATIC]
    assert soc, "no socratic item"
    assert "Grade 3 requires" in soc[0].payload["rubric"], \
        "an open question with no answer key must reveal the Mastery Criteria"


def test_hooks_are_banded_to_the_right_tier(items):
    fronts = {i.kind: [x.front for x in items if x.kind == i.kind] for i in items}
    assert any("SELECT NULLIF(5, 5)" in f for f in fronts[RECALL])
    assert any("exclude rows" in f for f in fronts[APPLY])
    assert any("trade-off" in f for f in fronts[SOCRATIC])


def test_uids_are_stable_across_re_extraction():
    """Re-running the extractor on an unchanged concept must not orphan a
    learner's history."""
    a = {i.uid for i in extract(CONCEPT, "con_x", "course_x")}
    b = {i.uid for i in extract(CONCEPT, "con_x", "course_x")}
    assert a == b and len(a) == len(extract(CONCEPT, "con_x", "course_x"))


def test_editing_a_fact_mints_a_new_item():
    """The question changed, so its recall history no longer describes it."""
    before = {i.uid for i in extract(CONCEPT, "con_x", "course_x")}
    after = {i.uid for i in extract(
        CONCEPT.replace("zero-width assertions", "width-consuming assertions"),
        "con_x", "course_x")}
    assert before != after


def test_missing_sections_do_not_raise():
    thin = "# Bare Concept\n\n## Core Explanation\nJust prose, no lists.\n"
    assert extract(thin, "c", "k") == []
    assert extract("", "c", "k") == []
    assert extract(None, "c", "k") == []


def test_no_item_leaks_its_answer_or_is_degenerate(items):
    for it in items:
        assert len(it.front) >= 12, f"unusably short prompt: {it.front!r}"
        assert len(it.back) >= 3, f"unusably short answer: {it.back!r}"
        assert it.concept_uid and it.course_uid and it.uid.startswith("itm_")


def test_section_and_bullet_helpers():
    assert "Bloom Target" in section(CONCEPT, "Metadata")
    assert section(CONCEPT, "Nonexistent") == ""
    assert len(bullets(section(CONCEPT, "Key Facts"))) == 3
    assert bloom_of(CONCEPT) == 4
    assert bloom_of("# x") == 2
    assert title_of(CONCEPT) == "NULLIF for Logical Equivalence"


def test_bullets_join_hanging_continuation_lines():
    body = "- **Belief**: a thing\n  **Correction**: the fix\n- second bullet"
    got = bullets(body)
    assert len(got) == 2 and "Correction" in got[0]
