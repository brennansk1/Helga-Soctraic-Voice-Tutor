"""Teaching software you operate, not just code you write.

Measured against a real career checklist (Data Science -> Analytics
Engineering -> Automation), 24 of its named capabilities:

    before: 15 reached NO domain at all, 7 more classified UNKNOWN,
            1 mis-routed to mathematics on the word "arithmetic".
            Zero got specific treatment.

The list named languages and paradigms; practitioners name TOOLS AND
PRACTICES. And one whole class of content — operating a vendor's interface —
had no kind at all.
"""
import pytest

from services.domains.registry import for_subject
from services.domains.computer_science import classify
from services.domains.computer_science.concept_kind import (
    GUIDANCE, RANK, TOOL_OPERATION, TOOL_BOUNDARY, MECHANISM, TOOLING,
)


# --- the kind that exists because Socratic questioning cannot reach it -------

@pytest.mark.parametrize("concept", [
    "One BI tool - Power BI",
    "Build a permissioned dashboard in Power BI",
    "Publishing a Tableau workbook",
    "Configure the workspace ribbon",
])
def test_gui_operation_is_its_own_kind(concept):
    assert classify(concept) == TOOL_OPERATION


def test_tool_operation_forbids_eliciting_the_click_path():
    """Where a vendor put a setting is a contingent fact about a product.

    No reasoning derives it, so asking is a quiz with the answer withheld —
    the same failure history's FACT rule exists to prevent.
    """
    g = GUIDANCE[TOOL_OPERATION]
    assert "SOCRATIC QUESTIONING" in g and "CANNOT REACH IT" in g
    assert "STATE THE PATH PLAINLY" in g
    assert "Do not ask them to find it" in g
    # And not via a code aid: there is no command, and a code block implies one.
    assert "do not use a `code` aid" in g


def test_tool_operation_still_spends_the_turn_on_reasoning():
    """The click path is the perishable half. The turn belongs to the durable
    half — when, why, and what it costs later."""
    g = GUIDANCE[TOOL_OPERATION]
    assert "WHEN you would reach for this" in g
    assert "perishable half" in g


# --- the half that IS Socratic ----------------------------------------------

@pytest.mark.parametrize("concept", [
    "Decide what logic belongs in dbt versus in the BI tool",
    "Drop into a code node when the visual layer runs out",
    "Should this transformation live in SQL or the visual builder",
])
def test_the_boundary_decision_is_recognised(concept):
    assert classify(concept) == TOOL_BOUNDARY


def test_boundary_beats_operation_when_both_match():
    """"...belongs in dbt versus in the BI tool" contains "BI tool".

    If TOOL_OPERATION matched first, the most reasoning-rich concept in the
    subject would be taught as a menu path.
    """
    assert classify(
        "Decide what logic belongs in dbt versus in the BI tool"
    ) == TOOL_BOUNDARY


def test_the_boundary_is_not_answered_for_them():
    g = GUIDANCE[TOOL_BOUNDARY]
    assert "Do NOT answer it" in g
    # And the engineer's instinct is not automatically right.
    assert "not always the engineer's instinct" in g


def test_the_boundary_is_taught_late_not_early():
    """Before the learner knows what either layer costs, "which is better" is
    a preference poll."""
    assert RANK[TOOL_BOUNDARY] > RANK[TOOL_OPERATION]
    assert RANK[TOOL_BOUNDARY] == RANK[MECHANISM]


# --- routing, measured on the checklist's own words -------------------------

CHECKLIST = [
    "Advanced SQL", "Dimensional modelling (Kimball)",
    "Slowly changing dimensions", "dbt end to end",
    "Semantic layer / MetricFlow", "Snowflake as primary",
    "Cost management and FinOps", "Security and RBAC", "Airflow",
    "CI/CD for data", "Testing strategy and data contracts",
    "Docker and containers", "Terraform and infrastructure as code",
    "One BI tool - Power BI", "n8n core", "Self-hosting n8n properly",
    "RAG that survives contact with a real business", "Eval harness design",
    "Guardrails and human-in-the-loop", "Observability and incident response",
]


def test_the_checklist_reaches_the_domain():
    """15 of 24 used to reach no domain at all."""
    missed = [c for c in CHECKLIST
              if getattr(for_subject(c), "DOMAIN", None) != "computer_science"]
    assert not missed, f"no domain teaching for: {missed}"


def test_most_of_the_checklist_gets_specific_guidance():
    """UNKNOWN is a safe floor — the standing rule still applies — but it is
    the ceiling that per-kind guidance buys."""
    unknown = [c for c in CHECKLIST if classify(c) == "UNKNOWN"]
    assert len(unknown) <= 3, f"still unclassified: {unknown}"


def test_plurals_classify():
    """A trailing \\b after an alternation cannot match "dimensions" — the
    boundary lands mid-word. The identical defect was found and fixed in the
    science domain first; it recurs wherever this idiom is used."""
    assert classify("Slowly changing dimensions") == MECHANISM
    assert classify("Docker and containers") == TOOLING


def test_bare_product_names_are_setup_not_theory():
    for product in ("Snowflake as primary", "Airflow", "n8n core"):
        assert classify(product) == TOOLING


def test_conceptual_design_is_not_swallowed_by_the_tool_kinds():
    """Kimball and semantic layers are reasoning, not clicking, even though
    they are practised inside tools."""
    for concept in ("Dimensional modelling (Kimball)",
                    "Semantic layer / MetricFlow", "Security and RBAC"):
        assert classify(concept) == MECHANISM


# --- what a click path is SHOWN with ----------------------------------------

def test_a_click_path_gets_a_steps_aid_not_a_code_block():
    """TOOL_OPERATION forbids a `code` aid, so it needs a vehicle.

    A click path IS a sequence of labelled actions, which is exactly what the
    `steps` aid is. Without this routing the CODE rule — which is deliberately
    broad — would match these and offer a code listing, implying a command
    exists where none does.
    """
    from services.common.aid_policy import suggest_kinds
    for concept in ("Power BI row-level security",
                    "Build a permissioned dashboard in Power BI",
                    "n8n canvas basics"):
        assert suggest_kinds(concept) == ("steps",), concept


def test_real_code_still_gets_a_code_aid():
    """The click-path rule sits ABOVE the code rule, so it must not swallow
    actual programming."""
    from services.common.aid_policy import suggest_kinds
    for concept in ("Binary search in Python", "Writing a recursive function"):
        assert "code" in suggest_kinds(concept), concept


def test_the_guidance_points_at_the_aid_that_exists():
    """Telling the tutor what NOT to use, without naming what to use, leaves
    it to improvise — which for a GUI path means prose."""
    assert "`steps` AID" in GUIDANCE[TOOL_OPERATION]
