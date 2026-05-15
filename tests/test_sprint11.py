from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from browser_use.cli import _build_llm, _final_result_from_history, _provider_metadata
from browser_use.llm import BaseChatModel, ChatCustom
from browser_use.llm.custom import ChatCustomConfig, ProviderCapabilities, detect_provider_from_base_url


def test_chat_custom_public_api_and_detection_matrix() -> None:
    config = ChatCustomConfig(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="key-1",
        model_name="qwen-max-latest",
        extra_headers={"X-Test": "yes"},
    )
    llm = ChatCustom(
        base_url=config.base_url,
        api_key="key-2",
        model="qwen-plus",
        extra_headers={"X-Trace": "abc"},
    )

    assert isinstance(llm, BaseChatModel)
    assert llm.model == "qwen-plus"
    assert llm.api_key == "key-2"
    assert llm.config.extra_headers == {"X-Trace": "abc"}
    assert llm.provider_name == "qwen"
    assert isinstance(llm.capabilities, ProviderCapabilities)

    cases = {
        "https://dashscope.aliyuncs.com/compatible-mode/v1": "qwen",
        "https://open.bigmodel.cn/api/paas/v4": "glm",
        "https://api.minimax.chat/v1": "minimax",
        "https://api.deepseek.com/v1": "deepseek",
        "https://api.moonshot.cn/v1": "kimi",
        "https://qianfan.baidubce.com/v2": "wenxin",
        "https://llm.internal.example/v1": "custom",
    }
    for url, expected in cases.items():
        assert detect_provider_from_base_url(url) == expected


def test_chat_custom_request_and_structured_fallback(monkeypatch: Any) -> None:
    class Probe(BaseModel):
        answer: str
        score: int

    requests: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_: Any) -> bool:
            return False

        async def post(self, url: str, json: dict[str, Any]) -> httpx.Response:
            requests.append({"url": url, "body": json, "headers": self.kwargs["headers"]})
            if (json.get("response_format") or {}).get("type") == "json_schema":
                return httpx.Response(400, json={"error": {"message": "json_schema unsupported"}})
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"answer":"structured-model","score":11}'}}]},
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    async def run() -> Probe:
        llm = ChatCustom(
            base_url="http://custom.test/v1",
            api_key="secret-key",
            model="structured-model",
            extra_headers={"X-Custom-Gateway": "test-gw"},
        )
        return await llm.with_structured_output(Probe).ainvoke([{"role": "user", "content": "return a probe"}])

    parsed = asyncio.run(run())

    assert parsed.answer == "structured-model"
    assert parsed.score == 11
    assert requests[0]["url"] == "http://custom.test/v1/chat/completions"
    assert requests[0]["headers"]["Authorization"] == "Bearer secret-key"
    assert requests[0]["headers"]["X-Custom-Gateway"] == "test-gw"
    assert requests[0]["body"]["response_format"]["type"] == "json_schema"
    assert requests[1]["body"]["response_format"]["type"] == "json_object"
    serialized_messages = json.dumps(requests[1]["body"]["messages"]).lower()
    assert "json" in serialized_messages
    assert "answer" in serialized_messages
    assert "score" in serialized_messages


def test_custom_provider_config_metadata_and_cli_build(monkeypatch: Any, tmp_path: Path) -> None:
    config_path = tmp_path / "providers.json"
    config_path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "name": "internal-qwen",
                        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "api_key": "cfg-key",
                        "model": "qwen-max-latest",
                        "capabilities": {"vision": True, "thinking": True},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BROWSER_USE_PROVIDERS_CONFIG", str(config_path))

    providers = {provider["name"]: provider for provider in _provider_metadata()}
    assert providers["custom"]["provider_type"] == "custom"
    assert providers["internal-qwen"]["provider_type"] == "qwen"
    assert providers["internal-qwen"]["default_model"] == "qwen-max-latest"
    assert providers["internal-qwen"]["capabilities"]["vision"] is True
    assert providers["internal-qwen"]["capabilities"]["thinking"] is True

    llm = _build_llm("internal-qwen", None, None)
    assert isinstance(llm, ChatCustom)
    assert llm.model == "qwen-max-latest"
    assert llm.api_key == "cfg-key"


def test_done_action_text_is_cli_final_result() -> None:
    history = type(
        "History",
        (),
        {"model_output": type("Output", (), {"next_goal": "finish", "actions": [{"done": {"text": "done text"}}]})()},
    )()

    assert _final_result_from_history(history) == "done text"
