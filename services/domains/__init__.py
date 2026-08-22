"""Per-domain teaching extensions.

WHY THIS PACKAGE EXISTS
-----------------------
Teaching computer science needs judgements that teaching history does not: what
KIND of knowledge a concept is (syntax is shown, mechanism is reasoned toward),
what order the kinds come in (you cannot debug before you can write), and what a
worked example looks like (a runnable code block with one token removed).

None of that generalises. A history course has no syntax; a chemistry course's
"worked example" is a stoichiometry calculation, not a `select` statement. Left
in `services/common/`, the CS answers would quietly become the defaults for
every subject, and the second domain added would have to unpick them.

So each domain owns its own module here, all implementing the same small
contract (see `registry.py`), and `services/common/` stays domain-neutral.
"""
