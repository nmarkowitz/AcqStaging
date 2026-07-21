"""Application-specific exceptions."""


class AcqStagingError(Exception):
    """Base application exception."""


class ConfigurationError(AcqStagingError):
    """Configuration is invalid."""


class MotionSafetyError(AcqStagingError):
    """A motion request violates a safety invariant."""


class AdapterError(AcqStagingError):
    """A hardware or simulation adapter failed."""
