import ast
import difflib
import fnmatch
import json
import os
import re
import subprocess
import shutil
import tempfile
import threading
import time
import uuid
from typing import Any

import questionary
from prompt_toolkit.styles import Style
from rich.live import Live
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from .config import (
    DANGEROUS_PATTERNS,
    SAFE_COMMAND_PREFIXES,
    Theme,
    console,
    load_config,
    save_config,
)
from .logging_config import logger
from .plugins import plugin_manager
from .utils import BackupManager, load_gitignore_patterns, should_ignore, truncate_output

# Global terminal lock to prevent garbled output from concurrent tool runs
terminal_lock = threading.Lock()
backup_manager = BackupManager()

_git_warned = False
_session_auto_approve = False

def _ask_permission(prompt_text: str, auto_approve: bool = False) -> bool:
    """Helper to handle approval prompts with a session-wide 'always' option."""
    global _session_auto_approve
    if auto_approve or _session_auto_approve:
        return True

    # We must use synchronous input since tools run in threads
    try:
        custom_style = Style([
            ('qmark', 'fg:#c084fc bold'),
            ('question', 'fg:#e2e8f0 bold'),
            ('answer', 'fg:#22d3ee bold'),
            ('pointer', 'fg:#c084fc bold'),
            ('highlighted', 'fg:#ffffff bold'),
            ('selected', 'fg:#22d3ee'),
            ('separator', 'fg:#6b7280'),
            ('instruction', 'fg:#6b7280'),
        ])

        ans = questionary.select(
            prompt_text.strip(),
            choices=[
                questionary.Choice("Yes, allow this time", "y"),
                questionary.Choice("No, deny", "n"),
                questionary.Choice("Always allow (YOLO mode)", "a")
            ],
            style=custom_style,
            instruction=" (Use ↑/↓ arrows to select, Enter to submit)"
        ).ask()

        if not ans:
            return False

        if ans in ("a", "always"):
            _session_auto_approve = True
            console.print(f"  [{Theme.SUCCESS}]✓ Always allow enabled and saved for future sessions.[/{Theme.SUCCESS}]")
            try:
                conf = load_config()
                conf["auto_approve"] = True
                conf["suppress_git_warning"] = True
                save_config(conf)
            except Exception as e:
                logger.debug(f"Failed to save to config: {e}")
            return True
        return ans == "y"
    except (EOFError, KeyboardInterrupt):
        return False

# Global dictionary for tracking background tasks
# task_id -> {"process": Popen, "stdout": list, "stderr": list, "command": str}
background_tasks: dict[str, dict[str, Any]] = {}
background_tasks_lock = threading.Lock()  # Protects background_tasks dict

def fuzzy_match_file(filepath: str) -> str | None:
    """
    If the exact filepath does not exist, searches the current directory tree
    for a close match. Returns the matched path or None.
    """
    if not filepath or filepath.startswith("/") or filepath.startswith("~"):
        return None  # Only fuzzy match relative paths safely

    ignore_patterns = load_gitignore_patterns()
    all_files = []

    # Collect all available files in the tree
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if not should_ignore(os.path.join(root, d), ignore_patterns)]
        for f in files:
            path = os.path.relpath(os.path.join(root, f), ".")
            if not should_ignore(path, ignore_patterns):
                all_files.append(path)

    # Use difflib to find the closest match
    matches = difflib.get_close_matches(filepath, all_files, n=1, cutoff=0.75)
    return matches[0] if matches else None

# ─── Constants ──────────────────────────────────────────────────────────────────

MAX_FILE_SIZE_BYTES = 1_048_576  # 1 MB — refuse to read files larger than this
MAX_FILE_SIZE_WARNING = 512_000  # 512 KB — warn but allow
BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat",
    ".pyc", ".pyo", ".class", ".o", ".obj",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".sqlite", ".db",
})

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
            "description": "Creates a new file or fully overwrites an existing one. For modifying existing files, prefer replace_file_content or multi_replace_file_content.",
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
                    "start_line": {
                        "type": "integer",
                        "description": "Optional starting line for absolute block rewrite.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Optional ending line for absolute block rewrite.",
                    },
                },
                "required": ["filepath", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_file_content",
            "description": "Edits a single contiguous block of an existing file. Uses start_line and end_line bounds to locate the target_content reliably.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the file to edit.",
                    },
                    "target_content": {
                        "type": "string",
                        "description": "The exact existing text block to replace.",
                    },
                    "replacement_content": {
                        "type": "string",
                        "description": "The replacement text.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Starting line number (1-indexed) to search within.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Ending line number (1-indexed) to search within.",
                    },
                    "allow_multiple": {
                        "type": "boolean",
                        "description": "If true, replaces all occurrences within the bounds.",
                    }
                },
                "required": ["filepath", "target_content", "replacement_content", "start_line", "end_line"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "multi_replace_file_content",
            "description": "Edits multiple non-adjacent blocks of an existing file in a single pass.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the file to edit.",
                    },
                    "replacement_chunks": {
                        "type": "array",
                        "description": "List of chunks to replace.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "target_content": { "type": "string" },
                                "replacement_content": { "type": "string" },
                                "start_line": { "type": "integer" },
                                "end_line": { "type": "integer" },
                                "allow_multiple": { "type": "boolean" }
                            },
                            "required": ["target_content", "replacement_content", "start_line", "end_line"]
                        }
                    }
                },
                "required": ["filepath", "replacement_chunks"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Executes a shell command. Safe read-only commands run automatically; destructive commands require user confirmation. Use the timeout parameter for long-running commands like builds or test suites.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds. Default 120. Set higher for builds/tests (e.g. 300).",
                    },
                    "background": {
                        "type": "boolean",
                        "description": "If true, runs the command asynchronously in the background. Returns a task ID immediately.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_background_task",
            "description": "Checks the status and reads the recent output of a command running in the background.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The ID of the background task.",
                    }
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kill_background_task",
            "description": "Kills a running background task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The ID of the background task.",
                    }
                },
                "required": ["task_id"],
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
    {
        "type": "function",
        "function": {
            "name": "get_file_outline",
            "description": "Extracts the abstract syntax tree (AST) outline of a Python file, showing classes, methods, and docstrings without the full implementation details. Useful for exploring large codebases.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the Python file.",
                    }
                },
                "required": ["filepath"],
            },
        },
    },
]


# ─── Helpers ────────────────────────────────────────────────────────────────────

def _is_binary_file(filepath: str) -> bool:
    """Check if a file is likely binary based on extension."""
    _, ext = os.path.splitext(filepath.lower())
    return ext in BINARY_EXTENSIONS


def _detect_language(filepath: str) -> str:
    """Detect Rich Syntax language from file extension for diff highlighting."""
    ext_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".jsx": "jsx", ".tsx": "tsx", ".html": "html", ".css": "css",
        ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
        ".md": "markdown", ".rs": "rust", ".go": "go", ".java": "java",
        ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
        ".rb": "ruby", ".php": "php", ".sh": "bash", ".bash": "bash",
        ".zsh": "bash", ".sql": "sql", ".xml": "xml", ".swift": "swift",
        ".kt": "kotlin", ".scala": "scala", ".r": "r",
        ".dockerfile": "dockerfile", ".tf": "hcl",
    }
    _, ext = os.path.splitext(filepath.lower())
    basename = os.path.basename(filepath).lower()
    if basename in ("dockerfile", "makefile", "gemfile", "rakefile"):
        return basename
    return ext_map.get(ext, "text")


def _render_diff(diff_lines: list[str], filepath: str) -> None:
    """Render a unified diff with rich terminal formatting."""
    diff_text = Text()

    # Cap diff display at 15 lines to avoid terminal spam
    max_diff_lines = 15
    display_lines = diff_lines[:max_diff_lines]

    for line in display_lines:
        line = line.rstrip("\n")
        if not line:
            continue

        if line.startswith("+++") or line.startswith("---"):
            diff_text.append(line + "\n", style=f"bold {Theme.ACCENT}")
        elif line.startswith("@@"):
            diff_text.append(line + "\n", style=Theme.SECONDARY)
        elif line.startswith("+"):
            # Green text on a very dark green background
            diff_text.append(line + "\n", style=f"{Theme.SUCCESS} on #0a1f10")
        elif line.startswith("-"):
            # Red text on a very dark red background
            diff_text.append(line + "\n", style=f"{Theme.ERROR} on #2a0f0f")
        else:
            diff_text.append(line + "\n", style=Theme.MUTED)

    if len(diff_lines) > max_diff_lines:
        remaining = len(diff_lines) - max_diff_lines
        diff_text.append(f"... ({remaining} more diff lines)\n", style=f"italic {Theme.WARNING}")

    if len(diff_text) > 0:
        console.print(diff_text)


def _try_repair_json(raw: str) -> dict | None:
    """
    Attempt to repair common JSON issues from LLM tool calls:
    - Trailing commas
    - Single quotes
    - Unquoted keys
    """
    # Try as-is first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Strip trailing commas before } or ]
    repaired = re.sub(r",\s*([}\]])", r"\1", raw)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Replace single quotes with double quotes (naive, but catches simple cases)
    repaired = raw.replace("'", '"')
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    return None


# ─── Tool Implementations ──────────────────────────────────────────────────────

backup_manager = BackupManager()

_git_warned = False

def _check_git_dirty(filepath: str) -> None:
    """Warn the user once per session if they are modifying files in a dirty git repo."""
    global _git_warned
    if _git_warned:
        return

    try:
        conf = load_config()
        if conf.get("suppress_git_warning", False):
            _git_warned = True
            return
    except Exception:
        pass

    try:
        abs_dir = os.path.dirname(os.path.abspath(filepath))
        # Check if we are in a git repo
        repo_dir = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=abs_dir, capture_output=True, text=True, check=True
        ).stdout.strip()

        # Check if dirty
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_dir, capture_output=True, text=True, check=True
        ).stdout.strip()

        if status:
            console.print(
                f"\n  [bold yellow]⚠️  Git Safety Warning:[/bold yellow] "
                f"[{Theme.WARNING}]You have uncommitted changes in this repository.[/{Theme.WARNING}]\n"
                f"  [{Theme.MUTED}]Aizen's modifications could mix with your uncommitted work.\n"
                f"  Consider committing or stashing your changes before proceeding.[/{Theme.MUTED}]\n"
            )
            _git_warned = True
    except Exception:
        pass  # Not a git repo or git not installed

def get_file_outline(filepath: str) -> str:
    """Extract AST outline of a Python file."""
    try:
        if not filepath.endswith('.py'):
            return f"Error: '{filepath}' is not a Python file."
        if not os.path.exists(filepath):
            match = fuzzy_match_file(filepath)
            if match:
                filepath = match
            else:
                return f"Error: File '{filepath}' does not exist."
        
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            content = f.read()
            
        tree = ast.parse(content)
        outline = [f"File: {filepath}\n"]
        
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                doc = ast.get_docstring(node)
                doc_str = f'    """{doc}"""\n' if doc else ''
                outline.append(f"class {node.name}:\n{doc_str}")
                for child in node.body:
                    if isinstance(child, ast.FunctionDef):
                        cdoc = ast.get_docstring(child)
                        cdoc_str = f'        """{cdoc}"""\n' if cdoc else ''
                        outline.append(f"    def {child.name}(...):\n{cdoc_str}")
            elif isinstance(node, ast.FunctionDef):
                doc = ast.get_docstring(node)
                doc_str = f'    """{doc}"""\n' if doc else ''
                outline.append(f"def {node.name}(...):\n{doc_str}")
                
        if len(outline) == 1:
            return outline[0] + "\nNo classes or functions found."
        return "\n".join(outline)
    except Exception as e:
        return f"Error getting file outline: {e}"

def read_file(filepath: str) -> str:
    """Read file contents with safety checks for size and binary detection."""
    logger.debug("read_file: %s", filepath)
    try:
        if not os.path.exists(filepath):
            match = fuzzy_match_file(filepath)
            if match:
                console.print(f"  [dim yellow]⚠️  File '{filepath}' not found, fuzzy matched to '{match}'[/dim yellow]")
                filepath = match
            else:
                return f"Error: File '{filepath}' does not exist."

        # Binary file check
        if _is_binary_file(filepath):
            return (
                f"Error: '{filepath}' appears to be a binary file. "
                f"Binary files cannot be read as text."
            )

        # File size check
        file_size = os.path.getsize(filepath)
        if file_size > MAX_FILE_SIZE_BYTES:
            size_mb = file_size / (1024 * 1024)
            return (
                f"Error: File '{filepath}' is too large ({size_mb:.1f} MB). "
                f"Maximum allowed size is {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB. "
                f"Use `run_command` with `head -n 100 {filepath}` to preview."
            )

        if file_size > MAX_FILE_SIZE_WARNING:
            size_kb = file_size / 1024
            console.print(
                f"  [yellow]⚠️  Large file: {filepath} ({size_kb:.0f} KB)[/yellow]"
            )

        with open(filepath, encoding="utf-8", errors="ignore") as f:
            content = f.read()

        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return f"[File: {filepath} | {line_count} lines | {file_size:,} bytes]\n{content}"
    except PermissionError:
        logger.error("Permission denied reading '%s'", filepath)
        return f"Error: Permission denied reading '{filepath}'."
    except Exception as e:
        logger.exception("Error reading file '%s'", filepath)
        return f"Error reading file: {e}"


def write_file_with_diff(filepath: str, content: str, auto_approve: bool = False, start_line: int | None = None, end_line: int | None = None) -> str:
    """Write/overwrite a file with diff preview and optional auto-approval. Supports block rewriting."""
    logger.debug("write_file: %s (%d bytes)", filepath, len(content))
    try:
        _check_git_dirty(filepath)
        old_content = ""
        exists = os.path.exists(filepath)
        if not exists:
            match = fuzzy_match_file(filepath)
            if match:
                console.print(f"  [dim yellow]⚠️  File '{filepath}' not found, fuzzy matched to '{match}'[/dim yellow]")
                filepath = match
                exists = True

        if exists:
            try:
                with open(filepath, encoding="utf-8", errors="ignore") as f:
                    old_content = f.read()

                if start_line is not None and end_line is not None:
                    lines = old_content.splitlines(keepends=True)
                    sl = max(0, start_line - 1)
                    el = min(len(lines), end_line)
                    new_content = "".join(lines[:sl]) + content
                    if not new_content.endswith("\n") and lines[el:]:
                        new_content += "\n"
                    new_content += "".join(lines[el:])
                    content = new_content

                if old_content == content:
                    return f"No changes to write for {filepath}"
            except Exception as e:
                logger.debug("Failed to read old content for %s: %s", filepath, e)

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
                    f"[bold {Theme.ACCENT}]◆ AIZEN[/bold {Theme.ACCENT}] [{Theme.TEXT}]wants to overwrite:[/{Theme.TEXT}] [bold {Theme.ACCENT}]{filepath}[/bold {Theme.ACCENT}]",
                    border_style=Theme.BORDER,
                )
            )
            _render_diff(diff, filepath)
        else:
            preview_lines = content.split("\n")[:15]
            preview = "\n".join(preview_lines)
            total_lines = len(content.split("\n"))
            if total_lines > 15:
                preview += f"\n... ({total_lines} total lines)"

            console.print(
                Panel(
                    f"[bold {Theme.ACCENT}]◆ AIZEN[/bold {Theme.ACCENT}] [{Theme.TEXT}]wants to create:[/{Theme.TEXT}] [bold {Theme.ACCENT}]{filepath}[/bold {Theme.ACCENT}]",
                    border_style=Theme.BORDER,
                )
            )
            lang = _detect_language(filepath)
            syntax = Syntax(preview, lang, theme="monokai", line_numbers=True)
            console.print(syntax)

        syntax_err = _validate_syntax(filepath, content)
        if syntax_err:
            return f"Error: The edit introduces a syntax or linting error and was aborted.\n{syntax_err}"

        # YOLO mode: skip confirmation
        with terminal_lock:
            if not _ask_permission("  ▸ Allow?", auto_approve):
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


def _validate_syntax(filepath: str, file_content: str) -> str | None:
    """Return error message if syntax is invalid, else None."""
    if filepath.endswith(".py"):
        try:
            ast.parse(file_content)
        except SyntaxError as e:
            return f"SyntaxError in Python code: {e.msg} at line {e.lineno}, col {e.offset}"
            
        # Additional Linting
        if shutil.which("ruff"):
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
                tmp.write(file_content)
                tmp_path = tmp.name
            try:
                result = subprocess.run(["ruff", "check", tmp_path], capture_output=True, text=True)
                if result.returncode != 0:
                    output = result.stdout.replace(tmp_path, os.path.basename(filepath))
                    return f"Ruff Linting Error:\n{output}"
            finally:
                os.unlink(tmp_path)
                
    elif filepath.endswith((".js", ".jsx", ".ts", ".tsx")):
        if shutil.which("eslint"):
            ext = os.path.splitext(filepath)[1]
            with tempfile.NamedTemporaryFile(mode="w", suffix=ext, delete=False) as tmp:
                tmp.write(file_content)
                tmp_path = tmp.name
            try:
                result = subprocess.run(["eslint", "--no-warn-ignored", tmp_path], capture_output=True, text=True)
                if result.returncode != 0:
                    output = result.stdout.replace(tmp_path, os.path.basename(filepath))
                    return f"ESLint Error:\n{output}"
            finally:
                os.unlink(tmp_path)
    return None

def _fuzzy_find_block(file_lines: list[str], target_content: str, start_line: int, end_line: int) -> str | None:
    """Find the best match for target_content within the specified line bounds."""
    start_idx = max(0, start_line - 1)
    end_idx = min(len(file_lines), end_line)
    search_lines = file_lines[start_idx:end_idx]
    search_str = "".join(search_lines)

    if target_content in search_str:
        return target_content

    parts = re.split(r'\s+', target_content.strip())
    escaped_parts = [re.escape(p) for p in parts if p]
    if escaped_parts:
        pattern_str = r'\s+'.join(escaped_parts)
        try:
            matches = list(re.finditer(pattern_str, search_str))
            if len(matches) == 1:
                return matches[0].group(0)
        except Exception:
            pass

    target_lines = target_content.splitlines(keepends=True)
    if not target_lines:
        return None

    best_ratio = 0
    best_match = None
    window_size = len(target_lines)

    for i in range(len(search_lines) - window_size + 1):
        window = "".join(search_lines[i:i + window_size])
        ratio = difflib.SequenceMatcher(None, target_content, window).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = window

    if best_ratio > 0.8:
        return best_match

    return None

def replace_file_content(filepath: str, target_content: str, replacement_content: str, start_line: int, end_line: int, allow_multiple: bool = False, auto_approve: bool = False) -> str:
    """Edits a single contiguous block of an existing file."""
    return multi_replace_file_content(
        filepath,
        [{"target_content": target_content, "replacement_content": replacement_content, "start_line": start_line, "end_line": end_line, "allow_multiple": allow_multiple}],
        auto_approve
    )

def multi_replace_file_content(filepath: str, replacement_chunks: list[dict], auto_approve: bool = False) -> str:
    """Edits multiple non-adjacent blocks of an existing file."""
    try:
        _check_git_dirty(filepath)
        if not os.path.exists(filepath):
            match = fuzzy_match_file(filepath)
            if match:
                console.print(f"  [dim yellow]⚠️  File '{filepath}' not found, fuzzy matched to '{match}'[/dim yellow]")
                filepath = match
            else:
                return f"Error: File '{filepath}' does not exist. Use write_file to create new files."

        with open(filepath, encoding="utf-8", errors="ignore") as f:
            file_content = f.read()

        file_lines = file_content.splitlines(keepends=True)
        new_file_content = file_content

        for idx, chunk in enumerate(replacement_chunks):
            target = chunk["target_content"]
            replacement = chunk["replacement_content"]
            sl = chunk.get("start_line", 1)
            el = chunk.get("end_line", len(file_lines))
            allow_mult = chunk.get("allow_multiple", False)

            actual_old = _fuzzy_find_block(file_lines, target, sl, el)
            if not actual_old:
                return f"Error in chunk {idx+1}: Could not find the specified target_content within lines {sl}-{el}. Please check your exact text."

            occurrence_count = new_file_content.count(actual_old)
            if occurrence_count == 0:
                return f"Error in chunk {idx+1}: The text was found originally but is no longer present after preceding replacements."
            if occurrence_count > 1 and not allow_mult:
                return f"Error in chunk {idx+1}: Found {occurrence_count} occurrences of the target text. Provide a more specific block or set allow_multiple=true."

            new_file_content = new_file_content.replace(actual_old, replacement, -1 if allow_mult else 1)
            file_lines = new_file_content.splitlines(keepends=True)

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

        syntax_err = _validate_syntax(filepath, new_file_content)
        if syntax_err:
            return f"Error: The edit introduces a syntax error and was aborted.\n{syntax_err}"

        console.print(
            Panel(
                f"[bold {Theme.ACCENT}]◆ AIZEN[/bold {Theme.ACCENT}] [{Theme.TEXT}]wants to edit:[/{Theme.TEXT}] [bold {Theme.ACCENT}]{filepath}[/bold {Theme.ACCENT}]",
                border_style=Theme.BORDER,
            )
        )
        _render_diff(diff, filepath)

        with terminal_lock:
            if not _ask_permission("  ▸ Apply edit?", auto_approve):
                return "User denied the edit."

        backup_manager.backup(filepath)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_file_content)

        return f"✓ Successfully applied {len(replacement_chunks)} replacement(s) to {filepath}"
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


def run_command_impl(command: str, auto_approve: bool = False, timeout: int = 120, background: bool = False) -> str:
    """Execute a shell command with safety checks, configurable timeout, and live output."""
    logger.debug("run_command: %s (timeout=%ds, background=%s)", command, timeout, background)

    # Intercept pure 'cd' commands to update process working directory persistently
    cmd_stripped = command.strip()
    if cmd_stripped.startswith("cd ") and not any(sep in cmd_stripped for sep in ["&&", ";", "||", "|"]):
        target_dir = cmd_stripped[3:].strip()
        target_dir = os.path.expanduser(target_dir.strip("\"'"))
        try:
            os.chdir(target_dir)
            new_cwd = os.getcwd()
            logger.info("Changed working directory to %s", new_cwd)
            console.print(f"  [dim]▶ Changed directory to {new_cwd}[/dim]")
            return f"Working directory changed to {new_cwd}"
        except Exception as e:
            logger.error("Failed to change directory to '%s': %s", target_dir, e)
            return f"Error changing directory: {e}"
    safe = is_command_safe(command)

    if not safe:
        console.print(
            Panel(
                f"[bold {Theme.ACCENT}]◆ AIZEN[/bold {Theme.ACCENT}] [{Theme.TEXT}]wants to run:[/{Theme.TEXT}]\n\n[bold {Theme.TEXT}]{command}[/bold {Theme.TEXT}]",
                border_style=Theme.BORDER,
            )
        )
        with terminal_lock:
            if not _ask_permission("  ▸ Allow?", auto_approve):
                return "User denied command execution."
    elif safe:
        console.print(f"  [dim]▶ {command}{' (background)' if background else ''}[/dim]")

    try:
        # Use Popen for streaming output with live display
        import select

        proc = subprocess.Popen(
            command,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if background:
            task_id = f"bg_{uuid.uuid4().hex[:8]}"
            task_info = {
                "process": proc,
                "stdout": [],
                "stderr": [],
                "command": command,
                "start_time": time.time()
            }
            with background_tasks_lock:
                background_tasks[task_id] = task_info

            def stream_reader(pipe, dest_list):
                for line in iter(pipe.readline, ''):
                    dest_list.append(line)
                pipe.close()

            threading.Thread(target=stream_reader, args=(proc.stdout, task_info["stdout"]), daemon=True).start()
            threading.Thread(target=stream_reader, args=(proc.stderr, task_info["stderr"]), daemon=True).start()

            return f"Task started in background with ID: {task_id}"

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        start_time = time.time()

        with Live(
            Text("  ▶ Running...", style="dim italic"),
            console=console,
            refresh_per_second=4,
            transient=True,
        ) as live:
            while proc.poll() is None:
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    proc.kill()
                    logger.warning("Command timed out after %ds: %s", timeout, command)
                    return f"Error: Command timed out after {timeout} seconds."

                # Read available stdout and stderr non-blockingly
                reads = []
                if proc.stdout:
                    reads.append(proc.stdout)
                if proc.stderr:
                    reads.append(proc.stderr)

                if reads:
                    rlist, _, _ = select.select(reads, [], [], 0.1)
                    for fd in rlist:
                        if fd == proc.stdout:
                            line = proc.stdout.readline()
                            if line:
                                stdout_lines.append(line)
                                # Show live output tail (last 15 lines)
                                tail = "".join(stdout_lines[-15:])
                                display = Text()
                                display.append(f"  ▶ Running ({elapsed:.0f}s)\n", style="dim italic")
                                display.append(tail.rstrip(), style="dim")
                                live.update(display)
                        elif fd == proc.stderr:
                            line = proc.stderr.readline()
                            if line:
                                stderr_lines.append(line)

            # Read remaining output after process exits
            if proc.stdout:
                remaining = proc.stdout.read()
                if remaining:
                    stdout_lines.append(remaining)
            if proc.stderr:
                stderr_lines.append(proc.stderr.read())

        output = "".join(stdout_lines)
        stderr_output = "".join(stderr_lines).strip()

        if stderr_output:
            if output:
                output += f"\nSTDERR:\n{stderr_output}"
            else:
                output = stderr_output
        if proc.returncode != 0:
            output += f"\n[Exit code: {proc.returncode}]"
        return output.strip() if output.strip() else f"Command completed (exit code {proc.returncode})"
    except subprocess.TimeoutExpired:
        logger.warning("Command timed out after %ds: %s", timeout, command)
        return f"Error: Command timed out after {timeout} seconds."
    except Exception as e:
        logger.exception("Error executing command: %s", command)
        return f"Error executing command: {e}"


def check_background_task_impl(task_id: str) -> str:
    """Checks the status of a background task and returns its recent output."""
    with background_tasks_lock:
        if task_id not in background_tasks:
            return f"Error: No such background task '{task_id}'."
        task = background_tasks[task_id]

    proc = task["process"]

    out_lines = list(task["stdout"])
    err_lines = list(task["stderr"])

    stdout_str = "".join(out_lines[-100:]).strip()  # Return last 100 lines to avoid massive output
    stderr_str = "".join(err_lines[-100:]).strip()

    status = "RUNNING" if proc.poll() is None else f"FINISHED (Exit code {proc.returncode})"

    result = f"Task: {task_id}\nCommand: {task['command']}\nStatus: {status}\n\n"
    if stdout_str:
        result += f"--- STDOUT (last 100 lines) ---\n{stdout_str}\n\n"
    if stderr_str:
        result += f"--- STDERR (last 100 lines) ---\n{stderr_str}\n"

    # Cleanup if done
    if proc.poll() is not None:
        with background_tasks_lock:
            background_tasks.pop(task_id, None)

    return result.strip()


def kill_background_task_impl(task_id: str) -> str:
    """Kills a running background task."""
    with background_tasks_lock:
        if task_id not in background_tasks:
            return f"Error: No such background task '{task_id}'."
        task = background_tasks.pop(task_id)

    proc = task["process"]

    if proc.poll() is None:
        proc.kill()
        return f"Task {task_id} killed."
    else:
        return f"Task {task_id} was already finished."


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
                if _is_binary_file(file_path):
                    continue
                try:
                    with open(file_path, encoding="utf-8", errors="ignore") as f:
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
                except (UnicodeDecodeError, PermissionError, OSError) as e:
                    logger.debug("grep_search skipped %s: %s", file_path, e)

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


# ─── Tool Dispatcher ───────────────────────────────────────────────────────────

def execute_tool(tool_call, auto_approve: bool = False) -> str:
    """
    Dispatch and execute a tool call from the AI model.

    Handles JSON parsing with repair, auto_approve passthrough,
    and configurable timeouts.
    """
    func_name = tool_call.function.name
    raw_args = tool_call.function.arguments
    logger.debug("Dispatching tool: %s", func_name)

    # Parse arguments with repair fallback
    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError:
        # Attempt JSON repair
        args = _try_repair_json(raw_args)
        if args is None:
            console.print(
                f"  [yellow]⚠️  Malformed JSON from model for {func_name}[/yellow]"
            )
            return (
                f"Error: Invalid JSON in tool arguments for '{func_name}'. "
                f"Please retry with valid JSON. The arguments received were: "
                f"{raw_args[:200]}{'...' if len(raw_args) > 200 else ''}"
            )
        else:
            console.print(
                f"  [dim yellow]⚠️  Repaired malformed JSON for {func_name}[/dim yellow]"
            )

    tool_label = Text("  ⚙️  ", style="magenta")
    tool_label.append(func_name, style="dim magenta")

    if func_name == "read_file":
        filepath = str(args.get("filepath", ""))
        tool_label.append(f" → {filepath or '?'}", style="dim")
        console.print(tool_label)
        return truncate_output(read_file(filepath))

    elif func_name == "write_file":
        filepath = str(args.get("filepath", ""))
        content = str(args.get("content", ""))
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        if start_line is not None:
            start_line = int(start_line)
        if end_line is not None:
            end_line = int(end_line)
        tool_label.append(f" → {filepath or '?'}", style="dim")
        console.print(tool_label)
        return write_file_with_diff(filepath, content, auto_approve=auto_approve, start_line=start_line, end_line=end_line)

    elif func_name == "replace_file_content":
        filepath = str(args.get("filepath", ""))
        target = str(args.get("target_content", ""))
        replacement = str(args.get("replacement_content", ""))
        sl = int(args.get("start_line", 1))
        el = int(args.get("end_line", 999999))
        am = bool(args.get("allow_multiple", False))
        tool_label.append(f" → {filepath or '?'}", style="dim")
        console.print(tool_label)
        return replace_file_content(filepath, target, replacement, sl, el, am, auto_approve=auto_approve)

    elif func_name == "multi_replace_file_content":
        filepath = str(args.get("filepath", ""))
        chunks = args.get("replacement_chunks", [])
        tool_label.append(f" → {filepath or '?'} ({len(chunks)} chunks)", style="dim")
        console.print(tool_label)
        return multi_replace_file_content(filepath, chunks, auto_approve=auto_approve)

    elif func_name == "run_command":
        command = str(args.get("command", ""))
        timeout = int(args.get("timeout", 120))
        background = bool(args.get("background", False))
        tool_label.append(f" → {command or '?'}", style="dim")
        console.print(tool_label)
        return truncate_output(run_command_impl(command, auto_approve, timeout=timeout, background=background))

    elif func_name == "check_background_task":
        task_id = str(args.get("task_id", ""))
        tool_label.append(f" → {task_id}", style="dim")
        console.print(tool_label)
        return check_background_task_impl(task_id)

    elif func_name == "kill_background_task":
        task_id = str(args.get("task_id", ""))
        tool_label.append(f" → {task_id}", style="dim")
        console.print(tool_label)
        return kill_background_task_impl(task_id)

    elif func_name == "list_directory":
        p = str(args.get("path", "."))
        tool_label.append(f" → {p}", style="dim")
        console.print(tool_label)
        return truncate_output(list_directory(p))

    elif func_name == "grep_search":
        query = str(args.get("query", ""))
        path = str(args.get("path", "."))
        is_regex = bool(args.get("is_regex", False))
        tool_label.append(f" → '{query or '?'}'", style="dim")
        console.print(tool_label)
        return truncate_output(grep_search(query, path, is_regex))

    elif func_name == "find_files":
        pattern = str(args.get("pattern", ""))
        path = str(args.get("path", "."))
        tool_label.append(f" → {pattern or '?'}", style="dim")
        console.print(tool_label)
        return truncate_output(find_files(pattern, path))

    elif func_name == "get_file_outline":
        filepath = str(args.get("filepath", ""))
        tool_label.append(f" → {filepath or '?'}", style="dim")
        console.print(tool_label)
        return truncate_output(get_file_outline(filepath))

    else:
        # Check if a plugin handles this tool
        plugin_result = plugin_manager.execute_tool(tool_call, auto_approve=auto_approve)
        if plugin_result is not None:
            return plugin_result

        console.print(tool_label)
        return f"Unknown tool: {func_name}"

