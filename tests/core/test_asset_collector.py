"""Tests for Phase 3 — Asset Collection.

The course is locked until this phase finishes, so its cost is real. Most of
these tests are therefore about NOT doing work: skipping concepts that want no
diagram, reusing within a course, and reusing across courses.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from services.core.asset_collector import (      # noqa: E402
    AssetCollector, load_manifest, plan_slots, wants_photograph,
)

AIDS_REPLY = {"aids": [
    {"slot": "opening", "kind": "geometry", "title": "3-4-5 triangle",
     "caption": "What is the third side?",
     "spec": {"points": {"A": [0, 0], "B": [4, 0], "C": [0, 3]},
              "polygons": [{"vertices": ["A", "B", "C"]}],
              "segments": [{"from": "B", "to": "C", "label": "?", "stage": 1}],
              "angles": [{"at": "A", "from": "B", "to": "C", "right": True}]}},
]}


class TestSkipping(unittest.TestCase):
    """Returning no slots is the single biggest wait-time lever: it means the
    concept never makes an LLM call at all."""

    def test_visual_subjects_get_slots(self):
        for title, content in [("The Pythagorean theorem", "right triangle hypotenuse"),
                               ("Comparing fractions", "numerator denominator"),
                               ("Solving inequalities", "greater than, number line")]:
            self.assertTrue(plan_slots(title, content, []), title)

    def test_abstract_subjects_cost_nothing(self):
        for title, content in [("The nature of moral obligation", "duty, deontology"),
                               ("Interpreting a poem", "metaphor, tone, voice")]:
            self.assertEqual(plan_slots(title, content, []), [], title)

    def test_a_misconception_earns_its_own_slot(self):
        plain = plan_slots("The Pythagorean theorem", "right triangle", [])
        withm = plan_slots("The Pythagorean theorem", "right triangle", ["a^2+b^2 = c"])
        self.assertIn("misconception:0", withm)
        self.assertNotIn("misconception:0", plain)

    def test_slots_are_capped(self):
        slots = plan_slots("Solving equations step by step", "solve method procedure",
                           ["m1", "m2", "m3", "m4"])
        self.assertLessEqual(len(slots), 2)


class TestPhotoRouting(unittest.TestCase):
    def test_photograph_only_where_the_thing_itself_is_the_content(self):
        self.assertTrue(wants_photograph("Vermeer's Girl with a Pearl Earring",
                                         "painting, oil on canvas"))
        self.assertTrue(wants_photograph("Basalt columns", "rock formation landform"))
        self.assertFalse(wants_photograph("Photosynthesis", "chloroplast converts light"))
        self.assertFalse(wants_photograph("Quadratic equations", "parabola vertex"))

    def test_domain_routing_also_triggers(self):
        self.assertTrue(wants_photograph("Anything", "", domains=("art",)))


class _Base(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("DATA_ROOT")
        self._tmp = tempfile.mkdtemp(prefix="helga_assets_")
        os.environ["DATA_ROOT"] = self._tmp
        from services.common.storage import StorageManager
        # PASS the directory. StorageManager's signature is
        # `__init__(self, data_dir="/app/data")` — it does not read DATA_ROOT,
        # so a bare StorageManager() ignored the temp dir this test just made
        # and wrote to /app/data. On a Mac that is a read-only-filesystem
        # error; INSIDE THE CONTAINER it silently succeeded and scribbled on
        # the real data directory, so the isolation this setUp appears to
        # provide never existed.
        self.st = StorageManager(self._tmp)
        self.calls = []

    def tearDown(self):
        if self._old is None:
            os.environ.pop("DATA_ROOT", None)
        else:
            os.environ["DATA_ROOT"] = self._old

    def _course(self, uid, concepts):
        self.st.courses.create_course({
            "uid": uid, "title": "T", "status": "building",
            "modules": [{"uid": "m", "title": "M", "units": [{"uid": "u", "title": "U",
                        "lessons": [{"uid": "l", "title": "L", "concepts": [
                            {"uid": cu, "title": ct} for cu, ct, _ in concepts]}]}]}]})
        for cu, _, body in concepts:
            self.st.courses.save_concept_content(uid, cu, body)

    def _reply(self, prompt, **kw):
        self.calls.append(prompt)
        return json.loads(json.dumps(AIDS_REPLY))


GEO = "## Metadata\nRight triangle hypotenuse legs.\n\n## Misconceptions\n- a^2+b^2 = c\n"
ABSTRACT = "## Metadata\nDuty, deontology and virtue ethics.\n"


class TestCollection(_Base):
    def test_abstract_concepts_never_reach_the_model(self):
        self._course("c1", [("con_1", "The nature of moral obligation", ABSTRACT)])
        with patch("services.common.llm_utils.llm_generate_json", side_effect=self._reply):
            collector = AssetCollector(self.st, images_enabled=False)
            collector.collect("c1")
        self.assertEqual(self.calls, [])
        self.assertEqual(collector.stats["skipped"], 1)

    def test_duplicate_subjects_are_drawn_once(self):
        """Only a whole-course pass can see this; per-concept hydration cannot."""
        self._course("c2", [("con_1", "The Pythagorean theorem", GEO),
                            ("con_2", "The Pythagorean theorem", GEO)])
        with patch("services.common.llm_utils.llm_generate_json", side_effect=self._reply):
            collector = AssetCollector(self.st, images_enabled=False)
            collector.collect("c2")
        self.assertEqual(len(self.calls), 1)
        self.assertGreater(collector.stats["reused"], 0)

    def test_aids_are_written_into_the_concept_markdown(self):
        from services.common.visual_aids import parse_concept_aids
        self._course("c3", [("con_1", "The Pythagorean theorem", GEO)])
        with patch("services.common.llm_utils.llm_generate_json", side_effect=self._reply):
            AssetCollector(self.st, images_enabled=False).collect("c3")
        body = self.st.courses.get_concept_content("c3", "con_1")
        aids = parse_concept_aids(body)
        self.assertIn("opening", aids)
        self.assertEqual(aids["opening"]["stages_total"], 1)
        # The original content must survive alongside the new section.
        self.assertIn("Misconceptions", body)

    def test_manifest_lets_a_session_learn_coverage_in_one_read(self):
        self._course("c4", [("con_1", "The Pythagorean theorem", GEO)])
        with patch("services.common.llm_utils.llm_generate_json", side_effect=self._reply):
            AssetCollector(self.st, images_enabled=False).collect("c4")
        manifest = load_manifest(self.st, "c4")
        self.assertIsNotNone(manifest)
        self.assertIn("con_1", manifest["concepts"])
        self.assertIn("opening", manifest["concepts"]["con_1"]["slots"])

    def test_reruns_do_not_duplicate_the_section(self):
        self._course("c5", [("con_1", "The Pythagorean theorem", GEO)])
        with patch("services.common.llm_utils.llm_generate_json", side_effect=self._reply):
            AssetCollector(self.st, images_enabled=False).collect("c5")
            AssetCollector(self.st, images_enabled=False).collect("c5")
        body = self.st.courses.get_concept_content("c5", "con_1")
        self.assertEqual(body.count("## Visual Aids"), 1)

    def test_a_model_failure_never_fails_the_build(self):
        self._course("c6", [("con_1", "The Pythagorean theorem", GEO)])
        with patch("services.common.llm_utils.llm_generate_json",
                   side_effect=Exception("ollama is down")):
            collector = AssetCollector(self.st, images_enabled=False)
            manifest = collector.collect("c6")     # must not raise
        self.assertEqual(manifest["concepts"], {})

    def test_invalid_specs_are_retried_against_the_named_error(self):
        """The build-time advantage: regenerate against the exact failure."""
        replies = [{"aids": [{"slot": "opening", "kind": "geometry", "spec": {}}]},
                   json.loads(json.dumps(AIDS_REPLY))]
        prompts = []

        def seq(prompt, **kw):
            prompts.append(prompt)
            return replies[min(len(prompts) - 1, len(replies) - 1)]

        self._course("c7", [("con_1", "The Pythagorean theorem", GEO)])
        with patch("services.common.llm_utils.llm_generate_json", side_effect=seq):
            collector = AssetCollector(self.st, images_enabled=False)
            collector.collect("c7")
        self.assertEqual(len(prompts), 2)
        self.assertIn("rejected", prompts[1])          # the error was named
        self.assertGreater(collector.stats["generated"], 0)

    def test_disabled_phase_is_a_clean_no_op(self):
        self._course("c8", [("con_1", "The Pythagorean theorem", GEO)])
        collector = AssetCollector(self.st, enabled=False)
        self.assertEqual(collector.collect("c8")["concepts"], {})
        self.assertEqual(self.calls, [])

    def test_course_budget_stops_a_runaway_build(self):
        self._course("c9", [(f"con_{i}", "The Pythagorean theorem", GEO) for i in range(6)])
        with patch("services.common.llm_utils.llm_generate_json", side_effect=self._reply):
            collector = AssetCollector(self.st, images_enabled=False, budget=1)
            collector.collect("c9")
        self.assertLessEqual(len(self.calls), 2)


class TestSharedLibrary(_Base):
    def test_a_second_course_reuses_the_first_courses_work(self):
        """'The water cycle' is drawn once on this machine, ever."""
        self._course("d1", [("con_1", "The Pythagorean theorem", GEO)])
        with patch("services.common.llm_utils.llm_generate_json", side_effect=self._reply):
            AssetCollector(self.st, images_enabled=False).collect("d1")
        self.assertEqual(len(self.calls), 1)

        self._course("d2", [("con_9", "The Pythagorean theorem", GEO)])
        with patch("services.common.llm_utils.llm_generate_json",
                   side_effect=AssertionError("should have hit the shared library")):
            collector = AssetCollector(self.st, images_enabled=False)
            collector.collect("d2")
        self.assertGreater(collector.stats["reused"], 0)

    def test_library_entries_are_revalidated_on_the_way_out(self):
        """A cache that returns something today's validator would reject is
        worse than a miss."""
        collector = AssetCollector(self.st, images_enabled=False)
        path = collector._library_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump({"poisoned|opening": {"opening": {"kind": "geometry", "spec": {}}}}, fh)
        self.assertIsNone(collector._library_get(("poisoned", ("opening",))))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------- book mode (EPUB/PDF sourced)
class TestBookMode(_Base):
    """A course built from an uploaded book uses that book's figures and
    nothing else."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        from tests.fixtures.make_book_fixtures import build
        cls.books = build(tempfile.mkdtemp(prefix="helga_bookmode_"))

    def _collect(self, uid, concepts, **kw):
        self._course(uid, concepts)
        with patch("services.common.llm_utils.llm_generate_json", side_effect=self._reply):
            collector = AssetCollector(
                self.st, document_path=self.books["epub"],
                document_title="Elements of Geometry", **kw)
            manifest = collector.collect(uid)
        return collector, manifest

    def test_external_archives_are_hard_disabled(self):
        """Not a preference — 'figures from the book only' means the external
        sources must be unreachable, not merely deprioritised."""
        collector = AssetCollector(self.st, document_path=self.books["epub"],
                                   images_enabled=True)
        self.assertFalse(collector.images_enabled)

    def test_book_figures_are_attached_with_the_authors_caption(self):
        from services.common.visual_aids import parse_concept_aids
        _, manifest = self._collect(
            "bk1", [("con_a", "Right triangles",
                     "## Metadata\nA right triangle has legs and a hypotenuse, 3 and 4 units.\n")])
        aids = parse_concept_aids(self.st.courses.get_concept_content("bk1", "con_a"))
        self.assertIn("photo", aids)
        photo = aids["photo"]
        self.assertEqual(photo["kind"], "image")
        self.assertEqual(photo["title"], "Figure 1.1")
        self.assertIn("right triangle", photo["caption"].lower())
        self.assertIn("figure", manifest["concepts"]["con_a"])

    def test_attribution_survives_the_markdown_round_trip(self):
        """Regression: render_concept_aids wrote only the tier, so source and
        licence were lost and a figure came back attributed to nobody."""
        from services.common.visual_aids import parse_concept_aids
        self._collect("bk2", [("con_a", "Right triangles",
                               "## Metadata\nA right triangle with legs 3 and 4 units.\n")])
        photo = parse_concept_aids(
            self.st.courses.get_concept_content("bk2", "con_a"))["photo"]
        self.assertEqual(photo["provenance"]["source"], "Elements of Geometry")
        self.assertIn("uploaded document", photo["provenance"]["license"])
        self.assertEqual(photo["provenance"]["tier"], "retrieved")

    def test_book_figures_never_enter_the_shared_library(self):
        """COPYRIGHT CONTAINMENT. The library is machine-wide; an uploaded book
        is very likely in copyright, and its plates must not be offered to an
        unrelated course."""
        collector, _ = self._collect(
            "bk3", [("con_a", "Right triangles",
                     "## Metadata\nA right triangle with legs 3 and 4 units.\n")])
        self.assertFalse(os.path.exists(collector._library_path()))

    def test_a_figure_is_used_by_at_most_one_concept(self):
        _, manifest = self._collect("bk4", [
            ("con_a", "Right triangles",
             "## Metadata\nA right triangle has legs and a hypotenuse, 3 and 4 units.\n"),
            ("con_b", "Circles: radius and circumference",
             "## Metadata\nThe circumference of a circle relates to its radius.\n")])
        used = [c["figure"]["src"] for c in manifest["concepts"].values() if "figure" in c]
        self.assertEqual(len(used), len(set(used)))

    def test_a_concept_the_book_does_not_illustrate_gets_no_figure(self):
        _, manifest = self._collect("bk5", [
            ("con_z", "Norse mythology", "## Metadata\nOdin, Thor and the nine realms.\n")])
        self.assertNotIn("figure", manifest["concepts"].get("con_z", {}))

    def test_diagrams_and_the_book_figure_coexist(self):
        """The diagram write must not clobber the figure written moments before."""
        from services.common.visual_aids import parse_concept_aids
        self._collect("bk6", [("con_a", "Right triangles",
                               "## Metadata\nA right triangle with legs 3 and 4 units.\n")])
        aids = parse_concept_aids(self.st.courses.get_concept_content("bk6", "con_a"))
        self.assertIn("photo", aids)
        self.assertIn("opening", aids)
