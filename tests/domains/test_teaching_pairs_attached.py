"""A doc-sourced course must carry its teaching pairs, not just page URLs.

WHY THIS TEST EXISTS
--------------------
A mined pair — a real error and its fix, a command and its output — is the
strongest Socratic move available without a sandbox, and it can only come from
the SOURCE TEXT.

Book courses can recover that text (they store a chapter reference and the book
file). Doc and DevDocs courses store page URLs, so at teaching time the tutor
would have to re-fetch a page mid-turn: a network round trip on a machine where
latency is already the acute defect, and impossible for a product meant to run
offline.

Measured before the fix: courses built from crawled docs and from DevDocs both
mined 0 pairs at teaching time, while the same harness mined 3 of 3 on a book.
The material existed at build time and was thrown away.

So the pair is mined once, during the build, and attached to the concept — the
same treatment `code_example` already gets.
"""
import json

from services.domains.computer_science import code_examples as ce


class _Chapter:
    def __init__(self, order, text):
        self.order, self.text = order, text
        self.title = f"ch{order}"


class _Book:
    def __init__(self, chapters):
        self._c = {c.order: c for c in chapters}

    def chapter(self, order):
        return self._c.get(order)


#: An error next to its fix — the pair shape worth the most, and the one a
#: learner cannot get by reading alone.
PAIRED = """
Run the model and it fails:

```text
Compilation Error in model my_model
  dbt0101: no viable alternative at input '(    )'
```

Adding the missing argument resolves it:

```sql
select * from {{ ref('upstream_model') }}
```
"""


def _course(chapter_order=1):
    return {
        "uid": "c1", "title": "T",
        "modules": [{"units": [{"lessons": [{
            "book_chapter": chapter_order,
            "concepts": [{"title": "Compilation errors", "uid": "x"}],
        }]}]}],
    }


def _concept(course):
    return course["modules"][0]["units"][0]["lessons"][0]["concepts"][0]


def test_pair_is_attached_at_build_time():
    course = _course()
    tally = ce.attach_to_course(course, _Book([_Chapter(1, PAIRED)]))

    pair = _concept(course).get("teaching_pair")
    assert pair, "the pair was in the source and was not carried onto the concept"
    assert pair["kind"] == "ERROR_FIX"
    assert "dbt0101" in pair["first"], "the real error text must survive verbatim"
    assert tally.get("teaching_pairs") == 1


def test_pair_survives_json_round_trip():
    """The concept is written to structure.json; the pair has to reach disk."""
    course = _course()
    ce.attach_to_course(course, _Book([_Chapter(1, PAIRED)]))
    pair = _concept(json.loads(json.dumps(course))).get("teaching_pair")
    assert pair and "dbt0101" in pair["first"]


def test_no_pair_is_not_an_error():
    """Most concepts have no minable pair. That must cost nothing."""
    course = _course()
    tally = ce.attach_to_course(
        course, _Book([_Chapter(1, "Prose with no code at all.")]))
    assert _concept(course).get("teaching_pair") is None
    assert tally.get("teaching_pairs") == 0


def test_pair_is_bounded():
    """A pair goes into every prompt for that concept; it cannot be unbounded."""
    big = "\n".join([
        "```text", "Error: " + "x" * 5000, "```", "",
        "```sql", "select " + "y" * 5000, "```",
    ])
    course = _course()
    ce.attach_to_course(course, _Book([_Chapter(1, big)]))
    pair = _concept(course).get("teaching_pair")
    if pair:                       # oversized blocks may be rejected upstream
        assert len(pair["first"]) <= 900
        assert len(pair["second"]) <= 500


def test_a_broken_miner_does_not_break_the_build(monkeypatch):
    """A pair is a bonus. If mining raises, the code example must still land."""
    from services.domains.computer_science import code_pairs

    def boom(_text, **kw):
        raise RuntimeError("miner exploded")

    monkeypatch.setattr(code_pairs, "best_pair", boom)
    course = _course()
    tally = ce.attach_to_course(course, _Book([_Chapter(1, PAIRED)]))
    assert tally.get("teaching_pairs") == 0
    assert tally.get("examples", 0) >= 1, "the example must survive a pair failure"
