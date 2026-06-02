#!/usr/bin/env python3
"""
Aether AI Agent v2.1 — A professional-grade AI coding assistant for your terminal.
"""

import os
import sys
import re
import json
import random
import argparse
import time
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.filters import has_completions, completion_is_selected
from openai import OpenAI, AuthenticationError, RateLimitError as OpenAIRateLimitError, APITimeoutError, APIConnectionError as OpenAIConnectionError
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text
from rich.live import Live

from .config import (
    VERSION,
    AETHER_ASCII,
    SYSTEM_PROMPT,
    console,
    get_active_model,
    set_active_model,
    load_config,
    save_config,
    get_api_key,
    check_for_updates,
)
from .utils import TokenTracker, Struct
from .session import save_session
from .tools import tools, backup_manager, execute_tool
from .commands import handle_slash_command, AetherCompleter
from .context import ContextManager


# ─── Retry Configuration ───────────────────────────────────────────────────────

MAX_API_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0
RETRYABLE_ERRORS = (OpenAIRateLimitError, APITimeoutError, OpenAIConnectionError)


def inject_file_context(user_input: str) -> str:
    pattern = r"(?:^|\s)@([a-zA-Z0-9_\-\./]+)"
    matches = re.findall(pattern, user_input)
    if not matches:
        return user_input

    context_blocks = []
    for filepath in set(matches):
        if os.path.isfile(filepath):
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                context_blocks.append(
                    f'<file_context path="{filepath}">\n{content}\n</file_context>'
                )
                console.print(f"  [dim]📎 Attached: {filepath}[/dim]")
            except Exception as e:
                console.print(
                    f"  [dim yellow]⚠️  Failed to read {filepath}: {e}[/dim yellow]"
                )
        elif os.path.isdir(filepath):
            console.print(
                f"  [dim yellow]⚠️  '{filepath}' is a directory, not a file[/dim yellow]"
            )
        else:
            console.print(f"  [dim yellow]⚠️  File not found: {filepath}[/dim yellow]")

    if context_blocks:
        user_input += "\n\n" + "\n".join(context_blocks)
    return user_input


def parse_args():
    parser = argparse.ArgumentParser(
        description="Aether AI Agent — A professional-grade AI coding assistant."
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
    return parser.parse_args()


def _create_api_stream(client, messages, model):
    """
    Create a streaming API call with retry logic for transient errors.
    Returns the stream generator on success, raises on permanent failure.
    """
    last_exception: BaseException = RuntimeError("API retries exhausted")

    for attempt in range(MAX_API_RETRIES + 1):
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                stream=True,
                stream_options={"include_usage": True},
            )
            return stream
        except RETRYABLE_ERRORS as e:
            last_exception = e
            if attempt < MAX_API_RETRIES:
                delay = RETRY_BACKOFF_BASE ** attempt
                retry_msg = Text()
                retry_msg.append("  ⏳ ", style="yellow")
                retry_msg.append(f"{type(e).__name__}. ", style="dim")
                retry_msg.append(
                    f"Retrying in {delay:.0f}s... ({attempt + 1}/{MAX_API_RETRIES})",
                    style="dim italic",
                )
                console.print(retry_msg)
                time.sleep(delay)

    raise last_exception


def main():
    args = parse_args()

    if args.version:
        print(f"Aether v{VERSION}")
        sys.exit(0)

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

    client = OpenAI(base_url=api_base, api_key=api_key)

    token_tracker = TokenTracker()
    context_manager = ContextManager(get_active_model())

    # Cleanup old backups
    backup_manager.cleanup()

    # Non-blocking update check (background thread, 24h cache)
    check_for_updates(config)

    # ── Header ──
    console.print(AETHER_ASCII)
    header = Text()
    header.append(f"v{VERSION}", style="bold magenta")
    header.append("  │  ", style="dim")
    header.append(get_active_model(), style="cyan")
    if auto_approve:
        header.append("  │  ", style="dim")
        header.append("YOLO MODE", style="bold red")
    console.print(header)
    console.print(
        "[dim]Type /help for commands  •  @file to attach  •  exit to quit[/dim]\n"
    )

    # ── Keybindings ──
    kb = KeyBindings()

    @kb.add("enter", filter=has_completions & completion_is_selected)
    def _(event):
        event.current_buffer.complete_state = None

    session = PromptSession(completer=AetherCompleter(), key_bindings=kb)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            # ── Multi-line Input ──
            lines = []
            prompt_html = HTML(
                "<ansimagenta>╭─</ansimagenta> <ansimagenta><b>👤 You</b></ansimagenta>\n"
                "<ansimagenta>╰─❯</ansimagenta> "
            )
            first_line = session.prompt(prompt_html)
            lines.append(first_line)

            # Continue reading if line ends with backslash
            while lines[-1].rstrip().endswith("\\"):
                lines[-1] = lines[-1].rstrip()[:-1]  # Remove trailing backslash
                continuation = session.prompt(
                    HTML("<ansimagenta>  ⋮ </ansimagenta> ")
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
                        pass
                console.print("[yellow]Goodbye! 👋[/yellow]")
                break

            if not user_input.strip():
                continue

            # ── Slash Commands ──
            if user_input.strip().startswith("/"):
                should_retry = handle_slash_command(
                    user_input.strip(), messages, token_tracker
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
                spinner_display = Text()
                spinner_display.append("  ✦ ", style="bold magenta")
                spinner_display.append(spinner_label, style="dim italic")

                try:
                    with Live(
                        spinner_display,
                        console=console,
                        refresh_per_second=8,
                    ) as live:
                        stream = _create_api_stream(
                            client, messages, get_active_model()
                        )

                        api_usage = None

                        for chunk in stream:
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
                                    rendered = Panel(
                                        Markdown(full_content),
                                        title="[bold magenta]✦ Aether[/bold magenta]",
                                        border_style="magenta",
                                        padding=(1, 2),
                                    )
                                    live.update(rendered)
                                except Exception:
                                    # Fallback for incomplete markdown
                                    live.update(
                                        Panel(
                                            Text(full_content),
                                            title="[bold magenta]✦ Aether[/bold magenta]",
                                            border_style="magenta",
                                            padding=(1, 2),
                                        )
                                    )

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
                                    tool_text.append("  ⚙️  ", style="magenta")
                                    tool_text.append(
                                        f"Preparing: {', '.join(names)}",
                                        style="dim italic",
                                    )
                                    live.update(tool_text)

                except AuthenticationError:
                    console.print(
                        "\n[bold red]Authentication Error:[/bold red] Invalid API key."
                    )
                    console.print(
                        "[dim]Hint: Run with --reset-key to enter a new key.[/dim]"
                    )
                    break
                except OpenAIRateLimitError:
                    console.print(
                        "\n[bold red]Rate Limited:[/bold red] Too many requests."
                    )
                    console.print(
                        "[dim]Hint: Wait a moment and try again, or switch to a different model.[/dim]"
                    )
                    break
                except APITimeoutError:
                    console.print(
                        "\n[bold red]Timeout:[/bold red] The request timed out."
                    )
                    console.print(
                        "[dim]Hint: Check your internet connection and try again.[/dim]"
                    )
                    break
                except OpenAIConnectionError:
                    console.print(
                        "\n[bold red]Connection Error:[/bold red] Could not reach the API."
                    )
                    console.print(
                        "[dim]Hint: Check your internet connection or API base URL.[/dim]"
                    )
                    break
                except Exception as e:
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
                tool_calls_list = []
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
                assistant_msg = {
                    "role": "assistant",
                    "content": full_content or "",
                }
                if tool_calls_list:
                    assistant_msg["tool_calls"] = tool_calls_list
                messages.append(assistant_msg)

                # If no tool calls, we're done
                if not tool_calls_list:
                    break

                # Execute tool calls
                for tc_dict in tool_calls_list:
                    func_struct = Struct(**tc_dict["function"])
                    tc_struct = Struct(
                        id=tc_dict["id"],
                        type=tc_dict["type"],
                        function=func_struct,
                    )

                    tool_result = execute_tool(tc_struct, auto_approve)

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_dict["id"],
                            "name": tc_dict["function"]["name"],
                            "content": tool_result,
                        }
                    )

                # Continue the loop — model processes tool results

            # ── Footer ──
            footer = Text()
            footer.append(
                f"  tokens: ~{token_tracker.total_tokens:,}  │  "
                f"messages: {token_tracker.message_count}  │  "
                f"model: {get_active_model()}",
                style="dim",
            )
            # Add context usage bar
            footer.append("  │  ", style="dim")
            console.print(
                f"[dim]  tokens: ~{token_tracker.total_tokens:,}  │  "
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
                    pass
            console.print("[yellow]Goodbye! 👋[/yellow]")
            break
        except Exception as e:
            console.print(f"\n[bold red]Error:[/bold red] {e}")


if __name__ == "__main__":
    main()
