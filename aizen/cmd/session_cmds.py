import copy
from datetime import datetime

from ..config import Theme, console, get_active_model
from ..session import list_sessions, load_session, save_session
from ..context import ContextPruner
from .registry import register, CommandContext
from rich.table import Table

# In-memory checkpoint storage for conversation branching
_checkpoints: dict[str, list] = {}

@register("/clear", "Clear conversation history")
async def clear_cmd(arg: str, ctx: CommandContext) -> bool:
    if len(ctx.messages) > 1:
        ctx.messages[:] = [ctx.messages[0]]
    console.print(f"  [{Theme.SUCCESS}]✓ Conversation cleared.[/{Theme.SUCCESS}]\n")
    return False

@register("/drop", "Drop attached files/URLs from history")
async def drop_cmd(arg: str, ctx: CommandContext) -> bool:
    dropped_count = ContextPruner.prune_attached_contexts(ctx.messages)
    if dropped_count > 0:
        console.print(
            f"  [{Theme.SUCCESS}]✓ Dropped attached contexts from {dropped_count} past messages.[/{Theme.SUCCESS}]\n"
        )
    else:
        console.print(
            f"  [{Theme.WARNING}]No attached contexts found to drop.[/{Theme.WARNING}]\n"
        )
    return False

@register("/save", "Save current conversation")
async def save_cmd(arg: str, ctx: CommandContext) -> bool:
    try:
        path = save_session(ctx.messages, arg if arg else None, ctx.token_tracker)
        console.print(f"  [{Theme.SUCCESS}]✓ Session saved to {path}[/{Theme.SUCCESS}]\n")
    except Exception as e:
        console.print(f"  [{Theme.ERROR}]Error saving session: {e}[/{Theme.ERROR}]\n")
    return False

@register("/load", "Load a saved conversation")
async def load_cmd(arg: str, ctx: CommandContext) -> bool:
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
            ctx.messages[:] = loaded
            console.print(
                f"  [{Theme.SUCCESS}]✓ Loaded session '{arg}' ({len(loaded)} messages)[/{Theme.SUCCESS}]\n"
            )
        else:
            console.print(f"  [{Theme.ERROR}]Session '{arg}' not found.[/{Theme.ERROR}]\n")
    return False

@register("/export", "Export conversation to Markdown")
async def export_cmd(arg: str, ctx: CommandContext) -> bool:
    current_model = get_active_model()
    filename = arg if arg else f"aizen_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    try:
        with open(filename, "w") as f:
            f.write("# Aizen Conversation Export\n\n")
            f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"**Model:** {current_model}\n\n---\n\n")
            for msg in ctx.messages:
                if msg["role"] == "system":
                    continue
                elif msg["role"] == "user":
                    f.write(f"## 👤 You\n\n{msg['content']}\n\n")
                elif msg["role"] == "assistant" and msg.get("content"):
                    f.write(f"## ✦ Aizen\n\n{msg['content']}\n\n")
        console.print(f"  [{Theme.SUCCESS}]✓ Exported to {filename}[/{Theme.SUCCESS}]\n")
    except Exception as e:
        console.print(f"  [{Theme.ERROR}]Error exporting: {e}[/{Theme.ERROR}]\n")
    return False

@register("/compact", "Summarize conversation to save tokens")
async def compact_cmd(arg: str, ctx: CommandContext) -> bool:
    if len(ctx.messages) <= 4:
        console.print(
            f"  [{Theme.WARNING}]Conversation is already compact.[/{Theme.WARNING}]\n"
        )
    else:
        system_msg = ctx.messages[0]
        recent = ctx.messages[-4:]
        middle = ctx.messages[1:-4]

        if middle:
            console.print(
                f"  [{Theme.MUTED}]Summarizing conversation with AI...[/{Theme.MUTED}]"
            )
            try:
                _client = ctx.client
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
                    messages=summary_messages,
                    max_tokens=1000,
                )
                summary = response.choices[0].message.content or ""
            except Exception:
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

            ctx.messages[:] = [
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
            console.print(
                f"  [{Theme.WARNING}]Not enough messages to compact.[/{Theme.WARNING}]\n"
            )
    return False

@register("/checkpoint", "Save a named snapshot of the conversation")
async def checkpoint_cmd(arg: str, ctx: CommandContext) -> bool:
    name = arg or f"cp_{datetime.now().strftime('%H%M%S')}"
    _checkpoints[name] = copy.deepcopy(ctx.messages)
    console.print(
        f"  [{Theme.SUCCESS}]✓ Checkpoint '{name}' saved ({len(ctx.messages)} messages)[/{Theme.SUCCESS}]\n"
    )
    return False

@register("/restore", "Restore a previously saved checkpoint")
async def restore_cmd(arg: str, ctx: CommandContext) -> bool:
    if not arg:
        if not _checkpoints:
            console.print(
                f"  [{Theme.WARNING}]No checkpoints saved. Use /checkpoint [name] first.[/{Theme.WARNING}]\n"
            )
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
            ctx.messages[:] = copy.deepcopy(_checkpoints[arg])
            console.print(
                f"  [{Theme.SUCCESS}]✓ Restored checkpoint '{arg}' ({len(ctx.messages)} messages)[/{Theme.SUCCESS}]\n"
            )
        else:
            console.print(f"  [{Theme.ERROR}]Checkpoint '{arg}' not found.[/{Theme.ERROR}]\n")
    return False
