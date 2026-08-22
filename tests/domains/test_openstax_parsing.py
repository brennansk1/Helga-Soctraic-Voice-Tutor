"""Parsing OpenStax book HTML into teachable material.

The fixture mirrors the real markup, verified against Calculus Volume 1 §1.1:
`[data-type=example]` wrapping `.os-problem-container` and
`.os-solution-container`, with expressions as MathML inside `<semantics>`
carrying a content-MathML `<annotation-xml>` duplicate.

CONTENT IS NOT CRAWLED. OpenStax robots.txt disallows /apps/archive and
/contents, so book content comes from a locally supplied copy. These tests run
entirely offline, which is also what makes them reliable.
"""
from services.domains.mathematics import openstax as ox

#: A `<math>` with the annotation-xml duplicate OpenStax really ships. Reading
#: both halves is what produced "f(x)=4-2x+5. f(x)=4-2x+5."
def _math(pres):
    return (f"<math><semantics><mrow>{pres}</mrow>"
            f"<annotation-xml encoding='MathML-Content'>{pres}"
            f"</annotation-xml></semantics></math>")


PAGE = f"""
<div data-type="page">
  <div data-type="example">
    <div class="os-problem-container">
      <p>Find the derivative of {_math('<msup><mi>x</mi><mn>2</mn></msup>')}.</p>
    </div>
    <div class="os-solution-container">
      <p>Step 1. Apply the power rule.</p>
      <p>Step 2. The result is {_math('<mn>2</mn><mi>x</mi>')} exactly.</p>
    </div>
  </div>

  <div data-type="example">
    <div class="os-problem-container"><p>An exercise with no solution.</p></div>
  </div>

  <div data-type="example">
    <div class="os-problem-container"><p>Another one.</p></div>
    <div class="os-solution-container"><p>See Answer Key.</p></div>
  </div>

  <div class="os-figure">
    <img src="x.png" alt="A parabola opening upward"/>
    <div class="os-caption">Figure 1.3 The graph of a quadratic.</div>
  </div>
  <div class="os-figure"><img src="deco.png"/></div>

  <div class="os-note-body">
    <p>Common mistake: writing {_math('<msqrt><mi>x</mi></msqrt>')} incorrectly.
       In fact it is not linear.</p>
  </div>
</div>
"""


def test_a_complete_worked_example_is_mined():
    out = ox.parse_book_html(PAGE)
    assert len(out["examples"]) == 1, "only the example WITH a real solution"
    ex = out["examples"][0]
    assert "power rule" in ex["solution"]


def test_the_mathematics_survives_as_latex():
    """The whole reason this module exists rather than a generic reader."""
    ex = ox.parse_book_html(PAGE)["examples"][0]
    assert "x^2" in ex["problem"], ex["problem"]
    assert "x 2" not in ex["problem"], "the exponent was flattened"


def test_the_content_mathml_duplicate_is_not_read_twice():
    """OpenStax <semantics> carries presentation AND content MathML."""
    ex = ox.parse_book_html(PAGE)["examples"][0]
    assert ex["problem"].count("x^2") == 1, ex["problem"]


def test_an_exercise_without_a_solution_is_refused():
    """A problem with no solution is not a worked example."""
    assert all("no solution" not in e["problem"]
               for e in ox.parse_book_html(PAGE)["examples"])


def test_a_pointer_to_the_answer_key_is_refused():
    """It promises a solution the tutor cannot show."""
    assert all("Answer Key" not in e["solution"]
               for e in ox.parse_book_html(PAGE)["examples"])


def test_steps_are_recovered_when_the_solution_is_stepped():
    ex = ox.parse_book_html(PAGE)["examples"][0]
    assert len(ex["steps"]) == 2, ex["steps"]


def test_only_captioned_figures_are_kept():
    """An uncaptioned figure is decoration, and cannot be described."""
    figs = ox.parse_book_html(PAGE)["figures"]
    assert len(figs) == 1
    assert "quadratic" in figs[0]["caption"]
    assert "parabola" in figs[0]["alt"]


def test_notes_are_captured_for_error_mining():
    notes = ox.parse_book_html(PAGE)["notes"]
    assert notes and "Common mistake" in notes[0]
    assert r"\sqrt{x}" in notes[0], "the note's mathematics must survive too"


def test_math_count_is_reported():
    assert ox.parse_book_html(PAGE)["math_count"] >= 3


def test_junk_never_raises():
    assert ox.parse_book_html("") is not None
    assert ox.parse_book_html("<p>no maths here</p>")["examples"] == []


# ---------------------------------------------------------- source selection

def test_openstax_books_are_chosen_by_level():
    assert "Calculus Volume 1" in ox.book_for("Calculus I")
    assert "Precalculus 2e" in ox.book_for("Precalculus")
    assert ox.book_for("Introductory Statistics")


def test_subjects_openstax_does_not_cover_return_nothing():
    """Returning a wrong-LEVEL book is worse than returning none.

    The generic relevance matcher answers "Linear Algebra" with OpenStax
    *Algebra 1* — a high-school text for a university subject — and a course
    built from it is wrong in a way no structural check would catch.
    """
    for subject in ("Linear Algebra", "Differential Equations",
                    "Real Analysis", "Abstract Algebra"):
        assert ox.book_for(subject) is None, subject


def test_no_subject_is_not_a_match():
    assert ox.book_for("") is None
    assert ox.book_for(None) is None
