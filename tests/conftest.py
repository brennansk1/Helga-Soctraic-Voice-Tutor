import os
import shutil
import tempfile
import pytest

# Create temp dir immediately upon conftest load (before modules are imported)
test_data_dir = tempfile.mkdtemp(prefix="helga_test_data_")
os.environ["DATA_ROOT"] = test_data_dir

import sys
from unittest.mock import MagicMock
# Globally mock dependencies missing on MacOS native test environments
for mod in ['kuzu', 'pyaudio', 'aiortc', 'webrtcvad', 'smbus2', 'faster_whisper', 
            'yaml', 'docker', 'docker.errors', 'libzim', 'zimply', 'zimply.zimply',
            'evdev', 'av', 'torch', 'sentence_transformers', 'audioop']:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

@pytest.fixture(scope="session", autouse=True)
def setup_test_env():
    """Cleanup temp directory after tests."""
    yield
    # Cleanup after all tests
    shutil.rmtree(test_data_dir, ignore_errors=True)

