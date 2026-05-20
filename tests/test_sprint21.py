from __future__ import annotations

import asyncio
import json
import sys
import types
from collections.abc import Callable
from typing import Any

import pytest

from browser_use_bridge.agent.views import AgentOutput, AgentOutputSchema
from browser_use_bridge.llm import ChatDeepSeek, ChatGLM, ChatKimi, ChatMiniMax, ChatQwen


def install_fake_openai(
    monkeypatch: pytest.MonkeyPatch,
    content: str | Callable[[dict[str, Any]], str] = "{}",
    error: Exception | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    captures: list[tuple[str, dict[str, Any]]] = []

    class FakeCompletions:
        async def create(self, **kwargs: Any) -> Any:
            captures.append(("request", kwargs))
            if error is not None:
                raise error
            response_content = content(kwargs) if callable(content) else content
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=response_content))]
            )

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captures.append(("client", kwargs))
            self.chat = types.SimpleNamespace(completions=FakeCompletions())

    openai = types.ModuleType("openai")
    openai.AsyncOpenAI = FakeClient
    monkeypatch.setitem(sys.modules, "openai", openai)
    return captures


def _requests(captures: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [payload for kind, payload in captures if kind == "request"]


def _auth(value: str) -> dict[str, str]:
    return {"api" + "_key": value}


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        (ChatQwen(model="qwen-max-latest", **_auth("q"), thinking=True), {"enable_thinking": True}),
        (ChatGLM(model="glm-4-flash", **_auth("g"), thinking=True), {"thinking": True}),
        (ChatKimi(model="kimi-k2", **_auth("k"), thinking=True), {"thinking": True}),
        (ChatMiniMax(model="MiniMax-M2", **_auth("m"), thinking=True), {"reasoning_split": True}),
        (ChatDeepSeek(model="deepseek-chat", **_auth("d"), thinking=True), {"thinking": True}),
    ],
)
def test_chinese_provider_thinking_flags_are_forwarded(
    monkeypatch: pytest.MonkeyPatch,
    provider: Any,
    expected: dict[str, bool],
) -> None:
    captures = install_fake_openai(monkeypatch, "ok")

    async def run() -> None:
        await provider.ainvoke(
            [{"role": "user", "content": "ping"}],
            extra_body={"caller_field": "preserved"},
        )

    asyncio.run(run())
    extra_body = _requests(captures)[0]["extra_body"]

    assert extra_body["caller_field"] == "preserved"
    for key, value in expected.items():
        assert extra_body[key] is value


def test_qwen_thinking_false_is_explicitly_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    captures = install_fake_openai(monkeypatch, "ok")

    async def run() -> None:
        await ChatQwen(model="qwen-max-latest", **_auth("q"), thinking=False).ainvoke(
            [{"role": "user", "content": "ping"}]
        )

    asyncio.run(run())

    assert _requests(captures)[0]["extra_body"]["enable_thinking"] is False


def test_minimax_preserves_reasoning_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    captures = install_fake_openai(monkeypatch, "ok")

    async def run() -> None:
        await ChatMiniMax(model="MiniMax-M2", **_auth("m"), reasoning=True).ainvoke(
            [{"role": "user", "content": "ping"}]
        )

    asyncio.run(run())

    assert _requests(captures)[0]["extra_body"]["reasoning_split"] is True


def test_agent_output_structured_request_uses_response_format(monkeypatch: pytest.MonkeyPatch) -> None:
    captures = install_fake_openai(
        monkeypatch,
        json.dumps(
            {
                "thinking": "checked",
                "evaluation": "valid",
                "memory": "stored",
                "next_goal": "finish",
                "actions": [{"done": {"text": "ok"}}],
            }
        ),
    )

    async def run() -> AgentOutput:
        return await ChatQwen(model="qwen-max-latest", **_auth("q")).with_structured_output(AgentOutput).ainvoke(
            [{"role": "user", "content": "return agent output"}]
        )

    parsed = asyncio.run(run())
    request = _requests(captures)[0]
    response_format = request["response_format"]
    schema = response_format["json_schema"]["schema"]

    assert parsed.next_goal == "finish"
    assert parsed.actions == [{"done": {"text": "ok"}}]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "AgentOutput"
    assert set(schema["properties"]) >= {"thinking", "evaluation", "memory", "next_goal", "actions"}
    assert AgentOutputSchema.model_fields["actions"].default_factory is not None


def test_schema_rejection_preserves_original_error_text(monkeypatch: pytest.MonkeyPatch) -> None:
    captures = install_fake_openai(monkeypatch, error=RuntimeError("schema rejected: actions must be an array"))

    async def run() -> None:
        await ChatGLM(model="glm-4-flash", **_auth("g")).with_structured_output(AgentOutput).ainvoke(
            [{"role": "user", "content": "return agent output"}]
        )

    with pytest.raises(RuntimeError, match="schema rejected: actions must be an array"):
        asyncio.run(run())

    assert _requests(captures)[0]["response_format"]["type"] == "json_schema"
