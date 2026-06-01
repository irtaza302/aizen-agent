#!/usr/bin/env python3
"""
Aether AI Agent v2.0 — A professional-grade AI coding assistant for your terminal.
"""

import os
import sys
import json
import subprocess
import re
import glob
import getpass
import argparse
import urllib.request
import fnmatch
import difflib
import random
import time
import shutil
import platform
from datetime import datetime
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.filters import has_completions, completion_is_selected
from dotenv import load_dotenv
from openai import OpenAI
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich.rule import Rule
from rich.live import Live
from rich.table import Table
from rich.syntax import Syntax

# ─── Constants ──────────────────────────────────────────────────────────────────

VERSION = "2.0.1"
CONFIG_PATH = os.path.expanduser("~/.aether_config.json")
SESSIONS_DIR = os.path.expanduser("~/.aether_sessions")
BACKUPS_DIR = os.path.expanduser("~/.aether_backups")
MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

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

# ─── Utility Classes ────────────────────────────────────────────────────────────

class Struct:
    """Simple namespace for converting dicts to attribute-access objects."""
    def __init__(self, **entries):
        self.__dict__.update(entries)


class TokenTracker:
    """Track token usage and session statistics."""

    def __init__(self):
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.message_count = 0
        self.start_time = datetime.now()

    def add_usage(self, input_tokens: int, output_tokens: int):
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.message_count += 1

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimate: ~1.3 tokens per word."""
        if not text:
            return 0
        return max(1, int(len(text.split()) * 1.3))

    @property
    def total_tokens(self):
        return self.total_input_tokens + self.total_output_tokens

    @property
    def session_duration(self):
        delta = datetime.now() - self.start_time
        minutes = int(delta.total_seconds() // 60)
        seconds = int(delta.total_seconds() % 60)
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def get_summary_table(self) -> Table:
        table = Table(title="📊 Session Usage", border_style="magenta", show_header=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")
        table.add_row("Messages", str(self.message_count))
        table.add_row("Input Tokens", f"{self.total_input_tokens:,}")
        table.add_row("Output Tokens", f"{self.total_output_tokens:,}")
        table.add_row("Total Tokens", f"{self.total_tokens:,}")
        table.add_row("Session Duration", self.session_duration)
        return table


class BackupManager:
    """Manage file backups for undo operations."""

    def __init__(self):
        os.makedirs(BACKUPS_DIR, exist_ok=True)
        self.undo_stack = []  # (original_path, backup_path, timestamp)

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
        except Exception:
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
        except Exception:
            pass


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


def check_for_updates():
    try:
        url = "https://pypi.org/pypi/aether-ai-cli/json"
        req = urllib.request.Request(url, headers={"User-Agent": "aether-ai-cli"})
        with urllib.request.urlopen(req, timeout=0.8) as response:
            data = json.loads(response.read().decode())
            latest = data["info"]["version"]
            if latest != VERSION:
                console.print(
                    f"[bold magenta]🔔 Update available:[/bold magenta] v{latest}"
                )
                console.print("[dim]Run: pip install -U aether-ai-cli[/dim]\n")
    except Exception:
        pass


# ─── Session Management ────────────────────────────────────────────────────────

def save_session(
    messages: list, name: str = None, token_tracker: TokenTracker = None
) -> str:
    os.makedirs(SESSIONS_DIR, exist_ok=True)

    if not name:
        name = datetime.now().strftime("session_%Y%m%d_%H%M%S")

    # Sanitize
    name = re.sub(r"[^\w\-]", "_", name)
    filepath = os.path.join(SESSIONS_DIR, f"{name}.json")

    session_data = {
        "name": name,
        "saved_at": datetime.now().isoformat(),
        "message_count": len(messages),
        "messages": messages,
    }

    if token_tracker:
        session_data["tokens"] = {
            "input": token_tracker.total_input_tokens,
            "output": token_tracker.total_output_tokens,
        }

    with open(filepath, "w") as f:
        json.dump(session_data, f, indent=2)

    return filepath


def load_session(name: str) -> list | None:
    filepath = os.path.join(SESSIONS_DIR, f"{name}.json")
    if not os.path.exists(filepath):
        filepath = os.path.join(SESSIONS_DIR, name)
        if not os.path.exists(filepath):
            return None

    try:
        with open(filepath, "r") as f:
            data = json.load(f)
        return data.get("messages", [])
    except Exception:
        return None


def list_sessions() -> list:
    if not os.path.exists(SESSIONS_DIR):
        return []

    sessions = []
    for f in sorted(os.listdir(SESSIONS_DIR), reverse=True):
        if f.endswith(".json"):
            try:
                filepath = os.path.join(SESSIONS_DIR, f)
                with open(filepath, "r") as fh:
                    data = json.load(fh)
                sessions.append(
                    {
                        "name": data.get("name", f),
                        "saved_at": data.get("saved_at", "unknown"),
                        "messages": data.get("message_count", 0),
                    }
                )
            except Exception:
                pass
    return sessions


# ─── Autocomplete & Gitignore ───────────────────────────────────────────────────

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
        except Exception:
            pass
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


SLASH_COMMANDS = [
    ("/help", "Show all available commands"),
    ("/model", "View or switch the active model"),
    ("/clear", "Clear conversation history"),
    ("/save", "Save current conversation"),
    ("/load", "Load a saved conversation"),
    ("/usage", "Show token usage statistics"),
    ("/compact", "Summarize conversation to save tokens"),
    ("/undo", "Undo the last file modification"),
    ("/retry", "Retry the last user message"),
    ("/copy", "Copy last AI response to clipboard"),
    ("/export", "Export conversation to Markdown"),
    ("/config", "View current configuration"),
]


class AetherCompleter(Completer):
    """Autocomplete for both slash commands (/) and file mentions (@)."""

    def __init__(self):
        super().__init__()
        self.ignore_patterns = load_gitignore_patterns()

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        stripped = text.lstrip()

        # ── Slash command completion ──
        # Only complete if '/' is the very first character typed (start of input)
        if stripped.startswith("/"):
            # Don't complete if there's already a space (user is typing args)
            if " " not in stripped:
                query = stripped.lower()
                for cmd, description in SLASH_COMMANDS:
                    if cmd.startswith(query):
                        yield Completion(
                            cmd,
                            start_position=-len(stripped),
                            display=cmd,
                            display_meta=description,
                        )
            return

        # ── File mention completion (@) ──
        words = text.split()
        if not words:
            return

        current = words[-1]
        if not current.startswith("@"):
            return

        query = current[1:]

        # Support directory traversal
        if "/" in query:
            dir_part = os.path.dirname(query)
            base_part = os.path.basename(query)
            search_dir = dir_part if dir_part else "."
            if os.path.isdir(search_dir):
                try:
                    for item in sorted(os.listdir(search_dir)):
                        item_path = os.path.join(search_dir, item)
                        if item.lower().startswith(base_part.lower()):
                            if not should_ignore(item_path, self.ignore_patterns):
                                display = os.path.join(dir_part, item)
                                if os.path.isdir(item_path):
                                    display += "/"
                                yield Completion(
                                    display, start_position=-len(query)
                                )
                except Exception:
                    pass
        else:
            try:
                for item in sorted(os.listdir(".")):
                    if item.lower().startswith(query.lower()):
                        item_path = item
                        if not should_ignore(item_path, self.ignore_patterns):
                            if os.path.isdir(item):
                                yield Completion(
                                    item + "/", start_position=-len(query)
                                )
                            elif os.path.isfile(item):
                                yield Completion(
                                    item, start_position=-len(query)
                                )
            except Exception:
                pass


# ─── Tools Definition ──────────────────────────────────────────────────────────

tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Reads the contents of a file. Use this to understand code before making changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the file to read.",
                    }
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Creates a new file or fully overwrites an existing one. For modifying existing files, prefer edit_file instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the file to create/overwrite.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The full content to write.",
                    },
                },
                "required": ["filepath", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Makes a surgical edit to an existing file by replacing a specific block of text with new text. Always use this instead of write_file when modifying existing files. The old_content must match exactly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the file to edit.",
                    },
                    "old_content": {
                        "type": "string",
                        "description": "The exact existing text block to find and replace. Must match the file content exactly.",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "The replacement text.",
                    },
                },
                "required": ["filepath", "old_content", "new_content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Executes a shell command. Safe read-only commands run automatically; destructive commands require user confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "Lists files and folders in a directory, respecting .gitignore patterns. Shows file sizes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list (defaults to '.').",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_search",
            "description": "Searches for a text or regex pattern in files under a directory. Returns matching lines with file paths and line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The text or regex pattern to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in (defaults to '.').",
                    },
                    "is_regex": {
                        "type": "boolean",
                        "description": "If true, treats query as a regex pattern. Default: false.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Finds files by name pattern (glob) across the workspace. Use this to locate files when you don't know the exact path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to match filenames (e.g., '*.py', 'test_*.js', 'Dockerfile').",
                    },
                    "path": {
                        "type": "string",
                        "description": "Root directory to search from (defaults to '.').",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
]


# ─── Tool Implementations ──────────────────────────────────────────────────────

backup_manager = BackupManager()


def read_file(filepath: str) -> str:
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return f"[File: {filepath} | {line_count} lines]\n{content}"
    except Exception as e:
        return f"Error reading file: {e}"


def write_file_with_diff(filepath: str, content: str) -> str:
    try:
        old_content = ""
        exists = os.path.exists(filepath)

        if exists:
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    old_content = f.read()
            except Exception:
                pass

        if exists:
            diff = list(
                difflib.unified_diff(
                    old_content.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=f"a/{filepath}",
                    tofile=f"b/{filepath}",
                    n=3,
                )
            )
            if not diff:
                return f"No changes to write for {filepath}"

            console.print(
                Panel(
                    f"[bold magenta]Aether wants to overwrite:[/bold magenta] [cyan]{filepath}[/cyan]",
                    border_style="magenta",
                )
            )
            for line in diff:
                if line.startswith("+") and not line.startswith("+++"):
                    console.print(f"[green]{line.rstrip()}[/green]")
                elif line.startswith("-") and not line.startswith("---"):
                    console.print(f"[red]{line.rstrip()}[/red]")
                elif line.startswith("@@"):
                    console.print(f"[cyan]{line.rstrip()}[/cyan]")
                else:
                    console.print(line.rstrip())
        else:
            preview_lines = content.split("\n")[:15]
            preview = "\n".join(preview_lines)
            total_lines = len(content.split("\n"))
            if total_lines > 15:
                preview += f"\n... ({total_lines} total lines)"
            console.print(
                Panel(
                    f"[bold magenta]Aether wants to create:[/bold magenta] [cyan]{filepath}[/cyan]\n\n"
                    f"[dim]{preview}[/dim]",
                    border_style="magenta",
                )
            )

        confirmation = input("  Allow? (y/n): ").strip().lower()
        if confirmation != "y":
            return "User denied file write operation."

        # Create backup before overwriting
        if exists:
            backup_manager.backup(filepath)

        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return f"✓ Successfully wrote to {filepath}"
    except Exception as e:
        return f"Error writing file: {e}"


def edit_file(filepath: str, old_content: str, new_content: str) -> str:
    try:
        if not os.path.exists(filepath):
            return f"Error: File '{filepath}' does not exist. Use write_file to create new files."

        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            file_content = f.read()

        # Check if old_content exists in the file
        occurrence_count = file_content.count(old_content)
        if occurrence_count == 0:
            # Try with normalized whitespace as a hint
            normalized_file = re.sub(r"[ \t]+", " ", file_content)
            normalized_old = re.sub(r"[ \t]+", " ", old_content)
            if normalized_old in normalized_file:
                return (
                    "Error: Exact match not found, but a similar block exists with different whitespace. "
                    "Please re-read the file and use the exact text including whitespace."
                )
            return (
                f"Error: Could not find the specified text in {filepath}. "
                f"Please read the file first to get the exact content."
            )

        if occurrence_count > 1:
            return (
                f"Error: Found {occurrence_count} occurrences of the target text in {filepath}. "
                f"Please provide a more specific/unique block to match exactly one location."
            )

        # Show diff preview
        new_file_content = file_content.replace(old_content, new_content, 1)
        diff = list(
            difflib.unified_diff(
                file_content.splitlines(keepends=True),
                new_file_content.splitlines(keepends=True),
                fromfile=f"a/{filepath}",
                tofile=f"b/{filepath}",
                n=3,
            )
        )

        if not diff:
            return "No changes detected."

        console.print(
            Panel(
                f"[bold magenta]Aether wants to edit:[/bold magenta] [cyan]{filepath}[/cyan]",
                border_style="magenta",
            )
        )
        for line in diff:
            if line.startswith("+") and not line.startswith("+++"):
                console.print(f"[green]{line.rstrip()}[/green]")
            elif line.startswith("-") and not line.startswith("---"):
                console.print(f"[red]{line.rstrip()}[/red]")
            elif line.startswith("@@"):
                console.print(f"[cyan]{line.rstrip()}[/cyan]")
            else:
                console.print(line.rstrip())

        confirmation = input("  Apply edit? (y/n): ").strip().lower()
        if confirmation != "y":
            return "User denied the edit."

        # Create backup
        backup_manager.backup(filepath)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_file_content)

        return f"✓ Successfully edited {filepath}"
    except Exception as e:
        return f"Error editing file: {e}"


def is_command_safe(command: str) -> bool:
    """Check if a command is safe to auto-execute without confirmation."""
    cmd_stripped = command.strip()

    # Check dangerous patterns first
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, cmd_stripped):
            return False

    # Check safe prefixes
    for safe in SAFE_COMMAND_PREFIXES:
        if cmd_stripped == safe or cmd_stripped.startswith(safe + " "):
            return True

    return False


def run_command_impl(command: str, auto_approve: bool = False) -> str:
    safe = is_command_safe(command)

    if not safe and not auto_approve:
        console.print(
            Panel(
                f"[bold magenta]Aether wants to run:[/bold magenta]\n\n[white]{command}[/white]",
                border_style="magenta",
            )
        )
        confirmation = input("  Allow? (y/n): ").strip().lower()
        if confirmation != "y":
            return "User denied command execution."
    elif safe:
        console.print(f"  [dim]▶ {command}[/dim]")

    try:
        result = subprocess.run(
            command,
            shell=True,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if output:
                output += f"\nSTDERR:\n{result.stderr}"
            else:
                output = result.stderr
        if result.returncode != 0:
            output += f"\n[Exit code: {result.returncode}]"
        return output.strip() if output.strip() else f"Command completed (exit code {result.returncode})"
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 120 seconds."
    except Exception as e:
        return f"Error executing command: {e}"


def list_directory(path: str = ".") -> str:
    try:
        if not path:
            path = "."
        if not os.path.exists(path):
            return f"Error: Path '{path}' does not exist."
        if not os.path.isdir(path):
            return f"Error: '{path}' is not a directory."

        items = os.listdir(path)
        ignore_patterns = load_gitignore_patterns()

        dirs = []
        files = []
        for item in sorted(items):
            item_path = os.path.join(path, item)
            if should_ignore(item_path, ignore_patterns):
                continue
            if os.path.isdir(item_path):
                dirs.append(f"📁 {item}/")
            else:
                try:
                    size = os.path.getsize(item_path)
                    if size < 1024:
                        size_str = f"{size}B"
                    elif size < 1024 * 1024:
                        size_str = f"{size / 1024:.1f}KB"
                    else:
                        size_str = f"{size / 1024 / 1024:.1f}MB"
                    files.append(f"📄 {item} ({size_str})")
                except OSError:
                    files.append(f"📄 {item}")

        if not dirs and not files:
            return f"Directory '{path}' is empty or all contents are ignored."

        result = ""
        if dirs:
            result += "\n".join(dirs)
        if files:
            if result:
                result += "\n"
            result += "\n".join(files)
        return result
    except Exception as e:
        return f"Error listing directory: {e}"


def grep_search(query: str, path: str = ".", is_regex: bool = False) -> str:
    try:
        if not path:
            path = "."
        if not os.path.exists(path):
            return f"Error: Path '{path}' does not exist."

        if is_regex:
            try:
                pattern = re.compile(query, re.IGNORECASE)
            except re.error as e:
                return f"Invalid regex pattern: {e}"

        ignore_patterns = load_gitignore_patterns()
        matches = []

        for root, dirs, files in os.walk(path):
            dirs[:] = [
                d
                for d in dirs
                if not should_ignore(os.path.join(root, d), ignore_patterns)
            ]

            for file in files:
                file_path = os.path.join(root, file)
                if should_ignore(file_path, ignore_patterns):
                    continue
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, 1):
                            matched = False
                            if is_regex:
                                matched = bool(pattern.search(line))
                            else:
                                matched = query.lower() in line.lower()

                            if matched:
                                matches.append(
                                    f"{file_path}:{line_num}: {line.strip()}"
                                )
                                if len(matches) >= 50:
                                    return (
                                        "\n".join(matches)
                                        + "\n\n(Showing first 50 results)"
                                    )
                except (UnicodeDecodeError, PermissionError, OSError):
                    pass

        if not matches:
            return f"No matches found for '{query}'."
        return "\n".join(matches)
    except Exception as e:
        return f"Error searching: {e}"


def find_files(pattern: str, path: str = ".") -> str:
    try:
        if not path:
            path = "."
        if not os.path.exists(path):
            return f"Error: Path '{path}' does not exist."

        ignore_patterns = load_gitignore_patterns()
        matches = []

        for root, dirs, files in os.walk(path):
            dirs[:] = [
                d
                for d in dirs
                if not should_ignore(os.path.join(root, d), ignore_patterns)
            ]

            for file in files:
                if fnmatch.fnmatch(file, pattern) or fnmatch.fnmatch(
                    file.lower(), pattern.lower()
                ):
                    file_path = os.path.join(root, file)
                    if not should_ignore(file_path, ignore_patterns):
                        matches.append(file_path)
                        if len(matches) >= 100:
                            return (
                                "\n".join(matches) + "\n\n(Showing first 100 results)"
                            )

        if not matches:
            return f"No files matching '{pattern}' found."
        return "\n".join(matches)
    except Exception as e:
        return f"Error finding files: {e}"


def truncate_output(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return (
        f"{text[:half]}\n\n"
        f"[... TRUNCATED {len(text) - max_chars} chars ...]\n\n"
        f"{text[-half:]}"
    )


# ─── Tool Dispatcher ───────────────────────────────────────────────────────────


def execute_tool(tool_call, auto_approve: bool = False) -> str:
    func_name = tool_call.function.name
    try:
        args = json.loads(tool_call.function.arguments)
    except json.JSONDecodeError:
        return "Error: Invalid JSON in tool arguments."

    tool_label = Text("  ⚙️  ", style="magenta")
    tool_label.append(func_name, style="dim magenta")

    if func_name == "read_file":
        tool_label.append(f" → {args.get('filepath', '?')}", style="dim")
        console.print(tool_label)
        return truncate_output(read_file(args.get("filepath")))

    elif func_name == "write_file":
        tool_label.append(f" → {args.get('filepath', '?')}", style="dim")
        console.print(tool_label)
        return write_file_with_diff(args.get("filepath"), args.get("content"))

    elif func_name == "edit_file":
        tool_label.append(f" → {args.get('filepath', '?')}", style="dim")
        console.print(tool_label)
        return edit_file(
            args.get("filepath"), args.get("old_content"), args.get("new_content")
        )

    elif func_name == "run_command":
        tool_label.append(f" → {args.get('command', '?')}", style="dim")
        console.print(tool_label)
        return truncate_output(run_command_impl(args.get("command"), auto_approve))

    elif func_name == "list_directory":
        p = args.get("path", ".")
        tool_label.append(f" → {p}", style="dim")
        console.print(tool_label)
        return truncate_output(list_directory(p))

    elif func_name == "grep_search":
        tool_label.append(f" → '{args.get('query', '?')}'", style="dim")
        console.print(tool_label)
        return truncate_output(
            grep_search(args.get("query"), args.get("path", "."), args.get("is_regex", False))
        )

    elif func_name == "find_files":
        tool_label.append(f" → {args.get('pattern', '?')}", style="dim")
        console.print(tool_label)
        return truncate_output(
            find_files(args.get("pattern"), args.get("path", "."))
        )

    else:
        console.print(tool_label)
        return f"Unknown tool: {func_name}"


# ─── File Context Injection ─────────────────────────────────────────────────────


def inject_file_context(user_input: str) -> str:
    pattern = r"(?:^|\s)@([a-zA-Z0-9_\-\./]+)"
    matches = re.findall(pattern, user_input)
    if not matches:
        return user_input

    context_blocks = []
    for filepath in set(matches):
        if os.path.isfile(filepath):
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                context_blocks.append(
                    f'<file_context path="{filepath}">\n{content}\n</file_context>'
                )
                console.print(f"  [dim]📎 Attached: {filepath}[/dim]")
            except Exception as e:
                console.print(
                    f"  [dim yellow]⚠️  Failed to read {filepath}: {e}[/dim yellow]"
                )
        elif os.path.isdir(filepath):
            console.print(
                f"  [dim yellow]⚠️  '{filepath}' is a directory, not a file[/dim yellow]"
            )
        else:
            console.print(f"  [dim yellow]⚠️  File not found: {filepath}[/dim yellow]")

    if context_blocks:
        user_input += "\n\n" + "\n".join(context_blocks)
    return user_input


# ─── Slash Commands ─────────────────────────────────────────────────────────────


def handle_slash_command(
    command_str: str, messages: list, token_tracker: TokenTracker
) -> bool:
    """Handle slash commands. Returns True if the agent loop should re-process (e.g. /retry)."""
    parts = command_str.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    global MODEL

    if cmd == "/clear":
        if len(messages) > 1:
            messages[:] = [messages[0]]
        console.print("[green]✓ Conversation cleared.[/green]\n")

    elif cmd == "/model":
        if arg:
            MODEL = arg
            console.print(
                f"[green]✓ Model switched to:[/green] [bold cyan]{MODEL}[/bold cyan]\n"
            )
        else:
            console.print(f"[bold]Current model:[/bold] [cyan]{MODEL}[/cyan]")
            console.print("[dim]Usage: /model <model_name>[/dim]\n")

    elif cmd == "/help":
        help_table = Table(
            title="⚡ Aether Commands",
            border_style="magenta",
            show_header=True,
            header_style="bold magenta",
        )
        help_table.add_column("Command", style="cyan bold", min_width=22)
        help_table.add_column("Description", style="white")
        help_table.add_row("/help", "Show this help message")
        help_table.add_row("/model [name]", "View or switch the active model")
        help_table.add_row("/clear", "Clear conversation history")
        help_table.add_row("/save [name]", "Save current conversation")
        help_table.add_row("/load [name]", "Load a saved conversation")
        help_table.add_row("/usage", "Show token usage statistics")
        help_table.add_row("/compact", "Summarize conversation to save tokens")
        help_table.add_row("/undo", "Undo the last file modification")
        help_table.add_row("/retry", "Retry the last user message")
        help_table.add_row("/copy", "Copy last AI response to clipboard")
        help_table.add_row("/export [file]", "Export conversation to Markdown")
        help_table.add_row("/config", "View current configuration")
        help_table.add_row("", "")
        help_table.add_row("@filename", "Attach file context (with autocomplete)")
        help_table.add_row("exit / quit", "Exit Aether")
        help_table.add_row("", "")
        help_table.add_row("[dim]Tip[/dim]", "[dim]End a line with \\\\ for multi-line input[/dim]")
        console.print(help_table)
        console.print()

    elif cmd == "/usage":
        console.print(token_tracker.get_summary_table())
        console.print()

    elif cmd == "/save":
        try:
            path = save_session(messages, arg if arg else None, token_tracker)
            console.print(f"[green]✓ Session saved to {path}[/green]\n")
        except Exception as e:
            console.print(f"[red]Error saving session: {e}[/red]\n")

    elif cmd == "/load":
        if not arg:
            sessions = list_sessions()
            if not sessions:
                console.print("[yellow]No saved sessions found.[/yellow]\n")
            else:
                table = Table(
                    title="📂 Saved Sessions",
                    border_style="magenta",
                    header_style="bold magenta",
                )
                table.add_column("Name", style="cyan")
                table.add_column("Saved At", style="dim")
                table.add_column("Messages", style="white", justify="right")
                for s in sessions[:10]:
                    table.add_row(s["name"], s["saved_at"][:19], str(s["messages"]))
                console.print(table)
                console.print("[dim]Usage: /load <session_name>[/dim]\n")
        else:
            loaded = load_session(arg)
            if loaded:
                messages[:] = loaded
                console.print(
                    f"[green]✓ Loaded session '{arg}' ({len(loaded)} messages)[/green]\n"
                )
            else:
                console.print(f"[red]Session '{arg}' not found.[/red]\n")

    elif cmd == "/undo":
        result = backup_manager.undo()
        console.print(f"[green]{result}[/green]\n")

    elif cmd == "/retry":
        # Remove last assistant + tool messages, then re-process the last user message
        while messages and messages[-1]["role"] in ("assistant", "tool"):
            messages.pop()
        if messages and messages[-1]["role"] == "user":
            console.print("[green]✓ Retrying last message...[/green]\n")
            return True  # Signal to re-process
        else:
            console.print("[yellow]Nothing to retry.[/yellow]\n")

    elif cmd == "/copy":
        last_response = None
        for msg in reversed(messages):
            if msg["role"] == "assistant" and msg.get("content"):
                last_response = msg["content"]
                break

        if last_response:
            try:
                if platform.system() == "Darwin":
                    subprocess.run(
                        ["pbcopy"],
                        input=last_response,
                        text=True,
                        check=True,
                    )
                elif platform.system() == "Linux":
                    subprocess.run(
                        ["xclip", "-selection", "clipboard"],
                        input=last_response,
                        text=True,
                        check=True,
                    )
                else:
                    subprocess.run(
                        ["clip"], input=last_response, text=True, check=True
                    )
                console.print("[green]✓ Copied to clipboard.[/green]\n")
            except Exception:
                console.print(
                    "[yellow]⚠️  Could not copy to clipboard.[/yellow]\n"
                )
        else:
            console.print("[yellow]No response to copy.[/yellow]\n")

    elif cmd == "/export":
        filename = (
            arg
            if arg
            else f"aether_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        )
        try:
            with open(filename, "w") as f:
                f.write("# Aether Conversation Export\n\n")
                f.write(
                    f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                f.write(f"**Model:** {MODEL}\n\n---\n\n")
                for msg in messages:
                    if msg["role"] == "system":
                        continue
                    elif msg["role"] == "user":
                        f.write(f"## 👤 You\n\n{msg['content']}\n\n")
                    elif msg["role"] == "assistant" and msg.get("content"):
                        f.write(f"## ✦ Aether\n\n{msg['content']}\n\n")
            console.print(f"[green]✓ Exported to {filename}[/green]\n")
        except Exception as e:
            console.print(f"[red]Error exporting: {e}[/red]\n")

    elif cmd == "/compact":
        if len(messages) <= 4:
            console.print("[yellow]Conversation is already compact.[/yellow]\n")
        else:
            system_msg = messages[0]
            recent = messages[-4:]
            middle = messages[1:-4]

            if middle:
                user_topics = [
                    m["content"][:100]
                    for m in middle
                    if m["role"] == "user" and m.get("content")
                ]
                summary = (
                    "Previous conversation summary: The user and assistant discussed "
                    + "; ".join(user_topics[:5])
                    + ". The assistant helped with these requests using code analysis and editing tools."
                )

                messages[:] = [
                    system_msg,
                    {"role": "user", "content": summary},
                    {
                        "role": "assistant",
                        "content": "Understood. I have the context from our previous discussion. How can I continue helping?",
                    },
                ] + recent
                console.print(
                    f"[green]✓ Compacted {len(middle)} messages into a summary.[/green]\n"
                )
            else:
                console.print("[yellow]Not enough messages to compact.[/yellow]\n")

    elif cmd == "/config":
        config = load_config()
        table = Table(
            title="⚙️  Configuration",
            border_style="magenta",
            header_style="bold magenta",
        )
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="white")
        table.add_row("Model", MODEL)
        table.add_row(
            "API Base URL",
            config.get("API_BASE_URL", "https://openrouter.ai/api/v1"),
        )
        api_key = config.get("OPENROUTER_API_KEY", "")
        table.add_row("API Key", f"***{api_key[-4:]}" if api_key else "Not set")
        table.add_row("Config File", CONFIG_PATH)
        table.add_row("Sessions Dir", SESSIONS_DIR)
        table.add_row("Backups Dir", BACKUPS_DIR)
        console.print(table)
        console.print()

    else:
        console.print(
            f"[red]Unknown command: {cmd}[/red] — type [bold]/help[/bold] for commands.\n"
        )

    return False


# ─── CLI Arguments ──────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(
        description="Aether AI Agent — A professional-grade AI coding assistant."
    )
    parser.add_argument("--version", action="store_true", help="Show version.")
    parser.add_argument("--model", type=str, help="Override the default model.")
    parser.add_argument(
        "--reset-key", action="store_true", help="Reset the saved API key."
    )
    parser.add_argument(
        "--set-base-url", type=str, help="Set custom API base URL."
    )
    parser.add_argument(
        "--yolo",
        action="store_true",
        help="Auto-approve all tool operations (no confirmations).",
    )
    return parser.parse_args()


# ─── Main ───────────────────────────────────────────────────────────────────────


def main():
    args = parse_args()

    if args.version:
        print(f"Aether v{VERSION}")
        sys.exit(0)

    config = load_config()

    if args.set_base_url:
        config["API_BASE_URL"] = args.set_base_url
        save_config(config)
        print(f"✓ API base URL set to: {args.set_base_url}")
        sys.exit(0)

    api_key = get_api_key(config, reset=args.reset_key)

    global MODEL
    if args.model:
        MODEL = args.model
    elif config.get("DEFAULT_MODEL"):
        MODEL = config["DEFAULT_MODEL"]

    api_base = config.get("API_BASE_URL", "https://openrouter.ai/api/v1")
    auto_approve = args.yolo

    client = OpenAI(base_url=api_base, api_key=api_key)

    token_tracker = TokenTracker()

    # Cleanup old backups
    backup_manager.cleanup()

    # Non-blocking update check
    check_for_updates()

    # ── Header ──
    console.print(AETHER_ASCII)
    header = Text()
    header.append(f"v{VERSION}", style="bold magenta")
    header.append("  │  ", style="dim")
    header.append(MODEL, style="cyan")
    if auto_approve:
        header.append("  │  ", style="dim")
        header.append("YOLO MODE", style="bold red")
    console.print(header)
    console.print(
        "[dim]Type /help for commands  •  @file to attach  •  exit to quit[/dim]\n"
    )

    # ── Keybindings ──
    kb = KeyBindings()

    @kb.add("enter", filter=has_completions & completion_is_selected)
    def _(event):
        event.current_buffer.complete_state = None

    session = PromptSession(completer=AetherCompleter(), key_bindings=kb)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            # ── Multi-line Input ──
            lines = []
            prompt_html = HTML(
                "<ansimagenta>╭─</ansimagenta> <ansimagenta><b>👤 You</b></ansimagenta>\n"
                "<ansimagenta>╰─❯</ansimagenta> "
            )
            first_line = session.prompt(prompt_html)
            lines.append(first_line)

            # Continue reading if line ends with backslash
            while lines[-1].rstrip().endswith("\\"):
                lines[-1] = lines[-1].rstrip()[:-1]  # Remove trailing backslash
                continuation = session.prompt(
                    HTML("<ansimagenta>  ⋮ </ansimagenta> ")
                )
                lines.append(continuation)

            user_input = "\n".join(lines)

            if user_input.lower().strip() in ("exit", "quit"):
                # Auto-save on exit
                if len(messages) > 2:
                    try:
                        save_session(messages, None, token_tracker)
                        console.print("[dim]Session auto-saved.[/dim]")
                    except Exception:
                        pass
                console.print("[yellow]Goodbye! 👋[/yellow]")
                break

            if not user_input.strip():
                continue

            # ── Slash Commands ──
            if user_input.strip().startswith("/"):
                should_retry = handle_slash_command(
                    user_input.strip(), messages, token_tracker
                )
                if should_retry and messages and messages[-1]["role"] == "user":
                    pass  # Fall through to the agent loop
                else:
                    continue
            else:
                user_input = inject_file_context(user_input)
                messages.append({"role": "user", "content": user_input})

            # ── Agent Loop ──────────────────────────────────────────────────
            while True:
                full_content = ""
                accumulated_tool_calls = {}

                # Build spinner text
                spinner_label = random.choice(
                    [
                        "Thinking...",
                        "Analyzing...",
                        "Reasoning...",
                        "Processing...",
                        "Considering...",
                        "Exploring...",
                    ]
                )
                spinner_display = Text()
                spinner_display.append("  ✦ ", style="bold magenta")
                spinner_display.append(spinner_label, style="dim italic")

                try:
                    with Live(
                        spinner_display,
                        console=console,
                        refresh_per_second=8,
                    ) as live:
                        stream = client.chat.completions.create(
                            model=MODEL,
                            messages=messages,
                            tools=tools,
                            tool_choice="auto",
                            stream=True,
                        )

                        for chunk in stream:
                            delta = (
                                chunk.choices[0].delta if chunk.choices else None
                            )
                            if not delta:
                                continue

                            # ── Content tokens ──
                            if delta.content:
                                full_content += delta.content
                                # Live-render Markdown in a panel
                                try:
                                    rendered = Panel(
                                        Markdown(full_content),
                                        title="[bold magenta]✦ Aether[/bold magenta]",
                                        border_style="magenta",
                                        padding=(1, 2),
                                    )
                                    live.update(rendered)
                                except Exception:
                                    # Fallback for incomplete markdown
                                    live.update(
                                        Panel(
                                            Text(full_content),
                                            title="[bold magenta]✦ Aether[/bold magenta]",
                                            border_style="magenta",
                                            padding=(1, 2),
                                        )
                                    )

                            # ── Tool call tokens ──
                            if delta.tool_calls:
                                for tc in delta.tool_calls:
                                    idx = tc.index
                                    if idx not in accumulated_tool_calls:
                                        accumulated_tool_calls[idx] = {
                                            "id": "",
                                            "name": "",
                                            "arguments": "",
                                            "type": "function",
                                        }
                                    if tc.id:
                                        accumulated_tool_calls[idx]["id"] = tc.id
                                    if tc.function:
                                        if tc.function.name:
                                            accumulated_tool_calls[idx][
                                                "name"
                                            ] += tc.function.name
                                        if tc.function.arguments:
                                            accumulated_tool_calls[idx][
                                                "arguments"
                                            ] += tc.function.arguments

                                # Update spinner with tool info
                                names = [
                                    v["name"]
                                    for v in accumulated_tool_calls.values()
                                    if v["name"]
                                ]
                                if names and not full_content:
                                    tool_text = Text()
                                    tool_text.append("  ⚙️  ", style="magenta")
                                    tool_text.append(
                                        f"Preparing: {', '.join(names)}",
                                        style="dim italic",
                                    )
                                    live.update(tool_text)

                except Exception as e:
                    console.print(f"\n[bold red]API Error:[/bold red] {e}")
                    error_str = str(e).lower()
                    if "401" in error_str or "unauthorized" in error_str:
                        console.print(
                            "[dim]Hint: API key may be invalid. Run with --reset-key[/dim]"
                        )
                    elif "429" in error_str or "rate" in error_str:
                        console.print(
                            "[dim]Hint: Rate limited. Wait a moment and retry.[/dim]"
                        )
                    elif "timeout" in error_str:
                        console.print(
                            "[dim]Hint: Request timed out. Check your connection.[/dim]"
                        )
                    break

                # Track tokens (estimate)
                if full_content:
                    estimated_input = token_tracker.estimate_tokens(
                        json.dumps(messages[-1]) if messages else ""
                    )
                    estimated_output = token_tracker.estimate_tokens(full_content)
                    token_tracker.add_usage(estimated_input, estimated_output)

                # Build tool calls list
                tool_calls_list = []
                for idx in sorted(accumulated_tool_calls.keys()):
                    tc = accumulated_tool_calls[idx]
                    tool_calls_list.append(
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": tc["arguments"],
                            },
                        }
                    )

                # Add assistant message to history
                assistant_msg = {
                    "role": "assistant",
                    "content": full_content or None,
                }
                if tool_calls_list:
                    assistant_msg["tool_calls"] = tool_calls_list
                messages.append(assistant_msg)

                # If no tool calls, we're done
                if not tool_calls_list:
                    break

                # Execute tool calls
                for tc_dict in tool_calls_list:
                    func_struct = Struct(**tc_dict["function"])
                    tc_struct = Struct(
                        id=tc_dict["id"],
                        type=tc_dict["type"],
                        function=func_struct,
                    )

                    tool_result = execute_tool(tc_struct, auto_approve)

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_dict["id"],
                            "name": tc_dict["function"]["name"],
                            "content": tool_result,
                        }
                    )

                # Continue the loop — model processes tool results

            # ── Footer ──
            console.print(
                f"[dim]  tokens: ~{token_tracker.total_tokens:,}  │  "
                f"messages: {token_tracker.message_count}  │  "
                f"model: {MODEL}[/dim]\n"
            )

        except (KeyboardInterrupt, EOFError):
            # Auto-save on interrupt
            if len(messages) > 2:
                try:
                    save_session(messages, None, token_tracker)
                    console.print("\n[dim]Session auto-saved.[/dim]")
                except Exception:
                    pass
            console.print("[yellow]Goodbye! 👋[/yellow]")
            break
        except Exception as e:
            console.print(f"\n[bold red]Error:[/bold red] {e}")


if __name__ == "__main__":
    main()
