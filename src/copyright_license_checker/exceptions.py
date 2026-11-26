"""Exceptions used to distinguish invalid input from checker failures."""


class CheckerError(Exception):
    """Base class for expected checker failures."""


class InvalidConfigurationError(CheckerError):
    """Raised when a user-supplied path or configuration is invalid."""


class CheckerExecutionError(CheckerError):
    """Raised when a required checker dependency cannot complete work."""
