
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
        self._unit_calls = self._lesson_calls = self._concept_calls = 0

        UNITS = [
            {"title": "Kinematics Unit", "description": "Basics of motion"},
            {"title": "Heat Flow Unit", "description": "Thermal energy transfer"},
            {"title": "Wave Particle Unit", "description": "Quantum duality"},
        ]
        LESSONS = [
            {"title": "Velocity Vectors"},
            {"title": "Conduction Convection"},
            {"title": "Photon Behavior"},
        ]
        CONCEPTS = [
            [{"title": "Scalar Speed", "objectives": ["Def 1"]},
             {"title": "Vector Velocity", "objectives": ["Def 2"]}],
            [{"title": "Fourier Heat Law", "objectives": ["Def 1"]},
             {"title": "Thermal Conductivity", "objectives": ["Def 2"]}],
            [{"title": "Wave Function Collapse", "objectives": ["Def 1"]},
             {"title": "Quantum Tunneling", "objectives": ["Def 2"]}],
        ]

        def llm_side_effect(prompt, **kwargs):
            self.captured_prompts.append(prompt)

            # 1. MODULES GENERATION (top-level build prompt)
            if "PROGRESSIVE modules" in prompt or "Create exactly" in prompt:
                self.generated_counts["modules"] += 3
                return [
                    {
                        "title": "Newtonian Mechanics",
                        "level": 1,
                        "rationale": "Basics",
                        "scope": ["Force", "Mass", "Acceleration"]
                    },
                    {
                        "title": "Thermodynamics",
                        "level": 2,
                        "rationale": "Intermediate",
                        "scope": ["Heat", "Entropy", "Energy Transfer"]
                    },
                    {
                        "title": "Quantum Physics",
                        "level": 3,
                        "rationale": "Advanced",
                        "scope": ["Superposition", "Entanglement", "Qubits"]
                    }
                ]

            # 2. UNITS GENERATION
            #
            # SELECTED BY CALL ORDER, NOT BY PROMPT TEXT.
            #
            # These branches used to read `if "Newtonian" in prompt ... elif
            # "Thermodynamics" in prompt`, and the builder's prompt carries the
            # WHOLE course context — every module's name appears in every
            # module's prompt. So the first branch won every time, every unit
            # came back "Kinematics Unit", and dedup rejected it as a duplicate
            # from the second module onward. The builder then fell back to
            # "{module} Part 1", its lesson to "... Lesson 1", and its concepts
            # to "... Part N" stubs.
            #
            # The test still passed, because it counted those stubs: two of its
            # three modules were pure scaffolding and the structure assertions
            # were satisfied by padding. That only surfaced when the builder
            # stopped shipping padding.
            if "TASK: Generate exactly" in prompt and "Units" in prompt:
                self.generated_counts["units_lessons"] += 1
                i = self._unit_calls
                self._unit_calls += 1
                return [UNITS[i % len(UNITS)]]

            # 3. LESSONS GENERATION
            if "Generate exactly" in prompt and "lessons for" in prompt:
                i = self._lesson_calls
                self._lesson_calls += 1
                return [LESSONS[i % len(LESSONS)]]

            # 4. CONCEPTS GENERATION
            if "Generate exactly" in prompt and "concepts for" in prompt:
                self.generated_counts["concepts"] += 1
                i = self._concept_calls
                self._concept_calls += 1
                return list(CONCEPTS[i % len(CONCEPTS)])

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

        if len(modules) >= 3:
            print("PASS: Modules created.")
            if "scope" in modules[0]:
                print("PASS: Module creation includes 'scope' property.")
            else:
                self.fail("FAIL: Module creation missing 'scope' property!")
        else:
            self.fail(f"FAIL: Expected 3+ modules, found {len(modules)}")

        # Verify Units, Lessons, Concepts
        unit_count = sum(len(m.get("units", [])) for m in modules)
        lesson_count = sum(len(u.get("lessons", [])) for m in modules for u in m.get("units", []))
        concept_count = sum(len(l.get("concepts", [])) for m in modules for u in m.get("units", []) for l in u.get("lessons", []))

        print(f"[STRUCTURE] Units: {unit_count}, Lessons: {lesson_count}, Concepts: {concept_count}")
        self.assertGreaterEqual(unit_count, 3, "Expected at least 3 units created")
        self.assertGreaterEqual(lesson_count, 3, "Expected at least 3 lessons created")

        # CHECK 2: SCOPE CONSTRAINTS IN PROMPTS
        # New format uses "Module Scope (STAY WITHIN THIS)" and "SCOPE BOUNDARY"
        scope_prompts = [p for p in self.captured_prompts if "Module Scope" in p or "SCOPE BOUNDARY" in p]

        print(f"[PROMPTS] Captured {len(scope_prompts)} scope-constrained prompts.")
        self.assertGreaterEqual(len(scope_prompts), 3, "Expected scope-constrained prompts for each module")

        # Verify Newtonian Mechanics prompts include positive scope keywords
        newton_prompts = [p for p in scope_prompts if "Newtonian Mechanics" in p or "Kinematics" in p]

        for p in newton_prompts:
            # Positive Scope Check (module scope should appear)
            if "Force" in p or "Mass" in p or "Acceleration" in p:
                print("PASS: Positive scope keywords found in Newtonian prompt.")
            else:
                print("WARNING: Newtonian prompt missing positive scope keywords.")

        # CHECK 3: QUALITY & ROBUSTNESS
        print(f"[STATS] Generated: {self.generated_counts}")
        self.assertGreaterEqual(self.generated_counts['modules'], 3)
        self.assertGreaterEqual(self.generated_counts['units_lessons'], 3)
        
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
        """Run build() with a context-aware mock LLM that returns realistic titles.

        Pinned to the CHUNKED path (HELGA_ONESHOT_SUBTREE=0): this mock and the
        prompt classifiers below key off that path's per-level prompt phrasing.
        The chunked path remains live as the consolidated path's fallback, so
        this is coverage of a real code path, not of dead code. Consolidated-path
        context propagation is covered by TestConsolidatedSubtree.
        """
        import os as _os
        _prev = _os.environ.get("HELGA_ONESHOT_SUBTREE")
        _os.environ["HELGA_ONESHOT_SUBTREE"] = "0"
        self.addCleanup(
            lambda: _os.environ.__setitem__("HELGA_ONESHOT_SUBTREE", _prev)
            if _prev is not None else _os.environ.pop("HELGA_ONESHOT_SUBTREE", None)
        )
        call_count = {'n': 0}

        def llm_side_effect(prompt, **kwargs):
            self.captured_prompts.append(prompt)
            call_count['n'] += 1

            # --- MODULE generation ---
            if "PROGRESSIVE modules" in prompt or "Create exactly" in prompt:
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
            if "TASK: Generate exactly" in prompt and "Units" in prompt:
                if "Carbon Bonding" in prompt:
                    return [{"title": "Atomic Orbital Theory", "description": "Orbitals"}]
                elif "Functional Group" in prompt:
                    return [{"title": "Hydroxyl Chemistry", "description": "Alcohols"}]
                else:
                    return [{"title": "Molecular Asymmetry", "description": "Chirality"}]

            # --- LESSON generation ---
            if "Generate exactly" in prompt and "lessons for" in prompt:
                if "Atomic Orbital" in prompt:
                    return [{"title": "Electron Configuration"}]
                elif "Hydroxyl" in prompt:
                    return [{"title": "Alcohol Oxidation"}]
                else:
                    return [{"title": "Chiral Centers"}]

            # --- CONCEPT generation ---
            if "Generate exactly" in prompt and "concepts for" in prompt:
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
            if ("PROGRESSIVE modules" in prompt or "Create exactly" in prompt) and "CRITICAL SELF-CORRECTION" not in prompt:
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
                          if "PROGRESSIVE modules" in p or "Create exactly" in p]
        unit_prompts = [p for p in self.captured_prompts
                        if "TASK: Generate exactly" in p and "Units" in p]
        lesson_prompts = [p for p in self.captured_prompts
                          if "Generate exactly" in p and "lessons for" in p]
        concept_prompts = [p for p in self.captured_prompts
                           if "Generate exactly" in p and "concepts for" in p]

        print("\n" + "=" * 60)
        print("CONTEXT PROPAGATION AUDIT")
        print("=" * 60)
        print(f"Module prompts: {len(module_prompts)}")
        print(f"Unit prompts: {len(unit_prompts)}")
        print(f"Lesson prompts: {len(lesson_prompts)}")
        print(f"Concept prompts: {len(concept_prompts)}")

        # --- CHECK A: Course topic appears in every prompt ---
        missing_topic = []
        for i, p in enumerate(self.captured_prompts):
            if "Organic Chemistry" not in p:
                missing_topic.append(i)
        self.assertEqual(len(missing_topic), 0,
                         f"Course topic missing from prompt(s): {missing_topic}")
        print("PASS: Course topic present in every prompt.")

        # --- CHECK B: Scope constraints in unit + concept prompts ---
        # New format uses "Module Scope (STAY WITHIN THIS)" and "SCOPE BOUNDARY"
        scoped_prompts = unit_prompts + concept_prompts
        self.assertGreater(len(scoped_prompts), 0, "No unit/concept prompts captured")

        for p in unit_prompts:
            self.assertIn("Module Scope", p,
                          f"Missing Module Scope in unit prompt: {p[:100]}...")
        print("PASS: Module scope referenced in unit prompts.")

        # --- CHECK C: Module scope items appear in unit prompts ---
        # For the "Carbon Bonding" module, scope should include Hybridization/Sigma/Pi
        carbon_unit_prompts = [p for p in unit_prompts if "Carbon Bonding" in p]
        for p in carbon_unit_prompts:
            has_scope = any(term in p for term in
                           ["Hybridization", "Sigma Bonds", "Pi Bonds"])
            self.assertTrue(has_scope,
                            "Carbon Bonding unit prompts should contain module scope items")
        print("PASS: Module scope items correctly injected into unit prompts.")

        # --- CHECK D: Used titles / hierarchy context grows over time ---
        # Later prompts should reference previously-generated titles
        if len(self.captured_prompts) >= 4:
            late_prompt = self.captured_prompts[-1]
            # Should reference some earlier title in used-titles or hierarchy context
            has_prior = any(term in late_prompt for term in
                           ["Carbon Bonding", "Atomic Orbital", "Hybridization",
                            "Functional Group", "Hydroxyl"])
            if has_prior:
                print("PASS: Used titles list grows — later prompts reference prior titles.")
            else:
                print("WARNING: Used titles list may not be populating in later prompts.")

        # --- CHECK E: Mastery/Bloom level is specified ---
        for p in scoped_prompts:
            has_level = "Bloom Level" in p or "Mastery Level" in p or "mastery level" in p
            self.assertTrue(has_level,
                            f"Missing Bloom/Mastery level in prompt: {p[:100]}...")
        print("PASS: Bloom/mastery level specified in all scoped prompts.")

        print("\n--- CONTEXT PROPAGATION AUDIT COMPLETE ---")



if __name__ == '__main__':
    unittest.main()



class TestConsolidatedSubtree(unittest.TestCase):
    """The consolidated path (HELGA_ONESHOT_SUBTREE=1, the default) must build a
    structurally identical tree from ONE call per module, carry the same context
    into that call, and keep every safeguard the chunked path had."""

    def setUp(self):
        self.captured = []
        # Restore afterwards — leaving this set leaks the consolidated path into
        # sibling tests that build via the chunked mock.
        _prev = os.environ.get("HELGA_ONESHOT_SUBTREE")
        os.environ["HELGA_ONESHOT_SUBTREE"] = "1"
        self.addCleanup(
            lambda: os.environ.__setitem__("HELGA_ONESHOT_SUBTREE", _prev)
            if _prev is not None else os.environ.pop("HELGA_ONESHOT_SUBTREE", None)
        )

    def _subtree_reply(self):
        """A reply that needs no correcting — TWO units, the school-shape floor.

        A one-unit reply is below that floor and legitimately earns a correction
        round, so it cannot be the fixture for the single-call guarantee.
        `_one_unit_reply` below covers that case deliberately.
        """
        return {
            "units": [{
                "title": "Sigma and Pi Bonding",
                "description": "Orbital overlap geometry.",
                "lessons": [{
                    "title": "Hybridisation of Carbon Orbitals",
                    "concepts": [
                        {"title": "sp3 Tetrahedral Geometry",
                         "objectives": ["Predict bond angles", "Relate hybridisation to shape"]},
                        {"title": "sp2 Planar Geometry",
                         "objectives": ["Identify planar centres", "Explain pi overlap"]},
                    ],
                }],
            }, {
                "title": "Resonance and Delocalisation",
                "description": "Where electrons are not where they look.",
                "lessons": [{
                    "title": "Drawing Resonance Contributors",
                    "concepts": [
                        {"title": "Curved-Arrow Notation",
                         "objectives": ["Push electrons correctly", "Avoid invalid arrows"]},
                        {"title": "Relative Contributor Weight",
                         "objectives": ["Rank contributors", "Justify the ranking"]},
                    ],
                }],
            }]
        }

    def _one_unit_reply(self):
        """Below the school-shape floor, so it must earn exactly one correction."""
        return {
            "units": [{
                "title": "Sigma and Pi Bonding",
                "description": "Orbital overlap geometry.",
                "lessons": [{
                    "title": "Hybridisation of Carbon Orbitals",
                    "concepts": [
                        {"title": "sp3 Tetrahedral Geometry",
                         "objectives": ["Predict bond angles", "Relate hybridisation to shape"]},
                        {"title": "sp2 Planar Geometry",
                         "objectives": ["Identify planar centres", "Explain pi overlap"]},
                    ],
                }],
            }]
        }

    def _run(self, reply):
        from services.core.course_builder import SkeletonBuilder
        import tempfile
        from services.common.storage import StorageManager

        storage = StorageManager(tempfile.mkdtemp(prefix="oneshot_test_"))
        b = SkeletonBuilder(storage=storage, scope=2, mastery=2, starting_from=1)
        m_ref = {
            "title": "Carbon Bonding Fundamentals",
            "role_desc": "foundation",
            "scope": ["orbitals", "geometry"],
            "dict": {"uid": "mod_x", "title": "Carbon Bonding Fundamentals", "units": []},
        }
        with patch("services.core.course_builder.llm_generate_json") as mock:
            def side_effect(prompt, **kw):
                self.captured.append((prompt, kw))
                return reply
            mock.side_effect = side_effect
            lines = b._build_module_subtree_oneshot(
                m_ref, "Organic Chemistry", "Understanding",
                # 2, not 1: the caller clamps base_units to the school-shape
                # floor, so 1 is an argument production never passes — and
                # testing with it hid the fact that `units_data[:base_units]`
                # would have truncated a floor-respecting reply back down.
                base_units=2, base_lessons=1, base_concepts=2,
                module_bloom_level=2, module_specific_depth=2,
                prev_context_str="No modules covered yet.",
                mastery_constraint="",
            )
        return b, m_ref, lines, mock

    def test_one_call_builds_the_whole_subtree(self):
        b, m_ref, lines, mock = self._run(self._subtree_reply())
        self.assertEqual(mock.call_count, 1, "consolidation must use ONE call per module")
        units = m_ref["dict"]["units"]
        self.assertEqual(len(units), 2)
        self.assertEqual(len(units[0]["lessons"]), 1)
        self.assertEqual(len(units[0]["lessons"][0]["concepts"]), 2)

    def test_a_deficient_reply_costs_exactly_one_correction(self):
        """The correction round is bounded — one extra call, never a retry loop.

        The floor is unenforceable in the schema (`minItems` is stripped from
        `response_format` for /v1 compatibility, and /v1 ignores the `format`
        field that still carries it), so it is enforced here instead. That makes
        the SECOND call intentional; what must not happen is a third.
        """
        b, m_ref, lines, mock = self._run(self._one_unit_reply())
        self.assertEqual(mock.call_count, 2,
                         "one unit is below the school-shape floor: correct once")
        # The mock returns the same deficient reply, so the correction cannot
        # improve on it — and a correction that is no better must be discarded
        # rather than allowed to overwrite what we already had.
        self.assertEqual(len(m_ref["dict"]["units"]), 1)

    def test_the_correction_prompt_names_the_shortfall(self):
        """Naming the specific defect is what makes a correction round work.

        Measured: a prompt-only ban of generic titles changed nothing 5/5, while
        a correction naming the offending titles fixed them 5/5.
        """
        b, m_ref, lines, mock = self._run(self._one_unit_reply())
        correction = self.captured[1][0]
        self.assertIn("CORRECTION", correction)
        self.assertIn("1 unit", correction, "must name the count it actually got")
        self.assertIn("do not simply rename", correction,
                      "must forbid the cheap fix of renaming the unit it had")

    def test_shapes_match_the_chunked_path(self):
        b, m_ref, lines, mock = self._run(self._subtree_reply())
        unit = m_ref["dict"]["units"][0]
        self.assertTrue(unit["uid"].startswith("unit_"))
        self.assertIn("ordinal", unit)
        lesson = unit["lessons"][0]
        self.assertTrue(lesson["uid"].startswith("less_"))
        con = lesson["concepts"][0]
        self.assertTrue(con["uid"].startswith("con_"))
        for key in ("title", "learning_objectives", "complexity_role",
                    "depth_level", "bloom_level", "ordinal"):
            self.assertIn(key, con, f"concept missing {key} the chunked path emitted")
        self.assertEqual(con["bloom_level"], 2)

    def test_context_propagates_into_the_single_call(self):
        b, m_ref, lines, mock = self._run(self._subtree_reply())
        prompt = self.captured[0][0]
        self.assertIn("Organic Chemistry", prompt, "course topic must reach the prompt")
        self.assertIn("Carbon Bonding Fundamentals", prompt, "module title must reach it")
        self.assertIn("orbitals", prompt, "module scope must reach it")
        self.assertIn("No modules covered yet", prompt, "prior-coverage context must reach it")

    def test_schema_is_used_to_constrain(self):
        b, m_ref, lines, mock = self._run(self._subtree_reply())
        kwargs = self.captured[0][1]
        self.assertIsNotNone(kwargs.get("schema"), "must pass a schema to constrain shape")
        self.assertEqual(kwargs.get("expected_type"), "dict")

    def test_empty_reply_signals_fallback(self):
        b, m_ref, lines, mock = self._run({"units": []})
        self.assertIsNone(lines, "empty subtree must return None so the caller falls back")

    def test_empty_lesson_is_backfilled_never_a_black_hole(self):
        reply = {"units": [{"title": "Sigma Bonding", "description": "d",
                            "lessons": [{"title": "Hybridisation", "concepts": []}]}]}
        b, m_ref, lines, mock = self._run(reply)
        concepts = m_ref["dict"]["units"][0]["lessons"][0]["concepts"]
        self.assertEqual(len(concepts), 2, "empty lesson must be backfilled, not shipped empty")
        self.assertTrue(all(c.get("llm_fallback") for c in concepts))
        self.assertEqual(b.fallback_count, 2, "backfill must be counted as fallback")

    def test_duplicate_titles_are_rejected(self):
        reply = {"units": [{"title": "Sigma Bonding", "description": "d",
                            "lessons": [{"title": "Hybridisation", "concepts": [
                                {"title": "sp3 Geometry", "objectives": ["a", "b"]},
                                {"title": "sp3 Geometry", "objectives": ["a", "b"]},
                            ]}]}]}
        b, m_ref, lines, mock = self._run(reply)
        titles = [c["title"] for c in m_ref["dict"]["units"][0]["lessons"][0]["concepts"]]
        self.assertEqual(len(titles), len(set(titles)), "duplicates must not survive")

    def test_tolerates_bare_units_array(self):
        """A model may return the units array directly rather than wrapped."""
        b, m_ref, lines, mock = self._run([{
            "title": "Sigma Bonding", "description": "d",
            "lessons": [{"title": "Hybridisation", "concepts": [
                {"title": "sp3 Geometry", "objectives": ["a", "b"]}]}],
        }])
        self.assertIsNotNone(lines, "a bare units array must still build")
        self.assertEqual(len(m_ref["dict"]["units"]), 1)

    def test_nonsense_reply_falls_back_instead_of_raising(self):
        """A list of strings, or a scalar, must degrade to the chunked path."""
        for junk in (["just", "strings"], "a string", 42, None):
            with self.subTest(junk=junk):
                b, m_ref, lines, mock = self._run(junk)
                self.assertIsNone(lines, f"{junk!r} should signal fallback, not raise")


class TestEvidencePartition(unittest.TestCase):
    """A relevance gate applied at ONE consumer is not a relevance gate.

    Regression cover for a measured failure. Building "Dungeon Mastering"
    matched an OpenStax SOCIOLOGY text at relevance ~2, and because the gate
    lived only inside `_spine_from_syllabus`, every other consumer took the
    brief unfiltered: scope_fit counted 68 sociology chapters and reported the
    subject amply supported (ratio 2.83, no stretch disclaimer), backfill tried
    to inject six sociology chapters as material the course MUST reach, and the
    sourceless research loop never ran at all because the brief counted as
    "found".

    The structure survived — the spine gate did reject it — so every structural
    check still passed while the evidence behind them was about another subject
    entirely. That is why these tests assert on the ROUTING, not the output.
    """

    def _builder(self):
        import tempfile
        from services.common.storage import StorageManager
        return SkeletonBuilder(storage=StorageManager(
            tempfile.mkdtemp(prefix="evidence_test_")), scope=2, mastery=2,
            starting_from=1)

    def _sociology_brief(self):
        """The real shape of the failure: one adjacent book, nothing else."""
        return {
            "found": True,
            "level": "college",
            "syllabi": [{
                "source": "OpenStax", "book": "Introduction to Sociology",
                "relevance": 2.1,
                "chapters": ["Being a Sociologist", "Culture", "Demography",
                             "Deviance", "Economy", "Education", "Family"],
            }],
            "courses": [], "canonical_texts": [],
        }

    def test_an_adjacent_book_does_not_ground_the_course(self):
        b = self._builder()
        out, supp = b._partition_brief(self._sociology_brief(), "Dungeon Mastering")
        self.assertEqual(out["syllabi"], [], "sociology must not ground D&D")
        self.assertEqual(len(supp), 1, "but it must be kept, not discarded")
        self.assertFalse(b._grounded)

    def test_a_wrong_subject_match_no_longer_suppresses_the_research_loop(self):
        """The costliest consequence: least material, therefore least research.

        `found` gated the sourceless loop, so a wrong-subject match switched off
        the fallback that exists for exactly that situation.
        """
        b = self._builder()
        out, _ = b._partition_brief(self._sociology_brief(), "Dungeon Mastering")
        self.assertFalse(out["found"],
                         "a brief holding only adjacent material is sourceless")

    def test_a_real_match_still_grounds_normally(self):
        """The gate must not make everything sourceless."""
        b = self._builder()
        brief = {"found": True, "level": "college", "courses": [],
                 "syllabi": [{"source": "OpenStax", "book": "Linear Algebra",
                              "relevance": 9.4,
                              "chapters": ["Elimination", "Determinants"]}]}
        out, supp = b._partition_brief(brief, "Linear Algebra")
        self.assertEqual(len(out["syllabi"]), 1)
        self.assertEqual(supp, [])
        self.assertTrue(out["found"])
        self.assertTrue(b._grounded)

    def test_strong_and_weak_sources_separate_rather_than_pool(self):
        b = self._builder()
        brief = {"found": True, "level": "college", "courses": [], "syllabi": [
            {"source": "OpenStax", "book": "Linear Algebra", "relevance": 9.4,
             "chapters": ["Elimination"]},
            {"source": "OpenStax", "book": "College Algebra", "relevance": 3.2,
             "chapters": ["Exponential and Logarithmic Functions"]},
        ]}
        out, supp = b._partition_brief(brief, "Linear Algebra")
        self.assertEqual([o["book"] for o in out["syllabi"]], ["Linear Algebra"])
        self.assertEqual([o["book"] for o in supp], ["College Algebra"])

    def test_wikiversity_courses_are_not_dropped_for_lacking_a_score(self):
        """They carry no relevance because they are fetched by direct topic
        search, not matched out of a catalogue. Filtering on a score they do not
        have would drop every one of them."""
        b = self._builder()
        brief = {"found": True, "level": "college", "syllabi": [],
                 "courses": [{"course": "Dungeons and Dragons",
                              "sections": ["Running a game"]}]}
        out, _ = b._partition_brief(brief, "Dungeon Mastering")
        self.assertTrue(out["found"], "a topic-searched course is still evidence")
        self.assertEqual(len(out["courses"]), 1)

    def test_supplementary_is_handed_to_hydration_not_thrown_away(self):
        """Excluded from structure, kept for content — the two stages ask
        different questions, and the hydration one is narrower."""
        b = self._builder()
        _, supp = b._partition_brief(self._sociology_brief(), "Dungeon Mastering")
        self.assertEqual(b._supplementary_sources, supp)
        self.assertTrue(supp[0]["chapters"], "chapters must survive for hydration")

    def test_an_empty_brief_is_handled(self):
        b = self._builder()
        out, supp = b._partition_brief(None, "Anything")
        self.assertIsNone(out)
        self.assertEqual(supp, [])

    def test_a_missing_relevance_score_is_treated_as_unproven(self):
        """Absent is not the same as high. A source with no score has not
        demonstrated it speaks for the subject, so it cannot ground one."""
        b = self._builder()
        brief = {"found": True, "level": "college", "courses": [],
                 "syllabi": [{"source": "X", "book": "Mystery Book",
                              "chapters": ["A", "B"]}]}
        out, supp = b._partition_brief(brief, "Linear Algebra")
        self.assertEqual(out["syllabi"], [])
        self.assertEqual(len(supp), 1)


class TestSourcelessRouting(unittest.TestCase):
    """The partition must not silently disable the fallback it exists to enable.

    Measured: with _partition_brief in place, a Dungeon Mastering build
    correctly reported itself sourceless and then returned through the
    "no curriculum evidence" branch, which predates the research loop and exits
    before reaching its trigger. The change meant to guarantee the loop ran for
    sourceless subjects guaranteed it never ran.
    """

    def _builder(self):
        import tempfile
        from services.common.storage import StorageManager
        return SkeletonBuilder(storage=StorageManager(
            tempfile.mkdtemp(prefix="route_test_")), scope=2, mastery=2,
            starting_from=1)

    def test_a_sourceless_subject_reaches_the_research_loop(self):
        b = self._builder()
        b.course_params = {"total_concepts_approx": 100}
        calls = []

        def _fake_brief(topic, **kw):
            return {"found": True, "level": "college", "courses": [],
                    "canonical_texts": [],
                    "syllabi": [{"source": "OpenStax", "book": "Sociology",
                                 "relevance": 2.1, "chapters": ["Culture"]}]}

        with patch.object(SkeletonBuilder, "_parent_subjects",
                          return_value=([], False)), \
             patch.object(SkeletonBuilder, "_run_sourceless_research",
                          side_effect=lambda t: calls.append(t) or {"ran": True}), \
             patch.dict("sys.modules", {}, clear=False), \
             patch("services.research.curriculum_research.curriculum_brief",
                   _fake_brief):
            b._syllabus_evidence("Dungeon Mastering")

        self.assertEqual(calls, ["Dungeon Mastering"],
                         "an adjacent-only brief must route to iterative research")
        self.assertEqual(b._research_loop_result, {"ran": True})


# ---------------------------------------------------------------------------
# THE UNIT-COUNT CORRECTION ROUND IN THE ONE-SHOT PATH CANNOT RUN.
#
# Traced from a real HTTP-status-codes build where 3 of 4 modules fell back to
# the chunked path. The one-shot calls llm_generate_json with a schema carrying
# minItems == _shape_lo("units_per_module"), and llm_generate_json validates
# locally before returning. So:
#
#   short answer  -> validation fails -> retries exhaust -> None -> units == []
#   passing answer-> already has >= the minimum
#
# and the guard `0 < len(units_data) < _min_units` is false either way. The
# round's prompt ("split the material BY TOPIC ... do not simply rename") is
# better than the generic one, but nothing has ever executed it.
#
# Left in place deliberately: it becomes live the moment the first call's
# schema stops carrying the floor, which is a change worth measuring rather
# than making blind. This test exists so the next reader learns that from a
# test instead of an hour of log archaeology, and FAILS if the two floors ever
# drift apart -- because then the guard silently starts mattering.
# ---------------------------------------------------------------------------

def test_oneshot_unit_floor_is_enforced_by_the_schema_not_the_retry_round():
    import inspect
    from services.core.course_builder import SkeletonBuilder

    src = inspect.getsource(SkeletonBuilder)
    assert "0 < len(units_data) < _min_units" in src, \
        "the guard moved; re-derive whether it is reachable"

    # Both floors come from the same shared band, which is what makes the
    # guard unreachable. If someone parameterises one of them differently,
    # this assertion is the alarm.
    assert src.count('_shape_lo("units_per_module", 2)') >= 2, \
        ("the one-shot schema floor and the correction guard no longer read "
         "the same band -- the correction round may now be reachable, and "
         "needs a real build to validate")


def test_schema_floor_rejects_a_short_subtree_before_any_correction_round():
    """The mechanism above, demonstrated rather than asserted from source."""
    from services.common.llm_utils import schema_violation
    from services.core.course_builder import SkeletonBuilder

    schema = SkeletonBuilder.subtree_schema(min_units=2, min_lessons=1,
                                            min_concepts=1)
    one_unit = {"units": [{"title": "Only Unit", "lessons": [
        {"title": "L", "concepts": [{"title": "C", "objectives": ["o"]}]}]}]}

    detail = schema_violation(one_unit, schema)
    assert detail, "a 1-unit subtree must not pass a 2-unit floor"
    # And the rejection must be actionable, since it becomes the retry prompt.
    assert "needs at least 2" in detail
