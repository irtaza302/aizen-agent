import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from aether.mcp import MCPManager

@pytest.fixture
def mock_mcp_config():
    return {
        "test_server": {
            "command": "node",
            "args": ["server.js"]
        }
    }

@pytest.mark.asyncio
@patch("aether.mcp.ClientSession")
@patch("aether.mcp.stdio_client")
async def test_mcp_manager_start_and_tools(mock_stdio_client, mock_client_session, mock_mcp_config):
    mock_transport = (AsyncMock(), AsyncMock())
    
    mock_stdio_cm = AsyncMock()
    mock_stdio_cm.__aenter__.return_value = mock_transport
    mock_stdio_client.return_value = mock_stdio_cm
    
    mock_session = AsyncMock()
    mock_session_cm = AsyncMock()
    mock_session_cm.__aenter__.return_value = mock_session
    mock_client_session.return_value = mock_session_cm
    
    mock_tool = MagicMock()
    mock_tool.name = "do_something"
    mock_tool.description = "Does something useful"
    mock_tool.inputSchema = {"type": "object"}
    
    mock_response = MagicMock()
    mock_response.tools = [mock_tool]
    mock_session.list_tools.return_value = mock_response
    
    manager = MCPManager(mock_mcp_config)
    await manager.start()
    
    tools = manager.get_tools()
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "mcp_test_server_do_something"
    assert tools[0]["function"]["description"] == "Does something useful"
    
    await manager.stop()

@pytest.mark.asyncio
@patch("aether.mcp.ClientSession")
@patch("aether.mcp.stdio_client")
async def test_mcp_manager_call_tool(mock_stdio_client, mock_client_session, mock_mcp_config):
    mock_transport = (AsyncMock(), AsyncMock())
    mock_stdio_cm = AsyncMock()
    mock_stdio_cm.__aenter__.return_value = mock_transport
    mock_stdio_client.return_value = mock_stdio_cm
    
    mock_session = AsyncMock()
    mock_session_cm = AsyncMock()
    mock_session_cm.__aenter__.return_value = mock_session
    mock_client_session.return_value = mock_session_cm
    
    mock_response = MagicMock()
    mock_response.tools = []
    mock_session.list_tools.return_value = mock_response
    
    # Mock call_tool result
    mock_result = MagicMock()
    mock_content = MagicMock()
    mock_content.type = "text"
    mock_content.text = "Success!"
    mock_result.content = [mock_content]
    mock_session.call_tool.return_value = mock_result
    
    manager = MCPManager(mock_mcp_config)
    await manager.start()
    
    result = await manager.call_tool("mcp_test_server_some_tool", {"arg1": "val1"})
    assert result == "Success!"
    mock_session.call_tool.assert_called_once_with("some_tool", arguments={"arg1": "val1"})
    
    await manager.stop()
