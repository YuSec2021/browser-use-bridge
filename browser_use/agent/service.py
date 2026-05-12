from __future__ import annotations

import inspect
import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from browser_use.agent.message_manager import MessageManager
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

    async def run(self) -> AgentHistoryList:
        """Run the task until completion or until the maximum step count is reached."""
        history_list = AgentHistoryList()
        manager = self.message_manager or MessageManager(task=self.task)
        loop_detector = self.loop_detector or ActionLoopDetector()
        tools = self.tools or _DefaultTools()

        for _ in range(self.max_steps):
            state = await self._perceive()
            nudge = loop_detector.consume_nudge()
            messages = manager.build_messages(state, nudge=nudge)
            model_output = await self._reason(messages)

            history = AgentHistory(model_output=model_output, state=state)
            history_list.histories.append(history)
            manager.add_history(history)

            should_stop = await self._act(model_output, state, loop_detector, tools)
            if should_stop:
                break

        self.message_manager = manager
        self.loop_detector = loop_detector
        return history_list

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
        tools: Any,
    ) -> bool:
        for action in model_output.actions:
            await self._execute_action(tools, action)
            loop_detector.record_action(action, state)
            if isinstance(action, dict) and "done" in action:
                return True
        return False

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
