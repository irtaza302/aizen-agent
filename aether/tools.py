import os
import json
import re
import difflib
import fnmatch
import subprocess
from rich.panel import Panel
from rich.text import Text
from rich.syntax import Syntax
from rich.progress import Progress, SpinnerColumn, TextColumn

from .config import console, SAFE_COMMAND_PREFIXES, DANGEROUS_PATTERNS
from .utils import BackupManager, truncate_output, load_gitignore_patterns, should_ignore, Struct
from .exceptions import FileOperationError, ToolExecutionError

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
    """Render a unified diff with syntax highlighting."""
    diff_text = ""
    for line in diff_lines:
        diff_text += line if line.endswith("\n") else line + "\n"

    if diff_text.strip():
        syntax = Syntax(
            diff_text,
            "diff",
            theme="monokai",
            line_numbers=True,
            word_wrap=True,
        )
        console.print(syntax)


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

def read_file(filepath: str) -> str:
    """Read file contents with safety checks for size and binary detection."""
    try:
        if not os.path.exists(filepath):
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
        return f"Error: Permission denied reading '{filepath}'."
    except Exception as e:
        return f"Error reading file: {e}"


def write_file_with_diff(filepath: str, content: str, auto_approve: bool = False) -> str:
    """Write/overwrite a file with diff preview and optional auto-approval."""
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
            _render_diff(diff, filepath)
        else:
            preview_lines = content.split("\n")[:15]
            preview = "\n".join(preview_lines)
            total_lines = len(content.split("\n"))
            if total_lines > 15:
                preview += f"\n... ({total_lines} total lines)"

            console.print(
                Panel(
                    f"[bold magenta]Aether wants to create:[/bold magenta] [cyan]{filepath}[/cyan]",
                    border_style="magenta",
                )
            )
            lang = _detect_language(filepath)
            syntax = Syntax(preview, lang, theme="monokai", line_numbers=True)
            console.print(syntax)

        # YOLO mode: skip confirmation
        if not auto_approve:
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
        _render_diff(diff, filepath)

        # YOLO mode: skip confirmation
        if not auto_approve:
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


def run_command_impl(command: str, auto_approve: bool = False, timeout: int = 120) -> str:
    """Execute a shell command with safety checks and configurable timeout."""
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
            timeout=timeout,
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
        return f"Error: Command timed out after {timeout} seconds."
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


# ─── Tool Dispatcher ───────────────────────────────────────────────────────────

def execute_tool(tool_call, auto_approve: bool = False) -> str:
    """
    Dispatch and execute a tool call from the AI model.

    Handles JSON parsing with repair, auto_approve passthrough,
    and configurable timeouts.
    """
    func_name = tool_call.function.name
    raw_args = tool_call.function.arguments

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
        tool_label.append(f" → {command or '?'}", style="dim")
        console.print(tool_label)
        return truncate_output(run_command_impl(command, auto_approve))

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
        console.print(tool_label)
        return f"Unknown tool: {func_name}"

