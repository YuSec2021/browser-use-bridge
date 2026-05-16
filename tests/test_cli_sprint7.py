from __future__ import annotations

import inspect
import json

from click.testing import CliRunner

import browser_use_bridge.cli as cli_module
from browser_use_bridge import Agent, BrowserSession, Tools
from browser_use_bridge.cli import main


def test_sprint_7_commands_are_visible_in_help() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "Usage:" in result.output
    assert "run" in result.output
    assert "list-tools" in result.output
    assert "inspect" in result.output
    assert "session" in result.output


def test_list_tools_json_contains_builtin_actions() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["list-tools", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    names = {tool["name"] for tool in payload["tools"]}
    assert {"navigate", "click", "input_text", "extract_content", "done"} <= names


def test_inspect_json_uses_machine_readable_summary(monkeypatch) -> None:
    async def fake_inspect_url(url: str, max_elements: int) -> dict[str, object]:
        return {
            "url": url,
            "title": "Sprint 7 Inspect",
            "elements": [{"index": 0, "tag_name": "button", "text": "Continue"}][:max_elements],
        }

    monkeypatch.setattr(cli_module, "_inspect_url", fake_inspect_url)
    runner = CliRunner()

    result = runner.invoke(main, ["inspect", "--url", "file:///tmp/page.html", "--json", "--max-elements", "20"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["title"] == "Sprint 7 Inspect"
    assert payload["elements"][0]["text"] == "Continue"


def test_run_mock_llm_json_is_deterministic(monkeypatch) -> None:
    async def fake_inspect_url(url: str, max_elements: int) -> dict[str, object]:
        return {
            "url": url,
            "title": "Sprint 7 Run",
            "elements": [{"index": 0, "tag_name": "button", "text": "Done"}],
        }

    monkeypatch.setattr(cli_module, "_inspect_url", fake_inspect_url)
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "run",
            "--task",
            "Open the page and finish when the Done button is visible",
            "--url",
            "file:///tmp/page.html",
            "--mock-llm",
            "--max-steps",
            "3",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "done"
    assert payload["steps"] >= 1
    assert "Done" in payload["final_result"]


def test_public_api_exports_have_docstrings_and_tools() -> None:
    assert inspect.getdoc(Agent)
    assert inspect.getdoc(BrowserSession)
    assert inspect.getdoc(Tools)

    names = {action["name"] for action in Tools().list_actions()}
    assert "navigate" in names
    assert "done" in names
