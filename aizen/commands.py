# Backward compatibility — import everything from the new cmd package
from .cmd import handle_slash_command, AizenCompleter, SLASH_COMMANDS

__all__ = ["handle_slash_command", "AizenCompleter", "SLASH_COMMANDS"]
