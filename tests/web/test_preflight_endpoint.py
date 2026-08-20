"""GET /api/system/preflight — the endpoint the startup gate is driven by.

The gate holds the whole app shut on a `blocked` verdict, so the two things
worth pinning here are that a real shortfall reaches the browser intact, and
that every way this endpoint can fail to measure comes back NAMED rather than
as a clean-looking pass. A preflight that reports green because it could not
run is worse than no preflight — it sends you looking somewhere else.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
sys.path.insert(0, os.path.join(_root, "services/web-ui"))
if _root not in sys.path:
    sys.path.insert(0, _root)

import app as web_app  # noqa: E402
from services.common import startup_preflight as pf  # noqa: E402


def _core_payload(total_gb=24.0, available_gb=9.0, free_bytes=200 * 2 ** 30):
    return {
        "memory": {"total_gb": total_gb, "available_gb": available_gb,
                   "pressure_level": 1, "reason": None, "source": "psutil"},
        "storage": {"disk": {"free_bytes": free_bytes}},
        "hardware": {"platform": "macOS-15.3-arm64"},
    }


def _ollama_ok(readings, _timeout):
    readings["ollama_reachable"] = True
    readings["model_installed"] = True
    readings["model_weights_gb"] = 12.74
    readings["model_resident_now"] = True


class TestPreflightEndpoint(unittest.TestCase):
    def setUp(self):
        web_app.app.config["TESTING"] = True
        self.client = web_app.app.test_client()
        # The real probe would reach for Ollama over the network; every case
        # here is about the verdict, not about this machine's Ollama.
        self.probe = patch.object(pf, "_probe_ollama", _ollama_ok)
        self.probe.start()
        self.addCleanup(self.probe.stop)
        # `scope` decides whether memory is judged at all, and it must be the
        # host reading in these tests regardless of where pytest is running.
        self.scope = patch.object(pf, "_in_container", return_value=False)
        self.scope.start()
        self.addCleanup(self.scope.stop)

    def _get(self, core_response):
        with patch.object(web_app.requests, "get", core_response):
            r = self.client.get("/api/system/preflight")
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def _core_returns(self, payload):
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = payload
        return MagicMock(return_value=resp)

    def test_healthy_machine_is_ok(self):
        v = self._get(self._core_returns(_core_payload()))
        self.assertEqual(v["state"], "ok")
        self.assertEqual(v["blocking"], [])
        self.assertEqual(len(v["checks"]), 4)

    def test_a_machine_without_the_memory_reaches_the_browser_blocked(self):
        v = self._get(self._core_returns(_core_payload(total_gb=16.0)))
        self.assertEqual(v["state"], "blocked")
        self.assertIn("installed_memory", v["blocking"])
        check = next(c for c in v["checks"] if c["id"] == "installed_memory")
        self.assertIn("16.0 GB", check["reason"])
        self.assertTrue(check["remedy"])

    def test_unreachable_core_says_so_instead_of_passing(self):
        v = self._get(MagicMock(side_effect=OSError("connection refused")))
        self.assertTrue(any("did not answer" in n for n in v["notes"]),
                        v["notes"])
        self.assertIn(v["state"], ("ok", "degraded", "blocked"))

    def test_missing_module_is_reported_by_name_not_as_a_pass(self):
        """The web-ui image is built from services/web-ui alone, so
        services/common is not always importable from here. That must surface
        as an unmeasured machine, never as a healthy one."""
        with patch.object(web_app, "_load_startup_preflight", return_value=None):
            with patch.object(web_app.app, "_preflight_reason",
                              "No module named 'services'", create=True):
                r = self.client.get("/api/system/preflight")
        v = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(v["state"], "degraded")
        self.assertEqual(v["blocking"], [])
        self.assertIn("startup_preflight", v["checks"][0]["reason"])

    def test_every_check_carries_what_the_ui_renders(self):
        v = self._get(self._core_returns(_core_payload()))
        for c in v["checks"]:
            for key in ("id", "label", "state", "reason", "remedy", "measured"):
                self.assertIn(key, c)
            self.assertIsInstance(c["reason"], str)


if __name__ == "__main__":
    unittest.main()
