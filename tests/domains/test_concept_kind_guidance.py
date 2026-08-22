"""Concept kinds, and the constraint that every one of them must respect.

THE CONSTRAINT
--------------
Teach programming Socratically WITHOUT asking the learner to type code and
WITHOUT a sandbox. There is nothing to check an answer against, and an
unchecked answer that merely sounds right is the confident-bluffer failure.

That constraint lives in prose, inside per-kind guidance strings, where nothing
enforces it. One edit adding "ask them to write the query" to SYNTAX guidance
would reintroduce it across every CS course with no test failing — and SYNTAX
is precisely the kind where that phrasing is most tempting.

So it is asserted here, per kind, for every kind that exists now or is added
later.
"""
import re

from services.domains.computer_science import concept_kind as ck

#: The phrasings that hand composition back to the learner. Deliberately close
#: to natural tutor language, because that is how it would creep back in.
ASKS_FOR_TYPING = re.compile(
    r"\b(ask (them|the (learner|student)) to (write|type|compose)"
    r"|have (them|the (learner|student)) (write|type)"
    r"|what would you (type|write|enter)"
    r"|(write|type|compose)\s+(the|a|an|your)\s+"
    r"(command|code|query|function|line|statement|snippet))", re.I)

#: The guidance is written to a model, so it states the rule by FORBIDDING the
#: bad move: "Do not ask them to type a whole statement from memory." A naive
#: search flags that prohibition as the violation it exists to prevent — which
#: is exactly what happened the first time this test ran. A negated phrase is
#: the constraint being enforced, not broken.
NEGATED = re.compile(
    r"(never|not|avoid|refrain from|rather than|instead of|don'?t|do not)"
    r"[^.]{0,40}$", re.I)


def _unnegated_hits(text):
    """Typing phrases that are NOT inside a prohibition."""
    hits = []
    for m in ASKS_FOR_TYPING.finditer(text or ""):
        before = text[max(0, m.start() - 60):m.start()]
        if not NEGATED.search(before):
            hits.append(m.group(0))
    return hits


def test_every_kind_has_guidance():
    """A kind with no guidance is a silent downgrade to generic teaching."""
    missing = [k for k in ck.RANK if k != ck.UNKNOWN and not ck.GUIDANCE.get(k)]
    assert not missing, f"kinds with no teaching guidance: {missing}"


def test_no_kind_asks_the_learner_to_type_code():
    """The goal's central constraint, checked against every kind."""
    offenders = []
    for kind, text in ck.GUIDANCE.items():
        for hit in _unnegated_hits(text or ""):
            offenders.append(f"{kind}: {hit!r}")
    assert not offenders, (
        "guidance asks the learner to produce code, which cannot be checked "
        "without a sandbox:\n  " + "\n  ".join(offenders))


def test_a_prohibition_is_not_read_as_a_violation():
    """Guard the guard: the first version of this test failed on SYNTAX's own
    'Do not ask them to type ...', reporting the rule as its breach."""
    assert _unnegated_hits("Do not ask them to type a whole statement.") == []
    assert _unnegated_hits("Never ask them to write the query.") == []
    assert _unnegated_hits("Ask them to type the command.") != []


def test_guidance_is_specific_per_kind():
    """Identical guidance means the classification bought nothing."""
    texts = [t for k, t in ck.GUIDANCE.items() if k != ck.UNKNOWN and t]
    assert len(set(texts)) == len(texts), "two kinds share the same guidance"


def test_classification_is_stable_for_clear_titles():
    cases = [
        ("Installing dbt on macOS", "TOOLING"),
        ("How the DAG is built from refs", "MECHANISM"),
        ("Debugging a failed model run", "DEBUGGING"),
    ]
    for title, expected in cases:
        got = ck.classify(title, "", None)
        assert got == expected, f"{title!r} -> {got}, expected {expected}"


def test_an_opaque_title_is_unknown_rather_than_guessed():
    """A wrong kind teaches the concept the wrong way. Unknown is safer."""
    assert ck.classify("Using defer", "", None) == ck.UNKNOWN


def test_unknown_never_carries_guidance():
    """UNKNOWN must fall through to generic teaching, not to a default kind."""
    assert not ck.GUIDANCE.get(ck.UNKNOWN)


def test_classify_survives_junk_input():
    for bad in (None, "", "   ", "\x00\x01", "?" * 500):
        ck.classify(bad, "", None)      # must not raise
