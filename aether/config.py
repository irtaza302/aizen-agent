import os
import sys
import json
import time
import getpass
import threading
import urllib.request
from dotenv import load_dotenv
from rich.console import Console

# ─── Constants ──────────────────────────────────────────────────────────────────

VERSION = "2.1.0"
CONFIG_PATH = os.path.expanduser("~/.aether_config.json")
SESSIONS_DIR = os.path.expanduser("~/.aether_sessions")
BACKUPS_DIR = os.path.expanduser("~/.aether_backups")
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

AETHER_ASCII = r"""[bold magenta]
    ___       __  __               
   /   | ___ / /_/ /_  ___  _____  
  / /| |/ _ \ __/ __ \/ _ \/ ___/  
 / ___ /  __/ /_/ / / /  __/ /     
/_/  |_\___/\__/_/ /_/\___/_/      
[/bold magenta]
    [dim]by Irtaza Malik[/dim]
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
]

SYSTEM_PROMPT = """\
You are Aether, an expert AI coding assistant running in a user's terminal. \
You help users write, debug, understand, and refactor code with precision and care.

## Your Workflow
1. **Understand**: Always read relevant files first. Don't guess at file contents or structure.
2. **Plan**: Briefly explain your approach before making changes.
3. **Implement**: Make precise, targeted changes. Use `edit_file` for modifying existing files \
(surgical edits). Use `write_file` only for creating new files.
4. **Verify**: After making changes, read the modified file or run tests to confirm correctness.

## Guidelines
- Be concise but thorough in explanations.
- Use tools iteratively to explore and understand the codebase.
- Prefer small, focused changes over large rewrites.
- When modifying existing files, ALWAYS use `edit_file` with the exact `old_content` to replace. \
Never use `write_file` to modify existing files unless a full rewrite is truly needed.
- Run tests or linting commands after changes when applicable.
- If unsure about something, ask the user rather than guessing.
- Use fenced code blocks with language identifiers when showing code.

## Tool Preferences
- `edit_file` > `write_file` for modifications (surgical precision)
- `grep_search` for finding patterns across the codebase
- `find_files` for locating files by name
- `list_directory` for understanding project structure
- `run_command` for running tests, builds, and verification"""

console = Console()

# Global state for active model
active_model = DEFAULT_MODEL

def set_active_model(model_name: str):
    global active_model
    active_model = model_name

def get_active_model() -> str:
    return active_model

# ─── Configuration ──────────────────────────────────────────────────────────────

def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


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

    console.print(AETHER_ASCII)
    console.print("[bold]Welcome to Aether![/bold]\n")
    console.print("To get started, enter your OpenRouter API key.")
    console.print("[dim](Get one free at https://openrouter.ai/keys)[/dim]\n")

    key = getpass.getpass("API Key: ").strip()
    if not key:
        console.print("[bold red]Error:[/bold red] API Key cannot be empty.")
        sys.exit(1)

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
        url = "https://pypi.org/pypi/aether-ai-cli/json"
        req = urllib.request.Request(url, headers={"User-Agent": "aether-ai-cli"})
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode())
            latest = data["info"]["version"]

            # Update the last-check timestamp
            config["_last_update_check"] = time.time()
            config["_latest_version"] = latest
            try:
                save_config(config)
            except Exception:
                pass

            if latest != VERSION:
                console.print(
                    f"\n[bold magenta]🔔 Update available:[/bold magenta] v{VERSION} → v{latest}"
                )
                console.print("[dim]Run: pip install -U aether-ai-cli[/dim]\n")
    except Exception:
        pass  # Silently fail — never block the user


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
            console.print(
                f"[bold magenta]🔔 Update available:[/bold magenta] v{VERSION} → v{cached}"
            )
            console.print("[dim]Run: pip install -U aether-ai-cli[/dim]\n")
        return

    thread = threading.Thread(target=_do_update_check, args=(config,), daemon=True)
    thread.start()
