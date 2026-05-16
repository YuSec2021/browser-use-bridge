from __future__ import annotations

import asyncio
import json

from click.testing import CliRunner

import browser_use_bridge.cli as cli_module
from browser_use_bridge.cli import main
from browser_use_bridge.observability import ObservabilityEvent, ObservabilityHub
from browser_use_bridge.tools import Tools


def test_sprint_10_actions_are_registered_with_schemas() -> None:
    tools = {tool["name"]: tool for tool in Tools().list_actions()}

    assert {"search_google", "open_tab", "switch_tab"} <= set(tools)
    assert "query" in tools["search_google"]["schema"]["required"]
    assert "url" in tools["open_tab"]["schema"]["properties"]
    assert "tab_id" in tools["switch_tab"]["schema"]["required"]


def test_search_google_action_is_deterministic() -> None:
    class FakeBrowserSession:
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def navigate(self, url: str) -> None:
            self.urls.append(url)

        async def get_current_url(self) -> str:
            return self.urls[-1]

    async def run_action() -> dict[str, object]:
        session = FakeBrowserSession()
        result = await Tools().execute_action(
            {"search_google": {"query": "browser use sprint 10 polish"}},
            browser_session=session,
        )
        assert session.urls == ["https://www.google.com/search?q=browser+use+sprint+10+polish"]
        return result

    assert asyncio.run(run_action()) == {
        "ok": True,
        "url": "https://www.google.com/search?q=browser+use+sprint+10+polish",
        "query": "browser use sprint 10 polish",
    }


def test_observability_hooks_receive_normalized_events() -> None:
    received: list[tuple[str, dict[str, object]]] = []
    hub = ObservabilityHub(trace_id="sprint10-hooks")
    hub.add_langsmith_hook(lambda event: received.append(("langsmith", event.model_dump())))
    hub.add_langfuse_hook(lambda event: received.append(("langfuse", event.model_dump())))

    hub.emit(ObservabilityEvent(name="agent_step", payload={"step": 1, "action": "open_tab"}))

    assert {provider for provider, _ in received} == {"langsmith", "langfuse"}
    for _, payload in received:
        assert payload["trace_id"] == "sprint10-hooks"
        assert payload["name"] == "agent_step"
        assert payload["payload"] == {"step": 1, "action": "open_tab"}


def test_run_cli_writes_json_logs_with_trace_id(monkeypatch, tmp_path) -> None:
    async def fake_inspect_url(url: str, max_elements: int) -> dict[str, object]:
        return {
            "url": url,
            "title": "Sprint 10 Logs",
            "elements": [{"index": 0, "tag_name": "button", "text": "Done"}],
        }

    monkeypatch.setenv("BROWSER_USE_TRACE_ID", "sprint10-trace-001")
    monkeypatch.setattr(cli_module, "_inspect_url", fake_inspect_url)
    log_file = tmp_path / "browser-use-bridge-sprint10.log"
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "run",
            "--task",
            "Finish when Done is visible",
            "--url",
            "file:///tmp/browser-use-bridge-sprint10-log.html",
            "--mock-llm",
            "--max-steps",
            "2",
            "--json",
            "--log-json",
            "--log-file",
            str(log_file),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "done"
    assert payload["trace_id"] == "sprint10-trace-001"
    assert payload["title"] == "Sprint 10 Logs"

    records = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    assert len(records) >= 2
    assert {record["trace_id"] for record in records} == {"sprint10-trace-001"}
    assert {"run_started", "run_finished"} <= {record["name"] for record in records}
