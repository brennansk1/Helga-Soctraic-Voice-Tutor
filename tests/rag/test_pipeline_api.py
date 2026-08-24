"""The pipeline surface: an outside model may author, never lower the bar.

WHAT THESE PIN
--------------
The point of the surface is that a strong model can write a course and the
local model can teach it — and that a learner cannot tell which wrote what,
because both were held to the same contract. Two things therefore have to stay
true, and both have already been broken once during development:

1. INJECTED CONTENT IS JUDGED. `validate_concept` returns a TUPLE
   `(ok, problems, details)`. An early version treated it as a list of
   problems, passed the tuple to `regeneration_hint`, raised, and swallowed
   the exception in a broad `except` — so validation reported "nothing wrong"
   and a 74-word body was accepted at a level requiring 320. The check was
   off and nothing said so.

2. HANDING BACK IS FREE. A part-authored course must report exactly which
   concepts have no body, because that list is what the local model fills.
"""
import json
import os
import sys
import tempfile

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _body(words=260, worked=True, source=True):
    text = "# T\n\n## Core Explanation\n" + ("a relational idea explained. " * (words // 4))
    if worked:
        text += ("\n\n## Worked Example\nConsider a table sales(id, amount). "
                 "Step by step: rows are read, then sorted.\n")
    if source:
        text += "\n## Sources\n- [PostgreSQL](https://www.postgresql.org/docs/current/) — documentation\n"
    return text


@pytest.fixture()
def client():
    tmp = tempfile.mkdtemp()
    os.environ["DATA_ROOT"] = tmp
    from services.common.storage import StorageManager
    from services.rag.pipeline_api import create_pipeline_blueprint
    from flask import Flask
    storage = StorageManager(tmp)
    app = Flask(__name__)
    app.register_blueprint(create_pipeline_blueprint(storage))
    app.config["TESTING"] = True
    c = app.test_client()
    c._storage = storage
    return c


def _make(client, mastery=2, bodies=None):
    payload = {
        "title": "SQL under test", "mastery": mastery,
        "teaching_domain": "computer_science", "model": "test-model",
        "modules": [{"title": "M1", "concepts": [
            {"uid": "con_a", "title": "A", "content": (bodies or {}).get("con_a")},
            {"uid": "con_b", "title": "B", "content": (bodies or {}).get("con_b")},
        ]}],
    }
    return client.post("/api/pipeline/course", json=payload)


class TestOneShot:
    def test_a_whole_course_lands_in_one_request(self, client):
        r = _make(client, bodies={"con_a": _body(), "con_b": _body()})
        assert r.status_code == 201, r.get_json()
        d = r.get_json()
        assert d["concepts_total"] == 2 and d["concepts_written"] == 2
        assert d["status"] == "ready"

    def test_concepts_without_a_body_are_named_not_hidden(self, client):
        """The missing list IS the handback instruction."""
        r = _make(client, bodies={"con_a": _body()})
        d = r.get_json()
        assert d["status"] == "partial"
        assert d["missing"] == ["con_b"]
        assert "resume_url" in d

    def test_it_never_claims_the_verdicts_it_did_not_run(self, client):
        d = _make(client, bodies={"con_a": _body(), "con_b": _body()}).get_json()
        assert d["verdicts_pending"] is True


class TestTheBarIsTheSameBar:
    def test_a_thin_body_is_refused_with_its_problems(self, client):
        """The regression that mattered: this silently passed once."""
        uid = _make(client, bodies={"con_a": _body()}).get_json()["course_uid"]
        # Above the 40-word crash floor, below the contract: the case that
        # silently passed once, and the one a real author actually hits.
        r = client.put(f"/api/pipeline/course/{uid}/concept/con_b",
                       json={"content": _body(words=100, worked=False,
                                              source=False)})
        assert r.status_code == 400
        d = r.get_json()
        assert d["ok"] is False
        assert d["problems"], "refused with no reason is not actionable"
        assert d["contract"]["word_min"] > 0

    def test_a_compliant_body_is_accepted(self, client):
        uid = _make(client, bodies={"con_a": _body()}).get_json()["course_uid"]
        r = client.put(f"/api/pipeline/course/{uid}/concept/con_b",
                       json={"content": _body()})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["ok"] is True

    def test_the_contract_is_published_before_writing(self, client):
        r = client.get("/api/pipeline/contract?mastery=3&domain=computer_science")
        d = r.get_json()
        assert d["depth_contract"]["word_min"] >= 200
        assert "worked_example" in d["depth_contract"]["required"]
        assert d["writing_standard"]["target_words"]
        assert d["enforced"] is True

    def test_below_contract_is_storable_only_when_asked_for(self, client):
        uid = _make(client, bodies={"con_a": _body()}).get_json()["course_uid"]
        r = client.put(f"/api/pipeline/course/{uid}/concept/con_b",
                       json={"content": _body(words=100, worked=False,
                                              source=False),
                             "allow_below_contract": True})
        assert r.status_code == 200
        assert r.get_json().get("below_contract") is True


class TestVisibility:
    def test_state_is_reported_per_concept_with_its_author(self, client):
        uid = _make(client, bodies={"con_a": _body()}).get_json()["course_uid"]
        d = client.get(f"/api/pipeline/course/{uid}").get_json()
        assert d["counts"] == {"concepts": 2, "with_content": 1, "thin": 0,
                               "missing": 1}
        by = {c["uid"]: c for c in d["concepts"]}
        assert by["con_a"]["has_content"] and by["con_a"]["written_by"] == "test-model"
        assert not by["con_b"]["has_content"]

    def test_a_concept_read_carries_the_bar_it_must_meet(self, client):
        uid = _make(client, bodies={"con_a": _body()}).get_json()["course_uid"]
        d = client.get(f"/api/pipeline/course/{uid}/concept/con_a").get_json()
        assert d["must_meet"]["depth_contract"]["word_min"] > 0
        assert d["must_meet"]["writing_standard"]["vocabulary"]


class TestFinalize:
    def test_status_follows_the_verdict_not_the_count(self, client):
        """Every concept having a body is not the same as the course being
        ready, and finalize must not confuse the two."""
        uid = _make(client, bodies={"con_a": _body(), "con_b": _body()}).get_json()["course_uid"]
        client.put(f"/api/pipeline/course/{uid}/concept/con_b",
                   json={"content": _body(words=100, worked=False,
                                          source=False),
                         "allow_below_contract": True})
        d = client.post(f"/api/pipeline/course/{uid}/finalize").get_json()
        assert d["status"] == "partial"
        assert d["below_contract"] == 1
        assert d["failures"][0]["problems"]
