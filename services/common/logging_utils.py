"""Structured JSON logging (B27.1).

Opt-in via HELGA_JSON_LOGS=true: every record becomes one JSON line carrying
service, level, message, and any bound correlation fields (student_id,
request_id). Plain formatter unchanged otherwise, so local dev logs stay
human-readable.
"""

import json
import logging
import os
import time


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record):
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("student_id", "request_id", "course_uid"):
            val = getattr(record, key, None)
            if val:
                entry[key] = val
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_json_logging(service: str) -> bool:
    """Swap root handlers to JSON when HELGA_JSON_LOGS=true. Returns whether
    JSON mode is active."""
    if os.getenv("HELGA_JSON_LOGS", "").lower() != "true":
        return False
    formatter = JsonFormatter(service)
    for handler in logging.getLogger().handlers:
        handler.setFormatter(formatter)
    logging.getLogger(__name__).info(f"JSON logging active for {service}")
    return True


class StudentContextAdapter(logging.LoggerAdapter):
    """logger = with_student(logging.getLogger(__name__), student_id) — every
    line from this adapter carries the correlation field."""

    def process(self, msg, kwargs):
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("student_id", self.extra.get("student_id"))
        return msg, kwargs


def with_student(logger, student_id):
    return StudentContextAdapter(logger, {"student_id": student_id})
