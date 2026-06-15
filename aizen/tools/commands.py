"""
Command execution tools: run_command, background task management.
"""

import os
import re
import subprocess
import threading
import time
import uuid
from typing import Any

from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from ..config import DANGEROUS_PATTERNS, SAFE_COMMAND_PREFIXES, Theme, console
from ..logging_config import logger
from .helpers import _ask_permission, terminal_lock

# Global dictionary for tracking background tasks
# task_id -> {"process": Popen, "stdout": list, "stderr": list, "command": str}
background_tasks: dict[str, dict[str, Any]] = {}
background_tasks_lock = threading.Lock()  # Protects background_tasks dict


class PersistentTerminal:
    """A stateful bash terminal session that persists across command executions."""

    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.marker = "===AIZEN_CMD_END==="
        self.stdout_buf = []
        self.stderr_buf = []

    def _start(self):
        self.proc = subprocess.Popen(
            ["bash"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=os.environ.copy(),
        )

        def reader(pipe, buf):
            for line in iter(pipe.readline, ""):
                buf.append(line)

        threading.Thread(
            target=reader, args=(self.proc.stdout, self.stdout_buf), daemon=True
        ).start()
        threading.Thread(
            target=reader, args=(self.proc.stderr, self.stderr_buf), daemon=True
        ).start()

    def run(self, command: str, timeout: int = 120) -> tuple[str, str, int, bool, str]:
        """Runs a command and returns (stdout, stderr, exit_code, timeout_occurred, new_pwd)."""
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()

            self.stdout_buf.clear()
            self.stderr_buf.clear()

            marker_str = f"{self.marker}_{uuid.uuid4().hex[:8]}"

            # The payload. We echo the exit code and the current working directory.
            cmd_payload = f'{command}\n__aizen_exit=$?; echo "{marker_str}:$__aizen_exit:$(pwd)"\n'

            try:
                self.proc.stdin.write(cmd_payload)
                self.proc.stdin.flush()
            except BrokenPipeError:
                self._start()
                self.proc.stdin.write(cmd_payload)
                self.proc.stdin.flush()

            start_time = time.time()
            exit_code = 0
            timeout_occurred = False
            new_pwd = ""

            with Live(
                Text("  ▶ Running...", style="dim italic"),
                console=console,
                refresh_per_second=4,
                transient=True,
            ) as live:
                while True:
                    elapsed = time.time() - start_time
                    if elapsed > timeout:
                        timeout_occurred = True
                        break

                    if self.proc.poll() is not None:
                        break

                    out_str = "".join(self.stdout_buf)
                    if marker_str in out_str:
                        # Give a tiny bit of time for stderr reader to catch up
                        time.sleep(0.05)
                        break

                    tail = "".join(self.stdout_buf[-15:])
                    display = Text()
                    display.append(f"  ▶ Running ({elapsed:.0f}s)\n", style="dim italic")
                    display.append(tail.rstrip(), style="dim")
                    live.update(display)

                    time.sleep(0.1)

            out_str = "".join(self.stdout_buf)
            err_str = "".join(self.stderr_buf)

            final_out = []
            for line in out_str.splitlines():
                if line.startswith(marker_str):
                    parts = line.split(":", 2)
                    if len(parts) >= 2:
                        try:
                            exit_code = int(parts[1])
                        except ValueError:
                            pass
                    if len(parts) >= 3:
                        new_pwd = parts[2].strip()
                    break
                final_out.append(line)

            output = "\n".join(final_out)
            stderr_output = err_str.strip()

            if timeout_occurred:
                self.proc.kill()
                self.proc = None

            return output, stderr_output, exit_code, timeout_occurred, new_pwd


# Global persistent terminal instance
_terminal = PersistentTerminal()


def is_command_safe(command: str) -> bool:
    """Check if a command is safe to auto-execute without confirmation."""
    cmd_stripped = command.strip()

    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, cmd_stripped):
            return False

    for safe in SAFE_COMMAND_PREFIXES:
        if cmd_stripped == safe or cmd_stripped.startswith(safe + " "):
            return True

    return False


def run_command_impl(
    command: str, auto_approve: bool = False, timeout: int = 120, background: bool = False
) -> str:
    """Execute a shell command with safety checks. Uses PersistentTerminal unless background=True."""
    logger.debug("run_command: %s (timeout=%ds, background=%s)", command, timeout, background)

    safe = is_command_safe(command)
    if not safe:
        console.print(
            Panel(
                f"[bold {Theme.ACCENT}]◆ AIZEN[/bold {Theme.ACCENT}] [{Theme.TEXT}]wants to run:[/{Theme.TEXT}]\n\n[bold {Theme.TEXT}]{command}[/bold {Theme.TEXT}]",
                border_style=Theme.BORDER,
            )
        )
        with terminal_lock:
            if not _ask_permission("  ▸ Allow?", auto_approve):
                return "User denied command execution."
    elif safe:
        console.print(f"  [dim]▶ {command}{' (background)' if background else ''}[/dim]")

    try:
        if background:
            # For background tasks, use isolated Popen so we don't block the persistent terminal
            proc = subprocess.Popen(
                command,
                shell=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            task_id = f"bg_{uuid.uuid4().hex[:8]}"
            task_info = {
                "process": proc,
                "stdout": [],
                "stderr": [],
                "command": command,
                "start_time": time.time(),
            }
            with background_tasks_lock:
                background_tasks[task_id] = task_info

            def stream_reader(pipe, dest_list):
                for line in iter(pipe.readline, ""):
                    dest_list.append(line)
                pipe.close()

            threading.Thread(
                target=stream_reader, args=(proc.stdout, task_info["stdout"]), daemon=True
            ).start()
            threading.Thread(
                target=stream_reader, args=(proc.stderr, task_info["stderr"]), daemon=True
            ).start()

            return f"Task started in background with ID: {task_id}"

        # Foreground interactive commands use the stateful terminal
        output, stderr_output, exit_code, timeout_occurred, new_pwd = _terminal.run(
            command, timeout
        )

        # Sync python's working directory with bash's working directory
        if new_pwd and os.path.exists(new_pwd) and new_pwd != os.getcwd():
            try:
                os.chdir(new_pwd)
                logger.info("Synced python cwd with bash: %s", new_pwd)
            except Exception as e:
                logger.error("Failed to sync python cwd with bash: %s", e)

        if timeout_occurred:
            logger.warning("Command timed out after %ds: %s", timeout, command)
            return f"Error: Command timed out after {timeout} seconds.\nPartial Output:\n{output}"

        if stderr_output:
            if output:
                output += f"\nSTDERR:\n{stderr_output}"
            else:
                output = stderr_output
        if exit_code != 0:
            output += f"\n[Exit code: {exit_code}]"

        return output.strip() if output.strip() else f"Command completed (exit code {exit_code})"

    except Exception as e:
        logger.exception("Error executing command: %s", command)
        return f"Error executing command: {e}"


def check_background_task_impl(task_id: str) -> str:
    """Checks the status of a background task and returns its recent output."""
    with background_tasks_lock:
        if task_id not in background_tasks:
            return f"Error: No such background task '{task_id}'."
        task = background_tasks[task_id]

    proc = task["process"]

    out_lines = list(task["stdout"])
    err_lines = list(task["stderr"])

    stdout_str = "".join(out_lines[-100:]).strip()
    stderr_str = "".join(err_lines[-100:]).strip()

    status = "RUNNING" if proc.poll() is None else f"FINISHED (Exit code {proc.returncode})"

    result = f"Task: {task_id}\nCommand: {task['command']}\nStatus: {status}\n\n"
    if stdout_str:
        result += f"--- STDOUT (last 100 lines) ---\n{stdout_str}\n\n"
    if stderr_str:
        result += f"--- STDERR (last 100 lines) ---\n{stderr_str}\n"

    # Cleanup if done
    if proc.poll() is not None:
        with background_tasks_lock:
            background_tasks.pop(task_id, None)

    return result.strip()


def kill_background_task_impl(task_id: str) -> str:
    """Kills a running background task."""
    with background_tasks_lock:
        if task_id not in background_tasks:
            return f"Error: No such background task '{task_id}'."
        task = background_tasks.pop(task_id)

    proc = task["process"]

    if proc.poll() is None:
        proc.kill()
        return f"Task {task_id} killed."
    else:
        return f"Task {task_id} was already finished."
