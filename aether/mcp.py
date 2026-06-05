import asyncio
import threading
import contextlib
import os
from typing import Dict, Any, List, Optional
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import console
from .logging_config import logger

class MCPManager:
    def __init__(self, mcp_servers_config: dict):
        self.config = mcp_servers_config
        self.sessions: Dict[str, ClientSession] = {}
        self.exit_stack: Optional[contextlib.AsyncExitStack] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready_event = threading.Event()
        self.tools_cache: List[dict] = []

    async def start(self):
        if not self.config:
            return
            
        logger.info("Starting MCP servers: %s", list(self.config.keys()))
        
        try:
            await self._init_all_servers()
        except Exception as e:
            console.print(f"[dim yellow]⚠️  Error initializing MCP servers: {e}[/dim yellow]")

    async def _init_all_servers(self):
        self.exit_stack = contextlib.AsyncExitStack()
        
        for name, server_config in self.config.items():
            command = server_config.get("command")
            args = server_config.get("args", [])
            env = server_config.get("env")
            
            if not command:
                continue
                
            try:
                # Inherit environment, merging any custom env
                merged_env = os.environ.copy()
                if env:
                    merged_env.update(env)

                server_params = StdioServerParameters(command=command, args=args, env=merged_env)
                stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
                read, write = stdio_transport
                
                session = await self.exit_stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                
                self.sessions[name] = session
                
                # Fetch tools and format them for OpenAI
                response = await session.list_tools()
                for tool in response.tools:
                    self.tools_cache.append({
                        "type": "function",
                        "function": {
                            # Prefix with mcp_serverName_ to avoid collisions
                            "name": f"mcp_{name}_{tool.name}",
                            "description": tool.description or f"MCP tool {tool.name} from {name}",
                            "parameters": tool.inputSchema,
                        }
                    })
                logger.info("MCP server '%s' connected with %d tools", name, len(response.tools))
                console.print(f"  [dim green]✓ Connected to MCP server: {name}[/dim green]")
            except Exception as e:
                logger.exception("Failed to connect to MCP server '%s'", name)
                console.print(f"  [dim yellow]⚠️  Failed to connect to MCP server {name}: {e}[/dim yellow]")

    async def stop(self):
        if self.exit_stack:
            await self.exit_stack.aclose()
        self.sessions.clear()

    def get_tools(self) -> List[dict]:
        return self.tools_cache

    async def call_tool(self, full_tool_name: str, arguments: dict) -> str:
        logger.debug("MCP call_tool: %s args=%s", full_tool_name, list(arguments.keys()))
        for server_name in self.sessions:
            prefix = f"mcp_{server_name}_"
            if full_tool_name.startswith(prefix):
                tool_name = full_tool_name[len(prefix):]
                session = self.sessions[server_name]
                try:
                    result = await session.call_tool(tool_name, arguments=arguments)
                    
                    if not result.content:
                        return "Tool executed successfully but returned no content."
                    
                    output = []
                    for item in result.content:
                        if item.type == "text":
                            output.append(item.text)
                        else:
                            output.append(f"[Non-text content: {item.type}]")
                    
                    return "\n".join(output)
                except Exception as e:
                    return f"Error executing MCP tool {tool_name} on {server_name}: {e}"
                    
        return f"Error: MCP server for tool '{full_tool_name}' not found."
