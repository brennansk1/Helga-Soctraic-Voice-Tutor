"""Computer-science concept kinds: the traps found by building real courses."""
import pytest


class TestBoundaryNeedsTwoLayers:
    """A bare comparison is not a layer decision.

    TOOL_BOUNDARY's guidance is "Do NOT answer it", so a false positive costs
    the learner an explanation of core material. Measured on a real SQL build
    before the fix: every one of the `SAME_LANGUAGE` titles below was typed
    TOOL_BOUNDARY on a bare "vs".
    """

    SAME_LANGUAGE = [
        "ROWS vs RANGE Distinction",
        "ROWS vs RANGE Semantics",
        "Time vs Event Frames",
        "UNION vs UNION ALL",
        "Index Scan Types",
    ]

    REAL_BOUNDARIES = [
        "Should this logic live in dbt or the BI tool",
        # The determiner matters: without allowing it, this missed and
        # TOOL_OPERATION claimed the concept on "visual builder".
        "Transformation in SQL versus the visual builder",
        "Which layer owns the metric",
        "Push logic down to the warehouse",
        "dbt vs Power BI for metrics",
    ]

    @pytest.mark.parametrize("title", SAME_LANGUAGE)
    def test_comparing_two_features_of_one_language_is_not_a_boundary(self, title):
        from services.domains.computer_science.concept_kind import (
            classify, TOOL_BOUNDARY,
        )
        assert classify(title, "", None) != TOOL_BOUNDARY, (
            f"{title!r} would tell the tutor to REFUSE to answer it")

    @pytest.mark.parametrize("title", REAL_BOUNDARIES)
    def test_a_genuine_layer_choice_still_matches(self, title):
        from services.domains.computer_science.concept_kind import (
            classify, TOOL_BOUNDARY,
        )
        assert classify(title, "", None) == TOOL_BOUNDARY
