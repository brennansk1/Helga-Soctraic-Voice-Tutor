"""Attaching a predict/observe pair to each science concept at BUILD time.

WHY BUILD TIME
--------------
Mining at teaching time means reopening the book mid-turn on a machine where
turn latency is already the acute defect. Every domain here mines once, during
the build, and attaches the result to the concept.

THE FIELD NAME IS `teaching_pair`
---------------------------------
Not `teaching_move`, not anything else. `fsm_logic._domain_teaching` reads
`teaching_pair`; all three earlier domains write `teaching_pair`. Writing
anything else attaches material the tutor never reads — a defect this
repository has now hit eleven times, once in the mathematics domain in a file
sitting beside the document describing the pattern.
`tests/domains/test_domain_reaches_the_tutor.py` fails any domain that writes a
`teach*` field the FSM does not read.

WHAT GETS ATTACHED, AND TO WHAT
-------------------------------
Only kinds that can USE a pair. `CLASSIFICATION` cannot — a taxonomy has
nothing to predict, and inventing a prediction for one would produce exactly
the fake-inquiry turn this domain exists to avoid.

Alternatives are stored alongside the default so the tutor can choose by
learner behaviour at teaching time, which is the mathematics domain's
`choose_move` contract: picking material from the concept alone is a script by
construction, since the same concept then produces the same turn whoever is
sitting there.
"""
import logging
import re

logger = logging.getLogger(__name__)

from services.domains.science import teaching_moves as tm  # noqa: E402
from services.domains.science.concept_kind import (  # noqa: E402
    AIDED_KINDS_ORDER, UNKNOWN, CLASSIFICATION,
)

_TITLE_WORD = re.compile(r"[A-Za-z]{4,}")

#: Words in half the titles of any science course, carrying no matching signal.
_STOP = {"science", "scientific", "introduction", "overview", "chapter",
         "unit", "basic", "basics", "fundamentals", "principles", "concepts",
         "study", "understanding", "the", "and", "for", "with", "from"}

#: WHICH MOVE SUITS WHICH KIND. Consulted BEFORE word overlap.
#:
#: The history domain learned by measurement that word matching alone swaps
#: concepts — a timeline concept took a historiography move because a
#: historian's quoted position mentioned "July". A concept's KIND states what
#: it NEEDS; its vocabulary only says what it MENTIONS.
_KIND_WANTS = {
    "MISCONCEPTION": (tm.PREDICT_OBSERVE,),
    "OBSERVATION": (tm.PREDICT_OBSERVE,),
    "LAW": (tm.PREDICT_OBSERVE, tm.UNITS_CHECK),
    "MECHANISM": (tm.PREDICT_OBSERVE, tm.EVIDENCE_CHECK),
    "MODEL": (tm.PREDICT_OBSERVE, tm.EVIDENCE_CHECK),
    "EXPERIMENT": (tm.EVIDENCE_CHECK, tm.PREDICT_OBSERVE),
    "QUANTITY": (tm.UNITS_CHECK, tm.PREDICT_OBSERVE),
}


def _best_for(concept, moves):
    """The move that is ABOUT this concept, or the next available."""
    if not moves:
        return None
    wants = _KIND_WANTS.get(concept.get("concept_kind") or "")
    if wants:
        for want in wants:
            for m in moves:
                if m.get("kind") == want:
                    return m
    words = {w.lower() for w in _TITLE_WORD.findall(concept.get("title") or "")}
    words -= _STOP
    if words:
        best, score = None, 0
        for m in moves:
            blob = ((m.get("first") or "") + " "
                    + (m.get("second") or "")).lower()
            hits = sum(1 for w in words if w in blob)
            if hits > score:
                best, score = m, hits
        if best is not None:
            return best
    return moves[0]


def attach_to_course(course, book, status_callback=None):
    """Attach a mined predict/observe pair to each aided concept.

    The registry contract's optional `attach_to_course` hook. Mutates `course`
    in place and returns a tally. Never raises: an asset failure must cost the
    asset, not the build.
    """
    tally = {"moves": 0, "poe": 0, "units": 0, "evidence": 0, "skipped": 0,
             "chapters": 0}
    aided = set(AIDED_KINDS_ORDER)
    lessons = [l for m in (course.get("modules") or [])
               for u in (m.get("units") or [])
               for l in (u.get("lessons") or [])]

    seen = set()
    for i, lesson in enumerate(lessons, 1):
        text = ""
        try:
            order = lesson.get("book_chapter")
            chapter = book.chapter(order) if (book and order is not None) else None
            text = getattr(chapter, "text", "") or ""
        except Exception:
            text = ""
        if not text:
            text = lesson.get("source_text") or ""
        if not text:
            continue

        tally["chapters"] += 1
        try:
            moves = tm.from_text(text)
        except Exception as e:
            logger.warning(f"[SCI] mining failed for "
                           f"{lesson.get('title','')!r}: {e}")
            continue
        if not moves:
            continue

        for concept in (lesson.get("concepts") or []):
            kind = concept.get("concept_kind") or UNKNOWN
            if kind == CLASSIFICATION or kind not in aided:
                # A taxonomy has nothing to predict. Manufacturing one would
                # produce the fake-inquiry turn this domain exists to avoid.
                tally["skipped"] += 1
                continue

            move = _best_for(concept, moves)
            if not move:
                tally["skipped"] += 1
                continue

            fingerprint = (move.get("first", "")[:120],
                           move.get("second", "")[:120])
            if fingerprint in seen:
                tally["skipped"] += 1
                continue
            seen.add(fingerprint)

            alts = [m for m in moves
                    if m is not move and m.get("kind") != move.get("kind")][:2]
            concept["teaching_pair"] = {
                "kind": move["kind"],
                "first": (move.get("first") or "")[:900],
                "second": (move.get("second") or "")[:900],
                "misconception": bool(move.get("misconception")),
                "alternatives": [
                    {"kind": m["kind"],
                     "first": (m.get("first") or "")[:900],
                     "second": (m.get("second") or "")[:900]}
                    for m in alts],
            }
            tally["moves"] += 1
            if move["kind"] == tm.PREDICT_OBSERVE:
                tally["poe"] += 1
            elif move["kind"] == tm.UNITS_CHECK:
                tally["units"] += 1
            elif move["kind"] == tm.EVIDENCE_CHECK:
                tally["evidence"] += 1
            moves = [m for m in moves if m is not move]

        if status_callback and i % 5 == 0:
            try:
                status_callback(f"SCI:MOVES:{i}:{len(lessons)}")
            except Exception:
                pass

    logger.info(f"[SCI] attached {tally['moves']} move(s): {tally['poe']} POE, "
                f"{tally['units']} units, {tally['evidence']} evidence, "
                f"skipped {tally['skipped']}")
    return tally
