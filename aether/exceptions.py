"""
Aether custom exception hierarchy.

All Aether-specific errors inherit from AetherError, enabling
typed error handling and user-friendly error messages.
"""


class AetherError(Exception):
    """Base exception for all Aether errors."""
    pass


class APIKeyError(AetherError):
    """Raised when the API key is missing, invalid, or rejected (HTTP 401)."""
    pass


class APIConnectionError(AetherError):
    """Raised when the API is unreachable or the request times out."""
    pass


class RateLimitError(AetherError):
    """Raised when the API returns HTTP 429 (rate limited)."""
    pass


class ToolExecutionError(AetherError):
    """Raised when a tool fails to execute properly."""
    pass


class FileOperationError(AetherError):
    """Raised when a file read/write/edit operation fails."""
    pass


class SessionCorruptedError(AetherError):
    """Raised when a session file cannot be loaded or is malformed."""
    pass


class ContextWindowExceededError(AetherError):
    """Raised when the conversation exceeds the model's context window."""
    pass
