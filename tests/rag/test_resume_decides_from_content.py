"""Resume must decide from the content, not from the recorded status.

The status is a cached judgement about the content. It can be wrong — written
before the last write landed, or made stale by a concept being cleared, a
failed write, or a repair. When it is wrong in the direction of "ready", the
course is missing a concept and nothing will fix it: resume refused on status
alone and replied "nothing to resume" while a concept sat empty.

Worse, the handback above it reported "resuming" to its own caller on any
reply, including that refusal — so one layer said the work had started while
the other had declined to start it, and the learner's course stayed broken
with both APIs reporting success.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


def test_the_gate_counts_missing_content():
    src = _read("services", "rag", "librarian.py")
    i = src.find("def resume_build")
    assert i > 0
    body = src[i:i + 4000]
    assert "get_concept_content" in body, \
        "resume_build still decides purely from the status field"
    assert "if not missing" in body


def test_ready_is_not_a_refusal_when_something_is_missing():
    src = _read("services", "rag", "librarian.py")
    i = src.find("def resume_build")
    body = src[i:i + 4000]
    # The old shape: an unconditional early return on status == "ready".
    assert 'if status == "ready":\n        return jsonify' not in body, \
        "a ready course with a missing concept is refused again"
    assert "marked ready but" in body, \
        "resuming a ready-but-incomplete course should say so out loud"


def test_the_handback_does_not_claim_work_it_did_not_start():
    src = _read("services", "rag", "pipeline_api.py")
    i = src.find("def hand_back")
    assert i > 0
    body = src[i:i + 3000]
    assert "not_started" in body, \
        "hand_back still reports 'resuming' whatever the upstream said"
    assert "r.status_code != 202" in body
