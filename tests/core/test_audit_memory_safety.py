"""Stage 4 must not be the thing that pushes the learner into swap.

This runs at the end of a build, beside Ollama holding 13.5 GB of builder, on
a machine measured at 13% free with 8 GB of swap already in use. Reading a
model back under load measured 84 MB/s here, so anything that forces a reload
of the builder costs minutes of a learner's time.

The split is deliberate: the deterministic tiers are arithmetic over text,
cost ~0.3s on a 95-concept course, and always run. The truth tier calls a model
holding roughly 3 GB and is batch work, so it is exactly what `allow_background`
exists to hold back — and it DEGRADES rather than waits, because truth is
advisory and blocking a finished build behind memory headroom is not a trade
worth making for an advisory number.
"""
import pytest

from services.core import course_builder


def test_the_truth_tier_is_skipped_under_pressure(monkeypatch):
    """And the reason survives, so the report says why rather than going quiet."""
    from services.common import memory_guard

    monkeypatch.setattr(memory_guard, "allow_background", lambda *a, **k: False)
    monkeypatch.setattr(memory_guard, "pressure_reason",
                        lambda *a, **k: "kernel reports warn")

    # Watch the VERIFIER specifically. The other ledger checks legitimately
    # use the connection under pressure — they are arithmetic over rows.
    consulted = []
    from services.core import claim_verifier
    monkeypatch.setattr(claim_verifier, "get_any_verifier",
                        lambda *a, **k: consulted.append(1))

    class _Storage:
        class courses:
            @staticmethod
            def get_concept_content(*a, **k):
                return None
    h = course_builder.ContentHydrator.__new__(course_builder.ContentHydrator)
    h.storage = _Storage()
    h.audit_enabled = True
    h.truth_check_enabled = True
    h.mastery_level = 3
    h.topic_domain = "computer_science"
    h.status_callback = None
    h._ledger_conn = lambda: _FakeConn([])

    report = h._run_audit("course_x", {"title": "T", "modules": []})
    truth = report["ledger"]["truth"]

    assert truth["checked"] is False
    assert "memory pressure" in truth["reason"]
    assert "warn" in truth["reason"], "the kernel's reason was dropped"
    assert not consulted, "the verifier was loaded under memory pressure"


def test_the_deterministic_tiers_still_run_under_pressure(monkeypatch):
    """They cost no model and no meaningful memory. Skipping them would make a
    build report less about itself for no saving."""
    from services.common import memory_guard
    monkeypatch.setattr(memory_guard, "allow_background", lambda *a, **k: False)
    monkeypatch.setattr(memory_guard, "pressure_reason", lambda *a, **k: "warn")

    class _Storage:
        class courses:
            @staticmethod
            def get_concept_content(*a, **k):
                return None
    h = course_builder.ContentHydrator.__new__(course_builder.ContentHydrator)
    h.storage = _Storage()
    h.audit_enabled = True
    h.truth_check_enabled = True
    h.mastery_level = 3
    h.topic_domain = ""
    h.status_callback = None
    h._ledger_conn = lambda: None

    report = h._run_audit("course_x", {"title": "T", "modules": []})
    assert report["ran"] is True
    assert "checks_run" in report
    assert report["verdict"], "no verdict was produced"


def test_a_skipped_truth_tier_is_not_a_pass(monkeypatch):
    """`ledger_not_run` must carry it, because unchecked is not clean."""
    from services.common import memory_guard
    monkeypatch.setattr(memory_guard, "allow_background", lambda *a, **k: False)
    monkeypatch.setattr(memory_guard, "pressure_reason", lambda *a, **k: "warn")

    class _Storage:
        class courses:
            @staticmethod
            def get_concept_content(*a, **k):
                return None
    h = course_builder.ContentHydrator.__new__(course_builder.ContentHydrator)
    h.storage = _Storage()
    h.audit_enabled = True
    h.truth_check_enabled = True
    h.mastery_level = 3
    h.topic_domain = ""
    h.status_callback = None
    h._ledger_conn = lambda: _FakeConn([])

    report = h._run_audit("course_x", {"title": "T", "modules": []})
    assert "truth" in report["ledger_not_run"]
    assert "truth" not in report["ledger_failed"]


class _FakeConn:
    """A connection whose every query returns nothing, recording any use."""

    def __init__(self, log):
        self._log = log

    def execute(self, *a, **k):
        self._log.append(a[0] if a else "")
        return []

    def cursor(self):
        return self
