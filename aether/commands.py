import os
import copy
import subprocess
import platform
from datetime import datetime
from prompt_toolkit.completion import Completer, Completion
from rich.table import Table

from .config import (
    console,
    get_active_model,
    set_active_model,
    CONFIG_PATH,
    SESSIONS_DIR,
    BACKUPS_DIR,
    load_config
)
from .utils import TokenTracker, load_gitignore_patterns, should_ignore
from .session import save_session, load_session, list_sessions
from .tools import backup_manager

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
    ("/checkpoint", "Save a named snapshot of the conversation"),
    ("/restore", "Restore a previously saved checkpoint"),
    ("/config", "View current configuration"),
    ("/mcp", "View configured MCP servers and their status"),
]

# In-memory checkpoint storage for conversation branching
_checkpoints: dict[str, list] = {}


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
    command_str: str, messages: list, token_tracker: TokenTracker, mcp_manager=None
) -> bool:
    """Handle slash commands. Returns True if the agent loop should re-process (e.g. /retry)."""
    parts = command_str.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    MODEL = get_active_model()

    if cmd == "/clear":
        if len(messages) > 1:
            messages[:] = [messages[0]]
        console.print("[green]✓ Conversation cleared.[/green]\n")

    elif cmd == "/model":
        if arg:
            set_active_model(arg)
            console.print(
                f"[green]✓ Model switched to:[/green] [bold cyan]{arg}[/bold cyan]\n"
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
        help_table.add_row("/checkpoint [name]", "Save a conversation snapshot")
        help_table.add_row("/restore [name]", "Restore a saved checkpoint")
        help_table.add_row("/config", "View current configuration")
        help_table.add_row("/mcp", "View configured MCP servers and their status")
        help_table.add_row("", "")
        help_table.add_row("@filename", "Attach file context (with autocomplete)")
        help_table.add_row("exit / quit", "Exit Aether")
        help_table.add_row("", "")
        help_table.add_row("[dim]Tip[/dim]", "[dim]End a line with \\\\ for multi-line input[/dim]")
        console.print(help_table)
        console.print()

    elif cmd == "/usage":
        console.print(token_tracker.get_summary_table(get_active_model()))
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
                # Attempt LLM-based summarization for much better context retention
                console.print("[dim]Summarizing conversation with AI...[/dim]")
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
                    f"[green]✓ Compacted {len(middle)} messages into an AI-generated summary.[/green]\n"
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

    elif cmd == "/mcp":
        if not mcp_manager:
            console.print("[yellow]MCP Manager is not available.[/yellow]\n")
            return False
            
        if not mcp_manager.config:
            console.print("[yellow]No MCP servers configured in ~/.aether_config.json[/yellow]\n")
            console.print("[dim]Add an 'mcp_servers' block to your config to enable MCP plugins.[/dim]\n")
            return False
            
        table = Table(
            title="🔌 Configured MCP Servers",
            border_style="magenta",
            header_style="bold magenta",
        )
        table.add_column("Server Name", style="cyan bold")
        table.add_column("Status", style="white")
        table.add_column("Tools Available", style="dim")
        
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
                status = "[green]Connected[/green]"
            else:
                status = "[red]Disconnected / Failed[/red]"
            
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
            f"[green]✓ Checkpoint '{name}' saved ({len(messages)} messages)[/green]\n"
        )

    elif cmd == "/restore":
        if not arg:
            if not _checkpoints:
                console.print("[yellow]No checkpoints saved. Use /checkpoint [name] first.[/yellow]\n")
            else:
                table = Table(
                    title="📌 Checkpoints",
                    border_style="magenta",
                    header_style="bold magenta",
                )
                table.add_column("Name", style="cyan")
                table.add_column("Messages", style="white", justify="right")
                for cp_name, cp_msgs in _checkpoints.items():
                    table.add_row(cp_name, str(len(cp_msgs)))
                console.print(table)
                console.print("[dim]Usage: /restore <name>[/dim]\n")
        else:
            if arg in _checkpoints:
                messages[:] = copy.deepcopy(_checkpoints[arg])
                console.print(
                    f"[green]✓ Restored checkpoint '{arg}' ({len(messages)} messages)[/green]\n"
                )
            else:
                console.print(f"[red]Checkpoint '{arg}' not found.[/red]\n")

    else:
        console.print(
            f"[red]Unknown command: {cmd}[/red] — type [bold]/help[/bold] for commands.\n"
        )

    return False
