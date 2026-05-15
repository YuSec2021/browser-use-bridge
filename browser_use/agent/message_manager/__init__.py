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
        screenshots: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        content = "\n\n".join(
            part for part in self._build_content_parts(current_state, nudge) if part
        )
        content = self._fit_to_budget(content)
        user_content: str | list[dict[str, Any]]
        if screenshots:
            user_content = [{"type": "text", "text": content}]
            user_content.extend(self._format_screenshot_parts(screenshots))
        else:
            user_content = content
        return [
            {
                "role": "system",
                "content": (
                    "You are a browser automation agent. Return structured output "
                    "with thinking, evaluation, memory, next_goal, and actions."
                ),
            },
            {"role": "user", "content": user_content},
        ]

    def build_planner_messages(
        self,
        task: str,
        browser_state: BrowserStateSummary,
        history: Any | None = None,
    ) -> list[dict[str, Any]]:
        original_task = self.task
        original_histories = self.histories
        try:
            self.task = task
            if history is not None:
                self.histories = list(getattr(history, "histories", history))
            content = "\n\n".join(
                part for part in self._build_content_parts(browser_state, nudge=None) if part
            )
            content = self._fit_to_budget(content)
        finally:
            self.task = original_task
            self.histories = original_histories
        return [
            {
                "role": "system",
                "content": "You are a planner. Return structured plan steps with sub-goals and expected states.",
            },
            {"role": "user", "content": content},
        ]

    def build_controller_messages(
        self,
        plan_step: str,
        step_results: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        lines = [
            f"Plan step: {plan_step}",
            "Step results:",
        ]
        for result in step_results or []:
            if hasattr(result, "model_dump"):
                result = result.model_dump()
            lines.append(f"- {result}")
        return [
            {
                "role": "system",
                "content": "You are a controller. Use action results to verify planned execution.",
            },
            {"role": "user", "content": "\n".join(lines)},
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
        rows: list[str] = []
        for element in elements[:20]:
            if hasattr(element, "model_dump"):
                element = element.model_dump()
            if isinstance(element, dict):
                index = element.get("index")
                tag = element.get("tag") or element.get("tag_name") or element.get("role") or "element"
                text = element.get("text") or element.get("label") or element.get("aria_label") or ""
                if index is not None:
                    rows.append(f"[{index}] <{tag}> {text}".strip())
                else:
                    rows.append(str(element))
            else:
                rows.append(str(element))
        return "\n".join(rows)

    @staticmethod
    def _format_screenshot_parts(screenshots: list[Any]) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        for screenshot in screenshots:
            boxes = getattr(screenshot, "bounding_boxes", [])
            box_lines = []
            for box in boxes:
                index = getattr(box, "index", None)
                label = getattr(box, "label", None) or getattr(box, "text", None) or ""
                if index is not None:
                    box_lines.append(f"[{index}] {label}".strip())
            parts.append(
                {
                    "type": "text",
                    "text": "\n".join(
                        [
                            f"Annotated screenshot ({getattr(screenshot, 'content_type', 'image/jpeg')}):",
                            *box_lines,
                        ]
                    ),
                }
            )
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": (
                            f"data:{getattr(screenshot, 'content_type', 'image/jpeg')};base64,"
                            f"{getattr(screenshot, 'base64_data', '')}"
                        )
                    },
                }
            )
        return parts


__all__ = ["MessageManager"]
