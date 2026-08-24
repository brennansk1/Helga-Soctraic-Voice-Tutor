"""Mathematics: what this domain needs that computer science does not.

Implements the `services.domains.registry` contract. Nothing here is shared
with the CS domain and nothing should be: `SYNTAX` versus `MECHANISM` is a real
distinction about code and meaningless about mathematics, where the distinction
that decides how a thing is taught is DEFINITION versus THEOREM versus
NOTATION.

WHY THERE IS NO `SHAPE` OVERRIDE
--------------------------------
The CS domain widens `SCHOOL_SHAPE` because documentation topics are genuinely
uneven — dbt ships 66 pages on models and 2 on installation, and forcing those
into one band either splits a topic or pads a thin one.

Mathematics is the case `SCHOOL_SHAPE` was calibrated FOR. It is taught on a
calendar, its chapters are deliberately sized to weeks by people who teach it,
and OpenStax Calculus Volume 1 is 9 chapters of comparable weight. Widening the
bands here would let the model produce a lopsided course and call it faithful
to the subject. So this domain declines the override — which is the registry
contract working: `SHAPE` is optional, and absence means the shared default.
"""
import logging

logger = logging.getLogger(__name__)

from services.domains.mathematics.concept_kind import (  # noqa: F401,E402
    classify, rank, guidance, prompt_line,
    DEFINITION, NOTATION, THEOREM, PROOF, PROCEDURE, REPRESENTATION,
    APPLICATION, ESTIMATION, MISCONCEPTION, UNKNOWN, AIDED_KINDS_ORDER,
)
from services.domains.mathematics.teaching_moves import (  # noqa: F401
    from_examples, best_move, choose_move, prompt_block as pair_block,
    ERROR_HUNT, WORKED_STEP, COMPARE, PREDICT,
)
from services.domains.mathematics.mathml import (  # noqa: F401
    to_latex, replace_math,
)
from services.domains.mathematics.worked_examples import (  # noqa: F401
    examples_in_text, notes_in_text, attach_to_course,
)
from services.domains.mathematics.concept_classifier import (  # noqa: F401
    classify_course as classify_concepts,
)
from services.domains.mathematics.openstax import (  # noqa: F401
    book_for, syllabus, parse_book_html, NOT_COVERED,
)

#: Names this extension answers to, for logging and provenance on a course.
DOMAIN = "mathematics"
LABEL = "Mathematics"

#: Subjects this domain claims. Owned HERE, not in the registry, so adding a
#: domain never means editing shared code.
#:
#: Short entries are matched on word boundaries by the registry — the CS domain
#: learned that the hard way when a bare "api" inside "therapist" routed a
#: therapy course to computer science. The same trap exists here and is worse:
#: a bare "set" would match "sunset" and "mean" is ordinary English, so neither
#: is listed.
KEYWORDS = (
    "mathematics", "math", "maths", "algebra", "calculus", "geometry",
    "trigonometry", "precalculus", "statistics", "probability",
    "linear algebra", "differential equations", "discrete mathematics",
    "number theory", "real analysis", "topology", "arithmetic",
    "prealgebra", "quantitative reasoning", "mathematical",
    # TOPIC-LEVEL TERMS, not just subject names.
    #
    # Measured on realistic personal-use topics: "the pythagorean theorem",
    # "quadratic equations" and "derivatives and integrals" all routed to
    # (generic) and got NO mathematics teaching, because the list named
    # SUBJECTS and a learner types a TOPIC. Eight of sixteen realistic topics
    # missed across all four domains.
    #
    # Short entries are matched on word boundaries, so "theorem" cannot fire
    # inside another word and "matrix" does not catch "matrices" — hence both.
    "theorem", "pythagorean", "derivative", "derivatives", "integral",
    "integrals", "quadratic", "polynomial", "logarithm", "exponent",
    "matrix", "matrices", "vector", "eigenvalue", "factorisation",
    "factorization", "fractions", "equations", "inequalities", "sequences",
    "limits", "differentiation", "integration", "proof",
)


def source_for(subject, doc_resolver=None, **_):
    """Where this mathematics subject's material should come from.

    Returns `(kind, pages, meta)` — the registry's optional source hook, in the
    shape `book_skeleton` already consumes from the computer-science domain.

      "TEXTBOOK"   -> a LibreTexts book was found and its pages are returned.
      "researched" -> no edited book at the right level. Named explicitly
                      rather than silently substituting a wrong-level one: the
                      generic relevance matcher answers "linear algebra" with
                      *Algebra 1*, a high-school text for a university subject,
                      and a course built from that is wrong in a way no
                      structural check would catch.

    WHY THIS CHANGED
    ----------------
    It used to return `("openstax", titles, {"content": "local copy
    required"})` — the right book, NAMED but unreadable, because OpenStax
    `robots.txt` disallows `/contents` and `/apps/archive` for every agent.
    Nothing downstream could act on a title, so `worked_examples` fell through
    to `lesson["source_text"]`, a key nothing in production ever wrote, and the
    whole mining layer sat behind a source that could not arrive.

    LibreTexts republishes the same edited textbooks — Calculus (OpenStax),
    Precalculus 2e, Abstract Algebra (Judson), Trench's Differential Equations
    — on a host whose robots.txt permits reading content pages. So the OpenStax
    titles are still what this domain WANTS; they are now also reachable.

    TWO SIGNATURE BUGS FIXED HERE
    -----------------------------
    `book_skeleton` calls this as `source_for(subject, doc_resolver=...)`. The
    old signature took `subject` only, so the call raised TypeError into that
    site's `except Exception`, was logged as "domain source lookup failed", and
    the domain silently supplied nothing. `**_` keeps that from recurring if
    the caller grows another keyword.
    """
    from services.research import libretexts as lt
    try:
        # NOT hardcoded to "math". LibreTexts keeps statistics in its own
        # library, and forcing `lib="math"` answered "statistics" with *Math
        # For Liberal Art Students*. This domain still CLAIMS the subject; the
        # library follows the subject.
        lib = lt.library_for(subject) or "math"
        pages, meta = lt.pages_for(subject, lib=lib)
    except Exception as e:
        logger.warning(f"[MATHS] LibreTexts lookup failed for {subject!r}: {e}")
        pages, meta = [], {}
    if pages:
        # The OpenStax titles this domain would have named anyway, recorded so
        # provenance still shows what the ideal source was.
        meta = dict(meta, preferred_titles=list(book_for(subject) or ()))
        return "TEXTBOOK", pages, meta
    titles = book_for(subject)
    return "researched", [], {
        "reason": ("no readable edited book found"
                   if titles else "no book at this level"),
        "preferred_titles": list(titles or ()),
    }
