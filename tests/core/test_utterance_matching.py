"""A student's answer is not a command, and containing a word is not saying it.

Both of these shipped as substring matches against the raw utterance, and both
were destructive on ordinary subject content:

  * `_detect_ignorance` listed "pass", "lost", "unknown", "help", "stuck",
    "skip" and matched them anywhere in the text, so "ions pass through the
    membrane" skipped the grader entirely and was hard-coded to grade 1 — a
    correct answer resetting the streak and dropping the Bloom level.
  * `handle_global_commands` matched "stop"/"reset"/"pause" the same way, so
    "the stop codon terminates translation" cleared the lesson node and emptied
    the syllabus queue mid-session.

These tests are the regression guard. The false-positive cases are drawn from
real subject matter, because that is where the bug actually lived.
"""
import os
import sys
import unittest

_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
sys.path.insert(0, _ROOT)
# fsm_logic imports its siblings by bare name (`import fsrs_engine`), so the
# service's own directory has to be importable too.
sys.path.insert(0, os.path.join(_ROOT, 'services', 'core'))


def _fsm():
    """A bare instance; these predicates touch no external state."""
    from services.core.fsm_logic import MnemosyneFSM
    return MnemosyneFSM.__new__(MnemosyneFSM)


class TestIgnoranceDetection(unittest.TestCase):
    # Real answers that a substring match mis-read as "I don't know".
    ANSWERS = [
        "Ions pass through selective channels",
        "Energy is lost as heat to the surroundings",
        "Solve for the unknown variable",
        "The enzyme helps catalyse the reaction",
        "Skip connections let gradients flow in ResNet",
        "A caesura is a pause in a line of verse",
        "The stop codon terminates translation",
        # A hedge on a real attempt is an attempt, not a refusal to answer.
        "I'm not sure, but I think the derivative is 2x",
    ]

    ADMISSIONS = [
        "I don't know", "idk", "dunno", "no idea", "I have no idea",
        "pass", "stuck", "I'm lost", "what?", "I need help with this",
        "no clue", "I'm confused",
    ]

    def test_real_answers_reach_the_grader(self):
        f = _fsm()
        for text in self.ANSWERS:
            self.assertFalse(
                f._detect_ignorance(text),
                f"{text!r} was treated as an admission of ignorance, so it "
                f"never reached the grader and was scored 1")

    def test_genuine_admissions_are_still_detected(self):
        f = _fsm()
        for text in self.ADMISSIONS:
            self.assertTrue(
                f._detect_ignorance(text),
                f"{text!r} is a genuine admission and must short-circuit the "
                f"grader")


class TestCommandMatching(unittest.TestCase):
    def test_content_containing_a_command_word_is_not_a_command(self):
        f = _fsm()
        for text in ["the stop codon terminates translation",
                     "a caesura is a pause in a line of verse",
                     "the next step is to divide both sides by 3",
                     "in the previous chapter we saw that",
                     "reset the apparatus before measuring",
                     "skip connections in a residual network"]:
            self.assertFalse(
                f._is_command(text, ("stop", "reset", "pause", "next",
                                     "previous", "skip")),
                f"{text!r} is subject content, not a command")

    def test_bare_commands_and_polite_wrappers_still_work(self):
        f = _fsm()
        self.assertTrue(f._is_command("stop", ("stop",)))
        self.assertTrue(f._is_command("Stop.", ("stop",)))
        self.assertTrue(f._is_command("please stop", ("stop",)))
        self.assertTrue(f._is_command("ok pause now", ("pause",)))
        self.assertTrue(f._is_command("next", ("next",)))

    def test_empty_input_is_not_a_command(self):
        f = _fsm()
        self.assertFalse(f._is_command("", ("stop",)))
        self.assertFalse(f._is_command("   ", ("stop",)))


if __name__ == '__main__':
    unittest.main()
