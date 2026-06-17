# Backward compatibility — import everything from the new cmd package
from .cmd import SLASH_COMMANDS, AizenCompleter, handle_slash_command

__all__ = ["handle_slash_command", "AizenCompleter", "SLASH_COMMANDS"]
