"""A4.1b — this learner's own record reaching the tutor.

The sprint plan calls it "the thing no competitor can do", and the claim holds:
ChatGPT has no memory of your last three sessions, and fixed-curriculum
platforms model you as a percentage. Helga has persisted `times_correct`,
`lapses` and FSRS `stability` per concept per learner since schema v10, and
until now nothing in the tutoring path read any of it.

The property these tests protect hardest is the REFUSAL. With no record, or a
record too thin to mean anything, the note must be None -- an invented struggle
is worse than no personalisation, because the tutor would open by correcting a
mistake the learner never made.
"""
from services.common import learner_history as lh

TITLES = {"c1": "Confounders", "c2": "Mediators", "c3": "Colliders"}


def _row(correct=0, lapses=0, seen=None, stability=None):
    r = {"times_correct": correct, "lapses": lapses}
    if seen is not None:
        r["times_seen"] = seen
    if stability is not None:
        r["stability"] = stability
    return r


# ------------------------------------------------------------- the refusal
def test_no_rows_means_no_note():
    assert lh.summarise({}, TITLES) is None


def test_a_single_attempt_is_noise_not_a_pattern():
    """One wrong answer on a Tuesday is not 'you struggle with this'."""
    assert lh.summarise({"c1": _row(correct=0, lapses=1, seen=1)}, TITLES) is None


def test_a_clean_record_produces_nothing_to_say():
    assert lh.summarise({"c1": _row(correct=4, lapses=0, seen=4)}, TITLES) is None


def test_a_concept_with_no_title_is_dropped_rather_than_shown_raw():
    """A uid in a prompt is noise the learner would see the tutor recite."""
    assert lh.summarise({"con_9fa21bb0": _row(correct=1, lapses=3)}, {}) is None


# -------------------------------------------------------------- what it says
def test_repeated_lapses_are_reported_with_the_count():
    note = lh.summarise({"c1": _row(correct=2, lapses=3)}, TITLES)
    assert note and "Confounders" in note and "3 times" in note


def test_a_wrong_answer_never_yet_right_is_reported():
    note = lh.summarise({"c2": _row(correct=0, lapses=1, seen=2)}, TITLES)
    assert note and "Mediators" in note


def test_a_fragile_memory_is_reported_even_when_last_answer_was_right():
    """FSRS stability is the point: right today, gone by Thursday."""
    note = lh.summarise({"c3": _row(correct=3, lapses=0, seen=3, stability=1.2)},
                        TITLES)
    assert note and "Colliders" in note and "fades" in note


def test_it_says_the_record_is_theirs_not_a_generalisation():
    """The whole reason this is a separate channel from `misconceptions`.

    "Students often believe X" is a claim about students. This is a claim about
    the person in the chair, and must read that way.
    """
    note = lh.summarise({"c1": _row(correct=2, lapses=3)}, TITLES)
    assert "their own past sessions" in note
    assert "Students often" not in note


def test_the_tutor_is_told_not_to_read_the_list_back():
    note = lh.summarise({"c1": _row(correct=2, lapses=3)}, TITLES)
    assert "not read this list back" in note


# ------------------------------------------------------------------ bounds
def test_the_worst_problem_comes_first():
    rows = {"c3": _row(correct=3, lapses=0, seen=3, stability=1.0),
            "c1": _row(correct=2, lapses=4)}
    note = lh.summarise(rows, TITLES)
    assert note.index("Confounders") < note.index("Colliders")


def test_it_is_bounded_because_it_rides_in_every_turn():
    rows = {f"c{i}": _row(correct=2, lapses=5) for i in range(20)}
    titles = {f"c{i}": f"Concept number {i}" for i in range(20)}
    note = lh.summarise(rows, titles)
    assert len(note) <= lh.MAX_CHARS
    assert note.count(";") < lh.MAX_CONCEPTS


# ------------------------------------------------------------ live storage
class _Boom:
    class progress:
        @staticmethod
        def get_progress(*a, **k):
            raise RuntimeError("db gone")


def test_a_storage_failure_never_breaks_the_turn():
    """Worst case is teaching without the history -- where we already were."""
    assert lh.for_concept(_Boom(), "c1", titles=TITLES) is None


class _Store:
    def __init__(self, rows):
        self._rows = rows
        outer = self

        class _P:
            @staticmethod
            def get_progress(uid, student_id=None):
                return outer._rows.get(uid)
        self.progress = _P()


def test_it_reads_the_current_concept_and_its_neighbours():
    store = _Store({"c1": _row(correct=2, lapses=3),
                    "c2": _row(correct=0, lapses=2, seen=2)})
    note = lh.for_concept(store, "c1", related_uids=["c2"], titles=TITLES)
    assert note and "Confounders" in note and "Mediators" in note


def test_the_sentences_are_grammatical():
    """"they has forgotten" shipped once; it reads like a bug in the tutor."""
    rows = {"c1": _row(correct=2, lapses=3),
            "c2": _row(correct=0, lapses=1, seen=2),
            "c3": _row(correct=3, lapses=0, seen=3, stability=1.0)}
    for note in (lh.summarise({k: v}, TITLES) for k, v in rows.items()):
        assert note
        for bad in ("they has", "they is", "they was", "they does"):
            assert bad not in note, f"ungrammatical: {note}"
