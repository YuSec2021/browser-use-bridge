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

from browser_use.agent import Agent
from browser_use.browser import BrowserSession
from browser_use.checkpoint import CheckpointManager
from browser_use.dom import DomService
from browser_use.history import HistoryExporter
from browser_use.memory import MemoryStore, MemoryType
from browser_use.mcp import BrowserUseServer, claude_desktop_config
from browser_use.observability import ObservabilityEvent, ObservabilityHub
from browser_use.tools import Tools
from browser_use.tui import BrowserUseTUI, DashboardState, THEME_SUMMARY, render_dashboard_text


def _load_state(path: str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main() -> None:
    """browser-use developer CLI."""


PROVIDER_DEFAULTS = {
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-20250514",
    "google": "gemini-2.0-flash",
    "kimi": "kimi-k2",
    "qwen": "qwen-max-latest",
    "glm": "glm-4-flash",
    "minimax": "MiniMax-M2",
    "ollama": "llama3",
}


def _build_llm(provider: str, model: str | None, api_key: str | None) -> Any:
    """Create an LLM adapter from provider name and optional overrides."""
    from browser_use.llm.custom import get_custom_provider_config

    custom_config = get_custom_provider_config(provider)
    if custom_config is not None:
        from browser_use.llm import ChatCustom

        return ChatCustom(
            base_url=custom_config.base_url,
            api_key=api_key if api_key is not None else custom_config.api_key,
            model=model or custom_config.model_name,
            extra_headers=custom_config.extra_headers,
            config=custom_config,
            provider_name=provider,
            capabilities=custom_config.capabilities,
        )

    model = model or PROVIDER_DEFAULTS.get(provider, "gpt-4o")

    if api_key is None:
        env_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY",
            "kimi": "MOONSHOT_API_KEY",
            "qwen": "DASHSCOPE_API_KEY",
            "glm": "ZHIPU_API_KEY",
            "minimax": "MINIMAX_API_KEY",
            "ollama": None,
        }
        env_var = env_map.get(provider)
        api_key = os.getenv(env_var) if env_var else None

    if provider == "openai":
        from browser_use.llm import ChatOpenAI

        return ChatOpenAI(model=model, api_key=api_key)
    if provider == "anthropic":
        from browser_use.llm import ChatAnthropic

        return ChatAnthropic(model=model, api_key=api_key)
    if provider == "google":
        from browser_use.llm import ChatGoogle

        return ChatGoogle(model=model, api_key=api_key)
    if provider == "kimi":
        from browser_use.llm import ChatKimi

        return ChatKimi(model=model, api_key=api_key)
    if provider == "qwen":
        from browser_use.llm import ChatQwen

        return ChatQwen(model=model, api_key=api_key)
    if provider == "glm":
        from browser_use.llm import ChatGLM

        return ChatGLM(model=model, api_key=api_key)
    if provider == "minimax":
        from browser_use.llm import ChatMiniMax

        return ChatMiniMax(model=model, api_key=api_key)
    if provider == "ollama":
        from browser_use.llm import ChatOllama

        return ChatOllama(model=model)

    raise click.UsageError(f"Unknown provider: {provider!r}. Use list-providers to see configured custom providers.")


@main.command("run")
@click.option("--task", required=True, help="Natural-language browser automation task to execute.")
@click.option("--url", default=None, help="Page URL to open before running the task.")
@click.option("--mock-llm", is_flag=True, help="Use deterministic local planning without a paid LLM provider.")
@click.option(
    "--provider",
    type=str,
    default=None,
    help="LLM provider or configured custom provider name. Required for real LLM execution (not --mock-llm).",
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
            raise click.UsageError("Specify --provider for real LLM execution. Use list-providers to see supported names.")
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


@main.command("list-providers")
@click.option("--json", "json_output", is_flag=True, help="Emit provider metadata as structured JSON.")
def list_providers(json_output: bool) -> None:
    """List supported LLM providers and local availability metadata."""

    payload = {"providers": _provider_metadata()}
    if json_output:
        _echo_json(payload)
        return

    for provider in payload["providers"]:
        status = "available" if provider["available"] else "unavailable"
        models = ", ".join(provider["models"]) if provider["models"] else provider["default_model"]
        click.echo(f"{provider['name']}\t{status}\tdefault={provider['default_model']}\tmodels={models}")


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


@main.command("replay")
@click.argument("checkpoint_id")
@click.option("--format", "replay_format", type=click.Choice(["json", "html", "gif", "all"]), default="html", show_default=True)
@click.option("--task-id", default=None, help="Task id that owns the checkpoint.")
@click.option("--output-dir", type=click.Path(file_okay=False, dir_okay=True), default="history-exports", show_default=True)
@click.option("--json", "json_output", is_flag=True, help="Emit generated artifact paths as JSON.")
def replay(checkpoint_id: str, replay_format: str, task_id: str | None, output_dir: str, json_output: bool) -> None:
    """Export saved checkpoint history as replay artifacts."""

    exporter = HistoryExporter(checkpoint_manager=_checkpoint_manager(), output_dir=output_dir)
    paths = exporter.export(checkpoint_id, task_id=task_id, format=replay_format)
    payload = {
        "checkpoint_id": checkpoint_id,
        "task_id": task_id,
        "format": replay_format,
        "paths": {name: str(path) for name, path in paths.items()},
    }
    if json_output:
        _echo_json(payload)
        return
    for name, path in payload["paths"].items():
        click.echo(f"{name}\t{path}")


@main.group("memory")
def memory_command() -> None:
    """Manage long-term memory entries."""


@memory_command.command("add")
@click.argument("text")
@click.option(
    "--type",
    "memory_type",
    type=click.Choice([memory_type.value for memory_type in MemoryType]),
    default=MemoryType.SEMANTIC.value,
    show_default=True,
)
@click.option("--metadata", multiple=True, help="Metadata as key=value. May be repeated.")
@click.option("--json", "json_output", is_flag=True, help="Emit the saved entry as JSON.")
def memory_add(text: str, memory_type: str, metadata: tuple[str, ...], json_output: bool) -> None:
    """Add a memory entry."""

    entry = _memory_store().add(text, type=memory_type, metadata=_parse_metadata(metadata))
    payload = entry.model_dump(mode="json")
    if json_output:
        _echo_json(payload)
        return
    click.echo(f"{entry.entry_id}\t{entry.type.value}\t{entry.text}")


@memory_command.command("search")
@click.argument("query")
@click.option("--top-k", default=5, show_default=True, type=click.IntRange(min=1), help="Maximum memories to return.")
@click.option(
    "--type",
    "memory_type",
    type=click.Choice([memory_type.value for memory_type in MemoryType]),
    default=None,
    help="Only search memories of this type.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit search results as JSON.")
def memory_search(query: str, top_k: int, memory_type: str | None, json_output: bool) -> None:
    """Search memory entries."""

    entries = _memory_store().search(query, top_k=top_k, type=memory_type)
    payload = {"memories": [entry.model_dump(mode="json") for entry in entries]}
    if json_output:
        _echo_json(payload)
        return
    for entry in entries:
        score = "" if entry.score is None else f"\tscore={entry.score:.4f}"
        click.echo(f"{entry.entry_id}\t{entry.type.value}{score}\t{entry.text}")


@memory_command.command("clear")
@click.option("--json", "json_output", is_flag=True, help="Emit clear result as JSON.")
def memory_clear(json_output: bool) -> None:
    """Clear all memory entries."""

    _memory_store().clear()
    payload = {"cleared": True}
    if json_output:
        _echo_json(payload)
        return
    click.echo("cleared=True")


@memory_command.command("stats")
@click.option("--json", "json_output", is_flag=True, help="Emit memory statistics as JSON.")
def memory_stats(json_output: bool) -> None:
    """Show memory statistics."""

    payload = _memory_store().stats()
    if json_output:
        _echo_json(payload)
        return
    click.echo(f"backend={payload['backend']} count={payload['count']}")


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
            "final_result": _final_result_from_history(last),
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


def _final_result_from_history(history: Any) -> str:
    if history is None or history.model_output is None:
        return ""
    for action in history.model_output.actions:
        if not isinstance(action, dict) or "done" not in action:
            continue
        done_payload = action["done"]
        if isinstance(done_payload, dict):
            text = done_payload.get("text")
            if text:
                return str(text)
        if isinstance(done_payload, str):
            return done_payload
    return history.model_output.next_goal


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


def _provider_metadata() -> list[dict[str, Any]]:
    from browser_use.llm.custom import (
        ProviderCapabilities,
        detect_provider_from_base_url,
        load_custom_provider_configs,
    )

    providers: list[dict[str, Any]] = []
    env_map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "kimi": "MOONSHOT_API_KEY",
        "qwen": "DASHSCOPE_API_KEY",
        "glm": "ZHIPU_API_KEY",
        "minimax": "MINIMAX_API_KEY",
    }
    for name, default_model in PROVIDER_DEFAULTS.items():
        if name == "ollama":
            providers.append(_ollama_provider_metadata(default_model))
            continue
        env_var = env_map.get(name)
        providers.append(
            {
                "name": name,
                "provider_type": name,
                "available": bool(os.getenv(env_var or "")),
                "requires_api_key": True,
                "default_model": default_model,
                "models": [default_model],
                "capabilities": _capabilities_payload(_builtin_provider_capabilities(name)),
            }
        )
    providers.append(
        {
            "name": "custom",
            "provider_type": "custom",
            "available": True,
            "requires_api_key": True,
            "default_model": "configured-model",
            "models": [],
            "capabilities": _capabilities_payload(ProviderCapabilities()),
        }
    )
    for custom_provider in load_custom_provider_configs():
        capabilities = custom_provider.capabilities
        providers.append(
            {
                "name": custom_provider.name or detect_provider_from_base_url(custom_provider.base_url),
                "provider_type": detect_provider_from_base_url(custom_provider.base_url),
                "available": True,
                "requires_api_key": True,
                "default_model": custom_provider.model_name,
                "models": [custom_provider.model_name],
                "base_url": custom_provider.base_url,
                "capabilities": _capabilities_payload(capabilities),
            }
        )
    return providers


def _ollama_provider_metadata(default_model: str) -> dict[str, Any]:
    from browser_use.llm.ollama import DEFAULT_OLLAMA_BASE_URL, OllamaHealthChecker

    base_url = os.getenv("BROWSER_USE_OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)

    async def check() -> Any:
        return await OllamaHealthChecker(base_url=base_url, timeout=2).check()

    try:
        status = asyncio.run(check())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            status = loop.run_until_complete(check())
        finally:
            loop.close()

    models = status.available_models if status.connected else []
    return {
        "name": "ollama",
        "provider_type": "ollama",
        "available": status.connected,
        "requires_api_key": False,
        "default_model": models[0] if models else default_model,
        "models": models,
        "base_url": status.base_url,
        "error": status.error,
        "capabilities": _capabilities_payload(_builtin_provider_capabilities("ollama")),
    }


def _builtin_provider_capabilities(provider: str) -> Any:
    from browser_use.llm.custom import ProviderCapabilities

    if provider in {"openai", "qwen", "glm", "kimi", "minimax"}:
        return ProviderCapabilities(
            structured_output="json_schema",
            vision=provider in {"openai", "qwen"},
            thinking=provider in {"qwen", "minimax"},
        )
    if provider == "ollama":
        return ProviderCapabilities(structured_output="prompt_injection", vision=True, thinking=False)
    return ProviderCapabilities(structured_output="json_object", vision=False, thinking=False)


def _capabilities_payload(capabilities: Any) -> dict[str, Any]:
    if hasattr(capabilities, "model_dump"):
        return capabilities.model_dump()
    return dict(capabilities)


def _echo_json(payload: dict[str, Any]) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False))


def _checkpoint_manager() -> CheckpointManager:
    return CheckpointManager(storage_dir=os.getenv("BROWSER_USE_CHECKPOINT_DIR"))


def _memory_store() -> MemoryStore:
    return MemoryStore(storage_path=os.getenv("BROWSER_USE_MEMORY_PATH"))


def _parse_metadata(values: tuple[str, ...]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise click.BadParameter("metadata values must use key=value format", param_hint="--metadata")
        key, item = value.split("=", 1)
        metadata[key] = item
    return metadata


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
