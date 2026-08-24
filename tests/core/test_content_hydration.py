
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

        # Assertions — fallback returns minimal structure with Hydration failed marker
        self.assertIn("# Topological Data Analysis", result)
        self.assertIn("## Metadata", result)
        self.assertIn("## Core Explanation", result)
        self.assertIn("[Hydration failed]", result)
        print("\n[SUCCESS] Fallback logic produced minimal structure on LLM failure.")

    @patch('services.core.course_builder.llm_generate')
    def test_hydrate_uses_llm_text_when_no_sources(self, mock_llm):
        """No research service → the model's own knowledge, and the course STILL builds.

        WHY THE MOCK RESPONSE IS LONG NOW
        ---------------------------------
        This test used to return a 20-word structuring response. That is under
        the 40-word usability floor in `_condense_and_structure_content`, so all
        three attempts were discarded and the concept came back as a
        `[Hydration failed]` stub — the test was not exercising "the model wrote
        the content", it was exercising "the model produced nothing".

        It passed anyway, because the hydrator counted that stub as a hydrated
        concept and shipped the course as "ready". A one-concept course made
        entirely of placeholders was reported as finished. Now a stub counts as
        a failure and the >50% gate refuses the build, so the old mock made this
        test assert the opposite of its own name.

        The two cases are different and both matter:
          * research unavailable, model wrote a real concept  → degraded
            SUCCESS. Low confidence, marked as leaning on model knowledge, and
            the course builds. That is this test.
          * model produced nothing, we wrote `[Hydration failed]` → a genuine
            failure that must count as one. That is
            `test_condense_and_structure_fallback` plus the abort gate.
        """

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

        # LLM returns content for both generation and structuring.
        #
        # The structured body must clear the 40-word floor in
        # _condense_and_structure_content. Below it every attempt is discarded
        # and the concept ends as a "[Hydration failed]" stub — which, now that
        # a stub counts as a failed concept, aborts a one-concept build before
        # this test's assertions can run. The short body was masking the fact
        # that this test never actually reached the success path it names.
        structured = (
            "## Core Definition\n"
            + ("A test concept explained at length from the model's own "
               "knowledge, because the research service could not be "
               "reached. ") * 6
            + "\n## Contextual Explanation\nDetailed explanation follows.\n"
              "## Socratic Hook\nWhy?\n")

        # LLM returns content for both generation and structuring
        llm_calls = []
        def mock_llm_side_effect(prompt, **kwargs):
            llm_calls.append(prompt)
            if "Explain the concept" in prompt:
                return "This is generated LLM content about the test concept."
            return structured

        mock_llm.side_effect = mock_llm_side_effect

        # `helga-research` does not resolve in the test environment, which IS
        # the scenario under test: no research, model-only grounding.
        self.hydrator.hydrate("test_uid")

        # Verify save_concept_content was called (hydration completed)
        self.assertTrue(self.mock_storage.courses.save_concept_content.called,
                       "Concept content should have been saved")

        # Verify LLM was called (no sources, so LLM generates)
        self.assertGreater(len(llm_calls), 0, "LLM should have been called for content generation")

        # The whole point: an ungrounded but real concept is a degraded
        # SUCCESS. If the abort gate ever starts failing this, it has stopped
        # distinguishing "no research" from "no content" and will kill every
        # build that runs without the research service.
        self.assertEqual(course_data.get("status"), "ready",
                         "a research-less build that produced real content "
                         "must still ship")

        # ...and it must be honest about being ungrounded rather than silent.
        saved = self.mock_storage.courses.save_concept_content.call_args[0][2]
        self.assertNotIn("[Hydration failed]", saved,
                         "real model content must not be recorded as a stub")
        self.assertIn("grounding unavailable", saved.lower(),
                      "a concept whose research call never completed must say "
                      "so, not claim sources were scarce")

if __name__ == '__main__':
    unittest.main()
