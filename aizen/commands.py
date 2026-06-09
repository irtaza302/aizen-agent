import copy
import os
import platform
import re
import subprocess
from datetime import datetime

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.shortcuts import prompt
from rich.table import Table

from .config import (
    BACKUPS_DIR,
    CONFIG_PATH,
    SESSIONS_DIR,
    Theme,
    console,
    get_active_model,
    get_cached_models,
    load_config,
    set_active_model,
)
from .logging_config import logger
from .session import list_sessions, load_session, save_session
from .tools import backup_manager
from .utils import TokenTracker, load_gitignore_patterns, should_ignore

SLASH_COMMANDS = [
    ("/help", "Show all available commands"),
    ("/model", "View or switch the active model"),
    ("/clear", "Clear conversation history"),
    ("/drop", "Drop attached files/URLs from history"),
    ("/save", "Save current conversation"),
    ("/load", "Load a saved conversation"),
    ("/usage", "Show token usage statistics"),
    ("/compact", "Summarize conversation to save tokens"),
    ("/undo", "Undo the last file modification"),
    ("/retry", "Retry the last user message"),
    ("/copy", "Copy last AI response to clipboard"),
    ("/export", "Export conversation to Markdown"),
    ("/checkpoint", "Save a named snapshot of the conversation"),
    ("/restore", "Restore a previously saved checkpoint"),
    ("/config", "View current configuration"),
    ("/mcp", "View configured MCP servers and their status"),
    ("/commit", "Auto-generate and commit changes"),
    ("/diff", "Show all uncommitted changes"),
    ("/auto", "Enter autonomous agentic mode for a complex task"),
]

# In-memory checkpoint storage for conversation branching
_checkpoints: dict[str, list] = {}


class AizenCompleter(Completer):
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
            if " " not in stripped:
                query = stripped.lower()
                cmds_with_args = {"/model", "/save", "/load", "/export", "/checkpoint", "/restore"}
                for cmd, description in SLASH_COMMANDS:
                    if cmd.startswith(query):
                        completion_text = cmd + " " if cmd in cmds_with_args else cmd
                        yield Completion(
                            completion_text,
                            start_position=-len(stripped),
                            display=cmd,
                            display_meta=description,
                        )
            elif stripped.startswith("/model "):
                query = stripped[7:].lower()
                models = get_cached_models()
                for m in models:
                    if m["id"].lower().startswith(query) or query in m["id"].lower() or query in m["name"].lower():
                        yield Completion(
                            m["id"],
                            start_position=-len(query),
                            display=m["id"],
                            display_meta=m["name"]
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
                except Exception as e:
                    logger.debug("Failed to list directory contents for autocomplete: %s", e)
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
            except Exception as e:
                logger.debug("Failed to list current directory for autocomplete: %s", e)


async def handle_slash_command(
    command_str: str, messages: list, token_tracker: TokenTracker, mcp_manager=None, client=None
) -> bool:
    """Handle slash commands. Returns True if the agent loop should re-process (e.g. /retry)."""
    parts = command_str.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    current_model = get_active_model()

    if cmd == "/clear":
        if len(messages) > 1:
            messages[:] = [messages[0]]
        console.print(f"  [{Theme.SUCCESS}]✓ Conversation cleared.[/{Theme.SUCCESS}]\n")

    elif cmd == "/drop":
        dropped_count = 0
        for msg in messages:
            if msg["role"] == "user" and msg.get("content"):
                old_content = msg["content"]
                new_content = re.sub(
                    r'<file_context path="[^"]+">.*?</file_context>',
                    '[File context dropped to save tokens]',
                    old_content,
                    flags=re.DOTALL
                )
                new_content = re.sub(
                    r'<url_context url="[^"]+">.*?</url_context>',
                    '[URL context dropped to save tokens]',
                    new_content,
                    flags=re.DOTALL
                )
                new_content = re.sub(
                    r'<directory_context path="[^"]+">.*?</directory_context>',
                    '[Directory context dropped to save tokens]',
                    new_content,
                    flags=re.DOTALL
                )
                new_content = re.sub(
                    r'<command_context cmd="[^"]+">.*?</command_context>',
                    '[Command context dropped to save tokens]',
                    new_content,
                    flags=re.DOTALL
                )
                if old_content != new_content:
                    msg["content"] = new_content
                    dropped_count += 1
        if dropped_count > 0:
            console.print(f"  [{Theme.SUCCESS}]✓ Dropped attached contexts from {dropped_count} past messages.[/{Theme.SUCCESS}]\n")
        else:
            console.print(f"  [{Theme.WARNING}]No attached contexts found to drop.[/{Theme.WARNING}]\n")

    elif cmd == "/model":
        if arg:
            if arg.startswith("search ") or arg == "list" or arg == "search":
                models = get_cached_models()
                if not models:
                    console.print(f"  [{Theme.WARNING}]Model list is still fetching or unavailable. Try again in a moment.[/{Theme.WARNING}]\n")
                    return False

                search_query = arg[7:].lower().strip() if arg.startswith("search ") else ""

                table = Table(
                    title=f"🧠 OpenRouter Models{' (Search: ' + search_query + ')' if search_query else ''}",
                    border_style=Theme.BORDER,
                    header_style=f"bold {Theme.PRIMARY}",
                )
                table.add_column("Model ID", style=Theme.ACCENT)
                table.add_column("Name", style=Theme.TEXT)
                table.add_column("Context", style=Theme.MUTED)
                table.add_column("Pricing", style=Theme.SUCCESS)

                count = 0
                for m in models:
                    if not search_query or search_query in m["id"].lower() or search_query in m["name"].lower():
                        pricing = m.get("pricing") or {}
                        price_prompt = pricing.get("prompt", "?")
                        price_comp = pricing.get("completion", "?")
                        pricing_str = f"P: {price_prompt} C: {price_comp}"
                        table.add_row(m["id"], m["name"], str(m.get("context_length")), pricing_str)
                        count += 1
                        if count >= 30:  # limit output
                            break

                console.print(table)
                if count >= 30:
                    console.print(f"  [{Theme.MUTED}]... and more (showing top 30). Use `/model search <query>` to narrow down.[/{Theme.MUTED}]\n")
                else:
                    console.print()
            else:
                models = get_cached_models()
                found = any(m["id"] == arg for m in models)

                if models and not found:
                    console.print(f"  [{Theme.WARNING}]⚠️  Warning: Model '{arg}' not found in OpenRouter API list.[/{Theme.WARNING}]")

                set_active_model(arg, save=True)
                console.print(f"  [{Theme.SUCCESS}]✓ Model switched to:[/{Theme.SUCCESS}] [bold {Theme.ACCENT}]{arg}[/bold {Theme.ACCENT}]\n")
        else:
            console.print(f"  [bold {Theme.TEXT}]Current model:[/bold {Theme.TEXT}] [{Theme.ACCENT}]{current_model}[/{Theme.ACCENT}]")
            console.print(f"  [{Theme.MUTED}]Usage: /model <model_name>[/{Theme.MUTED}]")
            console.print(f"  [{Theme.MUTED}]       /model search <query>  (or `/model list`)[/{Theme.MUTED}]\n")

    elif cmd == "/help":
        help_table = Table(
            title="⚡ Aizen Commands",
            border_style=Theme.BORDER,
            show_header=True,
            header_style=f"bold {Theme.PRIMARY}",
            title_style=f"bold {Theme.ACCENT}",
        )
        help_table.add_column("Command", style=f"{Theme.ACCENT} bold", min_width=24)
        help_table.add_column("Description", style=Theme.TEXT)

        # ── Navigation & Info ──
        help_table.add_row(f"[bold {Theme.MUTED}]── Navigation ──[/bold {Theme.MUTED}]", "")
        help_table.add_row("  📖 /help", "Show this help message")
        help_table.add_row("  ⚙️  /config", "View current configuration")
        help_table.add_row("  📊 /usage", "Show token usage statistics")
        help_table.add_row("  🔌 /mcp", "View MCP servers and status")

        # ── Model ──
        help_table.add_row(f"[bold {Theme.MUTED}]── Model ──[/bold {Theme.MUTED}]", "")
        help_table.add_row("  🧠 /model [name]", "View or switch the active model")

        # ── Session ──
        help_table.add_row(f"[bold {Theme.MUTED}]── Session ──[/bold {Theme.MUTED}]", "")
        help_table.add_row("  💾 /save [name]", "Save current conversation")
        help_table.add_row("  📂 /load [name]", "Load a saved conversation")
        help_table.add_row("  📌 /checkpoint [n]", "Save a conversation snapshot")
        help_table.add_row("  🔄 /restore [name]", "Restore a saved checkpoint")
        help_table.add_row("  📋 /export [file]", "Export conversation to Markdown")

        # ── Editing ──
        help_table.add_row(f"[bold {Theme.MUTED}]── Editing ──[/bold {Theme.MUTED}]", "")
        help_table.add_row("  🗑️  /clear", "Clear conversation history")
        help_table.add_row("  📎 /drop", "Drop attached files/URLs from history")
        help_table.add_row("  🧹 /compact", "Summarize conversation to save tokens")
        help_table.add_row("  ↩️  /undo", "Undo the last file modification")
        help_table.add_row("  🔁 /retry", "Retry the last user message")
        help_table.add_row("  📝 /copy", "Copy last AI response to clipboard")

        # ── Git ──
        help_table.add_row(f"[bold {Theme.MUTED}]── Git ──[/bold {Theme.MUTED}]", "")
        help_table.add_row("  🔀 /commit", "Auto-generate and commit changes")
        help_table.add_row("  📊 /diff", "Show all uncommitted changes")

        # ── Agent ──
        help_table.add_row(f"[bold {Theme.MUTED}]── Agent ──[/bold {Theme.MUTED}]", "")
        help_table.add_row("  🤖 /auto [task]", "Enter autonomous mode for a complex task")

        # ── Shortcuts ──
        help_table.add_row(f"[bold {Theme.MUTED}]── Shortcuts ──[/bold {Theme.MUTED}]", "")
        help_table.add_row(f"  [{Theme.PINK}]@file / @url[/{Theme.PINK}]", "Attach file context or web URL")
        help_table.add_row(f"  [{Theme.PINK}]exit / quit[/{Theme.PINK}]", "Exit Aizen")
        help_table.add_row(f"  [{Theme.MUTED}]Tip[/{Theme.MUTED}]", f"[{Theme.MUTED}]End a line with \\\\ for multi-line input[/{Theme.MUTED}]")
        console.print(help_table)
        console.print()

    elif cmd == "/usage":
        console.print(token_tracker.get_summary_table(get_active_model()))
        console.print()

    elif cmd == "/save":
        try:
            path = save_session(messages, arg if arg else None, token_tracker)
            console.print(f"  [{Theme.SUCCESS}]✓ Session saved to {path}[/{Theme.SUCCESS}]\n")
        except Exception as e:
            console.print(f"  [{Theme.ERROR}]Error saving session: {e}[/{Theme.ERROR}]\n")

    elif cmd == "/load":
        if not arg:
            sessions = list_sessions()
            if not sessions:
                console.print(f"  [{Theme.WARNING}]No saved sessions found.[/{Theme.WARNING}]\n")
            else:
                table = Table(
                    title="📂 Saved Sessions",
                    border_style=Theme.BORDER,
                    header_style=f"bold {Theme.PRIMARY}",
                )
                table.add_column("Name", style=Theme.ACCENT)
                table.add_column("Saved At", style=Theme.MUTED)
                table.add_column("Messages", style="white", justify="right")
                for s in sessions[:10]:
                    table.add_row(s["name"], s["saved_at"][:19], str(s["messages"]))
                console.print(table)
                console.print(f"  [{Theme.MUTED}]Usage: /load <session_name>[/{Theme.MUTED}]\n")
        else:
            loaded = load_session(arg)
            if loaded:
                messages[:] = loaded
                console.print(
                    f"  [{Theme.SUCCESS}]✓ Loaded session '{arg}' ({len(loaded)} messages)[/{Theme.SUCCESS}]\n"
                )
            else:
                console.print(f"  [{Theme.ERROR}]Session '{arg}' not found.[/{Theme.ERROR}]\n")

    elif cmd == "/undo":
        result = backup_manager.undo()
        console.print(f"  [{Theme.SUCCESS}]{result}[/{Theme.SUCCESS}]\n")

    elif cmd == "/retry":
        # Remove last assistant + tool messages, then re-process the last user message
        while messages and messages[-1]["role"] in ("assistant", "tool"):
            messages.pop()
        if messages and messages[-1]["role"] == "user":
            console.print(f"  [{Theme.SUCCESS}]✓ Retrying last message...[/{Theme.SUCCESS}]\n")
            return True  # Signal to re-process
        else:
            console.print(f"  [{Theme.WARNING}]Nothing to retry.[/{Theme.WARNING}]\n")

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
                console.print(f"  [{Theme.SUCCESS}]✓ Copied to clipboard.[/{Theme.SUCCESS}]\n")
            except Exception:
                console.print(
                    f"  [{Theme.WARNING}]⚠️  Could not copy to clipboard.[/{Theme.WARNING}]\n"
                )
        else:
            console.print(f"  [{Theme.WARNING}]No response to copy.[/{Theme.WARNING}]\n")

    elif cmd == "/export":
        filename = (
            arg
            if arg
            else f"aizen_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        )
        try:
            with open(filename, "w") as f:
                f.write("# Aizen Conversation Export\n\n")
                f.write(
                    f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                f.write(f"**Model:** {current_model}\n\n---\n\n")
                for msg in messages:
                    if msg["role"] == "system":
                        continue
                    elif msg["role"] == "user":
                        f.write(f"## 👤 You\n\n{msg['content']}\n\n")
                    elif msg["role"] == "assistant" and msg.get("content"):
                        f.write(f"## ✦ Aizen\n\n{msg['content']}\n\n")
            console.print(f"  [{Theme.SUCCESS}]✓ Exported to {filename}[/{Theme.SUCCESS}]\n")
        except Exception as e:
            console.print(f"  [{Theme.ERROR}]Error exporting: {e}[/{Theme.ERROR}]\n")

    elif cmd == "/compact":
        if len(messages) <= 4:
            console.print(f"  [{Theme.WARNING}]Conversation is already compact.[/{Theme.WARNING}]\n")
        else:
            system_msg = messages[0]
            recent = messages[-4:]
            middle = messages[1:-4]

            if middle:
                # Attempt LLM-based summarization for much better context retention
                console.print(f"  [{Theme.MUTED}]Summarizing conversation with AI...[/{Theme.MUTED}]")
                try:
                    from openai import AsyncOpenAI as _AsyncOpenAI

                    _config = load_config()
                    _api_key = _config.get("OPENROUTER_API_KEY", "")
                    _api_base = _config.get("API_BASE_URL", "https://openrouter.ai/api/v1")
                    _client = _AsyncOpenAI(base_url=_api_base, api_key=_api_key)

                    # Build a summarization request from the middle messages
                    summary_messages = [
                        {
                            "role": "system",
                            "content": (
                                "Summarize the following conversation between a user and an AI coding assistant. "
                                "Focus on: what files were discussed/modified, what tasks were completed, "
                                "what decisions were made, and any important context for continuing the work. "
                                "Be concise but thorough. Output only the summary."
                            ),
                        },
                        {
                            "role": "user",
                            "content": "\n".join(
                                f"[{m['role']}]: {(m.get('content') or '')[:500]}"
                                for m in middle
                                if m.get("content")
                            ),
                        },
                    ]

                    response = await _client.chat.completions.create(
                        model=get_active_model(),
                        messages=summary_messages,  # type: ignore
                        max_tokens=1000,
                    )
                    summary = response.choices[0].message.content or ""
                except Exception:
                    # Fallback to naive summarization if API call fails
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
                    {"role": "user", "content": f"Previous conversation summary:\n{summary}"},
                    {
                        "role": "assistant",
                        "content": "Understood. I have the context from our previous discussion. How can I continue helping?",
                    },
                ] + recent
                console.print(
                    f"  [{Theme.SUCCESS}]✓ Compacted {len(middle)} messages into an AI-generated summary.[/{Theme.SUCCESS}]\n"
                )
            else:
                console.print(f"  [{Theme.WARNING}]Not enough messages to compact.[/{Theme.WARNING}]\n")

    elif cmd == "/config":
        config = load_config()
        table = Table(
            title="⚙️  Configuration",
            border_style=Theme.BORDER,
            header_style=f"bold {Theme.PRIMARY}",
        )
        table.add_column("Key", style=Theme.ACCENT)
        table.add_column("Value", style=Theme.TEXT)
        table.add_row("Model", current_model)
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

    elif cmd == "/mcp":
        if not mcp_manager:
            console.print(f"  [{Theme.WARNING}]MCP Manager is not available.[/{Theme.WARNING}]\n")
            return False

        if not mcp_manager.config:
            console.print(f"  [{Theme.WARNING}]No MCP servers configured in ~/.aizen_config.json[/{Theme.WARNING}]\n")
            console.print(f"  [{Theme.MUTED}]Add an 'mcp_servers' block to your config to enable MCP plugins.[/{Theme.MUTED}]\n")
            return False

        table = Table(
            title="🔌 Configured MCP Servers",
            border_style=Theme.BORDER,
            header_style=f"bold {Theme.PRIMARY}",
        )
        table.add_column("Server Name", style=f"{Theme.ACCENT} bold")
        table.add_column("Status", style=Theme.TEXT)
        table.add_column("Tools Available", style=Theme.MUTED)

        tools = mcp_manager.get_tools()
        server_tools: dict[str, list[str]] = {srv: [] for srv in mcp_manager.config.keys()}

        for t in tools:
            name = t["function"]["name"]
            for server_name in mcp_manager.config.keys():
                prefix = f"mcp_{server_name}_"
                if name.startswith(prefix):
                    server_tools[server_name].append(name[len(prefix):])
                    break

        for server_name in mcp_manager.config.keys():
            if server_name in mcp_manager.sessions:
                status = f"[{Theme.SUCCESS}]● Connected[/{Theme.SUCCESS}]"
            else:
                status = f"[{Theme.ERROR}]● Disconnected[/{Theme.ERROR}]"

            tool_count = len(server_tools[server_name])
            if tool_count > 0:
                tool_list = ", ".join(server_tools[server_name])
                # Truncate if too long
                if len(tool_list) > 50:
                    tool_list = tool_list[:47] + "..."
                tools_display = f"{tool_count} tools: {tool_list}"
            else:
                tools_display = "0 tools"

            table.add_row(server_name, status, tools_display)

        console.print(table)
        console.print()

    elif cmd == "/checkpoint":
        name = arg or f"cp_{datetime.now().strftime('%H%M%S')}"
        _checkpoints[name] = copy.deepcopy(messages)
        console.print(
            f"  [{Theme.SUCCESS}]✓ Checkpoint '{name}' saved ({len(messages)} messages)[/{Theme.SUCCESS}]\n"
        )

    elif cmd == "/restore":
        if not arg:
            if not _checkpoints:
                console.print(f"  [{Theme.WARNING}]No checkpoints saved. Use /checkpoint [name] first.[/{Theme.WARNING}]\n")
            else:
                table = Table(
                    title="📌 Checkpoints",
                    border_style=Theme.BORDER,
                    header_style=f"bold {Theme.PRIMARY}",
                )
                table.add_column("Name", style=Theme.ACCENT)
                table.add_column("Messages", style="white", justify="right")
                for cp_name, cp_msgs in _checkpoints.items():
                    table.add_row(cp_name, str(len(cp_msgs)))
                console.print(table)
                console.print(f"  [{Theme.MUTED}]Usage: /restore <name>[/{Theme.MUTED}]\n")
        else:
            if arg in _checkpoints:
                messages[:] = copy.deepcopy(_checkpoints[arg])
                console.print(
                    f"  [{Theme.SUCCESS}]✓ Restored checkpoint '{arg}' ({len(messages)} messages)[/{Theme.SUCCESS}]\n"
                )
            else:
                console.print(f"  [{Theme.ERROR}]Checkpoint '{arg}' not found.[/{Theme.ERROR}]\n")

    elif cmd == "/commit":
        if not client:
            console.print(f"  [{Theme.ERROR}]API client is not available for /commit.[/{Theme.ERROR}]\n")
            return False

        try:
            # Check staged changes
            result = subprocess.run(["git", "diff", "--cached"], capture_output=True, text=True, check=True)
            diff = result.stdout.strip()

            if not diff:
                # Check unstaged
                result_unstaged = subprocess.run(["git", "diff"], capture_output=True, text=True, check=True)
                unstaged_diff = result_unstaged.stdout.strip()

                if not unstaged_diff:
                    console.print(f"  [{Theme.WARNING}]No changes found to commit.[/{Theme.WARNING}]\n")
                    return False

                answer = prompt("No staged changes. Stage all current changes? [Y/n] ")
                if answer.lower() not in ("y", "yes", ""):
                    console.print(f"  [{Theme.WARNING}]Commit aborted.[/{Theme.WARNING}]\n")
                    return False

                subprocess.run(["git", "add", "-u"], check=True)
                result = subprocess.run(["git", "diff", "--cached"], capture_output=True, text=True, check=True)
                diff = result.stdout.strip()

            if not diff:
                console.print(f"  [{Theme.WARNING}]No changes staged to commit.[/{Theme.WARNING}]\n")
                return False

            console.print(f"  [{Theme.MUTED}]Generating commit message...[/{Theme.MUTED}]")

            commit_messages = [
                {"role": "system", "content": "You are a senior developer. Write a concise, conventional commit message for the following diff. Output ONLY the commit message, no explanation, no markdown blocks."},
                {"role": "user", "content": f"Diff:\n{diff[:10000]}"}
            ]

            response = await client.chat.completions.create(
                model=get_active_model(),
                messages=commit_messages,
                max_tokens=200,
            )
            commit_msg = response.choices[0].message.content.strip()
            # Remove any markdown codeblocks if model didn't listen
            commit_msg = commit_msg.replace("```text", "").replace("```", "").strip()

            console.print(f"\n  [bold {Theme.TEXT}]Generated Commit Message:[/bold {Theme.TEXT}]")
            console.print(f"  [{Theme.ACCENT}]{commit_msg}[/{Theme.ACCENT}]\n")

            action = prompt("Commit with this message? [Y/n/e(dit)] ")
            action = action.lower().strip()

            if action in ("y", "yes", ""):
                final_msg = commit_msg
            elif action in ("e", "edit"):
                final_msg = prompt("Edit message: ", default=commit_msg)
            else:
                console.print("[yellow]Commit aborted.[/yellow]\n")
                return False

            subprocess.run(["git", "commit", "-m", final_msg], check=True)
            console.print(f"  [{Theme.SUCCESS}]✓ Committed successfully.[/{Theme.SUCCESS}]\n")

        except subprocess.CalledProcessError:
            console.print(f"  [{Theme.ERROR}]Error: Not a git repository or git command failed.[/{Theme.ERROR}]\n")
        except Exception as e:
            console.print(f"  [{Theme.ERROR}]Error during auto-commit: {e}[/{Theme.ERROR}]\n")

    elif cmd == "/diff":
        try:
            # Show staged + unstaged changes
            result_staged = subprocess.run(
                ["git", "diff", "--cached", "--stat"],
                capture_output=True, text=True, check=True
            )
            result_unstaged = subprocess.run(
                ["git", "diff", "--stat"],
                capture_output=True, text=True, check=True
            )
            result_untracked = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                capture_output=True, text=True, check=True
            )

            has_output = False

            if result_staged.stdout.strip():
                console.print(f"  [bold {Theme.SUCCESS}]Staged changes:[/bold {Theme.SUCCESS}]")
                console.print(f"[dim]{result_staged.stdout.strip()}[/dim]")
                has_output = True

            if result_unstaged.stdout.strip():
                console.print(f"  [bold {Theme.WARNING}]Unstaged changes:[/bold {Theme.WARNING}]")
                console.print(f"[dim]{result_unstaged.stdout.strip()}[/dim]")
                has_output = True

            if result_untracked.stdout.strip():
                untracked = result_untracked.stdout.strip().split("\n")
                console.print(f"  [bold {Theme.ACCENT}]Untracked files ({len(untracked)}):[/bold {Theme.ACCENT}]")
                for f in untracked[:20]:
                    console.print(f"  [dim]+ {f}[/dim]")
                if len(untracked) > 20:
                    console.print(f"  [dim]... and {len(untracked) - 20} more[/dim]")
                has_output = True

            if not has_output:
                console.print(f"  [{Theme.SUCCESS}]✓ Working tree is clean.[/{Theme.SUCCESS}]")

            # Show full diff if requested
            if arg == "--full" or arg == "-f":
                result_full = subprocess.run(
                    ["git", "diff"],
                    capture_output=True, text=True, check=True
                )
                if result_full.stdout.strip():
                    from rich.syntax import Syntax
                    syntax = Syntax(result_full.stdout, "diff", theme="monokai")
                    console.print(syntax)

            console.print()
        except subprocess.CalledProcessError:
            console.print(f"  [{Theme.ERROR}]Error: Not a git repository or git command failed.[/{Theme.ERROR}]\n")
        except Exception as e:
            console.print(f"  [{Theme.ERROR}]Error showing diff: {e}[/{Theme.ERROR}]\n")

    else:
        console.print(
            f"  [{Theme.ERROR}]Unknown command: {cmd}[/{Theme.ERROR}] — type [bold {Theme.ACCENT}]/help[/bold {Theme.ACCENT}] for commands.\n"
        )

    return False
