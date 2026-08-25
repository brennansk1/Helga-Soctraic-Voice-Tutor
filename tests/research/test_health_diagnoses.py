"""A health endpoint that crashes diagnoses nothing.

/health called len(cache) unguarded, so when diskcache's own tables were
missing it returned a 500 HTML page — "Internal Server Error", no subsystem
named, no remedy — and Docker reported the container unhealthy while the
service was in fact answering research requests correctly.

The cache is a SPEED dependency, not a correctness one. Without it every
lookup is cold. That is degraded, not dead, and it has to be reported as
itself.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "services", "research"))


@pytest.fixture()
def rs():
    pytest.importorskip("flask")
    try:
        import research_server
    except Exception as e:  # the module reaches for /app data at import
        pytest.skip(f"research_server not importable here: {e}")
    return research_server


class _Boom:
    def __len__(self):
        raise Exception("no such table: Settings")


def test_an_unreadable_cache_is_degraded_not_a_stack_trace(rs, monkeypatch):
    monkeypatch.setattr(rs, "cache", _Boom())
    r = rs.app.test_client().get("/health")
    assert r.status_code == 503, "a crash would be 500 with no JSON at all"
    body = r.get_json()
    assert body["status"] == "degraded"
    assert "cache" in body, "the failing subsystem is not named"
    assert "restart" in body["cache"].lower(), "no remedy given"


def test_the_verdict_still_fails_when_search_is_down(rs, monkeypatch):
    """The existing contract: reporting a subsystem and ignoring it in the
    verdict is how this service hid for weeks."""
    monkeypatch.setattr(rs.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    r = rs.app.test_client().get("/health")
    assert r.status_code == 503
    assert r.get_json()["searxng_reachable"] is False
