import getpass
import json
import logging
import os
import shutil
import ssl
import threading
import time
import urllib.error
import urllib.request
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from dotenv import load_dotenv
from rich.console import Console

logger = logging.getLogger("aizen")

# ─── Constants ──────────────────────────────────────────────────────────────────

# Read version from installed package metadata (stays in sync with pyproject.toml).
# Falls back to a hardcoded value only when running from source without installing.
_FALLBACK_VERSION = "2.4.1"
try:
    VERSION = _pkg_version("aizen-ai-cli")
except PackageNotFoundError:
    VERSION = _FALLBACK_VERSION
CONFIG_PATH = os.path.expanduser("~/.aizen_config.json")
SESSIONS_DIR = os.path.expanduser("~/.aizen_sessions")
BACKUPS_DIR = os.path.expanduser("~/.aizen_backups")
DEFAULT_MODEL = "openrouter/free"

# ─── Theme Colors ───────────────────────────────────────────────────────────────
# Centralized color palette — cyberpunk neon gradient
class Theme:
    PRIMARY = "#c084fc"       # Purple (main brand)
    SECONDARY = "#818cf8"     # Indigo (accents)
    ACCENT = "#22d3ee"        # Neon cyan (highlights)
    PINK = "#f472b6"          # Hot pink (flair)
    SUCCESS = "#4ade80"       # Vibrant green
    WARNING = "#fbbf24"       # Amber
    ERROR = "#f87171"         # Soft red
    MUTED = "#6b7280"         # Slate gray (dim text)
    TEXT = "#e2e8f0"          # Off-white (body text)
    BORDER = "#818cf8"        # Indigo (borders/frames)
    DIM_BORDER = "#4b5563"    # Dark gray (subtle borders)
    SURFACE = "#1e1b2e"       # Dark purple-black (backgrounds)

    # Badge prefix for system messages
    SYS = f"[bold {PRIMARY}]◈ SYS[/bold {PRIMARY}]"
    BADGE = f"[bold {ACCENT}]◆ AIZEN[/bold {ACCENT}]"


AIZEN_ASCII = """
[bold #c084fc]  ╔══════════════════════════════════════════════╗[/bold #c084fc]
[bold #c084fc]  ║[/bold #c084fc]                                              [bold #c084fc]║[/bold #c084fc]
[bold #c084fc]  ║[/bold #c084fc]  [bold #c084fc]█████[/bold #c084fc][bold #a78bfa]╗[/bold #a78bfa] [bold #a78bfa]██[/bold #a78bfa][bold #818cf8]╗[/bold #818cf8][bold #818cf8]███████[/bold #818cf8][bold #6366f1]╗[/bold #6366f1][bold #6366f1]███████[/bold #6366f1][bold #22d3ee]╗[/bold #22d3ee][bold #22d3ee]███╗   ██╗[/bold #22d3ee]  [bold #c084fc]║[/bold #c084fc]
[bold #c084fc]  ║[/bold #c084fc]  [bold #c084fc]██╔══██[/bold #c084fc][bold #a78bfa]╗[/bold #a78bfa][bold #a78bfa]██[/bold #a78bfa][bold #818cf8]║[/bold #818cf8][bold #818cf8]╚══███╔[/bold #818cf8][bold #6366f1]╝[/bold #6366f1][bold #6366f1]██╔════[/bold #6366f1][bold #22d3ee]╝[/bold #22d3ee][bold #22d3ee]████╗  ██║[/bold #22d3ee]  [bold #c084fc]║[/bold #c084fc]
[bold #c084fc]  ║[/bold #c084fc]  [bold #c084fc]█████[/bold #c084fc][bold #a78bfa]██║[/bold #a78bfa][bold #a78bfa]██[/bold #a78bfa][bold #818cf8]║[/bold #818cf8][bold #818cf8]  ███╔[/bold #818cf8][bold #6366f1]╝[/bold #6366f1] [bold #6366f1]█████[/bold #6366f1][bold #22d3ee]╗[/bold #22d3ee]  [bold #22d3ee]██╔██╗ ██║[/bold #22d3ee]  [bold #c084fc]║[/bold #c084fc]
[bold #c084fc]  ║[/bold #c084fc]  [bold #c084fc]██╔══██[/bold #c084fc][bold #a78bfa]║[/bold #a78bfa][bold #a78bfa]██[/bold #a78bfa][bold #818cf8]║[/bold #818cf8][bold #818cf8] ███╔[/bold #818cf8][bold #6366f1]╝[/bold #6366f1]  [bold #6366f1]██╔══[/bold #6366f1][bold #22d3ee]╝[/bold #22d3ee]  [bold #22d3ee]██║╚██╗██║[/bold #22d3ee]  [bold #c084fc]║[/bold #c084fc]
[bold #c084fc]  ║[/bold #c084fc]  [bold #c084fc]██║  ██[/bold #c084fc][bold #a78bfa]║[/bold #a78bfa][bold #a78bfa]██[/bold #a78bfa][bold #818cf8]║[/bold #818cf8][bold #818cf8]███████[/bold #818cf8][bold #6366f1]╗[/bold #6366f1][bold #6366f1]███████[/bold #6366f1][bold #22d3ee]╗[/bold #22d3ee][bold #22d3ee]██║ ╚████║[/bold #22d3ee]  [bold #c084fc]║[/bold #c084fc]
[bold #c084fc]  ║[/bold #c084fc]  [bold #c084fc]╚═╝  ╚═[/bold #c084fc][bold #a78bfa]╝[/bold #a78bfa][bold #a78bfa]╚═[/bold #a78bfa][bold #818cf8]╝[/bold #818cf8][bold #818cf8]╚══════[/bold #818cf8][bold #6366f1]╝[/bold #6366f1][bold #6366f1]╚══════[/bold #6366f1][bold #22d3ee]╝[/bold #22d3ee][bold #22d3ee]╚═╝  ╚═══╝[/bold #22d3ee]  [bold #c084fc]║[/bold #c084fc]
[bold #c084fc]  ║[/bold #c084fc]                                              [bold #c084fc]║[/bold #c084fc]
[bold #c084fc]  ║[/bold #c084fc]     [bold #22d3ee]⚡[/bold #22d3ee] [italic #e2e8f0]AI-Powered Coding Agent[/italic #e2e8f0] [bold #22d3ee]⚡[/bold #22d3ee]            [bold #c084fc]║[/bold #c084fc]
[bold #c084fc]  ║[/bold #c084fc]     [dim #6b7280]Made by Irtaza Malik[/dim #6b7280]                   [bold #c084fc]║[/bold #c084fc]
[bold #c084fc]  ╚══════════════════════════════════════════════╝[/bold #c084fc]
"""

# Safe commands that auto-execute without confirmation
SAFE_COMMAND_PREFIXES = [
    "ls", "cat", "head", "tail", "wc", "file",
    "git status", "git log", "git diff", "git branch", "git show", "git rev-parse",
    "pwd", "echo", "which", "type", "tree", "du", "df",
    "python --version", "python3 --version", "node --version",
    "npm --version", "pip --version", "pip list", "pip show",
    "cargo --version", "rustc --version", "go version",
    "date", "whoami", "uname", "printenv",
]

# Dangerous patterns that always require confirmation
DANGEROUS_PATTERNS = [
    r"\brm\s", r"\bsudo\b", r"\bchmod\b", r"\bchown\b", r"\bmkfs\b",
    r"\bdd\b", r":\(\)\{", r"\bkill\b", r"\bpkill\b", r"\bshutdown\b",
    r"\breboot\b", r">\s*/dev/", r"\bcurl\b.*\|\s*(ba)?sh",
    r"\bmktemp\b.*>", r"\btruncate\b", r"\bmv\s+/(?!tmp)", r"chmod\s+777",
    r"git\s+push\s+--force", r"\bdocker\s+run\b", r"\bpip\s+install\b", r"\bnpm\s+install\b",
    # Shell injection patterns
    r"`[^`]+`",           # Backtick command substitution
    r"\$\([^)]+\)",       # $() command substitution
    r"\beval\b",          # eval execution
    r"\bexec\b",          # exec execution
    r"\bsource\b",        # source execution
    r"\|\s*(ba)?sh\b",    # Pipe to shell
    r"\|\s*zsh\b",        # Pipe to zsh
    r"\|\s*python",       # Pipe to python
    r"\bwget\b.*\|\s*",   # wget piped to anything
    r"\bnohup\b",         # Background with nohup
]

SYSTEM_PROMPT = """\
You are Aizen, an expert AI coding assistant running in a user's terminal. \
You help users write, debug, understand, and refactor code with precision and care.

## Your Workflow
1. **Understand**: Always read relevant files first. Don't guess at file contents or structure.
2. **Plan**: Briefly explain your approach before making changes.
3. **Implement**: Make precise, targeted changes. Use `multi_replace_file_content` for editing multiple blocks \
or `replace_file_content` for single blocks (surgical edits). Provide line number bounds (start_line, end_line) \
to narrow the search area. Use `write_file` only for creating new files or fully overwriting.
4. **Verify**: After making changes, read the modified file or run tests to confirm correctness.

## Guidelines
- Be concise but thorough in explanations.
- Use tools iteratively to explore and understand the codebase.
- Prefer small, focused changes over large rewrites.
- When modifying existing files, ALWAYS use `replace_file_content` or `multi_replace_file_content` with line bounds. \
Never use `write_file` to modify existing files unless a full rewrite is truly needed.
- Run tests or linting commands after changes when applicable.
- If unsure about something, ask the user rather than guessing.
- Use fenced code blocks with language identifiers when showing code.

## Tool Preferences
- `multi_replace_file_content` / `replace_file_content` > `write_file` for modifications (surgical precision)
- `grep_search` for finding patterns across the codebase
- `find_files` for locating files by name
- `list_directory` for understanding project structure
- `run_command` for running tests, builds, and verification"""

console = Console()


# Project-level rules files (checked in order, first found wins)
_PROJECT_RULES_FILES = [".aizen_rules", ".cursorrules"]


def build_system_prompt(config: dict | None = None) -> str:
    """
    Build the final system prompt by merging:
    1. Default SYSTEM_PROMPT
    2. User override from ~/.aizen_config.json ("system_prompt" key)
    3. Project-specific rules from .aizen_rules or .cursorrules in CWD

    This allows per-project customization without modifying source code.
    """
    parts = []

    # Start with default or config override
    if config and config.get("system_prompt"):
        parts.append(config["system_prompt"])
    else:
        parts.append(SYSTEM_PROMPT)

    # Append project-specific rules if present
    for rules_file in _PROJECT_RULES_FILES:
        if os.path.isfile(rules_file):
            try:
                with open(rules_file, encoding="utf-8", errors="ignore") as f:
                    project_rules = f.read().strip()
                if project_rules:
                    parts.append(
                        f"\n\n## Project-Specific Rules\n"
                        f"The following rules are defined by the project maintainers "
                        f"(from {rules_file}):\n\n{project_rules}"
                    )
                    console.print(f"{Theme.SYS} Project rules loaded from [#d3fbff]{rules_file}[/#d3fbff]")
                break  # Only use the first rules file found
            except Exception as e:
                logger.debug("Failed to load project rules from %s: %s", rules_file, e)

    return "\n".join(parts)

# Global state for active model (protected by lock for thread safety)
active_model = DEFAULT_MODEL
_model_lock = threading.Lock()

def set_active_model(model_name: str, save: bool = False):
    global active_model
    with _model_lock:
        active_model = model_name
    if save:
        try:
            config = load_config()
            config["DEFAULT_MODEL"] = model_name
            with open(CONFIG_PATH, "w") as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            logger.error("Failed to save default model: %s", e)

def get_active_model() -> str:
    with _model_lock:
        return active_model

# ─── Configuration ──────────────────────────────────────────────────────────────


def migrate_legacy_data():
    """Migrate legacy Aether config/sessions to Aizen."""
    legacy_config = os.path.expanduser("~/.aether_config.json")
    if os.path.exists(legacy_config) and not os.path.exists(CONFIG_PATH):
        try:
            shutil.copy2(legacy_config, CONFIG_PATH)
            console.print("[dim]Migrated legacy config to ~/.aizen_config.json[/dim]")
        except Exception as e:
            logger.debug(f"Failed to migrate config: {e}")

    legacy_sessions = os.path.expanduser("~/.aether_sessions")
    if os.path.exists(legacy_sessions) and not os.path.exists(SESSIONS_DIR):
        try:
            shutil.copytree(legacy_sessions, SESSIONS_DIR)
            console.print("[dim]Migrated legacy sessions to ~/.aizen_sessions[/dim]")
        except Exception as e:
            logger.debug(f"Failed to migrate sessions: {e}")

def load_config() -> dict:
    migrate_legacy_data()

    config = {}
    # Load global config
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                config = json.load(f)
        except Exception as e:
            logger.debug("Failed to load global config file: %s", e)

    # Merge local config if present
    local_config_path = os.path.join(os.getcwd(), ".aizen_config.json")
    if os.path.exists(local_config_path):
        try:
            with open(local_config_path) as f:
                local_config = json.load(f)
                # Merge local config keys (overriding global ones)
                if isinstance(local_config, dict):
                    config.update(local_config)
                    console.print(f"{Theme.SYS} Local config loaded from [#d3fbff]{local_config_path}[/#d3fbff]")
        except Exception as e:
            logger.debug("Failed to load local config file: %s", e)

    return config


def get_mcp_servers(config: dict) -> dict:
    """Returns the configured MCP servers."""
    return config.get("mcp_servers", {})


def save_config(config: dict):
    try:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        console.print(f"[yellow]⚠️  Could not save config: {e}[/yellow]\n")


def get_api_key(config: dict, reset: bool = False) -> str:
    if reset:
        config.pop("OPENROUTER_API_KEY", None)
        save_config(config)

    key = config.get("OPENROUTER_API_KEY")
    if key:
        return key

    load_dotenv()
    env_key = os.getenv("OPENROUTER_API_KEY")
    if env_key and env_key != "your_api_key_here":
        return env_key

    console.print(AIZEN_ASCII)
    console.print("[bold]Welcome to Aizen![/bold]\n")
    console.print("To get started, enter your OpenRouter API key.")
    console.print("[dim](Get one free at https://openrouter.ai/keys)[/dim]\n")

    key = getpass.getpass("API Key: ").strip()
    if not key:
        from .exceptions import APIKeyError
        raise APIKeyError("API Key cannot be empty.")

    config["OPENROUTER_API_KEY"] = key
    save_config(config)
    console.print(f"[green]✓ API key saved to {CONFIG_PATH}[/green]\n")
    return key


# ─── Update Checker (Truly Non-Blocking) ────────────────────────────────────────

# Cache TTL: only check PyPI once every 24 hours
_UPDATE_CHECK_INTERVAL = 86400  # 24 hours in seconds


def _should_check_updates(config: dict) -> bool:
    """Determine if enough time has passed since the last update check."""
    last_check = config.get("_last_update_check", 0)
    return (time.time() - last_check) > _UPDATE_CHECK_INTERVAL


def _do_update_check(config: dict):
    """
    Background thread target: fetch latest version from PyPI
    and print a notice if an update is available.
    """
    try:
        ctx = ssl.create_default_context()
        try:
            import certifi
            ctx.load_verify_locations(cafile=certifi.where())
        except ImportError:
            ctx = ssl._create_unverified_context()

        url = "https://pypi.org/pypi/aizen-ai-cli/json"
        req = urllib.request.Request(url, headers={"User-Agent": "aizen-ai-cli"})
        with urllib.request.urlopen(req, timeout=3, context=ctx) as response:
            data = json.loads(response.read().decode())
            latest = data["info"]["version"]

            # Update the last-check timestamp
            config["_last_update_check"] = time.time()
            config["_latest_version"] = latest
            try:
                save_config(config)
            except Exception as e:
                logger.debug("Failed to save config after update check: %s", e)

            try:
                latest_parts = tuple(map(int, latest.split('.')))
                current_parts = tuple(map(int, VERSION.split('.')))
                is_newer = latest_parts > current_parts
            except Exception:
                is_newer = latest != VERSION

            if is_newer:
                console.print(
                    f"\n[bold magenta]🔔 Update available:[/bold magenta] v{VERSION} → v{latest}"
                )
                console.print("[dim]Run: pip install -U aizen-ai-cli (or brew upgrade aizen)[/dim]")
                console.print("[dim]Then restart Aizen to use the new version![/dim]\n"
                )
    except Exception as e:
        logger.debug("Update check failed (network/parsing): %s", e)


def check_for_updates(config: dict | None = None):
    """
    Launch a non-blocking background thread to check for updates.
    Respects a 24-hour cache to avoid repeated network calls.
    """
    if config is None:
        config = load_config()

    if not _should_check_updates(config):
        # Check if we have a cached latest version that's newer
        cached = config.get("_latest_version")
        if cached and cached != VERSION:
            try:
                cached_parts = tuple(map(int, cached.split('.')))
                current_parts = tuple(map(int, VERSION.split('.')))
                is_newer = cached_parts > current_parts
            except Exception:
                is_newer = cached != VERSION

            if is_newer:
                console.print(
                    f"\n[bold magenta]🔔 Update available:[/bold magenta] v{VERSION} → v{cached}"
                )
                console.print("[dim]Run: pip install -U aizen-ai-cli (or brew upgrade aizen)[/dim]")
                console.print("[dim]Then restart Aizen to use the new version![/dim]\n"
                )
        return

    thread = threading.Thread(target=_do_update_check, args=(config,), daemon=True)
    thread.start()

# ─── OpenRouter Models Cache ────────────────────────────────────────────────────

MODELS_CACHE_PATH = os.path.expanduser("~/.aizen_models.json")
_MODELS_CACHE_TTL = 86400  # 24 hours

def get_cached_models() -> list[dict]:
    if os.path.exists(MODELS_CACHE_PATH):
        try:
            with open(MODELS_CACHE_PATH, encoding="utf-8") as f:
                data = json.load(f)
                if time.time() - data.get("timestamp", 0) < _MODELS_CACHE_TTL:
                    return data.get("models", [])
        except Exception as e:
            logger.debug("Failed to load models cache: %s", e)
    return []

def _do_fetch_models():
    try:
        ctx = ssl.create_default_context()
        try:
            import certifi
            ctx.load_verify_locations(cafile=certifi.where())
        except ImportError:
            ctx = ssl._create_unverified_context()

        req = urllib.request.Request("https://openrouter.ai/api/v1/models")
        with urllib.request.urlopen(req, timeout=5, context=ctx) as response:
            data = json.loads(response.read().decode())
            models = data.get("data", [])
            simplified_models = []
            for m in models:
                simplified_models.append({
                    "id": m.get("id"),
                    "name": m.get("name"),
                    "context_length": m.get("context_length", "Unknown"),
                    "pricing": m.get("pricing", {})
                })

            with open(MODELS_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump({"timestamp": time.time(), "models": simplified_models}, f)
    except Exception as e:
        logger.debug("Failed to fetch OpenRouter models: %s", e)

def fetch_openrouter_models_bg():
    """Fetches OpenRouter models in the background if the cache is stale."""
    if os.path.exists(MODELS_CACHE_PATH):
        try:
            with open(MODELS_CACHE_PATH, encoding="utf-8") as f:
                data = json.load(f)
                if time.time() - data.get("timestamp", 0) < _MODELS_CACHE_TTL:
                    return
        except Exception:
            pass

    thread = threading.Thread(target=_do_fetch_models, daemon=True)
    thread.start()
