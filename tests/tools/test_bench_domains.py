"""The deterministic half of the domain benchmark must be trustworthy.

A benchmark nobody validated is worse than no benchmark: it produces numbers
that look like evidence. Everything here runs without a model, which is the
point -- the judged half is expensive and noisy, so anything computable from
the transcript is computed and tested.

The two properties that matter most, and why:

  STAGING. The aid grammar has a `stage` field so a diagram can withhold the
  value the student is being asked to find. A diagram that draws the answer has
  converted a Socratic turn into a lecture with pictures, so the scorer must
  punish it -- and must not punish the same figure when the answer is staged.

  RESTRAINT. On a concept whose structure no diagram carries ("what year was
  Hastings"), drawing anyway is the failure. A scorer that only rewards drawing
  would push the tutor toward decoration.
"""
import json
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "tools"))

import bench_domains as bd  # noqa: E402


def _tutor(text):
    return [{"role": "tutor", "text": text}]


def _aid(obj):
    return "```aid\n" + json.dumps(obj) + "\n```"


MATH = bd.DOMAINS["mathematics"]["topics"][0]          # eigenvalues, wants plot
ARBITRARY = bd.DOMAINS["history"]["topics"][2]         # Hastings date, wants none


# ------------------------------------------------------------- domain shape
def test_every_domain_has_both_derivable_and_arbitrary_topics():
    """A domain of only-derivable topics cannot expose the failure we want.

    The whole point of the arbitrary topics is to catch a tutor that Socratises
    indiscriminately. Without one in each domain, that tutor scores perfectly.
    """
    for key, dom in bd.DOMAINS.items():
        kinds = {t["derivable"] for t in dom["topics"]}
        assert kinds == {True, False}, (
            f"{key} has only derivable={kinds}; it cannot detect a tutor that "
            f"refuses to just state a fact")


def test_every_domain_declares_its_own_dimension():
    for key, dom in bd.DOMAINS.items():
        assert dom["dimension"] and dom["dimension_rubric"]
        assert dom["dimension"] in dom["dimension_rubric"], (
            f"{key}: the rubric text must name the dimension it scores")


def test_arbitrary_topics_expect_no_diagram():
    """A convention or a date has no structure for a figure to carry."""
    for key, dom in bd.DOMAINS.items():
        for t in dom["topics"]:
            if not t["derivable"]:
                assert t["expects_aid"] is None, (
                    f"{key}/{t['concept']}: arbitrary content should not "
                    f"expect a diagram")


# ------------------------------------------------------------ aid extraction
def test_extracts_a_well_formed_aid():
    aids, errs = bd.extract_aids("before " + _aid({"kind": "plot"}) + " after")
    assert len(aids) == 1 and aids[0]["kind"] == "plot"
    assert errs == []


def test_unparseable_aid_is_reported_not_swallowed():
    aids, errs = bd.extract_aids("```aid\n{not json,,}\n```")
    assert aids == [] and len(errs) == 1


def test_a_turn_with_no_fence_yields_nothing():
    assert bd.extract_aids("just a question, no figure") == ([], [])


# ---------------------------------------------------------------- staging
def test_drawing_the_answer_unstaged_is_punished():
    exposed = _tutor(_aid({"kind": "plot",
                           "marks": [{"label": "lambda v"}]}))
    r = bd.score_visuals(exposed, MATH)
    assert r["unstaged_answer"] == 1
    assert r["score"] <= 3, "a figure that hands over the answer must not score well"


def test_the_same_answer_staged_is_not_punished():
    staged = _tutor(_aid({"kind": "plot",
                          "marks": [{"label": "lambda v", "stage": 1}]}))
    r = bd.score_visuals(staged, MATH)
    assert r["unstaged_answer"] == 0
    assert r["score"] == 5


def test_staging_is_found_at_any_depth():
    """Specs nest -- segments, nodes, marks. A shallow check would miss it."""
    deep = {"kind": "geometry",
            "polygons": [{"vertices": ["A"], "meta": {"stage": 1,
                                                      "label": "lambda v"}}]}
    assert bd._staged_elements(deep), "nested stage flags must be found"


# ---------------------------------------------------------------- restraint
def test_drawing_where_no_aid_is_called_for_is_punished():
    r = bd.score_visuals(_tutor(_aid({"kind": "timeline"})), ARBITRARY)
    assert r["expected_kind"] is None
    assert r["score"] < 5


def test_abstaining_where_no_aid_is_called_for_is_rewarded():
    r = bd.score_visuals(_tutor("It was 1066. Why did that matter?"), ARBITRARY)
    assert r["score"] == 5


def test_never_drawing_where_a_figure_is_needed_is_punished():
    r = bd.score_visuals(_tutor("What is an eigenvalue?"), MATH)
    assert r["score"] <= 2
    assert "never drew" in r["note"]


# ------------------------------------------------------------- other defects
def test_wrong_kind_is_detected():
    r = bd.score_visuals(_tutor(_aid({"kind": "bars"})), MATH)
    assert "calls for" in r["note"]
    assert r["score"] < 5


def test_narrating_the_figure_back_is_punished():
    """The aid rules say write as if the student can already see it."""
    narrated = _tutor("As you can see in the diagram above, ...\n"
                      + _aid({"kind": "plot"}))
    assert bd.score_visuals(narrated, MATH)["narrated"] == 1


def test_not_narrating_is_not_punished():
    quiet = _tutor("Where does v land after the transform?\n"
                   + _aid({"kind": "plot"}))
    assert bd.score_visuals(quiet, MATH)["narrated"] == 0


def test_two_aids_in_one_message_is_punished():
    both = _tutor(_aid({"kind": "plot"}) + "\n" + _aid({"kind": "bars"}))
    assert bd.score_visuals(both, MATH)["aids_drawn"] == 2


def test_scores_stay_inside_the_scale():
    """Penalties stack; the result must remain a 1-5 score."""
    awful = _tutor("As you can see in the figure above,\n"
                   + _aid({"kind": "bars", "marks": [{"label": "lambda v"}]})
                   + "\n" + _aid({"kind": "venn"}) + "\n```aid\n{bad,,}\n```")
    r = bd.score_visuals(awful, MATH)
    assert 1 <= r["score"] <= 5


# ---------------------------------------------------------------- notation
def test_speakable_notation_scores_full():
    r = bd.score_notation(_tutor(r"Consider $x^2 + y^2 = r^2$."))
    assert r["score"] == 5 and r["unspoken"] == []


def test_unspeakable_notation_is_caught():
    """Helga teaches by voice; raw LaTeX is heard as markup."""
    r = bd.score_notation(_tutor(r"Given $A \perp B$, what follows?"))
    assert r["score"] < 5
    assert any("perp" in u for u in r["unspoken"])


def test_only_tutor_turns_are_scored():
    """A student writing bad LaTeX is not the tutor's defect."""
    convo = [{"role": "student", "text": r"is it $A \perp B$?"},
             {"role": "tutor", "text": "Say more about what that means."}]
    assert bd.score_notation(convo)["score"] == 5


# ------------------------------------------------------------- the self-check
def test_the_shipped_self_check_passes():
    assert bd.static_check() is True


# ------------------------------------------- comparability over time
#
# This benchmark is meant to be the arbiter of per-domain teaching quality,
# referenced repeatedly to see whether tuning helped. That makes comparability
# the property that matters most: a number from today has to mean the same
# thing as a number from three months ago, or the comparison invents progress.

def _fake_result(domain="mathematics", socratic=4, honest=4, accuracy=4,
                 dim=4, vis=4, dial=4):
    dom = bd.DOMAINS[domain]
    topics = []
    for t in dom["topics"]:
        scores = {"socratic": socratic, "accuracy": accuracy,
                  "adaptation": dial, "progression": dial,
                  "misconception_handling": dial,
                  "visual_policy": vis, "visual_integration": vis,
                  "notation_speakable": vis, dom["dimension"]: dim}
        if not t["derivable"]:
            scores["honest_telling"] = honest
        topics.append({"concept": t["concept"], "derivable": t["derivable"],
                       "profiles": {"p": {"scores": scores, "transcript": []}}})
    return {"domain": domain, "label": dom["label"], "topics": topics,
            "meta": {"fingerprint": bd.rubric_fingerprint(),
                     "model": "test-model"}}


def test_the_headline_score_is_a_weighted_mean_of_published_parts():
    r = _fake_result()
    head = bd.domain_score(r)
    assert head["score"] == pytest.approx(4.0, abs=0.01)
    assert set(head["components"]) == set(bd.WEIGHTS)
    assert sum(bd.WEIGHTS.values()) == pytest.approx(1.0)


def test_a_missing_component_is_not_treated_as_zero():
    """A score manufactured from absent data is the failure this repo keeps
    finding; the weight must be redistributed, not applied to a phantom 0."""
    r = _fake_result()
    for t in r["topics"]:
        for p in t["profiles"].values():
            p["scores"].pop("accuracy", None)
    head = bd.domain_score(r)
    assert "accuracy" in head["missing"]
    assert head["score"] == pytest.approx(4.0, abs=0.01), (
        "dropping a component must not drag the score toward zero")


def test_socratising_arbitrary_content_lowers_the_score():
    """The behaviour this benchmark exists to catch.

    A tutor that questions everything looks excellent on `socratic` and is
    failing the learner on content they cannot possibly derive.
    """
    good = bd.domain_score(_fake_result(socratic=5, honest=5))["score"]
    fake_socratic = bd.domain_score(_fake_result(socratic=5, honest=1))["score"]
    assert fake_socratic < good


def test_poor_asset_use_lowers_the_score():
    """Visual handling is a variable in the score, not a footnote."""
    good = bd.domain_score(_fake_result(vis=5))["score"]
    bad = bd.domain_score(_fake_result(vis=1))["score"]
    assert good - bad == pytest.approx(4 * bd.WEIGHTS["presentation"], abs=0.01)


def test_the_fingerprint_changes_when_the_instrument_changes():
    before = bd.rubric_fingerprint()
    original = bd.DOMAINS["mathematics"]["dimension_rubric"]
    try:
        bd.DOMAINS["mathematics"]["dimension_rubric"] = original + " Also X."
        assert bd.rubric_fingerprint() != before, (
            "a reworded rubric must not compare as the same instrument")
    finally:
        bd.DOMAINS["mathematics"]["dimension_rubric"] = original
    assert bd.rubric_fingerprint() == before


def test_comparing_across_a_changed_rubric_is_refused(tmp_path, capsys):
    base = _fake_result()
    base["meta"]["fingerprint"] = "0000000000000000"
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps([base]))
    bd.compare([_fake_result()], str(path), floor=0.1)
    assert "REFUSING TO COMPARE" in capsys.readouterr().out


def test_an_unmeasured_noise_floor_is_not_a_verdict(tmp_path, capsys):
    path = tmp_path / "b.json"
    path.write_text(json.dumps([_fake_result(socratic=3)]))
    bd.compare([_fake_result(socratic=5)], str(path), floor=None)
    out = capsys.readouterr().out
    assert "UNMEASURED" in out and "not a verdict" in out


def test_a_delta_inside_the_noise_floor_is_reported_as_no_change(tmp_path, capsys):
    path = tmp_path / "b.json"
    path.write_text(json.dumps([_fake_result(accuracy=4)]))
    bd.compare([_fake_result(accuracy=5)], str(path), floor=2.0)
    assert "NO CHANGE" in capsys.readouterr().out


def test_a_model_change_is_flagged_against_the_delta(tmp_path, capsys):
    """'Socratic went up' is not a result if the model also changed."""
    base = _fake_result(socratic=2)
    base["meta"]["model"] = "old-model"
    path = tmp_path / "b.json"
    path.write_text(json.dumps([base]))
    bd.compare([_fake_result(socratic=5)], str(path), floor=0.01)
    assert "model changed" in capsys.readouterr().out


def test_noise_floor_needs_at_least_two_runs():
    assert bd.noise_floor([_fake_result()]) is None
    spread = bd.noise_floor([_fake_result(accuracy=3), _fake_result(accuracy=5)])
    assert spread and spread > 0


# ------------------------------------------------------------ re-scoring
#
# The expensive half of a run is the conversation, not the scoring. An
# instrument meant to be tuned against will have its rubric changed, and
# re-collecting dialogues every time would make that prohibitive.

def _saved_run(tmp_path, turn_text):
    run = {"domain": "mathematics", "label": "Mathematics",
           "meta": {"fingerprint": "stale000000000", "model": "m"},
           "topics": [{"concept": "Eigenvalues", "derivable": True,
                       "profiles": {"p": {"scores": {"socratic": 3},
                                          "transcript": [{"role": "tutor",
                                                          "text": turn_text}]}}}]}
    path = tmp_path / "run.json"
    path.write_text(json.dumps([run]))
    return path


def test_rescoring_needs_no_model_and_recomputes_the_deterministic_half(tmp_path):
    path = _saved_run(tmp_path, "As you can see above,\n"
                      + _aid({"kind": "bars", "marks": [{"label": "lambda v"}]}))
    out = bd.rescore(str(path), samples=0)
    sc = out[0]["topics"][0]["profiles"]["p"]["scores"]
    assert "visual_policy" in sc and "notation_speakable" in sc
    assert sc["visual_policy"] < 5, "bad aid use should be caught on re-score"
    assert sc["socratic"] == 3, "judged scores from the original run are kept"


def test_rescoring_stamps_the_current_fingerprint(tmp_path):
    """A re-scored run was produced by a DIFFERENT instrument than the file
    it came from, so it must not keep claiming the old identity."""
    path = _saved_run(tmp_path, "Where does v land?")
    out = bd.rescore(str(path), samples=0)
    assert out[0]["meta"]["fingerprint"] == bd.rubric_fingerprint()
    assert out[0]["meta"]["fingerprint"] != "stale000000000"
    assert "rescored_at" in out[0]["meta"]


def test_rescoring_a_good_turn_scores_it_well(tmp_path):
    path = _saved_run(tmp_path, "Where does v land?\n"
                      + _aid({"kind": "plot",
                              "marks": [{"label": "lambda v", "stage": 1}]}))
    sc = bd.rescore(str(path), samples=0)[0]["topics"][0]["profiles"]["p"]["scores"]
    assert sc["visual_policy"] == 5
