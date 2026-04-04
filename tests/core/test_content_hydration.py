
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

# Mock dependencies that might be missing or heavy
sys.modules['kuzu'] = MagicMock()
sys.modules['libzim'] = MagicMock()
sys.modules['sentence_transformers'] = MagicMock()
sys.modules['psutil'] = MagicMock()

from services.core.course_builder import ContentHydrator

class TestContentHydration(unittest.TestCase):
    def setUp(self):
        # Create mock storage instead of real db connection
        self.mock_storage = MagicMock()

        # Instantiate with storage mock (no db_path needed)
        self.hydrator = ContentHydrator(
            providers=[MagicMock()],
            storage=self.mock_storage
        )

    @patch('services.core.course_builder.llm_generate')
    def test_condense_and_structure_content_success(self, mock_llm):
        """Test that content is structured correctly when LLM succeeds."""

        # Mock LLM response representing a perfect run
        mock_response = """# Photosynthesis

## Metadata
- **Target Depth**: 1
- **Source**: verification

## Core Definition
Photosynthesis is the process by which plants convert light energy into chemical energy.

## Component Breakdown
- **Light Absorption**: Chlorophyll captures sunlight.
- **Carbon Fixation**: CO2 is converted into sugars.

## Contextual Explanation
Plants use sunlight, water, and CO2 to produce oxygen and sugar. This is the foundation of life on Earth.

## Misconceptions
- **Belief**: Plants get food from soil.
- **Correction**: Plants make their own food using light.

## Socratic Hook
If plants make their own food, why do they need water?

## Flashcards
- **Front**: What pigment captures light?
  **Back**: Chlorophyll
"""
        mock_llm.return_value = mock_response

        # Call the method
        result = self.hydrator._condense_and_structure_content(
            title="Photosynthesis",
            raw_text="Raw Wikipedia text about plants...",
            course_title="Biology 101",
            depth=1,
            complexity_role="Teacher",
            source="test_script",
            pedagogy_data={"misconceptions": [{"belief": "Plants eat soil", "correction": "Plants make food"}]},
            hierarchy_context={"module": "Plant Biology", "unit": "Energy", "lesson": "Light Reactions"},
            previous_concepts=["Cell Structure"]
        )

        # Assertions
        self.assertIn("# Photosynthesis", result)
        self.assertIn("## Core Definition", result)
        self.assertIn("## Misconceptions", result)
        self.assertIn("Plants get food from soil", result)
        print("\n[SUCCESS] Hydration produced valid Strict Markdown.")

    @patch('services.core.course_builder.llm_generate')
    def test_condense_and_structure_fallback(self, mock_llm):
        """Test that fallback logic works when LLM fails."""

        # Mock LLM failure (returns None or raises exception)
        mock_llm.side_effect = Exception("LLM Generation Failed config error")

        # Call the method
        result = self.hydrator._condense_and_structure_content(
            title="Topological Data Analysis",
            raw_text="Complex math text...",
            course_title="Advanced Math",
            depth=3,
            complexity_role="Professor",
            source="test_script"
        )

        # Assertions
        self.assertIn("# Topological Data Analysis", result)
        self.assertIn("## Metadata", result)
        self.assertIn("(Fallback)", result)
        self.assertIn("## Contextual Explanation", result)
        self.assertIn("Complex math text...", result) # Raw text should be preserved
        print("\n[SUCCESS] Fallback logic used correctly preserved raw text and structure.")

    @patch('services.core.course_builder.llm_generate')
    def test_hydrate_uses_llm_text_when_no_sources(self, mock_llm):
        """Test that LLM-generated text is passed to structuring when no sources found."""

        # Setup: course with one concept, no source providers
        self.hydrator.provider = None  # No providers available

        course_data = {
            "title": "Test Course",
            "modules": [{
                "title": "Module 1",
                "uid": "m1",
                "units": [{
                    "title": "Unit 1",
                    "uid": "u1",
                    "lessons": [{
                        "title": "Lesson 1",
                        "uid": "l1",
                        "concepts": [{
                            "uid": "con_test",
                            "title": "Test Concept",
                            "learning_objectives": ["Understand testing"],
                            "complexity_role": "Intermediate"
                        }]
                    }]
                }]
            }]
        }

        self.mock_storage.courses.get_course.return_value = course_data
        self.mock_storage.courses.get_concept_content.return_value = None  # Not yet hydrated

        # LLM returns content for both generation and structuring
        llm_calls = []
        def mock_llm_side_effect(prompt, **kwargs):
            llm_calls.append(prompt)
            if "Explain the concept" in prompt:
                return "This is generated LLM content about the test concept."
            else:
                return "# Test Concept\n## Metadata\n## Core Definition\nA test concept.\n## Contextual Explanation\nDetailed explanation.\n## Socratic Hook\nWhy?"

        mock_llm.side_effect = mock_llm_side_effect

        self.hydrator.hydrate("test_uid")

        # Verify save_concept_content was called (hydration completed)
        self.assertTrue(self.mock_storage.courses.save_concept_content.called,
                       "Concept content should have been saved")

        # Verify LLM was called (no sources, so LLM generates)
        self.assertGreater(len(llm_calls), 0, "LLM should have been called for content generation")

if __name__ == '__main__':
    unittest.main()
