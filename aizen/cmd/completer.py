import os
from prompt_toolkit.completion import Completer, Completion
from ..config import get_cached_models
from ..logging_config import logger
from ..utils import load_gitignore_patterns, should_ignore
from .registry import get_slash_commands_list

class AizenCompleter(Completer):
    """Autocomplete for both slash commands (/) and file mentions (@)."""

    def __init__(self):
        super().__init__()
        self.ignore_patterns = load_gitignore_patterns()

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        stripped = text.lstrip()

        # ── Slash command completion ──
        # Only complete if '/' is the very first character typed (start of input)
        if stripped.startswith("/"):
            if " " not in stripped:
                query = stripped.lower()
                cmds_with_args = {
                    "/model",
                    "/save",
                    "/load",
                    "/export",
                    "/checkpoint",
                    "/restore",
                    "/search",
                    "/reindex",
                    "/remember",
                    "/forget",
                    "/pr",
                    "/branch",
                    "/budget",
                }
                for cmd, description in get_slash_commands_list():
                    if cmd.startswith(query):
                        completion_text = cmd + " " if cmd in cmds_with_args else cmd
                        yield Completion(
                            completion_text,
                            start_position=-len(stripped),
                            display=cmd,
                            display_meta=description,
                        )
            elif stripped.startswith("/model "):
                query = stripped[7:].lower()
                models = get_cached_models()
                for m in models:
                    if (
                        m["id"].lower().startswith(query)
                        or query in m["id"].lower()
                        or query in m["name"].lower()
                    ):
                        yield Completion(
                            m["id"],
                            start_position=-len(query),
                            display=m["id"],
                            display_meta=m["name"],
                        )
            return

        # ── File mention completion (@) ──
        words = text.split()
        if not words:
            return

        current = words[-1]
        if not current.startswith("@"):
            return

        query = current[1:]

        # Support directory traversal
        if "/" in query:
            dir_part = os.path.dirname(query)
            base_part = os.path.basename(query)
            search_dir = dir_part if dir_part else "."
            if os.path.isdir(search_dir):
                try:
                    for item in sorted(os.listdir(search_dir)):
                        item_path = os.path.join(search_dir, item)
                        if item.lower().startswith(base_part.lower()):
                            if not should_ignore(item_path, self.ignore_patterns):
                                display = os.path.join(dir_part, item)
                                if os.path.isdir(item_path):
                                    display += "/"
                                yield Completion(display, start_position=-len(query))
                except Exception as e:
                    logger.debug("Failed to list directory contents for autocomplete: %s", e)
        else:
            try:
                for item in sorted(os.listdir(".")):
                    if item.lower().startswith(query.lower()):
                        item_path = item
                        if not should_ignore(item_path, self.ignore_patterns):
                            if os.path.isdir(item):
                                yield Completion(item + "/", start_position=-len(query))
                            elif os.path.isfile(item):
                                yield Completion(item, start_position=-len(query))
            except Exception as e:
                logger.debug("Failed to list files for autocomplete: %s", e)
