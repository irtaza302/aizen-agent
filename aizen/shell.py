"""
Shell integration for Aizen.

Installs the `ai` command into zsh/bash, allowing one-shot generation,
pipe support, and capturing the last command's error.
"""

import os
import sys
from pathlib import Path

from .config import Theme

SHELL_SCRIPT = """
# Aizen Shell Integration

# ai command wrapper
ai() {
    # If stdin is a pipe (not a terminal)
    if [ ! -t 0 ]; then
        cat | aizen -p "$*"
    else
        # If the argument is "fix", find the last command that failed and pass it
        if [ "$1" = "fix" ]; then
            # Requires shell history integration to work perfectly,
            # but we pass "fix the last command" as a hint
            aizen -p "Fix the last shell command I ran which failed."
        else
            aizen -p "$*"
        fi
    fi
}
"""


def detect_shell() -> str:
    """Detect the current shell."""
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        return "zsh"
    elif "bash" in shell:
        return "bash"
    elif "fish" in shell:
        return "fish"
    return "unknown"


def install_shell_integration() -> None:
    """Install the ai function into the user's shell config."""
    from .config import console

    shell = detect_shell()
    home = Path.home()

    rc_file = None
    if shell == "zsh":
        rc_file = home / ".zshrc"
    elif shell == "bash":
        if sys.platform == "darwin":
            rc_file = home / ".bash_profile"
        else:
            rc_file = home / ".bashrc"

    if not rc_file or not rc_file.exists():
        console.print(
            f"[{Theme.ERROR}]Could not detect shell or config file for {shell}.[/{Theme.ERROR}]"
        )
        console.print(f"[{Theme.MUTED}]Manually add this to your shell config:[/{Theme.MUTED}]\n")
        console.print(SHELL_SCRIPT)
        return

    try:
        content = rc_file.read_text()
        if "Aizen Shell Integration" in content:
            console.print(
                f"[{Theme.SUCCESS}]Shell integration already installed in {rc_file}.[/{Theme.SUCCESS}]"
            )
            return

        with rc_file.open("a") as f:
            f.write("\n" + SHELL_SCRIPT + "\n")

        console.print(f"[{Theme.SUCCESS}]✓ Installed 'ai' command into {rc_file}[/{Theme.SUCCESS}]")
        console.print(
            f"[{Theme.ACCENT}]Restart your terminal or run:[/{Theme.ACCENT}] source {rc_file}"
        )

    except Exception as e:
        console.print(f"[{Theme.ERROR}]Error installing shell integration: {e}[/{Theme.ERROR}]")
