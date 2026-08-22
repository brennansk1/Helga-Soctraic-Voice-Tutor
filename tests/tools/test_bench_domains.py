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
import statistics
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


def test_no_figure_where_one_is_needed_is_punished():
    """Wording is "no figure shown", not "never drew": a figure can arrive
    from the build without the model drawing anything."""
    r = bd.score_visuals(_tutor("What is an eigenvalue?"), MATH)
    assert r["score"] <= 2
    assert "no figure shown" in r["note"]


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
    """Helga teaches by voice; markup that survives conversion is heard raw."""
    r = bd.score_notation(_tutor(r"Consider $\oiint_S \mathfrak{X}$."))
    assert r["score"] < 5
    assert any("oiint" in u for u in r["unspoken"])


def test_notation_the_converter_HANDLES_is_not_reported_as_unspeakable():
    """The scorer must run unspoken() on the SPOKEN form, not the source.

    Given the raw turn, unspoken() reports every command in it -- including
    \lambda, which speak() renders as "lambda" -- so any turn containing any
    maths scored as broken notation. That is the instrument marking the
    product down for working.
    """
    r = bd.score_notation(_tutor(
        r"If $A \perp\!\!\!\perp B \mid C$ and $\hat{\beta}$ is the estimate?"))
    assert r["score"] == 5, r["note"]
    assert r["unspoken"] == []


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


# ------------------------------------------------- the reuse path
#
# Assets are generated at BUILD time -- course_440a8494 shipped 44 of them for
# 24 concepts -- and a `reuse` decision attaches one straight to the UI with no
# model involvement. It therefore leaves no ```aid fence. A scorer that reads
# only the transcript text records "never drew" for a turn that showed a
# figure, which is the wrong way round: reuse is the PREFERRED path.

def _tutor_with_decision(text, action, slot=None):
    return [{"role": "tutor", "text": text,
             "aid_decision": {"action": action, "slot": slot,
                              "reason": "test", "suggested_kinds": []}}]


def test_a_reused_build_time_figure_counts_as_shown():
    r = bd.score_visuals(_tutor_with_decision("Where does v land?", "reuse",
                                              "opening"), MATH)
    assert r["aids_reused"] == 1
    assert r["figures_shown"] == 1
    assert r["score"] == 5, "reuse is the preferred path, not a failure"


def test_reuse_is_not_confused_with_the_model_drawing():
    r = bd.score_visuals(_tutor_with_decision("Where does v land?", "reuse"),
                         MATH)
    assert r["aids_drawn"] == 0, "no model call happened"
    assert "build-time" in r["note"]


def test_a_policy_that_never_asks_is_scored_differently_from_a_tutor_that_ignores_it():
    """Two different bugs needing two different fixes.

    'The policy never asked' is a policy defect. 'The policy asked and nothing
    came back' is a tutor defect. Collapsing them into one score hides which.
    """
    never_asked = bd.score_visuals(
        _tutor_with_decision("What is an eigenvalue?", "none"), MATH)
    asked_ignored = bd.score_visuals(
        _tutor_with_decision("What is an eigenvalue?", "generate"), MATH)
    assert never_asked["policy_asked"] == 0
    assert asked_ignored["policy_asked"] == 1
    assert asked_ignored["score"] < never_asked["score"]
    assert "never asked" in never_asked["note"]
    assert "none was produced" in asked_ignored["note"]


def test_reusing_where_no_figure_is_wanted_is_still_punished():
    r = bd.score_visuals(_tutor_with_decision("It was 1066. Why?", "reuse"),
                         ARBITRARY)
    assert r["figures_shown"] == 1
    assert r["score"] < 5


def test_a_transcript_with_no_decisions_still_scores():
    """Older saved runs predate the decision being recorded."""
    r = bd.score_visuals(_tutor("What is an eigenvalue?"), MATH)
    assert r["policy_asked"] == 0 and r["aids_reused"] == 0


# ------------------------------------- the judge must survive quoting LaTeX
#
# The judge is asked to quote the decisive tutor turn. On a maths transcript
# that quote contains raw backslashes -- "$Av=\lambda v$" -- which are not
# valid JSON escapes. A bare json.loads rejected the entire reply and the
# sample was dropped, so `notation_rigour` scored None on every maths run:
# the dimension that measures LaTeX handling, voided by LaTeX, in silence.

LATEX_REPLY = r'{"score": 1, "why": "the tutor wrote \'$Av=\lambda v$\' unspeakably"}'


def test_a_judge_reply_quoting_latex_is_parsed_not_dropped():
    got = bd._loads_tolerant(LATEX_REPLY)
    assert got is not None, "a maths judge reply must not be silently discarded"
    assert got["score"] == 1


def test_the_core_judge_uses_the_same_tolerance():
    import helgabench as hb
    assert hb._loads_tolerant(LATEX_REPLY) is not None


def test_ordinary_json_is_unaffected():
    assert bd._loads_tolerant('{"score": 4, "why": "fine"}')["score"] == 4


def test_prose_around_the_object_is_tolerated():
    assert bd._loads_tolerant('Sure!\n{"score": 3}\nHope that helps')["score"] == 3


def test_genuine_garbage_still_returns_none():
    """Tolerance must not become credulity."""
    assert bd._loads_tolerant("no object at all") is None
    assert bd._loads_tolerant("") is None


def test_a_float_score_is_accepted():
    """Judges return 4.5; int('4.5') raises and used to drop the sample."""
    assert bd._loads_tolerant('{"score": 4.5}')["score"] == 4.5


# ------------------------------------- the bench must measure THE PRODUCT
#
# run_dialogue calls the prompt builder and the model directly -- it never
# touches the FSM. A4.1a, the dialogue contract, lives in the FSM. So the
# instrument built to measure `socratic` could not see the fix built to raise
# it: a 90-word lecture reached the judge unmodified and scored a tutor that
# does not exist in the product.

def test_the_bench_enforces_the_dialogue_contract(monkeypatch):
    import helgabench as hb
    lecture = " ".join(["word"] * 140) + "."
    fixed = "You said vectors stretch — by how much?"
    seen = {"retries": 0, "carried": False}

    def fake(url, model, messages, **kw):
        seen["retries"] += 1
        joined = " ".join(str(m.get("content", "")) for m in messages).lower()
        seen["carried"] = "contract" in joined and "140 words" in joined
        return fixed

    monkeypatch.setattr(hb, "_chat_messages", fake)
    out = hb._apply_contract("u", "m", [{"role": "system", "content": "s"}],
                             lecture, "vectors stretch", [],
                             {"concept": "Eigenvalues", "context": "x"})
    assert out == fixed
    assert seen["retries"] == 1
    assert seen["carried"], "the correction must name the measurement"


def test_a_compliant_turn_costs_no_extra_call(monkeypatch):
    import helgabench as hb
    calls = {"n": 0}

    def fake(*a, **k):
        calls["n"] += 1
        return "x"

    monkeypatch.setattr(hb, "_chat_messages", fake)
    good = "You said vectors stretch — by how much?"
    out = hb._apply_contract("u", "m", [{"role": "system", "content": "s"}],
                             good, "vectors stretch", [],
                             {"concept": "E", "context": "x"})
    assert out == good and calls["n"] == 0


def test_a_retry_that_fixes_nothing_is_not_shipped(monkeypatch):
    """Otherwise this is prompt-only enforcement wearing a second call."""
    import helgabench as hb
    lecture = " ".join(["word"] * 140) + "."
    monkeypatch.setattr(hb, "_chat_messages",
                        lambda *a, **k: " ".join(["word"] * 130) + ".")
    out = hb._apply_contract("u", "m", [{"role": "system", "content": "s"}],
                             lecture, "vectors stretch", [],
                             {"concept": "E", "context": "x"})
    assert out == lecture


# ------------------------------------------------- per-dimension noise floors
#
# The composite floor is necessary and NOT sufficient. Measured on two
# IDENTICAL mathematics runs (2026-08-20): the composite moved 0.162 while
# `visual_integration` moved 1.20. A claim like "visual integration improved by
# a point" can therefore be pure noise while the headline delta looks safe.
#
# The reporting half of the same bug: the dimension table printed MEDIANS.
# Across those two identical runs the visual_integration median went 5 -> 1 --
# a four-point swing on a five-point scale, from nothing at all.

def _run_with(dim_values):
    """A result whose per-dialogue scores are exactly `dim_values`."""
    r = _fake_result()
    for t in r["topics"]:
        for p in t["profiles"].values():
            p["scores"].update(dim_values)
    return r


def test_a_dimension_floor_needs_two_runs():
    assert bd.dimension_floors([_fake_result()]) == {}


def test_dimension_floors_measure_each_dimension_separately():
    a = _run_with({"socratic": 2, "visual_integration": 5})
    b = _run_with({"socratic": 2, "visual_integration": 1})
    floors = bd.dimension_floors([a, b])
    assert floors["socratic"] == 0.0, "unchanged dimension has no floor"
    assert floors["visual_integration"] == 4.0


def test_judge_prose_is_not_mistaken_for_a_score():
    """`_visual_note` and friends are strings living inside `scores`."""
    a = _run_with({"socratic": 2, "_visual_note": "no figure shown"})
    b = _run_with({"socratic": 3, "_visual_note": "a figure was shown"})
    floors = bd.dimension_floors([a, b])
    assert "_visual_note" not in floors


def test_the_dimension_table_reports_means_not_medians(capsys):
    """A median over n=15 bimodal judgements flips on one sample."""
    r = _fake_result()
    profiles = [p for t in r["topics"] for p in t["profiles"].values()]
    for i, p in enumerate(profiles):
        p["scores"]["socratic"] = 5 if i % 2 else 1
    vals = [p["scores"]["socratic"] for p in profiles]
    mean, median = statistics.mean(vals), statistics.median(vals)
    assert mean != median, "the fixture must distinguish the two statistics"

    bd.summarise(r)
    line = [ln for ln in capsys.readouterr().out.splitlines()
            if "socratic " in ln][0]
    assert f"{mean:.2f}" in line, f"expected the mean {mean:.2f} in: {line}"
    assert f" {median:.2f} " not in line, f"that is the median: {line}"
    assert f"range {min(vals)}-{max(vals)}" in line, (
        "the spread must be visible, not just a point estimate")


def test_an_unstable_dimension_is_labelled_in_the_table(capsys):
    bd.summarise(_fake_result())
    out = capsys.readouterr().out
    vi = [ln for ln in out.splitlines() if "visual_integration" in ln][0]
    assert "unstable" in vi, (
        "visual_integration swings +/-1.20 on identical runs; the table must "
        "say so rather than print a confident number")


def test_compare_names_which_dimension_moved(tmp_path, capsys):
    base = _run_with({"accuracy": 2})
    base_path = tmp_path / "b.json"
    base_path.write_text(json.dumps([base]))
    bd.compare([_run_with({"accuracy": 5})], str(base_path), floor=0.162)
    out = capsys.readouterr().out
    assert "per dimension:" in out
    assert "accuracy" in out and "REAL" in out


def test_a_dimension_move_inside_its_own_floor_is_called_noise(tmp_path, capsys):
    """The exact case that nearly shipped: visual_integration +0.93."""
    base = _run_with({"visual_integration": 3})
    base_path = tmp_path / "b.json"
    base_path.write_text(json.dumps([base]))
    bd.compare([_run_with({"visual_integration": 4})], str(base_path), floor=0.162)
    line = [ln for ln in capsys.readouterr().out.splitlines()
            if "visual_integration" in ln][0]
    assert "noise" in line, f"1.00 < floor 1.20 must read as noise: {line}"


def test_a_verdict_does_not_turn_on_the_last_mantissa_bit(tmp_path, capsys):
    """1.40-1.80 is -0.4000000000000001, which read as REAL against a 0.40
    floor before the delta was rounded."""
    base = _run_with({"honest_telling": 1.8})
    base_path = tmp_path / "b.json"
    base_path.write_text(json.dumps([base]))
    bd.compare([_run_with({"honest_telling": 1.4})], str(base_path), floor=0.162)
    line = [ln for ln in capsys.readouterr().out.splitlines()
            if "honest_telling" in ln][0]
    assert "noise" in line, f"exactly at the floor is not movement: {line}"


# --------------------------------------------- the self-check must be able to fail
#
# `static_check` is a CI gate. It PRINTED the speakable and unspeakable notation
# scores and set its verdict from neither, so no result could fail it — and its
# "unspeakable" sample was `$A \perp B$`, which became speakable the day \perp
# was taught to the converter. It was gating on two passing cases and reporting
# a pass. A gate that cannot fail is worse than no gate: it is a green light
# wired to nothing.

def _run_static():
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = bd.static_check()
    return result, buf.getvalue()


def test_the_static_check_passes_on_a_working_tree():
    ok, out = _run_static()
    assert ok, out
    assert "Deterministic scorers behave." in out


def test_the_static_check_fails_when_the_notation_scorer_is_broken(monkeypatch):
    monkeypatch.setattr(bd, "score_notation",
                        lambda turns: {"score": 5, "note": "stub", "unspoken": []})
    ok, out = _run_static()
    assert ok is False, "a scorer that calls everything speakable must fail"
    assert "SCORERS BROKEN" in out


def test_the_static_check_fails_when_the_visual_scorer_is_broken(monkeypatch):
    monkeypatch.setattr(bd, "score_visuals",
                        lambda turns, topic: {"score": 5, "note": "stub",
                                              "aids_drawn": 0, "narrated": 0,
                                              "unstaged_answer": 0,
                                              "expected_kind": None})
    ok, out = _run_static()
    assert ok is False, "a scorer that rewards everything must fail"


def test_the_unspeakable_sample_is_actually_unspeakable():
    """The sample must stay ahead of the speech converter.

    `$A \\perp B$` was the sample until \\perp was taught to the converter, at
    which point the check compared two speakable strings forever.
    """
    r = bd.score_notation([{"role": "tutor",
                            "text": r"Consider $\oiint_S \mathfrak{X}$."}])
    assert r["score"] < 5 and r["unspoken"], (
        "the converter learned this notation; pick a harder sample")


# ------------------------------- the bench must measure the system that ships
#
# `get_socratic_tutor_prompt` takes 14 inputs. The FSM supplies all of them;
# this bench supplied FOUR — and the fingerprint did not cover which, so a
# baseline taken with a different set would have compared as though the two
# instruments matched.
#
# The concrete cost: A.2's turn state is built from graded answers, nothing in
# the bench produced a grade, so the one intervention aimed at the semantic
# quality of a question was invisible to the instrument measuring exactly that.

def test_the_fingerprint_covers_which_production_inputs_are_supplied():
    before = bd.rubric_fingerprint()
    original = bd.BENCH_PROMPT_INPUTS
    try:
        bd.BENCH_PROMPT_INPUTS = original + ("misconceptions",)
        assert bd.rubric_fingerprint() != before, (
            "supplying a different set of production inputs measures a "
            "different system and must refuse to compare against an old "
            "baseline")
    finally:
        bd.BENCH_PROMPT_INPUTS = original
    assert bd.rubric_fingerprint() == before


def test_turn_state_is_among_the_supplied_inputs():
    assert "turn_state" in bd.BENCH_PROMPT_INPUTS


def test_grading_is_declared_in_the_fingerprint():
    """Turning grading off changes what the tutor is told, so it is an
    instrument change, not a speed setting."""
    import json as _json
    payload = _json.dumps(bd.rubric_fingerprint.__doc__ or "")
    # the flag itself must be inside the hashed payload, not merely nearby
    src = open(bd.__file__, encoding="utf-8").read()
    assert '"grade_answers": True' in src


# ------------------------------------------------------- the bench-side grader
def test_the_grader_returns_the_shape_turn_state_expects(monkeypatch):
    import helgabench as hb
    monkeypatch.setattr(hb, "_chat_messages", lambda *a, **k:
                        '{"grade": 1, "reason": "confused vector with scalar",'
                        ' "missing_concepts": ["non-zero requirement"]}')
    topic = {"concept": "Eigenvalues", "context": "Av = lambda v."}
    g = hb._grade_student_turn("u", "m", topic, "What is it?", "the vector")
    assert g["grade"] == 1 and g["graded"] is True
    assert g["missing_concepts"] == ["non-zero requirement"]

    from services.common.turn_state import TurnState
    ts = TurnState()
    ts.ask("What is it?")
    ts.record("the vector", g)
    assert "STILL WRONG" in ts.render()


@pytest.mark.parametrize("reply", ["not json", '{"reason":"no grade"}', ""])
def test_an_ungradeable_reply_costs_the_state_entry_not_the_dialogue(
        monkeypatch, reply):
    import helgabench as hb
    monkeypatch.setattr(hb, "_chat_messages", lambda *a, **k: reply)
    topic = {"concept": "X", "context": "y"}
    assert hb._grade_student_turn("u", "m", topic, "q", "a") is None


def test_a_dead_model_does_not_raise_out_of_the_grader(monkeypatch):
    import helgabench as hb

    def boom(*a, **k):
        raise RuntimeError("model down")
    monkeypatch.setattr(hb, "_chat_messages", boom)
    assert hb._grade_student_turn("u", "m", {"concept": "X"}, "q", "a") is None


# ----------------------------------- the last withheld production input
#
# One of the five student profiles is defined as holding "ONE specific,
# confidently-stated misconception". The bench SCORED `misconception_handling`
# while withholding the concept's misconception list — so the tutor was asked
# to catch a belief it had no list to check against, and `adaptation` was
# scored on a learner it could not recognise.

def test_every_topic_declares_its_misconceptions():
    for key, dom in bd.DOMAINS.items():
        for t in dom["topics"]:
            mis = t.get("misconceptions")
            assert mis, f"{key}/{t['concept']}: no misconceptions declared"
            assert all(isinstance(m, str) and m.strip() for m in mis)


def test_misconceptions_are_beliefs_not_restatements():
    """A misconception must be something a learner could WRONGLY believe, not
    a paraphrase of the correct answer."""
    for key, dom in bd.DOMAINS.items():
        for t in dom["topics"]:
            for m in t["misconceptions"]:
                assert len(m.split()) >= 3, f"{key}/{t['concept']}: '{m}' too thin"


def test_misconceptions_are_in_the_fingerprint_via_the_input_set():
    assert "misconceptions" in bd.BENCH_PROMPT_INPUTS


def test_the_bench_actually_supplies_them():
    """The third instance of 'computed but never passed' would be the fourth
    bug of that shape in this repository."""
    import os
    src = open(os.path.join(_ROOT, "tools/helgabench.py"), encoding="utf-8").read()
    assert 'misconceptions=topic.get("misconceptions")' in src


# ------------------------- the judge must not change when the tutor does
#
# One client drove both the tutor and the judge until 2026-08-21. Setting
# OLLAMA_MODEL to compare models therefore swapped the JUDGE too — changing
# the instrument and the subject at the same time, which makes any comparison
# meaningless. It surfaced as `JUDGE MISCALIBRATED` when a 12B was tried, which
# is the harness catching it; a model that happened to pass calibration would
# have produced a plausible and worthless number instead.

def test_no_judged_call_uses_the_tutor_client():
    import inspect
    import re
    src = inspect.getsource(bd.run_domain)
    on_tutor = (re.findall(r"_median_judged\(\s*client", src)
                + re.findall(r"hb\.judge\(client", src))
    assert not on_tutor, (
        f"{len(on_tutor)} judged call(s) still use the tutor's client; a model "
        f"comparison would move the judge with the subject")


def test_the_judge_model_is_part_of_the_fingerprint():
    before = bd.rubric_fingerprint()
    original = bd.JUDGE_MODEL
    try:
        bd.JUDGE_MODEL = "some-other-judge"
        assert bd.rubric_fingerprint() != before, (
            "a different judge is a different instrument and must refuse to "
            "compare against an old baseline")
    finally:
        bd.JUDGE_MODEL = original
    assert bd.rubric_fingerprint() == before
