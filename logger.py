"""
Logging module for GSM Door Opener application.
Provides configurable verbosity levels for SIP client logging.
"""

import logging
from enum import IntEnum


class LogLevel(IntEnum):
    """Verbosity levels for application logging."""
    CRITICAL = 0 # Critical errors and essential startup/shutdown only
    LOW = 1      # Essential messages (errors, registration, call start/end)
    MEDIUM = 2   # Include SIP responses and state changes
    HIGH = 3     # Full debug output with all SIP protocol details


class AppLogger:
    """
    Centralized logger with configurable verbosity.

    Verbosity levels:
    - CRITICAL: Critical errors and essential startup/shutdown only
    - LOW: Essential events (registration, call start/end, errors)
    - MEDIUM: Include SIP response codes and state changes
    - HIGH: Full SIP protocol details and debugging information
    """

    def __init__(self, name: str, level: LogLevel = LogLevel.CRITICAL):
        """
        Initialize the logger.

        Args:
            name: Logger name (usually module name)
            level: Verbosity level (CRITICAL, LOW, MEDIUM, HIGH)
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

    # Critical messages (shown at all verbosity levels)
    def critical(self, message: str):
        """Log critical information (always shown at all levels)."""
        self.logger.info(message)

    def error(self, message: str):
        """Log error message (always shown)."""
        self.logger.error(message)

    # Essential messages (shown at LOW and above)
    def essential(self, message: str):
        """Log essential information (LOW and above)."""
        if self.verbosity >= LogLevel.LOW:
            self.logger.info(message)

    def success(self, message: str):
        """Log success message (LOW and above)."""
        if self.verbosity >= LogLevel.LOW:
            self.logger.info(message)

    # Medium verbosity messages
    def info(self, message: str):
        """Log informational message (MEDIUM and HIGH)."""
        if self.verbosity >= LogLevel.MEDIUM:
            self.logger.info(message)

    # High verbosity messages
    def debug(self, message: str):
        """Log debug message (HIGH only)."""
        if self.verbosity >= LogLevel.HIGH:
            self.logger.debug(message)

    # SIP-specific logging methods
    def sip_separator(self, major: bool = False):
        """Log separator line for SIP messages."""
        if major:
            # Major separators only shown at LOW+
            if self.verbosity >= LogLevel.LOW:
                self.logger.info("=" * 60)
        else:
            # Minor separators only at MEDIUM+
            self.info("─" * 60)

    def sip_section_start(self, title: str):
        """Log start of SIP section (LOW and above)."""
        if self.verbosity >= LogLevel.LOW:
            self.logger.info("")
            self.logger.info("=" * 60)
            self.logger.info(title)
            self.logger.info("=" * 60)

    def sip_section_info(self, key: str, value: str):
        """Log SIP section information (LOW and above)."""
        if self.verbosity >= LogLevel.LOW:
            self.logger.info(f"{key:<12} {value}")

    def sip_request(self, message: str):
        """Log outgoing SIP request (essential for LOW, detailed for MEDIUM+)."""
        if self.verbosity >= LogLevel.MEDIUM:
            self.logger.info(f"──► {message}")
        else:
            # For LOW verbosity, only log major requests
            if any(req in message for req in ['REGISTER', 'INVITE', 'BYE']):
                self.logger.info(f"──► {message.split()[0]}")

    def sip_response(self, code: int, message: str):
        """Log incoming SIP response."""
        if self.verbosity >= LogLevel.MEDIUM:
            self.info("─" * 60)
            self.logger.info(f"◄── SIP PROXY RESPONSE: {code}")
            self.logger.info(f"    {message}")
        else:
            # For LOW verbosity, only log important responses
            if code in [200, 180, 486, 603]:
                self.logger.info(f"◄── {code} {message.split('-')[0].strip()}")

    def sip_state_change(self, message: str):
        """Log SIP state change."""
        if self.verbosity >= LogLevel.MEDIUM:
            self.logger.info(f"    {message}")

    def call_event(self, message: str):
        """Log call event (essential)."""
        self.essential(message)

    def registration_event(self, code: int, message: str):
        """Log registration event (essential)."""
        if code == 200:
            self.essential(f"◄── {code} OK (registration successful)")
        else:
            self.error(f"◄── {code} {message}")

    def timer_event(self, message: str):
        """Log timer event (essential)."""
        if self.verbosity >= LogLevel.MEDIUM:
            self.logger.info(f"⏱  {message}")
        else:
            # For LOW verbosity, only log when timer starts
            if "timer:" in message.lower():
                self.logger.info(f"⏱  {message}")


def create_logger(name: str, level_str: str = "") -> AppLogger:
    """
    Create a logger instance with specified verbosity.

    Args:
        name: Logger name
        level_str: Verbosity level as string ("CRITICAL", "LOW", "MEDIUM", "HIGH")
                  Empty string defaults to CRITICAL (minimal output)

    Returns:
        AppLogger instance
    """
    level_map = {
        "CRITICAL": LogLevel.CRITICAL,
        "LOW": LogLevel.LOW,
        "MEDIUM": LogLevel.MEDIUM,
        "HIGH": LogLevel.HIGH
    }

    # Default to CRITICAL if empty or invalid
    level = level_map.get(level_str.upper(), LogLevel.CRITICAL)
    return AppLogger(name, level)
