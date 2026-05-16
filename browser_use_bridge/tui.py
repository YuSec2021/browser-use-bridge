from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import DataTable, Footer, Header, RichLog, Static


TERMINAL_PRECISION_THEME: dict[str, str] = {
    "surface": "#0d1117",
    "surface_raised": "#161b22",
    "border": "#30363d",
    "text_primary": "#e6edf3",
    "text_muted": "#8b949e",
    "accent_blue": "#58a6ff",
    "accent_green": "#3fb950",
    "accent_amber": "#d29922",
    "accent_red": "#f85149",
    "accent_purple": "#bc8cff",
}


THEME_SUMMARY = (
    "surface=#0d1117 surface_raised=#161b22 border=#30363d "
    "text_primary=#e6edf3 text_muted=#8b949e accent_blue=#58a6ff "
    "accent_green=#3fb950 accent_amber=#d29922 accent_red=#f85149 "
    "accent_purple=#bc8cff"
)


@dataclass(slots=True)
class DashboardElement:
    index: int
    tag: str
    text: str

    @classmethod
    def from_any(cls, value: Any) -> "DashboardElement":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(
                index=int(value.get("index", 0)),
                tag=str(value.get("tag") or value.get("tag_name") or ""),
                text=str(value.get("text") or value.get("label") or ""),
            )
        return cls(index=0, tag=type(value).__name__, text=str(value))


@dataclass(slots=True)
class DashboardState:
    task: str = "No active task"
    step: int = 0
    url: str = "about:blank"
    elements: list[DashboardElement] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    llm_tokens: str = ""
    loop_alert: str | None = None

    @classmethod
    def sample(cls) -> "DashboardState":
        return cls(
            task="Summarize the current page",
            step=3,
            url="https://example.test/dashboard",
            elements=[
                DashboardElement(index=0, tag="button", text="Search"),
                DashboardElement(index=1, tag="input", text="Query"),
                DashboardElement(index=2, tag="a", text="Docs"),
            ],
            actions=["navigate", "input_text", "click"],
            llm_tokens="thinking -> inspect elements -> choose next action",
            loop_alert="Possible action loop detected on this page.",
        )


def normalize_dashboard_state(state: DashboardState | dict[str, Any] | None = None) -> DashboardState:
    if state is None:
        return DashboardState.sample()
    if isinstance(state, DashboardState):
        return state
    elements = [DashboardElement.from_any(element) for element in state.get("elements", [])]
    actions = [str(action) for action in state.get("actions", [])]
    return DashboardState(
        task=str(state.get("task", "No active task")),
        step=int(state.get("step", 0)),
        url=str(state.get("url", "about:blank")),
        elements=elements,
        actions=actions,
        llm_tokens=str(state.get("llm_tokens", "")),
        loop_alert=state.get("loop_alert"),
    )


def render_dashboard_text(
    state: DashboardState | dict[str, Any] | None = None,
    *,
    color: bool = True,
) -> str:
    dashboard = normalize_dashboard_state(state)
    output = io.StringIO()
    console = Console(
        color_system="truecolor" if color else None,
        force_terminal=color,
        file=output,
        width=100,
        record=True,
    )
    console.print(_dashboard_renderable(dashboard))
    return console.export_text(styles=color).rstrip()


def _dashboard_renderable(state: DashboardState) -> Group:
    status = Table.grid(padding=(0, 2))
    status.add_column(style=TERMINAL_PRECISION_THEME["accent_blue"], no_wrap=True)
    status.add_column(style=TERMINAL_PRECISION_THEME["text_primary"])
    status.add_row("Active Task", state.task)
    status.add_row("Step Counter", str(state.step))
    status.add_row("Current URL", state.url)

    elements = Table(title="DOM Index Table", expand=True)
    elements.add_column("Index", style=TERMINAL_PRECISION_THEME["accent_purple"], no_wrap=True)
    elements.add_column("Tag", style=TERMINAL_PRECISION_THEME["accent_green"], no_wrap=True)
    elements.add_column("Text", style=TERMINAL_PRECISION_THEME["text_primary"])
    for element in state.elements:
        elements.add_row(f"[{element.index}]", element.tag, element.text)
    if not state.elements:
        elements.add_row("-", "-", "No DOM elements captured")

    actions = "\n".join(f"{index + 1}. {action}" for index, action in enumerate(state.actions))
    if not actions:
        actions = "No actions recorded"

    tokens = state.llm_tokens or "No LLM tokens streamed"
    alert = state.loop_alert or "No loop alert"
    alert_style = TERMINAL_PRECISION_THEME["accent_amber"] if state.loop_alert else TERMINAL_PRECISION_THEME["text_muted"]

    return Group(
        Panel(status, title="browser-use-bridge TUI", border_style=TERMINAL_PRECISION_THEME["accent_blue"]),
        Panel(elements, title="DOM Elements", border_style=TERMINAL_PRECISION_THEME["border"]),
        Panel(actions, title="Action History Stream", border_style=TERMINAL_PRECISION_THEME["accent_green"]),
        Panel(tokens, title="Streaming LLM Tokens", border_style=TERMINAL_PRECISION_THEME["accent_purple"]),
        Panel(Text(f"Loop Alert: {alert}", style=alert_style), title="Loop Detection Alerts", border_style=alert_style),
    )


class BrowserUseTUI(App[None]):
    CSS = """
    Screen {
        background: #0d1117;
        color: #e6edf3;
    }

    Header, Footer {
        background: #161b22;
        color: #e6edf3;
    }

    #dashboard {
        height: 100%;
        padding: 1;
    }

    .panel {
        border: solid #30363d;
        background: #161b22;
        padding: 1 2;
        margin: 0 1 1 0;
    }

    #top-row {
        height: 8;
    }

    #task-status {
        width: 2fr;
        border-title-color: #58a6ff;
    }

    #url-bar {
        width: 3fr;
        border-title-color: #58a6ff;
    }

    #dom-table {
        height: 1fr;
        border: solid #30363d;
        background: #161b22;
    }

    #dom-title {
        height: 1;
        color: #58a6ff;
    }

    #streams {
        height: 12;
    }

    #action-stream, #token-stream {
        width: 1fr;
    }

    #loop-alert {
        height: 3;
        color: #d29922;
        border-title-color: #d29922;
    }
    """

    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, state: DashboardState | dict[str, Any] | None = None) -> None:
        super().__init__()
        self.state = normalize_dashboard_state(state)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="dashboard"):
            with Horizontal(id="top-row"):
                yield Static(id="task-status", classes="panel")
                yield Static(id="url-bar", classes="panel")
            yield Static("DOM Index Table", id="dom-title")
            yield DataTable(id="dom-table")
            with Horizontal(id="streams"):
                yield RichLog(id="action-stream", classes="panel", highlight=True, markup=True)
                yield RichLog(id="token-stream", classes="panel", highlight=True, markup=True)
            yield Static(id="loop-alert", classes="panel")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "browser-use-bridge TUI"
        self._render_state()

    def _render_state(self) -> None:
        self.query_one("#task-status", Static).update(
            f"[b]Active Task[/b]\n{self.state.task}\n\n[b]Step Counter[/b]\n{self.state.step}"
        )
        self.query_one("#url-bar", Static).update(f"[b]Current URL[/b]\n{self.state.url}")

        table = self.query_one("#dom-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Index", "Tag", "Text")
        for element in self.state.elements:
            table.add_row(f"[{element.index}]", element.tag, element.text)

        actions = self.query_one("#action-stream", RichLog)
        actions.border_title = "Action History Stream"
        for action in self.state.actions or ["No actions recorded"]:
            actions.write(action)

        tokens = self.query_one("#token-stream", RichLog)
        tokens.border_title = "Streaming LLM Tokens"
        tokens.write(self.state.llm_tokens or "No LLM tokens streamed")

        alert = self.state.loop_alert or "No loop alert"
        self.query_one("#loop-alert", Static).update(f"[b]Loop Alert[/b]\n{alert}")


__all__ = [
    "BrowserUseTUI",
    "DashboardElement",
    "DashboardState",
    "TERMINAL_PRECISION_THEME",
    "THEME_SUMMARY",
    "render_dashboard_text",
]
