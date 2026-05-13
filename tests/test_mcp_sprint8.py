from __future__ import annotations

import asyncio
import json
from pathlib import Path

from click.testing import CliRunner

from browser_use.cli import main
from browser_use.mcp import CURRENT_STATE_URI, BrowserUseServer


def test_mcp_command_is_visible_and_documents_stdio_json() -> None:
    runner = CliRunner()

    root_result = runner.invoke(main, ["--help"])
    mcp_result = runner.invoke(main, ["mcp", "--help"])

    assert root_result.exit_code == 0
    assert "mcp" in root_result.output
    assert mcp_result.exit_code == 0
    assert "--stdio" in mcp_result.output
    assert "--json" in mcp_result.output
    assert "Start an MCP server" in mcp_result.output


def test_mcp_claude_config_json_points_to_stdio_command() -> None:
    result = CliRunner().invoke(main, ["mcp", "--claude-config", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    server = payload["mcpServers"]["browser-use"]
    assert server["command"] == "browser-use"
    assert "mcp" in server["args"]
    assert "--stdio" in server["args"]


def test_mcp_initialize_and_tools_list_contract() -> None:
    async def run() -> tuple[dict[str, object], dict[str, object]]:
        server = BrowserUseServer()
        initialize = await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
            }
        )
        tools = await server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        return initialize or {}, tools or {}

    initialize, tools = asyncio.run(run())

    assert initialize["jsonrpc"] == "2.0"
    assert initialize["id"] == 1
    assert "tools" in initialize["result"]["capabilities"]
    assert "browser-use" in initialize["result"]["serverInfo"]["name"]
    names = {tool["name"] for tool in tools["result"]["tools"]}
    assert {"navigate", "click", "extract_content", "done"} <= names
    assert all(tool["description"] and isinstance(tool["inputSchema"], dict) for tool in tools["result"]["tools"])


def test_mcp_static_tool_call_and_resource_read(tmp_path: Path) -> None:
    page = tmp_path / "sprint8.html"
    page.write_text(
        "<html><head><title>Sprint 8 Resource</title></head>"
        '<body><input aria-label="Search" /><button>Continue</button></body></html>',
        encoding="utf-8",
    )

    async def run() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        server = BrowserUseServer()
        server._browser_unavailable = True
        navigate = await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "navigate", "arguments": {"url": page.as_uri()}},
            }
        )
        extract = await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "extract_content", "arguments": {}},
            }
        )
        resource = await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "resources/read",
                "params": {"uri": CURRENT_STATE_URI},
            }
        )
        return navigate or {}, extract or {}, resource or {}

    navigate, extract, resource = asyncio.run(run())

    assert "error" not in navigate
    assert navigate["result"]["content"]
    assert "Sprint 8 Resource" in extract["result"]["content"][0]["text"]
    assert "Search" in resource["result"]["contents"][0]["text"]
    assert page.as_uri() in resource["result"]["contents"][0]["text"]
