import asyncio

from ..config import Theme, console
from .registry import CommandContext, register


@register("/search", "Search the codebase using semantic (RAG) search")
async def search_cmd(arg: str, ctx: CommandContext) -> bool:
    from ..rag import SlashCommandRunner, get_global_embedding_generator, get_global_vector_store

    runner = SlashCommandRunner(get_global_vector_store(), get_global_embedding_generator())
    runner.run(f"/search {arg}", console)
    console.print()
    return False

@register("/reindex", "Reindex the codebase for semantic search")
async def reindex_cmd(arg: str, ctx: CommandContext) -> bool:
    from ..rag import reindex_directory

    target_dir = arg if arg else "."
    console.print(
        f"  [{Theme.MUTED}]Re-indexing codebase directory '{target_dir}' in background...[/{Theme.MUTED}]"
    )
    try:
        await asyncio.to_thread(reindex_directory, target_dir)
        console.print(f"  [{Theme.SUCCESS}]✓ Reindexing complete.[/{Theme.SUCCESS}]\n")
    except Exception as e:
        console.print(f"  [{Theme.ERROR}]Error during reindexing: {e}[/{Theme.ERROR}]\n")
    return False
