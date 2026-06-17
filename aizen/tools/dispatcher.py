"""
Tool dispatcher — routes AI tool calls to the correct implementation.
Also defines the tools schema list exposed to the OpenAI API.
"""

import json
import os
from collections.abc import Callable

from rich.text import Text

from ..config import console
from ..logging_config import logger
from ..memory import REMEMBER_TOOL_SCHEMA, get_memory_store
from ..plugins import plugin_manager
from ..utils import truncate_output
from .browser import (
    browser_click,
    browser_evaluate,
    browser_get_content,
    browser_goto,
    browser_screenshot,
)
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
    replace_function,
    write_file_with_diff,
)
from .helpers import _ask_permission, try_repair_json
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
                    },
                },
                "required": [
                    "filepath",
                    "target_content",
                    "replacement_content",
                    "start_line",
                    "end_line",
                ],
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
                                "target_content": {"type": "string"},
                                "replacement_content": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "end_line": {"type": "integer"},
                                "allow_multiple": {"type": "boolean"},
                            },
                            "required": [
                                "target_content",
                                "replacement_content",
                                "start_line",
                                "end_line",
                            ],
                        },
                    },
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
            "description": "Extracts the abstract syntax tree (AST) outline of a file (Python, JS, TS, etc.) using tree-sitter, showing classes, methods, and docstrings without the full implementation details. Useful for exploring large codebases.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the source file.",
                    }
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_function",
            "description": "Replaces an entire function or class by name using tree-sitter AST bounds. Eliminates indentation and line-number errors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the file.",
                    },
                    "function_name": {
                        "type": "string",
                        "description": "The exact name of the function or class to replace.",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "The full replacement content for the function/class.",
                    },
                    "description": {
                        "type": "string",
                        "description": "A brief explanation of what was changed.",
                    }
                },
                "required": ["filepath", "function_name", "new_content", "description"],
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
    {
        "type": "function",
        "function": {
            "name": "semantic_search",
            "description": "Programmatically retrieve relevant code snippets based on semantic search query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The semantic search query."},
                    "limit": {
                        "type": "integer",
                        "description": "Number of results to return.",
                        "default": 5,
                    },
                    "path": {
                        "type": "string",
                        "description": "Restrict search to a specific directory or file path.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_goto",
            "description": "Navigate a headless Playwright browser to a specific URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to navigate to.",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click an element in the browser using a CSS or XPath selector.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "The selector to click.",
                    }
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_get_content",
            "description": "Get the current HTML content of the page.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": "Take a screenshot of the current page and save it to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "The path to save the screenshot.",
                    }
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_evaluate",
            "description": "Evaluate a JavaScript expression in the browser and return its string result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "The JavaScript code to evaluate.",
                    }
                },
                "required": ["script"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": "Delegate a sub-task to an autonomous background instance of Aizen. This sub-agent will run in the background in YOLO mode to complete the task. Use check_background_task to read its output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {
                        "type": "string",
                        "description": "A highly detailed prompt describing what the sub-agent should do, including which files to edit.",
                    }
                },
                "required": ["task_description"],
            },
        },
    },
    REMEMBER_TOOL_SCHEMA,
]


# ─── Tool Handler Registry ──────────────────────────────────────────────────────

# Each handler receives (args, auto_approve) and returns
# (label_suffix, result) so the dispatcher can print the tool label
# with the correct suffix before returning the result.
ToolResult = tuple[str, str]

TOOL_HANDLERS: dict[str, Callable[..., ToolResult]] = {}


def _register(name: str):
    """Decorator to register a handler function in the TOOL_HANDLERS registry."""
    def decorator(func: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
        TOOL_HANDLERS[name] = func
        return func
    return decorator


# ─── Handler Functions ──────────────────────────────────────────────────────────


@_register("read_file")
def _handle_read_file(args: dict, auto_approve: bool) -> ToolResult:
    filepath = str(args.get("filepath", ""))
    return (f" → {filepath or '?'}", truncate_output(read_file(filepath)))


@_register("write_file")
def _handle_write_file(args: dict, auto_approve: bool) -> ToolResult:
    filepath = str(args.get("filepath", ""))
    content = str(args.get("content", ""))
    start_line = args.get("start_line")
    end_line = args.get("end_line")
    if start_line is not None:
        start_line = int(start_line)
    if end_line is not None:
        end_line = int(end_line)
    result = write_file_with_diff(
        filepath, content, auto_approve=auto_approve, start_line=start_line, end_line=end_line
    )
    return (f" → {filepath or '?'}", result)


@_register("replace_file_content")
def _handle_replace_file_content(args: dict, auto_approve: bool) -> ToolResult:
    filepath = str(args.get("filepath", ""))
    target = str(args.get("target_content", ""))
    replacement = str(args.get("replacement_content", ""))
    sl = int(args.get("start_line", 1))
    el = int(args.get("end_line", 999999))
    am = bool(args.get("allow_multiple", False))
    result = replace_file_content(
        filepath, target, replacement, sl, el, am, auto_approve=auto_approve
    )
    return (f" → {filepath or '?'}", result)


@_register("multi_replace_file_content")
def _handle_multi_replace_file_content(args: dict, auto_approve: bool) -> ToolResult:
    filepath = str(args.get("filepath", ""))
    chunks = args.get("replacement_chunks", [])
    result = multi_replace_file_content(filepath, chunks, auto_approve=auto_approve)
    return (f" → {filepath or '?'} ({len(chunks)} chunks)", result)


@_register("run_terminal_command")
def _handle_run_terminal_command(args: dict, auto_approve: bool) -> ToolResult:
    command = str(args.get("command", ""))
    timeout = int(args.get("timeout", 120))
    background = bool(args.get("background", False))
    result = truncate_output(
        run_command_impl(command, auto_approve, timeout=timeout, background=background)
    )
    return (f" → {command or '?'}", result)


@_register("check_background_task")
def _handle_check_background_task(args: dict, auto_approve: bool) -> ToolResult:
    task_id = str(args.get("task_id", ""))
    return (f" → {task_id}", check_background_task_impl(task_id))


@_register("kill_background_task")
def _handle_kill_background_task(args: dict, auto_approve: bool) -> ToolResult:
    task_id = str(args.get("task_id", ""))
    return (f" → {task_id}", kill_background_task_impl(task_id))


@_register("list_directory")
def _handle_list_directory(args: dict, auto_approve: bool) -> ToolResult:
    p = str(args.get("path", "."))
    return (f" → {p}", truncate_output(list_directory(p)))


@_register("grep_search")
def _handle_grep_search(args: dict, auto_approve: bool) -> ToolResult:
    query = str(args.get("query", ""))
    path = str(args.get("path", "."))
    is_regex = bool(args.get("is_regex", False))
    return (f" → '{query or '?'}'", truncate_output(grep_search(query, path, is_regex)))


@_register("web_search")
def _handle_web_search(args: dict, auto_approve: bool) -> ToolResult:
    query = str(args.get("query", ""))
    return (f" → '{query or '?'}'", truncate_output(web_search_impl(query)))


@_register("find_files")
def _handle_find_files(args: dict, auto_approve: bool) -> ToolResult:
    pattern = str(args.get("pattern", ""))
    path = str(args.get("path", "."))
    return (f" → {pattern or '?'}", truncate_output(find_files(pattern, path)))


@_register("get_file_outline")
def _handle_get_file_outline(args: dict, auto_approve: bool) -> ToolResult:
    filepath = str(args.get("filepath", ""))
    return (f" → {filepath or '?'}", truncate_output(get_file_outline(filepath)))


@_register("replace_function")
def _handle_replace_function(args: dict, auto_approve: bool) -> ToolResult:
    filepath = str(args.get("filepath", ""))
    function_name = str(args.get("function_name", ""))
    new_content = str(args.get("new_content", ""))
    description = str(args.get("description", "AST Function Replacement"))

    if not auto_approve:
        preview = f"Replace {function_name} in {filepath}"
        if not _ask_permission("replace_function", preview):
            return ("", "Operation cancelled by user.")

    res = replace_function(filepath, function_name, new_content, description)
    return (f" → {filepath}", res)


@_register("semantic_search")
def _handle_semantic_search(args: dict, auto_approve: bool) -> ToolResult:
    query = str(args.get("query", ""))
    limit = args.get("limit", 5)
    path = args.get("path")
    from ..rag import (
        get_global_embedding_generator,
        get_global_vector_store,
        semantic_search_tool,
    )

    store = get_global_vector_store()
    embedder = get_global_embedding_generator()
    result = semantic_search_tool(store, embedder, query=query, limit=limit, path=path)
    return (f" → '{query or '?'}' (limit={limit}, path={path})", result)


@_register("remember_fact")
def _handle_remember_fact(args: dict, auto_approve: bool) -> ToolResult:
    fact = str(args.get("fact", ""))
    category = str(args.get("category", "general"))
    suffix = f" → {fact[:60]}{'...' if len(fact) > 60 else ''}"
    try:
        memory_store = get_memory_store()
        project_name = os.path.basename(os.getcwd())
        memory_id = memory_store.remember(
            fact, category=category, source="ai", project=project_name
        )
        return (suffix, f"✓ Remembered (#{memory_id}): {fact}")
    except Exception as e:
        return (suffix, f"Error storing memory: {e}")


@_register("browser_goto")
def _handle_browser_goto(args: dict, auto_approve: bool) -> ToolResult:
    url = str(args.get("url", ""))
    return (f" → {url}", browser_goto(url))

@_register("browser_click")
def _handle_browser_click(args: dict, auto_approve: bool) -> ToolResult:
    selector = str(args.get("selector", ""))
    return (f" → {selector}", browser_click(selector))

@_register("browser_get_content")
def _handle_browser_get_content(args: dict, auto_approve: bool) -> ToolResult:
    return (" → page content", browser_get_content())

@_register("browser_screenshot")
def _handle_browser_screenshot(args: dict, auto_approve: bool) -> ToolResult:
    filepath = str(args.get("filepath", ""))
    return (f" → {filepath}", browser_screenshot(filepath))

@_register("browser_evaluate")
def _handle_browser_evaluate(args: dict, auto_approve: bool) -> ToolResult:
    script = str(args.get("script", ""))
    return (" → js execution", browser_evaluate(script))

@_register("delegate_task")
def _handle_delegate_task(args: dict, auto_approve: bool) -> ToolResult:
    import sys
    task_desc = str(args.get("task_description", ""))

    # We construct a command that runs a completely isolated aizen loop in auto mode
    # --yolo ensures it doesn't block on terminal prompts.
    # We quote the task description carefully to avoid bash injection.
    import shlex
    escaped_task = shlex.quote(task_desc)
    cmd = f"{sys.executable} -m aizen.main --yolo -p {escaped_task}"

    # We reuse the run_command_impl to get standard background execution tracking
    res = run_command_impl(cmd, background=True, auto_approve=True)
    return (" → sub-agent spawned", res)

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
            console.print(f"  [yellow]⚠️  Malformed JSON from model for {func_name}[/yellow]")
            return (
                f"Error: Invalid JSON in tool arguments for '{func_name}'. "
                f"Please retry with valid JSON. The arguments received were: "
                f"{raw_args[:200]}{'...' if len(raw_args) > 200 else ''}"
            )
        else:
            console.print(f"  [dim yellow]⚠️  Repaired malformed JSON for {func_name}[/dim yellow]")

    tool_label = Text("  ⚙️  ", style="magenta")
    tool_label.append(func_name, style="dim magenta")

    # Look up handler in the registry
    handler = TOOL_HANDLERS.get(func_name)
    if handler is not None:
        label_suffix, result = handler(args, auto_approve)
        tool_label.append(label_suffix, style="dim")
        console.print(tool_label)
        return result

    # Fall back to plugin system
    plugin_result = plugin_manager.execute_tool(tool_call, auto_approve=auto_approve)
    if plugin_result is not None:
        return plugin_result

    console.print(tool_label)
    return f"Unknown tool: {func_name}"
