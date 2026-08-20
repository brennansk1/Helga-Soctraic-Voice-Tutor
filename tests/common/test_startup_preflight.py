"""The startup preflight has to be right about two different failures.

"This machine has 16 GB and needs 21" and "this machine has 24 GB but 21 are in
use" are the same shortfall in gigabytes and opposite advice. Telling someone
to close Chrome when the memory is not installed wastes their afternoon; not
telling them when it is, wastes the ten seconds that would have fixed it. These
tests pin that distinction, and pin the promise that a preflight never takes
down the caller that was trying to protect itself.
"""

import os
import sys
import unittest
from unittest.mock import patch

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.common import startup_preflight as pf  # noqa: E402


def _readings(**kw):
    """A healthy reference machine: the measured appliance, at rest."""
    base = dict(
        total_gb=24.0, available_gb=8.0, pressure_level=1, pressure_reason=None,
        disk_free_gb=120.0, model="nail-35b-a3b",
        ollama_url="http://host.docker.internal:11434",
        ollama_reachable=True, model_installed=True, model_near_miss=None,
        model_weights_gb=12.74, model_resident_now=True,
        scope="host", platform="macOS-15.3-arm64", notes=[],
    )
    base.update(kw)
    return base


def _check(verdict, cid):
    return next(c for c in verdict["checks"] if c["id"] == cid)


class TestHealthyMachine(unittest.TestCase):
    def test_reference_appliance_is_ok(self):
        v = pf.evaluate(_readings())
        self.assertEqual(v["state"], pf.OK)
        self.assertEqual(v["blocking"], [])
        for c in v["checks"]:
            self.assertEqual(c["state"], pf.OK, c)

    def test_verdict_shape(self):
        v = pf.evaluate(_readings())
        for key in ("state", "summary", "checks", "blocking", "checked_at"):
            self.assertIn(key, v)
        for c in v["checks"]:
            for key in ("id", "label", "state", "reason", "remedy", "measured"):
                self.assertIn(key, c)


class TestInsufficientInstalledRam(unittest.TestCase):
    """Mode 1: the memory is not there and never will be."""

    def test_small_machine_is_blocked(self):
        v = pf.evaluate(_readings(total_gb=16.0, available_gb=14.0))
        self.assertEqual(v["state"], pf.BLOCKED)
        self.assertIn("installed_memory", v["blocking"])

    def test_never_suggests_closing_applications(self):
        """The whole point of separating the two modes.

        13.2 GB of model on a 16 GB machine cannot be recovered by quitting
        anything, and advice that implies otherwise sends the user looking in
        the wrong place — the exact failure this module exists to prevent.
        """
        c = _check(pf.evaluate(_readings(total_gb=16.0)), "installed_memory")
        self.assertEqual(c["state"], pf.BLOCKED)
        # The remedy offers the two levers that exist — a smaller model, or a
        # bigger machine — and nothing else.
        self.assertNotIn("clos", c["remedy"].lower())
        # The reason mentions closing only to rule it out, which is the point.
        self.assertIn("closing applications cannot recover", c["reason"].lower())
        self.assertIn("not installed", c["reason"].lower())

    def test_states_the_actual_numbers(self):
        c = _check(pf.evaluate(_readings(total_gb=16.0)), "installed_memory")
        self.assertIn("16.0 GB", c["reason"])
        self.assertEqual(c["measured"]["total_gb"], 16.0)
        self.assertGreater(c["measured"]["required_gb"], 16.0)

    def test_plenty_of_free_ram_does_not_rescue_a_small_machine(self):
        """A 16 GB machine sitting idle still cannot hold the model."""
        v = pf.evaluate(_readings(total_gb=16.0, available_gb=15.5))
        self.assertEqual(_check(v, "installed_memory")["state"], pf.BLOCKED)
        self.assertEqual(_check(v, "available_memory")["state"], pf.OK)

    def test_fits_without_a_guard_margin_is_degraded_not_blocked(self):
        # 13.18 model + 7.6 floor = 20.8 required; 22 GB fits but leaves under
        # the 2 GB the budget reserves for a build that spikes.
        c = _check(pf.evaluate(_readings(total_gb=22.0)), "installed_memory")
        self.assertEqual(c["state"], pf.DEGRADED)

    def test_uses_ollamas_own_weight_figure_when_it_has_one(self):
        c = _check(pf.evaluate(_readings(model_weights_gb=20.0)), "installed_memory")
        # 20 GB of weights on a 24 GB machine does not fit alongside the OS.
        self.assertEqual(c["state"], pf.BLOCKED)
        self.assertTrue(c["measured"]["model_size_measured"])

    def test_falls_back_to_the_measured_default_when_ollama_is_silent(self):
        c = _check(pf.evaluate(_readings(model_weights_gb=None)), "installed_memory")
        self.assertFalse(c["measured"]["model_size_measured"])
        self.assertAlmostEqual(c["measured"]["model_resident_gb"],
                               pf.DEFAULT_MODEL_RESIDENT_GB, places=1)


class TestTransientPressure(unittest.TestCase):
    """Mode 2: the memory is installed, something else is holding it."""

    def test_almost_no_free_memory_is_blocked(self):
        v = pf.evaluate(_readings(available_gb=0.8))
        self.assertEqual(v["state"], pf.BLOCKED)
        self.assertIn("available_memory", v["blocking"])

    def test_remedy_is_something_the_user_can_actually_do(self):
        c = _check(pf.evaluate(_readings(available_gb=0.8)), "available_memory")
        self.assertIn("Close other applications", c["remedy"])
        self.assertIn("GB more", c["remedy"])

    def test_the_hardware_check_stays_green(self):
        """Transient pressure must not be reported as a hardware problem."""
        v = pf.evaluate(_readings(available_gb=0.8))
        self.assertEqual(_check(v, "installed_memory")["state"], pf.OK)

    def test_kernel_critical_outranks_a_comfortable_free_figure(self):
        """macOS knows whether it is thrashing and we do not — the same
        calibration lesson memory_guard learned the hard way."""
        c = _check(pf.evaluate(_readings(available_gb=6.0, pressure_level=4)),
                   "available_memory")
        self.assertEqual(c["state"], pf.BLOCKED)
        self.assertIn("critical", c["reason"].lower())

    def test_kernel_warning_throttles_rather_than_stops(self):
        c = _check(pf.evaluate(_readings(available_gb=6.0, pressure_level=2)),
                   "available_memory")
        self.assertEqual(c["state"], pf.DEGRADED)

    def test_unloaded_model_with_no_room_to_load_it_is_blocked(self):
        """The scenario that used to take the machine down: work starts, Ollama
        pulls 12.7 GB in, and the box goes over the cliff mid-build."""
        c = _check(pf.evaluate(_readings(available_gb=6.0,
                                         model_resident_now=False)),
                   "available_memory")
        self.assertEqual(c["state"], pf.BLOCKED)
        self.assertIn("not loaded", c["reason"])

    def test_loaded_model_is_not_charged_for_twice(self):
        """With the weights already resident the machine has paid for them.
        Demanding another 13 GB free would block every healthy appliance."""
        c = _check(pf.evaluate(_readings(available_gb=6.0,
                                         model_resident_now=True)),
                   "available_memory")
        self.assertEqual(c["state"], pf.OK)

    def test_unknown_residency_never_blocks(self):
        c = _check(pf.evaluate(_readings(available_gb=6.0,
                                         model_resident_now=None)),
                   "available_memory")
        self.assertEqual(c["state"], pf.OK)


class TestContainerisedReading(unittest.TestCase):
    def test_docker_memory_is_unknown_not_a_hardware_verdict(self):
        """Inside Docker on macOS psutil reports the Linux VM's 8 GB, not the
        Mac's 24. Judged as hardware, that would hold the gate shut forever on
        a machine that is fine."""
        v = pf.evaluate(_readings(scope="container", total_gb=7.8,
                                  available_gb=0.4))
        self.assertEqual(_check(v, "installed_memory")["state"], pf.UNKNOWN)
        self.assertEqual(_check(v, "available_memory")["state"], pf.UNKNOWN)
        self.assertEqual(v["state"], pf.DEGRADED)
        self.assertEqual(v["blocking"], [])

    def test_it_says_why_out_loud(self):
        c = _check(pf.evaluate(_readings(scope="container")), "installed_memory")
        self.assertIn("Docker", c["reason"])


class TestDisk(unittest.TestCase):
    def test_nearly_full_disk_blocks(self):
        v = pf.evaluate(_readings(disk_free_gb=0.7))
        self.assertEqual(_check(v, "disk_space")["state"], pf.BLOCKED)
        self.assertIn("disk_space", v["blocking"])

    def test_tight_disk_warns(self):
        c = _check(pf.evaluate(_readings(disk_free_gb=6.0)), "disk_space")
        self.assertEqual(c["state"], pf.DEGRADED)

    def test_unreadable_disk_is_unknown(self):
        c = _check(pf.evaluate(_readings(disk_free_gb=None)), "disk_space")
        self.assertEqual(c["state"], pf.UNKNOWN)


class TestOllama(unittest.TestCase):
    def test_unreachable_blocks_because_there_is_no_fallback(self):
        v = pf.evaluate(_readings(ollama_reachable=False))
        self.assertEqual(_check(v, "ollama_model")["state"], pf.BLOCKED)

    def test_missing_model_names_the_near_miss(self):
        c = _check(pf.evaluate(_readings(model_installed=False,
                                         model_near_miss="nail-35b-a3b-ctx",
                                         model_weights_gb=None)),
                   "ollama_model")
        self.assertEqual(c["state"], pf.BLOCKED)
        self.assertIn("nail-35b-a3b-ctx", c["reason"])
        self.assertIn("ollama pull", c["remedy"])

    def test_not_probed_is_unknown_not_a_failure(self):
        c = _check(pf.evaluate(_readings(ollama_reachable=None)), "ollama_model")
        self.assertEqual(c["state"], pf.UNKNOWN)

    def test_exact_tag_matching(self):
        """A substring test reported a green preflight for a model Ollama could
        not serve; every generation call then 404'd."""
        installed = [{"name": "qwen3:14b-q4_K_M"}]
        ok, near, _entry = pf._model_match("qwen3:14b", installed)
        self.assertFalse(ok)
        self.assertEqual(near, "qwen3:14b-q4_K_M")

    def test_bare_name_resolves_to_latest(self):
        ok, _near, _e = pf._model_match("nail", [{"name": "nail:latest"}])
        self.assertTrue(ok)


class TestNeverRaises(unittest.TestCase):
    """A preflight that crashes is worse than one that reports degraded: the
    crash takes down the caller that was trying to protect itself."""

    def test_empty_readings(self):
        v = pf.evaluate({})
        self.assertIn(v["state"], (pf.DEGRADED, pf.BLOCKED))
        self.assertEqual(len(v["checks"]), 4)

    def test_malformed_numbers_become_unknown_not_an_exception(self):
        v = pf.evaluate(_readings(total_gb="twenty-four", available_gb=None,
                                  disk_free_gb="lots"))
        self.assertEqual(v["blocking"], [])
        for cid in ("installed_memory", "available_memory", "disk_space"):
            self.assertEqual(_check(v, cid)["state"], pf.UNKNOWN)

    def test_a_check_that_throws_is_reported_by_name(self):
        with patch.object(pf, "_check_disk", side_effect=RuntimeError("boom")):
            v = pf.evaluate(_readings())
        self.assertEqual(v["state"], pf.DEGRADED)
        self.assertTrue(any("boom" in c["reason"] for c in v["checks"]))

    def test_preflight_survives_a_broken_gather(self):
        with patch.object(pf, "gather", side_effect=RuntimeError("no /proc")):
            v = pf.preflight()
        self.assertEqual(v["state"], pf.DEGRADED)
        self.assertEqual(v["blocking"], [])
        self.assertIn("no /proc", v["summary"])

    def test_gather_tolerates_a_garbage_resources_payload(self):
        for payload in ({}, {"memory": None}, {"memory": {"error": "nope"}},
                        {"memory": {"total_gb": "many"}, "storage": "broken"}):
            r = pf.gather(resources=payload, probe_ollama=False)
            self.assertIn("scope", r)
            self.assertIsNone(r["ollama_reachable"])

    def test_describe_never_raises(self):
        self.assertIn("preflight", pf.describe(pf.evaluate(_readings())).lower())


class TestOverallFolding(unittest.TestCase):
    def test_one_blocked_check_blocks_the_verdict(self):
        v = pf.evaluate(_readings(disk_free_gb=0.5))
        self.assertEqual(v["state"], pf.BLOCKED)
        self.assertEqual(v["summary"], _check(v, "disk_space")["reason"])

    def test_advisory_mode_downgrades_the_overall_state_only(self):
        """The escape hatch must not hide anything — the check still says what
        it measured, it is simply not held shut."""
        with patch.dict(os.environ, {"HELGA_PREFLIGHT_ADVISORY": "1"}):
            v = pf.evaluate(_readings(total_gb=16.0))
        self.assertEqual(v["state"], pf.DEGRADED)
        self.assertTrue(v["advisory"])
        self.assertEqual(_check(v, "installed_memory")["state"], pf.BLOCKED)
        self.assertIn("installed_memory", v["blocking"])


if __name__ == "__main__":
    unittest.main()
