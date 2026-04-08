
import unittest
import sys
import os
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from services.core.service_manager import ServiceManager

class TestServiceManager(unittest.TestCase):
    def setUp(self):
        self.sm = ServiceManager()

    def test_stop_for_ingestion_is_noop(self):
        """stop_for_ingestion() should succeed silently (no-op stub)."""
        # Should not raise
        result = self.sm.stop_for_ingestion()
        # No-op returns None
        self.assertIsNone(result)

    def test_restart_after_ingestion_is_noop(self):
        """restart_after_ingestion() should succeed silently (no-op stub)."""
        result = self.sm.restart_after_ingestion()
        self.assertIsNone(result)

    def test_check_service_health_returns_true(self):
        """check_service_health() should always return True (no-op stub)."""
        result = self.sm.check_service_health("helga-rag-engine")
        self.assertTrue(result)

    def test_check_service_health_with_timeout(self):
        """check_service_health() accepts optional timeout kwarg."""
        result = self.sm.check_service_health("helga-core-logic", timeout=30)
        self.assertTrue(result)

    def test_constructor_accepts_kwargs(self):
        """ServiceManager constructor should accept legacy kwargs without error."""
        sm = ServiceManager(compose_cmd="docker compose", dev_mode=True)
        self.assertIsNotNone(sm)

if __name__ == '__main__':
    unittest.main()
