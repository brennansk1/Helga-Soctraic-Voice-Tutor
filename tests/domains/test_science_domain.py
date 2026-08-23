"""The science domain: kinds, the POE miner, and the wiring.

The fixtures here are built from REAL LibreTexts prose, not invented. Every
earlier domain in this repository shipped a detector validated against
hand-written text that no publisher writes, and each time the detector found
nothing on a real book.
"""
import pytest

import services.domains.science as sci
from services.domains.science import teaching_moves as tm
from services.domains.science.concept_kind import (
    OBSERVATION, QUANTITY, LAW, MECHANISM, MODEL, REPRESENTATION, EXPERIMENT,
    CLASSIFICATION, MISCONCEPTION, UNKNOWN, RANK, GUIDANCE, LEVEL,
)


# --- the contract ------------------------------------------------------------

def test_implements_the_registry_contract():
    from services.domains.registry import contract_report
    report = contract_report(sci)
    assert report["missing_required"] == []
    for hook in ("attach_to_course", "source_for", "classify_concepts",
                 "pair_block", "KEYWORDS"):
        assert hook in report["has_optional"], f"{hook} not declared"


def test_source_for_accepts_the_call_sites_keywords():
    """`book_skeleton` calls `source_for(subject, doc_resolver=...)`.

    The mathematics domain shipped a signature that took `subject` only, so
    every call raised TypeError into that site's `except Exception` and the
    domain silently supplied nothing. Asserted rather than assumed.
    """
    import inspect
    sig = inspect.signature(sci.source_for)
    assert "doc_resolver" in sig.parameters
    assert any(p.kind is inspect.Parameter.VAR_KEYWORD
               for p in sig.parameters.values())


def test_source_for_degrades_without_network(monkeypatch):
    """A source failure must cost the material, not the build."""
    from services.research import libretexts as lt
    monkeypatch.setattr(lt, "pages_for",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    kind, pages, meta = sci.source_for("Physics")
    assert kind == "researched" and pages == []


# --- kinds -------------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Ohm's Law", LAW),
    ("The units of electric charge", QUANTITY),
    ("Why does the current continue to flow", MECHANISM),
    ("The ideal gas model and its assumptions", MODEL),
    ("Balancing chemical equations", REPRESENTATION),
    ("Measuring the speed of light: the apparatus", EXPERIMENT),
    ("Taxonomic classification of vertebrates", CLASSIFICATION),
    ("A common misconception about force and motion", MISCONCEPTION),
    ("Observing the colour change", OBSERVATION),
    ("Nonsense phrase with no signal", UNKNOWN),
])
def test_classification(title, expected):
    assert sci.classify(title) == expected


def test_observation_is_taught_first():
    """The phenomenon must exist for the learner before any model of it can
    mean anything."""
    assert RANK[OBSERVATION] == 0
    for kind in (MECHANISM, MODEL, REPRESENTATION):
        assert RANK[OBSERVATION] < RANK[kind]


def test_misconceptions_are_met_early_not_last():
    """A claim from the FCI literature, not a preference: these beliefs
    survive instruction that never addresses them directly."""
    assert RANK[MISCONCEPTION] < RANK[MECHANISM]
    assert RANK[MISCONCEPTION] < RANK[EXPERIMENT]


def test_every_kind_has_guidance_and_a_johnstone_level():
    for kind in RANK:
        if kind == UNKNOWN:
            continue
        assert GUIDANCE.get(kind), f"{kind} has no guidance"
        assert LEVEL.get(kind), f"{kind} has no Johnstone level"


def test_standing_rule_forbids_both_demands():
    rule = sci.NEVER_DEMAND_OBSERVATION
    assert "observation or a measurement they cannot make" in rule
    assert "never ask them to calculate a value" in rule.lower()
    # And it must offer the alternative, not only the prohibition.
    assert "PREDICT" in rule


def test_prompt_line_always_carries_the_standing_rule():
    for kind in list(RANK) + ["NOT_A_KIND"]:
        assert "never ask the learner for an observation" in sci.prompt_line(kind)


def test_prompt_line_names_the_level():
    line = sci.prompt_line(MECHANISM)
    assert "SUBMICROSCOPIC" in line.upper()


# --- the POE miner, on real prose --------------------------------------------
#
# Taken from LibreTexts physics and biology pages. See the module docstring.

_REAL_PHYSICS = (
    "A capacitor consists of two conducting surfaces. When connected to a "
    "voltage source, electric charge accumulates on the two surfaces but, "
    "since the conducting surfaces are separated by an insulator, the charges "
    "cannot travel from one surface to the other. If the voltage source is "
    "suddenly removed, current will continue to flow in the coil because of "
    "electromagnetic induction. The magnetic field is measured in tesla."
)

_REAL_BIOLOGY = (
    "As the time of cell division nears, chromatin associates with even more "
    "proteins, condensing to form visible chromosomes, while the nuclear "
    "envelope dissolves. The experiment used a control group in which the "
    "protein was absent, so that the result would have shown no condensation "
    "had the hypothesis been false."
)

#: Real boilerplate, which the first version of the miner offered to the tutor
#: as something for a learner to predict.
_OBJECTIVES = (
    "When you have mastered the information in this chapter, you should be "
    "able to: compare and contrast hypotheses and theories and place them "
    "and other elements of scientific method in order."
)


def test_poe_is_mined_from_declarative_prose():
    """The measured finding: POE is a classroom activity, not a textbook
    genre. Books state results directly, and the pair has to be split out of
    ordinary conditional sentences."""
    pairs = tm.poe_in_text(_REAL_PHYSICS)
    assert pairs, "no pair mined from real physics prose"
    joined = " ".join(p["second"] for p in pairs)
    assert "continue to flow" in joined or "cannot travel" in joined


def test_the_outcome_is_actually_withheld():
    """The setup must not contain its own answer, or there is nothing to
    predict."""
    for p in tm.poe_in_text(_REAL_PHYSICS):
        assert p["second"] not in p["first"]


def test_learning_objectives_are_not_predictions():
    """Grammatically a conditional; pedagogically a table of contents."""
    assert tm.poe_in_text(_OBJECTIVES) == []


def test_definitions_are_not_predictions():
    text = ("When a substance is an acid, it is a proton donor in the "
            "Bronsted sense and is a species that dissociates in water.")
    assert tm.poe_in_text(text) == []


def test_advice_to_the_reader_is_not_an_outcome():
    text = ("As a time-saving shorthand, nonbonding electrons are often "
            "omitted, but you still have to keep them in mind since they "
            "are often crucial in chemical reactions.")
    assert tm.poe_in_text(text) == []


def test_noun_forms_do_not_count_as_something_happening():
    """"the change in potential energy" and "chemical reactions" both contain
    a physical word and neither describes an outcome anyone could predict."""
    assert tm._has_physical_verb("the change in electric potential energy") is False
    assert tm._has_physical_verb("crucial in chemical reactions") is False
    assert tm._has_physical_verb("the current will continue to flow") is True


def test_overlapping_matches_do_not_duplicate_an_outcome():
    pairs = tm.poe_in_text(_REAL_BIOLOGY)
    outcomes = [p["second"][:60].lower() for p in pairs]
    assert len(outcomes) == len(set(outcomes))


def test_units_and_evidence_are_mined():
    assert tm.units_in_text(_REAL_PHYSICS)
    assert tm.evidence_in_text(_REAL_BIOLOGY)


# --- move selection ----------------------------------------------------------

def test_behaviour_chooses_the_move():
    moves = [{"kind": tm.PREDICT_OBSERVE, "first": "a", "second": "b"},
             {"kind": tm.UNITS_CHECK, "first": "c", "second": ""},
             {"kind": tm.EVIDENCE_CHECK, "first": "d", "second": ""}]
    assert tm.best_move(moves, behaviour="bluffing")["kind"] == tm.UNITS_CHECK
    assert tm.best_move(moves, behaviour="stuck")["kind"] == tm.PREDICT_OBSERVE
    assert tm.best_move(moves, behaviour="ahead")["kind"] == tm.EVIDENCE_CHECK


def test_a_misconception_pair_outranks_a_plain_one():
    moves = [{"kind": tm.PREDICT_OBSERVE, "first": "a", "second": "b"},
             {"kind": tm.PREDICT_OBSERVE, "first": "c", "second": "d",
              "misconception": True}]
    assert tm.best_move(moves)["first"] == "c"


# --- the prompt block --------------------------------------------------------

def test_prompt_block_withholds_the_outcome_explicitly():
    block = tm.prompt_block({"kind": tm.PREDICT_OBSERVE,
                             "first": "the voltage source is removed",
                             "second": "current continues to flow"})
    assert "WITHHOLD" in block
    assert "do not hint at it" in block
    assert "Once they commit" in block


def test_misconception_block_tells_the_tutor_not_to_steer():
    block = tm.prompt_block({"kind": tm.PREDICT_OBSERVE, "first": "a",
                             "second": "b", "misconception": True})
    assert "do not steer them away" in block


def test_units_block_forbids_arithmetic():
    block = tm.prompt_block({"kind": tm.UNITS_CHECK,
                             "first": "measured in tesla", "second": ""})
    assert "not arithmetic" in block


def test_prompt_block_of_nothing_is_empty():
    assert tm.prompt_block(None) == ""


# --- attaching ---------------------------------------------------------------

def _course(kind):
    return {"modules": [{"units": [{"lessons": [
        {"title": "Capacitors", "source_text": _REAL_PHYSICS,
         "concepts": [{"title": "Charge on the plates", "concept_kind": kind}]}
    ]}]}]}


def test_attach_writes_the_field_the_fsm_reads():
    """`teaching_pair`, not `teaching_move`. The mathematics domain wrote the
    wrong name in a file beside the document describing the defect."""
    course = _course(OBSERVATION)
    tally = sci.attach_to_course(course, book=None)
    concept = course["modules"][0]["units"][0]["lessons"][0]["concepts"][0]
    assert tally["moves"] >= 1
    assert "teaching_pair" in concept
    assert concept["teaching_pair"]["kind"] in (
        tm.PREDICT_OBSERVE, tm.UNITS_CHECK, tm.EVIDENCE_CHECK)


def test_classification_concepts_get_nothing():
    """A taxonomy has nothing to predict, and inventing a prediction for one
    produces exactly the fake-inquiry turn this domain exists to avoid."""
    course = _course(CLASSIFICATION)
    sci.attach_to_course(course, book=None)
    concept = course["modules"][0]["units"][0]["lessons"][0]["concepts"][0]
    assert "teaching_pair" not in concept


def test_attach_never_raises_on_a_broken_course():
    assert sci.attach_to_course({}, book=None)["moves"] == 0
    assert sci.attach_to_course({"modules": None}, book=None)["moves"] == 0


# --- separation --------------------------------------------------------------

def test_science_does_not_borrow_another_domains_kinds():
    for foreign in ("SYNTAX", "THEOREM", "PROOF", "CONTESTED", "CHRONOLOGY",
                    "FACT", "NOTATION"):
        assert foreign not in RANK


def test_subjects_route_to_science_and_not_elsewhere():
    from services.domains.registry import for_subject
    for subject in ("Physics", "organic chemistry", "cell biology",
                    "thermodynamics", "genetics"):
        ext = for_subject(subject)
        assert getattr(ext, "DOMAIN", None) == "science", subject


def test_science_keywords_do_not_steal_other_domains():
    """The traps are real: "cell" sits inside "Excel", "force" inside
    "workforce", "energy" inside energy-policy courses."""
    from services.domains.registry import for_subject
    for subject, expected in (("Calculus", "mathematics"),
                              ("World History", "history"),
                              ("Rust programming", "computer_science")):
        assert getattr(for_subject(subject), "DOMAIN", None) == expected
