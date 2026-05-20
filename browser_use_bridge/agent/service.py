from __future__ import annotations

import inspect
import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from browser_use_bridge.agent.message_manager import MessageManager
from browser_use_bridge.agent.controller import Controller, ControllerResult
from browser_use_bridge.agent.planner import Plan, Planner, PlanningContext
from browser_use_bridge.agent.retry import RetryController, RetryExhaustedError
from browser_use_bridge.agent.views import ActionLoopDetector, AgentHistory, AgentHistoryList, AgentOutput
from browser_use_bridge.browser import BrowserSession
from browser_use_bridge.browser.views import BrowserStateSummary
from browser_use_bridge.llm.base import BaseChatModel


def _strip_markdown_code_fence(content: str | bytes | bytearray) -> str | bytes:
    if isinstance(content, str):
        stripped = content.strip()
        lines = stripped.splitlines()
        if _is_markdown_fenced_block(lines):
            return "\n".join(lines[1:-1]).strip()
        return stripped

    stripped_bytes = bytes(content).strip()
    lines_bytes = stripped_bytes.splitlines()
    if _is_markdown_fenced_block(lines_bytes):
        return b"\n".join(lines_bytes[1:-1]).strip()
    return stripped_bytes


def _is_markdown_fenced_block(lines: list[str] | list[bytes]) -> bool:
    if len(lines) < 2:
        return False
    opener = lines[0].strip()
    closer = lines[-1].strip()
    fence = b"```" if isinstance(opener, bytes) else "```"
    backtick = b"`" if isinstance(opener, bytes) else "`"
    if closer != fence:
        return False
    if not opener.startswith(fence):
        return False
    info = opener[3:].strip()
    return backtick not in info


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
            return self._parse_agent_output_string(raw_output)
        if isinstance(raw_output, bytes | bytearray):
            return AgentOutput.model_validate_json(_strip_markdown_code_fence(raw_output))
        if isinstance(raw_output, dict) and "content" in raw_output:
            return self._parse_model_content(raw_output["content"])
        content = getattr(raw_output, "content", None)
        if content is not None:
            return self._parse_model_content(content)
        return AgentOutput.model_validate(raw_output)

    def _parse_agent_output_string(self, raw_output: str) -> AgentOutput:
        """Parse agent output string, handling JSON, markdown-fenced JSON, and YAML-like formats."""
        import re
        stripped = _strip_markdown_code_fence(raw_output)
        # Normalize memory and evaluation fields: must be strings, not objects/arrays/null.
        stripped = re.sub(r'"memory"\s*:\s*\[[^\]]*\]', '"memory": ""', stripped)
        stripped = re.sub(r'"memory"\s*:\s*\{[^}]*\}', '"memory": ""', stripped)
        stripped = re.sub(r'"memory"\s*:\s*null', '"memory": ""', stripped)
        stripped = re.sub(r'"evaluation"\s*:\s*\{[^}]*\}', '"evaluation": ""', stripped)
        stripped = re.sub(r'"evaluation"\s*:\s*\[[^\]]*\]', '"evaluation": ""', stripped)
        stripped = re.sub(r'"evaluation"\s*:\s*null', '"evaluation": ""', stripped)
        stripped = re.sub(r'"thinking"\s*:\s*\{[^}]*\}', '"thinking": ""', stripped)
        stripped = re.sub(r'"thinking"\s*:\s*\[[^\]]*\]', '"thinking": ""', stripped)
        stripped = re.sub(r'"thinking"\s*:\s*null', '"thinking": ""', stripped)
        stripped = re.sub(r'"next_goal"\s*:\s*\{[^}]*\}', '"next_goal": ""', stripped)
        stripped = re.sub(r'"next_goal"\s*:\s*\[[^\]]*\]', '"next_goal": ""', stripped)
        stripped = re.sub(r'"next_goal"\s*:\s*null', '"next_goal": ""', stripped)
        try:
            return AgentOutput.model_validate_json(stripped)
        except Exception:
            pass
        # Try YAML-like format (model outputs YAML when thinking is enabled)
        if stripped.startswith("thinking:") or "\nthinking:" in stripped or stripped.startswith('{"'):
            parsed = self._parse_yaml_like_output(stripped)
            if parsed is not None:
                return parsed
        # Fallback: model returned plain text (e.g., refusal). Use the text as evaluation.
        return AgentOutput(
            evaluation=stripped[:2000],
            thinking="",
            memory="",
            next_goal="",
            actions=[],
        )

    def _parse_yaml_like_output(self, text: str) -> AgentOutput | None:
        """Parse YAML-like or malformed-JSON output format from models that use reasoning."""
        import re
        import json
        import ast
        result: dict[str, Any] = {}

        # Try Python dict literal format (single quotes): {'action': 'navigate', 'url': '...'}
        if text.strip().startswith("{"):
            try:
                parsed = ast.literal_eval(text.strip())
                if isinstance(parsed, dict):
                    result.update(parsed)
                    for field in ("memory", "evaluation", "thinking", "next_goal"):
                        if field in result and not isinstance(result[field], str):
                            result[field] = ""
            except Exception:
                pass

        # Try to parse whole text as JSON first
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                result.update(parsed)
                for field in ("memory", "evaluation", "thinking", "next_goal"):
                    if field in result and not isinstance(result[field], str):
                        result[field] = ""
        except Exception:
            pass

        # Extract actions from JSON array format: [{"action_type": "navigate", "url": "..."}]
        if "actions" not in result or not result.get("actions"):
            json_actions_match = re.search(r'"actions":\s*\[(\s*\{[^}]+\}[^\]]*)\]', text, re.DOTALL)
            if json_actions_match:
                actions_str = "[" + json_actions_match.group(1) + "]"
                try:
                    result["actions"] = json.loads(actions_str)
                except Exception:
                    pass

        # Extract actions from single dict format: {"action": "navigate", "url": "..."}
        if "actions" not in result or not result.get("actions"):
            json_single_action = re.search(r'"actions":\s*\{([^}]+)\}', text, re.DOTALL)
            if json_single_action:
                try:
                    result["actions"] = [json.loads("{" + json_single_action.group(1) + "}")]
                except Exception:
                    pass

        # Extract actions from YAML list format: "- action_type: value"
        if not result.get("actions"):
            actions = []
            yaml_action_pattern = re.compile(r'^\s*-\s+(\w+):\s*([^\n]+)', re.MULTILINE)
            for match in yaml_action_pattern.finditer(text):
                action_type = match.group(1)
                action_value = match.group(2).strip().rstrip(',').rstrip('}')
                actions.append({action_type: action_value})
            if actions:
                result["actions"] = actions

        # Extract text fields using regex
        for field in ("thinking", "evaluation", "memory", "next_goal"):
            if field not in result or not result.get(field):
                pattern = rf'"{field}"\s*:\s*"(.*?)"(?:\s*,|\s*\n|\s*\}}|\s*$)'
                match = re.search(pattern, text, re.DOTALL)
                if not match:
                    pattern = rf"{field}:\s*[\"']?(.+?)[\"']?\s*(?=\n|\w+:|\[)"
                    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
                if match:
                    value = match.group(1).strip()
                    if field == "memory":
                        if value in ("[]", "None", "", "null") or value.startswith("["):
                            result[field] = ""
                        else:
                            result[field] = value
                    else:
                        result[field] = value

        # Ensure all required fields exist
        for field in ("thinking", "evaluation", "memory", "next_goal"):
            if field not in result:
                result[field] = ""
        if "actions" not in result:
            result["actions"] = []

        try:
            return AgentOutput.model_validate(result)
        except Exception:
            pass
        return None

    def _parse_model_content(self, content: Any) -> AgentOutput:
        if isinstance(content, AgentOutput):
            return content
        if isinstance(content, str):
            return AgentOutput.model_validate_json(_strip_markdown_code_fence(content))
        if isinstance(content, bytes | bytearray):
            return AgentOutput.model_validate_json(_strip_markdown_code_fence(content))
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
