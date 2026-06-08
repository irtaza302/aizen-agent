import os
import json
import re
import time
import difflib
import fnmatch
import subprocess
from rich.panel import Panel
from rich.text import Text
from rich.syntax import Syntax
from rich.live import Live
from rich.progress import Progress, SpinnerColumn, TextColumn

from .config import console, SAFE_COMMAND_PREFIXES, DANGEROUS_PATTERNS
from .utils import BackupManager, truncate_output, load_gitignore_patterns, should_ignore, Struct
from .exceptions import FileOperationError, ToolExecutionError
from .logging_config import logger
from .plugins import plugin_manager
import uuid
import threading
from typing import Any

# Global dictionary for tracking background tasks
# task_id -> {"process": Popen, "stdout": list, "stderr": list, "command": str}
background_tasks: dict[str, dict[str, Any]] = {}
background_tasks_lock = threading.Lock()  # Protects background_tasks dict

terminal_lock = threading.Lock()

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
    
    for line in diff_lines:
        line = line.rstrip("\n")
        if not line:
            continue
            
        if line.startswith("+++") or line.startswith("---"):
            diff_text.append(line + "\n", style="bold cyan")
        elif line.startswith("@@"):
            diff_text.append(line + "\n", style="cyan")
        elif line.startswith("+"):
            # Green text on a very dark green background
            diff_text.append(line + "\n", style="green on #0e2a14")
        elif line.startswith("-"):
            # Red text on a very dark red background
            diff_text.append(line + "\n", style="red on #3b1414")
        else:
            diff_text.append(line + "\n", style="dim")

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
                "\n  [bold yellow]⚠️  Git Safety Warning:[/bold yellow] "
                "[yellow]You have uncommitted changes in this repository.[/yellow]\n"
                "  [dim]Aizen's modifications could mix with your uncommitted work.\n"
                "  Consider committing or stashing your changes before proceeding.[/dim]\n"
            )
            _git_warned = True
    except Exception:
        pass  # Not a git repo or git not installed


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

        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return f"[File: {filepath} | {line_count} lines | {file_size:,} bytes]\n{content}"
    except PermissionError:
        logger.error("Permission denied reading '%s'", filepath)
        return f"Error: Permission denied reading '{filepath}'."
    except Exception as e:
        logger.exception("Error reading file '%s'", filepath)
        return f"Error reading file: {e}"


def write_file_with_diff(filepath: str, content: str, auto_approve: bool = False) -> str:
    """Write/overwrite a file with diff preview and optional auto-approval."""
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
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    old_content = f.read()
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
                    f"[bold magenta]Aizen wants to overwrite:[/bold magenta] [cyan]{filepath}[/cyan]",
                    border_style="magenta",
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
                    f"[bold magenta]Aizen wants to create:[/bold magenta] [cyan]{filepath}[/cyan]",
                    border_style="magenta",
                )
            )
            lang = _detect_language(filepath)
            syntax = Syntax(preview, lang, theme="monokai", line_numbers=True)
            console.print(syntax)

        # YOLO mode: skip confirmation
        if not auto_approve:
            with terminal_lock:
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


def edit_file(filepath: str, old_content: str, new_content: str, auto_approve: bool = False) -> str:
    """Surgical edit with diff preview and optional auto-approval."""
    logger.debug("edit_file: %s", filepath)
    try:
        _check_git_dirty(filepath)
        if not os.path.exists(filepath):
            match = fuzzy_match_file(filepath)
            if match:
                console.print(f"  [dim yellow]⚠️  File '{filepath}' not found, fuzzy matched to '{match}'[/dim yellow]")
                filepath = match
            else:
                return f"Error: File '{filepath}' does not exist. Use write_file to create new files."

        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            file_content = f.read()

        # Check if old_content exists in the file
        occurrence_count = file_content.count(old_content)
        if occurrence_count == 0:
            # Attempt auto-healing by using a whitespace-agnostic regex
            parts = re.split(r'\s+', old_content.strip())
            escaped_parts = [re.escape(p) for p in parts if p]
            if escaped_parts:
                pattern_str = r'\s+'.join(escaped_parts)
                try:
                    matches = list(re.finditer(pattern_str, file_content))
                    if len(matches) == 1:
                        # Exactly one match found! Auto-heal
                        actual_old = matches[0].group(0)
                        old_content = actual_old
                        console.print(f"  [dim yellow]⚡ Auto-healed whitespace mismatch in {os.path.basename(filepath)}[/dim yellow]")
                        occurrence_count = 1
                    elif len(matches) > 1:
                        return (
                            f"Error: Exact match not found, and fuzzy match found {len(matches)} occurrences. "
                            "Please be more specific."
                        )
                except Exception as e:
                    logger.debug("Auto-heal regex failed: %s", e)

        if occurrence_count == 0:
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
                f"[bold magenta]Aizen wants to edit:[/bold magenta] [cyan]{filepath}[/cyan]",
                border_style="magenta",
            )
        )
        _render_diff(diff, filepath)

        # YOLO mode: skip confirmation
        if not auto_approve:
            with terminal_lock:
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

    if not safe and not auto_approve:
        console.print(
            Panel(
                f"[bold magenta]Aizen wants to run:[/bold magenta]\n\n[white]{command}[/white]",
                border_style="magenta",
            )
        )
        with terminal_lock:
            confirmation = input("  Allow? (y/n): ").strip().lower()
        if confirmation != "y":
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
        shown_live = False

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

                # Read available stdout non-blockingly
                if proc.stdout:
                    rlist, _, _ = select.select([proc.stdout], [], [], 0.1)
                    if rlist:
                        line = proc.stdout.readline()
                        if line:
                            stdout_lines.append(line)
                            # Show live output tail (last 15 lines)
                            tail = "".join(stdout_lines[-15:])
                            display = Text()
                            display.append(f"  ▶ Running ({elapsed:.0f}s)\n", style="dim italic")
                            display.append(tail.rstrip(), style="dim")
                            live.update(display)
                            shown_live = True

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
        tool_label.append(f" → {filepath or '?'}", style="dim")
        console.print(tool_label)
        return write_file_with_diff(filepath, content, auto_approve=auto_approve)

    elif func_name == "edit_file":
        filepath = str(args.get("filepath", ""))
        old_content = str(args.get("old_content", ""))
        new_content = str(args.get("new_content", ""))
        tool_label.append(f" → {filepath or '?'}", style="dim")
        console.print(tool_label)
        return edit_file(filepath, old_content, new_content, auto_approve=auto_approve)

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

    else:
        # Check if a plugin handles this tool
        plugin_result = plugin_manager.execute_tool(tool_call, auto_approve=auto_approve)
        if plugin_result is not None:
            return plugin_result
            
        console.print(tool_label)
        return f"Unknown tool: {func_name}"

