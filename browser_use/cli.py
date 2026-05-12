from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import click

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_use.tui import BrowserUseTUI, DashboardState, THEME_SUMMARY, render_dashboard_text


def _load_state(path: str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main() -> None:
    """browser-use developer CLI."""


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


if __name__ == "__main__":
    main()
