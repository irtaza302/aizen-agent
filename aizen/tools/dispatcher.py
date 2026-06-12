"""
Tool dispatcher — routes AI tool calls to the correct implementation.
Also defines the tools schema list exposed to the OpenAI API.
"""

import json

from rich.text import Text

from ..config import Theme, console
from ..logging_config import logger
from ..plugins import plugin_manager
from ..utils import truncate_output
from .commands import (
    check_background_task_impl,
    kill_background_task_impl,
    run_command_impl,
)
from .file_ops import (
    get_file_outline,
    multi_replace_file_content,
    read_file,
    replace_file_content,
    write_file_with_diff,
)
from .helpers import try_repair_json
from .search import find_files, grep_search, list_directory, web_search_impl

# ─── Tools Schema (exposed to the AI model) ────────────────────────────────────

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
            "name": "run_terminal_command",
            "description": "Executes a shell command in a stateful, persistent bash session. Environment variables and working directory changes (cd) persist across calls. Use the timeout parameter for long-running commands like builds or test suites.",
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
                        "description": "If true, runs the command asynchronously in a separate isolated background process (not the persistent shell). Returns a task ID immediately.",
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
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information. Use when you need up-to-date docs, error messages, or API references.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    }
                },
                "required": ["query"],
            },
        },
    },
]


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
        args = try_repair_json(raw_args)
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

    elif func_name == "run_terminal_command":
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

    elif func_name == "web_search":
        query = str(args.get("query", ""))
        tool_label.append(f" → '{query or '?'}'", style="dim")
        console.print(tool_label)
        return truncate_output(web_search_impl(query))

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
