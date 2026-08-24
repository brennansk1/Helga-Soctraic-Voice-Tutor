"""Padding is not content, and a course must not ship it.

Measured on a real "Advanced SQL for analytics engineering" build: the module
"Set Operations and Data Reconciliation" came out with two units. The first had
nine real concepts. The second was entirely scaffolding — three lessons named
"... Part 2 Lesson 1/2/3" holding five concepts named "... Part 2 Lesson 3
Part 1", each with the single objective "Understand ... Part 2 Lesson 3".

The builder pads deliberately, reasoning that a generic concept beats a black
hole in the learning path. It does not: the course reported 108 concepts, five
of which were dead ends, and hydration spent model time writing content for
titles that mean nothing.
"""
import pytest

from services.core.course_builder import SkeletonBuilder


class TestIsPlaceholderTitle:
    def test_padded_child_of_its_own_parent_is_scaffolding(self):
        assert SkeletonBuilder.is_placeholder_title(
            "Set Operations and Deduplication Logic Part 2 Lesson 3 Part 1",
            "Set Operations and Deduplication Logic Part 2 Lesson 3")

    def test_a_real_title_is_not(self):
        assert not SkeletonBuilder.is_placeholder_title(
            "Window Function Frames", "Frames and Ordering")

    def test_part_n_under_a_DIFFERENT_parent_is_content(self):
        """"Window Functions Part 2" is a legitimate title when the lesson is
        not called "Window Functions" — the stem must match the parent."""
        assert not SkeletonBuilder.is_placeholder_title(
            "Window Functions Part 2", "Ordering and Framing")


def _course():
    return {"modules": [{"title": "Set Operations", "units": [
        {"title": "Real Unit", "lessons": [
            {"title": "Mechanics", "concepts": [
                {"title": "Union Semantics"}, {"title": "Null Alignment"}]}]},
        {"title": "Set Operations Part 2", "lessons": [
            {"title": "Set Operations Part 2 Lesson 1", "concepts": [
                {"title": "Set Operations Part 2 Lesson 1 Part 1"}]},
            {"title": "Set Operations Part 2 Lesson 2", "concepts": [
                {"title": "Set Operations Part 2 Lesson 2 Part 1"},
                {"title": "Set Operations Part 2 Lesson 2 Part 2"}]}]}]}]}


class TestPrune:
    def test_a_wholly_padded_unit_is_dropped(self):
        c = _course()
        tally = SkeletonBuilder.prune_placeholder_scaffolding(SkeletonBuilder, c)
        assert [u["title"] for u in c["modules"][0]["units"]] == ["Real Unit"]
        assert tally == {"concepts": 3, "lessons": 2, "units": 1}

    def test_real_content_survives(self):
        c = _course()
        SkeletonBuilder.prune_placeholder_scaffolding(SkeletonBuilder, c)
        kept = [con["title"] for u in c["modules"][0]["units"]
                for l in u["lessons"] for con in l["concepts"]]
        assert kept == ["Union Semantics", "Null Alignment"]

    def test_a_lesson_with_ONE_real_concept_is_kept_whole(self):
        """Partial padding is not pruned: dropping the padded siblings of a
        real concept would silently thin a lesson the model did populate."""
        c = {"modules": [{"title": "M", "units": [{"title": "U", "lessons": [
            {"title": "L", "concepts": [
                {"title": "A Real Idea"}, {"title": "L Part 2"}]}]}]}]}
        tally = SkeletonBuilder.prune_placeholder_scaffolding(SkeletonBuilder, c)
        assert tally["lessons"] == 0
        assert len(c["modules"][0]["units"][0]["lessons"][0]["concepts"]) == 2

    def test_a_module_is_never_emptied_completely(self):
        c = {"modules": [{"title": "M", "units": [{"title": "M Part 1", "lessons": [
            {"title": "M Part 1 Lesson 1", "concepts": [
                {"title": "M Part 1 Lesson 1 Part 1"}]}]}]}]}
        SkeletonBuilder.prune_placeholder_scaffolding(SkeletonBuilder, c)
        assert c["modules"][0]["units"], "an absent module is a visible hole"
