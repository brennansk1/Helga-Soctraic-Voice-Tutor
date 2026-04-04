
import unittest
import sys
import os
from unittest.mock import MagicMock, patch

# Add services/core to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

# Mock dependencies BEFORE import
sys.modules['docker'] = MagicMock()
sys.modules['docker.errors'] = MagicMock()
sys.modules['yaml'] = MagicMock()

from services.core.service_manager import ServiceManager

class TestServiceManager(unittest.TestCase):
    def setUp(self):
        self.sm = ServiceManager()
        # Mock internal logger to avoid clutter
        self.sm.logger = MagicMock()

    @patch('subprocess.run')
    def test_start_service_success(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "started"
        
        result = self.sm.start_service("helga-rag-engine", retries=1)
        self.assertTrue(result)
        # Check that it called 'docker start helga-rag-engine'
        args, _ = mock_run.call_args
        cmd_list = args[0]
        self.assertIn("start", cmd_list)
        self.assertIn("helga-rag-engine", cmd_list)

    @patch('subprocess.run')
    def test_stop_service_failure(self, mock_run):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "No such container"
        
        result = self.sm.stop_service("helga-rag-engine", retries=1)
        self.assertFalse(result)

    @patch('services.core.service_manager.requests.get')
    def test_verify_service_healthy_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'status': 'healthy'}
        mock_get.return_value = mock_resp
        
        result = self.sm.verify_service_healthy("helga-rag-engine")
        self.assertTrue(result)

    @patch('requests.get')
    def test_verify_service_healthy_failure(self, mock_get):
        mock_get.side_effect = Exception("Connection Refused")
        # Reduce retries for test speed (config is shared so we mock it or just wait)
        with patch.dict(self.sm.SERVICE_CONFIG, {'helga-rag-engine': {'health_url': 'http://foo', 'health_check_retries': 1}}):
             result = self.sm.verify_service_healthy("helga-rag-engine")
             self.assertFalse(result)

if __name__ == '__main__':
    unittest.main()
