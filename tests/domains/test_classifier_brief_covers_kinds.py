"""Every kind the schema ALLOWS must be a kind the prompt DEFINES.

WHY THIS TEST EXISTS
--------------------
`ANSWERABLE` is built from each domain's `RANK`, so adding a kind to
`concept_kind.py` silently widens what the model may answer. The prompt's
`_KIND_BRIEF` is a separate dict, and adding to it is a separate act that is
easy to forget.

When they drift, nothing raises. The model is handed an enum value whose
meaning it was never told and guesses from the name. Measured on a real
"Advanced SQL" build, after TOOL_OPERATION and TOOL_BOUNDARY were added to the
computer-science kinds and not to its brief: "Index Scan Types", "Set Operation
Efficiency" and "Adjacency List Traversal" — all MECHANISM — came back as
TOOL_BOUNDARY, whose guidance is "Do NOT answer it". The tutor would have
refused to teach core material, and the course would have looked fine from the
outside because every concept had *a* kind.

This is the seventh time in this project that a component was extended and one
of its readers was left behind. A test is cheaper than the eighth.
"""
import importlib
import pytest

DOMAINS = ["computer_science", "mathematics", "history", "science"]


@pytest.mark.parametrize("domain", DOMAINS)
def test_every_answerable_kind_is_defined_in_the_prompt(domain):
    m = importlib.import_module(f"services.domains.{domain}.concept_classifier")
    missing = set(m.KINDS) - set(m._KIND_BRIEF)
    assert not missing, (
        f"{domain}: the schema lets the model answer {sorted(missing)}, but "
        f"the prompt never says what they mean. Add them to _KIND_BRIEF.")


@pytest.mark.parametrize("domain", DOMAINS)
def test_brief_has_no_kind_that_cannot_be_answered(domain):
    """The other direction: a brief entry for a kind the enum rejects is dead
    text that spends prompt tokens describing an illegal answer."""
    m = importlib.import_module(f"services.domains.{domain}.concept_classifier")
    stale = set(m._KIND_BRIEF) - set(m.KINDS)
    assert not stale, f"{domain}: _KIND_BRIEF describes non-answerable {sorted(stale)}"
