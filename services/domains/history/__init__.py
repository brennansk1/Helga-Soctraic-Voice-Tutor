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
from services.domains.history.concept_kind import (  # noqa: F401
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
