"""
Logging module for GSM Door Opener application.
Provides configurable verbosity levels for SIP client logging.
"""

import logging
from enum import IntEnum


class LogLevel(IntEnum):
    """Verbosity levels for application logging."""
    ERROR = 0    # Errors only
    INFO = 1     # Errors + informational messages


class AppLogger:
    """
    Centralized logger with configurable verbosity.

    Verbosity levels:
    - ERROR: Errors only
    - INFO: Errors + informational messages
    """

    def __init__(self, name: str, level: LogLevel = LogLevel.ERROR):
        """
        Initialize the logger.

        Args:
            name: Logger name (usually module name)
            level: Verbosity level (ERROR, INFO)
        """
        self.logger = logging.getLogger(name)
        self.verbosity = level

        # Prevent duplicate handlers
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            # Simple format without timestamp/level for cleaner output
            formatter = logging.Formatter('%(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.DEBUG)

        # Prevent propagation to root logger to avoid duplicates
        self.logger.propagate = False

    def set_verbosity(self, level: LogLevel):
        """Set the verbosity level."""
        self.verbosity = level

    def get_verbosity(self) -> LogLevel:
        """Get the current verbosity level."""
        return self.verbosity

    def error(self, message: str):
        """Log error message (always shown)."""
        self.logger.error(message)

    def info(self, message: str):
        """Log informational message (INFO level only)."""
        if self.verbosity >= LogLevel.INFO:
            self.logger.info(message)

    # SIP-specific logging methods
    def sip_request(self, message: str):
        """Log outgoing SIP request (INFO level)."""
        if self.verbosity >= LogLevel.INFO:
            self.logger.info(f"──► {message}")

    def sip_response(self, code: int, message: str):
        """Log incoming SIP response (INFO level)."""
        if self.verbosity >= LogLevel.INFO:
            self.logger.info(f"◄── {code} {message}")

    def call_event(self, message: str):
        """Log call event (INFO level)."""
        if self.verbosity >= LogLevel.INFO:
            self.logger.info(message)

    def registration_event(self, code: int, message: str):
        """Log registration event."""
        if code == 200:
            self.info(f"SIP registered successfully")
        else:
            self.error(f"SIP registration failed: {code} {message}")


def create_logger(name: str, level_str: str = "") -> AppLogger:
    """
    Create a logger instance with specified verbosity.

    Args:
        name: Logger name
        level_str: Verbosity level as string ("ERROR", "INFO")
                  Empty string defaults to ERROR (errors only)

    Returns:
        AppLogger instance
    """
    level_map = {
        "ERROR": LogLevel.ERROR,
        "INFO": LogLevel.INFO
    }

    # Default to ERROR if empty or invalid
    level = level_map.get(level_str.upper(), LogLevel.ERROR)
    return AppLogger(name, level)
