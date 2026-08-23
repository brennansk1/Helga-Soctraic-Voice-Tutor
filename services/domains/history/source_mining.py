"""Attaching history's teaching material to concepts at BUILD time.

WHY BUILD TIME
--------------
A doc- or book-sourced lesson stores a chapter reference, not the chapter, so
mining at teaching time means reopening the book mid-turn on a machine where
turn latency is already the acute defect. Every domain here mines once, during
the build, and attaches the result to the concept.

THE FIELD NAME IS `teaching_pair`
---------------------------------
Not `teaching_move`, not anything else. `fsm_logic._domain_teaching` reads
`teaching_pair`; both existing domains write `teaching_pair`. Writing anything
else attaches material the tutor never reads — the defect this repository has
now hit nine times, most recently in the mathematics domain, in a file sitting
beside the document describing the pattern.

WHAT GETS ATTACHED, AND TO WHAT
-------------------------------
Only kinds that can USE material. A `FACT` concept — the date of Hastings —
needs stating, not a source exercise; giving it one would invite exactly the
reasoning-toward-a-date that this domain forbids. `CONTEXT` is prose.

Alternatives are stored alongside the default so the tutor can choose at
teaching time by learner behaviour, which is the mathematics domain's
`choose_move` contract generalised: choosing material from the concept alone is
a script by construction, since the same concept then produces the same turn
whoever is sitting there.
"""
import logging
import re

logger = logging.getLogger(__name__)

from services.domains.history import teaching_moves as tm  # noqa: E402
from services.domains.history.concept_kind import (  # noqa: E402
    AIDED_KINDS_ORDER, UNKNOWN, FACT,
)

_TITLE_WORD = re.compile(r"[A-Za-z]{4,}")

#: Words in half the titles of any history course, carrying no matching signal.
_STOP = {"history", "historical", "the", "and", "for", "with", "from",
         "about", "century", "period", "events", "event", "understanding",
         "introduction", "overview", "study", "reading", "sources", "source"}


#: WHICH MOVE SUITS WHICH KIND. Consulted BEFORE word overlap.
#:
#: Word matching alone swapped two concepts on a real build: "Timeline of July
#: Crisis" (CHRONOLOGY) took the HISTORIOGRAPHY move because Albertini's
#: position mentions "the last week of JULY", and by the time the actual
#: CONTESTED concept was reached that move was gone — so the debate concept
#: got a source extract and the timeline concept got a debate.
#:
#: A concept's KIND states what it needs. Vocabulary only says what it mentions.
_KIND_WANTS = {
    "CONTESTED": (tm.HISTORIOGRAPHY, tm.CORROBORATE),
    "CAUSATION": (tm.COUNTERFACTUAL, tm.HISTORIOGRAPHY),
    "SIGNIFICANCE": (tm.HISTORIOGRAPHY, tm.CORROBORATE),
    "SOURCE": (tm.SOURCE_CHECK, tm.CORROBORATE),
    "MISCONCEPTION": (tm.SOURCE_CHECK, tm.CORROBORATE),
    "CHRONOLOGY": (tm.CORROBORATE, tm.SOURCE_CHECK),
}


def _best_for(concept, moves):
    """The move that is ABOUT this concept, or the next available.

    KIND FIRST, then title vocabulary. The mathematics domain learned by
    measurement that popping moves in order gives every concept its
    NEIGHBOUR's material; this domain then learned that word overlap alone is
    not enough either, because a concept can MENTION what another concept IS.
    """
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


#: How many primary documents one course may fetch. Each is a search plus a
#: revision fetch against a rate-limited host, so this is a build-time cost cap,
#: not a quality judgement.
MAX_DOCUMENTS = 12


def _document_move(concept, documents_fn, budget):
    """A SOURCE_CHECK built from a real primary document, or None.

    WHY THIS IS NEEDED AT ALL
    -------------------------
    `sources_in_text` recovers a labelled source block from chapter prose. That
    works on the hand-built fixtures it was written against and does not work
    on a real textbook. Measured on U.S. History (American YAWP), 14 pages
    sampled across the whole book, 69,065 characters:

        labelled source blocks ("Source A: ...")   0
        historian attributions ("<Name> argues")   0

    A narrative survey does not print labelled primary sources, so on any real
    history book this domain's central move had nothing to attach. Loosening
    the regex until something matched would manufacture sources out of ordinary
    narration, which is the failure `contested_interpretation` punishes.

    So the document comes from an archive that publishes documents WITH their
    attribution, and the provenance is read from a header field rather than
    guessed from prose.
    """
    if budget <= 0 or documents_fn is None:
        return None
    title = (concept.get("title") or "").strip()
    if not title:
        return None
    try:
        docs = documents_fn(title, limit=1)
    except Exception as e:
        logger.info(f"[HIST] document lookup failed for {title!r}: {e}")
        return None
    if not docs:
        return None
    d = docs[0]
    return {
        "kind": tm.SOURCE_CHECK,
        # The attribution goes FIRST because sourcing is a question about it —
        # Wineburg's finding is that historians read the attribution before the
        # document and students read it last, if at all.
        "first": f"{d['provenance']}. {(d.get('notes') or '').strip()}".strip(),
        "second": (d.get("text") or "")[:900],
        "source_url": d.get("url", ""),
    }


def attach_to_course(course, book, status_callback=None, documents_fn=None):
    """Attach a mined source or historiographical debate to each aided concept.

    The registry contract's optional `attach_to_course` hook. Mutates `course`
    in place and returns a tally. Never raises: an asset failure must cost the
    asset, not the build.

    `documents_fn` is the primary-document lookup, defaulting to Wikisource and
    injectable so tests need no network. It is consulted only where the BOOK
    yields nothing — the textbook is still the first source, and the archive
    covers what a narrative survey structurally cannot supply.
    """
    if documents_fn is None:
        try:
            from services.research.wikisource import documents as documents_fn
        except Exception as e:                   # pragma: no cover - defensive
            logger.info(f"[HIST] wikisource unavailable: {e}")
            documents_fn = None

    tally = {"moves": 0, "sources": 0, "debates": 0, "skipped": 0,
             "chapters": 0, "documents": 0}
    doc_budget = MAX_DOCUMENTS
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

        moves = []
        if text:
            tally["chapters"] += 1
            try:
                moves = tm.from_text(text)
            except Exception as e:
                logger.warning(f"[HIST] mining failed for "
                               f"{lesson.get('title','')!r}: {e}")
                moves = []

        # NO `continue` HERE, AND THAT IS THE POINT. This used to skip the
        # whole lesson when the book yielded no moves — which, measured on a
        # real narrative survey, is every lesson. The concepts were therefore
        # never reached, so the archive fallback below could never have fired
        # and the domain's central move was unreachable on any real book.
        for concept in (lesson.get("concepts") or []):
            kind = concept.get("concept_kind") or UNKNOWN
            if kind == FACT:
                # A date needs stating. Attaching a source exercise to it
                # invites reasoning toward a contingent fact, which is the one
                # thing this domain forbids outright.
                tally["skipped"] += 1
                continue
            if kind not in aided:
                tally["skipped"] += 1
                continue

            move = _best_for(concept, moves)
            from_archive = False
            if not move:
                # The book had nothing for this concept. Ask the archive for a
                # primary document instead — see `_document_move`.
                move = _document_move(concept, documents_fn, doc_budget)
                if move:
                    from_archive = True
                    doc_budget -= 1
                    tally["documents"] += 1
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
                "alternatives": [
                    {"kind": m["kind"],
                     "first": (m.get("first") or "")[:900],
                     "second": (m.get("second") or "")[:900]}
                    for m in alts],
            }
            # Provenance for a document the learner may want to read whole.
            if move.get("source_url"):
                concept["teaching_pair"]["source_url"] = move["source_url"]
            tally["moves"] += 1
            if move["kind"] == tm.HISTORIOGRAPHY:
                tally["debates"] += 1
            elif move["kind"] in (tm.SOURCE_CHECK, tm.CORROBORATE):
                tally["sources"] += 1
            # Only book-mined moves are consumed from the pool. An archive
            # document was never in it, and removing by identity would silently
            # drop nothing while reading as if it had.
            if not from_archive:
                moves = [m for m in moves if m is not move]

        if status_callback and i % 5 == 0:
            try:
                status_callback(f"HIST:SOURCES:{i}:{len(lessons)}")
            except Exception:
                pass

    logger.info(f"[HIST] attached {tally['moves']} move(s): "
                f"{tally['debates']} debate(s), {tally['sources']} source(s), "
                f"{tally['documents']} from the archive, "
                f"skipped {tally['skipped']}")
    return tally
