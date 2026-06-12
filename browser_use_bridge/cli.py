from __future__ import annotations

import asyncio
import json
import os
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

from browser_use_bridge.agent import Agent
from browser_use_bridge.browser import BrowserSession
from browser_use_bridge.checkpoint import CheckpointManager
from browser_use_bridge.dom import DomService
from browser_use_bridge.mcp import BrowserUseServer, claude_desktop_config
from browser_use_bridge.observability import ObservabilityEvent, ObservabilityHub
from browser_use_bridge.tools import Tools

try:
    from browser_use_bridge.tui import BrowserUseTUI, DashboardState, THEME_SUMMARY, render_dashboard_text
except ImportError:
    BrowserUseTUI = None
    DashboardState = None
    THEME_SUMMARY = ""
    render_dashboard_text = None


def _load_state(path: str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main() -> None:
    """browser-use-bridge developer CLI."""


PROVIDER_DEFAULTS = {
    "openai": "gpt-5.5",
    "anthropic": "opus-4.8",
    "google": "gemini-3.5-flash",
    "kimi": "kimi-2.6",
    "qwen": "qwen3.7-plus",
    "glm": "glm-5.1",
    "minimax": "MiniMax-M3",
    "deepseek": "deepseek-v4-pro",
    "ollama": "llama3",
}


def _build_llm(provider: str, model: str | None, api_key: str | None) -> Any:
    """Create an LLM adapter from provider name and optional overrides."""
    model = model or PROVIDER_DEFAULTS.get(provider, "gpt-5.5")

    if api_key is None:
        env_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY",
            "kimi": "MOONSHOT_API_KEY",
            "qwen": "DASHSCOPE_API_KEY",
            "glm": "ZHIPU_API_KEY",
            "minimax": "MINIMAX_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "ollama": None,
        }
        env_var = env_map.get(provider)
        api_key = os.getenv(env_var) if env_var else None

    if provider == "openai":
        from browser_use_bridge.llm import ChatOpenAI

        return ChatOpenAI(model=model, api_key=api_key)
    if provider == "anthropic":
        from browser_use_bridge.llm import ChatAnthropic

        return ChatAnthropic(model=model, api_key=api_key)
    if provider == "google":
        from browser_use_bridge.llm import ChatGoogle

        return ChatGoogle(model=model, api_key=api_key)
    if provider == "kimi":
        from browser_use_bridge.llm import ChatKimi

        return ChatKimi(model=model, api_key=api_key)
    if provider == "qwen":
        from browser_use_bridge.llm import ChatQwen

        return ChatQwen(model=model, api_key=api_key)
    if provider == "glm":
        from browser_use_bridge.llm import ChatGLM

        return ChatGLM(model=model, api_key=api_key)
    if provider == "minimax":
        from browser_use_bridge.llm import ChatMiniMax

        return ChatMiniMax(model=model, api_key=api_key)
    if provider == "deepseek":
        from browser_use_bridge.llm import ChatDeepSeek

        return ChatDeepSeek(model=model, api_key=api_key)
    if provider == "ollama":
        raise click.UsageError(
            "Ollama is not yet implemented. "
            "Available providers: openai, anthropic, google, kimi, qwen, glm, minimax, deepseek."
        )

    raise click.UsageError(f"Unknown provider: {provider!r}")


@main.command("run")
@click.option("--task", required=True, help="Natural-language browser automation task to execute.")
@click.option("--url", default=None, help="Page URL to open before running the task.")
@click.option("--mock-llm", is_flag=True, help="Use deterministic local planning without a paid LLM provider.")
@click.option(
    "--provider",
    type=click.Choice(["openai", "anthropic", "google", "kimi", "qwen", "glm", "minimax", "deepseek"]),
    default=None,
    help="LLM provider. Required for real LLM execution (not --mock-llm).",
)
@click.option("--model", default=None, help="Model name. Provider default is used if omitted.")
@click.option("--api-key", default=None, help="API key. Falls back to environment variables.")
@click.option("--max-steps", default=20, show_default=True, type=click.IntRange(min=1), help="Maximum automation steps.")
@click.option("--json", "json_output", is_flag=True, help="Emit a structured JSON result.")
@click.option("--log-json", is_flag=True, help="Write structured JSON execution logs.")
@click.option("--log-file", type=click.Path(dir_okay=False), help="File path for structured execution logs.")
def run(
    task: str,
    url: str | None,
    mock_llm: bool,
    provider: str | None,
    model: str | None,
    api_key: str | None,
    max_steps: int,
    json_output: bool,
    log_json: bool,
    log_file: str | None,
) -> None:
    """Run a browser automation task."""

    hub = ObservabilityHub(trace_id=os.getenv("BROWSER_USE_TRACE_ID"), log_file=log_file, log_json=log_json)

    if mock_llm:
        result = asyncio.run(_run_mock_task(task=task, url=url, max_steps=max_steps, observability=hub))
    else:
        if provider is None and model is not None:
            provider = "openai"
        if provider is None:
            raise click.UsageError("Specify --provider (openai, anthropic, google, kimi, qwen, glm, minimax, deepseek, ollama) for real LLM execution.")
        llm = _build_llm(provider, model, api_key)
        result = asyncio.run(_run_agent_task(task=task, url=url, llm=llm, max_steps=max_steps, observability=hub))

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


@main.group("checkpoint")
def checkpoint_command() -> None:
    """Manage saved task checkpoints."""


@checkpoint_command.command("list")
@click.option("--task-id", default=None, help="Only list checkpoints for this task id.")
@click.option("--json", "json_output", is_flag=True, help="Emit checkpoint metadata as JSON.")
def checkpoint_list(task_id: str | None, json_output: bool) -> None:
    """List saved checkpoints."""

    checkpoints = [_checkpoint_payload(checkpoint) for checkpoint in _checkpoint_manager().list_checkpoints(task_id=task_id)]
    if json_output:
        _echo_json({"checkpoints": checkpoints})
        return

    for checkpoint in checkpoints:
        click.echo(
            f"{checkpoint['checkpoint_id']}\t{checkpoint['task_id']}\t"
            f"step={checkpoint['step_counter']}\t{checkpoint['label']}"
        )


@checkpoint_command.command("delete")
@click.argument("checkpoint_id")
@click.option("--task-id", default=None, help="Task id that owns the checkpoint.")
@click.option("--json", "json_output", is_flag=True, help="Emit deletion result as JSON.")
def checkpoint_delete(checkpoint_id: str, task_id: str | None, json_output: bool) -> None:
    """Delete a saved checkpoint."""

    deleted = _checkpoint_manager().delete(checkpoint_id, task_id=task_id)
    payload = {"deleted": deleted, "checkpoint_id": checkpoint_id}
    if json_output:
        _echo_json(payload)
        return
    click.echo(f"deleted={deleted} checkpoint_id={checkpoint_id}")


@main.command("resume")
@click.argument("checkpoint_id")
@click.option("--task-id", default=None, help="Task id that owns the checkpoint.")
@click.option("--dry-run", is_flag=True, help="Load the checkpoint and report resume readiness without running.")
@click.option("--json", "json_output", is_flag=True, help="Emit resume status as JSON.")
def resume(checkpoint_id: str, task_id: str | None, dry_run: bool, json_output: bool) -> None:
    """Resume a task from a saved checkpoint."""

    checkpoint = _checkpoint_manager().load(checkpoint_id, task_id=task_id)
    payload = {
        "status": "ready_to_resume" if dry_run else "loaded",
        "checkpoint_id": checkpoint.checkpoint_id,
        "task_id": checkpoint.task_id,
        "step_counter": checkpoint.step_counter,
        "current_url": checkpoint.current_url,
        "pending_actions": checkpoint.pending_actions_queue,
        "label": checkpoint.label,
    }
    if json_output:
        _echo_json(payload)
        return
    click.echo(f"{payload['status']}: {checkpoint.checkpoint_id} step={checkpoint.step_counter}")


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


async def _run_agent_task(
    task: str,
    url: str | None,
    llm: Any,
    max_steps: int,
    observability: ObservabilityHub | None = None,
) -> dict[str, Any]:
    hub = observability or ObservabilityHub()
    hub.emit(ObservabilityEvent(name="run_started", payload={"task": task, "url": url, "max_steps": max_steps}))

    session = BrowserSession()
    tools = Tools()
    try:
        await session.start()
        if url:
            await session.navigate(url)

        agent = Agent(task=task, llm=llm, browser_session=session, tools=tools, max_steps=max_steps)
        history_list = await agent.run()

        last = history_list.histories[-1] if history_list.histories else None
        result = {
            "task": task,
            "status": "done",
            "steps": len(history_list.histories),
            "final_result": last.model_output.next_goal if last else "",
            "url": last.state.url if last else "",
            "title": last.state.title if last else "",
            "trace_id": hub.trace_id,
        }
    finally:
        await session.close()

    hub.emit(ObservabilityEvent(name="run_finished", payload=result))
    return result


async def _run_mock_task(
    task: str,
    url: str | None,
    max_steps: int,
    observability: ObservabilityHub | None = None,
) -> dict[str, Any]:
    hub = observability or ObservabilityHub()
    hub.emit(ObservabilityEvent(name="run_started", payload={"task": task, "url": url, "max_steps": max_steps}))
    summary = await _inspect_url(url, max_elements=50) if url else {"url": "", "title": "", "elements": []}
    labels = [str(element.get("text", "")) for element in summary["elements"] if element.get("text")]
    completion_label = _best_completion_label(labels)
    final_result = (
        f"Completed after seeing {completion_label}."
        if completion_label
        else "Completed deterministic mock run."
    )
    result = {
        "task": task,
        "status": "done",
        "steps": min(max_steps, 1),
        "final_result": final_result,
        "url": summary["url"],
        "title": summary["title"],
        "trace_id": hub.trace_id,
    }
    hub.emit(ObservabilityEvent(name="run_finished", payload=result))
    return result


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


def _checkpoint_manager() -> CheckpointManager:
    return CheckpointManager(storage_dir=os.getenv("BROWSER_USE_CHECKPOINT_DIR"))


def _checkpoint_payload(checkpoint: Any) -> dict[str, Any]:
    return checkpoint.model_dump(mode="json")


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


@main.group("scheduler")
def scheduler_command() -> None:
    """Runtime scheduler management commands."""
    pass


@scheduler_command.command("status")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable status JSON.")
def scheduler_status(json_output: bool) -> None:
    """Show current scheduler status: active tasks, queue depth, pool utilization."""
    try:
        from browser_use_bridge.runtime.scheduler import RuntimeScheduler, SchedulerStatus
        pool = getattr(RuntimeScheduler, "_pool", None)
        if pool is None:
            click.echo("Scheduler not initialized (no pool).")
            return
        # Try to get state from a live scheduler instance
        sched_state = {
            "status": "no-instance",
            "active_tasks": 0,
            "queued_tasks": 0,
            "pool_size": getattr(pool, "size", 0),
            "message": "Use RuntimeScheduler directly for live status",
        }
        if json_output:
            click.echo(json.dumps(sched_state, indent=2))
        else:
            click.echo(f"Scheduler status: {sched_state['status']}")
            click.echo(f"Pool size: {sched_state['pool_size']}")
            click.echo(sched_state["message"])
    except Exception as e:
        if json_output:
            click.echo(json.dumps({"error": str(e)}))
        else:
            click.echo(f"Error: {e}")


@scheduler_command.command("queue")
@click.option("--json", "json_output", is_flag=True, help="Emit pending tasks as JSON.")
def scheduler_queue(json_output: bool) -> None:
    """Show pending tasks in the scheduler queue."""
    try:
        if json_output:
            click.echo(json.dumps({"pending": [], "message": "Queue view requires live RuntimeScheduler instance"}))
        else:
            click.echo("Queue view: instantiate RuntimeScheduler to inspect queue state")
    except Exception as e:
        if json_output:
            click.echo(json.dumps({"error": str(e)}))
        else:
            click.echo(f"Error: {e}")
