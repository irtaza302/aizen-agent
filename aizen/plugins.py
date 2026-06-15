import importlib.util
import os
import sys
from collections.abc import Callable

from .logging_config import logger

PLUGINS_DIR = os.path.expanduser("~/.aizen/plugins")


class PluginManager:
    """Manages loading and executing tools from user-provided Python scripts."""

    def __init__(self):
        self.plugins = {}
        self.tools = []
        self.handlers: dict[str, Callable] = {}
        self._load_plugins()

    def _load_plugins(self):
        if not os.path.exists(PLUGINS_DIR):
            try:
                os.makedirs(PLUGINS_DIR, exist_ok=True)
            except Exception as e:
                logger.debug("Failed to create plugins directory: %s", e)
            return

        for filename in os.listdir(PLUGINS_DIR):
            if filename.endswith(".py") and not filename.startswith("_"):
                name = filename[:-3]
                path = os.path.join(PLUGINS_DIR, filename)
                try:
                    spec = importlib.util.spec_from_file_location(name, path)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        # Add to sys.modules so plugins can import each other if needed
                        sys.modules[f"aizen_plugin_{name}"] = module
                        spec.loader.exec_module(module)

                        if hasattr(module, "get_tools") and hasattr(module, "execute_tool"):
                            plugin_tools = module.get_tools()
                            self.plugins[name] = module
                            self.tools.extend(plugin_tools)
                            for t in plugin_tools:
                                self.handlers[t["function"]["name"]] = module.execute_tool
                            logger.info("Loaded plugin '%s' with %d tools", name, len(plugin_tools))
                except Exception as e:
                    logger.error("Failed to load plugin '%s': %s", filename, e)

    def get_tools(self) -> list[dict]:
        return self.tools

    def execute_tool(self, tool_call, auto_approve: bool = False) -> str | None:
        """Executes a plugin tool. Returns None if tool is not handled by plugins."""
        func_name = tool_call.function.name
        if func_name in self.handlers:
            try:
                return self.handlers[func_name](tool_call, auto_approve)
            except Exception as e:
                logger.error("Plugin tool error: %s", e)
                return f"Error executing plugin tool {func_name}: {e}"
        return None


# Global instance
plugin_manager = PluginManager()
