#!/usr/bin/env python3
"""
Aizen AI Agent — A professional-grade AI coding assistant for your terminal.
"""

import argparse
import asyncio
import base64
import json
import mimetypes
import os
import random
import re
import subprocess
import sys
from typing import Any

from openai import APIConnectionError as OpenAIConnectionError
from openai import APITimeoutError, AsyncOpenAI, AuthenticationError, BadRequestError
from openai import RateLimitError as OpenAIRateLimitError
from prompt_toolkit import PromptSession
from prompt_toolkit.filters import completion_is_selected, has_completions
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text

from .commands import AizenCompleter, handle_slash_command
from .config import (
    AIZEN_ASCII,
    VERSION,
    Theme,
    build_system_prompt,
    check_for_updates,
    console,
    fetch_openrouter_models_bg,
    get_active_model,
    get_api_key,
    get_mcp_servers,
    load_config,
    save_config,
    set_active_model,
)
from .context import ContextManager
from .logging_config import logger, setup_logging
from .mcp import MCPManager
from .plugins import plugin_manager
from .retry import retry_with_backoff
from .session import save_session
from .tools import backup_manager, execute_tool, tools
from .utils import Struct, TokenTracker, fetch_url_content, generate_directory_tree, truncate_output


def inject_file_context(user_input: str) -> str:
    context_blocks = []

    # 1. Handle command injection (@cmd:"...")
    cmd_pattern = r"(?:^|\s)@cmd:(?:\"([^\"]+)\"|\'([^\']+)\'|([^\s]+))"
    cmd_matches = re.finditer(cmd_pattern, user_input)
    for match in cmd_matches:
        cmd = match.group(1) or match.group(2) or match.group(3)
        if cmd:
            console.print(f"  [dim]⚡ Executing: {cmd}[/dim]")
            try:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
                output = result.stdout
                if result.stderr:
                    output += "\n--- STDERR ---\n" + result.stderr
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
                context_blocks.append(
                    f'<url_context url="{item}">\n{content}\n</url_context>'
                )
        elif os.path.isfile(item):
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
                    console.print(
                        f"  [dim yellow]⚠️  Failed to read {item}: {e}[/dim yellow]"
                    )
        elif os.path.isdir(item):
            try:
                tree_output = generate_directory_tree(item)
                context_blocks.append(
                    f'<directory_context path="{item}">\n{tree_output}\n</directory_context>'
                )
                console.print(f"  [dim]📂 Attached directory tree: {item}[/dim]")
            except Exception as e:
                console.print(
                    f"  [dim yellow]⚠️  Failed to read directory {item}: {e}[/dim yellow]"
                )
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
    parser.add_argument(
        "--reset-key", action="store_true", help="Reset the saved API key."
    )
    parser.add_argument(
        "--set-base-url", type=str, help="Set custom API base URL."
    )
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
    return parser.parse_args()

@retry_with_backoff(max_retries=3, backoff_base=2.0)
async def _create_api_stream(client, messages, model, active_tools):
    """
    Create a streaming API call with retry logic for transient errors.
    Retry is handled by the @retry_with_backoff decorator (with jitter).
    """
    return await client.chat.completions.create(
        model=model,
        messages=messages,
        tools=active_tools,
        tool_choice="auto",
        stream=True,
        stream_options={"include_usage": True},
    )
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

    client = AsyncOpenAI(base_url=api_base, api_key=api_key)

    token_tracker = TokenTracker()
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

    active_tools = tools + mcp_manager.get_tools() + plugin_manager.get_tools()

    # ── Header ──
    console.print(AIZEN_ASCII)
    console.print(f"  [{Theme.MUTED}]{'─' * 48}[/{Theme.MUTED}]")
    console.print(f"  {Theme.SYS} [bold {Theme.TEXT}]Initializing Aizen AI[/bold {Theme.TEXT}] [{Theme.ACCENT}]v{VERSION}[/{Theme.ACCENT}]")
    console.print(f"  {Theme.SYS} [{Theme.TEXT}]Model:[/{Theme.TEXT}] [bold {Theme.ACCENT}]{get_active_model()}[/bold {Theme.ACCENT}]")
    if auto_approve:
        console.print(f"  {Theme.SYS} [{Theme.TEXT}]Mode:[/{Theme.TEXT}] [bold {Theme.WARNING}]⚡ YOLO[/bold {Theme.WARNING}]")
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
    cyberpunk_style = Style.from_dict({
        # Completion menu layout
        "completion-menu.completion": f"bg:{Theme.SURFACE} {Theme.TEXT}",
        "completion-menu.completion.current": f"bg:{Theme.PRIMARY} #ffffff bold",
        "completion-menu.meta.completion": f"bg:{Theme.SURFACE} {Theme.MUTED}",
        "completion-menu.meta.completion.current": f"bg:{Theme.PRIMARY} #ffffff",
        "scrollbar.background": f"bg:{Theme.DIM_BORDER}",
        "scrollbar.button": f"bg:{Theme.ACCENT}",
    })

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
            ctx_color = "fg:#f87171" # ERROR
        elif ctx_pct >= 75:
            ctx_color = "fg:#fbbf24" # WARNING
        else:
            ctx_color = "fg:#4ade80" # SUCCESS
            
        return FormattedText([
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
        ])

    session: PromptSession = PromptSession(
        completer=AizenCompleter(),
        key_bindings=kb,
        style=cyberpunk_style,
        bottom_toolbar=get_bottom_toolbar
    )

    messages = [{"role": "system", "content": build_system_prompt(config)}]

    while True:
        try:
            # ── Multi-line Input ──
            lines = []
            cwd_name = os.path.basename(os.getcwd()) or "~"
            prompt_html = FormattedText([
                ("fg:#818cf8", "╭─"),
                ("fg:#c084fc bold", " aizen "),
                ("fg:#818cf8", "─ "),
                ("fg:#22d3ee", cwd_name),
                ("fg:#818cf8", " "),
                ("", "\n"),
                ("fg:#818cf8", "╰─"),
                ("fg:#c084fc bold", " ▸ "),
            ])
            first_line = await session.prompt_async(prompt_html)
            lines.append(first_line)

            # Continue reading if line ends with backslash
            while lines[-1].rstrip().endswith("\\"):
                lines[-1] = lines[-1].rstrip()[:-1]  # Remove trailing backslash
                continuation = await session.prompt_async(
                    FormattedText([("", "  ")])
                )
                lines.append(continuation)

            user_input = "\n".join(lines)

            if user_input.lower().strip() in ("exit", "quit"):
                # Auto-save on exit
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
                console.print(f"  [{Theme.PRIMARY}]Goodbye! 👋[/{Theme.PRIMARY}]")
                break

            if not user_input.strip():
                continue

            # ── Slash Commands ──
            if user_input.strip().startswith("/"):
                if user_input.strip().startswith("/auto"):
                    task_desc = user_input.strip()[5:].strip()
                    if not task_desc:
                        console.print(f"  [{Theme.WARNING}]Please provide a task. Usage: /auto <task>[/{Theme.WARNING}]")
                        continue
                    auto_approve = True
                    messages.append({
                        "role": "user",
                        "content": (
                            f"AUTONOMOUS MODE INITIATED.\nTask: {task_desc}\n\n"
                            "You are now in fully autonomous mode. Break the task into steps, execute them using your tools, "
                            "verify the results, and do NOT stop to ask for permission. Keep running tools until the task is completely finished."
                        )
                    })
                    console.print(f"  [{Theme.PRIMARY}]🤖 Autonomous mode engaged! YOLO is active.[/{Theme.PRIMARY}]\n")
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
                user_input = inject_file_context(user_input)
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
                console.print("[dim yellow]⚡ Context limit reached. Attempting smart pruning...[/dim yellow]")
                dropped_count = 0
                
                # First pass: try semantic truncation (dropping file/url/dir context blocks)
                for msg in messages[1:-2]:
                    if msg["role"] == "user" and msg.get("content"):
                        old_content = msg["content"]
                        new_content = re.sub(r'<file_context path="[^"]+">.*?</file_context>', '[File context dropped]', old_content, flags=re.DOTALL)
                        new_content = re.sub(r'<url_context url="[^"]+">.*?</url_context>', '[URL context dropped]', new_content, flags=re.DOTALL)
                        new_content = re.sub(r'<directory_context path="[^"]+">.*?</directory_context>', '[Directory context dropped]', new_content, flags=re.DOTALL)
                        new_content = re.sub(r'<command_context cmd="[^"]+">.*?</command_context>', '[Command context dropped]', new_content, flags=re.DOTALL)
                        
                        if old_content != new_content:
                            msg["content"] = new_content
                            dropped_count += 1
                
                estimated_total = context_manager.estimate_messages_tokens(messages, token_tracker.estimate_tokens)
                context_manager.update(estimated_total)
                
                # Second pass: if still over threshold, do naive summarization
                if context_manager.needs_auto_compact() and len(messages) > 6:
                    console.print("[dim yellow]⚡ Context still full. Compacting older messages...[/dim yellow]")
                    system_msg = messages[0]
                    recent = messages[-4:]
                    middle = messages[1:-4]
                    if middle:
                        user_topics = [m["content"][:100] for m in middle if m["role"] == "user" and m.get("content")]
                        summary = "Previous conversation summary: The user and assistant discussed " + "; ".join(user_topics[:5]) + ". The assistant helped with these requests."
                        messages[:] = [
                            system_msg,
                            {"role": "user", "content": f"Previous conversation summary:\n{summary}"},
                            {"role": "assistant", "content": "Understood. I have the context. How can I continue helping?"},
                        ] + recent
                        console.print(f"[green]✓ Auto-compacted {len(middle)} messages into a summary.[/green]\n")
                elif dropped_count > 0:
                    console.print(f"[green]✓ Pruned attached contexts from {dropped_count} past messages to save space.[/green]\n")
                
                estimated_total = context_manager.estimate_messages_tokens(messages, token_tracker.estimate_tokens)
                context_manager.update(estimated_total)

            # ── Agent Loop ──────────────────────────────────────────────────
            while True:
                full_content = ""
                accumulated_tool_calls = {}

                # Build spinner text
                spinner_label = random.choice(
                    [
                        "Thinking...",
                        "Analyzing...",
                        "Reasoning...",
                        "Processing...",
                        "Considering...",
                        "Exploring...",
                        "Synthesizing...",
                    ]
                )
                spinner_display = Spinner("dots2", text=Text(f" {spinner_label}", style=f"{Theme.MUTED} italic"), style=f"{Theme.PRIMARY} bold")

                try:
                    with Live(
                        spinner_display,
                        console=console,
                        refresh_per_second=8,
                    ) as live:
                        stream = await _create_api_stream(
                            client, messages, get_active_model(), active_tools
                        )

                        api_usage = None

                        async for chunk in stream:
                            # Parse API-reported usage from the final chunk
                            if hasattr(chunk, "usage") and chunk.usage:
                                api_usage = chunk.usage

                            delta = (
                                chunk.choices[0].delta if chunk.choices else None
                            )
                            if not delta:
                                continue

                            # ── Content tokens ──
                            if delta.content:
                                full_content += delta.content
                                # Live-render Markdown in a panel only if there's actual text
                                if full_content.strip():
                                    try:
                                        # Prepend styled AIZEN badge before the markdown
                                        display_content = f"**◆ AIZEN:** {full_content}"
                                        rendered = Markdown(display_content)
                                        live.update(rendered)
                                    except Exception:
                                        # Fallback for incomplete markdown
                                        display_text = Text.from_markup(f"{Theme.BADGE} {full_content}")
                                        live.update(display_text)

                            # ── Tool call tokens ──
                            if delta.tool_calls:
                                for tc in delta.tool_calls:
                                    idx = tc.index
                                    if idx not in accumulated_tool_calls:
                                        accumulated_tool_calls[idx] = {
                                            "id": "",
                                            "name": "",
                                            "arguments": "",
                                            "type": "function",
                                        }
                                    if tc.id:
                                        accumulated_tool_calls[idx]["id"] = tc.id
                                    if tc.function:
                                        if tc.function.name:
                                            accumulated_tool_calls[idx][
                                                "name"
                                            ] += tc.function.name
                                        if tc.function.arguments:
                                            accumulated_tool_calls[idx][
                                                "arguments"
                                            ] += tc.function.arguments

                                # Update spinner with tool info
                                names = [
                                    v["name"]
                                    for v in accumulated_tool_calls.values()
                                    if v["name"]
                                ]
                                if names and not full_content.strip():
                                    tool_text = Text()
                                    tool_text.append("  ◆ ", style=f"bold {Theme.ACCENT}")
                                    tool_text.append("Invoking ", style=f"{Theme.TEXT}")
                                    tool_text.append(
                                        f"{', '.join(names)}",
                                        style=f"bold {Theme.ACCENT}",
                                    )
                                    tool_text.append(" ...", style=f"{Theme.MUTED}")
                                    live.update(tool_text)

                except AuthenticationError:
                    logger.error("Authentication failed — invalid API key")
                    console.print(
                        f"\n  [bold {Theme.ERROR}]✖ Authentication Error[/bold {Theme.ERROR}] [{Theme.TEXT}]Invalid API key.[/{Theme.TEXT}]"
                    )
                    console.print(
                        f"  [{Theme.MUTED}]↳ Run with --reset-key to enter a new key.[/{Theme.MUTED}]"
                    )
                    break
                except OpenAIRateLimitError:
                    logger.warning("Rate limited by API")
                    console.print(
                        f"\n  [bold {Theme.ERROR}]✖ Rate Limited[/bold {Theme.ERROR}] [{Theme.TEXT}]Too many requests.[/{Theme.TEXT}]"
                    )
                    console.print(
                        f"  [{Theme.MUTED}]↳ Wait a moment and try again, or switch models.[/{Theme.MUTED}]"
                    )
                    break
                except APITimeoutError:
                    logger.warning("API request timed out")
                    console.print(
                        f"\n  [bold {Theme.ERROR}]✖ Timeout[/bold {Theme.ERROR}] [{Theme.TEXT}]The request timed out.[/{Theme.TEXT}]"
                    )
                    console.print(
                        f"  [{Theme.MUTED}]↳ Check your internet connection and try again.[/{Theme.MUTED}]"
                    )
                    break
                except OpenAIConnectionError:
                    logger.warning("API connection failed")
                    console.print(
                        f"\n  [bold {Theme.ERROR}]✖ Connection Error[/bold {Theme.ERROR}] [{Theme.TEXT}]Could not reach the API.[/{Theme.TEXT}]"
                    )
                    console.print(
                        f"  [{Theme.MUTED}]↳ Check your internet connection or API base URL.[/{Theme.MUTED}]"
                    )
                    break
                except BadRequestError as e:
                    logger.error("Bad request to API: %s", e)
                    console.print(f"\n  [bold {Theme.ERROR}]✖ Bad Request[/bold {Theme.ERROR}] [{Theme.TEXT}]{e}[/{Theme.TEXT}]")
                    console.print(
                        f"  [{Theme.MUTED}]↳ This usually means the model ID is invalid or context length exceeded.[/{Theme.MUTED}]"
                    )
                    break
                except Exception as e:
                    logger.error("Unexpected API error: %s", e)
                    console.print(f"\n  [bold {Theme.ERROR}]✖ API Error[/bold {Theme.ERROR}] [{Theme.TEXT}]{e}[/{Theme.TEXT}]")
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
                    break
                except (asyncio.CancelledError, KeyboardInterrupt):
                    logger.warning("Generation cancelled by user")
                    console.print(f"\n  [{Theme.WARNING}]⚡ Generation cancelled.[/{Theme.WARNING}]")
                    break

                # Track tokens — prefer API-reported usage, fall back to estimation
                if api_usage and hasattr(api_usage, "prompt_tokens"):
                    token_tracker.add_api_usage(
                        api_usage.prompt_tokens or 0,
                        api_usage.completion_tokens or 0,
                    )
                    context_manager.update(
                        (api_usage.prompt_tokens or 0) + (api_usage.completion_tokens or 0)
                    )
                elif full_content:
                    estimated_input = context_manager.estimate_messages_tokens(
                        messages, token_tracker.estimate_tokens
                    )
                    estimated_output = token_tracker.estimate_tokens(full_content)
                    token_tracker.add_usage(estimated_input, estimated_output)

                # Build tool calls list
                tool_calls_list: list[dict[str, Any]] = []
                for idx in sorted(accumulated_tool_calls.keys()):
                    tc = accumulated_tool_calls[idx]
                    tool_calls_list.append(
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": tc["arguments"],
                            },
                        }
                    )

                # Add assistant message to history
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": full_content or "",
                }
                if tool_calls_list:
                    assistant_msg["tool_calls"] = tool_calls_list
                messages.append(assistant_msg)

                # If no tool calls, we're done
                if not tool_calls_list:
                    break

                # Execute tool calls in parallel
                async def _exec_tool(tc_dict):
                    func_name = tc_dict["function"]["name"]
                    if func_name.startswith("mcp_"):
                        try:
                            args = json.loads(tc_dict["function"]["arguments"])
                            result = await mcp_manager.call_tool(func_name, args)
                        except json.JSONDecodeError:
                            result = f"Error: Invalid JSON arguments for {func_name}."
                    else:
                        func_struct = Struct(**tc_dict["function"])
                        tc_struct = Struct(
                            id=tc_dict["id"],
                            type=tc_dict["type"],
                            function=func_struct,
                        )
                        result = await asyncio.to_thread(execute_tool, tc_struct, auto_approve)

                    return {
                        "role": "tool",
                        "tool_call_id": tc_dict["id"],
                        "name": func_name,
                        "content": truncate_output(result),
                    }

                tool_results = await asyncio.gather(*[_exec_tool(tc) for tc in tool_calls_list])
                messages.extend(tool_results)

                # Continue the loop — model processes tool results

            # Footer is now handled by the persistent bottom_toolbar

        except (KeyboardInterrupt, EOFError):
            # Auto-save on interrupt
            if len(messages) > 2:
                try:
                    save_session(messages, token_tracker=token_tracker)
                    console.print("\n[dim]Session auto-saved.[/dim]")
                except Exception:
                    logger.exception("Failed to auto-save session on interrupt")
            try:
                await mcp_manager.stop()
            except Exception:
                logger.exception("Failed to stop MCP manager on interrupt")
            console.print(f"  [{Theme.PRIMARY}]Goodbye! 👋[/{Theme.PRIMARY}]")
            break
        except Exception as e:
            logger.exception("Unhandled error in main loop: %s", e)
            console.print(f"\n  [bold {Theme.ERROR}]✖ Error[/bold {Theme.ERROR}] [{Theme.TEXT}]{e}[/{Theme.TEXT}]")
def main():
    asyncio.run(main_loop())

if __name__ == "__main__":
    main()
