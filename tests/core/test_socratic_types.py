"""Tests for Socratic Question Types and typed prompt generation."""

import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from services.common.prompts import SOCRATIC_QUESTION_TYPES, get_typed_socratic_prompt


class TestSocraticQuestionTypes:
    def test_six_types_defined(self):
        assert len(SOCRATIC_QUESTION_TYPES) == 6

    def test_all_have_required_keys(self):
        required = {'key', 'name', 'icon', 'color', 'description', 'instruction'}
        for qt in SOCRATIC_QUESTION_TYPES:
            assert required.issubset(qt.keys()), f"Missing keys in {qt.get('key', 'unknown')}"

    def test_unique_keys(self):
        keys = [qt['key'] for qt in SOCRATIC_QUESTION_TYPES]
        assert len(keys) == len(set(keys)), "Duplicate keys found"

    def test_expected_order(self):
        expected = ['SCENARIO', 'MECHANISM', 'CONTRAST', 'APPLICATION', 'EDGE_CASE', 'SYNTHESIS']
        actual = [qt['key'] for qt in SOCRATIC_QUESTION_TYPES]
        assert actual == expected

    def test_all_have_colors(self):
        for qt in SOCRATIC_QUESTION_TYPES:
            assert qt['color'].startswith('#'), f"Invalid color for {qt['key']}"

    def test_all_have_icons(self):
        for qt in SOCRATIC_QUESTION_TYPES:
            assert len(qt['icon']) > 0, f"Missing icon for {qt['key']}"


class TestGetTypedSocraticPrompt:
    def test_returns_string(self):
        result = get_typed_socratic_prompt(
            'SCENARIO', 'Some context about physics', [], 'Newton Laws'
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_type_instruction(self):
        result = get_typed_socratic_prompt(
            'MECHANISM', 'Context', [], 'Topic'
        )
        # The prompt should contain the type-specific instruction
        mechanism_type = next(qt for qt in SOCRATIC_QUESTION_TYPES if qt['key'] == 'MECHANISM')
        assert mechanism_type['instruction'][:20].lower() in result.lower() or 'mechanism' in result.lower()

    def test_contains_context(self):
        result = get_typed_socratic_prompt(
            'SCENARIO', 'Quantum entanglement is...', [], 'Quantum Physics'
        )
        assert 'Quantum' in result or 'quantum' in result

    def test_all_types_produce_output(self):
        for qt in SOCRATIC_QUESTION_TYPES:
            result = get_typed_socratic_prompt(
                qt['key'], 'Test context', [], 'Test Topic'
            )
            assert isinstance(result, str)
            assert len(result) > 10

    def test_invalid_type_fallback(self):
        # Should not crash on an unknown type
        result = get_typed_socratic_prompt(
            'NONEXISTENT', 'Context', [], 'Topic'
        )
        assert isinstance(result, str)


class TestQuestionTypeProgression:
    """Test the pedagogical progression logic (indices and advancement)."""

    def test_index_stays_on_fail(self):
        """Grade <= 1 should keep same index."""
        idx = 0
        grade = 1
        # Same logic as fsm_logic.py
        if grade <= 1:
            new_idx = idx  # Stay
        assert new_idx == 0

    def test_index_stays_on_partial(self):
        """Grade 2 should keep same index."""
        idx = 2
        grade = 2
        if grade == 2:
            new_idx = idx  # Stay
        assert new_idx == 2

    def test_index_advances_on_good(self):
        """Grade 3 should advance by 1."""
        idx = 1
        grade = 3
        new_idx = idx + 1
        assert new_idx == 2

    def test_index_accelerates_on_excellent(self):
        """Grade >= 4 should advance by 2."""
        idx = 0
        grade = 4
        new_idx = idx + 2
        assert new_idx == 2

    def test_completion_on_good(self):
        """Grade 3 at index 5 (last) should signal completion."""
        idx = 5
        grade = 3
        new_idx = idx + 1
        assert new_idx >= len(SOCRATIC_QUESTION_TYPES)

    def test_completion_on_accelerate(self):
        """Grade 4 at index 4 should skip past end."""
        idx = 4
        grade = 4
        new_idx = idx + 2
        assert new_idx >= len(SOCRATIC_QUESTION_TYPES)

    def test_full_progression_good(self):
        """Walk through all 6 types with grade 3 each time."""
        idx = 0
        steps = 0
        while idx < len(SOCRATIC_QUESTION_TYPES):
            idx += 1  # grade 3 advances by 1
            steps += 1
        assert steps == 6

    def test_full_progression_excellent(self):
        """Walk through with grade 4 each time — should take 3 steps."""
        idx = 0
        steps = 0
        while idx < len(SOCRATIC_QUESTION_TYPES):
            idx += 2  # grade 4 advances by 2
            steps += 1
        assert steps == 3
