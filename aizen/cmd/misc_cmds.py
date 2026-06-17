import platform
import subprocess

from rich.table import Table

from ..config import Theme, console, get_active_model
from ..tools import backup_manager
from .registry import CommandContext, register


@register("/usage", "Show token usage statistics")
async def usage_cmd(arg: str, ctx: CommandContext) -> bool:
    console.print(ctx.token_tracker.get_summary_table(get_active_model()))
    console.print()
    return False

@register("/undo", "Undo the last file modification")
async def undo_cmd(arg: str, ctx: CommandContext) -> bool:
    result = backup_manager.undo()
    console.print(f"  [{Theme.SUCCESS}]{result}[/{Theme.SUCCESS}]\n")
    return False

@register("/retry", "Retry the last user message")
async def retry_cmd(arg: str, ctx: CommandContext) -> bool:
    while ctx.messages and ctx.messages[-1]["role"] in ("assistant", "tool"):
        ctx.messages.pop()
    if ctx.messages and ctx.messages[-1]["role"] == "user":
        console.print(f"  [{Theme.SUCCESS}]✓ Retrying last message...[/{Theme.SUCCESS}]\n")
        return True  # Signal to re-process
    else:
        console.print(f"  [{Theme.WARNING}]Nothing to retry.[/{Theme.WARNING}]\n")
    return False

@register("/copy", "Copy last AI response to clipboard")
async def copy_cmd(arg: str, ctx: CommandContext) -> bool:
    last_response = None
    for msg in reversed(ctx.messages):
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
                subprocess.run(["clip"], input=last_response, text=True, check=True)
            console.print(f"  [{Theme.SUCCESS}]✓ Copied to clipboard.[/{Theme.SUCCESS}]\n")
        except Exception:
            console.print(
                f"  [{Theme.WARNING}]⚠️  Could not copy to clipboard.[/{Theme.WARNING}]\n"
            )
    else:
        console.print(f"  [{Theme.WARNING}]No response to copy.[/{Theme.WARNING}]\n")
    return False

@register("/mcp", "View configured MCP servers and their status")
async def mcp_cmd(arg: str, ctx: CommandContext) -> bool:
    if not ctx.mcp_manager:
        console.print(f"  [{Theme.WARNING}]MCP Manager is not available.[/{Theme.WARNING}]\n")
        return False

    if not ctx.mcp_manager.config:
        console.print(
            f"  [{Theme.WARNING}]No MCP servers configured in ~/.aizen_config.json[/{Theme.WARNING}]\n"
        )
        console.print(
            f"  [{Theme.MUTED}]Add an 'mcp_servers' block to your config to enable MCP plugins.[/{Theme.MUTED}]\n"
        )
        return False

    table = Table(
        title="🔌 Configured MCP Servers",
        border_style=Theme.BORDER,
        header_style=f"bold {Theme.PRIMARY}",
    )
    table.add_column("Server Name", style=f"{Theme.ACCENT} bold")
    table.add_column("Status", style=Theme.TEXT)
    table.add_column("Tools Available", style=Theme.MUTED)

    tools = ctx.mcp_manager.get_tools()
    server_tools: dict[str, list[str]] = {srv: [] for srv in ctx.mcp_manager.config.keys()}

    for t in tools:
        name = t["function"]["name"]
        for server_name in ctx.mcp_manager.config.keys():
            prefix = f"mcp_{server_name}_"
            if name.startswith(prefix):
                server_tools[server_name].append(name[len(prefix) :])
                break

    for server_name in ctx.mcp_manager.config.keys():
        if server_name in ctx.mcp_manager.sessions:
            status = f"[{Theme.SUCCESS}]● Connected[/{Theme.SUCCESS}]"
        else:
            status = f"[{Theme.ERROR}]● Disconnected[/{Theme.ERROR}]"

        tool_count = len(server_tools[server_name])
        if tool_count > 0:
            tool_list = ", ".join(server_tools[server_name])
            if len(tool_list) > 50:
                tool_list = tool_list[:47] + "..."
            tools_display = f"{tool_count} tools: {tool_list}"
        else:
            tools_display = "0 tools"

        table.add_row(server_name, status, tools_display)

    console.print(table)
    console.print()
    return False

@register("/budget", "View or set session cost budget")
async def budget_cmd(arg: str, ctx: CommandContext) -> bool:
    if arg:
        try:
            budget_val = float(arg)
            if ctx.token_tracker is not None:
                ctx.token_tracker.budget_limit = budget_val
                console.print(
                    f"  [{Theme.SUCCESS}]✓ Budget set to ${budget_val:.2f}[/{Theme.SUCCESS}]\n"
                )
            else:
                console.print(
                    f"  [{Theme.ERROR}]Failed to set budget. Tracker not initialized.[/{Theme.ERROR}]\n"
                )
        except ValueError:
            console.print(
                f"  [{Theme.ERROR}]Invalid budget value. Use a number like: /budget 0.50[/{Theme.ERROR}]\n"
            )
    else:
        if ctx.token_tracker and ctx.token_tracker.budget_limit is not None:
            remaining = ctx.token_tracker.budget_limit - ctx.token_tracker.get_estimated_cost(
                get_active_model()
            )
            console.print(f"  [{Theme.TEXT}]💰 Budget: ${ctx.token_tracker.budget_limit:.2f}")
            console.print(
                f"  [{Theme.TEXT}]   Spent: ${ctx.token_tracker.get_estimated_cost(get_active_model()):.4f}"
            )
            console.print(f"  [{Theme.TEXT}]   Remaining: ${remaining:.4f}\n")
        else:
            console.print(
                f"  [{Theme.MUTED}]No budget set. Use /budget <amount> or --budget flag.[/{Theme.MUTED}]\n"
            )
    return False

@register("/stats", "Show cross-session usage analytics")
async def stats_cmd(arg: str, ctx: CommandContext) -> bool:
    from ..analytics import format_stats_display, get_analytics

    analytics = get_analytics()
    stats = analytics.get_summary()
    display = format_stats_display(stats)
    console.print(display)
    console.print()
    return False
