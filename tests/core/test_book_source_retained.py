"""A book course must retain the chapter it was written from.

The book path reads a chapter, hands it to the model as `content_to_use`, and
the concept is written from it — that structural link is the whole difference
between a course built FROM a book and a course ABOUT one.

It was also the one source never stored. `sources` held only what web research
returned, so a book-built concept had no retained text at all, and the audit's
truth check could not verify a single claim against the very chapter it came
from. The most authoritative material in the pipeline was the only material
with no record.
"""
import re


def test_the_book_passage_is_added_to_the_evidence_list():
    """Read from the source, because exercising the whole hydrate() path needs
    a book, a model and a network. What must be true is that the branch which
    selects a book passage also retains it."""
    import inspect

    from services.core import course_builder
    src = inspect.getsource(course_builder.ContentHydrator.hydrate)

    # The branch that uses an uploaded passage as the material.
    assert "content_to_use = user_excerpt" in src
    # ...must also record it, or nothing downstream can check against it.
    branch = src[src.index("content_to_use = user_excerpt"):]
    branch = branch[:2000]
    assert "research_evidence" in branch, \
        "the book passage is used as material and never retained as a source"
    assert '"passage": user_excerpt' in branch, \
        "the evidence row carries no text, which is what made this useless"


def test_it_is_stored_as_evidence_not_as_a_citation():
    """A chapter has no URL. Rendering "[Chapter 3]()" to a learner is a broken
    link, so it is retained for CHECKING and never rendered as a reference."""
    import inspect

    from services.core import course_builder
    src = inspect.getsource(course_builder.ContentHydrator.hydrate)
    branch = src[src.index("content_to_use = user_excerpt"):][:2000]
    assert '"cited": False' in branch


def test_retain_sources_does_not_require_a_url():
    """The insert must accept a source with no URL, or the book row is dropped
    silently at the last step."""
    import inspect

    from services.core import course_builder
    src = inspect.getsource(course_builder.ContentHydrator._retain_sources)
    loop = src[src.index("for s in list(research_sources"):]
    # The only skip in the loop is a non-dict; a missing url must not skip.
    assert "if not isinstance(s, dict)" in loop
    assert not re.search(r"if not s\.get\(\"url\"\)", loop), \
        "a source without a URL is dropped, which is every book chapter"
