#!/usr/bin/env python3
"""
Aizen AI Agent — A professional-grade AI coding assistant for your terminal.
"""

import argparse
import asyncio
import base64
import mimetypes
import os
import re
import subprocess
import sys
from datetime import datetime

from openai import AsyncOpenAI
from prompt_toolkit import PromptSession
from prompt_toolkit.filters import completion_is_selected, has_completions
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from .agent import AgentRunner
from .commands import AizenCompleter, handle_slash_command
from .config import (
    AIZEN_ASCII,
    DANGEROUS_PATTERNS,
    VERSION,
    Theme,
    build_system_prompt,
    check_for_updates,
    console,
    fetch_openrouter_models_bg,
    get_active_model,
    get_api_key,
    get_cached_models,
    get_mcp_servers,
    load_config,
    save_config,
    set_active_model,
)
from .context import ContextManager, ContextPruner
from .exceptions import APIKeyError, SecurityError, SessionCorruptedError, UsageError
from .logging_config import logger, setup_logging
from .mcp import MCPManager
from .plugins import plugin_manager
from .session import save_session
from .tools import backup_manager, tools
from .utils import TokenTracker, fetch_url_content, generate_directory_tree

SAFE_COMMANDS = {"git", "cat", "ls", "grep", "find", "mkdir", "echo", "help"}


def inject_file_context(user_input: str, auto_approve: bool = False) -> str:
    context_blocks = []

    # 1. Handle command injection (@cmd:"...")
    cmd_pattern = r"(?:^|\s)@cmd:(?:\"([^\"]+)\"|\'([^\']+)\'|([^\s]+))"
    cmd_matches = re.finditer(cmd_pattern, user_input)
    for match in cmd_matches:
        cmd = match.group(1) or match.group(2) or match.group(3)
        if cmd:
            parts = cmd.split()
            # Safety check: validate against dangerous patterns
            is_dangerous = any(re.search(p, ' '.join(parts)) for p in DANGEROUS_PATTERNS)
            if is_dangerous:
                raise SecurityError(f"💣 Dangerous command detected: {cmd}")

            # Safe command whitelist
            if parts[0] not in SAFE_COMMANDS and not auto_approve:
                raise UsageError(
                    f"✍️  Command not in safe list. Permitted commands: {list(SAFE_COMMANDS)}\n"
                    f"Use --yolo flag to bypass."
                )

            console.print(f"  [dim]⚡ Executing: {cmd}[/dim]")
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True,
                    timeout=30, cwd=os.getcwd()
                )
                output = result.stdout
                if result.stderr:
                    output += "\n--- STDERR ---\n" + result.stderr
                # Truncate output to prevent memory exhaustion
                _max_cmd_output = 50_000  # 50KB limit
                if len(output) > _max_cmd_output:
                    output = output[:_max_cmd_output] + "\n[Output truncated at 50KB]"
                if not output.strip():
                    output = "[Command executed successfully with no output]"
                context_blocks.append(
                    f'<command_context cmd="{cmd}">\n{output}\n</command_context>'
                )
            except Exception as e:
                console.print(f"  [dim yellow]⚠️  Command failed: {e}[/dim yellow]")

    # 2. Handle standard file/url/directory injection
    pattern = r"(?:^|\s)@(?!(?:cmd:))([a-zA-Z0-9_\-\./:?&=]+)"
    matches = re.findall(pattern, user_input)
    if not matches and not context_blocks:
        return user_input

    images = []

    for item in set(matches):
        if item.startswith("http://") or item.startswith("https://"):
            console.print(f"  [dim]🌐 Fetching: {item}[/dim]")
            content = fetch_url_content(item)
            if content.startswith("Error fetching URL:"):
                console.print(f"  [dim yellow]⚠️  {content}[/dim yellow]")
            else:
                context_blocks.append(f'<url_context url="{item}">\n{content}\n</url_context>')
        elif os.path.isfile(item):
            # Path traversal sanitization
            abs_item = os.path.abspath(item)
            if os.path.commonpath([os.getcwd(), abs_item]) != os.getcwd():
                raise SecurityError(f"Attempt to access files outside project root: {item}")

            ext = os.path.splitext(item)[1].lower()
            if ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"]:
                try:
                    with open(item, "rb") as f:
                        b64_img = base64.b64encode(f.read()).decode("utf-8")
                    mime_type = mimetypes.guess_type(item)[0] or "image/png"
                    images.append(f"data:{mime_type};base64,{b64_img}")
                    console.print(f"  [dim]🖼️  Attached image: {item}[/dim]")
                except Exception as e:
                    console.print(f"  [dim yellow]⚠️  Failed to read image {item}: {e}[/dim yellow]")
            else:
                try:
                    with open(item, encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    context_blocks.append(
                        f'<file_context path="{item}">\n{content}\n</file_context>'
                    )
                    console.print(f"  [dim]📎 Attached: {item}[/dim]")
                except Exception as e:
                    console.print(f"  [dim yellow]⚠️  Failed to read {item}: {e}[/dim yellow]")
        elif os.path.isdir(item):
            # Path traversal sanitization
            abs_item = os.path.abspath(item)
            if os.path.commonpath([os.getcwd(), abs_item]) != os.getcwd():
                raise SecurityError(f"Attempt to access directory outside project root: {item}")

            try:
                tree_output = generate_directory_tree(item)
                context_blocks.append(
                    f'<directory_context path="{item}">\n{tree_output}\n</directory_context>'
                )
                console.print(f"  [dim]📂 Attached directory tree: {item}[/dim]")
            except Exception as e:
                console.print(f"  [dim yellow]⚠️  Failed to read directory {item}: {e}[/dim yellow]")
        else:
            console.print(f"  [dim yellow]⚠️  File not found: {item}[/dim yellow]")

    if context_blocks:
        user_input += "\n\n" + "\n".join(context_blocks)

    if images:
        content_list = [{"type": "text", "text": user_input}]
        for img_url in images:
            content_list.append({"type": "image_url", "image_url": {"url": img_url}})
        return content_list

    return user_input


def parse_args():
    parser = argparse.ArgumentParser(
        description="Aizen AI Agent — A professional-grade AI coding assistant."
    )
    parser.add_argument("--version", action="store_true", help="Show version.")
    parser.add_argument("--model", type=str, help="Override the default model.")
    parser.add_argument("--reset-key", action="store_true", help="Reset the saved API key.")
    parser.add_argument("--set-base-url", type=str, help="Set custom API base URL.")
    parser.add_argument(
        "--yolo",
        action="store_true",
        help="Auto-approve all tool operations (no confirmations).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging output to console.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=25,
        help="Maximum iterations for autonomous mode (default: 25).",
    )
    parser.add_argument(
        "-p",
        "--prompt",
        type=str,
        help="One-shot prompt (non-interactive mode). Runs a single turn and exits.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="Write response to a file instead of stdout (one-shot mode).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output response as JSON (one-shot mode).",
    )
    parser.add_argument(
        "--no-tools",
        action="store_true",
        help="Disable tool use (pure chat mode).",
    )
    parser.add_argument(
        "--budget",
        type=float,
        help="Maximum cost budget in USD for this session (e.g., 0.50).",
    )
    parser.add_argument(
        "--install-shell",
        action="store_true",
        help="Install shell integration (ai command) into your shell config.",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable streaming output.",
    )
    parser.add_argument(
        "--temp",
        type=float,
        help="Set temperature (default: 0.7).",
    )
    return parser.parse_args()


async def _cleanup(messages, token_tracker, mcp_manager):
    """Shared exit cleanup: auto-save session, stop MCP, log analytics."""
    if len(messages) > 2:
        try:
            save_session(messages, token_tracker=token_tracker)
            console.print("[dim]Session auto-saved.[/dim]")
        except Exception:
            logger.exception("Failed to auto-save session on exit")
    try:
        await mcp_manager.stop()
    except Exception:
        logger.exception("Failed to stop MCP manager on exit")
    try:
        from .analytics import get_analytics

        duration = (datetime.now() - token_tracker.start_time).total_seconds()
        cost = token_tracker.get_estimated_cost(get_active_model())
        project_name = os.path.basename(os.getcwd())
        get_analytics().log_session(
            model=get_active_model(),
            input_tokens=token_tracker.total_input_tokens
            + token_tracker.api_reported_input,
            output_tokens=token_tracker.total_output_tokens
            + token_tracker.api_reported_output,
            estimated_cost=cost,
            messages_count=token_tracker.message_count,
            duration_seconds=duration,
            project=project_name,
        )
    except Exception as e:
        logger.debug("Failed to log analytics: %s", e)


async def main_loop():
    args = parse_args()

    if args.version:
        print(f"Aizen v{VERSION}")
        sys.exit(0)

    # Initialize structured logging (file + optional console)
    setup_logging(verbose=getattr(args, "verbose", False))
    logger.info("Aizen starting v%s", VERSION)

    config = load_config()

    if args.set_base_url:
        config["API_BASE_URL"] = args.set_base_url
        save_config(config)
        print(f"✓ API base URL set to: {args.set_base_url}")
        sys.exit(0)

    api_key = get_api_key(config, reset=args.reset_key)

    if args.model:
        set_active_model(args.model)
    elif config.get("DEFAULT_MODEL"):
        set_active_model(config["DEFAULT_MODEL"])

    api_base = config.get("API_BASE_URL", "https://openrouter.ai/api/v1")
    auto_approve = args.yolo or config.get("auto_approve", False)

    is_auto_mode = False
    max_auto_iterations = getattr(args, "max_iterations", 25)
    auto_iteration_count = 0

    # Handle --install-shell before anything else
    if getattr(args, "install_shell", False):
        from .shell import install_shell_integration

        install_shell_integration()
        sys.exit(0)

    client = AsyncOpenAI(base_url=api_base, api_key=api_key)

    token_tracker = TokenTracker()

    # Apply budget limit if specified
    if getattr(args, "budget", None):
        token_tracker.budget_limit = args.budget

    context_manager = ContextManager(get_active_model())

    # Cleanup old backups
    backup_manager.cleanup()

    # Non-blocking update check (background thread, 24h cache)
    check_for_updates(config)

    # Non-blocking models fetch (background thread, 24h cache)
    fetch_openrouter_models_bg()

    # Initialize MCP
    mcp_servers_config = get_mcp_servers(config)
    mcp_manager = MCPManager(mcp_servers_config)
    if mcp_servers_config:
        console.print("[dim]Initializing MCP servers...[/dim]")
        await mcp_manager.start()

    active_tools = (
        []
        if getattr(args, "no_tools", False)
        else tools + mcp_manager.get_tools() + plugin_manager.get_tools()
    )

    # ── One-Shot Mode ────────────────────────────────────────────────────────
    if getattr(args, "prompt", None):
        prompt_text = args.prompt

        # Read piped stdin if available and append as context
        if not sys.stdin.isatty():
            piped_input = sys.stdin.read().strip()
            if piped_input:
                prompt_text += f"\n\n<piped_input>\n{piped_input}\n</piped_input>"

        # Process @file references in the prompt
        try:
            prompt_text = inject_file_context(prompt_text, auto_approve=auto_approve)
        except (SecurityError, UsageError) as e:
            console.print(f"\n[bold red]✖ {e.__class__.__name__}:[/bold red] {e}")
            sys.exit(1)

        messages = [{"role": "system", "content": build_system_prompt(config)}]
        messages.append({"role": "user", "content": prompt_text})

        # Check for model override in message
        model_override = None
        for m in get_cached_models():
            if prompt_text.startswith(f"@{m['id']}"):
                model_override = m["id"]
                prompt_text = prompt_text[len(f"@{m['id']}") :].strip()
                break

        runner = AgentRunner(
            client=client,
            active_tools=active_tools,
            context_manager=context_manager,
            token_tracker=token_tracker,
            mcp_manager=mcp_manager,
            auto_approve=auto_approve,
        )

        try:
            await runner.run_turn(messages, model_override=model_override)
        except Exception as e:
            console.print(f"\n[bold red]✖ Error:[/bold red] {e}")
            sys.exit(1)

        # Extract the last assistant response
        last_response = ""
        for msg in reversed(messages):
            if msg["role"] == "assistant" and msg.get("content"):
                last_response = msg["content"]
                break

        # Output handling
        if getattr(args, "json", False):
            import json as json_mod

            output = json_mod.dumps(
                {
                    "response": last_response,
                    "model": get_active_model(),
                    "tokens": token_tracker.total_tokens,
                    "cost": token_tracker.get_estimated_cost(get_active_model()),
                },
                indent=2,
            )
            if getattr(args, "output", None):
                with open(args.output, "w") as f:
                    f.write(output)
            else:
                print(output)
        elif getattr(args, "output", None):
            with open(args.output, "w") as f:
                f.write(last_response)
            console.print(f"[green]✓ Response written to {args.output}[/green]")

        # Cleanup MCP
        try:
            await mcp_manager.stop()
        except Exception:
            pass
        return

    # ── Header ──
    console.print(AIZEN_ASCII)
    console.print(f"  [{Theme.MUTED}]{'─' * 48}[/{Theme.MUTED}]")
    console.print(
        f"  {Theme.SYS} [bold {Theme.TEXT}]Initializing Aizen AI[/bold {Theme.TEXT}] [{Theme.ACCENT}]v{VERSION}[/{Theme.ACCENT}]"
    )
    console.print(
        f"  {Theme.SYS} [{Theme.TEXT}]Model:[/{Theme.TEXT}] [bold {Theme.ACCENT}]{get_active_model()}[/bold {Theme.ACCENT}]"
    )
    if auto_approve:
        console.print(
            f"  {Theme.SYS} [{Theme.TEXT}]Mode:[/{Theme.TEXT}] [bold {Theme.WARNING}]⚡ YOLO[/bold {Theme.WARNING}]"
        )
    console.print(f"  [{Theme.MUTED}]{'─' * 48}[/{Theme.MUTED}]")
    console.print(
        f"\n  [{Theme.MUTED}]/help[/{Theme.MUTED}] commands  [{Theme.MUTED}]•[/{Theme.MUTED}]  [{Theme.MUTED}]@file[/{Theme.MUTED}] attach  [{Theme.MUTED}]•[/{Theme.MUTED}]  [{Theme.MUTED}]exit[/{Theme.MUTED}] quit\n"
    )

    # ── Keybindings ──
    kb = KeyBindings()

    @kb.add("enter", filter=has_completions & completion_is_selected)
    def _(event):
        event.current_buffer.complete_state = None

    # ── Custom Prompt Styling ──
    cyberpunk_style = Style.from_dict(
        {
            # Completion menu layout
            "completion-menu.completion": f"bg:{Theme.SURFACE} {Theme.TEXT}",
            "completion-menu.completion.current": f"bg:{Theme.PRIMARY} #ffffff bold",
            "completion-menu.meta.completion": f"bg:{Theme.SURFACE} {Theme.MUTED}",
            "completion-menu.meta.completion.current": f"bg:{Theme.PRIMARY} #ffffff",
            "scrollbar.background": f"bg:{Theme.DIM_BORDER}",
            "scrollbar.button": f"bg:{Theme.ACCENT}",
        }
    )

    def get_bottom_toolbar():
        # Get dynamic stats
        cost = token_tracker.get_estimated_cost(get_active_model())
        cost_str = f" (${cost:.3f})" if cost > 0 else ""
        msgs = token_tracker.message_count
        tokens = token_tracker.total_tokens
        model = get_active_model()
        ctx_pct = context_manager.usage_percent

        # Color coding for context usage
        if ctx_pct >= 85:
            ctx_color = "fg:#f87171"  # ERROR
        elif ctx_pct >= 75:
            ctx_color = "fg:#fbbf24"  # WARNING
        else:
            ctx_color = "fg:#4ade80"  # SUCCESS

        return FormattedText(
            [
                ("bg:#1e1b2e fg:#6b7280", " ◈ "),
                ("bg:#1e1b2e fg:#e2e8f0", f"{tokens:,} tokens"),
                ("bg:#1e1b2e fg:#4ade80 bold", f"{cost_str}"),
                ("bg:#1e1b2e fg:#4b5563", " │ "),
                ("bg:#1e1b2e fg:#e2e8f0", f"{msgs}"),
                ("bg:#1e1b2e fg:#6b7280", " msgs"),
                ("bg:#1e1b2e fg:#4b5563", " │ "),
                ("bg:#1e1b2e fg:#22d3ee", f"{model}"),
                ("bg:#1e1b2e fg:#4b5563", " │ "),
                ("bg:#1e1b2e fg:#6b7280", "ctx: "),
                (f"bg:#1e1b2e {ctx_color} bold", f"{ctx_pct}%"),
                ("bg:#1e1b2e", " "),
            ]
        )

    session: PromptSession = PromptSession(
        completer=AizenCompleter(),
        key_bindings=kb,
        style=cyberpunk_style,
        bottom_toolbar=get_bottom_toolbar,
    )

    messages = [{"role": "system", "content": build_system_prompt(config)}]

    while True:
        try:
            # ── Multi-line Input ──
            lines = []
            cwd_name = os.path.basename(os.getcwd()) or "~"
            prompt_html = FormattedText(
                [
                    ("fg:#818cf8", "╭─"),
                    ("fg:#c084fc bold", " aizen "),
                    ("fg:#818cf8", "─ "),
                    ("fg:#22d3ee", cwd_name),
                    ("fg:#818cf8", " "),
                    ("", "\n"),
                    ("fg:#818cf8", "╰─"),
                    ("fg:#c084fc bold", " ▸ "),
                ]
            )
            first_line = await session.prompt_async(prompt_html)
            lines.append(first_line)

            # Continue reading if line ends with backslash
            while lines[-1].rstrip().endswith("\\"):
                lines[-1] = lines[-1].rstrip()[:-1]  # Remove trailing backslash
                continuation = await session.prompt_async(FormattedText([("", "  ")]))
                lines.append(continuation)

            user_input = "\n".join(lines)

            if user_input.lower().strip() in ("exit", "quit"):
                await _cleanup(messages, token_tracker, mcp_manager)
                console.print(f"  [{Theme.PRIMARY}]Goodbye! 👋[/{Theme.PRIMARY}]")
                break

            if not user_input.strip():
                continue

            # ── Slash Commands ──
            if user_input.strip().startswith("/"):
                if user_input.strip().startswith("/auto"):
                    task_desc = user_input.strip()[5:].strip()
                    if not task_desc:
                        console.print(
                            f"  [{Theme.WARNING}]Please provide a task. Usage: /auto <task>[/{Theme.WARNING}]"
                        )
                        continue
                    auto_approve = True
                    is_auto_mode = True
                    auto_iteration_count = 0
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"AUTONOMOUS MODE INITIATED.\nTask: {task_desc}\n\n"
                                "You are now in fully autonomous mode. Break the task into steps, execute them using your tools, "
                                "verify the results, and do NOT stop to ask for permission. Keep running tools until the task is completely finished."
                            ),
                        }
                    )
                    console.print(
                        f"  [{Theme.PRIMARY}]🤖 Autonomous mode engaged! YOLO is active.[/{Theme.PRIMARY}]\n"
                    )
                    pass  # Fall through to the agent loop
                else:
                    should_retry = await handle_slash_command(
                        user_input.strip(), messages, token_tracker, mcp_manager, client
                    )
                    if should_retry and messages and messages[-1]["role"] == "user":
                        pass  # Fall through to the agent loop
                    else:
                        continue
            else:
                # Check for model override in message
                model_override = None
                for m in get_cached_models():
                    if user_input.startswith(f"@{m['id']}"):
                        model_override = m["id"]
                        user_input = user_input[len(f"@{m['id']}") :].strip()
                        break

                try:
                    user_input = inject_file_context(user_input, auto_approve=auto_approve)
                except (SecurityError, UsageError) as e:
                    console.print(f"\n  [bold red]✖ {e.__class__.__name__}:[/bold red] {e}")
                    continue

                if not user_input.strip():
                    continue

                messages.append({"role": "user", "content": user_input})

            # ── Context Window Check ──
            estimated_total = context_manager.estimate_messages_tokens(
                messages, token_tracker.estimate_tokens
            )
            context_manager.update(estimated_total)
            warning = context_manager.check_and_warn()
            if warning:
                console.print(f"[yellow]{warning}[/yellow]\n")

            # ── Auto-compact if context is critically full ──
            if context_manager.needs_auto_compact() and len(messages) > 4:
                console.print(
                    "[dim yellow]⚡ Context limit reached. Attempting smart pruning...[/dim yellow]"
                )

                dropped_count = ContextPruner.prune_attached_contexts(messages)
                estimated_total = context_manager.estimate_messages_tokens(
                    messages, token_tracker.estimate_tokens
                )
                context_manager.update(estimated_total)

                if context_manager.needs_auto_compact() and len(messages) > 6:
                    console.print(
                        "[dim yellow]⚡ Context still full. Compacting older messages...[/dim yellow]"
                    )
                    ContextPruner.summarize_old_messages(messages)
                    console.print("[green]✓ Auto-compacted messages into a summary.[/green]\n")
                elif dropped_count > 0:
                    console.print(
                        f"[green]✓ Pruned attached contexts from {dropped_count} past messages to save space.[/green]\n"
                    )

                estimated_total = context_manager.estimate_messages_tokens(
                    messages, token_tracker.estimate_tokens
                )
                context_manager.update(estimated_total)

            # Check budget limit
            if token_tracker.budget_limit is not None:
                current_cost = token_tracker.get_estimated_cost(get_active_model())
                if current_cost >= token_tracker.budget_limit:
                    console.print(
                        f"  [{Theme.ERROR}]Budget limit reached (${current_cost:.2f} / ${token_tracker.budget_limit:.2f})[/{Theme.ERROR}]"
                    )
                    console.print(
                        f"  [{Theme.MUTED}]Increase budget with /budget <amount> or --budget flag.[/{Theme.MUTED}]\n"
                    )
                    if is_auto_mode:
                        is_auto_mode = False  # Break out of auto mode
                    messages.pop()  # Remove the last user message we just appended
                    continue

            # ── Agent Loop ──────────────────────────────────────────────────
            runner = AgentRunner(
                client=client,
                active_tools=active_tools,
                context_manager=context_manager,
                token_tracker=token_tracker,
                mcp_manager=mcp_manager,
                auto_approve=auto_approve,
                is_auto_mode=is_auto_mode,
                auto_iteration_count=auto_iteration_count,
                max_auto_iterations=max_auto_iterations,
            )

            try:
                await runner.run_turn(messages, model_override=model_override)

                # Update state back from runner (if it changed during auto mode)
                is_auto_mode = runner.is_auto_mode
                auto_iteration_count = runner.auto_iteration_count
            except Exception as e:
                logger.error("Unexpected API error: %s", e)
                console.print(
                    f"\n  [bold {Theme.ERROR}]✖ API Error[/bold {Theme.ERROR}] [{Theme.TEXT}]{e}[/{Theme.TEXT}]"
                )
                error_str = str(e).lower()
                if "401" in error_str or "unauthorized" in error_str:
                    console.print(
                        f"  [{Theme.MUTED}]↳ API key may be invalid. Run with --reset-key[/{Theme.MUTED}]"
                    )
                elif "429" in error_str or "rate" in error_str:
                    console.print(
                        f"  [{Theme.MUTED}]↳ Rate limited. Wait a moment and retry.[/{Theme.MUTED}]"
                    )
                elif "timeout" in error_str:
                    console.print(
                        f"  [{Theme.MUTED}]↳ Request timed out. Check your connection.[/{Theme.MUTED}]"
                    )

        except (KeyboardInterrupt, EOFError):
            console.print()  # Newline after ^C
            await _cleanup(messages, token_tracker, mcp_manager)
            console.print(f"  [{Theme.PRIMARY}]Goodbye! 👋[/{Theme.PRIMARY}]")
            break
        except Exception as e:
            logger.exception("Unhandled error in main loop: %s", e)
            console.print(
                f"\n  [bold {Theme.ERROR}]✖ Error[/bold {Theme.ERROR}] [{Theme.TEXT}]{e}[/{Theme.TEXT}]"
            )


def main():
    try:
        asyncio.run(main_loop())
    except APIKeyError as e:
        console.print(f"[bold red]API Key Error:[/bold red] {e}")
        sys.exit(1)
    except SessionCorruptedError as e:
        console.print(f"[bold red]Session Error:[/bold red] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
