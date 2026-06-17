from .registry import handle_slash_command, get_slash_commands_list
from .completer import AizenCompleter

# Import all command modules to register them
from . import session_cmds
from . import git_cmds
from . import model_cmds
from . import memory_cmds
from . import search_cmds
from . import misc_cmds

# Re-export SLASH_COMMANDS for main.py (it's used but wait, let me use property or just export it)
SLASH_COMMANDS = get_slash_commands_list()

__all__ = ["handle_slash_command", "AizenCompleter", "SLASH_COMMANDS"]
