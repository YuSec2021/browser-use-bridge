from __future__ import annotations

import inspect
import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from browser_use.agent.message_manager import MessageManager
from browser_use.agent.controller import Controller, ControllerResult
from browser_use.agent.planner import Plan, Planner, PlanningContext
from browser_use.agent.retry import RetryController, RetryExhaustedError
from browser_use.agent.views import ActionLoopDetector, AgentHistory, AgentHistoryList, AgentOutput
from browser_use.browser import BrowserSession
from browser_use.browser.views import BrowserStateSummary
from browser_use.llm.base import BaseChatModel


class Agent(BaseModel):
    """Browser automation agent that perceives page state, asks an LLM for actions, and executes them."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task: str
    llm: BaseChatModel
    browser_session: Any
    tools: Any | None = None
    max_steps: int = 20
    message_manager: MessageManager | None = None
    loop_detector: ActionLoopDetector | None = None
    retry_controller: RetryController | None = None
    planner: Any | None = None
    controller: Any | None = None
    memory_store: Any | None = None

    async def run(self) -> AgentHistoryList:
        """Run the task until completion or until the maximum step count is reached."""
        if self.planner is not None or self.controller is not None:
            return await self._run_separated()

        history_list = AgentHistoryList()
        manager = self.message_manager or MessageManager(task=self.task, memory_store=self.memory_store)
        if self.memory_store is not None and manager.memory_store is None:
            manager.memory_store = self.memory_store
        loop_detector = self.loop_detector or ActionLoopDetector()
        retry_controller = self.retry_controller or RetryController()
        tools = self.tools or _DefaultTools()

        for _ in range(self.max_steps):
            state = await self._perceive()
            nudge = loop_detector.consume_nudge()
            messages = manager.build_messages(state, nudge=nudge)
            model_output = await self._reason(messages)

            history = AgentHistory(model_output=model_output, state=state)
            history_list.histories.append(history)
            manager.add_history(history)
            self._remember_step(history)

            try:
                should_stop = await self._act(model_output, state, loop_detector, retry_controller, tools)
            except RetryExhaustedError as error:
                history.error_summary = error.summary
                break
            if should_stop:
                break

        self.message_manager = manager
        self.loop_detector = loop_detector
        self.retry_controller = retry_controller
        return history_list

    async def _run_separated(self) -> AgentHistoryList:
        history_list = AgentHistoryList()
        manager = self.message_manager or MessageManager(task=self.task, memory_store=self.memory_store)
        if self.memory_store is not None and manager.memory_store is None:
            manager.memory_store = self.memory_store
        planner = self.planner or Planner()
        tools = self.tools or _DefaultTools()
        controller = self.controller or Controller(
            tools=tools,
            planner=planner,
            browser_session=self.browser_session,
        )
        if getattr(controller, "planner", None) is None:
            controller.planner = planner
        if getattr(controller, "browser_session", None) is None:
            controller.browser_session = self.browser_session

        state = await self._perceive()
        context = PlanningContext(task=self.task, browser_state=state, history=history_list)
        plan = await self._decompose(planner, context)
        controller_result = await self._execute_plan(controller, plan)
        model_output = self._model_output_from_controller(plan, controller_result)
        history = AgentHistory(
            model_output=model_output,
            state=state,
            plan=plan,
            controller_result=controller_result,
        )
        history_list.histories.append(history)
        manager.add_history(history)
        self._remember_step(history)

        self.message_manager = manager
        self.planner = planner
        self.controller = controller
        return history_list

    def _remember_step(self, history: AgentHistory) -> None:
        store = self.memory_store or getattr(self.message_manager, "memory_store", None)
        if store is None:
            return
        remember = getattr(store, "add_from_agent_step", None) or getattr(store, "remember_step", None)
        if remember is not None:
            remember(history, task_id=self.task)

    async def _decompose(self, planner: Any, context: PlanningContext) -> Plan:
        plan = planner.decompose(context)
        if inspect.isawaitable(plan):
            plan = await plan
        return plan

    async def _execute_plan(self, controller: Any, plan: Plan) -> ControllerResult:
        result = controller.execute_plan(plan)
        if inspect.isawaitable(result):
            result = await result
        return result

    @staticmethod
    def _model_output_from_controller(plan: Plan, result: ControllerResult) -> AgentOutput:
        next_goal = plan.steps[0].sub_goal if plan.steps else ""
        actions = [step.action for step in plan.steps]
        return AgentOutput(
            evaluation="controller completed plan" if result.success else "controller failed plan",
            next_goal=next_goal,
            actions=actions,
        )

    async def _perceive(self) -> BrowserStateSummary:
        url = await self.browser_session.get_current_url()
        title = await self.browser_session.get_title()
        elements = await self._extract_elements()
        return BrowserStateSummary(url=url, title=title, elements=elements)

    async def _extract_elements(self) -> list[dict[str, Any]]:
        expression = """() => Array.from(document.querySelectorAll('a, button, input, textarea, select'))
            .slice(0, 50)
            .map((element, index) => ({
                index,
                tag: element.tagName.toLowerCase(),
                text: (element.innerText || element.value || element.getAttribute('aria-label') || '').trim(),
                href: element.href || null,
                type: element.type || null
            }))"""
        try:
            result = await self.browser_session.evaluate(expression)
        except Exception:
            return []
        return result if isinstance(result, list) else []

    async def _reason(self, messages: list[dict[str, Any]]) -> AgentOutput:
        raw_output = await self.llm.ainvoke(messages)
        if isinstance(raw_output, AgentOutput):
            return raw_output
        if isinstance(raw_output, str):
            return AgentOutput.model_validate_json(raw_output)
        if isinstance(raw_output, bytes | bytearray):
            return AgentOutput.model_validate_json(bytes(raw_output))
        if isinstance(raw_output, dict) and "content" in raw_output:
            return self._parse_model_content(raw_output["content"])
        content = getattr(raw_output, "content", None)
        if content is not None:
            return self._parse_model_content(content)
        return AgentOutput.model_validate(raw_output)

    def _parse_model_content(self, content: Any) -> AgentOutput:
        if isinstance(content, AgentOutput):
            return content
        if isinstance(content, str):
            return AgentOutput.model_validate_json(content)
        if isinstance(content, bytes | bytearray):
            return AgentOutput.model_validate_json(bytes(content))
        if isinstance(content, dict):
            return AgentOutput.model_validate(content)
        return AgentOutput.model_validate(json.loads(str(content)))

    async def _act(
        self,
        model_output: AgentOutput,
        state: BrowserStateSummary,
        loop_detector: ActionLoopDetector,
        retry_controller: RetryController,
        tools: Any,
    ) -> bool:
        for action in model_output.actions:
            await retry_controller.run(
                lambda action=action: self._execute_action(tools, action),
                operation=self._operation_name(action),
            )
            loop_detector.record_action(action, state)
            if isinstance(action, dict) and "done" in action:
                return True
        return False

    def _operation_name(self, action: Any) -> str:
        if isinstance(action, dict) and len(action) == 1:
            return f"action:{next(iter(action))}"
        return "action:unknown"

    async def _execute_action(self, tools: Any, action: Any) -> Any:
        executor = getattr(tools, "execute_action", None)
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


class _DefaultTools:
    async def execute_action(
        self,
        action: Any,
        browser_session: BrowserSession | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if not isinstance(action, dict):
            return {"ok": False, "error": "Action must be a dictionary"}
        if "navigate" in action:
            if browser_session is None:
                raise RuntimeError("navigate action requires a browser session")
            await browser_session.navigate(str(action["navigate"]["url"]))
            return {"ok": True}
        if "done" in action:
            return {"ok": True, "done": True}
        if "click" in action:
            if browser_session is None:
                raise RuntimeError("click action requires a browser session")
            index = int(action["click"]["index"])
            await browser_session.evaluate(
                """(index) => {
                    const elements = Array.from(document.querySelectorAll('a, button, input, textarea, select'));
                    if (!elements[index]) throw new Error(`No clickable element at index ${index}`);
                    elements[index].click();
                }""",
                index,
            )
            return {"ok": True}
        return {"ok": False, "error": f"Unsupported action: {action}"}


__all__ = ["Agent"]
