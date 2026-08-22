"""Filling the kinds that patterns cannot, by reading the source.

UNKNOWN is not neutral: it costs the concept its per-kind teaching guidance and
its build-time aid. Mathematics titles are especially thin — "Eigenvalues"
names an object and says nothing about how the section treats it — so the
gap-filler matters more here than in computer science.

The standing NEVER_SOLVE rule still applies to an UNKNOWN concept, so the floor
is safe either way. This raises the ceiling.
"""
from services.domains.mathematics import concept_classifier as cc
from services.domains.mathematics import concept_kind as mk


class _Chapter:
    def __init__(self, order, text):
        self.order, self.text = order, text


class _Book:
    def __init__(self, chapters):
        self._c = {c.order: c for c in chapters}

    def chapter(self, order):
        return self._c.get(order)


def _course(titles, chapter=1):
    return {"modules": [{"units": [{"lessons": [{
        "title": "A section", "book_chapter": chapter,
        "concepts": [{"title": t} for t in titles],
    }]}]}]}


def _concepts(course):
    return course["modules"][0]["units"][0]["lessons"][0]["concepts"]


#: Deliberately NEUTRAL prose. Earlier this said "how the symbol is written",
#: which the NOTATION pattern matches — so the body fallback classified the
#: concept and it never reached the model, making these tests measure the
#: opposite of what they claim.
BOOK = _Book([_Chapter(1, "This part of the book discusses the ideas "
                          "introduced earlier and relates them. " * 12)])


def test_the_title_outranks_the_body():
    """"Proof of the Chain Rule" in a section whose prose happens to say "how
    the symbol is written" came back NOTATION — an incidental phrase outvoting
    an explicit one. The title is the deliberate signal; the body is a
    fallback, not a peer."""
    noisy = _Book([_Chapter(1, "A section about how the symbol is written. " * 12)])
    course = _course(["Proof of the Chain Rule"])
    cc.classify_course(course, noisy, llm_json_fn=lambda **k: {})
    assert _concepts(course)[0]["concept_kind"] == "PROOF"


def test_the_body_still_classifies_when_the_title_says_nothing():
    noisy = _Book([_Chapter(1, "A section about how the symbol is written. " * 12)])
    course = _course(["Working with these ideas"])
    calls = []
    cc.classify_course(course, noisy,
                       llm_json_fn=lambda **k: calls.append(1) or {})
    assert _concepts(course)[0]["concept_kind"] == "NOTATION"
    assert calls == [], "the body answered; no model call was needed"


def test_patterns_answer_first_and_cost_no_call():
    course = _course(["Proof of the Chain Rule", "Sigma Notation"])
    tally = cc.classify_course(course, BOOK, llm_json_fn=lambda **k: {})
    assert tally["calls"] == 0
    assert tally["by_pattern"] == 2
    assert [c["concept_kind"] for c in _concepts(course)] == ["PROOF", "NOTATION"]


def test_reading_fills_what_patterns_cannot():
    course = _course(["Working with these ideas"])

    def fake(**kwargs):
        return {"concepts": [{"title": "Working with these ideas",
                              "kind": "NOTATION", "why": "explains a symbol"}]}

    tally = cc.classify_course(course, BOOK, llm_json_fn=fake)
    assert tally["by_reading"] == 1
    assert _concepts(course)[0]["concept_kind"] == "NOTATION"


def test_an_invalid_kind_from_the_model_is_refused():
    """A kind outside the vocabulary would silently disable guidance."""
    course = _course(["Working with these ideas"])
    tally = cc.classify_course(
        course, BOOK,
        llm_json_fn=lambda **k: {"concepts": [
            {"title": "Working with these ideas", "kind": "VIBES"}]})
    assert tally["unknown"] == 1
    assert _concepts(course)[0]["concept_kind"] == mk.UNKNOWN


def test_no_model_degrades_rather_than_failing():
    """A build with no LLM must keep pattern classification."""
    course = _course(["Sigma Notation", "Working with these ideas"])
    tally = cc.classify_course(course, BOOK, llm_json_fn=None)
    assert tally["by_pattern"] == 1 and tally["unknown"] == 1


def test_a_model_failure_costs_the_guidance_not_the_build():
    course = _course(["Working with these ideas"])

    def boom(**kwargs):
        raise RuntimeError("model down")

    tally = cc.classify_course(course, BOOK, llm_json_fn=boom)
    assert tally["unknown"] == 1
    assert _concepts(course)[0]["concept_kind"] == mk.UNKNOWN


def test_no_source_text_means_no_call():
    """Classifying from a title alone is what patterns already tried."""
    course = _course(["Working with these ideas"], chapter=99)
    calls = []
    cc.classify_course(course, BOOK,
                       llm_json_fn=lambda **k: calls.append(1) or {})
    assert calls == []


def test_lesson_source_text_is_used_when_there_is_no_chapter():
    """Doc-sourced lessons carry text directly rather than a chapter index."""
    course = _course(["Working with these ideas"], chapter=None)
    lesson = course["modules"][0]["units"][0]["lessons"][0]
    lesson["source_text"] = ("This part of the book discusses the ideas "
                             "introduced earlier. " * 12)
    tally = cc.classify_course(
        course, BOOK,
        llm_json_fn=lambda **k: {"concepts": [
            {"title": "Working with these ideas", "kind": "NOTATION"}]})
    assert tally["by_reading"] == 1


def test_an_empty_course_never_raises():
    for course in ({}, {"modules": []}, {"modules": [{"units": []}]}):
        cc.classify_course(course, BOOK, llm_json_fn=lambda **k: {})


def test_the_domain_exposes_it_under_the_shared_hook_name():
    """The CS domain exposes `classify_concepts`; the builder calls that name
    via hasattr, so a domain using a different one is silently skipped."""
    from services.domains import registry
    module = registry.for_domain("mathematics")
    assert callable(getattr(module, "classify_concepts", None))


def test_the_module_name_does_not_shadow_the_contract_function():
    """A submodule named `classify.py` binds as a package attribute and hides
    the `classify` function the registry contract requires — which is exactly
    what happened in the computer-science package."""
    from services.domains import registry
    for key in registry.available():
        module = registry.for_domain(key)
        assert callable(getattr(module, "classify", None)), key
