"""Filling the kinds history's patterns cannot, by reading the source.

History titles are thin in a way that matters more than elsewhere, because the
kinds they fail to separate need OPPOSITE treatment. "Appeasement" could be a
CONTESTED question, a CHRONOLOGY, or a FACT (Munich was signed on 30 September
1938). The first must present two positions and refuse to resolve; the last
must be stated outright and never asked for.
"""
from services.domains.history import concept_classifier as cc
from services.domains.history import concept_kind as hk


class _Chapter:
    def __init__(self, order, text):
        self.order, self.text = order, text


class _Book:
    def __init__(self, chapters):
        self._c = {c.order: c for c in chapters}

    def chapter(self, order):
        return self._c.get(order)


NEUTRAL = _Book([_Chapter(1, "This section discusses the material introduced "
                             "earlier and relates it to what follows. " * 12)])


def _course(titles, chapter=1):
    return {"modules": [{"units": [{"lessons": [{
        "title": "A section", "book_chapter": chapter,
        "concepts": [{"title": t} for t in titles],
    }]}]}]}


def _concepts(course):
    return course["modules"][0]["units"][0]["lessons"][0]["concepts"]


def test_patterns_answer_first_and_cost_no_call():
    course = _course(["The date of the Battle of Hastings",
                      "Why historians disagree about appeasement"])
    tally = cc.classify_course(course, NEUTRAL, llm_json_fn=lambda **k: {})
    assert tally["calls"] == 0 and tally["by_pattern"] == 2
    assert [c["concept_kind"] for c in _concepts(course)] == [hk.FACT,
                                                              hk.CONTESTED]


def test_reading_fills_what_patterns_cannot():
    course = _course(["Appeasement"])
    tally = cc.classify_course(
        course, NEUTRAL,
        llm_json_fn=lambda **k: {"concepts": [
            {"title": "Appeasement", "kind": "CONTESTED",
             "why": "historians differ sharply"}]})
    assert tally["by_reading"] == 1
    assert _concepts(course)[0]["concept_kind"] == hk.CONTESTED


def test_an_invalid_kind_from_the_model_is_refused():
    course = _course(["Appeasement"])
    tally = cc.classify_course(
        course, NEUTRAL,
        llm_json_fn=lambda **k: {"concepts": [
            {"title": "Appeasement", "kind": "VIBES"}]})
    assert tally["unknown"] == 1
    assert _concepts(course)[0]["concept_kind"] == hk.UNKNOWN


def test_the_prompt_warns_against_manufacturing_a_debate():
    """The domain dimension penalises inventing controversy as hard as
    flattening it, so the classifier must not hand out CONTESTED freely."""
    text = cc._prompt("Appeasement", ["Appeasement"], "some source text")
    assert "historians actually disagree" in text
    assert "not mark something contested merely because it is complicated" \
        in text.replace("Do ", "").replace("do ", "")


def test_the_prompt_puts_FACT_first():
    text = cc._prompt("x", ["y"], "z")
    assert "FACT is the most important to get right" in text


def test_no_model_degrades_rather_than_failing():
    course = _course(["The date of the Battle of Hastings", "Appeasement"])
    tally = cc.classify_course(course, NEUTRAL, llm_json_fn=None)
    assert tally["by_pattern"] == 1 and tally["unknown"] == 1


def test_a_model_failure_costs_the_guidance_not_the_build():
    def boom(**kwargs):
        raise RuntimeError("model down")

    course = _course(["Appeasement"])
    tally = cc.classify_course(course, NEUTRAL, llm_json_fn=boom)
    assert tally["unknown"] == 1


def test_an_empty_course_never_raises():
    for course in ({}, {"modules": []}, {"modules": [{"units": []}]}):
        cc.classify_course(course, NEUTRAL, llm_json_fn=lambda **k: {})


def test_the_domain_exposes_it_under_the_shared_hook_name():
    """`book_skeleton` calls `classify_concepts` via hasattr; a domain using a
    different name is silently skipped."""
    from services.domains import registry
    module = registry.for_domain("history")
    assert callable(getattr(module, "classify_concepts", None))
    assert callable(getattr(module, "classify", None)), "contract fn shadowed"
