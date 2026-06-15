"""
Shared helpers for tool implementations: approval prompts, JSON repair,
diff rendering, file detection, fuzzy matching, and syntax validation.
"""

import ast
import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

import questionary
from prompt_toolkit.styles import Style
from rich.text import Text

from ..config import (
    Theme,
    console,
    load_config,
    save_config,
)
from ..logging_config import logger
from ..utils import BackupManager, load_gitignore_patterns, should_ignore

# ─── Constants ──────────────────────────────────────────────────────────────────

MAX_FILE_SIZE_BYTES = 1_048_576  # 1 MB — refuse to read files larger than this
MAX_FILE_SIZE_WARNING = 512_000  # 512 KB — warn but allow
BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".svg",
        ".mp3",
        ".mp4",
        ".wav",
        ".avi",
        ".mov",
        ".mkv",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".7z",
        ".rar",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".dat",
        ".pyc",
        ".pyo",
        ".class",
        ".o",
        ".obj",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".sqlite",
        ".db",
    }
)

# Global terminal lock to prevent garbled output from concurrent tool runs
terminal_lock = threading.Lock()
backup_manager = BackupManager()

_git_warned = False
_session_auto_approve = False


# ─── Approval ───────────────────────────────────────────────────────────────────


def _ask_permission(prompt_text: str, auto_approve: bool = False) -> bool:
    """Helper to handle approval prompts with a session-wide 'always' option."""
    global _session_auto_approve
    if auto_approve or _session_auto_approve:
        return True

    # We must use synchronous input since tools run in threads
    try:
        custom_style = Style(
            [
                ("qmark", "fg:#c084fc bold"),
                ("question", "fg:#e2e8f0 bold"),
                ("answer", "fg:#22d3ee bold"),
                ("pointer", "fg:#c084fc bold"),
                ("highlighted", "fg:#ffffff bold"),
                ("selected", "fg:#22d3ee"),
                ("separator", "fg:#6b7280"),
                ("instruction", "fg:#6b7280"),
            ]
        )

        ans = questionary.select(
            prompt_text.strip(),
            choices=[
                questionary.Choice("Yes, allow this time", "y"),
                questionary.Choice("No, deny", "n"),
                questionary.Choice("Always allow (YOLO mode)", "a"),
            ],
            style=custom_style,
            instruction=" (Use ↑/↓ arrows to select, Enter to submit)",
        ).ask()

        if not ans:
            return False

        if ans in ("a", "always"):
            _session_auto_approve = True
            console.print(
                f"  [{Theme.SUCCESS}]✓ Always allow enabled and saved for future sessions.[/{Theme.SUCCESS}]"
            )
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


# ─── File Helpers ───────────────────────────────────────────────────────────────


def is_binary_file(filepath: str) -> bool:
    """Check if a file is likely binary based on extension."""
    _, ext = os.path.splitext(filepath.lower())
    return ext in BINARY_EXTENSIONS


def detect_language(filepath: str) -> str:
    """Detect Rich Syntax language from file extension for diff highlighting."""
    ext_map = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "jsx",
        ".tsx": "tsx",
        ".html": "html",
        ".css": "css",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".md": "markdown",
        ".rs": "rust",
        ".go": "go",
        ".java": "java",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".rb": "ruby",
        ".php": "php",
        ".sh": "bash",
        ".bash": "bash",
        ".zsh": "bash",
        ".sql": "sql",
        ".xml": "xml",
        ".swift": "swift",
        ".kt": "kotlin",
        ".scala": "scala",
        ".r": "r",
        ".dockerfile": "dockerfile",
        ".tf": "hcl",
    }
    _, ext = os.path.splitext(filepath.lower())
    basename = os.path.basename(filepath).lower()
    if basename in ("dockerfile", "makefile", "gemfile", "rakefile"):
        return basename
    return ext_map.get(ext, "text")


def render_diff(diff_lines: list[str], filepath: str) -> None:
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
            diff_text.append(line + "\n", style=f"{Theme.SUCCESS} on #0a1f10")
        elif line.startswith("-"):
            diff_text.append(line + "\n", style=f"{Theme.ERROR} on #2a0f0f")
        else:
            diff_text.append(line + "\n", style=Theme.MUTED)

    if len(diff_lines) > max_diff_lines:
        remaining = len(diff_lines) - max_diff_lines
        diff_text.append(f"... ({remaining} more diff lines)\n", style=f"italic {Theme.WARNING}")

    if len(diff_text) > 0:
        console.print(diff_text)


def try_repair_json(raw: str) -> dict | None:
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


def validate_syntax(filepath: str, file_content: str) -> str | None:
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
                result = subprocess.run(
                    ["eslint", "--no-warn-ignored", tmp_path], capture_output=True, text=True
                )
                if result.returncode != 0:
                    output = result.stdout.replace(tmp_path, os.path.basename(filepath))
                    return f"ESLint Error:\n{output}"
            finally:
                os.unlink(tmp_path)
    return None


# ─── Fuzzy Matching ─────────────────────────────────────────────────────────────

_fuzzy_file_cache = None
_fuzzy_file_cache_time = 0


def fuzzy_match_file(filepath: str) -> str | None:
    """
    If the exact filepath does not exist, searches the current directory tree
    for a close match. Returns the matched path or None.
    """
    if not filepath or filepath.startswith("/") or filepath.startswith("~"):
        return None  # Only fuzzy match relative paths safely

    global _fuzzy_file_cache, _fuzzy_file_cache_time
    now = time.time()

    if _fuzzy_file_cache is not None and (now - _fuzzy_file_cache_time) < 10:
        all_files = _fuzzy_file_cache
    else:
        ignore_patterns = load_gitignore_patterns()
        all_files = []

        for root, dirs, files in os.walk("."):
            dirs[:] = [d for d in dirs if not should_ignore(os.path.join(root, d), ignore_patterns)]
            for f in files:
                path = os.path.relpath(os.path.join(root, f), ".")
                if not should_ignore(path, ignore_patterns):
                    all_files.append(path)

        _fuzzy_file_cache = all_files
        _fuzzy_file_cache_time = now

    # Use difflib to find the closest match
    matches = difflib.get_close_matches(filepath, all_files, n=1, cutoff=0.75)
    return matches[0] if matches else None


def fuzzy_find_block(
    file_lines: list[str], target_content: str, start_line: int, end_line: int
) -> str | None:
    """Find the best match for target_content within the specified line bounds."""
    start_idx = max(0, start_line - 1)
    end_idx = min(len(file_lines), end_line)
    search_lines = file_lines[start_idx:end_idx]
    search_str = "".join(search_lines)

    if target_content in search_str:
        return target_content

    parts = re.split(r"\s+", target_content.strip())
    escaped_parts = [re.escape(p) for p in parts if p]
    if escaped_parts:
        pattern_str = r"\s+".join(escaped_parts)
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
        window = "".join(search_lines[i : i + window_size])
        ratio = difflib.SequenceMatcher(None, target_content, window).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = window

    if best_ratio > 0.8:
        return best_match

    return None


def check_git_dirty(filepath: str) -> None:
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
        repo_dir = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=abs_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
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
