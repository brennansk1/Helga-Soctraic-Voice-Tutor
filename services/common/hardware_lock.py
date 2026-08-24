"""hardware_lock.py — Single-Active Account Hardware Access Manager.

Enforces exclusive hardware access (Ollama GPU gate + STT/TTS audio devices)
such that only ONE account can be actively using system hardware at a time.
"""

import logging
import threading
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class HardwareLockError(Exception):
    """Raised when an unauthorized user attempts hardware access while another account is active."""
    pass


class HardwareSessionManager:
    """Manages single-active account hardware allocation."""

    def __init__(self, idle_timeout_s: int = 1800):
        self._lock = threading.Lock()
        self.active_user_id: Optional[str] = None
        self.active_user_email: Optional[str] = None
        self.last_active_time: float = 0.0
        self.idle_timeout_s = idle_timeout_s

    def claim_hardware_session(self, user_id: str, email: str = "") -> bool:
        """Claim exclusive hardware access for `user_id`. Releases stale sessions if timed out."""
        with self._lock:
            now = time.time()
            if self.active_user_id and self.active_user_id != user_id:
                if (now - self.last_active_time) > self.idle_timeout_s:
                    logger.info(f"Hardware session for '{self.active_user_id}' timed out due to inactivity. Reassigning to '{user_id}'.")
                    self.active_user_id = user_id
                    self.active_user_email = email
                    self.last_active_time = now
                    return True
                return False

            self.active_user_id = user_id
            self.active_user_email = email
            self.last_active_time = now
            return True

    def release_hardware_session(self, user_id: str) -> bool:
        """Release hardware access held by `user_id`."""
        with self._lock:
            if self.active_user_id == user_id:
                logger.info(f"Released hardware session for user '{user_id}'.")
                self.active_user_id = None
                self.active_user_email = None
                self.last_active_time = 0.0
                return True
            return False

    def check_hardware_access(self, user_id: str):
        """Assert that `user_id` holds active hardware access, raising HardwareLockError if locked by another user."""
        with self._lock:
            now = time.time()
            if self.active_user_id is None:
                self.active_user_id = user_id
                self.last_active_time = now
                return

            if (now - self.last_active_time) > self.idle_timeout_s:
                self.active_user_id = user_id
                self.last_active_time = now
                return

            if self.active_user_id != user_id:
                raise HardwareLockError(
                    f"Hardware is currently locked by active user '{self.active_user_email or self.active_user_id}'. "
                    f"Only one account can use hardware at a time."
                )
            self.last_active_time = now

    def get_status(self) -> Dict:
        """Return status of active hardware session."""
        with self._lock:
            now = time.time()
            is_active = bool(self.active_user_id and (now - self.last_active_time) <= self.idle_timeout_s)
            return {
                "active_hardware_user_id": self.active_user_id if is_active else None,
                "active_hardware_user_email": self.active_user_email if is_active else None,
                "idle_seconds": int(now - self.last_active_time) if self.active_user_id else None,
                "is_hardware_busy": is_active,
            }


# Singleton hardware session manager instance
_hardware_manager = HardwareSessionManager()


def get_hardware_manager() -> HardwareSessionManager:
    return _hardware_manager
