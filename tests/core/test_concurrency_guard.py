"""Tests for course_builder concurrency guard (P2-13)."""
import threading
import time
import sys
import os
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from unittest.mock import patch, MagicMock
from services.core.course_builder import SkeletonBuilder, _build_lock


class TestConcurrencyGuard:
    """Verify that only one course build can run at a time."""

    def setup_method(self):
        # Ensure lock is released before each test
        if _build_lock.locked():
            _build_lock.release()

    @patch("services.core.course_builder.SkeletonBuilder._build_inner")
    @patch("services.core.course_builder.StorageManager")
    def test_concurrent_build_rejected(self, mock_storage, mock_build_inner):
        """Second concurrent build should be rejected immediately."""
        mock_build_inner.return_value = "course_abc123"
        mock_storage.return_value = MagicMock()

        results = {}
        errors = {}

        # Simulate first build holding the lock
        def slow_build():
            builder = SkeletonBuilder(storage=MagicMock())
            # Override _build_inner to sleep
            original = builder._build_inner
            def slow_inner(*args, **kwargs):
                time.sleep(0.5)
                return "course_slow"
            builder._build_inner = slow_inner
            results["first"] = builder.build("Slow Topic")

        def fast_build():
            time.sleep(0.1)  # Ensure first build has the lock
            builder = SkeletonBuilder(storage=MagicMock())
            callback_msgs = []
            builder.status_callback = lambda msg: callback_msgs.append(msg)
            results["second"] = builder.build("Fast Topic")
            errors["callback_msgs"] = callback_msgs

        t1 = threading.Thread(target=slow_build)
        t2 = threading.Thread(target=fast_build)

        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # First build should succeed
        assert results.get("first") == "course_slow"
        # Second build should be rejected
        assert results.get("second") is None
        # Error callback should have been called
        assert any("already being built" in msg for msg in errors.get("callback_msgs", []))

    @patch("services.core.course_builder.SkeletonBuilder._build_inner")
    def test_lock_released_after_build(self, mock_build_inner):
        """Lock should be released after a build completes (success or failure)."""
        mock_build_inner.return_value = "course_123"

        builder = SkeletonBuilder(storage=MagicMock())
        result = builder.build("Test Topic")
        assert result == "course_123"

        # Lock should be released
        assert not _build_lock.locked()

    @patch("services.core.course_builder.SkeletonBuilder._build_inner")
    def test_lock_released_on_exception(self, mock_build_inner):
        """Lock should be released even if _build_inner raises."""
        mock_build_inner.side_effect = RuntimeError("LLM crashed")

        builder = SkeletonBuilder(storage=MagicMock())
        with pytest.raises(RuntimeError):
            builder.build("Crashing Topic")

        # Lock must still be released
        assert not _build_lock.locked()

    @patch("services.core.course_builder.SkeletonBuilder._build_inner")
    def test_sequential_builds_work(self, mock_build_inner):
        """Sequential builds should both succeed."""
        mock_build_inner.side_effect = ["course_a", "course_b"]

        builder = SkeletonBuilder(storage=MagicMock())
        r1 = builder.build("Topic A")
        r2 = builder.build("Topic B")

        assert r1 == "course_a"
        assert r2 == "course_b"
