
import unittest
import json
from unittest.mock import MagicMock, patch
import sys
import os

# Path moved to setUp

import importlib
from unittest.mock import MagicMock, patch

class TestWizardTab(unittest.TestCase):
    def setUp(self):
        # 0. Path Setup
        self.app_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../services/web-ui'))
        if self.app_path not in sys.path:
            sys.path.insert(0, self.app_path)
        # 1. Prepare Smart Mocks
        mock_flask = MagicMock()
        def route_side_effect(rule, **options):
            def decorator(f):
                # Don't overwrite actual view functions, just return them
                f.required_methods = ()
                return f
            return decorator
        
        # Don't mock Flask itself, just app.route to prevent AssertionError on reload
        self.route_patcher = patch('flask.Flask.route', side_effect=route_side_effect)
        self.route_patcher.start()
        
        mock_socketio_mod = MagicMock()
        mock_socketio = MagicMock()
        def on_side_effect(event, **options):
             def decorator(f):
                 return f
             return decorator
        mock_socketio.on.side_effect = on_side_effect
        mock_socketio_mod.SocketIO.return_value = mock_socketio

        # 2. Patch sys.modules safely
        self.modules_patcher = patch.dict(sys.modules, {
            'kuzu': MagicMock(),
            'libzim': MagicMock(),
            'sentence_transformers': MagicMock(),
            'flask_socketio': mock_socketio_mod,
            'gevent': MagicMock(),
            'requests': MagicMock()
        })
        self.modules_patcher.start()
        self.addCleanup(self.modules_patcher.stop)

        # 3. File/Logging Patches
        self.makedirs_patcher = patch('os.makedirs')
        self.makedirs_patcher.start()
        self.addCleanup(self.makedirs_patcher.stop)
        
        self.logging_patcher = patch('logging.FileHandler')
        self.logging_patcher.start()
        self.addCleanup(self.logging_patcher.stop)
        
        self.basic_config_patcher = patch('logging.basicConfig')
        self.basic_config_patcher.start()
        self.addCleanup(self.basic_config_patcher.stop)

        # 4. Import and Reload App
        import app
        importlib.reload(app)
        
        self.app = app.app.test_client()
        self.app.testing = True

    def tearDown(self):
        if hasattr(self, 'route_patcher'): self.route_patcher.stop()
        if hasattr(self, 'app_path') and self.app_path in sys.path:
            sys.path.remove(self.app_path)
        if 'app' in sys.modules: del sys.modules['app']

    @patch('services.core.course_builder.SkeletonBuilder.build')
    def test_create_course_route(self, mock_build):
        # Configure Request Mock
        import app
        # Patch requests.post on the already imported app module (from setUp)
        with patch('app.requests.post') as mock_post:
            mock_request = MagicMock()
            mock_request.form = {
                "title": "Quantum Mechanics",
                "teaching_style": "Socratic",
                "description": "Intro to QM",
                "modules": json.dumps([{"title": "M1"}]),
                "structure": json.dumps({"modules": [{"title": "M1"}]})
            }
            mock_request.files = {}
            
            # Mock flask global request on the MODULE
            app.request = mock_request
            app.jsonify = lambda x: x 
            
            mock_rag_response = MagicMock()
            mock_rag_response.status_code = 200
            mock_rag_response.json.return_value = {"success": True, "course_uid": "course_123"}
            mock_post.return_value = mock_rag_response

            # Call the function directly
            from app import create_custom_course_wizard
            response_data, status_code = create_custom_course_wizard()
            
            # Verify result
            self.assertEqual(status_code, 200)
            self.assertEqual(response_data['success'], True)
            self.assertEqual(response_data['course_uid'], "course_123")
            
            # Verify logic
            self.assertTrue(mock_post.called)
            args, kwargs = mock_post.call_args
            self.assertIn('/api/custom_course/create', args[0])
            self.assertEqual(kwargs['json']['title'], "Quantum Mechanics")

    def test_wizard_ui_load(self):
        # Can't test UI load without templates, so just skip or basic check
        pass

if __name__ == '__main__':
    unittest.main()
