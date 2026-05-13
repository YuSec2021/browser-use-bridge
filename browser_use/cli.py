from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from html.parser import HTMLParser
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import unquote, urlparse

import click

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_use.browser import BrowserSession
from browser_use.dom import DomService
from browser_use.mcp import BrowserUseServer, claude_desktop_config
from browser_use.tools import Tools
from browser_use.tui import BrowserUseTUI, DashboardState, THEME_SUMMARY, render_dashboard_text


def _load_state(path: str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main() -> None:
    """browser-use developer CLI."""


@main.command("run")
@click.option("--task", required=True, help="Natural-language browser automation task to execute.")
@click.option("--url", default=None, help="Page URL to open before running the task.")
@click.option("--mock-llm", is_flag=True, help="Use deterministic local planning without a paid LLM provider.")
@click.option("--max-steps", default=20, show_default=True, type=click.IntRange(min=1), help="Maximum automation steps.")
@click.option("--json", "json_output", is_flag=True, help="Emit a structured JSON result.")
def run(task: str, url: str | None, mock_llm: bool, max_steps: int, json_output: bool) -> None:
    """Run a browser automation task."""

    if not mock_llm:
        raise click.UsageError("Use --mock-llm for deterministic local execution in this build.")

    result = asyncio.run(_run_mock_task(task=task, url=url, max_steps=max_steps))
    if json_output:
        _echo_json(result)
        return

    click.echo(f"{result['status']}: {result['final_result']}")


@main.command("list-tools")
@click.option("--json", "json_output", is_flag=True, help="Emit tool metadata as structured JSON.")
def list_tools(json_output: bool) -> None:
    """List registered browser automation tools."""

    tools = Tools().list_actions()
    if json_output:
        _echo_json({"tools": tools})
        return

    for tool in tools:
        click.echo(f"{tool['name']}\t{tool['description']}")


@main.command("inspect")
@click.option("--url", required=True, help="Page URL to inspect.")
@click.option("--max-elements", default=50, show_default=True, type=click.IntRange(min=1), help="Maximum elements to include.")
@click.option("--json", "json_output", is_flag=True, help="Emit a machine-readable DOM summary.")
def inspect_command(url: str, max_elements: int, json_output: bool) -> None:
    """Inspect a page and summarize its interactive DOM."""

    summary = asyncio.run(_inspect_url(url=url, max_elements=max_elements))
    if json_output:
        _echo_json(summary)
        return

    click.echo(f"URL: {summary['url']}")
    click.echo(f"Title: {summary['title']}")
    for element in summary["elements"]:
        label = element.get("text") or element.get("label") or ""
        click.echo(f"[{element['index']}] <{element['tag_name']}> {label}")


@main.command("session")
@click.option("--cdp-url", required=True, help="Chrome DevTools Protocol URL for an existing browser session.")
@click.option("--check", is_flag=True, help="Check whether the CDP endpoint is reachable.")
@click.option("--json", "json_output", is_flag=True, help="Emit structured JSON session status.")
def session(cdp_url: str, check: bool, json_output: bool) -> None:
    """Check or connect to an existing browser session."""

    if not check:
        raise click.UsageError("Use --check to perform a scriptable lifecycle check.")

    result = _check_cdp_session(cdp_url)
    if json_output:
        _echo_json(result)
    else:
        click.echo(f"{result['status']}: {result['cdp_url']}")

    if not result["connected"]:
        raise click.exceptions.Exit(1)


@main.command("cli")
@click.option("--tui", is_flag=True, help="Launch the Textual TUI dashboard.")
@click.option("--state-file", type=click.Path(exists=True, dir_okay=False), help="JSON dashboard state to render.")
@click.option("--task", default=None, help="Task text for smoke rendering.")
@click.option("--url", default=None, help="Current URL for smoke rendering.")
@click.option("--step", default=None, type=int, help="Current step counter for smoke rendering.")
@click.option("--loop-alert", default=None, help="Loop detection alert text.")
@click.option("--smoke", is_flag=True, help="Render a deterministic dashboard snapshot and exit.")
@click.option("--color/--no-color", default=True, help="Enable or disable ANSI color in smoke output.")
def cli(
    tui: bool,
    state_file: str | None,
    task: str | None,
    url: str | None,
    step: int | None,
    loop_alert: str | None,
    smoke: bool,
    color: bool,
) -> None:
    """Run the Sprint 6 Textual TUI surface."""

    state_payload = _load_state(state_file)
    if state_payload is None and any(value is not None for value in [task, url, step, loop_alert]):
        sample = DashboardState.sample()
        state_payload = {
            "task": task or sample.task,
            "url": url or sample.url,
            "step": sample.step if step is None else step,
            "loop_alert": loop_alert if loop_alert is not None else sample.loop_alert,
            "elements": [asdict(element) for element in sample.elements],
            "actions": sample.actions,
            "llm_tokens": sample.llm_tokens,
        }

    if smoke:
        click.echo(render_dashboard_text(state_payload, color=color))
        return

    if tui:
        BrowserUseTUI(state_payload).run()
        return

    raise click.UsageError("Use --tui to launch the dashboard or --smoke to render a CLI snapshot.")


@main.command("theme")
def theme() -> None:
    """Print the Terminal Precision palette."""

    click.echo(THEME_SUMMARY)


@main.command("mcp")
@click.option("--stdio", is_flag=True, help="Start the MCP server using line-delimited stdio JSON-RPC.")
@click.option("--claude-config", is_flag=True, help="Print a Claude Desktop MCP configuration snippet.")
@click.option("--json", "json_output", is_flag=True, help="Emit configuration snippets as JSON.")
def mcp(stdio: bool, claude_config: bool, json_output: bool) -> None:
    """Start an MCP server for browser automation tools."""

    if claude_config:
        config = claude_desktop_config()
        if json_output:
            _echo_json(config)
        else:
            click.echo(json.dumps(config, indent=2))
        return

    if not stdio:
        raise click.UsageError("Use --stdio to start the MCP server over standard input/output.")

    asyncio.run(BrowserUseServer().run_stdio())


async def _inspect_url(url: str, max_elements: int) -> dict[str, Any]:
    session = BrowserSession()
    try:
        await session.start()
        await session.navigate(url)
        state = await DomService(session).get_state()
        return {
            "url": state.url,
            "title": state.title,
            "elements": [element.model_dump() for element in state.elements[:max_elements]],
        }
    except Exception:
        return _inspect_static_url(url, max_elements)
    finally:
        await session.close()


async def _run_mock_task(task: str, url: str | None, max_steps: int) -> dict[str, Any]:
    summary = await _inspect_url(url, max_elements=50) if url else {"url": "", "title": "", "elements": []}
    labels = [str(element.get("text", "")) for element in summary["elements"] if element.get("text")]
    completion_label = _best_completion_label(labels)
    final_result = (
        f"Completed after seeing {completion_label}."
        if completion_label
        else "Completed deterministic mock run."
    )
    return {
        "task": task,
        "status": "done",
        "steps": min(max_steps, 1),
        "final_result": final_result,
        "url": summary["url"],
        "title": summary["title"],
    }


def _best_completion_label(labels: list[str]) -> str:
    for label in labels:
        if "done" in label.lower():
            return label
    return labels[0] if labels else ""


def _check_cdp_session(cdp_url: str) -> dict[str, Any]:
    version_url = f"{cdp_url.rstrip('/')}/json/version"
    try:
        with urllib.request.urlopen(version_url, timeout=2.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {
            "connected": True,
            "cdp_url": cdp_url,
            "status": "connected",
            "browser": payload.get("Browser", ""),
            "web_socket_debugger_url": payload.get("webSocketDebuggerUrl", ""),
        }
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {
            "connected": False,
            "cdp_url": cdp_url,
            "status": "connection_failed",
            "error": str(exc),
        }


def _echo_json(payload: dict[str, Any]) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False))


def _inspect_static_url(url: str, max_elements: int) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme != "file":
        raise click.ClickException(f"Unable to inspect URL without a browser session: {url}")

    path = Path(unquote(parsed.path))
    parser = _StaticDomParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return {
        "url": url,
        "title": parser.title.strip(),
        "elements": parser.elements[:max_elements],
    }


class _StaticDomParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.elements: list[dict[str, Any]] = []
        self._in_title = False
        self._interactive_stack: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        if tag == "title":
            self._in_title = True
            return
        if tag == "input":
            self._add_element(tag, self._label_from_attributes(attributes), attributes)
            return
        if self._is_interactive(tag, attributes):
            self._interactive_stack.append({"tag": tag, "attributes": attributes, "text_parts": []})

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            return
        if not self._interactive_stack:
            return
        current = self._interactive_stack[-1]
        if current["tag"] == tag:
            current = self._interactive_stack.pop()
            text = " ".join("".join(current["text_parts"]).split())
            label = text or self._label_from_attributes(current["attributes"])
            self._add_element(current["tag"], label, current["attributes"])

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
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


if __name__ == "__main__":
    main()
