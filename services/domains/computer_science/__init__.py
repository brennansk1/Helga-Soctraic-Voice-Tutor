"""Computer science: what this domain needs that others do not.

Implements the `services.domains.registry` contract. Everything here is
CS-specific ON PURPOSE — syntax-versus-mechanism, code examples with a removed
token, an ordering that puts tooling before practice. A history or chemistry
extension will answer the same three questions with entirely different content,
which is exactly why these answers do not live in `services/common/`.
"""
from services.domains.computer_science.concept_kind import (  # noqa: F401
    classify, rank, guidance, prompt_line,
    ORIENTATION, TOOLING, SYNTAX, PROCEDURE, MECHANISM, DEBUGGING,
    CONVENTION, REFERENCE, UNKNOWN, CODE_KINDS_ORDER,
)
from services.domains.computer_science.code_examples import (  # noqa: F401
    example_for, attach_to_course, blocks_in, choose_blank,
)
# NAMED `concept_classifier`, NOT `classify`. Importing a submodule binds it
# as an attribute of the package, so a module called `classify.py` SHADOWED the
# `classify` function this domain must expose under the registry contract —
# `ext.classify(...)` returned a module object. `contract_report` still passed
# it, because `hasattr` was satisfied by the wrong thing.
from services.domains.computer_science.concept_classifier import (  # noqa: F401
    classify_course as classify_concepts,
)
from services.domains.computer_science.code_pairs import (  # noqa: F401
    best_pair, pairs_in, prompt_block as pair_block,
)
from services.domains.computer_science.devdocs import (  # noqa: F401
    classify_subject, pages_for as devdocs_pages, find as devdocs_find,
    TECHNOLOGY, TOOL, CONCEPT,
)


def source_for(subject, doc_resolver=None):
    """Where this CS subject's material should come from.

    The registry contract's optional hook for "which pipeline does this subject
    need". Returns (kind, pages, meta):

      TECHNOLOGY -> DevDocs has it; pages are returned, no crawl needed
      TOOL       -> not in DevDocs but a real doc site exists; caller crawls
      CONCEPT    -> neither; caller must use the RESEARCHED path, because a
                    concept like recursion has no authoritative site and
                    building from the first blog that ranks is how a course
                    ends up teaching one person's opinion as fact
    """
    kind = classify_subject(subject, doc_resolver=doc_resolver)
    if kind == TECHNOLOGY:
        pages, meta = devdocs_pages(subject)
        if pages:
            return kind, pages, meta
        # In the manifest but unfetchable — fall back rather than fail.
        return TOOL, [], {}
    return kind, [], {}

#: Names this extension answers to, for logging and provenance on a course.
DOMAIN = "computer_science"
LABEL = "Computer Science"


#: COURSE SHAPE FOR PROGRAMMING SUBJECTS.
#
# `tools.structure_quality.SCHOOL_SHAPE` is a school timetable — its own
# comments say "a module is ~2-4 WEEKS", "a week is ~3 CLASS SESSIONS", "a
# 50-MINUTE session". That calibration is right for a subject taught on a
# calendar and wrong for one taught from documentation, where topic sizes are
# genuinely uneven: dbt's docs carry 66 pages on building models and 2 on
# installation. Forcing those into the same band either splits a coherent topic
# or pads a thin one, and padding is the exact failure `check_filler` exists to
# catch.
#
# So the CEILING is widened. The FLOOR is not, and deliberately: SCHOOL_SHAPE's
# docstring has the principle right — "a module of one unit is a week wearing a
# module's name, and that is collapse rather than variation". The measured dbt
# failure (1 module, 36 units, ~1.1 lessons per unit) breaks the floor AND the
# ceiling, so it stays rejected under these bands too.
#
# `concepts_per_lesson` is UNCHANGED. That band is cognitive load, not
# timetable: a learner holds 2-4 new ideas at once regardless of subject.
SHAPE = {
    "units_per_module": (2, 8),      # a big capability earns more units
    "lessons_per_unit": (2, 8),      # reference-dense topics run longer
    "concepts_per_lesson": (2, 4),   # cognitive load — unchanged
}

#: Modules in a programming course are legitimately uneven — "Models" is bigger
#: than "Installation" and pretending otherwise distorts the subject. So
#: balance is checked far more loosely than for a timetabled course.
ALLOW_UNEVEN_MODULES = True


#: Subjects this domain claims. Owned HERE, not in the registry, so adding a
#: domain never means editing shared code. Short entries are matched on word
#: boundaries — bare "api" inside "therapist" routed a therapy course to
#: computer science.
KEYWORDS = (
    "programming", "coding", "software", "computer science", "algorithm",
    "data structure", "devops", "backend", "frontend", "web development",
    "python", "javascript", "typescript", "java", "rust", "golang", "sql",
    "dbt", "django", "react", "kubernetes", "docker", "terraform", "airflow",
    "pandas", "database", "git", "api",
)
