"""Mining worked examples from plain text, and attaching them at build time.

`openstax.parse_book_html` reads semantic markup and is near-perfect where it
applies — which is only where the source IS that markup. The generic pipeline
hands this domain a `Chapter` carrying plain text and nothing else, so a maths
course built from any ordinary textbook would otherwise get no worked examples
at all. That is the normal case, not the edge case.

A worked example is this domain's primary asset: the move that replaces "now
you try" is *show the whole solution and ask what licenses one step*.
"""
from services.domains.mathematics import worked_examples as we

CHAPTER = r"""
Some introductory prose about derivatives and their meaning.

EXAMPLE 3.4
Find the derivative of $f(x)=x^2-2x$.

Solution
Step 1. Apply the limit definition: $\frac{f(x+h)-f(x)}{h}$.
Step 2. Expand and cancel to get $2x+h-2$.
Step 3. Let $h\to 0$ to get $2x-2$.

EXAMPLE 3.5
An example whose solution the book leaves to the reader.

EXAMPLE 3.6
Evaluate $\int x\,dx$.

Solution
Apply the power rule for integration to obtain $\frac{x^2}{2}+C$ directly.

Common mistake: students often write $\sqrt{a+b}=\sqrt{a}+\sqrt{b}$.
This is incorrect; in fact $\sqrt{9+16}=5$, not $3+4=7$.

Exercises
1. Try these on your own.
"""


class _Chapter:
    def __init__(self, order, text):
        self.order, self.text = order, text


class _Book:
    def __init__(self, chapters):
        self._c = {c.order: c for c in chapters}

    def chapter(self, order):
        return self._c.get(order)


def test_labelled_examples_are_mined():
    found = we.examples_in_text(CHAPTER)
    assert len(found) == 2, [e["problem"][:40] for e in found]
    assert "x^2-2x" in found[0]["problem"]


def test_an_example_with_no_solution_is_refused():
    """The turn would promise a solution the tutor cannot show."""
    assert all("leaves to the reader" not in e["problem"]
               for e in we.examples_in_text(CHAPTER))


def test_steps_are_recovered_when_present():
    found = we.examples_in_text(CHAPTER)
    assert len(found[0]["steps"]) == 3
    assert "limit definition" in found[0]["steps"][0]


def test_an_unstepped_solution_yields_no_false_steps():
    found = we.examples_in_text(CHAPTER)
    assert found[1]["steps"] == []


def test_prose_with_no_mathematics_is_refused():
    text = ("EXAMPLE 1\nDiscuss the history of the subject.\n\n"
            "Solution\nIt developed slowly over several centuries in Europe.")
    assert we.examples_in_text(text) == []


def test_flagged_notes_are_mined():
    notes = we.notes_in_text(CHAPTER)
    assert notes and "sqrt" in notes[0]


def test_junk_never_raises():
    for bad in (None, "", "   ", "EXAMPLE", "Solution"):
        we.examples_in_text(bad)
        we.notes_in_text(bad)


# ------------------------------------------------------------ attachment

def _course(kinds, chapter=1):
    return {"modules": [{"units": [{"lessons": [{
        "title": "Derivatives", "book_chapter": chapter,
        "concepts": [{"title": f"c{i}", "concept_kind": k}
                     for i, k in enumerate(kinds)],
    }]}]}]}


def _concepts(course):
    return course["modules"][0]["units"][0]["lessons"][0]["concepts"]


BOOK = _Book([_Chapter(1, CHAPTER)])


def test_an_aided_kind_gets_a_teaching_pair():
    course = _course(["PROCEDURE"])
    tally = we.attach_to_course(course, BOOK)
    move = _concepts(course)[0].get("teaching_pair")
    assert move and move["kind"] in ("ERROR_HUNT", "WORKED_STEP")
    assert tally["moves"] == 1


def test_a_definition_gets_nothing():
    """A definition does not need a worked example; a procedure is nearly
    useless without one."""
    course = _course(["DEFINITION"])
    we.attach_to_course(course, BOOK)
    assert _concepts(course)[0].get("teaching_pair") is None


def test_the_same_example_is_not_attached_twice():
    """The same worked example on three concepts teaches the third nothing."""
    course = _course(["PROCEDURE", "PROCEDURE", "PROCEDURE", "PROCEDURE"])
    we.attach_to_course(course, BOOK)
    attached = [c["teaching_pair"] for c in _concepts(course)
                if c.get("teaching_pair")]
    seen = {(m["first"][:120], m["second"][:120]) for m in attached}
    assert len(seen) == len(attached)


def test_steps_reach_the_concept():
    course = _course(["PROCEDURE", "PROCEDURE"])
    we.attach_to_course(course, BOOK)
    assert any(c.get("worked_steps") for c in _concepts(course))


def test_attachment_survives_a_json_round_trip():
    """The concept is written to structure.json; the move has to reach disk."""
    import json
    course = _course(["PROCEDURE"])
    we.attach_to_course(course, BOOK)
    back = json.loads(json.dumps(course))
    assert back["modules"][0]["units"][0]["lessons"][0]["concepts"][0][
        "teaching_pair"]


def test_a_missing_chapter_costs_the_asset_not_the_build():
    course = _course(["PROCEDURE"], chapter=99)
    tally = we.attach_to_course(course, BOOK)
    assert tally["moves"] == 0
    assert _concepts(course)[0].get("teaching_pair") is None


def test_doc_sourced_lessons_use_their_own_text():
    """Doc-sourced lessons carry text directly rather than a chapter index."""
    course = _course(["PROCEDURE"], chapter=None)
    course["modules"][0]["units"][0]["lessons"][0]["source_text"] = CHAPTER
    tally = we.attach_to_course(course, BOOK)
    assert tally["moves"] == 1


def test_an_empty_course_never_raises():
    for course in ({}, {"modules": []}, {"modules": [{"units": []}]}):
        we.attach_to_course(course, BOOK)


def test_the_builder_hook_is_discoverable():
    """`book_skeleton` calls this via hasattr; a different name is skipped
    silently, which is the defect class this repository keeps hitting."""
    from services.domains import registry
    module = registry.for_domain("mathematics")
    assert callable(getattr(module, "attach_to_course", None))


# ------------------------------------------------- matching, not popping

TWO_TOPICS = r"""
EXAMPLE 1
Evaluate $\int x e^x\,dx$ using integration by parts.

Solution
Step 1. Let $u=x$ and $dv=e^x dx$.
Step 2. Apply the parts formula to get $xe^x-e^x+C$.

EXAMPLE 2
Compute $\int_0^1 x^2\,dx$ with the power rule.

Solution
Step 1. Antidifferentiate to $\frac{x^3}{3}$.
Step 2. Evaluate at the limits to get $\frac{1}{3}$.
"""


def test_material_is_matched_to_its_concept_not_popped_in_order():
    """MEASURED on a real build before this: "Applying the Squeeze Theorem"
    was taught with a factoring limit, "Integration by parts" with the
    antiderivative of 1/x, and "Definite integrals and power rule" with the
    one example that IS integration by parts. Systematically off by one, and
    every pairing wrong in a way a learner notices before the tutor does."""
    book = _Book([_Chapter(1, TWO_TOPICS)])
    course = _course(["PROCEDURE", "PROCEDURE"])
    cs = _concepts(course)
    cs[0]["title"] = "Definite Integrals via Power Rule"
    cs[1]["title"] = "Integration by Parts"

    we.attach_to_course(course, book)
    first = (cs[0].get("teaching_pair") or {}).get("first", "")
    second = (cs[1].get("teaching_pair") or {}).get("first", "")
    assert "power rule" in first.lower(), first
    assert "parts" in second.lower(), second


def test_a_concept_with_no_shared_vocabulary_still_gets_material():
    """Matching must not become a filter: an unmatched concept in a lesson
    still deserves a worked example from that lesson."""
    book = _Book([_Chapter(1, TWO_TOPICS)])
    course = _course(["PROCEDURE"])
    _concepts(course)[0]["title"] = "Zzz Qqq Wwww"
    we.attach_to_course(course, book)
    assert _concepts(course)[0].get("teaching_pair")


def test_common_words_do_not_drive_the_match():
    """"Function", "value" and "applying" appear in half the titles in any
    maths course and carry no signal."""
    book = _Book([_Chapter(1, TWO_TOPICS)])
    course = _course(["PROCEDURE"])
    _concepts(course)[0]["title"] = "Applying Function Values"
    we.attach_to_course(course, book)
    assert _concepts(course)[0].get("teaching_pair")
