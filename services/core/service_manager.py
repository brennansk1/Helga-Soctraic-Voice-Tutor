"""
Service Manager — No-op stub.

The old ServiceManager stopped/started Docker services during course creation
to release KuzuDB file locks. With SQLite WAL mode, this is no longer needed.
All methods are no-ops to maintain backward compatibility with callers.
"""

import logging

logger = logging.getLogger(__name__)


class ServiceManager:
    def __init__(self, compose_cmd=None, dev_mode=False, **kwargs):
        logger.info("ServiceManager initialized (no-op — SQLite WAL mode)")

    def stop_for_ingestion(self):
        logger.info("stop_for_ingestion: no-op (services stay running)")

    def restart_after_ingestion(self):
        logger.info("restart_after_ingestion: no-op (services already running)")

    def check_service_health(self, service_name, timeout=10):
        return True
