from __future__ import annotations

from click.testing import CliRunner

from browser_use_bridge.cli import main
from browser_use_bridge.tui import DashboardState, THEME_SUMMARY, render_dashboard_text


def test_dashboard_text_renders_all_sprint_6_panels_without_color() -> None:
    output = render_dashboard_text(DashboardState.sample(), color=False)

    assert "browser-use-bridge TUI" in output
    assert "Active Task" in output
    assert "Step Counter" in output
    assert "Current URL" in output
    assert "DOM Index Table" in output
    assert "[0]" in output
    assert "Action History Stream" in output
    assert "Streaming LLM Tokens" in output
    assert "Loop Alert" in output
    assert "\x1b[" not in output


def test_dashboard_text_renders_terminal_precision_color() -> None:
    output = render_dashboard_text(DashboardState.sample(), color=True)

    assert "\x1b[" in output
    assert "browser-use-bridge TUI" in output
    assert "Loop Alert" in output


def test_cli_smoke_and_theme_commands_are_deterministic() -> None:
    runner = CliRunner()

    smoke = runner.invoke(main, ["cli", "--tui", "--smoke", "--no-color"])
    assert smoke.exit_code == 0
    assert "browser-use-bridge TUI" in smoke.output
    assert "DOM Index Table" in smoke.output
    assert "\x1b[" not in smoke.output

    theme = runner.invoke(main, ["theme"])
    assert theme.exit_code == 0
    assert theme.output.strip() == THEME_SUMMARY
