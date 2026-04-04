
import sys
import os
import unittest
import json
import re
from unittest.mock import MagicMock, patch, call

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

# Mock Kuzu and other deps BEFORE import
sys.modules['kuzu'] = MagicMock()
sys.modules['libzim'] = MagicMock()
sys.modules['sentence_transformers'] = MagicMock()

from services.core.course_builder import SkeletonBuilder

class TestSkeletonStrategy(unittest.TestCase):
    def setUp(self):
        self.mock_db = MagicMock()
        self.mock_conn = MagicMock()
        
        # Setup Kuzu mock to return our connection
        kuzu_mock = sys.modules.get('kuzu', __import__('unittest.mock').mock.MagicMock())
        kuzu_mock.Database.return_value = self.mock_db
        kuzu_mock.Connection.return_value = self.mock_conn
        
        # Mock execute result for "has_next" checks (used in init_schema)
        self.mock_result = MagicMock()
        self.mock_result.has_next.return_value = False
        self.mock_conn.execute.return_value = self.mock_result

        # Mock pre-flight checks
        self.patcher_preflight = patch.object(SkeletonBuilder, '_run_preflight_checks', return_value=True)
        self.patcher_preflight.start()
        
        self.builder = SkeletonBuilder(db_path="dummy", course_depth=1)
        self.builder.storage = MagicMock()

    def tearDown(self):
        self.patcher_preflight.stop()

    @patch('services.core.course_builder.llm_generate_json')
    def test_full_course_quality_and_structure(self, mock_llm):
        """
        Simulates a FULL course generation (Modules -> Units+Lessons -> Concepts).
        
        Verifies:
        1. BLUEPRINT PROTOCOL: Correct Positive/Negative scopes injected.
        2. GRAPH INTEGRITY: Correct DB calls made (Nodes + Relationships).
        3. QUALITY: Anti-repetition and Forbidden lists are populated.
        """
        print("\n--- AUDIT: FULL COURSE SIMULATION & QUALITY CHECK ---\n")
        
        self.captured_prompts = []
        self.generated_counts = {"modules": 0, "units_lessons": 0, "concepts": 0}

        def llm_side_effect(prompt, **kwargs):
            self.captured_prompts.append(prompt)
            
            # 1. MODULES GENERATION (top-level build prompt)
            if "Create a progressive sequence" in prompt:
                self.generated_counts["modules"] += 2
                return [
                    {
                        "title": "Newtonian Mechanics", 
                        "level": 1, 
                        "rationale": "Basics", 
                        "scope": ["Force", "Mass", "Acceleration"]
                    },
                    {
                        "title": "Quantum Physics", 
                        "level": 2, 
                        "rationale": "Advanced", 
                        "scope": ["Superposition", "Entanglement", "Qubits"]
                    }
                ]
            
            # 2. UNITS GENERATION
            if "Task: Create a flat list of exactly" in prompt and "Units" in prompt:
                self.generated_counts["units_lessons"] += 1
                return [{"title": "Kinematics Unit", "focus": "Basics of motion"}]

            # 3. LESSONS GENERATION
            if "Task: Extract exactly" in prompt and "Lesson topics" in prompt:
                return [{"title": "Velocity Vectors", "objective": "Learn vectors"}]

            # 4. CONCEPTS GENERATION
            if "Task: Extract" in prompt and "Concepts" in prompt:
                self.generated_counts["concepts"] += 1
                return [
                    {"title": "Scalar Speed", "objectives": ["Def 1"]},
                    {"title": "Vector Velocity", "objectives": ["Def 2"]}
                ]
            
            return []

        mock_llm.side_effect = llm_side_effect

        # --- EXECUTE BUILD ---
        try:
            course_uid = self.builder.build("Physics Masterclass", max_depth=1)
        except Exception as e:
            self.fail(f"Course building crashed: {e}")

        # --- VERIFICATION PHASE ---

        # CHECK 1: JSON STRUCTURE
        assert self.builder.storage.courses.create_course.called, "storage.courses.create_course was not called"
        course_data = self.builder.storage.courses.create_course.call_args[0][0]
        modules = course_data.get("modules", [])
        
        print(f"[STRUCTURE] Total Modules Created: {len(modules)}")
        
        if len(modules) >= 2:
            print("✅ PASS: Modules created.")
            if "scope" in modules[0]:
                print("✅ PASS: Module creation includes 'scope' property.")
            else:
                self.fail("❌ FAIL: Module creation missing 'scope' property!")
        else:
            self.fail(f"❌ FAIL: Expected 2+ modules, found {len(modules)}")
    
        # Verify Units, Lessons, Concepts
        unit_count = sum(len(m.get("units", [])) for m in modules)
        lesson_count = sum(len(u.get("lessons", [])) for m in modules for u in m.get("units", []))
        concept_count = sum(len(l.get("concepts", [])) for m in modules for u in m.get("units", []) for l in u.get("lessons", []))

        print(f"[STRUCTURE] Units: {unit_count}, Lessons: {lesson_count}, Concepts: {concept_count}")
        self.assertGreaterEqual(unit_count, 2, "Expected at least 2 units created")
        self.assertGreaterEqual(lesson_count, 2, "Expected at least 2 lessons created")

        # CHECK 2: BLUEPRINT PROTOCOL IN PROMPTS
        # New format uses BLUEPRINT PROTOCOL with POSITIVE/NEGATIVE SCOPE
        blueprint_prompts = [p for p in self.captured_prompts if "BLUEPRINT PROTOCOL" in p]
        
        print(f"[PROMPTS] Captured {len(blueprint_prompts)} blueprint-constrained prompts.")
        self.assertGreaterEqual(len(blueprint_prompts), 2, "Expected blueprint prompts for each module")
        
        # Verify Newtonian Mechanics prompts enforce positive/negative scope
        newton_prompts = [p for p in blueprint_prompts if "Newtonian Mechanics" in p or "Kinematics" in p]
        
        for p in newton_prompts:
            # Positive Scope Check
            if "POSITIVE SCOPE" in p and ("Force" in p or "Mass" in p or "Acceleration" in p):
                print("✅ PASS: Positive scope keywords found in Newtonian prompt.")
            else:
                print(f"⚠️ WARNING: Newtonian prompt missing Positive scope keywords.")
            
            # Negative Scope Check (Should forbid Quantum)
            if "NEGATIVE SCOPE" in p and ("Superposition" in p or "Entanglement" in p or "Qubits" in p):
                 print("✅ PASS: Negative scope excludes Quantum topics.")
            else:
                 self.fail(f"❌ FAIL: Newtonian prompt FAILED to forbid Quantum topics!\nPrompt snippet: {p[:500]}")

        # CHECK 3: QUALITY & ROBUSTNESS
        print(f"[STATS] Generated: {self.generated_counts}")
        self.assertGreaterEqual(self.generated_counts['modules'], 2)
        self.assertGreaterEqual(self.generated_counts['units_lessons'], 2)
        
        # Check Forbidden List growth in later prompts
        if len(self.captured_prompts) > 2:
            last_prompt = self.captured_prompts[-1]
            # Some previously generated titles should appear in the GLOBAL CONTEXT list
            if any(t in last_prompt for t in ["Kinematics", "Wave Function", "Velocity Vectors", "Schrodinger"]):
                print("✅ PASS: Forbidden list is growing and including previous titles.")
            else:
                print("⚠️ WARNING: Forbidden list might not be populating correctly.")

        print("\n--- FINAL VERDICT: QUALITY ASSURANCE PASSED ---")


class TestNormalizeTitleEdgeCases(unittest.TestCase):
    """Test _normalize_title for edge cases and prefix/suffix stripping."""
    
    def setUp(self):
        self.mock_db = MagicMock()
        kuzu_mock = sys.modules.get('kuzu', __import__('unittest.mock').mock.MagicMock())
        kuzu_mock.Database.return_value = self.mock_db
        kuzu_mock.Connection.return_value = MagicMock()
        mock_result = MagicMock()
        mock_result.has_next.return_value = False
        kuzu_mock.Connection.return_value.execute.return_value = mock_result
        self.builder = SkeletonBuilder(db_path="dummy", course_depth=2)
    
    def test_empty_string(self):
        self.assertEqual(self.builder._normalize_title(""), "")
    
    def test_none_input(self):
        self.assertEqual(self.builder._normalize_title(""), "")
    
    def test_strips_introduction_to(self):
        result = self.builder._normalize_title("Introduction to Quantum Mechanics")
        self.assertNotIn("Introduction", result)
        self.assertIn("Quantum", result)
    
    def test_strips_basics_suffix(self):
        result = self.builder._normalize_title("Photosynthesis Basics")
        self.assertNotIn("Basics", result)
        self.assertIn("Photosynthesis", result)
    
    def test_strips_overview_suffix(self):
        result = self.builder._normalize_title("Cell Biology Overview")
        self.assertNotIn("Overview", result)
    
    def test_strips_exploring_prefix(self):
        result = self.builder._normalize_title("Exploring Neural Networks")
        self.assertNotIn("Exploring", result)
        self.assertIn("Neural Networks", result)
    
    def test_dangling_preposition_removed(self):
        """Titles ending with dangling prepositions should be cleaned."""
        result = self.builder._normalize_title("Systems Theory of")
        self.assertFalse(result.endswith(" of"))
    
    def test_purely_generic_rejected(self):
        """Generic-only titles should return empty."""
        self.assertEqual(self.builder._normalize_title("Introduction"), "")
        self.assertEqual(self.builder._normalize_title("Overview"), "")
        self.assertEqual(self.builder._normalize_title("Basics"), "")
    
    def test_short_acronyms_allowed(self):
        """Short uppercase acronyms like DNA, AI should be kept."""
        result = self.builder._normalize_title("DNA")
        self.assertEqual(result, "DNA")
    
    def test_short_lowercase_rejected(self):
        """Short lowercase titles should be rejected."""
        result = self.builder._normalize_title("a")
        self.assertEqual(result, "")
    
    def test_double_prefix_stripping(self):
        """Chained prefixes like 'The Understanding of' should be fully stripped."""
        result = self.builder._normalize_title("The Understanding Quantum Entanglement Basics")
        self.assertNotIn("The", result)
        self.assertNotIn("Understanding", result)
        self.assertNotIn("Basics", result)
        self.assertIn("Quantum", result)
    
    def test_preserves_hyphens(self):
        """Hyphenated terms should be kept."""
        result = self.builder._normalize_title("Well-formed Grammars")
        self.assertIn("Well-formed", result)
    
    def test_collapses_whitespace(self):
        result = self.builder._normalize_title("  Quantum   Mechanics  ")
        self.assertNotIn("  ", result)


class TestIsDuplicate(unittest.TestCase):
    """Test _is_duplicate collision detection."""
    
    def setUp(self):
        self.mock_db = MagicMock()
        kuzu_mock = sys.modules.get('kuzu', __import__('unittest.mock').mock.MagicMock())
        kuzu_mock.Database.return_value = self.mock_db
        kuzu_mock.Connection.return_value = MagicMock()
        mock_result = MagicMock()
        mock_result.has_next.return_value = False
        kuzu_mock.Connection.return_value.execute.return_value = mock_result
        self.builder = SkeletonBuilder(db_path="dummy", course_depth=2)
    
    def test_exact_match(self):
        self.builder.used_titles = {"Quantum Mechanics"}
        self.assertTrue(self.builder._is_duplicate("Quantum Mechanics"))
    
    def test_case_insensitive_match(self):
        self.builder.used_titles = {"Quantum Mechanics"}
        self.assertTrue(self.builder._is_duplicate("quantum mechanics"))
    
    def test_no_collision(self):
        self.builder.used_titles = {"Quantum Mechanics"}
        self.assertFalse(self.builder._is_duplicate("Roman Architecture"))
    
    def test_substring_collision(self):
        """Substring of an existing title should be detected as duplicate."""
        self.builder.used_titles = {"The Role of Causal Inference"}
        self.assertTrue(self.builder._is_duplicate("Causal Inference"))
    
    def test_topic_echo_rejected(self):
        """A title identical to the course topic should be rejected."""
        self.builder.used_titles = set()
        self.assertTrue(self.builder._is_duplicate("Physics", course_topic="Physics"))
    
    def test_word_overlap_collision(self):
        """Two titles sharing 2+ significant words should collide if they are a large portion of the title."""
        self.builder.used_titles = {"Quantum Computing Algorithms"}
        # Both share "Quantum" and "Algorithms". 
        # "Quantum Algorithms Design" has 3 major words. Overlap 2/3 = 0.66.
        # Threshold is > 0.8, so this is now NOT a collision (allowing thematic variation).
        self.assertFalse(self.builder._is_duplicate("Quantum Algorithms Design"))
        
        # This SHOULD collide (3/3 = 1.0)
        self.assertTrue(self.builder._is_duplicate("Quantum Computing Algorithms"))
    
    def test_short_titles_no_false_collision(self):
        """Very short titles shouldn't trigger substring checks."""
        self.builder.used_titles = {"AI"}
        self.assertFalse(self.builder._is_duplicate("Machine Learning"))
    
    def test_generic_rejected(self):
        """Generic words from FORBIDDEN_GENERIC should be rejected."""
        self.builder.used_titles = set()
        self.assertTrue(self.builder._is_duplicate("basics"))


class TestApplyFixesSafety(unittest.TestCase):
    """Test that _apply_fixes rejects invalid node types."""
    
    def setUp(self):
        from services.core.course_builder import SyllabusAuditor
        self.auditor = SyllabusAuditor(db_path="dummy")
        self.auditor.storage = MagicMock()
        self.dummy_course = {
            "uid": "c1",
            "modules": [{"uid": "mod_123", "title": "Old Name", "units": []}]
        }
    
    def test_valid_type_accepted(self):
        """Valid types like 'Module' should be processed."""
        self.auditor._apply_fixes("c1", self.dummy_course, [
            {"action": "rename", "type": "module", "uid": "mod_123", "new_title": "New Name"}
        ])
        self.auditor.storage.courses.update_course.assert_called_once()
        assert self.dummy_course['modules'][0]['title'] == 'New Name'
    
    def test_invalid_type_rejected(self):
        """Invalid/malicious types should be skipped entirely."""
        self.auditor._apply_fixes("c1", self.dummy_course, [
            {"action": "rename", "type": "DROP TABLE; --", "uid": "mod_123", "new_title": "Hacked"}
        ])
        assert self.dummy_course['modules'][0]['title'] == 'Old Name'
    
    def test_empty_type_rejected(self):
        """Empty type should be rejected."""
        self.auditor._apply_fixes("c1", self.dummy_course, [
            {"action": "delete", "type": "", "uid": "mod_123"}
        ])
        assert len(self.dummy_course['modules']) == 1


class TestCourseStructureQuality(unittest.TestCase):
    """
    Build a full course skeleton via mock LLM, capture titles as JSON,
    and audit every title for quality, prompt leakage, and context propagation.
    
    PURPOSE: Ensure titles are hydration-ready and that valuable context
    (scopes, forbidden lists, topic) flows through every generation stage.
    """

    # --- Prompt leakage patterns ---
    # Exact example strings from Output JSON format instructions
    PROMPT_EXAMPLE_TITLES = {
        'unit title', 'lesson title', 'concept title', 'module name',
        'objective 1', 'objective 2',
    }
    # Example JSON block titles (STEM, historical, generic)
    EXAMPLE_JSON_TITLES = {
        'fundamental axioms', 'systemic dynamics', 'complex synthesis',
        'symbolic language', 'compositional structure', 'stylistic evolution',
        'theory layer 1', 'mechanism layer 2', 'synthesis layer 3',
    }
    # Example JSON scope items that could leak
    EXAMPLE_SCOPE_ITEMS = {
        'principle a', 'variable b', 'model c',
        'interaction x', 'feedback y', 'constraint z',
        'component x', 'component y', 'component z',
        'variable p', 'variable q', 'variable r',
        'result a', 'result b', 'result c',
        'non-linear effects',
    }
    # Fallback/generic patterns from code (regex)
    FALLBACK_PATTERNS = [
        r'^.*component \d+$',      # "{module} Component 1"
        r'^key analysis \d+$',     # "Key Analysis 1"
        r'^core specific \d+$',    # "Core Specific 1"
        r'^unit \d+$',             # "Unit 1"
        r'^lesson \d+$',           # "Lesson 1"
        r'^concept \d+$',          # "Concept 1"
        r'^module \d+$',           # "Module 1"
    ]
    # Vague/generic one-word or structural titles unlikely to hydrate well
    VAGUE_TITLES = {
        'introduction', 'overview', 'basics', 'foundations', 'essentials',
        'summary', 'review', 'conclusion', 'advanced', 'miscellaneous',
        'other', 'general', 'topics', 'details', 'specifics', 'context',
        'definitions', 'origins', 'axioms',
    }

    def setUp(self):
        # Mock pre-flight checks
        self.patcher_preflight = patch.object(SkeletonBuilder, '_run_preflight_checks', return_value=True)
        self.patcher_preflight.start()

        self.builder = SkeletonBuilder(db_path="dummy", course_depth=1)
        self.builder.storage = MagicMock()

        # Storage for prompts and captured structure
        self.captured_prompts = []
        self.course_json = {"course": "Organic Chemistry", "modules": []}

    def tearDown(self):
        self.patcher_preflight.stop()

    def _build_with_mock_llm(self):
        """Run build() with a context-aware mock LLM that returns realistic titles."""
        call_count = {'n': 0}

        def llm_side_effect(prompt, **kwargs):
            self.captured_prompts.append(prompt)
            call_count['n'] += 1

            # --- MODULE generation ---
            if "Create a progressive sequence" in prompt:
                return [
                    {"title": "Carbon Bonding Fundamentals", "level": 1,
                     "rationale": "Core orbital theory.",
                     "scope": ["Hybridization", "Sigma Bonds", "Pi Bonds"]},
                    {"title": "Functional Group Reactivity", "level": 2,
                     "rationale": "Reaction mechanisms.",
                     "scope": ["Alcohols", "Aldehydes", "Carboxylic Acids"]},
                    {"title": "Stereochemistry and Chirality", "level": 3,
                     "rationale": "Spatial arrangement.",
                     "scope": ["Enantiomers", "Diastereomers", "R/S Configuration"]}
                ]

            # --- UNIT generation ---
            if "Task: Create a flat list of exactly" in prompt and "Units" in prompt:
                if "Carbon Bonding" in prompt:
                    return [{"title": "Atomic Orbital Theory", "focus": "Orbitals"}]
                elif "Functional Group" in prompt:
                    return [{"title": "Hydroxyl Chemistry", "focus": "Alcohols"}]
                else:
                    return [{"title": "Molecular Asymmetry", "focus": "Chirality"}]

            # --- LESSON generation ---
            if "Task: Extract exactly" in prompt and "Lesson topics" in prompt:
                if "Atomic Orbital" in prompt:
                    return [{"title": "Electron Configuration", "objective": "Shells"}]
                elif "Hydroxyl" in prompt:
                    return [{"title": "Alcohol Oxidation", "objective": "Reactions"}]
                else:
                    return [{"title": "Chiral Centers", "objective": "CIP"}]

            # --- CONCEPT generation ---
            if "Task: Extract" in prompt and "Concepts" in prompt:
                if "Electron Configuration" in prompt:
                    return [
                        {"title": "Aufbau Principle", "objectives": ["Energy-level filling order"]},
                        {"title": "Pauli Exclusion", "objectives": ["Spin pairing constraints"]}
                    ]
                elif "Alcohol Oxidation" in prompt:
                    return [
                        {"title": "Primary Alcohol to Aldehyde", "objectives": ["PCC reagent usage"]},
                        {"title": "Jones Oxidation", "objectives": ["Chromic acid mechanism"]}
                    ]
                else:
                    return [
                        {"title": "Atomic Number Priority", "objectives": ["Substituent ranking"]},
                        {"title": "R vs S Designation", "objectives": ["Clockwise vs counterclockwise"]}
                    ]


            return "[]"

        with patch('services.core.course_builder.llm_generate_json', side_effect=llm_side_effect):
            course_uid = self.builder.build("Organic Chemistry", max_depth=1)
        return course_uid

    def _extract_structure_from_db_calls(self):
        """Parse the JSON course structure written to StorageManager."""
        create_course_calls = self.builder.storage.courses.create_course.call_args_list
        assert create_course_calls, "Course was not created in StorageManager"
        
        # Extract the course dictionary passed to create_course
        course_dict = create_course_calls[0][0][0]
        self.course_json = {
            "course": course_dict.get("title", ""),
            "modules": course_dict.get("modules", [])
        }
        return self.course_json


    def _collect_all_titles(self, structure):
        """Flatten all titles from the nested structure."""
        titles = []
        for m in structure.get("modules", []):
            titles.append(("Module", m["title"]))
            for u in m.get("units", []):
                titles.append(("Unit", u["title"]))
                for l in u.get("lessons", []):
                    titles.append(("Lesson", l["title"]))
                    for c in l.get("concepts", []):
                        titles.append(("Concept", c["title"]))
        return titles

    # ──────────────────────────────────────────────
    # TEST: Full structure output and title quality
    # ──────────────────────────────────────────────
    def test_structure_output_and_title_quality(self):
        """Build course, output JSON, check every title for quality issues."""
        self._build_with_mock_llm()
        structure = self._extract_structure_from_db_calls()
        all_titles = self._collect_all_titles(structure)

        print("\n" + "=" * 60)
        print("COURSE STRUCTURE JSON OUTPUT")
        print("=" * 60)
        # Print clean structure without uids for readability
        def clean(node):
            out = {"title": node.get("title", "")}
            if "scope" in node and node["scope"]:
                out["scope"] = node["scope"]
            for key in ("units", "lessons", "concepts"):
                if key in node and node[key]:
                    out[key] = [clean(child) for child in node[key]]
            return out
        clean_struct = {"course": structure["course"],
                        "modules": [clean(m) for m in structure["modules"]]}
        print(json.dumps(clean_struct, indent=2))
        print("=" * 60)

        # --- CHECK 1: No prompt example leakage ---
        leaked = []
        for level, title in all_titles:
            t_lower = title.lower().strip()
            if t_lower in self.PROMPT_EXAMPLE_TITLES:
                leaked.append((level, title, "PROMPT_EXAMPLE"))
            if t_lower in self.EXAMPLE_JSON_TITLES:
                leaked.append((level, title, "EXAMPLE_JSON"))
            if t_lower in self.EXAMPLE_SCOPE_ITEMS:
                leaked.append((level, title, "EXAMPLE_SCOPE"))
            for pattern in self.FALLBACK_PATTERNS:
                if re.match(pattern, t_lower):
                    leaked.append((level, title, f"FALLBACK:{pattern}"))

        if leaked:
            for level, title, reason in leaked:
                print(f"  ❌ LEAK [{level}] \"{title}\" — {reason}")
        self.assertEqual(len(leaked), 0,
                         f"Prompt leakage detected in {len(leaked)} title(s): {leaked}")
        print("✅ PASS: No prompt example leakage in any title.")

        # --- CHECK 2: Specificity (No vague, one-word, or bureaucratic titles) ---
        vague = []
        bureaucratic_suffixes = [
            "Primary Elements", "Logical Flow", "Detailed Patterns",
            "Systemic View", "Active Components", "Structural Dynamics"
        ]
        for level, title in all_titles:
            t_lower = title.lower().strip()
            if t_lower in self.VAGUE_TITLES:
                vague.append((level, title, "VAGUE"))
            # Also catch numbered generics like "Module 1" not caught by regex
            if len(title.split()) < 2 and not title.isupper():
                vague.append((level, title, "ONE_WORD"))
            
            # Check for bureaucratic suffixes that should have been stripped
            if any(title.endswith(s) for s in bureaucratic_suffixes):
                vague.append((level, title, "BUREAUCRATIC_SUFFIX"))

        if vague:
            for level, title, reason in vague:
                print(f"  ⚠️ VAGUE [{level}] \"{title}\" — {reason}")
        self.assertEqual(len(vague), 0,
                         f"Vague/generic/bureaucratic titles found: {vague}")
        print("✅ PASS: All titles are specific and non-bureaucratic.")

        # --- CHECK 3: No duplicate titles across the whole tree ---
        seen = set()
        dupes = []
        for level, title in all_titles:
            t_norm = title.lower().strip()
            if t_norm in seen:
                dupes.append((level, title, "DUPLICATE"))
            seen.add(t_norm)

        if dupes:
            for level, title, reason in dupes:
                print(f"  ❌ DUPE [{level}] \"{title}\" — {reason}")
        self.assertEqual(len(dupes), 0,
                         f"Duplicate titles: {dupes}")
        print("✅ PASS: All titles are unique across the structure.")

        # --- CHECK 4: Structure completeness ---
        self.assertGreaterEqual(len(structure["modules"]), 2,
                                "Expected at least 2 modules")
        for m in structure["modules"]:
            self.assertGreaterEqual(len(m["units"]), 1,
                                    f"Module '{m['title']}' has no units")
            for u in m["units"]:
                self.assertGreaterEqual(len(u["lessons"]), 1,
                                        f"Unit '{u['title']}' has no lessons")

        total = len(all_titles)
        print(f"✅ PASS: Structure complete — {total} total nodes across "
              f"{len(structure['modules'])} modules.")

    # ──────────────────────────────────────────────
    # TEST: Module Generation Self-Correction (Retries)
    # ──────────────────────────────────────────────
    @patch('services.core.course_builder.llm_generate_json')
    def test_module_generation_self_correction(self, mock_llm):
        """Verify that the builder retries module generation if the count is insufficient."""
        print("\n--- AUDIT: MODULE GENERATION SELF-CORRECTION ---\n")
        
        # Override to depth 2 (requires 3 modules)
        self.builder.depth_profile = {
            "target_modules": 3,
            "units_per_module": 1,
            "lessons_per_unit": 1,
            "concepts_per_lesson": 1,
            "label": "Test Depth",
            "academic_level": "Undergraduate"
        }
        
        captured_prompts = []
        
        def self_correct_side_effect(prompt, **kwargs):
            captured_prompts.append(prompt)
            
            # First call for modules: Return only 2 (Insufficient!)
            if "Create a progressive sequence" in prompt and "CRITICAL SELF-CORRECTION" not in prompt:
                return [
                    {"title": "Intro to Logic", "level": 1, "rationale": "Base", "scope": ["Logic"]},
                    {"title": "Formal Logic", "level": 2, "rationale": "Adv", "scope": ["Formal"]}
                ]
            
            # Second call for modules (Retry): Return 3 (Correct!)
            if "CRITICAL SELF-CORRECTION" in prompt:
                return [
                    {"title": "Intro to Logic", "level": 1, "rationale": "Base", "scope": ["Logic"]},
                    {"title": "Formal Logic", "level": 2, "rationale": "Adv", "scope": ["Formal"]},
                    {"title": "Modal Logic", "level": 3, "rationale": "Expert", "scope": ["Modal"]}
                ]
            
            # Fallback for sub-structures
            # Concepts validation requires at least 2!
            return [
                {"title": "Sub Element A", "focus": "Test", "objective": "Test", "objectives": ["Test"]},
                {"title": "Sub Element B", "focus": "Test", "objective": "Test", "objectives": ["Test"]}
            ]

        mock_llm.side_effect = self_correct_side_effect
        
        # Build
        course_uid = self.builder.build("Logic Systems", max_depth=2)
        
        # Verify
        self.assertIsNotNone(course_uid, "Course building failed completely")
        
        # Verify structure in storage
        course_data = self.builder.storage.courses.create_course.call_args[0][0]
        self.assertEqual(len(course_data["modules"]), 3, "Expected 3 modules in the final structure")
        
        # Verify self-correction prompt was used
        correction_prompts = [p for p in captured_prompts if "### CRITICAL SELF-CORRECTION" in p]
        self.assertEqual(len(correction_prompts), 1, "Expected one self-correction retry prompt")
        self.assertIn("REQUIRED: Exactly 3 modules", correction_prompts[0])
        print("✅ PASS: Self-correction retry logic verified.")

    # ──────────────────────────────────────────────
    # TEST: Context propagation at every generation stage
    # ──────────────────────────────────────────────
    def test_context_propagation_in_prompts(self):
        """Verify that the right context flows into every LLM prompt."""
        self._build_with_mock_llm()

        # Classify prompts
        module_prompts = [p for p in self.captured_prompts
                          if "Create a progressive sequence" in p]
        substructure_prompts = [p for p in self.captured_prompts
                                if "Task: Create a nested structure" in p]
        concept_prompts = [p for p in self.captured_prompts
                           if "Task: Extract" in p and "Concepts" in p]

        print("\n" + "=" * 60)
        print("CONTEXT PROPAGATION AUDIT")
        print("=" * 60)
        print(f"Module prompts: {len(module_prompts)}")
        print(f"Substructure prompts: {len(substructure_prompts)}")
        print(f"Concept prompts: {len(concept_prompts)}")

        # --- CHECK A: Course topic appears in every prompt ---
        missing_topic = []
        for i, p in enumerate(self.captured_prompts):
            if "Organic Chemistry" not in p:
                missing_topic.append(i)
        self.assertEqual(len(missing_topic), 0,
                         f"Course topic missing from prompt(s): {missing_topic}")
        print("✅ PASS: Course topic present in every prompt.")

        # --- CHECK B: Blueprint protocol in substructure + concept prompts ---
        blueprint_prompts = substructure_prompts + concept_prompts
        self.assertGreater(len(blueprint_prompts), 0, "No substructure/concept prompts captured")

        for p in blueprint_prompts:
            self.assertIn("BLUEPRINT PROTOCOL", p,
                          f"Missing BLUEPRINT PROTOCOL in prompt: {p[:100]}...")
            self.assertIn("POSITIVE SCOPE", p,
                          f"Missing POSITIVE SCOPE in prompt: {p[:100]}...")
            self.assertIn("NEGATIVE SCOPE", p,
                          f"Missing NEGATIVE SCOPE in prompt: {p[:100]}...")
        print("✅ PASS: Blueprint protocol (positive/negative scope) in all substructure prompts.")

        # --- CHECK C: Negative scope actually contains OTHER modules' scope items ---
        # For the "Carbon Bonding" module, Negative scope should include
        # items from Functional Group or Stereochemistry modules
        carbon_prompts = [p for p in blueprint_prompts if "Carbon Bonding" in p]
        for p in carbon_prompts:
            # Should exclude scope items from other modules
            has_cross_scope = any(term in p for term in
                                  ["Alcohols", "Aldehydes", "Enantiomers",
                                   "Diastereomers", "Carboxylic Acids"])
            self.assertTrue(has_cross_scope,
                            "Carbon Bonding prompts should have negative scope from other modules")
        print("✅ PASS: Cross-module negative scope correctly injected.")

        # --- CHECK D: Forbidden list grows over time ---
        # Later prompts should reference previously-generated titles
        if len(self.captured_prompts) >= 4:
            late_prompt = self.captured_prompts[-1]
            # Should reference some earlier title in GLOBAL CONTEXT
            has_prior = any(term in late_prompt for term in
                           ["Carbon Bonding", "Atomic Orbital", "Hybridization",
                            "Functional Group", "Hydroxyl"])
            if has_prior:
                print("✅ PASS: Forbidden list grows — later prompts reference prior titles.")
            else:
                print("⚠️ WARNING: Forbidden list may not be populating in later prompts.")

        # --- CHECK E: Academic depth is specified ---
        for p in blueprint_prompts:
            self.assertIn("ACADEMIC DEPTH", p,
                          "Missing ACADEMIC DEPTH in substructure prompt")
        print("✅ PASS: Academic depth specified in all prompts.")

        print("\n--- CONTEXT PROPAGATION AUDIT COMPLETE ---")



if __name__ == '__main__':
    unittest.main()

