"""B17.1–B17.3 tests: grade-band profiles, band-aware prompts, band-bounded
Bloom/mastery (design spec 02 §8 acceptance criteria).
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), 'services/core'))

import logging
logging.basicConfig = MagicMock()
logging.FileHandler = MagicMock()

from services.common.prompts import (
    GRADE_BAND_PROFILES, get_band_profile,
    get_socratic_tutor_prompt, get_socratic_grading_prompt,
    SOCRATIC_QUESTION_TYPES,
)

core_deps = {'yaml': MagicMock(), 'kuzu': MagicMock(), 'libzim': MagicMock()}
with patch.dict('sys.modules', core_deps):
    from fsm_logic import MnemosyneFSM

BANDS = ["K-2", "3-5", "6-8", "9-12"]


def _sys_prompt(band):
    msgs = get_socratic_tutor_prompt("Some concept text.", [("hi", "hello")],
                                     grade_band=band)
    return msgs[0]["content"]


def _bare_fsm(band="6-8"):
    with patch.object(MnemosyneFSM, '__init__', lambda self, *a, **kw: None):
        fsm = MnemosyneFSM.__new__(MnemosyneFSM)
    fsm.grade_band = band
    fsm.concept_correct_streak = 0
    fsm.concept_question_count = 0
    fsm.current_bloom_level = 1
    fsm.concept_bloom_target = 1
    fsm.course_bloom_floor = 1
    fsm.course_bloom_ceiling = 6
    fsm.passed_question_types = set()
    return fsm


class TestProfiles(unittest.TestCase):
    def test_four_bands_exist(self):
        assert set(GRADE_BAND_PROFILES.keys()) == set(BANDS)

    def test_word_caps_monotonic(self):
        caps = [GRADE_BAND_PROFILES[b]["max_words"] for b in BANDS]
        assert caps == sorted(caps) and len(set(caps)) == len(caps)

    def test_bloom_ceilings_monotonic(self):
        ceilings = [GRADE_BAND_PROFILES[b]["bloom_ceiling"] for b in BANDS]
        assert ceilings == sorted(ceilings)

    def test_unknown_band_falls_back(self):
        assert get_band_profile("nonsense") == GRADE_BAND_PROFILES["6-8"]
        assert get_band_profile(None) == GRADE_BAND_PROFILES["6-8"]


class TestTutorPrompt(unittest.TestCase):
    def test_band_caps_injected(self):
        for band in BANDS:
            p = _sys_prompt(band)
            cap = GRADE_BAND_PROFILES[band]["max_words"]
            assert f"at most {cap} words" in p, f"missing cap for {band}"

    def test_persona_differs_by_band(self):
        assert "very young child" in _sys_prompt("K-2")
        assert "rigorous academic mentor" in _sys_prompt("9-12")

    def test_markdown_gated_by_band(self):
        assert "NEVER use markdown" in _sys_prompt("K-2")
        assert "NEVER use markdown" not in _sys_prompt("9-12")

    def test_register_always_present(self):
        for band in BANDS:
            assert "GRADE REGISTER:" in _sys_prompt(band)

    def test_style_modifier_composes_not_replaces(self):
        msgs = get_socratic_tutor_prompt("ctx", [("a", "b")],
                                         style_modifier="drill sergeant",
                                         grade_band="K-2")
        p = msgs[0]["content"]
        assert "very young child" in p          # band persona survives
        assert "rapid-fire" in p                 # style overlay applied


class TestGradingPrompt(unittest.TestCase):
    def test_k2_calibration_protects_terse_answers(self):
        msgs = get_socratic_grading_prompt("Counting", "How many?", "three",
                                           grade_band="K-2")
        p = msgs[0]["content"]
        assert "GRADE CALIBRATION" in p and "K-2" in p
        assert "do NOT demand written explanation" in p

    def test_912_expects_justification(self):
        msgs = get_socratic_grading_prompt("Entropy", "Why?", "entropy",
                                           grade_band="9-12")
        assert "Expect justification" in msgs[0]["content"]

    def test_no_band_no_calibration(self):
        msgs = get_socratic_grading_prompt("X", "Q?", "a")
        assert "GRADE CALIBRATION" not in msgs[0]["content"]


class TestMasteryGate(unittest.TestCase):
    def test_k2_gate_passes_early(self):
        fsm = _bare_fsm("K-2")
        fsm.concept_correct_streak = 2
        fsm.concept_question_count = 2
        fsm.current_bloom_level = 1
        fsm.passed_question_types = {"clarify", "evidence"}
        assert fsm._check_mastery_gate()

    def test_912_gate_needs_more(self):
        fsm = _bare_fsm("9-12")
        fsm.concept_correct_streak = 2
        fsm.concept_question_count = 3
        fsm.current_bloom_level = 6
        fsm.passed_question_types = {"clarify", "evidence", "implication"}
        assert not fsm._check_mastery_gate()   # needs streak 3 / 4 questions
        fsm.concept_correct_streak = 3
        fsm.concept_question_count = 4
        assert fsm._check_mastery_gate()

    def test_low_ceiling_course_can_complete(self):
        """Regression for baseline B3.5: bloom target above the course ceiling
        must not make the gate impossible."""
        fsm = _bare_fsm("K-2")
        fsm.concept_bloom_target = 5      # over-ambitious target
        fsm.course_bloom_ceiling = 3      # K-2 clamped ceiling
        fsm.current_bloom_level = 3
        fsm.concept_correct_streak = 2
        fsm.concept_question_count = 2
        fsm.passed_question_types = {"clarify", "evidence"}
        assert fsm._check_mastery_gate()


class TestBloomBounds(unittest.TestCase):
    def test_k2_clamps_ceiling(self):
        fsm = _bare_fsm("K-2")
        fsm._load_course_bloom_bounds({"bloom_floor": 1, "bloom_ceiling": 6})
        assert fsm.course_bloom_ceiling == 3
        assert fsm.course_bloom_floor == 1

    def test_912_keeps_course_bounds(self):
        fsm = _bare_fsm("9-12")
        fsm._load_course_bloom_bounds({"bloom_floor": 2, "bloom_ceiling": 5})
        assert fsm.course_bloom_ceiling == 5
        assert fsm.course_bloom_floor == 2

    def test_floor_never_exceeds_ceiling(self):
        fsm = _bare_fsm("K-2")
        fsm._load_course_bloom_bounds({"bloom_floor": 5, "bloom_ceiling": 6})
        assert fsm.course_bloom_floor <= fsm.course_bloom_ceiling == 3


if __name__ == '__main__':
    unittest.main()
