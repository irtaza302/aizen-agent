from rich.table import Table

from ..config import (
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
from .registry import CommandContext, register


@register("/model", "View or switch the active model")
async def model_cmd(arg: str, ctx: CommandContext) -> bool:
    current_model = get_active_model()
    if arg:
        if arg.startswith("search ") or arg == "list" or arg == "search":
            models = get_cached_models()
            if not models:
                console.print(
                    f"  [{Theme.WARNING}]Model list is still fetching or unavailable. Try again in a moment.[/{Theme.WARNING}]\n"
                )
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
                if (
                    not search_query
                    or search_query in m["id"].lower()
                    or search_query in m["name"].lower()
                ):
                    pricing = m.get("pricing") or {}
                    price_prompt = pricing.get("prompt", "?")
                    price_comp = pricing.get("completion", "?")
                    pricing_str = f"P: {price_prompt} C: {price_comp}"
                    table.add_row(m["id"], m["name"], str(m.get("context_length")), pricing_str)
                    count += 1
                    if count >= 30:
                        break

            console.print(table)
            if count >= 30:
                console.print(
                    f"  [{Theme.MUTED}]... and more (showing top 30). Use `/model search <query>` to narrow down.[/{Theme.MUTED}]\n"
                )
            else:
                console.print()
        else:
            models = get_cached_models()
            found = any(m["id"] == arg for m in models)

            if models and not found:
                console.print(
                    f"  [{Theme.WARNING}]⚠️  Warning: Model '{arg}' not found in OpenRouter API list.[/{Theme.WARNING}]"
                )

            set_active_model(arg, save=True)
            console.print(
                f"  [{Theme.SUCCESS}]✓ Model switched to:[/{Theme.SUCCESS}] [bold {Theme.ACCENT}]{arg}[/bold {Theme.ACCENT}]\n"
            )
    else:
        console.print(
            f"  [bold {Theme.TEXT}]Current model:[/bold {Theme.TEXT}] [{Theme.ACCENT}]{current_model}[/{Theme.ACCENT}]"
        )
        console.print(f"  [{Theme.MUTED}]Usage: /model <model_name>[/{Theme.MUTED}]")
        console.print(
            f"  [{Theme.MUTED}]       /model search <query>  (or `/model list`)[/{Theme.MUTED}]\n"
        )
    return False

@register("/config", "View current configuration")
async def config_cmd(arg: str, ctx: CommandContext) -> bool:
    config = load_config()
    current_model = get_active_model()
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
    return False
