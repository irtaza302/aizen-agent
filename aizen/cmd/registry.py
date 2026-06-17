from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class CommandContext:
    """Shared context passed to all command handlers."""
    messages: list[dict]
    token_tracker: Any  # TokenTracker
    mcp_manager: Any  # MCPManager | None
    client: Any  # AsyncOpenAI | None

class CommandHandler(Protocol):
    async def __call__(self, arg: str, ctx: CommandContext) -> bool: ...

# Registry of commands: cmd_name -> (description, handler)
_COMMANDS: dict[str, tuple[str, CommandHandler]] = {}

def register(name: str, description: str):
    """Decorator to register a slash command handler."""
    def decorator(func):
        _COMMANDS[name] = (description, func)
        return func
    return decorator

def get_commands() -> dict[str, tuple[str, CommandHandler]]:
    return _COMMANDS

def get_slash_commands_list() -> list[tuple[str, str]]:
    """Returns a list of (command, description) for UI help menus."""
    # Special commands not explicitly registered but handled by prompt loop
    base = [("/help", "Show all available commands")]
    registered = [(cmd, desc) for cmd, (desc, _) in _COMMANDS.items() if cmd != "/help"]
    return base + registered

async def handle_slash_command(
    command_str: str, messages: list, token_tracker: Any, mcp_manager: Any = None, client: Any = None
) -> bool:
    """Handle slash commands. Returns True if the agent loop should re-process (e.g. /retry)."""
    parts = command_str.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    ctx = CommandContext(
        messages=messages,
        token_tracker=token_tracker,
        mcp_manager=mcp_manager,
        client=client,
    )

    if cmd in _COMMANDS:
        _, handler = _COMMANDS[cmd]
        return await handler(arg, ctx)

    # Special case for /help which requires all commands
    if cmd == "/help":
        _handle_help()
        return False

    from ..config import Theme, console
    console.print(
        f"  [{Theme.ERROR}]Unknown command: {cmd}[/{Theme.ERROR}] — type [bold {Theme.ACCENT}]/help[/bold {Theme.ACCENT}] for commands.\n"
    )
    return False

def _handle_help():
    from rich.table import Table

    from ..config import Theme, console

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

    # ── Search & RAG ──
    help_table.add_row(f"[bold {Theme.MUTED}]── Search & RAG ──[/bold {Theme.MUTED}]", "")
    help_table.add_row("  🔍 /search [query]", "Search the codebase using semantic (RAG) search")
    help_table.add_row("  🔄 /reindex [dir]", "Reindex the codebase for semantic search")

    # ── Agent ──
    help_table.add_row(f"[bold {Theme.MUTED}]── Agent ──[/bold {Theme.MUTED}]", "")
    help_table.add_row("  🤖 /auto [task]", "Enter autonomous mode for a complex task (max iterations apply)")

    # ── Shortcuts ──
    help_table.add_row(f"[bold {Theme.MUTED}]── Shortcuts ──[/bold {Theme.MUTED}]", "")
    help_table.add_row(f"  [{Theme.PINK}]@file / @url[/{Theme.PINK}]", "Attach file context or web URL")
    help_table.add_row(f"  [{Theme.PINK}]exit / quit[/{Theme.PINK}]", "Exit Aizen")
    help_table.add_row(
        f"  [{Theme.MUTED}]Tip[/{Theme.MUTED}]",
        f"[{Theme.MUTED}]End a line with \\\\ for multi-line input[/{Theme.MUTED}]",
    )
    console.print(help_table)
    console.print()
