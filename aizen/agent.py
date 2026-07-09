"""
AgentRunner — Encapsulates the core agent turn loop.

Extracted from main.py to enable:
- Isolated testing with mocked clients
- Cleaner separation between CLI plumbing and agent logic
- Reuse in non-interactive contexts (e.g., scripted pipelines)
"""

import asyncio
import json
import random
import re
from typing import Any

from openai import (
    APIConnectionError as OpenAIConnectionError,
)
from openai import (
    APITimeoutError,
)
from openai import (
    RateLimitError as OpenAIRateLimitError,
)
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text

from .config import Theme, console, get_active_model
from .logging_config import logger
from .tools import execute_tool
from .utils import Struct, truncate_output


class AgentRunner:
    """Handles a single conversational turn: stream → parse → execute tools → loop."""

    def __init__(
        self,
        client,
        active_tools: list[dict],
        context_manager,
        token_tracker,
        mcp_manager=None,
        auto_approve: bool = False,
        is_auto_mode: bool = False,
        auto_iteration_count: int = 0,
        max_auto_iterations: int = 50,
    ):
        self.tool_semaphore = asyncio.Semaphore(3)
        self.client = client
        self.active_tools = active_tools
        self.context_manager = context_manager
        self.token_tracker = token_tracker
        self.mcp_manager = mcp_manager
        self.auto_approve = auto_approve
        self.is_auto_mode = is_auto_mode
        self.auto_iteration_count = auto_iteration_count
        self.max_auto_iterations = max_auto_iterations

        # Precompile regex for cleaning up thought tags
        self._cleaning_pattern = re.compile(
            r"<think>.*?(?:</think>|$)|<\|channel>thought.*?(?:<channel\|>|$)",
            flags=re.DOTALL,
        )

    async def run_turn(self, messages: list[dict], model_override: str | None = None) -> None:
        """
        Execute a full agent turn: stream the model's response, handle tool calls,
        and loop until the model produces a final text response (no more tool calls).
        """
        while True:
            if self.is_auto_mode:
                self.auto_iteration_count += 1
                if self.auto_iteration_count > self.max_auto_iterations:
                    console.print(
                        f"  [{Theme.WARNING}]⚠️  Autonomous mode reached iteration limit "
                        f"({self.max_auto_iterations}). Exiting auto mode.[/{Theme.WARNING}]"
                    )
                    self.is_auto_mode = False
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"You have reached the maximum number of autonomous iterations "
                                f"({self.max_auto_iterations}). Please provide a brief summary "
                                f"of what you have accomplished and what remains."
                            ),
                        }
                    )
                    self.auto_iteration_count = 0

            # Stream the response
            stream_result = await self._stream_response(messages, model_override=model_override)
            if stream_result is None:
                break  # Error occurred (already printed to console)

            full_content, tool_calls_list, api_usage = stream_result

            # Track tokens
            self._track_tokens(messages, full_content, api_usage)

            # Add assistant message to history
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": full_content or "",
            }
            if tool_calls_list:
                assistant_msg["tool_calls"] = tool_calls_list
            messages.append(assistant_msg)

            # If no tool calls, we're done with this turn
            if not tool_calls_list:
                if not full_content.strip():
                    console.print(
                        f"  [{Theme.WARNING}]⚠️ The model returned an empty response. "
                        f"This can happen with free models or when the context is too large.[/{Theme.WARNING}]"
                    )
                break

            # Execute tool calls
            tool_results = await self._execute_tools(tool_calls_list)
            messages.extend(tool_results)

            # Loop continues — model processes tool results

    async def _stream_response(
        self,
        messages: list[dict],
        model_override: str | None = None,
    ) -> tuple[str, list[dict], Any] | None:
        """
        Stream a response from the model.

        Returns (full_content, tool_calls_list, api_usage) or None on error.
        Handles KeyboardInterrupt gracefully — returns partial content instead of crashing.
        """
        content_chunks: list[str] = []
        accumulated_tool_calls: dict[int, dict] = {}
        api_usage = None
        cancelled = False

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

        if self.is_auto_mode:
            spinner_text = Text(
                f" [Step {self.auto_iteration_count}/{self.max_auto_iterations}] {spinner_label}",
                style=f"{Theme.MUTED} italic",
            )
        else:
            spinner_text = Text(f" {spinner_label}", style=f"{Theme.MUTED} italic")

        spinner_display = Spinner("dots2", text=spinner_text, style=f"{Theme.PRIMARY} bold")

        model = model_override or get_active_model()
        max_retries = 3
        backoff_base = 2.0

        try:
            with Live(spinner_display, console=console, refresh_per_second=8) as live:
                from openai import AsyncStream

                api_params: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                }
                if self.active_tools:
                    api_params["tools"] = self.active_tools
                    api_params["tool_choice"] = "auto"

                # Retry logic for transient API errors
                stream: AsyncStream | None = None
                for attempt in range(max_retries + 1):
                    try:
                        stream = await self.client.chat.completions.create(**api_params)
                        break
                    except (OpenAIRateLimitError, APITimeoutError, OpenAIConnectionError) as e:
                        if attempt < max_retries:
                            delay = backoff_base**attempt * (1.0 + random.uniform(-0.25, 0.25))
                            retry_text = Text()
                            retry_text.append("  ⏳ ", style="yellow")
                            retry_text.append(f"{type(e).__name__}. ", style="dim")
                            retry_text.append(
                                f"Retrying in {delay:.1f}s... ({attempt + 1}/{max_retries})",
                                style="dim italic",
                            )
                            live.update(retry_text)
                            await asyncio.sleep(delay)
                        else:
                            raise

                if stream is None:
                    return None

                try:
                    async for chunk in stream:
                        if hasattr(chunk, "usage") and chunk.usage:
                            api_usage = chunk.usage

                        delta = chunk.choices[0].delta if chunk.choices else None
                        if not delta:
                            continue

                        if delta.content:
                            content_chunks.append(delta.content)
                            full_so_far = ''.join(content_chunks)
                            if full_so_far.strip():
                                try:
                                    # Strip reasoning/thought tags for cleaner UI display
                                    cleaned_content = self._cleaning_pattern.sub("", full_so_far)

                                    display_content = f"**◆ AIZEN:** {cleaned_content.strip()}"
                                    rendered = Markdown(display_content)
                                    live.update(rendered)
                                except Exception:
                                    display_text = Text.from_markup(f"{Theme.BADGE} {full_so_far}")
                                    live.update(display_text)

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
                                        accumulated_tool_calls[idx]["name"] += tc.function.name
                                    if tc.function.arguments:
                                        accumulated_tool_calls[idx]["arguments"] += (
                                            tc.function.arguments
                                        )

                            names = [
                                v["name"] for v in accumulated_tool_calls.values() if v["name"]
                            ]
                            if names and not ''.join(content_chunks).strip():
                                tool_text = Text()
                                tool_text.append("  ◆ ", style=f"bold {Theme.ACCENT}")
                                tool_text.append("Invoking ", style=f"{Theme.TEXT}")
                                tool_text.append(
                                    f"{', '.join(names)}", style=f"bold {Theme.ACCENT}"
                                )
                                tool_text.append(" ...", style=f"{Theme.MUTED}")
                                live.update(tool_text)

                except KeyboardInterrupt:
                    # Graceful cancellation — save partial content, return to prompt
                    cancelled = True
                    try:
                        await stream.close()
                    except Exception:
                        pass

        except KeyboardInterrupt:
            cancelled = True
        except Exception:
            # Re-raise — let the caller (main_loop) handle specific exception types
            raise

        full_content = ''.join(content_chunks)

        if cancelled:
            console.print(f"\n  [{Theme.WARNING}]⚡ Response cancelled.[/{Theme.WARNING}]")
            if full_content.strip():
                # Return partial content so it's saved in message history
                return full_content + "\n\n[Response cancelled by user]", [], api_usage
            return None

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

        return full_content, tool_calls_list, api_usage

    async def _execute_tools(self, tool_calls_list: list[dict]) -> list[dict]:
        """Execute tool calls (in parallel where safe) and return tool result messages."""

        async def _exec_tool(tc_dict: dict) -> dict:
            async with self.tool_semaphore:
                try:
                    func_name = tc_dict["function"]["name"]
                    if func_name.startswith("mcp_") and self.mcp_manager:
                        try:
                            args = json.loads(tc_dict["function"]["arguments"])
                            result = await asyncio.wait_for(self.mcp_manager.call_tool(func_name, args), timeout=60.0)
                        except json.JSONDecodeError:
                            result = f"Error: Invalid JSON arguments for {func_name}."
                    else:
                        func_struct = Struct(**tc_dict["function"])
                        tc_struct = Struct(
                            id=tc_dict["id"],
                            type=tc_dict["type"],
                            function=func_struct,
                        )
                        result = await asyncio.wait_for(asyncio.to_thread(execute_tool, tc_struct, self.auto_approve), timeout=120.0)

                    return {
                        "role": "tool",
                        "tool_call_id": tc_dict["id"],
                        "name": func_name,
                        "content": truncate_output(result),
                    }
                except asyncio.TimeoutError:
                    return {
                        "role": "tool",
                        "tool_call_id": tc_dict["id"],
                        "name": tc_dict["function"]["name"],
                        "content": "Error: Tool execution timed out.",
                    }

        tool_results = await asyncio.gather(
            *[_exec_tool(tc) for tc in tool_calls_list],
            return_exceptions=True,
        )

        # Handle individual tool failures gracefully
        for i, result in enumerate(tool_results):
            if isinstance(result, Exception):
                logger.error("Tool execution failed: %s", result)
                tool_results[i] = {
                    "role": "tool",
                    "tool_call_id": tool_calls_list[i]["id"],
                    "name": tool_calls_list[i]["function"]["name"],
                    "content": f"Error: Tool execution failed — {type(result).__name__}: {result}",
                }

        return list(tool_results)

    def _track_tokens(self, messages, full_content, api_usage):
        """Update token tracking from API usage or estimation."""
        if api_usage and hasattr(api_usage, "prompt_tokens"):
            self.token_tracker.add_api_usage(
                api_usage.prompt_tokens or 0,
                api_usage.completion_tokens or 0,
            )
            self.context_manager.update(
                (api_usage.prompt_tokens or 0) + (api_usage.completion_tokens or 0)
            )
        elif full_content:
            estimated_input = self.context_manager.estimate_messages_tokens(
                messages, self.token_tracker.estimate_tokens
            )
            estimated_output = self.token_tracker.estimate_tokens(full_content)
            self.token_tracker.add_usage(estimated_input, estimated_output)
