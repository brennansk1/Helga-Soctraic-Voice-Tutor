"""Helga runs on the user's own computer; batch work must not OOM it.

Measured on the target hardware (Mac Mini M4 Pro, 24 GB) with Docker DOWN and
only a browser open: 10.3 GB of 11.3 GB swap already consumed and Ollama
holding 6.1 GB resident. Bringing up the containers and a larger model on top
of that thrashes the machine the user is trying to use.

The GpuGate limits CONCURRENCY, which does not help — one background job is
enough. These tests pin the behaviour of the memory dimension.
"""

import os
import sys
import unittest
from unittest.mock import patch

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.common import memory_guard as mg  # noqa: E402


def _snap(**kw):
    base = dict(total_gb=24.0, available_gb=12.0, used_pct=50.0,
                swap_total_gb=8.0, swap_used_gb=1.0, swap_used_frac=0.125,
                source="test")
    base.update(kw)
    return mg.MemorySnapshot(base)


class TestPressureDetection(unittest.TestCase):
    def test_healthy_machine_allows_background(self):
        self.assertIsNone(mg.pressure_reason(_snap()))
        self.assertTrue(mg.allow_background(_snap()))

    def test_low_free_memory_is_pressure(self):
        r = mg.pressure_reason(_snap(available_gb=1.0))
        self.assertIsNotNone(r)
        self.assertIn("free", r)

    def test_high_swap_is_pressure_even_with_free_ram(self):
        """The observed real failure: RAM looked fine, swap was at 86%.

        A naive free-RAM check passes here and lets the machine keep thrashing.
        """
        r = mg.pressure_reason(_snap(available_gb=15.0, swap_used_gb=6.9,
                                     swap_used_frac=0.86))
        self.assertIsNotNone(r, "swap exhaustion must count as pressure")
        self.assertIn("swap", r)

    def test_unreadable_memory_fails_open(self):
        """An unreadable memory subsystem must not wedge the pipeline."""
        self.assertIsNone(mg.pressure_reason(_snap(source="unavailable",
                                                   available_gb=0.0,
                                                   swap_used_frac=0.99)))
        self.assertTrue(mg.allow_background(_snap(source="unavailable",
                                                  available_gb=0.0)))


class TestWorkerSizing(unittest.TestCase):
    def test_workers_scale_down_under_pressure(self):
        with patch.object(mg, 'snapshot', return_value=_snap(available_gb=3.5)):
            self.assertEqual(mg.suggested_workers(default=3), 1)

    def test_workers_available_when_roomy(self):
        with patch.object(mg, 'snapshot', return_value=_snap(available_gb=20.0)):
            self.assertGreaterEqual(mg.suggested_workers(default=3), 1)

    def test_never_returns_zero(self):
        """Zero workers would silently stall generation forever."""
        with patch.object(mg, 'snapshot', return_value=_snap(available_gb=0.1)):
            self.assertGreaterEqual(mg.suggested_workers(default=3), 1)


class TestRealMachineReadable(unittest.TestCase):
    def test_snapshot_works_on_this_host(self):
        s = mg.snapshot(force=True)
        self.assertGreater(s.total_gb, 0, "should read real memory on macOS/Linux")
        self.assertIn(s.source, ("psutil", "vm_stat"))

    def test_describe_is_human_readable(self):
        self.assertIn("mem", mg.describe())


class TestGateIntegration(unittest.TestCase):
    def test_gate_consults_memory_for_background_only(self):
        """Foreground work must never be blocked on memory — a student waiting
        mid-lesson is not the thing to sacrifice."""
        import inspect
        from services.core.gpu_gate import GpuGate
        src = inspect.getsource(GpuGate.admit)
        self.assertIn("memory_guard", src)
        self.assertIn("BACKGROUND", src)
        # The guard must be inside a background-only branch.
        self.assertLess(src.index("BACKGROUND"), src.index("memory_guard"),
                        "memory check must be gated on the background class")


if __name__ == '__main__':
    unittest.main()
