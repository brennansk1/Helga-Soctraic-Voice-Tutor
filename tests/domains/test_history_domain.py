"""History: the domain where the failure is ASKING, not telling.

Computer science must not make a learner type code; mathematics must not make
them solve. History's constraint points the other way — much of its content
cannot be reasoned to at all, and the failure is asking:

    You can elicit WHY the July Crisis escalated.
    You cannot elicit that Hastings was 14 October 1066.

`honest_telling`, the dimension that scores exactly this, is 2.20 for history.

And the domain's own dimension, `contested_interpretation`, penalises TWO
OPPOSITE failures — flattening a live debate into consensus, and inventing a
controversy where historians broadly agree. A module that hedges everything
scores no better than one that settles everything, which is the reason for the
two-named-historians rule below.
"""
import re

from services.domains.history import concept_kind as hk
from services.domains.history import teaching_moves as tm

CHAPTER = """
The origins of the war remain disputed.

Fischer argued that Germany deliberately sought a continental war in 1914,
pointing to the September Programme as evidence of long-held aims. Taylor
contended instead that the powers stumbled into conflict through mobilisation
timetables that none of them could halt once begun.

Source A
Telegram from the German Chancellor to the ambassador in Vienna, 6 July 1914.
Austria must judge what is to be done to clear up her relations with Serbia;
but whatever her decision, she can count with certainty upon it that Germany
will stand behind her as an ally.

Source B
Diary of a Russian foreign ministry official, written in St Petersburg,
25 July 1914. We were told the Austrian note was unacceptable by design. The
mood in the ministry was that mobilisation could not be delayed without losing
every advantage we still held.

Source C
An unattributed fragment carrying no provenance whatsoever, long enough to pass
a length check but impossible to source, which is the whole point of it.
"""


# ------------------------------------------------------------- kinds

def test_a_date_is_a_FACT_and_outranks_everything():
    """A concept that is both 'a date' and 'about causation' must be taught as
    the date — every other kind's guidance invites reasoning, and reasoning to
    a contingent fact is impossible."""
    assert hk.classify("The date of the Battle of Hastings", "", None) == hk.FACT
    assert hk.rank(hk.FACT) == 0


def test_the_bench_topics_classify_as_their_derivable_flag_implies():
    """The benchmark marks Hastings derivable=False and the other two True.
    The kinds must agree, or the domain teaches against its own instrument."""
    assert hk.classify("The date of the Battle of Hastings", "", None) == hk.FACT
    assert hk.classify("The sequence of the July Crisis", "", None) == hk.CHRONOLOGY
    assert hk.classify("The causes of the First World War", "", None) == hk.CAUSATION


def test_clear_titles_classify():
    for title, expect in [
        ("Why historians disagree about appeasement", hk.CONTESTED),
        ("Reading a soldier's diary from the Somme", hk.SOURCE),
        ("The significance of the Norman Conquest", hk.SIGNIFICANCE),
        ("Continuity and change in Tudor government", hk.CONTINUITY),
        ("The myth that the assassination alone caused the war",
         hk.MISCONCEPTION),
    ]:
        assert hk.classify(title, "", None) == expect, title


def test_an_opaque_title_stays_unknown():
    assert hk.classify("Working through the material", "", None) == hk.UNKNOWN


def test_junk_never_raises():
    for bad in (None, "", "   ", "\x00", "?" * 400):
        hk.classify(bad, "", None)


# --------------------------------------------------- the standing rules

_ASKS_TO_GUESS = re.compile(
    r"\b(ask (them|the (learner|student)) to (guess|recall|remember|name the "
    r"(date|year))|what year (was|did)|can you (recall|remember))", re.I)
_NEGATED = re.compile(
    r"(never|not|avoid|rather than|instead of|don'?t|do not)[^.]{0,40}$", re.I)


def test_no_kind_asks_the_learner_to_guess_a_fact():
    offenders = []
    for kind, text in hk.GUIDANCE.items():
        for m in _ASKS_TO_GUESS.finditer(text or ""):
            if _NEGATED.search(text[max(0, m.start() - 60):m.start()]):
                continue
            offenders.append(f"{kind}: {m.group(0)!r}")
    assert not offenders, offenders


def test_the_standing_rule_covers_BOTH_failures():
    """A rule against only one of them pushes the tutor into the other."""
    assert "guess a contingent fact" in hk.NEVER_QUIZ
    assert "do not manufacture a" in hk.NEVER_QUIZ
    assert "as settled" in hk.NEVER_QUIZ


def test_the_standing_rule_rides_every_turn_including_unknown():
    for kind in list(hk.RANK) + [None]:
        assert hk.NEVER_QUIZ in hk.prompt_line(kind)


def test_the_FACT_guidance_says_tell_it():
    text = hk.guidance(hk.FACT)
    assert "TELL IT" in text
    assert "do not ask the learner to guess" in text.lower()


# ------------------------------------------------------------- mining

def test_a_source_without_provenance_is_refused():
    """Sourcing is a question ABOUT the attribution; without one there is
    nothing to ask, and an extract alone is a quotation."""
    labels = [s["label"] for s in tm.sources_in_text(CHAPTER)]
    assert labels == ["A", "B"], labels


def test_named_historians_are_found():
    assert [h["historian"] for h in tm.historians_in_text(CHAPTER)] == \
        ["Fischer", "Taylor"]


def test_a_hedge_is_not_evidence_of_a_live_debate():
    """"Some historians argue" appears just as readily in front of a settled
    question. Inventing controversy scores as badly as flattening it."""
    hedged = ("Some historians argue that the harvest failed. Many scholars "
              "suggest the same thing, at similar length, in similar words.")
    assert tm.historians_in_text(hedged) == []
    assert not [m for m in tm.from_text(hedged)
                if m["kind"] == tm.HISTORIOGRAPHY]


def test_two_named_positions_produce_a_historiography_move():
    moves = tm.from_text(CHAPTER)
    top = moves[0]
    assert top["kind"] == tm.HISTORIOGRAPHY
    assert "Fischer" in top["first"] and "Taylor" in top["second"]


def test_two_sources_produce_a_corroboration_move():
    assert any(m["kind"] == tm.CORROBORATE for m in tm.from_text(CHAPTER))


def test_the_historiography_block_refuses_to_resolve_the_debate():
    block = tm.prompt_block(tm.from_text(CHAPTER)[0])
    assert "do NOT resolve it" in block or "Do NOT resolve it" in block
    assert "which is correct" in block


def test_the_source_block_asks_about_the_author_not_the_events():
    src = [m for m in tm.from_text(CHAPTER) if m["kind"] == tm.SOURCE_CHECK][0]
    block = tm.prompt_block(src)
    assert "about the AUTHOR rather than the events" in block
    assert "needs no outside knowledge" in block


def test_behaviour_selects_different_material():
    moves = tm.from_text(CHAPTER)
    assert tm.best_move(moves, behaviour="AHEAD")["kind"] == tm.HISTORIOGRAPHY
    assert tm.best_move(moves, behaviour="BLUFFING")["kind"] == tm.SOURCE_CHECK


def test_mining_junk_never_raises():
    for bad in (None, "", "Source", "Source A", "x" * 5000):
        tm.from_text(bad)
        tm.sources_in_text(bad)
        tm.historians_in_text(bad)


def test_prompt_block_of_none_is_empty():
    assert tm.prompt_block(None) == ""
    assert tm.prompt_block("nonsense") == ""


def test_the_FACT_guidance_requires_correcting_a_misstated_fact():
    """Measured on the history benchmark, Hastings topic: a student said the
    battle was 1065 and the tutor let it stand.

    A wrong date left uncorrected is the worst outcome available on a FACT
    concept — worse than never raising it — because the learner leaves more
    confident and wrong. The original guidance said only "TELL IT" and said
    nothing about what to do when the learner supplies a wrong version.
    """
    text = hk.guidance(hk.FACT)
    assert "CORRECT THEM PLAINLY AND AT ONCE" in text


def test_the_FACT_guidance_says_the_fact_is_settled():
    """`contested_interpretation` penalises inventing controversy as hard as
    flattening it, and it is scored on FACT topics too — the Hastings topic
    averaged 1.80 of 5.

    A learner who cannot tell which parts of history are settled and which are
    argued over has not understood the subject, so saying briefly that a date
    is not in dispute is part of teaching it honestly.
    """
    text = hk.guidance(hk.FACT)
    assert "SAY THAT IT IS SETTLED" in text
    assert "Do not manufacture a debate" in text


def test_the_FACT_guidance_still_forbids_asking():
    """The additions must not weaken the original constraint."""
    text = hk.guidance(hk.FACT)
    assert "TELL IT" in text
    assert "do not ask the learner to guess" in text.lower()


# ---------------------------------------------------- kind drives the match

def test_material_is_matched_by_KIND_before_vocabulary():
    """MEASURED on a real build. "Timeline of July Crisis" (CHRONOLOGY) took
    the HISTORIOGRAPHY move because Albertini's position mentions "the last
    week of JULY" — and by the time the actual CONTESTED concept was reached
    that move was gone, so the debate concept got a source extract and the
    timeline concept got a debate.

    A concept's KIND states what it needs; its vocabulary only says what it
    mentions.
    """
    from services.domains.history import source_mining as sm

    class _Ch:
        def __init__(self):
            self.order, self.text = 1, CHAPTER

    class _Bk:
        def chapter(self, order):
            return _Ch() if order == 1 else None

    course = {"modules": [{"units": [{"lessons": [{
        "title": "L", "book_chapter": 1,
        "concepts": [
            # deliberately ordered so vocabulary alone would mis-assign
            {"title": "Timeline of the July Crisis", "concept_kind": hk.CHRONOLOGY},
            {"title": "Debates on responsibility", "concept_kind": hk.CONTESTED},
            {"title": "Reading the telegram", "concept_kind": hk.SOURCE},
        ],
    }]}]}]}
    sm.attach_to_course(course, _Bk())
    got = {c["title"]: (c.get("teaching_pair") or {}).get("kind")
           for c in course["modules"][0]["units"][0]["lessons"][0]["concepts"]}
    assert got["Debates on responsibility"] == tm.HISTORIOGRAPHY, got
    assert got["Reading the telegram"] == tm.SOURCE_CHECK, got
    assert got["Timeline of the July Crisis"] != tm.HISTORIOGRAPHY, got


def test_a_FACT_concept_is_given_no_material_at_all():
    """A date needs stating. A source exercise on it invites the
    reasoning-toward-a-fact this domain forbids outright."""
    from services.domains.history import source_mining as sm

    class _Ch:
        def __init__(self):
            self.order, self.text = 1, CHAPTER

    class _Bk:
        def chapter(self, order):
            return _Ch() if order == 1 else None

    course = {"modules": [{"units": [{"lessons": [{
        "title": "L", "book_chapter": 1,
        "concepts": [{"title": "The date of Hastings", "concept_kind": hk.FACT}],
    }]}]}]}
    tally = sm.attach_to_course(course, _Bk())
    concept = course["modules"][0]["units"][0]["lessons"][0]["concepts"][0]
    assert concept.get("teaching_pair") is None
    assert tally["skipped"] == 1


def test_a_memorandum_counts_as_provenance():
    """Measured: Source A of a fixture chapter — "Memorandum from the German
    Chancellor to the Foreign Office, Berlin, 1912" — was refused for having
    no provenance, which is exactly the attribution the move interrogates.
    Memoranda are a staple of the period this domain is most asked about."""
    for line in ("Memorandum from the Chancellor to the Foreign Office, 1912.",
                 "Minute by the Permanent Under-Secretary, London, 1914.",
                 "Communique issued by the Foreign Ministry, Vienna, 1914."):
        assert tm._PROVENANCE.search(line), line
    assert not tm._PROVENANCE.search(
        "An unattributed fragment with nothing identifying it at all.")
