"""The cap bounds explanation, not citations.

The hydrator validated the body it generated; the caller then appended the
low-confidence banner, "## Sources" and "## Visual Aids", and stored THAT. So
hydration judged one artefact and finalize judged a longer one — a concept
passed at write time and failed at finalize on "1343 words (max 1300)", a bar
the retry loop had never been shown.

Penalising a concept for being well sourced is backwards. Count the teaching
text; detect elements across the whole document, so a URL in "## Sources"
still satisfies `any_source`.
"""
import pytest

from services.core.depth_contract import teaching_text, validate_concept


def test_machine_sections_do_not_count_toward_the_cap():
    body = "# T\n\nreal explanation here\n\n## Sources\n" + \
           "\n".join(f"- [src {i}](https://example.org/{i}) — web" for i in range(200))
    assert len(body.split()) > 800
    assert len(teaching_text(body).split()) < 30


@pytest.mark.parametrize("heading", [
    "## Sources", "## Metadata", "## Visual Aids",
    "## Prerequisites", "## Mastery Criteria",
])
def test_each_appended_section_is_excluded(heading):
    body = f"# T\n\nkept text\n\n{heading}\nDROPPED DROPPED DROPPED\n"
    out = teaching_text(body)
    assert "kept text" in out
    assert "DROPPED" not in out


def test_teaching_sections_are_kept():
    body = ("# T\n\n## Core Explanation\nkept one\n\n## Worked Example\nkept two\n"
            "\n## Misconceptions\nkept three\n\n## Sources\n- [x](https://e.org)\n")
    out = teaching_text(body)
    for s in ("kept one", "kept two", "kept three"):
        assert s in out
    assert "https://e.org" not in out


def test_a_url_in_sources_still_satisfies_any_source():
    """Excluded from the COUNT, not from element detection — otherwise the fix
    would break the very requirement the Sources block exists to meet."""
    body = ("# T\n\n**Definition.** A thing is defined as a thing.\n\n"
            "## Worked Example\nStep 1. Consider a table. We compute 2 = 2.\n"
            + ("filler words to clear the floor. " * 90) +
            "\n\n## Sources\n- [doc](https://example.org/doc) — web\n")
    ok, problems, _ = validate_concept(body, 2, "topic", "computer_science")
    assert "missing required element: any_source" not in problems, problems


def test_a_genuinely_overlong_explanation_still_fails():
    """The cap is not being removed, only measured on the right text."""
    body = "# T\n\n" + ("word " * 2000)
    ok, problems, _ = validate_concept(body, 2, "topic", "computer_science")
    assert any("too long" in p for p in problems), problems
