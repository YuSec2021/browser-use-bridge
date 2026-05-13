from __future__ import annotations

import asyncio
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from browser_use.browser import BrowserSession
from browser_use.dom import DomService
from browser_use.tools import Tools


CURRENT_STATE_URI = "browser-use://state/current"


class BrowserUseServer:
    """Line-delimited stdio MCP server for browser-use automation tools."""

    def __init__(self) -> None:
        self.tools = Tools()
        self.browser_session: BrowserSession | None = None
        self._browser_unavailable = False
        self._static_state: dict[str, Any] = {"url": "", "title": "", "content": "", "elements": []}

    async def run_stdio(self) -> None:
        """Read JSON-RPC messages from stdin and write JSON-RPC responses to stdout."""
        try:
            while True:
                line = await asyncio.to_thread(sys.stdin.readline)
                if line == "":
                    break
                response = await self.handle_line(line)
                if response is not None:
                    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                    sys.stdout.flush()
        finally:
            await self.close()

    async def handle_line(self, line: str) -> dict[str, Any] | None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            return self._error(None, -32700, f"Parse error: {exc}")
        return await self.handle_message(message)

    async def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}
        is_notification = "id" not in message

        if method == "notifications/initialized":
            return None

        try:
            if method == "initialize":
                result = self._initialize_result(params)
            elif method == "tools/list":
                result = {"tools": self._mcp_tools()}
            elif method == "tools/call":
                result = await self._call_tool(params)
            elif method == "resources/list":
                result = {"resources": self._resources()}
            elif method == "resources/read":
                result = await self._read_resource(params)
            else:
                if is_notification:
                    return None
                return self._error(request_id, -32601, f"Method not found: {method}")
        except Exception as exc:
            if is_notification:
                return None
            return self._error(request_id, -32000, str(exc))

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    async def close(self) -> None:
        if self.browser_session is not None:
            await self.browser_session.close()
            self.browser_session = None

    def _initialize_result(self, params: dict[str, Any]) -> dict[str, Any]:
        protocol_version = params.get("protocolVersion") or "2024-11-05"
        return {
            "protocolVersion": protocol_version,
            "serverInfo": {"name": "browser-use", "version": "0.8.0"},
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
            },
        }

    def _mcp_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for action in self.tools.list_actions():
            schema = action.get("schema", {})
            if schema.get("type") != "object":
                schema = {"type": "object", **schema}
            tools.append(
                {
                    "name": action["name"],
                    "description": action["description"],
                    "inputSchema": schema,
                }
            )
        return tools

    async def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("tools/call requires params.name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError("tools/call params.arguments must be an object")

        if self._can_use_static_tool(name, arguments):
            result = await self._call_static_tool(name, arguments)
        else:
            try:
                session = await self._ensure_browser_session()
                result = await self.tools.execute_action({name: arguments}, browser_session=session)
                await self._refresh_state_from_browser()
            except Exception:
                if not self._can_use_static_tool(name, arguments):
                    raise
                self._browser_unavailable = True
                result = await self._call_static_tool(name, arguments)

        return {"content": [{"type": "text", "text": self._content_text(result)}]}

    async def _read_resource(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = params.get("uri")
        if uri != CURRENT_STATE_URI:
            raise ValueError(f"Unknown resource URI: {uri}")
        state = await self._current_state()
        return {
            "contents": [
                {
                    "uri": CURRENT_STATE_URI,
                    "mimeType": "application/json",
                    "text": json.dumps(state, ensure_ascii=False),
                }
            ]
        }

    def _resources(self) -> list[dict[str, Any]]:
        return [
            {
                "uri": CURRENT_STATE_URI,
                "name": "Current browser state",
                "description": "Active page URL, title, readable content, and interactive elements.",
                "mimeType": "application/json",
            }
        ]

    async def _ensure_browser_session(self) -> BrowserSession:
        if self._browser_unavailable:
            raise RuntimeError("Browser session is unavailable")
        if self.browser_session is None:
            self.browser_session = BrowserSession()
            try:
                await self.browser_session.start()
            except Exception:
                self._browser_unavailable = True
                self.browser_session = None
                raise
        return self.browser_session

    async def _refresh_state_from_browser(self) -> dict[str, Any]:
        if self.browser_session is None:
            return self._static_state
        state = await DomService(self.browser_session).get_state()
        content = ""
        try:
            page = self.browser_session.session_manager.get_active_tab().page
            content = await page.evaluate(
                "() => [document.title, document.body?.innerText || ''].filter(Boolean).join('\\n')"
            )
        except Exception:
            content = state.title
        self._static_state = {
            "url": state.url,
            "title": state.title,
            "content": content,
            "elements": [element.model_dump() for element in state.elements],
        }
        return self._static_state

    async def _current_state(self) -> dict[str, Any]:
        if self.browser_session is not None and not self.browser_session.is_closed:
            try:
                return await self._refresh_state_from_browser()
            except Exception:
                return self._static_state
        return self._static_state

    def _can_use_static_tool(self, name: str, arguments: dict[str, Any]) -> bool:
        if not self._browser_unavailable:
            return False
        return name in {"navigate", "extract_content", "done"} or (
            name in {"click", "input_text", "scroll", "go_back"} and bool(self._static_state.get("url"))
        )

    async def _call_static_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "navigate":
            url = str(arguments.get("url") or "")
            self._static_state = _inspect_static_url(url)
            return {"ok": True, "url": self._static_state["url"]}
        if name == "extract_content":
            return {
                "ok": True,
                "url": self._static_state["url"],
                "title": self._static_state["title"],
                "content": self._static_state["content"],
                "elements": self._static_state["elements"],
            }
        if name == "done":
            return {"ok": True, "done": True, "success": arguments.get("success", True), "text": arguments.get("text", "")}
        if name == "click":
            return {"ok": True, "index": arguments.get("index")}
        if name == "input_text":
            return {"ok": True, "index": arguments.get("index"), "text": arguments.get("text", "")}
        if name == "scroll":
            return {"ok": True, "amount": arguments.get("amount", 0)}
        if name == "go_back":
            return {"ok": True, "url": self._static_state["url"]}
        raise KeyError(f"Unknown action: {name}")

    def _content_text(self, result: Any) -> str:
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)

    def _error(self, request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def claude_desktop_config() -> dict[str, Any]:
    """Return a Claude Desktop mcpServers snippet for this CLI."""
    return {
        "mcpServers": {
            "browser-use": {
                "command": "browser-use",
                "args": ["mcp", "--stdio"],
            }
        }
    }


def _inspect_static_url(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme != "file":
        raise RuntimeError(f"Browser unavailable and static inspection only supports file:// URLs: {url}")

    path = Path(unquote(parsed.path))
    parser = _StaticPageParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return {
        "url": url,
        "title": parser.title.strip(),
        "content": " ".join(f"{parser.title} {parser.body_text}".split()),
        "elements": parser.elements,
    }


class _StaticPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.body_text = ""
        self.elements: list[dict[str, Any]] = []
        self._in_title = False
        self._in_body = False
        self._interactive_stack: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        if tag == "title":
            self._in_title = True
            return
        if tag == "body":
            self._in_body = True
        if tag == "input":
            self._add_element(tag, self._label_from_attributes(attributes), attributes)
            return
        if self._is_interactive(tag, attributes):
            self._interactive_stack.append({"tag": tag, "attributes": attributes, "text_parts": []})

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            return
        if tag == "body":
            self._in_body = False
        if not self._interactive_stack:
            return
        current = self._interactive_stack[-1]
        if current["tag"] == tag:
            current = self._interactive_stack.pop()
            text = " ".join("".join(current["text_parts"]).split())
            self._add_element(tag, text or self._label_from_attributes(current["attributes"]), current["attributes"])

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._in_body:
            self.body_text += f" {data}"
        if self._interactive_stack:
            self._interactive_stack[-1]["text_parts"].append(data)

    def _add_element(self, tag: str, text: str, attributes: dict[str, str]) -> None:
        self.elements.append(
            {
                "index": len(self.elements),
                "tag_name": tag,
                "text": text,
                "is_interactive": True,
                "attributes": attributes,
                "x": 0,
                "y": 0,
                "width": 0,
                "height": 0,
            }
        )

    def _is_interactive(self, tag: str, attributes: dict[str, str]) -> bool:
        return tag in {"button", "a", "textarea", "select", "summary"} or "onclick" in attributes or attributes.get("role") in {
            "button",
            "link",
            "textbox",
            "checkbox",
            "radio",
            "switch",
            "combobox",
        }

    def _label_from_attributes(self, attributes: dict[str, str]) -> str:
        for name in ["aria-label", "placeholder", "title", "value", "name", "id"]:
            value = attributes.get(name, "").strip()
            if value:
                return value
        return ""


__all__ = ["BrowserUseServer", "CURRENT_STATE_URI", "claude_desktop_config"]
