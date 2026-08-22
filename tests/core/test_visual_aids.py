"""Tests for visual teaching aids (B13).

The spec layer is the security and correctness boundary for everything the model
sends, so most of these are adversarial: malformed JSON, hostile strings, absurd
counts, dangling references, unknown kinds. The rule under test throughout is
that a bad aid costs the learner NOTHING — the tutor's message is still
delivered, and no JSON ever leaks into the chat.
"""
import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from services.common.visual_aids import (      # noqa: E402
    AidStore, KINDS, MAX_AIDS_PER_MESSAGE, aid_id, describe, descriptor,
    extract_aids, layout_graph, max_stage, normalize_aid,
)


# ---------------------------------------------------------------- validation
class TestNormalization(unittest.TestCase):
    def test_every_declared_kind_has_a_working_minimal_spec(self):
        """KINDS is the public contract — each must normalize from a minimal spec.
        Guards against a kind being advertised to the model but not implemented."""
        minimal = {
            "number_line": {"min": 0, "max": 10},
            "geometry": {"points": {"A": [0, 0], "B": [1, 1]}},
            "plot": {"series": [{"points": [[0, 0], [1, 1]]}]},
            "bars": {"categories": ["a"], "series": [{"values": [1]}]},
            "graph": {"nodes": [{"id": "n1"}]},
            "timeline": {"events": [{"at": 1969, "label": "Moon"}]},
            "table": {"columns": ["a"], "rows": [["1"]]},
            "venn": {"sets": [{"label": "A"}, {"label": "B"}]},
            "cycle": {"steps": [{"label": "one"}, {"label": "two"}]},
            "steps": {"steps": [{"label": "first"}]},
            "fraction": {"models": [{"parts": 4, "shaded": 1}]},
            "image": {"src": "/static/img/x.png"},
            "code": {"language": "python", "code": "x = 1"},
        }
        self.assertEqual(set(minimal), set(KINDS), "KINDS and this table drifted apart")
        for kind, spec in minimal.items():
            aid, err = normalize_aid({"kind": kind, "spec": spec})
            self.assertIsNone(err, f"{kind}: {err}")
            self.assertEqual(aid["kind"], kind)
            self.assertTrue(aid["alt"], f"{kind} produced no description")

    def test_unknown_kind_is_rejected_with_a_usable_message(self):
        aid, err = normalize_aid({"kind": "hologram", "spec": {}})
        self.assertIsNone(aid)
        self.assertIn("hologram", err)
        self.assertIn("number_line", err)     # tells the model what IS valid

    def test_inline_spec_and_nested_spec_both_accepted(self):
        """Models write both shapes naturally; neither should be a failure."""
        nested, e1 = normalize_aid({"kind": "steps", "spec": {"steps": [{"label": "x"}]}})
        inline, e2 = normalize_aid({"kind": "steps", "steps": [{"label": "x"}]})
        self.assertIsNone(e1)
        self.assertIsNone(e2)
        self.assertEqual(nested["spec"], inline["spec"])

    def test_angle_brackets_survive_because_maths_needs_them(self):
        """Regression: stripping <> server-side turned '2 < x < 5' into '2 x 5',
        destroying the inequality. Escaping belongs at the render boundary."""
        aid, err = normalize_aid({"kind": "number_line", "spec": {
            "min": 0, "max": 10,
            "intervals": [{"from": 2, "to": 5, "label": "2 < x <= 5"}]}})
        self.assertIsNone(err)
        self.assertEqual(aid["spec"]["intervals"][0]["label"], "2 < x <= 5")

    def test_control_characters_are_stripped_and_text_is_capped(self):
        aid, err = normalize_aid({"kind": "steps", "spec": {
            "steps": [{"label": "a\x00b\nc" + "z" * 500}]}})
        self.assertIsNone(err)
        label = aid["spec"]["steps"][0]["label"]
        self.assertNotIn("\x00", label)
        self.assertNotIn("\n", label)
        self.assertLessEqual(len(label), 160)

    def test_colour_is_an_enum_so_no_css_can_be_injected(self):
        aid, _ = normalize_aid({"kind": "steps", "spec": {
            "steps": [{"label": "x", "color": "url(javascript:alert(1))"}]}})
        self.assertEqual(aid["spec"]["steps"][0]["color"], "c1")

    def test_image_src_rejects_remote_hosts(self):
        """An offline-first tutor must not silently fetch from the internet."""
        _, err = normalize_aid({"kind": "image", "spec": {"src": "https://evil.example/x.png"}})
        self.assertIsNotNone(err)
        ok, err2 = normalize_aid({"kind": "image", "spec": {"src": "/static/img/cell.png"}})
        self.assertIsNone(err2)
        self.assertEqual(ok["spec"]["src"], "/static/img/cell.png")

    def test_dangling_graph_edges_are_dropped_not_fatal(self):
        aid, err = normalize_aid({"kind": "graph", "spec": {
            "nodes": [{"id": "a"}, {"id": "b"}],
            "edges": [{"from": "a", "to": "b"}, {"from": "a", "to": "ghost"},
                      {"from": "a", "to": "a"}]}})
        self.assertIsNone(err)
        self.assertEqual(len(aid["spec"]["edges"]), 1)   # ghost + self-edge dropped

    def test_absurd_counts_are_clamped(self):
        """Counts are truncated, not rejected — a model that asks for 200 marks
        gets a readable 60 rather than no diagram at all."""
        aid, err = normalize_aid({"kind": "number_line", "spec": {
            "min": 0, "max": 10,
            "marks": [{"at": i} for i in range(200)]}})
        self.assertIsNone(err)
        self.assertLessEqual(len(aid["spec"]["marks"]), 60)

    def test_byte_cap_is_checked_before_clamping(self):
        """Cheap rejection first: a megabyte of marks is refused outright rather
        than normalized down, so a runaway generation costs almost no work."""
        _, err = normalize_aid({"kind": "number_line", "spec": {
            "min": 0, "max": 10,
            "marks": [{"at": i, "label": "m" * 100} for i in range(5000)]}})
        self.assertIn("too large", err)

    def test_oversized_spec_is_refused(self):
        _, err = normalize_aid({"kind": "steps", "spec": {
            "steps": [{"label": "x", "detail": "y" * 400} for _ in range(400)]}})
        self.assertIn("too large", err)

    def test_non_finite_numbers_never_reach_the_client(self):
        _, err = normalize_aid({"kind": "number_line",
                                "spec": {"min": float("nan"), "max": 10}})
        self.assertIsNotNone(err)

    def test_normalize_never_raises_on_arbitrary_garbage(self):
        for junk in [None, [], "string", 42, {}, {"kind": None},
                     {"kind": "geometry", "spec": {"points": "not a dict"}},
                     {"kind": "table", "spec": {"columns": ["a"], "rows": ["notalist"]}}]:
            aid, err = normalize_aid(junk)
            self.assertIsNone(aid)
            self.assertTrue(err)

    def test_content_addressed_ids_are_stable_and_distinguishing(self):
        a = {"kind": "steps", "spec": {"steps": [{"label": "one"}]}}
        b = {"kind": "steps", "spec": {"steps": [{"label": "two"}]}}
        self.assertEqual(normalize_aid(a)[0]["id"], normalize_aid(a)[0]["id"])
        self.assertNotEqual(normalize_aid(a)[0]["id"], normalize_aid(b)[0]["id"])

    def test_provenance_defaults_to_authored_and_computed_is_preserved(self):
        plain, _ = normalize_aid({"kind": "steps", "spec": {"steps": [{"label": "x"}]}})
        self.assertEqual(plain["provenance"]["tier"], "authored")
        comp, _ = normalize_aid({"kind": "steps", "spec": {"steps": [{"label": "x"}]},
                                 "provenance": {"tier": "computed"}})
        self.assertEqual(comp["provenance"]["tier"], "computed")

    def test_reveal_defaults_to_tutor_controlled(self):
        """The default must not hand the learner a button that skips the thinking."""
        aid, _ = normalize_aid({"kind": "steps", "spec": {"steps": [{"label": "x"}]}})
        self.assertEqual(aid["reveal"], "tutor")
        learner, _ = normalize_aid({"kind": "steps", "reveal": "learner",
                                    "spec": {"steps": [{"label": "x"}]}})
        self.assertEqual(learner["reveal"], "learner")


# ------------------------------------------------------------------- staging
class TestStages(unittest.TestCase):
    def test_stages_total_is_found_at_any_nesting_depth(self):
        aid, _ = normalize_aid({"kind": "geometry", "spec": {
            "points": {"A": [0, 0], "B": [1, 0], "C": [0, 1]},
            "segments": [{"from": "A", "to": "B"},
                         {"from": "B", "to": "C", "label": "?", "stage": 2}]}})
        self.assertEqual(aid["stages_total"], 2)
        self.assertEqual(aid["stage"], 0)          # always starts concealed

    def test_unstaged_aid_reports_no_layers(self):
        aid, _ = normalize_aid({"kind": "steps", "spec": {"steps": [{"label": "x"}]}})
        self.assertEqual(aid["stages_total"], 0)

    def test_stage_is_clamped(self):
        self.assertLessEqual(max_stage({"a": {"stage": 999}}), 6)


# ---------------------------------------------------------------- extraction
class TestExtraction(unittest.TestCase):
    MSG = ('Look at the two shorter sides.\n\n'
           '```aid\n{"kind":"number_line","min":0,"max":10}\n```\n\n'
           'What do you notice?')

    def test_fence_is_removed_and_aid_returned(self):
        clean, aids, errors = extract_aids(self.MSG)
        self.assertNotIn('```', clean)
        self.assertNotIn('kind', clean)
        self.assertEqual(len(aids), 1)
        self.assertEqual(errors, [])
        self.assertIn('Look at the two shorter sides.', clean)
        self.assertIn('What do you notice?', clean)

    def test_malformed_json_still_removes_the_fence(self):
        """The learner must never see a wall of braces, even on failure."""
        clean, aids, errors = extract_aids('Before\n```aid\n{oh no\n```\nAfter')
        self.assertNotIn('```', clean)
        self.assertNotIn('oh no', clean)
        self.assertEqual(aids, [])
        self.assertTrue(errors)
        self.assertIn('Before', clean)
        self.assertIn('After', clean)

    def test_message_without_a_fence_is_returned_untouched(self):
        text = 'Just a normal question. What is 2 + 2?'
        clean, aids, errors = extract_aids(text)
        self.assertEqual(clean, text)
        self.assertEqual(aids, [])

    def test_more_than_the_cap_is_dropped(self):
        blocks = ''.join('```aid\n{"kind":"steps","steps":[{"label":"s%d"}]}\n```\n' % i
                         for i in range(5))
        clean, aids, errors = extract_aids('Hi\n' + blocks)
        self.assertLessEqual(len(aids), MAX_AIDS_PER_MESSAGE)
        self.assertNotIn('```', clean)
        self.assertTrue(errors)

    def test_a_json_array_of_aids_is_accepted(self):
        _, aids, _ = extract_aids(
            '```aid\n[{"kind":"steps","steps":[{"label":"a"}]},'
            '{"kind":"steps","steps":[{"label":"b"}]}]\n```')
        self.assertEqual(len(aids), 2)

    def test_case_insensitive_fence(self):
        _, aids, _ = extract_aids('```AID\n{"kind":"steps","steps":[{"label":"x"}]}\n```')
        self.assertEqual(len(aids), 1)


# --------------------------------------------------------------- description
class TestDescription(unittest.TestCase):
    def test_description_is_generated_when_the_model_omits_alt(self):
        aid, _ = normalize_aid({"kind": "geometry", "title": "Right triangle", "spec": {
            "points": {"A": [0, 0], "B": [4, 0], "C": [0, 3]},
            "segments": [{"from": "A", "to": "B", "label": "4"}],
            "polygons": [{"vertices": ["A", "B", "C"]}],
            "angles": [{"at": "A", "from": "B", "to": "C", "right": True}]}})
        self.assertIn("triangle", aid["alt"].lower())
        self.assertIn("right angle", aid["alt"].lower())
        self.assertIn("4", aid["alt"])

    def test_supplied_alt_wins_over_the_generated_one(self):
        aid, _ = normalize_aid({"kind": "steps", "alt": "My own words",
                                "spec": {"steps": [{"label": "x"}]}})
        self.assertEqual(aid["alt"], "My own words")

    def test_labels_that_merely_repeat_their_value_are_not_read_twice(self):
        aid, _ = normalize_aid({"kind": "number_line", "spec": {
            "min": 0, "max": 10, "marks": [{"at": 5, "label": "5"}]}})
        self.assertNotIn("5 labelled 5", aid["alt"])

    def test_describe_never_raises_on_a_odd_spec(self):
        self.assertTrue(describe("number_line", {"min": 0, "max": 1, "marks": [],
                                                 "intervals": [], "label": ""}))
        self.assertIsInstance(describe("nonexistent_kind", {}), str)

    def test_descriptor_is_small_enough_for_a_two_second_poll(self):
        """The whole architecture rests on this: descriptors ride in the
        transcript, specs do not."""
        aid, _ = normalize_aid({"kind": "graph", "spec": {
            "nodes": [{"id": "n%d" % i, "label": "Concept %d" % i} for i in range(20)],
            "edges": [{"from": "n%d" % i, "to": "n%d" % (i + 1)} for i in range(19)]}})
        d = len(json.dumps(descriptor(aid)))
        full = len(json.dumps(aid))
        self.assertLess(d, 1200)
        self.assertLess(d * 2, full)
        self.assertIn("alt", descriptor(aid))    # fallback text travels with it


# -------------------------------------------------------------------- layout
class TestGraphLayout(unittest.TestCase):
    def test_layout_is_deterministic(self):
        """A diagram that twitches between renders is a broken diagram."""
        nodes = [{"id": c} for c in "abcd"]
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "a", "to": "d"}]
        self.assertEqual(layout_graph(nodes, edges, "TB"), layout_graph(nodes, edges, "TB"))

    def test_chain_is_layered_in_order(self):
        nodes = [{"id": c} for c in "abc"]
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
        pos = layout_graph(nodes, edges, "TB")
        self.assertLess(pos["a"][1], pos["b"][1])
        self.assertLess(pos["b"][1], pos["c"][1])

    def test_direction_swaps_the_axes(self):
        nodes = [{"id": "a"}, {"id": "b"}]
        edges = [{"from": "a", "to": "b"}]
        tb, lr = layout_graph(nodes, edges, "TB"), layout_graph(nodes, edges, "LR")
        self.assertEqual(tb["b"][1], lr["b"][0])

    def test_a_cycle_terminates(self):
        """Concept maps are rarely DAGs; the relaxation must not spin."""
        nodes = [{"id": c} for c in "abc"]
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "c", "to": "a"}]
        pos = layout_graph(nodes, edges, "TB")
        self.assertEqual(len(pos), 3)

    def test_every_node_gets_a_position_even_when_isolated(self):
        pos = layout_graph([{"id": "lonely"}, {"id": "b"}], [], "TB")
        self.assertIn("lonely", pos)
        self.assertIn("b", pos)


# --------------------------------------------------------------------- store
class TestAidStore(unittest.TestCase):
    def _aid(self, label):
        return normalize_aid({"kind": "steps", "spec": {"steps": [{"label": label}]}})[0]

    def test_lru_eviction_keeps_the_store_bounded(self):
        store = AidStore(capacity=3)
        for i in range(10):
            store.put(self._aid("step %d" % i))
        self.assertEqual(len(store), 3)

    def test_recently_read_entries_survive_eviction(self):
        store = AidStore(capacity=2)
        first = store.put(self._aid("one"))
        store.put(self._aid("two"))
        store.get(first)                       # touch it
        store.put(self._aid("three"))
        self.assertIn(first, store)

    def test_reemitting_an_identical_aid_does_not_duplicate(self):
        store = AidStore(capacity=8)
        store.put(self._aid("same"))
        store.put(self._aid("same"))
        self.assertEqual(len(store), 1)

    def test_missing_aid_returns_none_rather_than_raising(self):
        self.assertIsNone(AidStore().get("aid_doesnotexist"))
        self.assertIsNone(AidStore().set_stage("aid_nope", 2))

    def test_set_stage_is_clamped_to_the_spec(self):
        store = AidStore()
        aid = normalize_aid({"kind": "steps", "spec": {
            "steps": [{"label": "a"}, {"label": "b", "stage": 1}]}})[0]
        key = store.put(aid)
        self.assertEqual(store.set_stage(key, 99)["stage"], 1)   # stages_total == 1
        self.assertEqual(store.set_stage(key, -5)["stage"], 0)


# ------------------------------------------------------- FSM wiring (B13/B14)
core_deps = {
    'kuzu': MagicMock(), 'libzim': MagicMock(), 'sentence_transformers': MagicMock(),
    'psutil': MagicMock(), 'yaml': MagicMock(), 'fsrs_engine': MagicMock(),
    'safety': MagicMock(), 'service_manager': MagicMock(), 'db_manager': MagicMock(),
    'content_provider': MagicMock(), 'course_builder': MagicMock(),
}
with patch.dict('sys.modules', core_deps):
    # Keep the MODULE object, not just the class: fsm_logic does
    # `from safety import check_output_safety`, so the name to patch lives on
    # this module, and `services.core.fsm_logic` is not resolvable as an
    # attribute path after an import performed inside patch.dict.
    from services.core import fsm_logic as fsm_module     # noqa: E402
    MnemosyneFSM = fsm_module.MnemosyneFSM


def _bare_fsm(aids_enabled=True):
    """Same pattern as test_fsm_tools.py — the real FSM __init__ is heavy."""
    with patch.object(MnemosyneFSM, '__init__', lambda self, *a, **kw: None):
        fsm = MnemosyneFSM.__new__(MnemosyneFSM)
    fsm._visual_aids_enabled = aids_enabled
    fsm.aid_store = AidStore(capacity=16)
    fsm._pending_aids = []
    fsm.transcript = []
    fsm.current_lesson_node = {"title": "Pythagorean theorem"}
    fsm.grade_band = "9-12"
    fsm.student_id = "stu_test0001"
    fsm._log_safety_incident = MagicMock()
    return fsm


class TestFSMWiring(unittest.TestCase):
    SAFE = MagicMock(is_safe=True, category="", confidence=0.0, message="")

    def test_aid_is_lifted_out_of_the_message_and_stored(self):
        fsm = _bare_fsm()
        msg = ('Look at this.\n```aid\n{"kind":"number_line","min":0,"max":10}\n```\n'
               'Where does 7 sit?')
        with patch.object(fsm_module, 'check_output_safety', return_value=self.SAFE):
            fsm.add_message(msg)
        entry = fsm.transcript[0]
        self.assertNotIn('```', entry['text'])
        self.assertIn('Where does 7 sit?', entry['text'])
        self.assertEqual(len(entry['aids']), 1)
        # The transcript carries the descriptor; the spec lives in the store.
        self.assertNotIn('spec', entry['aids'][0])
        self.assertIn(entry['aids'][0]['id'], fsm.aid_store)

    def test_message_without_an_aid_gains_no_aids_key(self):
        fsm = _bare_fsm()
        with patch.object(fsm_module, 'check_output_safety', return_value=self.SAFE):
            fsm.add_message('Just a question?')
        self.assertNotIn('aids', fsm.transcript[0])

    def test_flag_off_leaves_the_fence_untouched(self):
        fsm = _bare_fsm(aids_enabled=False)
        msg = 'Hi\n```aid\n{"kind":"steps","steps":[{"label":"x"}]}\n```'
        with patch.object(fsm_module, 'check_output_safety', return_value=self.SAFE):
            fsm.add_message(msg)
        self.assertNotIn('aids', fsm.transcript[0])

    def test_a_suppressed_message_keeps_no_diagram(self):
        """An orphaned figure under a safety fallback is worse than none."""
        fsm = _bare_fsm()
        unsafe = MagicMock(is_safe=False, category="test", confidence=0.9,
                           message="Let's talk about something else.")
        msg = 'Bad\n```aid\n{"kind":"steps","steps":[{"label":"x"}]}\n```'
        with patch.object(fsm_module, 'check_output_safety', return_value=unsafe):
            fsm.add_message(msg)
        self.assertNotIn('aids', fsm.transcript[0])
        self.assertEqual(fsm.transcript[0]['text'], "Let's talk about something else.")

    def test_broken_aid_still_delivers_the_message(self):
        fsm = _bare_fsm()
        msg = 'Think about it.\n```aid\n{"kind":"bogus"}\n```\nWhat next?'
        with patch.object(fsm_module, 'check_output_safety', return_value=self.SAFE):
            fsm.add_message(msg)
        self.assertIn('Think about it.', fsm.transcript[0]['text'])
        self.assertIn('What next?', fsm.transcript[0]['text'])
        self.assertNotIn('aids', fsm.transcript[0])

    def test_tool_produced_aids_attach_to_the_next_message(self):
        fsm = _bare_fsm()
        aid = normalize_aid({"kind": "steps", "spec": {"steps": [{"label": "x"}]}})[0]
        fsm._aid_sink(aid)
        with patch.object(fsm_module, 'check_output_safety', return_value=self.SAFE):
            fsm.add_message('Here is the plan.')
        self.assertEqual(len(fsm.transcript[0]['aids']), 1)
        self.assertEqual(fsm._pending_aids, [])     # drained

    def test_aid_sink_is_bounded(self):
        fsm = _bare_fsm()
        for i in range(20):
            fsm._aid_sink(normalize_aid(
                {"kind": "steps", "spec": {"steps": [{"label": "s%d" % i}]}})[0])
        self.assertLessEqual(len(fsm._pending_aids), 4)

    def test_reveal_advances_store_and_transcript_together(self):
        """If only the store advanced, the next poll would undo the reveal."""
        fsm = _bare_fsm()
        msg = ('See.\n```aid\n{"kind":"steps","steps":[{"label":"a"},'
               '{"label":"b","stage":1}]}\n```')
        with patch.object(fsm_module, 'check_output_safety', return_value=self.SAFE):
            fsm.add_message(msg)
        aid_key = fsm.transcript[0]['aids'][0]['id']
        self.assertTrue(fsm.reveal_aid(aid_key))
        self.assertEqual(fsm.aid_store.get(aid_key)['stage'], 1)
        self.assertEqual(fsm.transcript[0]['aids'][0]['stage'], 1)

    def test_reveal_of_an_evicted_aid_is_harmless(self):
        fsm = _bare_fsm()
        self.assertFalse(fsm.reveal_aid('aid_gone'))


# ------------------------------------------------------------- tool registry
class TestAidTools(unittest.TestCase):
    def test_aid_tools_absent_without_a_sink(self):
        from services.common.tutor_tools import build_default_registry
        names = [t['function']['name']
                 for t in build_default_registry().to_ollama_tools('qwen3.5:9b')]
        self.assertNotIn('show_visual', names)

    def test_aid_tools_present_with_a_sink(self):
        from services.common.tutor_tools import build_default_registry
        names = [t['function']['name'] for t in
                 build_default_registry(aid_sink=lambda a: None).to_ollama_tools('qwen3.5:9b')]
        for expected in ('show_visual', 'visualize_function', 'visualize_data'):
            self.assertIn(expected, names)

    def test_show_visual_reaches_the_sink_and_confirms_briefly(self):
        from services.common.tutor_tools import build_default_registry
        got = []
        r = build_default_registry(aid_sink=got.append)
        out = r.execute('qwen3.5:9b', 'show_visual', {
            'kind': 'venn', 'title': 'Cells',
            'spec': {'sets': [{'label': 'Plant'}, {'label': 'Animal'}]}})
        self.assertTrue(out['ok'])
        self.assertEqual(len(got), 1)
        payload = json.loads(out['result'])
        self.assertTrue(payload['shown_to_student'])
        # The model gets a short confirmation, not the spec echoed back.
        self.assertNotIn('sets', out['result'])
        self.assertLess(len(out['result']), 800)

    def test_bad_spec_returns_a_correctable_error_not_a_crash(self):
        from services.common.tutor_tools import build_default_registry
        got = []
        r = build_default_registry(aid_sink=got.append)
        out = r.execute('qwen3.5:9b', 'show_visual', {'kind': 'geometry', 'spec': {}})
        self.assertTrue(out['ok'])                      # the CALL succeeded
        payload = json.loads(out['result'])
        self.assertFalse(payload['shown_to_student'])   # the AID did not
        self.assertIn('point', payload['error'])
        self.assertEqual(got, [])

    def test_small_models_cannot_draw(self):
        """A ≤4B model does not emit well-formed nested specs, and a malformed
        diagram is worse than none."""
        from services.common.tutor_tools import build_default_registry
        r = build_default_registry(aid_sink=lambda a: None)
        out = r.execute('qwen3:1.5b', 'show_visual',
                        {'kind': 'steps', 'spec': {'steps': [{'label': 'x'}]}})
        self.assertFalse(out['ok'])
        self.assertIn('tier', out['error'])


# ------------------------------------------------------------------- prompts
class TestPromptWiring(unittest.TestCase):
    def test_grammar_is_taught_when_enabled_and_omitted_when_not(self):
        from services.common import prompts
        with patch.dict(os.environ, {"HELGA_ENABLE_VISUAL_AIDS": "true"}):
            on = prompts.get_socratic_tutor_prompt("ctx", [("hi", "hello")])[0]["content"]
        with patch.dict(os.environ, {"HELGA_ENABLE_VISUAL_AIDS": "false"}):
            off = prompts.get_socratic_tutor_prompt("ctx", [("hi", "hello")])[0]["content"]
        self.assertIn("```aid", on)
        self.assertNotIn("```aid", off)
        self.assertLess(len(off), len(on))

    def test_every_advertised_kind_is_actually_implemented(self):
        """The prompt is a contract with the model — it must not name a kind the
        normalizer would reject."""
        from services.common.prompts import VISUAL_AID_RULES
        for kind in KINDS:
            if kind == "image":
                continue          # not model-authorable; needs a validated src
            self.assertIn(kind, VISUAL_AID_RULES, f"{kind} missing from the prompt")


if __name__ == '__main__':
    unittest.main()


# ------------------------------------------------- assistance rails (B13.10)
class TestAssistanceRails(unittest.TestCase):
    """The model here is qwen3.5:9b (tier 2), asked for nested JSON with
    cross-references, inline in prose, with no retry and no constrained
    decoding. These rails turn a near-miss into a diagram instead of a silent
    drop — the failure mode that would otherwise make the feature never fire."""

    def test_rail1_malformed_json_is_repaired(self):
        # TRUNCATED output ('{"kind":"steps","steps":[{"label":"a"}') can only
        # be recovered by a real repair backend. Both are declared in every
        # service requirements.txt and both are present in the container images
        # (python:3.11-slim); neither installs on a 3.9 host, because
        # json-repair needs >=3.10 and fast-json-repair >=3.11.
        #
        # Skipping is the honest outcome: hand-rolling a bracket-closer here
        # was tried and removed — it could close brackets but could not recover
        # a truncated STRING, which the library does.
        if not any(importlib.util.find_spec(m)
                   for m in ("fast_json_repair", "json_repair")):
            self.skipTest("no JSON repair backend installed (needs Python >=3.10)")
        for payload in [
            "{'kind': 'bars', 'categories': ['Mon','Tue',], 'series': [{'values': [4,7,],},]}",
            '{"kind": "graph", "nodes": [{"id": "a"}], "directed": False}',
            'Sure! Here it is:\n{"kind":"steps","steps":[{"label":"Square it"}]}\nHope that helps.',
            '{"kind":"steps","steps":[{"label":"a"}',           # truncated
        ]:
            _, aids, errors = extract_aids("Look.\n```aid\n%s\n```\nWhy?" % payload)
            self.assertEqual(len(aids), 1, f"{payload[:40]} -> {errors}")

    def test_rail1_preserves_apostrophes(self):
        """A naive quote fix turns "Newton's law" into a syntax error."""
        _, aids, _ = extract_aids(
            "```aid\n{'kind':'steps','steps':[{'label':\"Newton's law\"}]}\n```")
        self.assertEqual(aids[0]["spec"]["steps"][0]["label"], "Newton's law")

    def test_rail2_kind_synonyms_resolve(self):
        for given, expected in [("flowchart", "graph"), ("concept_map", "graph"),
                                ("bar_chart", "bars"), ("pie", "fraction"),
                                ("numberline", "number_line"), ("line_graph", "plot"),
                                ("worked_example", "steps"), ("Bar Chart", "bars")]:
            spec = {"graph": {"nodes": [{"id": "a"}]},
                    "bars": {"categories": ["x"], "series": [{"values": [1]}]},
                    "fraction": {"models": [{"parts": 2, "shaded": 1}]},
                    "number_line": {"min": 0, "max": 5},
                    "plot": {"series": [{"points": [[0, 0], [1, 1]]}]},
                    "steps": {"steps": [{"label": "s"}]}}[expected]
            aid, err = normalize_aid({"kind": given, "spec": spec})
            self.assertIsNotNone(aid, f"{given}: {err}")
            self.assertEqual(aid["kind"], expected)

    def test_rail3_field_synonyms_resolve(self):
        aid, err = normalize_aid({"kind": "graph", "spec": {
            "nodes": [{"id": "a"}, {"id": "b"}], "links": [{"from": "a", "to": "b"}]}})
        self.assertIsNone(err)
        self.assertEqual(len(aid["spec"]["edges"]), 1)
        self.assertIn("graph.links->edges", aid["rails"])

    def test_rail3_bare_lists_become_objects(self):
        aid, _ = normalize_aid({"kind": "steps", "spec": {"steps": ["First", "Second"]}})
        self.assertEqual([s["label"] for s in aid["spec"]["steps"]], ["First", "Second"])
        bars, _ = normalize_aid({"kind": "bars", "spec": {"labels": ["a", "b"], "values": [3, 5]}})
        self.assertEqual(bars["spec"]["series"][0]["values"], [3.0, 5.0])
        self.assertEqual(bars["spec"]["categories"], ["a", "b"])

    def test_rail4_polygon_edges_become_segments(self):
        aid, _ = normalize_aid({"kind": "geometry", "spec": {
            "points": {"A": [0, 0], "B": [4, 0], "C": [0, 3]},
            "polygons": [{"vertices": ["A", "B", "C"]}]}})
        self.assertEqual(len(aid["spec"]["segments"]), 3)

    def test_rail5_false_right_angle_is_removed(self):
        """The rail that matters most: it stops a WRONG diagram, not a missing
        one. A right-angle marker on a 45° angle teaches an error with the full
        authority of a figure, and no prompt wording prevents it."""
        true_right, _ = normalize_aid({"kind": "geometry", "spec": {
            "points": {"A": [0, 0], "B": [4, 0], "C": [0, 3]},
            "angles": [{"at": "A", "from": "B", "to": "C", "right": True}]}})
        self.assertTrue(true_right["spec"]["angles"][0]["right"])

        false_claim, _ = normalize_aid({"kind": "geometry", "spec": {
            "points": {"A": [0, 0], "B": [4, 0], "C": [3, 3]},
            "angles": [{"at": "A", "from": "B", "to": "C", "right": True}]}})
        self.assertFalse(false_claim["spec"]["angles"][0]["right"])
        self.assertTrue(any("right-angle" in r for r in false_claim["rails"]))

    def test_code_blocks_are_never_eaten(self):
        """The tolerant fence must not swallow a Python example the tutor is
        teaching from — that would be far worse than a missing diagram."""
        for text in ['Here:\n```python\nfor i in range(3):\n    print(i)\n```\nWhat prints?',
                     'Try:\n```\nx = 1\n```\nWhat is x?',
                     'JSON:\n```json\n{"name": "Ada", "age": 36}\n```\nWhich key?']:
            clean, aids, _ = extract_aids(text)
            self.assertEqual(clean, text, "a non-aid code block was modified")
            self.assertEqual(aids, [])

    def test_explicit_aid_fence_is_always_removed_even_when_broken(self):
        clean, aids, errors = extract_aids('A\n```aid\n{totally broken\n```\nB')
        self.assertNotIn('```', clean)
        self.assertEqual(aids, [])
        self.assertTrue(errors)

    def test_implicit_json_fence_is_taken_only_when_it_is_an_aid(self):
        clean, aids, _ = extract_aids(
            'See.\n```json\n{"kind":"steps","steps":[{"label":"x"}]}\n```\nOk?')
        self.assertEqual(len(aids), 1)
        self.assertNotIn('```', clean)
