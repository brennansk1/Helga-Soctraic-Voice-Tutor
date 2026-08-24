"""Science: what this domain needs that the other three do not.

Implements the `services.domains.registry` contract. Fourth domain, after
computer science, mathematics and history.

THE CONSTRAINT
--------------
    computer science   do not ask them to type code
    mathematics        do not ask them to produce a solved answer
    history            do not ask them to guess a contingent fact
    science            DO NOT ASK FOR AN OBSERVATION THEY CANNOT MAKE

And unlike the other three, the rule opens a door rather than only closing one:
the learner PREDICTS, the tutor SUPPLIES the observation, the learner explains
the gap. Predict–Observe–Explain is both the best-evidenced conceptual-change
strategy in science education and, under this project's constraint, ideal —
predicting commits the learner to a consequence of what they already believe
and asks them to compute nothing.

See `concept_kind` for Johnstone's triangle and why the kinds encode it.

WHY NO `SHAPE` OVERRIDE
-----------------------
Same reason as mathematics and history. Science is taught on a calendar, its
chapters are sized to weeks by people who teach it, and `SCHOOL_SHAPE` was
calibrated for exactly that. Computer science widens the bands because
documentation topics are genuinely uneven; a physics syllabus is not.
"""
import logging

logger = logging.getLogger(__name__)

from services.domains.science.concept_kind import (  # noqa: F401,E402
    classify, rank, guidance, prompt_line, level_of, NEVER_DEMAND_OBSERVATION,
    OBSERVATION, QUANTITY, LAW, MECHANISM, MODEL, REPRESENTATION, EXPERIMENT,
    CLASSIFICATION, MISCONCEPTION, UNKNOWN, AIDED_KINDS_ORDER, LEVEL,
)
from services.domains.science.teaching_moves import (  # noqa: F401,E402
    from_text, best_move, choose_move, prompt_block as pair_block,
    poe_in_text, units_in_text, evidence_in_text,
    PREDICT_OBSERVE, LEVEL_BRIDGE, UNITS_CHECK, EVIDENCE_CHECK,
)
from services.domains.science.concept_classifier import (  # noqa: F401,E402
    classify_course as classify_concepts,
)
from services.domains.science.source_mining import (  # noqa: F401,E402
    attach_to_course,
)

#: Names this extension answers to, for logging and provenance on a course.
DOMAIN = "science"
LABEL = "Science"

#: Subjects this domain claims.
#:
#: Matched on WORD BOUNDARIES by the registry, and all three earlier domains
#: were bitten by not doing that: a bare "api" inside "therapist" routed a
#: therapy course to computer science, and substring matching sent
#: "Precalculus" to a calculus textbook.
#:
#: The traps here are specific and worse, because they cross INTO other
#: domains: "cell" sits inside "Excel" and inside spreadsheet lessons;
#: "organic" is ordinary English; "force" appears in "workforce" and in
#: "brute force"; "energy" appears in economics and policy courses. Every one
#: of those is listed only inside a longer phrase.
KEYWORDS = (
    "physics", "chemistry", "biology", "science", "scientific",
    "astronomy", "geology", "earth science", "physical science",
    "life science", "natural science", "thermodynamics", "mechanics",
    "electromagnetism", "optics", "quantum", "relativity",
    "organic chemistry", "inorganic chemistry", "biochemistry",
    "physical chemistry", "analytical chemistry", "stoichiometry",
    "genetics", "evolution", "ecology", "microbiology", "anatomy",
    "physiology", "botany", "zoology", "cell biology", "molecular biology",
    "neuroscience", "immunology", "photosynthesis", "periodic table",
    "newtonian", "kinematics", "electricity and magnetism",
    # TOPIC-LEVEL TERMS. Same measurement: "newtons laws of motion" and
    # "cell division" both routed to (generic).
    #
    # The traps this module's docstring names are handled by the registry
    # matching a single word at a LEADING word boundary — "cell" cannot fire
    # inside "Excel", "force" cannot fire inside "workforce".
    #
    # That was not true when these were added. The rule was keyed on keyword
    # LENGTH (boundary under five characters, substring otherwise), so "force"
    # substring-matched and "Managing your workforce" routed here. The comment
    # that replaced this one asserted it was "verified, not assumed" and it was
    # assumed; the test that now covers it is what found the difference.
    # "force" and "forces" are NOT here, and that is the module docstring
    # being right. They are ordinary English — brute force, force of habit,
    # police force, sales force — and a bare "force" routed "Brute force
    # negotiation tactics" to science. Physics reaches this domain through
    # "newton's laws", "momentum", "friction" and the LLM matcher; ambiguous
    # English words are not worth the courses they mis-teach.
    "cell", "cells", "atom", "atoms", "molecule", "molecules", "gravity",
    "momentum", "friction", "velocity", "acceleration",
    "mitosis", "meiosis", "dna", "rna", "gene", "genes", "protein",
    "enzyme", "electron", "proton", "neutron", "isotope", "reaction",
    "newtons laws", "newton's laws", "natural selection", "ecosystem",
    "circuit", "voltage", "current", "magnetism", "wavelength", "entropy",
)


def source_for(subject, doc_resolver=None, **_):
    """Where this science subject's material should come from.

    Returns `(kind, pages, meta)` — the registry's optional source hook, in the
    shape `book_skeleton` already consumes from the other domains.

    LibreTexts keeps a SEPARATE LIBRARY PER SCIENCE — `bio`, `chem`, `phys` —
    so the library is chosen from the subject rather than hardcoded. That
    matters more here than in any earlier domain: this one domain spans three
    libraries, and forcing a single one would answer "organic chemistry" from
    the physics shelf.

    `**_` is not decoration. `book_skeleton` calls this with `doc_resolver=`,
    and the mathematics domain's original signature took `subject` only — so
    the call raised TypeError into that site's `except Exception`, was logged
    as "domain source lookup failed", and the domain silently supplied nothing.
    """
    from services.research import libretexts as lt
    try:
        lib = lt.library_for(subject)
        if lib not in ("bio", "chem", "phys", "stats", "eng"):
            # `library_for` scores across every library and can land on `math`
            # for a physics subject full of equations. Fall back to a direct
            # read of the subject rather than accepting a wrong shelf.
            lib = _library_from_subject(subject) or lib or "phys"
        pages, meta = lt.pages_for(subject, lib=lib)
    except Exception as e:
        logger.warning(f"[SCI] LibreTexts lookup failed for {subject!r}: {e}")
        pages, meta = [], {}
    if pages:
        return "TEXTBOOK", pages, meta
    return "researched", [], {"reason": "no readable science textbook found"}


_LIBRARY_WORDS = (
    ("chem", ("chemistry", "chemical", "stoichiometry", "organic",
              "inorganic", "biochem", "periodic table", "reaction",
              "molecule", "bonding", "acid", "base", "titration")),
    ("bio", ("biology", "genetic", "ecolog", "cell", "evolution", "anatomy",
             "physiolog", "microbiolog", "botany", "zoolog", "organism",
             "photosynthesis", "enzyme", "dna", "neuroscience", "immunolog")),
    ("phys", ("physics", "mechanic", "thermodynamic", "electromagnet",
              "optic", "quantum", "relativity", "kinematic", "newton",
              "astronomy", "circuit", "wave", "force", "momentum", "energy")),
)


def _library_from_subject(subject):
    """Which science library, read straight off the subject. None if unclear."""
    s = (subject or "").lower()
    best, score = None, 0
    for lib, words in _LIBRARY_WORDS:
        hit = sum(len(w) for w in words if w in s)
        if hit > score:
            best, score = lib, hit
    return best
