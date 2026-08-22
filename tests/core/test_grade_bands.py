"""Mode B grade bands, re-banded 2026-08-21 on research advice.

WHAT CHANGED AND WHY
--------------------
The old bands were K-2 | 3-5 | 6-8 | 9-12. Research found the discontinuity
that matters is READING FLUENCY — the "learning to read" to "reading to learn"
transition around the end of grade 2 — not a grade line. "K-2" therefore
spanned two genuinely different users: a kindergartener who cannot read,
cannot type, and whose speech an ASR mis-transcribes 20-40% of the time, and a
second-grader who is starting to do all three.

New bands: K-1 | 2-3 | 4-5 | 6-8 | 9-12.

MODE B ONLY. Mode A (adults) carries no band and resolves to the 6-8 default,
which is deliberately unchanged — none of this reaches the adult tutor.
"""
import os

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))

from services.common.prompts import (
    DEFAULT_GRADE_BAND, GRADE_BAND_PROFILES, LEGACY_GRADE_BANDS,
    get_band_profile, is_young_band,
)

BANDS = ["K-1", "2-3", "4-5", "6-8", "9-12"]


def test_the_five_bands_exist():
    assert set(GRADE_BAND_PROFILES) == set(BANDS)


def test_demands_grow_monotonically_with_age():
    """A younger child must never be asked for more than an older one."""
    for field in ("max_words", "max_sentences", "bloom_ceiling"):
        values = [GRADE_BAND_PROFILES[b][field] for b in BANDS]
        assert values == sorted(values), f"{field} not monotonic: {values}"


def test_the_youngest_band_is_genuinely_small():
    """15 words, one sentence, one idea — a 5-year-old holds ~4 items."""
    k1 = GRADE_BAND_PROFILES["K-1"]
    assert k1["max_words"] <= 15
    assert k1["max_sentences"] == 1
    assert k1["new_ideas"] == 1
    assert k1["gate_types"] == 1, (
        "only concrete question types are developmentally available at K-1")


def test_voice_is_on_where_the_child_cannot_read():
    for band in ("K-1", "2-3"):
        assert GRADE_BAND_PROFILES[band]["tts_default"] is True
    for band in ("4-5", "6-8", "9-12"):
        assert GRADE_BAND_PROFILES[band]["tts_default"] is False


def test_markdown_is_off_where_the_child_cannot_read_it():
    for band in ("K-1", "2-3", "4-5"):
        assert GRADE_BAND_PROFILES[band]["allow_markdown"] is False


# --------------------------------------------------- the silent-fallback trap
#
# Student records were written with the OLD band names. Without a mapping,
# get_band_profile("K-2") misses the dict and falls back to 6-8 — handing a
# five-year-old a 70-word adult register. Nothing errors. Nothing logs.

def test_legacy_band_names_still_resolve():
    for old in LEGACY_GRADE_BANDS:
        profile = get_band_profile(old)
        assert profile is not GRADE_BAND_PROFILES[DEFAULT_GRADE_BAND], (
            f"legacy band {old!r} silently fell back to the adult default")


def test_the_old_K2_maps_DOWN_not_up():
    """K-2 spanned the reading transition. Under-serving a second-grader is
    recoverable; over-facing a kindergartener is not."""
    assert get_band_profile("K-2") is GRADE_BAND_PROFILES["K-1"]


def test_an_unknown_band_still_falls_back_rather_than_raising():
    assert get_band_profile("wat") is GRADE_BAND_PROFILES[DEFAULT_GRADE_BAND]
    assert get_band_profile(None) is GRADE_BAND_PROFILES[DEFAULT_GRADE_BAND]


# ------------------------------------------------------------ young detection
def test_young_band_detection_covers_new_and_legacy_names():
    for band in ("K-1", "2-3", "K-2", "3-5"):
        assert is_young_band(band), f"{band} must count as a young learner"
    for band in ("4-5", "6-8", "9-12", None, ""):
        assert not is_young_band(band)


def test_the_aid_policy_shares_the_definition():
    """Two definitions would let the diagram budget and the safety filter
    disagree about who is a child."""
    from services.common import aid_policy
    assert aid_policy.is_young_band is is_young_band


# --------------------------------------------------------------- Mode A guard
def test_mode_A_is_untouched_by_the_rebanding():
    """The adult tutor resolves to 6-8 and must be unaffected."""
    adult = get_band_profile(None)
    assert adult["max_words"] == 70
    assert adult["persona"] == "a curious thinking-partner"
    assert not is_young_band(None)


# ------------------------------------------------- praise, per the evidence
#
# Brummelman et al. (2014): inflated praise ("amazing!") reduces
# challenge-seeking in children with low self-esteem, and person-praise
# ("you're so smart") makes children give up sooner after failure than
# process-praise ("you worked hard on that").

@pytest.mark.parametrize("band", ["K-1", "2-3", "4-5"])
def test_young_bands_ask_for_process_praise_not_person_praise(band):
    register = GRADE_BAND_PROFILES[band]["register"].lower()
    assert "praise" in register, f"{band} says nothing about praise"
    assert "effort" in register or "specific" in register


def test_the_youngest_band_names_the_praise_failure_mode():
    register = GRADE_BAND_PROFILES["K-1"]["register"]
    assert "you're so smart" in register.lower(), (
        "the anti-pattern must be named, not merely implied — a prompt that "
        "says 'praise well' measures 0/5 in this repository")


# ------------------------------------------- question types a child can answer
#
# The six Socratic types are an adult design. Mechanism and Synthesis require
# holding several elements in working memory at once — a 5-year-old holds
# about four items total. Edge Case requires knowing a rule well enough to see
# its boundary.

from services.common.prompts import (            # noqa: E402
    SOCRATIC_QUESTION_TYPES, question_types_for_band,
)


def _keys(band):
    return [q["key"] for q in question_types_for_band(band)]


def test_the_youngest_bands_get_only_concrete_types():
    for band in ("K-1", "2-3"):
        keys = set(_keys(band))
        assert keys == {"SCENARIO", "APPLICATION", "CONTRAST"}, keys
        assert "MECHANISM" not in keys and "SYNTHESIS" not in keys


def test_abstract_types_arrive_at_grade_four():
    keys = set(_keys("4-5"))
    assert {"MECHANISM", "SYNTHESIS", "EDGE_CASE"} <= keys


def test_older_bands_and_mode_A_keep_all_six():
    for band in ("6-8", "9-12", None):
        assert len(_keys(band)) == len(SOCRATIC_QUESTION_TYPES)


def test_legacy_bands_are_gated_too():
    assert set(_keys("K-2")) == {"SCENARIO", "APPLICATION", "CONTRAST"}


def test_the_canonical_order_is_preserved():
    """Indices are persisted in the session blob; reordering would move a
    learner to a different question type on reload."""
    order = [q["key"] for q in SOCRATIC_QUESTION_TYPES]
    for band in ("K-1", "4-5", "6-8"):
        got = _keys(band)
        assert got == [k for k in order if k in set(got)]


def test_the_mastery_gate_cannot_demand_more_types_than_the_band_offers():
    """A gate of 3 distinct types against 3 available means a K-1 child must
    pass EVERY form; the band sets 1 for exactly this reason."""
    for band in BANDS:
        available = len(_keys(band))
        required = GRADE_BAND_PROFILES[band]["gate_types"]
        assert required <= available, (
            f"{band}: gate needs {required} types but only {available} exist")
        if band == "K-1":
            assert required < available, (
                "K-1 must not require every available type")


# ------------------------------------ the silent fallbacks the re-banding left
#
# Renaming the bands broke five sites that matched the OLD names inline. None
# raised. None logged. Each simply stopped applying to young children:
#
#   1. grading calibration  -> a K-1 child answering "four" was graded against
#      the adult rubric ("Correct AND explains the reasoning") and earned a 2
#   2. hint ladder skip     -> young bands got the full adult 4-step ladder
#   3. create_student       -> the API rejected every new band with a 400
#   4. standards seed       -> new bands failed validation
#   5. FSM default band     -> defaulted to 9-12, not DEFAULT_GRADE_BAND
#
# This is why is_young_band() exists. These tests cover the sites it could not
# reach on its own.

def test_grading_is_calibrated_for_every_young_band():
    """A one-word answer from a five-year-old is a COMPLETE answer."""
    from services.common.prompts import get_socratic_grading_prompt
    for band in ("K-1", "2-3", "K-2", "3-5"):
        msgs = get_socratic_grading_prompt("Counting", "How many?", "four",
                                           grade_band=band)
        assert "GRADE CALIBRATION" in str(msgs), (
            f"{band}: graded against the adult rubric")


def test_only_young_bands_get_the_LENIENT_calibration():
    """9-12 also gets a calibration, but the STRICT one ("expect
    justification"). The failure being guarded is a young child receiving the
    strict rubric, or an older student the lenient one."""
    from services.common.prompts import get_socratic_grading_prompt
    LENIENT = "do NOT demand written explanation or mechanism from a young child"
    STRICT = "a bare correct term without reasoning is Grade 2"
    for band in ("K-1", "2-3", "K-2", "3-5"):
        text = str(get_socratic_grading_prompt("X", "q", "a", grade_band=band))
        assert LENIENT in text, f"{band} is graded like an adult"
        assert STRICT not in text
    for band in ("4-5", "6-8", None):
        assert LENIENT not in str(
            get_socratic_grading_prompt("X", "q", "a", grade_band=band))
    assert STRICT in str(
        get_socratic_grading_prompt("X", "q", "a", grade_band="9-12"))


def test_the_hint_ladder_shortens_for_young_learners():
    """A 4-item working memory cannot hold a four-step ladder."""
    from services.common.prompts import get_hint_prompt
    for band in ("K-1", "2-3", "K-2"):
        assert get_hint_prompt("card", "text", 2, grade_band=band) is not None
    import services.common.prompts as pr
    skips = {}
    for band in ("K-1", "2-3", "4-5", "6-8"):
        b = pr.LEGACY_GRADE_BANDS.get(band, band)
        skips[band] = {"K-1": 3, "2-3": 2, "4-5": 1}.get(b, 0)
    assert skips["K-1"] > skips["2-3"] > skips["4-5"] > skips["6-8"], skips


def test_the_api_accepts_every_current_and_legacy_band():
    """Rejecting a band makes a real student uncreatable."""
    import re as _re
    src = open(os.path.join(_ROOT, "services/web-ui/app.py"),
               encoding="utf-8").read()
    m = _re.search(r"if grade_band not in \(([^)]*)\)", src, _re.S)
    assert m, "the create_student band guard moved"
    allowed = set(_re.findall(r"'([^']+)'", m.group(1)))
    assert set(BANDS) <= allowed, f"API rejects {set(BANDS) - allowed}"
    assert {"K-2", "3-5"} <= allowed, "API rejects legacy bands still in records"


def test_the_standards_loader_accepts_every_current_band():
    from services.common.standards_loader import _BANDS
    assert set(BANDS) <= _BANDS, f"seed validation rejects {set(BANDS) - _BANDS}"


def test_the_fsm_default_band_matches_the_declared_default():
    """It defaulted to 9-12 — an adult with no student row got the 110-word
    rigorous-mentor register instead of the stated 6-8."""
    import re as _re
    src = open(os.path.join(_ROOT, "services/core/fsm_logic.py"),
               encoding="utf-8").read()
    assert 'self.grade_band = "9-12"' not in src
    assert "self.grade_band = DEFAULT_GRADE_BAND" in src


def test_no_runtime_site_indexes_the_unfiltered_question_types():
    """Band gating half-applied is worse than not applied — it looks done.

    Four call sites were converted and eight were missed, so a K-1 learner
    could still be routed to a Mechanism question the band excludes. Every
    RUNTIME index must go through _question_types(); only the import and a
    comment may name the raw list.
    """
    import re as _re
    src = open(os.path.join(_ROOT, "services/core/fsm_logic.py"),
               encoding="utf-8").read()
    offenders = []
    for i, line in enumerate(src.splitlines(), 1):
        if "SOCRATIC_QUESTION_TYPES" not in line:
            continue
        stripped = line.strip()
        if stripped.startswith("#") or stripped == "SOCRATIC_QUESTION_TYPES,":
            continue
        offenders.append(f"{i}: {stripped}")
    assert not offenders, (
        "these bypass band gating:\n  " + "\n  ".join(offenders))
