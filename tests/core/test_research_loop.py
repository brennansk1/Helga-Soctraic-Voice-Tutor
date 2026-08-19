"""The model decides what to look for; the code decides when to stop.

A model driving its own research loop is worth having -- query formulation is
exactly what world knowledge is for. But it must not exit on its own judgement:
scope_fit exists because asking a model "is there enough material?" invites the
same optimism that produces padded courses, and this repo's judges swing +/-10
points on identical input.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.research.research_loop import (  # noqa: E402
    DRY_ROUNDS, checklist_from, run_research_loop)


def _src(title):
    return {"title": title, "snippet": ""}


class TestExitIsMeasuredNotJudged(unittest.TestCase):
    def test_it_stops_when_the_checklist_is_covered(self):
        items = ["Gram Schmidt process", "Least squares approximation"]
        r = run_research_loop(items, search_fn=lambda q: [_src(q)])
        assert r["stopped_because"] == "covered"
        assert r["coverage_pct"] == 100

    def test_the_exit_rule_is_recorded_as_a_measurement(self):
        """So nobody later reads it as the model's opinion."""
        r = run_research_loop(["Alpha topic"], search_fn=lambda q: [_src(q)])
        assert "no model judgement" in r["exit_rule"]

    def test_a_confident_model_cannot_end_the_loop_early(self):
        """The proposer has no vote on sufficiency -- it only supplies queries."""
        calls = {"n": 0}

        def propose(outstanding, found):
            calls["n"] += 1
            return ["irrelevant query"]          # never covers anything

        r = run_research_loop(["Gram Schmidt process"],
                              search_fn=lambda q: [_src("unrelated material")],
                              propose_queries_fn=propose)
        assert r["coverage_pct"] == 0
        assert r["stopped_because"] in ("budget", "dry")


class TestNoProgressStops(unittest.TestCase):
    def test_a_dry_subject_ends_the_loop(self):
        """Without this a loop grinds its whole budget on a dry subject -- or on
        a silently failing service, which is what the 4096-token ceiling looked
        like from outside."""
        r = run_research_loop(["Nothing findable here"], search_fn=lambda q: [])
        assert r["stopped_because"] == "dry"
        assert r["rounds"] <= DRY_ROUNDS

    def test_one_empty_round_is_not_enough_to_stop(self):
        """A single empty round is often a bad query; two is the subject."""
        state = {"n": 0}

        def search(q):
            state["n"] += 1
            return [] if state["n"] == 1 else [_src("Gram Schmidt process")]

        r = run_research_loop(["Gram Schmidt process"], search_fn=search)
        assert r["coverage_pct"] == 100

    def test_the_budget_is_hard(self):
        r = run_research_loop(["Unreachable topic"],
                              search_fn=lambda q: [_src("something new %s" % q)],
                              propose_queries_fn=lambda o, f: ["q%d" % len(f)],
                              max_rounds=3)
        assert r["rounds"] <= 3
        assert r["stopped_because"] == "budget"


class TestFailuresAreNotCoverage(unittest.TestCase):
    def test_a_failing_search_never_retires_an_item(self):
        def boom(q):
            raise OSError("service down")

        r = run_research_loop(["Gram Schmidt process"], search_fn=boom)
        assert r["coverage_pct"] == 0
        assert r["outstanding"] == ["Gram Schmidt process"]


class TestChecklistProvenance(unittest.TestCase):
    def test_a_published_syllabus_replaces_the_model_list(self):
        """Merging would let invented items dilute a published standard, and
        then "covered the checklist" would stop meaning "covered the syllabus"."""
        brief = {"syllabi": [{"chapters": ["Orthogonality", "Determinants"]}]}
        c = checklist_from(brief, model_fallback=["Something invented"])
        assert c["items"] == ["Orthogonality", "Determinants"]
        assert c["authoritative"] is True

    def test_no_syllabus_falls_back_and_says_so(self):
        c = checklist_from({"syllabi": []}, model_fallback=["Encounter design"])
        assert c["items"] == ["Encounter design"]
        assert c["authoritative"] is False
        assert "does not demonstrate parity" in c["note"]

    def test_an_empty_checklist_does_not_run(self):
        assert run_research_loop([], search_fn=lambda q: [])["ran"] is False


if __name__ == "__main__":
    unittest.main()


class TestTopicNameExtraction(unittest.TestCase):
    """Models answer "list the topics" with "Topic: a sentence explaining it".

    MEASURED against the live service: the loop found 248 sources and scored
    0/12, because every checklist item was a 20-word sentence and matching half
    its words against source titles is a test nothing can pass.
    """

    def test_the_explanation_is_stripped(self):
        from services.research.research_loop import topic_name
        item = ("Narrative Architecture and Pacing: Structuring sessions with "
                "clear acts, rising tension, and satisfying resolutions.")
        assert topic_name(item) == "Narrative Architecture and Pacing"

    def test_a_hyphenated_term_survives(self):
        """Gram-Schmidt must not be split at its hyphen."""
        from services.research.research_loop import topic_name
        assert topic_name("Gram-Schmidt Orthogonalization") == \
            "Gram-Schmidt Orthogonalization"

    def test_a_separating_dash_is_stripped(self):
        from services.research.research_loop import topic_name
        assert topic_name("Projections — how to compute them") == "Projections"

    def test_a_plain_topic_is_unchanged(self):
        from services.research.research_loop import topic_name
        assert topic_name("Least Squares") == "Least Squares"

    def test_a_verbose_item_can_now_be_covered(self):
        from services.research.research_loop import _default_is_covered
        item = ("Narrative Architecture and Pacing: Structuring sessions with "
                "clear acts and rising tension while managing time.")
        sources = [{"title": "Narrative architecture in tabletop RPGs",
                    "snippet": "pacing a session"}]
        assert _default_is_covered(item, sources) is True
