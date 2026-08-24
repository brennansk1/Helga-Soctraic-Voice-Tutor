"""An external author is not bound to the presets' shape.

The preset counts (modules, units per module, concepts per lesson) exist to
size a LOCAL build, which has to guess how much a subject can carry before it
has written any of it. A caller holding the whole curriculum in one context
knows that already, and forcing it to pad a thin module to a target — or split
a genuinely large one — makes the course worse, not more uniform.

So: nothing about the posted shape may be refused, truncated or padded.
"""
import os
import tempfile

import pytest


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


def _shape(course):
    return [sum(len(l.get("concepts") or [])
                for u in m.get("units") or []
                for l in u.get("lessons") or [])
            for m in course.get("modules") or []]


def test_wildly_uneven_modules_are_kept_as_posted(client):
    modules = [
        # One concept. A real subject sometimes has a one-idea module.
        {"title": "Tiny", "concepts": [{"title": "Only Concept"}]},
        # Nine, flat, no units or lessons invented by the caller.
        {"title": "Huge", "concepts": [{"title": f"Big {i}"} for i in range(1, 10)]},
        # Nested and uneven inside.
        {"title": "Nested", "units": [{"title": "U", "lessons": [
            {"title": "L1", "concepts": [{"title": "N1"}, {"title": "N2"}]},
            {"title": "L2", "concepts": [{"title": "N3"}]},
        ]}]},
    ]
    r = client.post("/api/pipeline/course",
                    json={"title": "Irregular", "model": "test", "mastery": 2,
                          "modules": modules})
    assert r.status_code in (200, 201), r.get_json()
    uid = r.get_json()["course_uid"]
    assert r.get_json()["concepts_total"] == 13

    course = client._storage.courses.get_course(uid)
    assert _shape(course) == [1, 9, 3], (
        "the posted shape was altered — something is padding or truncating")
    assert len(course["modules"]) == 3


def test_a_single_module_course_is_accepted(client):
    r = client.post("/api/pipeline/course",
                    json={"title": "One Module", "model": "test", "mastery": 2,
                          "modules": [{"title": "All of it",
                                       "concepts": [{"title": "A"}, {"title": "B"}]}]})
    assert r.status_code in (200, 201), r.get_json()
    assert r.get_json()["concepts_total"] == 2


def test_the_freedom_is_stated_where_a_caller_will_read_it(client):
    """A capability nothing documents is one an external model will not use."""
    d = client.get("/api/pipeline").get_json()
    assert "structure" in d, "the self-description says nothing about shape"
    assert "FREE" in d["structure"].upper()
