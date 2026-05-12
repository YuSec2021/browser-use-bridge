from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from browser_use.agent.views import AgentHistory
from browser_use.browser.views import BrowserStateSummary


class MessageManager(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    task: str
    max_tokens: int = 4000
    keep_recent_steps: int = 3
    histories: list[AgentHistory] = Field(default_factory=list)

    def add_history(self, history: AgentHistory) -> None:
        self.histories.append(history)

    def build_messages(
        self,
        current_state: BrowserStateSummary,
        nudge: str | None = None,
    ) -> list[dict[str, Any]]:
        content = "\n\n".join(
            part for part in self._build_content_parts(current_state, nudge) if part
        )
        content = self._fit_to_budget(content)
        return [
            {
                "role": "system",
                "content": (
                    "You are a browser automation agent. Return structured output "
                    "with thinking, evaluation, memory, next_goal, and actions."
                ),
            },
            {"role": "user", "content": content},
        ]

    def _build_content_parts(
        self,
        current_state: BrowserStateSummary,
        nudge: str | None,
    ) -> list[str]:
        older, recent = self._split_history()
        parts = [
            f"Task: {self.task}",
            self._format_current_state(current_state),
        ]
        if older:
            parts.append(self._summarize_history(older))
        if recent:
            parts.append(self._format_recent_history(recent))
        if nudge:
            parts.append(f"Nudge: {nudge}")
        return parts

    def _split_history(self) -> tuple[list[AgentHistory], list[AgentHistory]]:
        if self.keep_recent_steps <= 0:
            return self.histories, []
        return self.histories[:-self.keep_recent_steps], self.histories[-self.keep_recent_steps :]

    def _format_current_state(self, state: BrowserStateSummary) -> str:
        return "\n".join(
            [
                "Current browser state:",
                f"- url: {state.url}",
                f"- title: {state.title}",
                f"- elements: {self._format_elements(state.elements)}",
            ]
        )

    def _summarize_history(self, histories: list[AgentHistory]) -> str:
        lines = ["Compressed older history:"]
        for index, history in enumerate(histories):
            output = history.model_output
            if output is None:
                continue
            summary_bits = [
                bit
                for bit in [
                    output.evaluation,
                    output.memory,
                    output.next_goal,
                    self._format_actions(output.actions),
                ]
                if bit
            ]
            lines.append(f"- step {index}: {' | '.join(summary_bits)}")
        return "\n".join(lines)

    def _format_recent_history(self, histories: list[AgentHistory]) -> str:
        lines = ["Recent uncompressed history:"]
        start = len(self.histories) - len(histories)
        for offset, history in enumerate(histories):
            output = history.model_output
            state = history.state
            lines.append(f"- step {start + offset}:")
            if state is not None:
                lines.append(f"  state: {state.title} ({state.url})")
            if output is not None:
                lines.append(f"  thinking: {output.thinking}")
                lines.append(f"  evaluation: {output.evaluation}")
                lines.append(f"  memory: {output.memory}")
                lines.append(f"  next_goal: {output.next_goal}")
                lines.append(f"  actions: {self._format_actions(output.actions)}")
        return "\n".join(lines)

    def _fit_to_budget(self, content: str) -> str:
        max_chars = max(self.max_tokens * 4, 1000)
        if len(content) <= max_chars:
            return content
        keep_head = max_chars // 3
        keep_tail = max_chars - keep_head - 40
        return f"{content[:keep_head]}\n...[context compressed]...\n{content[-keep_tail:]}"

    @staticmethod
    def _format_actions(actions: list[Any]) -> str:
        return ", ".join(str(action) for action in actions)

    @staticmethod
    def _format_elements(elements: list[Any]) -> str:
        if not elements:
            return "[]"
        return str(elements[:20])


__all__ = ["MessageManager"]
