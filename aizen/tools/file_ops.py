"""
File operation tools: read, write, replace (single and multi-block), and file outline.
"""

import ast
import difflib
import os
import re
import threading

from rich.panel import Panel
from rich.syntax import Syntax

from ..config import Theme, console
from ..logging_config import logger
from .helpers import (
    MAX_FILE_SIZE_BYTES,
    MAX_FILE_SIZE_WARNING,
    _ask_permission,
    backup_manager,
    check_git_dirty,
    detect_language,
    fuzzy_find_block,
    fuzzy_match_file,
    is_binary_file,
    render_diff,
    terminal_lock,
    validate_syntax,
)

# ── Path Traversal Protection ───────────────────────────────────────────────
# Configurable workspace root — defaults to CWD
_workspace_root: str = os.getcwd()


def set_workspace_root(path: str) -> None:
    """Set the allowed workspace root directory for file operations."""
    global _workspace_root
    _workspace_root = os.path.realpath(path)


def validate_file_path(filepath: str) -> str:
    """Resolve and validate a file path is within the workspace.

    Raises FileOperationError if path traversal is detected.
    Returns the resolved absolute path.
    """
    # Expand user home and resolve symlinks
    resolved = os.path.realpath(os.path.expanduser(filepath))
    workspace = os.path.realpath(_workspace_root)

    # Also allow home directory config files that Aizen manages
    aizen_dirs = [
        os.path.realpath(os.path.expanduser("~/.aizen")),
        os.path.realpath(os.path.expanduser("~/.aizen_backups")),
    ]

    # Check if path is within workspace or allowed dirs
    allowed = any(
        resolved.startswith(d + os.sep) or resolved == d
        for d in [workspace] + aizen_dirs
    )

    if not allowed:
        from ..exceptions import FileOperationError

        raise FileOperationError(
            f"Path traversal blocked: '{filepath}' resolves to '{resolved}' "
            f"which is outside the workspace '{workspace}'"
        )

    return resolved


# Global lock to prevent race conditions when multiple tools write to files concurrently
file_write_lock = threading.Lock()


def get_file_outline(filepath: str) -> str:
    """Extract AST/regex outline of a Python or JS/TS file."""
    try:
        filepath = validate_file_path(filepath)
        if not os.path.exists(filepath):
            match = fuzzy_match_file(filepath)
            if match:
                filepath = match
            else:
                return f"Error: File '{filepath}' does not exist."

        with open(filepath, encoding="utf-8", errors="ignore") as f:
            content = f.read()

        outline = [f"File: {filepath}\n"]

        if filepath.endswith(".py"):
            tree = ast.parse(content)
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    doc = ast.get_docstring(node)
                    doc_str = f'    """{doc}"""\n' if doc else ""
                    outline.append(f"class {node.name}:\n{doc_str}")
                    for child in node.body:
                        if isinstance(child, ast.FunctionDef):
                            cdoc = ast.get_docstring(child)
                            cdoc_str = f'        """{cdoc}"""\n' if cdoc else ""
                            outline.append(f"    def {child.name}(...):\n{cdoc_str}")
                elif isinstance(node, ast.FunctionDef):
                    doc = ast.get_docstring(node)
                    doc_str = f'    """{doc}"""\n' if doc else ""
                    outline.append(f"def {node.name}(...):\n{doc_str}")
        elif filepath.endswith((".js", ".ts", ".jsx", ".tsx")):
            lines = content.splitlines()
            for line in lines:
                line_s = line.strip()
                if re.match(r"^(export\s+)?(default\s+)?class\s+\w+", line_s):
                    outline.append(line_s)
                elif re.match(r"^(export\s+)?(default\s+)?(async\s+)?function\s+\w+", line_s):
                    outline.append(line_s)
                elif re.match(
                    r"^(export\s+)?(const|let|var)\s+\w+\s*=\s*(async\s*)?(\([^)]*\)|[^=]+)\s*=>",
                    line_s,
                ):
                    outline.append(line_s)
        else:
            return f"Error: '{filepath}' language is not supported for outlining. Use read_file instead."

        if len(outline) == 1:
            return outline[0] + "\nNo classes or functions found."
        return "\n".join(outline)
    except Exception as e:
        return f"Error getting file outline: {e}"


def read_file(filepath: str) -> str:
    """Read file contents with safety checks for size and binary detection."""
    logger.debug("read_file: %s", filepath)
    try:
        filepath = validate_file_path(filepath)
        if not os.path.exists(filepath):
            match = fuzzy_match_file(filepath)
            if match:
                console.print(
                    f"  [dim yellow]⚠️  File '{filepath}' not found, fuzzy matched to '{match}'[/dim yellow]"
                )
                filepath = match
            else:
                return f"Error: File '{filepath}' does not exist."

        # Binary file check
        if is_binary_file(filepath):
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
            console.print(f"  [yellow]⚠️  Large file: {filepath} ({size_kb:.0f} KB)[/yellow]")

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


def write_file_with_diff(
    filepath: str,
    content: str,
    auto_approve: bool = False,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """Write/overwrite a file with diff preview and optional auto-approval. Supports block rewriting."""
    logger.debug("write_file: %s (%d bytes)", filepath, len(content))
    try:
        filepath = validate_file_path(filepath)
        check_git_dirty(filepath)
        old_content = ""
        exists = os.path.exists(filepath)
        if not exists:
            match = fuzzy_match_file(filepath)
            if match:
                console.print(
                    f"  [dim yellow]⚠️  File '{filepath}' not found, fuzzy matched to '{match}'[/dim yellow]"
                )
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
            render_diff(diff, filepath)
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
            lang = detect_language(filepath)
            syntax = Syntax(preview, lang, theme="monokai", line_numbers=True)
            console.print(syntax)

        syntax_err = validate_syntax(filepath, content)
        if syntax_err:
            return f"Error: The edit introduces a syntax or linting error and was aborted.\n{syntax_err}"

        with terminal_lock:
            if not _ask_permission("  ▸ Allow?", auto_approve):
                return "User denied file write operation."

        # Create backup before overwriting
        if exists:
            backup_manager.backup(filepath)

        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with file_write_lock:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
        return f"✓ Successfully wrote to {filepath}"
    except Exception as e:
        return f"Error writing file: {e}"


def replace_file_content(
    filepath: str,
    target_content: str,
    replacement_content: str,
    start_line: int,
    end_line: int,
    allow_multiple: bool = False,
    auto_approve: bool = False,
) -> str:
    """Edits a single contiguous block of an existing file."""
    filepath = validate_file_path(filepath)
    return multi_replace_file_content(
        filepath,
        [
            {
                "target_content": target_content,
                "replacement_content": replacement_content,
                "start_line": start_line,
                "end_line": end_line,
                "allow_multiple": allow_multiple,
            }
        ],
        auto_approve,
    )


def multi_replace_file_content(
    filepath: str, replacement_chunks: list[dict], auto_approve: bool = False
) -> str:
    """Edits multiple non-adjacent blocks of an existing file."""
    try:
        filepath = validate_file_path(filepath)
        check_git_dirty(filepath)
        if not os.path.exists(filepath):
            match = fuzzy_match_file(filepath)
            if match:
                console.print(
                    f"  [dim yellow]⚠️  File '{filepath}' not found, fuzzy matched to '{match}'[/dim yellow]"
                )
                filepath = match
            else:
                return (
                    f"Error: File '{filepath}' does not exist. Use write_file to create new files."
                )

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

            actual_old = fuzzy_find_block(file_lines, target, sl, el)
            if not actual_old:
                return f"Error in chunk {idx + 1}: Could not find the specified target_content within lines {sl}-{el}. Please check your exact text."

            occurrence_count = new_file_content.count(actual_old)
            if occurrence_count == 0:
                return f"Error in chunk {idx + 1}: The text was found originally but is no longer present after preceding replacements."
            if occurrence_count > 1 and not allow_mult:
                return f"Error in chunk {idx + 1}: Found {occurrence_count} occurrences of the target text. Provide a more specific block or set allow_multiple=true."

            new_file_content = new_file_content.replace(
                actual_old, replacement, -1 if allow_mult else 1
            )
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

        syntax_err = validate_syntax(filepath, new_file_content)
        if syntax_err:
            return f"Error: The edit introduces a syntax error and was aborted.\n{syntax_err}"

        console.print(
            Panel(
                f"[bold {Theme.ACCENT}]◆ AIZEN[/bold {Theme.ACCENT}] [{Theme.TEXT}]wants to edit:[/{Theme.TEXT}] [bold {Theme.ACCENT}]{filepath}[/bold {Theme.ACCENT}]",
                border_style=Theme.BORDER,
            )
        )
        render_diff(diff, filepath)

        with terminal_lock:
            if not _ask_permission("  ▸ Apply edit?", auto_approve):
                return "User denied the edit."

        backup_manager.backup(filepath)
        with file_write_lock:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(new_file_content)

        return f"✓ Successfully applied {len(replacement_chunks)} replacement(s) to {filepath}"
    except Exception as e:
        return f"Error editing file: {e}"
