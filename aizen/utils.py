import os
import time
import shutil
import fnmatch
import re
import urllib.request
from datetime import datetime
from rich.table import Table

from .config import BACKUPS_DIR
from .logging_config import logger

# ─── Optional tiktoken for accurate token counting ─────────────────────────────

_tiktoken_encoding = None

def _get_tiktoken_encoding():
    """Lazily load tiktoken for accurate token counting. Falls back to estimation."""
    global _tiktoken_encoding
    if _tiktoken_encoding is not None:
        return _tiktoken_encoding

    try:
        import tiktoken  # pyrefly: ignore[missing-import]
        # cl100k_base covers GPT-4, Claude, and most modern models
        _tiktoken_encoding = tiktoken.get_encoding("cl100k_base")
    except ImportError:
        _tiktoken_encoding = False  # Mark as unavailable
    return _tiktoken_encoding


class Struct:
    """Simple namespace for converting dicts to attribute-access objects."""
    def __init__(self, **entries):
        self.__dict__.update(entries)


class TokenTracker:
    """Track token usage and session statistics with accurate or estimated counting."""

    def __init__(self):
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.api_reported_input: int = 0
        self.api_reported_output: int = 0
        self.message_count: int = 0
        self.start_time: datetime = datetime.now()
        self._using_api_usage: bool = False

    def add_usage(self, input_tokens: int, output_tokens: int):
        """Add estimated token usage for a single exchange."""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.message_count += 1

    def add_api_usage(self, prompt_tokens: int, completion_tokens: int):
        """
        Add API-reported token usage (more accurate than estimation).
        When API usage is available, it takes precedence in the summary.
        """
        self.api_reported_input += prompt_tokens
        self.api_reported_output += completion_tokens
        self._using_api_usage = True
        self.message_count += 1

    def estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for a given text.
        Uses tiktoken if available, falls back to word-based heuristic.
        """
        if not text:
            return 0

        enc = _get_tiktoken_encoding()
        if enc and enc is not False:
            try:
                return len(enc.encode(text))
            except Exception as e:
                logger.debug("tiktoken encoding failed: %s", e)

        # Fallback: ~1.3 tokens per word (rough but usable)
        return max(1, int(len(text.split()) * 1.3))

    @property
    def total_tokens(self) -> int:
        if self._using_api_usage:
            return self.api_reported_input + self.api_reported_output
        return self.total_input_tokens + self.total_output_tokens

    @property
    def input_tokens(self) -> int:
        if self._using_api_usage:
            return self.api_reported_input
        return self.total_input_tokens

    @property
    def output_tokens(self) -> int:
        if self._using_api_usage:
            return self.api_reported_output
        return self.total_output_tokens
        
    def get_estimated_cost(self, active_model: str) -> float:
        """Returns the estimated cost in USD for the current session."""
        return get_model_cost(active_model, self.input_tokens, self.output_tokens)

    @property
    def session_duration(self) -> str:
        delta = datetime.now() - self.start_time
        minutes = int(delta.total_seconds() // 60)
        seconds = int(delta.total_seconds() % 60)
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def get_summary_table(self, active_model: str = "") -> Table:
        source = "API-reported" if self._using_api_usage else "Estimated"
        enc = _get_tiktoken_encoding()
        method = "tiktoken" if (enc and enc is not False and not self._using_api_usage) else "heuristic"

        table = Table(title="📊 Session Usage", border_style="magenta", show_header=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")
        table.add_row("Messages", str(self.message_count))
        table.add_row("Input Tokens", f"{self.input_tokens:,}")
        table.add_row("Output Tokens", f"{self.output_tokens:,}")
        table.add_row("Total Tokens", f"{self.total_tokens:,}")
        
        if active_model:
            cost = get_model_cost(active_model, self.input_tokens, self.output_tokens)
            if cost > 0:
                table.add_row("Est. Cost", f"${cost:.4f} USD")
            else:
                table.add_row("Est. Cost", "Unknown (Pricing not in DB)")

        table.add_row("Counting Method", f"{source} ({method})")
        table.add_row("Session Duration", self.session_duration)
        return table

# Pricing per 1,000,000 tokens (input, output) in USD
MODEL_PRICING = {
    # Anthropic
    "anthropic/claude-sonnet-4": (3.00, 15.00),
    "anthropic/claude-3.5-sonnet": (3.00, 15.00),
    "anthropic/claude-3.7-sonnet": (3.00, 15.00),
    "anthropic/claude-3-opus": (15.00, 75.00),
    "anthropic/claude-3.5-haiku": (0.80, 4.00),
    "anthropic/claude-4-opus": (15.00, 75.00),
    # Google
    "google/gemini-2.5-pro": (1.25, 10.00),
    "google/gemini-2.5-flash": (0.15, 0.60),
    "google/gemini-2.0-flash": (0.10, 0.40),
    # OpenAI
    "openai/gpt-4o": (2.50, 10.00),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4.1": (2.00, 8.00),
    "openai/gpt-4.1-mini": (0.40, 1.60),
    "openai/gpt-4.1-nano": (0.10, 0.40),
    "openai/o1": (15.00, 60.00),
    "openai/o3": (10.00, 40.00),
    "openai/o3-mini": (1.10, 4.40),
    "openai/o4-mini": (1.10, 4.40),
    # DeepSeek
    "deepseek/deepseek-chat-v3": (0.27, 1.10),
    "deepseek/deepseek-chat": (0.14, 0.28),
    "deepseek/deepseek-r1": (0.55, 2.19),
    # Meta
    "meta-llama/llama-4-maverick": (0.20, 0.60),
    "meta-llama/llama-3.3-70b-instruct": (0.10, 0.25),
}

def get_model_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate the estimated cost for a given model and token count."""
    for known_model, (in_price, out_price) in MODEL_PRICING.items():
        if known_model in model_name.lower():
            return (input_tokens * in_price / 1_000_000) + (output_tokens * out_price / 1_000_000)
    return 0.0


class BackupManager:
    """Manage file backups for undo operations."""

    def __init__(self):
        os.makedirs(BACKUPS_DIR, exist_ok=True)
        self.undo_stack: list[tuple[str, str, str]] = []  # (original_path, backup_path, timestamp)

    def backup(self, filepath: str) -> str | None:
        """Create a backup before modification. Returns backup path."""
        if not os.path.exists(filepath):
            return None
        try:
            abs_path = os.path.abspath(filepath)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            basename = os.path.basename(filepath)
            backup_name = f"{timestamp}_{basename}"
            backup_path = os.path.join(BACKUPS_DIR, backup_name)
            shutil.copy2(abs_path, backup_path)
            self.undo_stack.append((abs_path, backup_path, timestamp))
            return backup_path
        except Exception as e:
            logger.debug("Failed to backup file %s: %s", filepath, e)
            return None

    def undo(self) -> str:
        """Undo the last file modification."""
        if not self.undo_stack:
            return "Nothing to undo."

        original_path, backup_path, _ = self.undo_stack.pop()

        if not os.path.exists(backup_path):
            return f"Backup file not found: {backup_path}"

        try:
            shutil.copy2(backup_path, original_path)
            os.remove(backup_path)
            return f"✓ Restored {os.path.basename(original_path)} to its previous state."
        except Exception as e:
            return f"Error restoring file: {e}"

    def cleanup(self, max_age_hours: int = 24):
        """Remove backups older than max_age_hours."""
        try:
            now = time.time()
            for f in os.listdir(BACKUPS_DIR):
                path = os.path.join(BACKUPS_DIR, f)
                if os.path.isfile(path):
                    age_hours = (now - os.path.getmtime(path)) / 3600
                    if age_hours > max_age_hours:
                        os.remove(path)
        except Exception as e:
            logger.debug("Cleanup backups failed: %s", e)


def truncate_output(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return (
        f"{text[:half]}\n\n"
        f"[... TRUNCATED {len(text) - max_chars} chars ...]\n\n"
        f"{text[-half:]}"
    )


def load_gitignore_patterns() -> list:
    patterns = [
        ".git/", "node_modules/", "__pycache__/", "venv/", ".env",
        "dist/", "build/", "*.egg-info/", ".DS_Store",
    ]
    if os.path.exists(".gitignore"):
        try:
            with open(".gitignore", "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line)
        except Exception as e:
            logger.debug("Failed to load .gitignore: %s", e)
    return patterns


def should_ignore(path: str, patterns: list) -> bool:
    path = os.path.normpath(path)
    parts = path.split(os.sep)
    for pattern in patterns:
        clean_pattern = pattern.rstrip("/")
        for part in parts:
            if fnmatch.fnmatch(part, clean_pattern):
                return True
        if fnmatch.fnmatch(path, clean_pattern):
            return True
    return False


def fetch_url_content(url: str, timeout: int = 10) -> str:
    """Fetch content from a URL and strip HTML tags if it's a webpage."""
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AizenAgent'}
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content_type = response.headers.get_content_type()
            charset = response.headers.get_content_charset() or 'utf-8'
            raw_data = response.read().decode(charset, errors='replace')
            
            if 'html' in content_type:
                # Basic HTML stripping
                # Remove script and style elements
                text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', raw_data, flags=re.IGNORECASE | re.DOTALL)
                # Remove HTML tags
                text = re.sub(r'<[^>]+>', ' ', text)
                # Unescape common HTML entities
                text = text.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&').replace('&quot;', '"')
                # Collapse whitespace
                text = re.sub(r'\s+', ' ', text).strip()
                return text
            
            return raw_data
    except Exception as e:
        return f"Error fetching URL: {e}"


def generate_directory_tree(path: str) -> str:
    """Generate a formatted text tree of a directory, respecting .gitignore."""
    ignore_patterns = load_gitignore_patterns()
    
    lines = []
    
    def _walk(current_path: str, prefix: str = ""):
        try:
            entries = sorted(os.listdir(current_path))
        except PermissionError:
            lines.append(f"{prefix}[Permission Denied]")
            return
        
        # Filter entries
        valid_entries = []
        for entry in entries:
            full_path = os.path.join(current_path, entry)
            if not should_ignore(full_path, ignore_patterns):
                valid_entries.append((entry, full_path))
                
        for i, (entry, full_path) in enumerate(valid_entries):
            is_last = i == (len(valid_entries) - 1)
            connector = "└── " if is_last else "├── "
            
            if os.path.isdir(full_path):
                lines.append(f"{prefix}{connector}{entry}/")
                new_prefix = prefix + ("    " if is_last else "│   ")
                _walk(full_path, new_prefix)
            else:
                lines.append(f"{prefix}{connector}{entry}")
                
    base_name = os.path.basename(os.path.abspath(path)) or path
    lines.append(f"{base_name}/")
    _walk(path)
    return "\n".join(lines)
