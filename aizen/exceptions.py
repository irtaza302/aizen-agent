"""
Aizen custom exception hierarchy.

All Aizen-specific errors inherit from AizenError, enabling
typed error handling and user-friendly error messages.
"""


class AizenError(Exception):
    """Base exception for all Aizen errors."""

    pass


class APIKeyError(AizenError):
    """Raised when the API key is missing, invalid, or rejected (HTTP 401)."""

    pass


class APIConnectionError(AizenError):
    """Raised when the API is unreachable or the request times out."""

    pass


class RateLimitError(AizenError):
    """Raised when the API returns HTTP 429 (rate limited)."""

    pass


class ToolExecutionError(AizenError):
    """Raised when a tool fails to execute properly."""

    pass


class FileOperationError(AizenError):
    """Raised when a file read/write/edit operation fails."""

    pass


class SessionCorruptedError(AizenError):
    """Raised when a session file cannot be loaded or is malformed."""

    pass


class ContextWindowExceededError(AizenError):
    """Raised when the conversation exceeds the model's context window."""

    pass


class UsageError(AizenError):
    """Raised for command whitelist warnings or misuse."""

    pass


class SecurityError(AizenError):
    """Raised for unsafe operations like path traversal or dangerous commands."""

    pass


class ToolError(AizenError):
    """Raised for general tool execution errors."""

    pass
