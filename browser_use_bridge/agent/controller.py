from __future__ import annotations

import inspect
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from browser_use_bridge.agent.planner import Plan, PlanStep


class ControllerState(Enum):
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    REPLANNING = "replanning"
    DONE = "done"
    FAILED = "failed"


class StateTransition(BaseModel):
    model_config = ConfigDict(extra="allow")

    state: ControllerState
    reason: str = ""


class StepResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    pending_action_id: str
    sub_goal: str
    action: dict[str, Any]
    result: Any = None
    verified: bool = False
    attempts: int = 1
    reason: str = ""


class ControllerResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    success: bool
    step_results: list[StepResult] = Field(default_factory=list)
    failure_reason: str | None = None


class Controller(BaseModel):
    """Executes planned actions and records observable state transitions."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    tools: Any
    planner: Any | None = None
    browser_session: Any | None = None
    state: ControllerState = ControllerState.PLANNING
    transition_history: list[StateTransition] = Field(default_factory=list)
    step_results: list[StepResult] = Field(default_factory=list)
    current_plan: Plan | None = None
    failure_reason: str | None = None

    async def execute_plan(self, plan: Plan) -> ControllerResult:
        self.current_plan = plan
        self.failure_reason = None
        self._transition(ControllerState.PLANNING, "plan accepted")
        active_plan = plan

        while True:
            replan_requested = False
            for step in active_plan.steps:
                step_result = await self._execute_step(step)
                self.step_results.append(step_result)
                if step_result.verified:
                    continue
                if step.fallback_strategy == "replan" and self.planner is not None:
                    self._transition(ControllerState.REPLANNING, step_result.reason)
                    active_plan = await self._revise_plan(active_plan, step, step_result.reason)
                    self.current_plan = active_plan
                    replan_requested = True
                    break
                self.abort(step_result.reason or f"Step failed: {step.sub_goal}")
                return ControllerResult(
                    success=False,
                    step_results=self.step_results,
                    failure_reason=self.failure_reason,
                )
            if replan_requested:
                continue
            self._transition(ControllerState.DONE, "plan complete")
            return ControllerResult(success=True, step_results=self.step_results)

    def step_result(self, pending_action_id: str) -> StepResult:
        for result in self.step_results:
            if result.pending_action_id == pending_action_id:
                return result
        raise KeyError(pending_action_id)

    def abort(self, reason: str = "aborted") -> None:
        self.failure_reason = reason
        self._transition(ControllerState.FAILED, reason)

    def checkpoint(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "failure_reason": self.failure_reason,
            "plan": self.current_plan.model_dump() if self.current_plan else None,
            "step_results": [result.model_dump() for result in self.step_results],
            "transition_history": [entry.model_dump() for entry in self.transition_history],
        }

    async def _execute_step(self, step: PlanStep) -> StepResult:
        pending_action_id = f"step-{uuid.uuid4().hex[:12]}"
        attempts = step.max_retries + 1
        last_result: Any = None
        last_reason = ""
        for attempt in range(1, attempts + 1):
            self._transition(ControllerState.EXECUTING, step.sub_goal)
            last_result = await self._execute_action(step.action)
            self._transition(ControllerState.VERIFYING, step.expected_state)
            verified, last_reason = self._verify_result(step.expected_state, last_result)
            if verified:
                return StepResult(
                    pending_action_id=pending_action_id,
                    sub_goal=step.sub_goal,
                    action=step.action,
                    result=last_result,
                    verified=True,
                    attempts=attempt,
                )
        return StepResult(
            pending_action_id=pending_action_id,
            sub_goal=step.sub_goal,
            action=step.action,
            result=last_result,
            verified=False,
            attempts=attempts,
            reason=last_reason,
        )

    async def _execute_action(self, action: dict[str, Any]) -> Any:
        executor = getattr(self.tools, "execute_action", None)
        if executor is None:
            raise TypeError("tools must provide execute_action(action, **kwargs)")
        kwargs: dict[str, Any] = {}
        parameters = inspect.signature(executor).parameters
        if "browser_session" in parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        ):
            kwargs["browser_session"] = self.browser_session
        result = executor(action, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _revise_plan(self, plan: Plan, failed_step: PlanStep, reason: str) -> Plan:
        revised = self.planner.revise(plan, failed_step, reason)
        if inspect.isawaitable(revised):
            revised = await revised
        return revised

    def _transition(self, state: ControllerState, reason: str = "") -> None:
        self.state = state
        self.transition_history.append(StateTransition(state=state, reason=reason))

    @classmethod
    def _verify_result(cls, expected_state: str, result: Any) -> tuple[bool, str]:
        if cls._result_failed(result):
            return False, "action result was not ok"
        expected = expected_state.strip()
        if not expected:
            return True, ""
        expected_lower = expected.lower()
        result_text = cls._result_text(result)
        if expected_lower in {"ok", "ok is true", "success", "success is true"}:
            return (not cls._result_failed(result), "") if result is not None else (True, "")
        if expected_lower.startswith("text contains "):
            needle = expected[len("text contains ") :].strip().lower()
            return (needle in result_text, "") if needle in result_text else (False, f"text did not contain {needle!r}")
        if expected_lower.startswith("url contains "):
            needle = expected[len("url contains ") :].strip().lower()
            return (needle in result_text, "") if needle in result_text else (False, f"url did not contain {needle!r}")
        return (expected_lower in result_text, "") if expected_lower in result_text else (False, f"expected state not observed: {expected}")

    @staticmethod
    def _result_failed(result: Any) -> bool:
        return isinstance(result, dict) and result.get("ok") is False

    @staticmethod
    def _result_text(result: Any) -> str:
        if isinstance(result, dict):
            return " ".join(str(value) for value in result.values()).lower()
        return str(result).lower()


__all__ = [
    "Controller",
    "ControllerResult",
    "ControllerState",
    "StateTransition",
    "StepResult",
]
