"""History: what this domain needs that computer science and mathematics do not.

Implements the `services.domains.registry` contract. Nothing here is shared
with the other two domains, and the reason is sharper than it was for
mathematics: the OTHER domains' central constraint is that a learner must not
be asked to produce something nobody can check. History's is the reverse —
much of its content cannot be reasoned to AT ALL, and the failure is asking.

You can elicit why the July Crisis escalated. You cannot elicit that Hastings
was 14 October 1066, and asking is a quiz with the answer withheld.

WHY THERE IS NO `SHAPE` OVERRIDE
--------------------------------
Same reasoning as mathematics: history is taught on a calendar, its periods are
sized to terms by people who teach it, and `SCHOOL_SHAPE` was calibrated for
exactly that. The computer-science domain widens the bands because
documentation topics are genuinely uneven; a history syllabus is not.

WHAT THIS DOMAIN IS SCORED ON
-----------------------------
`contested_interpretation`, and it penalises two OPPOSITE failures: flattening
a live historiographical debate into one settled story, and inventing a
controversy where historians broadly agree. A module that hedges everything
scores no better than one that settles everything, which is why
`teaching_moves` requires TWO NAMED historians before it will call a question
live — a hedge ("some historians argue") appears just as readily in front of a
settled question and is not evidence of anything.
"""
import logging

logger = logging.getLogger(__name__)

from services.domains.history.concept_kind import (  # noqa: F401,E402
    classify, rank, guidance, prompt_line, NEVER_QUIZ,
    FACT, CHRONOLOGY, CONTESTED, CAUSATION, SOURCE, CONTEXT, SIGNIFICANCE,
    CONTINUITY, MISCONCEPTION, UNKNOWN, AIDED_KINDS_ORDER,
)
from services.domains.history.concept_classifier import (  # noqa: F401
    classify_course as classify_concepts,
)
from services.domains.history.source_mining import (  # noqa: F401
    attach_to_course,
)
from services.domains.history.teaching_moves import (  # noqa: F401
    from_text, best_move, choose_move, prompt_block as pair_block,
    sources_in_text, historians_in_text,
    SOURCE_CHECK, CORROBORATE, HISTORIOGRAPHY, COUNTERFACTUAL,
)

#: Names this extension answers to, for logging and provenance on a course.
DOMAIN = "history"
LABEL = "History"

#: Subjects this domain claims.
#:
#: Short entries are matched on WORD BOUNDARIES by the registry, and both
#: earlier domains were bitten by not doing that: a bare "api" inside
#: "therapist" routed a therapy course to computer science, and substring
#: matching sent "Precalculus" to a calculus textbook. The trap here is
#: "war" — it sits inside "warehouse", "warrant" and "software" — so it is
#: listed only inside longer phrases.
KEYWORDS = (
    "history", "historical", "historiography", "civilisation", "civilization",
    "ancient", "medieval", "renaissance", "revolution", "empire",
    "world war", "civil war", "cold war", "colonial", "reformation",
    "antiquity", "archaeology", "dynasty", "monarchy", "crusades",
)


def source_for(subject, doc_resolver=None, **_):
    """Where this history subject's material should come from.

    Returns `(kind, pages, meta)` — the registry's optional source hook, in the
    shape `book_skeleton` already consumes from the other two domains.

    WHY THIS EXISTS AT ALL
    ----------------------
    It did not, and that was the defect. `source_mining.attach_to_course`
    reads `lesson["book_chapter"]` or `lesson["source_text"]` and attaches
    nothing when both are absent — and with no `source_for`, this domain was
    never asked where a book might come from, so on any course not built from
    an uploaded file both were always absent. Every mined source extract and
    every historiographical debate this module can produce was unreachable
    except by upload.

    WHY LIBRETEXTS
    --------------
    Its History shelf carries edited university survey texts — U.S. History
    (American YAWP, 339 pages), World History I and II (OpenStax), Western
    Civilization (Brooks) — on a host whose robots.txt permits reading content
    pages. Narrative history of that kind is what `historians_in_text` needs:
    it looks for a NAMED historian taking a position, and a survey text
    attributes its interpretations where an encyclopaedia summary does not.

    WHAT THIS DOES NOT SOLVE
    ------------------------
    `SOURCE_CHECK` wants primary documents WITH provenance and refuses an
    extract that has none. A survey textbook quotes primary sources but does
    not reliably print their attribution in a form `_PROVENANCE` can read.
    Wikisource carries the documents themselves with author and date, and
    wiring it is the obvious next step — deliberately NOT claimed here,
    because it is not built.
    """
    from services.research import libretexts as lt
    try:
        # `shelf="History"` matters: the humanities library holds Art,
        # Literature and Philosophy on the same shelf tree, and unconstrained
        # this domain's own subjects selected art books — "US History"
        # returned *Art History II (Lumen)*.
        pages, meta = lt.pages_for(subject, lib="human", shelf="History")
    except Exception as e:
        logger.warning(f"[HIST] LibreTexts lookup failed for {subject!r}: {e}")
        pages, meta = [], {}
    if pages:
        return "TEXTBOOK", pages, meta
    return "researched", [], {"reason": "no readable edited history book found"}
