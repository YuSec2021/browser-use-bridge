from __future__ import annotations

import asyncio
import json
import sys
import types
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

import pytest

from browser_use_bridge.llm import BaseChatModel, ChatDeepSeek, ChatGLM, ChatKimi, ChatMiniMax, ChatQwen
from browser_use_bridge.llm.glm import ChatGLM as ModuleGLM
from browser_use_bridge.llm.kimi import ChatKimi as ModuleKimi
from browser_use_bridge.llm.minimax import ChatMiniMax as ModuleMiniMax
from browser_use_bridge.llm.qwen import ChatQwen as ModuleQwen


def install_fake_openai(content: str | Callable[[dict[str, Any]], str]) -> list[tuple[str, dict[str, Any]]]:
    captures: list[tuple[str, dict[str, Any]]] = []

    class FakeCompletions:
        async def create(self, **kwargs: Any) -> Any:
            captures.append(("request", kwargs))
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
    sys.modules["openai"] = openai
    return captures


def test_chinese_provider_public_api_instantiates_without_network() -> None:
    providers = [
        ChatKimi(model="kimi-k2", api_key="test-kimi"),
        ChatQwen(model="qwen-max-latest", api_key="test-qwen"),
        ChatGLM(model="glm-4-flash", api_key="test-glm"),
        ChatMiniMax(model="MiniMax-M2", api_key="test-minimax"),
    ]

    assert ChatKimi is ModuleKimi
    assert ChatQwen is ModuleQwen
    assert ChatGLM is ModuleGLM
    assert ChatMiniMax is ModuleMiniMax
    for provider in providers:
        assert isinstance(provider, BaseChatModel)
        assert provider.model
        assert provider.api_key and provider.api_key.startswith("test-")


def test_chinese_providers_read_provider_specific_api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOONSHOT_API_KEY", "env-kimi")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "env-qwen")
    monkeypatch.setenv("ZHIPU_API_KEY", "env-glm")
    monkeypatch.setenv("MINIMAX_API_KEY", "env-minimax")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-deepseek")

    assert ChatKimi(model="kimi-k2").api_key == "env-kimi"
    assert ChatQwen(model="qwen-max-latest").api_key == "env-qwen"
    assert ChatGLM(model="glm-4-flash").api_key == "env-glm"
    assert ChatMiniMax(model="MiniMax-M2").api_key == "env-minimax"
    assert ChatDeepSeek(model="deepseek-chat").api_key == "env-deepseek"


def test_chinese_provider_missing_api_key_error_names_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_name in ["MOONSHOT_API_KEY", "DASHSCOPE_API_KEY", "ZHIPU_API_KEY", "MINIMAX_API_KEY", "DEEPSEEK_API_KEY"]:
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    providers = [
        (ChatKimi(model="kimi-k2"), "MOONSHOT_API_KEY"),
        (ChatQwen(model="qwen-max-latest"), "DASHSCOPE_API_KEY"),
        (ChatGLM(model="glm-4-flash"), "ZHIPU_API_KEY"),
        (ChatMiniMax(model="MiniMax-M2"), "MINIMAX_API_KEY"),
        (ChatDeepSeek(model="deepseek-chat"), "DEEPSEEK_API_KEY"),
    ]

    async def invoke(provider: BaseChatModel) -> None:
        await provider.ainvoke([{"role": "user", "content": "ping"}])

    for provider, env_name in providers:
        with pytest.raises(RuntimeError, match=env_name):
            asyncio.run(invoke(provider))


def test_kimi_routes_moonshot_endpoints_and_preserves_request_parameters() -> None:
    captures = install_fake_openai("kimi-ok")

    async def run() -> None:
        intl = ChatKimi(model="kimi-k2", api_key="moonshot-intl", endpoint="international", temperature=0.2)
        cn = ChatKimi(model="kimi-k2-thinking", api_key="moonshot-cn", endpoint="cn")
        assert await intl.ainvoke([{"role": "user", "content": "ping"}]) == "kimi-ok"
        assert await cn.ainvoke([{"role": "user", "content": "ping"}]) == "kimi-ok"

    asyncio.run(run())
    clients = [payload for kind, payload in captures if kind == "client"]
    requests = [payload for kind, payload in captures if kind == "request"]

    assert clients[0]["api_key"] == "moonshot-intl"
    assert str(clients[0]["base_url"]).rstrip("/") == "https://api.moonshot.ai/v1"
    assert clients[1]["api_key"] == "moonshot-cn"
    assert str(clients[1]["base_url"]).rstrip("/") == "https://api.moonshot.cn/v1"
    assert requests[0]["model"] == "kimi-k2"
    assert requests[0]["temperature"] == 0.2
    assert requests[0]["messages"][0]["content"] == "ping"
    assert requests[1]["model"] == "kimi-k2-thinking"


def test_qwen_uses_dashscope_regions_and_forwards_thinking_options() -> None:
    captures = install_fake_openai("qwen-ok")

    async def run() -> None:
        cn = ChatQwen(model="qwen-max-latest", api_key="dashscope-cn", region="cn")
        intl = ChatQwen(
            model="qwq-32b",
            api_key="dashscope-intl",
            region="international",
            thinking=True,
            thinking_budget=1024,
        )
        assert await cn.ainvoke([{"role": "user", "content": "hello"}]) == "qwen-ok"
        assert await intl.ainvoke([{"role": "user", "content": "reason"}]) == "qwen-ok"

    asyncio.run(run())
    clients = [payload for kind, payload in captures if kind == "client"]
    requests = [payload for kind, payload in captures if kind == "request"]

    assert str(clients[0]["base_url"]).rstrip("/") == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert str(clients[1]["base_url"]).rstrip("/") == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    assert requests[0]["model"] == "qwen-max-latest"
    assert requests[1]["model"] == "qwq-32b"
    assert requests[1]["extra_body"]["enable_thinking"] is True
    assert requests[1]["extra_body"]["thinking_budget"] == 1024


def test_glm_supports_bigmodel_free_tier_endpoint() -> None:
    captures = install_fake_openai("glm-ok")

    async def run() -> None:
        glm = ChatGLM(model="glm-4-flash", api_key="zhipu-test", temperature=0.1)
        assert await glm.ainvoke([{"role": "user", "content": "ping"}], max_tokens=32) == "glm-ok"

    asyncio.run(run())
    client = [payload for kind, payload in captures if kind == "client"][0]
    request = [payload for kind, payload in captures if kind == "request"][0]

    assert client["api_key"] == "zhipu-test"
    assert str(client["base_url"]).rstrip("/") == "https://open.bigmodel.cn/api/paas/v4"
    assert request["model"] == "glm-4-flash"
    assert request["temperature"] == 0.1
    assert request["max_tokens"] == 32


def test_minimax_accepts_reasoning_model_ids_and_forwards_reasoning_options() -> None:
    captures = install_fake_openai("minimax-ok")

    async def run() -> None:
        m1 = ChatMiniMax(model="MiniMax-M1", api_key="minimax-test")
        m2 = ChatMiniMax(model="MiniMax-M2", api_key="minimax-test", reasoning=True, endpoint="international")
        assert await m1.ainvoke([{"role": "user", "content": "m1"}]) == "minimax-ok"
        assert await m2.ainvoke([{"role": "user", "content": "m2"}]) == "minimax-ok"

    asyncio.run(run())
    clients = [payload for kind, payload in captures if kind == "client"]
    requests = [payload for kind, payload in captures if kind == "request"]

    assert str(clients[0]["base_url"]).rstrip("/") == "https://api.minimax.io/v1"
    assert str(clients[1]["base_url"]).rstrip("/") == "https://api.minimax.io/v1"
    assert requests[0]["model"] == "MiniMax-M1"
    assert requests[1]["model"] == "MiniMax-M2"
    assert requests[1]["extra_body"]["reasoning_split"] is True


def test_chinese_providers_work_with_structured_output_wrapper() -> None:
    class Probe(BaseModel):
        answer: str
        score: int

    install_fake_openai(lambda request: json.dumps({"answer": request["model"], "score": 9}))

    async def run() -> list[str]:
        providers = [
            ChatKimi(model="kimi-k2", api_key="k"),
            ChatQwen(model="qwen-max-latest", api_key="q"),
            ChatGLM(model="glm-4-flash", api_key="g"),
            ChatMiniMax(model="MiniMax-M2", api_key="m"),
        ]
        results: list[str] = []
        for provider in providers:
            parsed = await provider.with_structured_output(Probe).ainvoke(
                [{"role": "user", "content": "return json"}]
            )
            assert parsed.score == 9
            results.append(parsed.answer)
        return results

    assert asyncio.run(run()) == ["kimi-k2", "qwen-max-latest", "glm-4-flash", "MiniMax-M2"]
