from __future__ import annotations

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


class ActionLoopDetector(BaseModel):
    model_config = ConfigDict(extra="allow")

    recent_actions: list[Any] = Field(default_factory=list)
    max_repetitions: int = 3

    def is_looping(self) -> bool:
        if self.max_repetitions <= 1 or len(self.recent_actions) < self.max_repetitions:
            return False
        tail = self.recent_actions[-self.max_repetitions :]
        return all(action == tail[0] for action in tail)
