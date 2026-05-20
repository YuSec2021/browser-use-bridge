from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from pydantic_core import ValidationError

from browser_use_bridge.agent.service import Agent as BridgeAgent
from browser_use_bridge.llm.base import BaseChatModel as BridgeBaseChatModel


class FakeSession:
    async def get_current_url(self) -> str:
        return "https://example.test"

    async def get_title(self) -> str:
        return "Example"

    async def evaluate(self, *_: object, **__: object) -> list[object]:
        return []


class BridgeFakeLLM(BridgeBaseChatModel):
    def __init__(self, response: str | bytes) -> None:
        super().__init__(model="fake")
        self.response = response

    async def ainvoke(self, messages: Sequence[dict[str, Any]] | Sequence[Any], **kwargs: Any) -> str | bytes:
        return self.response

    async def astream(
        self,
        messages: Sequence[dict[str, Any]] | Sequence[Any],
        **kwargs: Any,
    ) -> AsyncIterator[str | bytes]:
        if False:
            yield self.response


def test_bridge_agent_reason_accepts_fenced_json() -> None:
    response = """```json
{"thinking":"qwen fenced","evaluation":"ok","memory":"","next_goal":"finish","actions":[{"done":{"success":true,"text":"wrapped json parsed"}}]}
```"""

    async def run() -> None:
        agent = BridgeAgent(task="finish", llm=BridgeFakeLLM(response), browser_session=FakeSession(), max_steps=1)
        history = await agent.run()
        output = history.histories[0].model_output
        assert output is not None
        assert output.thinking == "qwen fenced"
        assert output.actions == [{"done": {"success": True, "text": "wrapped json parsed"}}]

    asyncio.run(run())


def test_bridge_parse_model_content_accepts_fenced_and_raw_json() -> None:
    agent = BridgeAgent.model_construct()

    fenced = agent._parse_model_content(
        """```JSON
{"thinking":"nested fenced","actions":[{"done":{"success":true}}]}
```"""
    )
    raw = agent._parse_model_content('{"thinking":"raw","actions":[]}')

    assert fenced.thinking == "nested fenced"
    assert fenced.actions == [{"done": {"success": True}}]
    assert raw.thinking == "raw"
    assert raw.actions == []


def test_bridge_agent_reason_accepts_fenced_bytes_json() -> None:
    response = b"""```json
{"thinking":"bridge bytes","actions":[{"done":{"success":true,"text":"bytes parsed"}}]}
```"""

    async def run() -> None:
        agent = BridgeAgent(task="finish", llm=BridgeFakeLLM(response), browser_session=FakeSession(), max_steps=1)
        history = await agent.run()
        output = history.histories[0].model_output
        assert output is not None
        assert output.thinking == "bridge bytes"
        assert output.actions == [{"done": {"success": True, "text": "bytes parsed"}}]

    asyncio.run(run())


def test_invalid_non_fenced_model_output_is_still_rejected() -> None:
    agent = BridgeAgent.model_construct()

    with pytest.raises(ValidationError):
        agent._parse_model_content('Here is the JSON: {"actions": []}')
