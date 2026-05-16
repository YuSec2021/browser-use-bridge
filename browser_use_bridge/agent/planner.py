from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from browser_use_bridge.agent.views import AgentHistoryList
from browser_use_bridge.browser.views import BrowserStateSummary


FallbackStrategy = Literal["retry", "skip", "fallback", "abort", "replan"]


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="allow")

    sub_goal: str
    expected_state: str
    action: dict[str, Any] = Field(default_factory=dict)
    max_retries: int = Field(default=1, ge=0)
    fallback_strategy: FallbackStrategy = "replan"


class Plan(BaseModel):
    model_config = ConfigDict(extra="allow")

    task: str
    steps: list[PlanStep] = Field(default_factory=list)


class PlanningContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    task: str
    browser_state: BrowserStateSummary
    history: AgentHistoryList = Field(default_factory=AgentHistoryList)


class Planner:
    """Deterministic task decomposer used by the separated agent loop."""

    async def decompose(self, context: PlanningContext) -> Plan:
        action = self._choose_action(context)
        sub_goal = self._sub_goal_for(context.task, action)
        expected_state = self._expected_state_for(context.task, action)
        return Plan(
            task=context.task,
            steps=[
                PlanStep(
                    sub_goal=sub_goal,
                    expected_state=expected_state,
                    action=action,
                    max_retries=1,
                    fallback_strategy="replan",
                )
            ],
        )

    async def revise(self, plan: Plan, failed_step: PlanStep, reason: str) -> Plan:
        return Plan(
            task=plan.task,
            steps=[
                PlanStep(
                    sub_goal=f"Recover from failed step: {failed_step.sub_goal}",
                    expected_state="ok is true",
                    action={"done": {"success": False, "text": reason}},
                    max_retries=0,
                    fallback_strategy="abort",
                )
            ],
        )

    def _choose_action(self, context: PlanningContext) -> dict[str, Any]:
        task_lower = context.task.lower()
        for element in context.browser_state.elements:
            element_data = self._element_dict(element)
            text = str(element_data.get("text") or "").lower()
            tag = str(element_data.get("tag") or element_data.get("tag_name") or "").lower()
            index = element_data.get("index")
            if index is None:
                continue
            if "click" in task_lower or "continue" in task_lower or tag in {"button", "a"}:
                if not text or text in task_lower or "continue" in text or tag in {"button", "a"}:
                    return {"click": {"index": index}}
        if "http://" in context.task or "https://" in context.task or "file://" in context.task:
            for token in context.task.split():
                if token.startswith(("http://", "https://", "file://")):
                    return {"navigate": {"url": token.strip(".,")}}
        return {"done": {"success": True, "text": context.task}}

    @staticmethod
    def _sub_goal_for(task: str, action: dict[str, Any]) -> str:
        if "click" in action:
            return f"Click the target element for: {task}"
        if "navigate" in action:
            return f"Navigate to the requested page for: {task}"
        return f"Finish task: {task}"

    @staticmethod
    def _expected_state_for(task: str, action: dict[str, Any]) -> str:
        if "click" in action:
            return f"task progresses toward {task}"
        if "navigate" in action:
            url = action["navigate"].get("url", "")
            return f"url contains {url}"
        return "ok is true"

    @staticmethod
    def _element_dict(element: Any) -> dict[str, Any]:
        if hasattr(element, "model_dump"):
            element = element.model_dump(exclude_none=True)
        return element if isinstance(element, dict) else {}


__all__ = ["FallbackStrategy", "Plan", "PlanStep", "Planner", "PlanningContext"]
