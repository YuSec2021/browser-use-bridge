from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from browser_use.browser.views import BrowserStateSummary


class AgentOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    thinking: str = ""
    evaluation: str = ""
    memory: str = ""
    next_goal: str = ""
    actions: list[Any] = Field(default_factory=list)


class AgentHistory(BaseModel):
    model_config = ConfigDict(extra="allow")

    model_output: AgentOutput | None = None
    state: BrowserStateSummary | None = None
    error_summary: dict[str, Any] | None = None


class AgentHistoryList(BaseModel):
    model_config = ConfigDict(extra="allow")

    histories: list[AgentHistory] = Field(default_factory=list)

    def save_to_file(self, path: str | Path) -> None:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            self.model_dump_json(indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load_from_file(cls, path: str | Path) -> "AgentHistoryList":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(raw)


class ActionLoopDetector(BaseModel):
    model_config = ConfigDict(extra="allow")

    recent_actions: list[dict[str, Any]] = Field(default_factory=list)
    max_repetitions: int = 3
    nudge: str = (
        "Possible action loop detected on this page. Try a different action "
        "or finish if the task is complete."
    )

    def is_looping(self) -> bool:
        if self.max_repetitions <= 1 or len(self.recent_actions) < self.max_repetitions:
            return False
        tail = self.recent_actions[-self.max_repetitions :]
        return all(entry["action"] == tail[0]["action"] and entry["page"] == tail[0]["page"] for entry in tail)

    def record_action(self, action: Any, state: BrowserStateSummary | None = None) -> None:
        self.recent_actions.append(
            {
                "action": self._stable_json(action),
                "page": self._page_fingerprint(state),
            }
        )
        max_history = max(self.max_repetitions * 2, self.max_repetitions, 1)
        if len(self.recent_actions) > max_history:
            self.recent_actions = self.recent_actions[-max_history:]

    def consume_nudge(self) -> str | None:
        if not self.is_looping():
            return None
        self.recent_actions.clear()
        return self.nudge

    @classmethod
    def _page_fingerprint(cls, state: BrowserStateSummary | None) -> str:
        if state is None:
            return ""
        elements = [
            cls._normalize_element(element)
            for element in state.elements
        ]
        return cls._stable_json({"url": state.url, "elements": elements})

    @staticmethod
    def _normalize_element(element: Any) -> Any:
        if hasattr(element, "model_dump"):
            element = element.model_dump(exclude_none=True)
        if not isinstance(element, dict):
            return str(element)
        return {
            key: element.get(key)
            for key in ("index", "tag", "tag_name", "text", "href", "type")
            if element.get(key) not in (None, "")
        }

    @staticmethod
    def _stable_json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
