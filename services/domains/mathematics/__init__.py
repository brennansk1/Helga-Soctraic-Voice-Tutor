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
from services.domains.mathematics.concept_kind import (  # noqa: F401
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
)


def source_for(subject):
    """Where this mathematics subject's material should come from.

    Returns (kind, titles, meta):

      "openstax"   -> OpenStax publishes a book at the right level. `titles`
                      are the candidates, best first. The CONTENT must come
                      from a local copy: OpenStax `robots.txt` disallows
                      /apps/archive and /contents, so the book is not crawled.
      "researched" -> OpenStax has no book at the right level. Named
                      explicitly rather than silently substituting a
                      wrong-level one: the generic relevance matcher answers
                      "linear algebra" with *Algebra 1*, a high-school text for
                      a university subject, and a course built from that is
                      wrong in a way no structural check would catch.
    """
    titles = book_for(subject)
    if titles:
        return "openstax", list(titles), {"content": "local copy required"}
    return "researched", [], {"reason": "no OpenStax book at this level"}
