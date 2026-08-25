"""Ask for the definition, then judge the definition.

The depth contract requires `formal_definition` from mastery 3 up, and the
"## Core Explanation" instruction never mentioned one. Measured on a live
build: 8 of 8 consecutive concepts missed formal_definition on the first
attempt and paid for a full regeneration — a 5600-token prompt and roughly 90
seconds each — to be told a requirement the first prompt could have stated.

Half the build was being spent discovering something already known. And the
instruction has to name the FORM the detector recognises: "state a definition"
and "write **Definition.**" are the same request to a person and different
requests to a regex.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


def test_the_prompt_asks_for_a_definition_when_one_is_required():
    src = _read("services", "core", "course_builder.py")
    i = src.find("definition_line = \"\"")
    assert i > 0, "the generator does not condition on the contract at all"
    block = src[i:i + 1200]
    assert "formal_definition" in block, \
        "the hint is not tied to the contract that demands it"
    assert "**Definition.**" in block, \
        "the hint must name the form the detector recognises"


def test_the_hint_reaches_the_template():
    src = _read("services", "core", "course_builder.py")
    i = src.find("## Core Explanation\n[{core_inst}")
    assert i > 0, "the Core Explanation instruction moved"
    assert "{definition_line}" in src[i:i + 200], \
        "the hint is computed and never interpolated — the defect this "\
        "project keeps producing"


def test_the_form_the_hint_asks_for_actually_passes_the_detector():
    """The two ends must agree, or the hint teaches the model to fail."""
    from services.core.depth_contract import _has
    asked_for = "**Definition.** A window function is a function computed " \
                "across a set of rows related to the current row."
    assert _has("formal_definition", asked_for)


def test_a_level_that_does_not_require_it_is_not_asked_for_it():
    """Mastery 2 is not judged on a formal definition; demanding one there
    would spend words the word cap needs elsewhere."""
    from services.core.depth_contract import contract_for
    assert "formal_definition" not in contract_for(2, "sql", "computer_science")["required"]
    assert "formal_definition" in contract_for(3, "sql", "computer_science")["required"]


# ------------------------------------------------ the higher levels' elements

def test_the_prompt_asks_for_a_derivation_where_one_is_required():
    """Measured across 12 concepts of a mastery-3 build: derivation_or_proof
    appeared in 3, not because SQL cannot support one but because nothing
    asked for it."""
    src = _read("services", "core", "course_builder.py")
    i = src.find("definition_line = \"\"")
    block = src[i:i + 3000]
    assert "derivation_or_proof" in block
    assert "step by step" in block.lower()


def test_the_prompt_asks_for_the_normative_source():
    src = _read("services", "core", "course_builder.py")
    i = src.find("definition_line = \"\"")
    block = src[i:i + 3000]
    assert "primary_source" in block
    assert "normative" in block.lower()
