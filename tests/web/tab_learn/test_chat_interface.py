
import unittest
import json
from unittest.mock import MagicMock, patch
import sys
import os

# Path moved to setUp

import importlib
from unittest.mock import MagicMock, patch

class TestLearnTab(unittest.TestCase):
    def setUp(self):
        # 0. Path Setup
        self.app_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../services/web-ui'))
        if self.app_path not in sys.path:
            sys.path.insert(0, self.app_path)
        # 1. Prepare Smart Mocks
        mock_flask = MagicMock()
        def route_side_effect(rule, **options):
            def decorator(f):
                return f
            return decorator
        mock_app_inst = MagicMock()
        mock_app_inst.route.side_effect = route_side_effect
        mock_flask.Flask.return_value = mock_app_inst
        
        mock_socketio_mod = MagicMock()
        mock_socketio = MagicMock()
        def on_side_effect(event, **options):
             def decorator(f):
                 return f
             return decorator
        mock_socketio.on.side_effect = on_side_effect
        mock_socketio_mod.SocketIO.return_value = mock_socketio
        sys.modules['flask_socketio'] = mock_socketio_mod

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

        import app
        importlib.reload(app)
        
        self.client = app.socketio.test_client(app.app)

    def tearDown(self):
        if hasattr(self, 'app_path') and self.app_path in sys.path:
            sys.path.remove(self.app_path)
        if 'app' in sys.modules: del sys.modules['app']

    def test_socket_message(self):
        # Test handle_text_input directly
        from app import handle_text_input
        with patch('app.requests.post') as mock_post:
            data = {'text': 'Hello'}
            handle_text_input(data)
            
            # Verify it posts to Core
            self.assertTrue(mock_post.called)
            args, kwargs = mock_post.call_args
            self.assertIn('/event', args[0])
            self.assertEqual(kwargs['json']['type'], 'TEXT_INPUT')
            self.assertEqual(kwargs['json']['payload'], data)

    def test_learn_page_load(self):
        pass

if __name__ == '__main__':
    unittest.main()
