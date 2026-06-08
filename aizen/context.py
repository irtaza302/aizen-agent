"""
Context window management for Aizen.

Tracks token usage against model context limits and auto-compacts
conversations when approaching the boundary.
"""

import json
from rich.text import Text

from .config import console


# Known context window sizes for popular models (in tokens).
# Users can override via config.
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # Anthropic
    "anthropic/claude-sonnet-4": 200_000,
    "anthropic/claude-3.5-sonnet": 200_000,
    "anthropic/claude-3.7-sonnet": 200_000,
    "anthropic/claude-3-opus": 200_000,
    "anthropic/claude-3-haiku": 200_000,
    "anthropic/claude-3.5-haiku": 200_000,
    "anthropic/claude-4-opus": 200_000,
    # OpenAI
    "openai/gpt-4o": 128_000,
    "openai/gpt-4o-mini": 128_000,
    "openai/gpt-4-turbo": 128_000,
    "openai/gpt-4": 8_192,
    "openai/gpt-4.1": 1_047_576,
    "openai/gpt-4.1-mini": 1_047_576,
    "openai/gpt-4.1-nano": 1_047_576,
    "openai/o1": 200_000,
    "openai/o1-mini": 128_000,
    "openai/o3": 200_000,
    "openai/o3-mini": 200_000,
    "openai/o4-mini": 200_000,
    # Google
    "google/gemini-2.5-pro": 1_048_576,
    "google/gemini-2.5-flash": 1_048_576,
    "google/gemini-2.0-flash": 1_048_576,
    "google/gemini-2.0-flash-001": 1_048_576,
    "google/gemini-pro-1.5": 1_048_576,
    # Meta
    "meta-llama/llama-4-maverick": 1_048_576,
    "meta-llama/llama-3.3-70b-instruct": 131_072,
    "meta-llama/llama-3.1-405b-instruct": 131_072,
    "meta-llama/llama-3.1-70b-instruct": 131_072,
    "meta-llama/llama-3.1-8b-instruct": 131_072,
    # Nvidia
    "nvidia/nemotron-3-super-120b-a12b:free": 32_768,
    # DeepSeek
    "deepseek/deepseek-chat-v3": 128_000,
    "deepseek/deepseek-chat": 64_000,
    "deepseek/deepseek-coder": 64_000,
    "deepseek/deepseek-r1": 128_000,
    # Mistral
    "mistralai/mistral-large": 128_000,
    "mistralai/mixtral-8x7b-instruct": 32_768,
    # Qwen
    "qwen/qwen-2.5-72b-instruct": 131_072,
    "qwen/qwen3-235b-a22b": 131_072,
}

# Default context window when model is unknown
DEFAULT_CONTEXT_WINDOW = 32_768

# Warn when usage exceeds this fraction of the context window
WARNING_THRESHOLD = 0.75

# Auto-compact when usage exceeds this fraction
AUTO_COMPACT_THRESHOLD = 0.85


class ContextManager:
    """Tracks token usage against model context limits."""

    def __init__(self, model: str, custom_limit: int | None = None):
        self.model = model
        self._custom_limit = custom_limit
        self._total_tokens = 0
        self._warned = False

    @property
    def context_limit(self) -> int:
        """Get the context window size for the current model."""
        if self._custom_limit:
            return self._custom_limit
        return MODEL_CONTEXT_WINDOWS.get(self.model, DEFAULT_CONTEXT_WINDOW)

    @property
    def usage_fraction(self) -> float:
        """Current usage as a fraction of the context window (0.0 to 1.0+)."""
        if self.context_limit == 0:
            return 0.0
        return self._total_tokens / self.context_limit

    @property
    def usage_percent(self) -> int:
        """Current usage as a percentage."""
        return int(self.usage_fraction * 100)

    def update(self, total_tokens: int) -> None:
        """Update the tracked token count."""
        self._total_tokens = total_tokens

    def estimate_messages_tokens(self, messages: list, estimator) -> int:
        """Estimate total tokens across all messages using the provided estimator function."""
        total = 0
        for msg in messages:
            content = msg.get("content", "") or ""
            total += estimator(content)
            # Account for tool calls in the message
            if msg.get("tool_calls"):
                total += estimator(json.dumps(msg["tool_calls"]))
        return total

    def set_model(self, model: str) -> None:
        """Update the model (resets warning state)."""
        self.model = model
        self._warned = False

    def check_and_warn(self) -> str | None:
        """
        Check usage against thresholds.
        Returns a warning message if threshold exceeded, None otherwise.
        """
        fraction = self.usage_fraction

        if fraction >= AUTO_COMPACT_THRESHOLD:
            return (
                f"⚠️  Context window is {self.usage_percent}% full "
                f"({self._total_tokens:,}/{self.context_limit:,} tokens). "
                f"Consider using /compact to free up space."
            )
        elif fraction >= WARNING_THRESHOLD and not self._warned:
            self._warned = True
            return (
                f"💡 Context window is {self.usage_percent}% full "
                f"({self._total_tokens:,}/{self.context_limit:,} tokens). "
                f"Use /compact if the conversation gets long."
            )
        return None

    def needs_auto_compact(self) -> bool:
        """Returns True if the conversation should be auto-compacted."""
        return self.usage_fraction >= AUTO_COMPACT_THRESHOLD

    def get_usage_bar(self, width: int = 20) -> str:
        """
        Generate a visual usage bar for the footer.

        Example: [████████░░░░░░░░░░░░] 42%
        """
        fraction = min(self.usage_fraction, 1.0)
        filled = int(width * fraction)
        empty = width - filled

        # Color coding based on usage
        if fraction >= AUTO_COMPACT_THRESHOLD:
            bar_char = "█"
            style = "bold red"
        elif fraction >= WARNING_THRESHOLD:
            bar_char = "█"
            style = "yellow"
        else:
            bar_char = "█"
            style = "green"

        bar = f"[{style}]{bar_char * filled}[/{style}][dim]{'░' * empty}[/dim]"
        return f"[{bar}] {self.usage_percent}%"

    def get_footer_text(self) -> str:
        """Get a compact footer string showing context usage."""
        return f"ctx: {self.get_usage_bar(10)}"
