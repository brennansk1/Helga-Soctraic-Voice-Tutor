"""A concept hit has to say which course it is in.

`/search` returned {uid, title, text, type} for concepts, where `uid` is the
CONCEPT. The header search then built `/learn?course_uid=<that concept uid>`,
naming a course that does not exist, and the learn page bounced the learner
back to /courses. Every concept result was a dead link — which is most results,
since the endpoint only falls through to concepts when no course title matched.

The storage layer had always returned `course_uid` on the row; the response
dropped it. And learn.html already deep-links `?course_uid=&concept_uid=`, so
the destination was built and only the link to it was wrong.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


def test_the_endpoint_returns_the_owning_course():
    src = _read("services", "rag", "librarian.py")
    # Both the FTS path and the substring fallback build concept results.
    blocks = [m.start() for m in re.finditer(r'"type": "Concept"', src)]
    assert len(blocks) >= 2, "expected an FTS result and a fallback result"
    for i in blocks:
        window = src[max(0, i - 700):i]
        assert "course_uid" in window, (
            "a concept result is built without its course_uid — the link "
            "built from it cannot work")


def test_the_storage_row_already_had_it():
    """So this was a dropped field, not a missing capability."""
    src = _read("services", "common", "storage.py")
    i = src.find("def search(self, query")
    assert i > 0
    assert "course_uid" in src[i:i + 1200]


def test_the_front_end_uses_it_for_concepts():
    js = _read("services", "web-ui", "static", "js", "search.js")
    i = js.find("function buildResultItem")
    assert i > 0
    body = js[i:i + 1800]
    assert "r.course_uid" in body, "the link is still built from r.uid alone"
    assert "concept_uid=" in body, \
        "the concept is not passed, so the learner lands on the path view"


def test_the_learn_page_accepts_that_deep_link():
    """The half this fix depends on: if learn.html stopped reading
    concept_uid, the link would be valid and still land nowhere useful."""
    html = _read("services", "web-ui", "templates", "learn.html")
    assert "urlParams.get('concept_uid')" in html
    assert "urlParams.get('course_uid')" in html
