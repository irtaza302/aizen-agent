#!/usr/bin/env python3
"""
Aizen AI Agent — A professional-grade AI coding assistant for your terminal.
"""

import argparse
import asyncio
import json
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
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from .commands import AizenCompleter, handle_slash_command
from .config import (
    AIZEN_ASCII,
    VERSION,
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
    auto_approve = args.yolo

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
    console.print(f"[bold #ffabf3][SYSTEM][/bold #ffabf3] Initializing Aizen AI v{VERSION}...")
    console.print(f"[bold #ffabf3][SYSTEM][/bold #ffabf3] Model: {get_active_model()}")
    if auto_approve:
        console.print("[bold #ffabf3][SYSTEM][/bold #ffabf3] Mode: YOLO")
    console.print(
        "\n[dim]Type /help for commands  •  @file to attach  •  exit to quit[/dim]\n"
    )

    # ── Keybindings ──
    kb = KeyBindings()

    @kb.add("enter", filter=has_completions & completion_is_selected)
    def _(event):
        event.current_buffer.complete_state = None

    session: PromptSession = PromptSession(completer=AizenCompleter(), key_bindings=kb)

    messages = [{"role": "system", "content": build_system_prompt(config)}]

    while True:
        try:
            # ── Multi-line Input ──
            lines = []
            prompt_html = FormattedText([
                ("fg:#ffabf3", "➜"),
                ("", " "),
                ("fg:#d3fbff", "~"),
                ("", " ")
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
                console.print("[yellow]Goodbye! 👋[/yellow]")
                break

            if not user_input.strip():
                continue

            # ── Slash Commands ──
            if user_input.strip().startswith("/"):
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

            # ── Auto-compact if context is critically full (>90%) ──
            if context_manager.needs_auto_compact() and len(messages) > 6:
                console.print("[dim yellow]⚡ Auto-compacting conversation to stay within context limits...[/dim yellow]")
                system_msg = messages[0]
                recent = messages[-4:]
                middle = messages[1:-4]
                if middle:
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
                        f"[green]✓ Auto-compacted {len(middle)} messages into a summary.[/green]\n"
                    )
                    # Recalculate token usage after compaction
                    estimated_total = context_manager.estimate_messages_tokens(
                        messages, token_tracker.estimate_tokens
                    )
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
                    ]
                )
                spinner_display = Spinner("dots", text=Text(spinner_label, style="#8e8e93 italic"), style="#ffabf3 bold")

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
                                # Live-render Markdown in a panel
                                try:
                                    # Prepend AIZEN: styling before the markdown
                                    display_content = f"**AIZEN:** {full_content}"
                                    rendered = Markdown(display_content)
                                    live.update(rendered)
                                except Exception:
                                    # Fallback for incomplete markdown
                                    display_text = Text.from_markup(f"[bold #ffabf3]AIZEN:[/bold #ffabf3] {full_content}")
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
                                if names and not full_content:
                                    tool_text = Text()
                                    tool_text.append("AIZEN: ", style="bold #ffabf3")
                                    tool_text.append(
                                        f"Invoking [#d3fbff]{', '.join(names)}[/#d3fbff]...",
                                    )
                                    live.update(tool_text)

                except AuthenticationError:
                    logger.error("Authentication failed — invalid API key")
                    console.print(
                        "\n[bold red]Authentication Error:[/bold red] Invalid API key."
                    )
                    console.print(
                        "[dim]Hint: Run with --reset-key to enter a new key.[/dim]"
                    )
                    break
                except OpenAIRateLimitError:
                    logger.warning("Rate limited by API")
                    console.print(
                        "\n[bold red]Rate Limited:[/bold red] Too many requests."
                    )
                    console.print(
                        "[dim]Hint: Wait a moment and try again, or switch to a different model.[/dim]"
                    )
                    break
                except APITimeoutError:
                    logger.warning("API request timed out")
                    console.print(
                        "\n[bold red]Timeout:[/bold red] The request timed out."
                    )
                    console.print(
                        "[dim]Hint: Check your internet connection and try again.[/dim]"
                    )
                    break
                except OpenAIConnectionError:
                    logger.warning("API connection failed")
                    console.print(
                        "\n[bold red]Connection Error:[/bold red] Could not reach the API."
                    )
                    console.print(
                        "[dim]Hint: Check your internet connection or API base URL.[/dim]"
                    )
                    break
                except BadRequestError as e:
                    logger.error("Bad request to API: %s", e)
                    console.print(f"\n[bold red]Bad Request Error:[/bold red] {e}")
                    console.print(
                        "[dim]Hint: This usually means the model ID is invalid or the context length was exceeded.[/dim]"
                    )
                    break
                except Exception as e:
                    logger.error("Unexpected API error: %s", e)
                    console.print(f"\n[bold red]API Error:[/bold red] {e}")
                    error_str = str(e).lower()
                    if "401" in error_str or "unauthorized" in error_str:
                        console.print(
                            "[dim]Hint: API key may be invalid. Run with --reset-key[/dim]"
                        )
                    elif "429" in error_str or "rate" in error_str:
                        console.print(
                            "[dim]Hint: Rate limited. Wait a moment and retry.[/dim]"
                        )
                    elif "timeout" in error_str:
                        console.print(
                            "[dim]Hint: Request timed out. Check your connection.[/dim]"
                        )
                    break
                except (asyncio.CancelledError, KeyboardInterrupt):
                    logger.warning("Generation cancelled by user")
                    console.print("\n[yellow]Generation cancelled.[/yellow]")
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
                    estimated_input = token_tracker.estimate_tokens(
                        json.dumps(messages[-1]) if messages else ""
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

            # ── Footer ──
            footer = Text()

            # Calculate estimated cost
            cost = token_tracker.get_estimated_cost(get_active_model())

            footer.append(
                f"  tokens: ~{token_tracker.total_tokens:,} (${cost:.3f})  │  " if cost > 0 else f"  tokens: ~{token_tracker.total_tokens:,}  │  "
            )
            footer.append(
                f"messages: {token_tracker.message_count}  │  "
                f"model: {get_active_model()}",
                style="dim",
            )
            # Add context usage bar
            footer.append("  │  ", style="dim")

            # Reconstruct string for dim printing to match existing pattern
            cost_display = f" (${cost:.3f})" if cost > 0 else ""
            console.print(
                f"[dim]  tokens: ~{token_tracker.total_tokens:,}{cost_display}  │  "
                f"messages: {token_tracker.message_count}  │  "
                f"model: {get_active_model()}  │  "
                f"{context_manager.get_footer_text()}[/dim]\n"
            )

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
            console.print("[yellow]Goodbye! 👋[/yellow]")
            break
        except Exception as e:
            logger.exception("Unhandled error in main loop: %s", e)
            console.print(f"\n[bold red]Error:[/bold red] {e}")
def main():
    asyncio.run(main_loop())

if __name__ == "__main__":
    main()
