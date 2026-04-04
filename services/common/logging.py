"""
Shared JSON Logging Formatter for all services.

This module provides a reusable JSONFormatter class that can be imported
by any service to format log records as structured JSON with consistent fields.
"""

import logging
import json


class JSONFormatter(logging.Formatter):
    """Formats log records as JSON with structured fields.
    
    This formatter creates consistent JSON log output across all services,
    making logs easier to parse and aggregate.
    
    Args:
        service_name (str): Name of the service using this formatter (e.g., 'core', 'rag', 'ingest')
    """
    
    def __init__(self, service_name='unknown'):
        super().__init__()
        self.service_name = service_name
    
    def format(self, record):
        """Format a log record as JSON.
        
        Args:
            record: LogRecord to format
            
        Returns:
            JSON string with structured log data
        """
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "service": self.service_name,
            "step": getattr(record, 'step', 'unknown'),
            "message": record.getMessage()
        }
        return json.dumps(log_data)
