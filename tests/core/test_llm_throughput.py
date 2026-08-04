"""Throughput and residency: the two things that decide how long a user waits.

Measured on a 12-concept mastery-4 build (tools/llm_profile.py): 70 LLM calls,
~35,500 generated tokens, and decode is 93% of the wall clock. So build time is
(tokens to generate) ÷ (tokens per second × how many stream at once), and the
last factor was pinned at 1 by a gate default while the server could do four.

The tutoring side is decided by a different number: whether the weights are
still in memory when the student finally answers.
"""

import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

for _m in ("kuzu", "psutil", "yaml", "sentence_transformers", "torch"):
    sys.modules.setdefault(_m, MagicMock())

from services.core.gpu_gate import (          # noqa: E402
    GpuGate, LLMContext, INTERACTIVE, BACKGROUND,
)


class TestBackgroundConcurrency(unittest.TestCase):
    """Course building may use every slot but one."""

    def test_background_gets_every_slot_but_one(self):
        """This defaulted to 1, and the hydrator sizes its thread pool from it,
        so a build ran one concept at a time against a four-way server."""
        self.assertEqual(GpuGate(num_parallel=4).bg_slots, 3)
        self.assertEqual(GpuGate(num_parallel=8).bg_slots, 7)

    def test_a_single_slot_server_still_works(self):
        self.assertEqual(GpuGate(num_parallel=1).bg_slots, 1)

    def test_two_slots_leaves_one_for_the_student(self):
        self.assertEqual(GpuGate(num_parallel=2).bg_slots, 1)

    def test_the_old_behaviour_is_one_env_var_away(self):
        with patch.dict(os.environ, {"HELGA_BG_SLOTS": "1"}):
            self.assertEqual(GpuGate(num_parallel=4).bg_slots, 1)

    def test_explicit_argument_still_wins(self):
        self.assertEqual(GpuGate(num_parallel=4, bg_slots=2).bg_slots, 2)


class TestStudentIsNeverStarved(unittest.TestCase):
    """The property the raised default depends on.

    Background may hold at most cap-1 slots, so an arriving interactive turn
    always finds one free and is dispatched immediately — it never queues
    behind a 30-second background generation.
    """

    def test_background_cannot_take_the_last_slot(self):
        gate = GpuGate(num_parallel=4)
        held = [gate.admit(LLMContext(klass=BACKGROUND)) for _ in range(3)]
        self.assertEqual(gate.bg_slots, 3)

        start = time.monotonic()
        slot = gate.admit(LLMContext(klass=INTERACTIVE, student_id="s1"),
                          timeout=2.0)
        self.assertLess(time.monotonic() - start, 0.5,
                        "a student waited behind a course build")
        slot.release()
        for h in held:
            h.release()

    def test_a_fourth_background_call_waits_rather_than_taking_it(self):
        gate = GpuGate(num_parallel=4)
        held = [gate.admit(LLMContext(klass=BACKGROUND)) for _ in range(3)]
        got = threading.Event()

        def extra():
            try:
                gate.admit(LLMContext(klass=BACKGROUND), timeout=0.4).release()
                got.set()
            except Exception:
                pass

        t = threading.Thread(target=extra, daemon=True)
        t.start()
        t.join(1.5)
        self.assertFalse(got.is_set(),
                         "background exceeded its share and could strand a student")
        for h in held:
            h.release()

    def test_background_still_yields_to_a_waiting_student(self):
        """Beyond the reserved slot, a freed slot goes to the student first."""
        gate = GpuGate(num_parallel=2)          # 1 background, 1 reserved
        bg = gate.admit(LLMContext(klass=BACKGROUND))
        interactive = gate.admit(LLMContext(klass=INTERACTIVE, student_id="s1"))
        order = []

        def want(klass, sid):
            try:
                gate.admit(LLMContext(klass=klass, student_id=sid),
                           timeout=3.0).release()
                order.append(klass)
            except Exception:
                pass

        waiters = [threading.Thread(target=want, args=(BACKGROUND, "_bg"), daemon=True),
                   threading.Thread(target=want, args=(INTERACTIVE, "s2"), daemon=True)]
        for w in waiters:
            w.start()
        time.sleep(0.2)
        bg.release()
        interactive.release()
        for w in waiters:
            w.join(3.5)
        self.assertTrue(order, "nobody was admitted")
        self.assertEqual(order[0], INTERACTIVE, "background jumped the student")


class TestKeepAlive(unittest.TestCase):
    """Ollama unloads after five idle minutes by default, and a student
    reading a question and thinking about it routinely exceeds that."""

    def _payload(self, capture, **kw):
        from services.core.llm_client import LLMClient

        class _R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"choices": [{"message": {"content": "hi"}}]}

        def post(url, json=None, timeout=None, **_kw):    # noqa: A002
            capture.append(json)
            return _R()

        import requests
        with patch.object(requests, "post", post):
            LLMClient().chat("sys", "user", retries=1, **kw)
        return capture[-1]

    def test_chat_asks_the_server_to_keep_the_model_resident(self):
        self.assertIn("keep_alive", self._payload([]))

    def test_the_host_setting_wins_when_present(self):
        with patch.dict(os.environ, {"OLLAMA_KEEP_ALIVE": "30m"}):
            import importlib
            from services.core import llm_client as mod
            importlib.reload(mod)
            try:
                self.assertEqual(mod.KEEP_ALIVE, "30m")
            finally:
                importlib.reload(mod)

    def test_llm_utils_sends_it_too(self):
        """A build runs dozens of calls with research fetches between them; the
        model can idle out mid-pipeline."""
        import requests
        from services.common import llm_utils
        seen = []

        class _R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {"choices": [{"message": {"content": "text"},
                                     "finish_reason": "stop"}]}

        def post(url, json=None, timeout=None, **_kw):    # noqa: A002
            seen.append(json)
            return _R()

        with patch.object(requests, "post", post):
            llm_utils.llm_generate("prompt", sys_prompt="sys", max_tokens=10)
        self.assertTrue(seen)
        self.assertIn("keep_alive", seen[0])


class TestResidencyProbe(unittest.TestCase):
    """`health_check` reads /api/tags, which lists INSTALLED models — it says
    nothing about whether the weights are in memory. /api/ps is the one that
    knows, and the difference is a multi-second cold load on the next answer."""

    def _probe(self, body, status=200):
        import requests
        from services.core.llm_client import LLMClient

        class _R:
            ok = status == 200
            status_code = status
            def json(self): return body

        with patch.object(requests, "get", lambda *a, **k: _R()):
            return LLMClient(model="qwen3.5:9b").residency()

    def test_reports_a_model_that_is_not_loaded(self):
        self.assertEqual(self._probe({"models": []})["loaded"], False)

    def test_a_far_future_expiry_reads_as_pinned(self):
        from datetime import datetime, timedelta, timezone
        when = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
        state = self._probe({"models": [{"name": "qwen3.5:9b", "expires_at": when}]})
        self.assertTrue(state["loaded"])
        self.assertTrue(state["pinned"])

    def test_a_five_minute_expiry_reads_as_not_pinned(self):
        """The default. Every answer after a pause pays a model load."""
        from datetime import datetime, timedelta, timezone
        when = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        state = self._probe({"models": [{"name": "qwen3.5:9b", "expires_at": when}]})
        self.assertFalse(state["pinned"])

    def test_a_malformed_expiry_is_unknown_not_false(self):
        """Reporting 'not pinned' on a parse failure would train the user to
        ignore a warning that is sometimes wrong."""
        state = self._probe({"models": [{"name": "qwen3.5:9b",
                                         "expires_at": "not-a-date"}]})
        self.assertIsNone(state["pinned"])

    def test_an_unreachable_server_is_diagnostics_not_an_exception(self):
        import requests
        from services.core.llm_client import LLMClient

        def boom(*a, **k):
            raise requests.exceptions.ConnectionError("refused")

        with patch.object(requests, "get", boom):
            self.assertIn("error", LLMClient().residency())


if __name__ == "__main__":
    unittest.main()
