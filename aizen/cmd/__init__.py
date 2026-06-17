# Import all command modules to register them
from . import git_cmds, memory_cmds, misc_cmds, model_cmds, search_cmds, session_cmds  # noqa: F401
from .completer import AizenCompleter
from .registry import get_slash_commands_list, handle_slash_command

# Re-export SLASH_COMMANDS for main.py (it's used but wait, let me use property or just export it)
SLASH_COMMANDS = get_slash_commands_list()

__all__ = ["handle_slash_command", "AizenCompleter", "SLASH_COMMANDS"]
