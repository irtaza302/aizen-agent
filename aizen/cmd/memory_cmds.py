import os

from rich.table import Table

from ..config import Theme, console
from .registry import CommandContext, register


@register("/remember", "Store a fact in persistent memory")
async def remember_cmd(arg: str, ctx: CommandContext) -> bool:
    if not arg:
        console.print(
            f"  [{Theme.WARNING}]Usage: /remember <fact to remember>[/{Theme.WARNING}]"
        )
        console.print(
            f"  [{Theme.MUTED}]Example: /remember This project uses FastAPI with SQLAlchemy[/{Theme.MUTED}]\n"
        )
    else:
        from ..memory import get_memory_store

        memory = get_memory_store()
        project_name = os.path.basename(os.getcwd())
        memory_id = memory.remember(arg, source="user", project=project_name)
        console.print(
            f"  [{Theme.SUCCESS}]✓ Remembered (#{memory_id}): {arg}[/{Theme.SUCCESS}]\n"
        )
    return False

@register("/memory", "View all stored memories")
async def memory_cmd(arg: str, ctx: CommandContext) -> bool:
    from ..memory import get_memory_store

    memory = get_memory_store()
    project_name = os.path.basename(os.getcwd())
    memories = memory.list_all(project=project_name)
    if not memories:
        console.print(
            f"  [{Theme.MUTED}]No memories stored yet. Use /remember <fact> to add one.[/{Theme.MUTED}]\n"
        )
    else:
        table = Table(
            title="🧠 Persistent Memory",
            border_style=Theme.BORDER,
            title_style=f"bold {Theme.ACCENT}",
        )
        table.add_column("ID", style=Theme.MUTED, width=5)
        table.add_column("Fact", style=Theme.TEXT, ratio=3)
        table.add_column("Category", style=Theme.SECONDARY, width=14)
        table.add_column("Source", style=Theme.MUTED, width=8)
        for mem in memories:
            table.add_row(
                str(mem["id"]),
                mem["fact"][:120],
                mem.get("category", "general"),
                mem.get("source", "user"),
            )
        console.print(table)
        console.print()
    return False

@register("/forget", "Remove a memory by ID (or 'all')")
async def forget_cmd(arg: str, ctx: CommandContext) -> bool:
    if not arg:
        console.print(
            f"  [{Theme.WARNING}]Usage: /forget <id> or /forget all[/{Theme.WARNING}]\n"
        )
    else:
        from ..memory import get_memory_store

        memory = get_memory_store()
        if arg.lower() == "all":
            project_name = os.path.basename(os.getcwd())
            count = memory.forget_all(project=project_name)
            console.print(
                f"  [{Theme.SUCCESS}]✓ Deleted {count} memories for this project.[/{Theme.SUCCESS}]\n"
            )
        else:
            try:
                memory_id = int(arg)
                if memory.forget(memory_id):
                    console.print(
                        f"  [{Theme.SUCCESS}]✓ Memory #{memory_id} deleted.[/{Theme.SUCCESS}]\n"
                    )
                else:
                    console.print(
                        f"  [{Theme.ERROR}]Memory #{memory_id} not found.[/{Theme.ERROR}]\n"
                    )
            except ValueError:
                console.print(
                    f"  [{Theme.ERROR}]Invalid ID. Use /forget <number> or /forget all[/{Theme.ERROR}]\n"
                )
    return False
