from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from browser_use.llm.base import BaseChatModel, StructuredModelT, _parse_structured_response


PROVIDERS_CONFIG_ENV = "BROWSER_USE_PROVIDERS_CONFIG"


class ProviderCapabilities(BaseModel):
    """Capability matrix for an OpenAI-compatible provider."""

    structured_output: str = "json_schema"
    vision: bool = False
    thinking: bool = False


class ChatCustomConfig(BaseModel):
    """Configuration for custom OpenAI-compatible chat providers."""

    base_url: str
    api_key: str | None = None
    model_name: str
    extra_headers: dict[str, str] = Field(default_factory=dict)
    name: str | None = None
    capabilities: ProviderCapabilities = Field(default_factory=ProviderCapabilities)
    timeout: float = 120


class ChatCustomResponseError(Exception):
    """Raised when a custom provider returns an invalid or failed response."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ChatCustom(BaseChatModel):
    """OpenAI-compatible chat adapter for user-declared provider endpoints."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        model: str | None = None,
        model_name: str | None = None,
        extra_headers: dict[str, str] | None = None,
        config: ChatCustomConfig | None = None,
        provider_name: str | None = None,
        capabilities: ProviderCapabilities | dict[str, Any] | None = None,
        timeout: float | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> None:
        resolved_model = model or model_name or (config.model_name if config else None)
        if resolved_model is None:
            raise ValueError("ChatCustom requires a model or model_name.")

        resolved_capabilities = _coerce_capabilities(capabilities) if capabilities is not None else None
        self.config = config or ChatCustomConfig(
            base_url=base_url,
            api_key=api_key,
            model_name=resolved_model,
            extra_headers=dict(extra_headers or {}),
            capabilities=resolved_capabilities or ProviderCapabilities(),
            timeout=120 if timeout is None else timeout,
        )
        if config is not None:
            update: dict[str, Any] = {"base_url": base_url, "model_name": resolved_model}
            if api_key is not None:
                update["api_key"] = api_key
            if extra_headers is not None:
                update["extra_headers"] = dict(extra_headers)
            if resolved_capabilities is not None:
                update["capabilities"] = resolved_capabilities
            if timeout is not None:
                update["timeout"] = timeout
            self.config = config.model_copy(update=update)

        super().__init__(
            model=self.config.model_name,
            api_key=self.config.api_key,
            temperature=temperature,
            **kwargs,
        )
        self.base_url = self.config.base_url.rstrip("/")
        self.provider_name = provider_name or self.config.name or detect_provider_from_base_url(self.base_url)
        self.capabilities = self.config.capabilities

    async def ainvoke(self, messages: Sequence[dict[str, Any]] | Sequence[Any], **kwargs: Any) -> Any:
        payload = self._request_payload(messages, stream=False, **kwargs)
        async with httpx.AsyncClient(timeout=self.config.timeout, headers=self._headers()) as client:
            response = await client.post(self._chat_completions_url(), json=payload)
        response_payload = self._decode_response(response)
        return _assistant_content(response_payload)

    async def astream(
        self,
        messages: Sequence[dict[str, Any]] | Sequence[Any],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        payload = self._request_payload(messages, stream=True, **kwargs)
        async with httpx.AsyncClient(timeout=self.config.timeout, headers=self._headers()) as client:
            async with client.stream("POST", self._chat_completions_url(), json=payload) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise ChatCustomResponseError(_error_message(response.status_code, body), response.status_code)
                async for line in response.aiter_lines():
                    token = _stream_token_from_line(line)
                    if token == "[DONE]":
                        break
                    if token:
                        yield token

    def with_structured_output(
        self,
        schema: type[StructuredModelT],
        **kwargs: Any,
    ) -> "_CustomStructuredOutputChatModel[StructuredModelT]":
        return _CustomStructuredOutputChatModel(self, schema, **kwargs)

    def _request_payload(
        self,
        messages: Sequence[dict[str, Any]] | Sequence[Any],
        *,
        stream: bool,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_openai_message(message) for message in messages],
            "stream": stream,
            **self.model_kwargs,
            **kwargs,
        }
        if not stream:
            payload.pop("stream", None)
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.tools:
            payload["tools"] = list(self.tools)
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.config.extra_headers)
        return headers

    def _chat_completions_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    @staticmethod
    def _decode_response(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            raise ChatCustomResponseError(_error_message(response.status_code, response.content), response.status_code)
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ChatCustomResponseError(f"Custom provider returned invalid JSON: {response.text}") from exc
        if isinstance(payload, dict) and payload.get("error"):
            raise ChatCustomResponseError(str(payload["error"]))
        if not isinstance(payload, dict):
            raise ChatCustomResponseError(f"Custom provider returned unexpected payload: {payload!r}")
        return payload


class _CustomStructuredOutputChatModel(BaseChatModel):
    def __init__(
        self,
        base_model: ChatCustom,
        schema: type[StructuredModelT],
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model=base_model.model,
            api_key=base_model.api_key,
            temperature=base_model.temperature,
            **base_model.model_kwargs,
        )
        self.base_model = base_model
        self.schema = schema
        self.structured_output_kwargs = kwargs

    async def ainvoke(
        self,
        messages: Sequence[dict[str, Any]] | Sequence[Any],
        **kwargs: Any,
    ) -> StructuredModelT:
        attempts = [
            (list(messages), {"response_format": _json_schema_format(self.schema)}),
            (_inject_json_instruction(messages, self.schema), {"response_format": {"type": "json_object"}}),
            (_inject_json_instruction(messages, self.schema), {}),
        ]
        last_error: ChatCustomResponseError | None = None
        for request_messages, format_kwargs in attempts:
            try:
                raw_response = await self.base_model.ainvoke(
                    request_messages,
                    **format_kwargs,
                    **self.structured_output_kwargs,
                    **kwargs,
                )
                return _parse_structured_response(raw_response, self.schema)
            except ChatCustomResponseError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def astream(
        self,
        messages: Sequence[dict[str, Any]] | Sequence[Any],
        **kwargs: Any,
    ) -> AsyncIterator[StructuredModelT]:
        chunks: list[str] = []
        request_messages = _inject_json_instruction(messages, self.schema)
        async for chunk in self.base_model.astream(
            request_messages,
            response_format={"type": "json_object"},
            **self.structured_output_kwargs,
            **kwargs,
        ):
            chunks.append(str(chunk))
        yield _parse_structured_response("".join(chunks), self.schema)

    def bind_tools(self, tools: Sequence[Any]) -> "_CustomStructuredOutputChatModel":
        self.base_model.bind_tools(tools)
        self.tools = tools
        return self


def detect_provider_from_base_url(base_url: str) -> str:
    lowered = base_url.lower()
    if "dashscope" in lowered or "aliyuncs.com/compatible-mode" in lowered:
        return "qwen"
    if "bigmodel.cn" in lowered or "zhipu" in lowered:
        return "glm"
    if "minimax" in lowered:
        return "minimax"
    if "deepseek" in lowered:
        return "deepseek"
    if "moonshot" in lowered:
        return "kimi"
    if "qianfan" in lowered or "baidubce" in lowered or "wenxin" in lowered:
        return "wenxin"
    return "custom"


def load_custom_provider_configs(path: str | None = None) -> list[ChatCustomConfig]:
    config_path = path or os.getenv(PROVIDERS_CONFIG_ENV)
    if not config_path:
        return []
    raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
    providers = raw.get("providers", []) if isinstance(raw, dict) else raw
    if not isinstance(providers, list):
        raise ValueError("providers config must contain a providers list.")
    return [_provider_config_from_raw(provider) for provider in providers]


def get_custom_provider_config(name: str) -> ChatCustomConfig | None:
    for provider in load_custom_provider_configs():
        if provider.name == name:
            return provider
    return None


def _provider_config_from_raw(raw: Any) -> ChatCustomConfig:
    if not isinstance(raw, dict):
        raise ValueError("Each custom provider config must be an object.")
    payload = dict(raw)
    if "model" in payload and "model_name" not in payload:
        payload["model_name"] = payload.pop("model")
    if "capabilities" in payload:
        payload["capabilities"] = _coerce_capabilities(payload["capabilities"])
    return ChatCustomConfig.model_validate(payload)


def _coerce_capabilities(value: ProviderCapabilities | dict[str, Any]) -> ProviderCapabilities:
    if isinstance(value, ProviderCapabilities):
        return value
    return ProviderCapabilities.model_validate(value)


def _to_openai_message(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return dict(message)
    return {"role": "user", "content": str(message)}


def _assistant_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not choices:
        raise ChatCustomResponseError(f"Custom provider response did not include choices: {payload!r}")
    first = choices[0]
    if not isinstance(first, dict):
        raise ChatCustomResponseError(f"Custom provider choice was not an object: {first!r}")
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if content is not None:
            return str(content)
    raise ChatCustomResponseError(f"Custom provider response did not include assistant content: {payload!r}")


def _stream_token_from_line(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    if stripped.startswith("data:"):
        stripped = stripped[5:].strip()
    if stripped == "[DONE]":
        return "[DONE]"
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ChatCustomResponseError(f"Invalid custom provider stream chunk: {line!r}") from exc
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    delta = first.get("delta")
    if isinstance(delta, dict) and delta.get("content") is not None:
        return str(delta["content"])
    message = first.get("message")
    if isinstance(message, dict) and message.get("content") is not None:
        return str(message["content"])
    return None


def _json_schema_format(schema: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__,
            "schema": schema.model_json_schema(),
            "strict": True,
        },
    }


def _inject_json_instruction(
    messages: Sequence[dict[str, Any]] | Sequence[Any],
    schema: type[BaseModel],
) -> list[dict[str, Any]]:
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    instruction = (
        "Respond only with valid JSON matching this schema. "
        f"Do not include markdown or explanatory text. Schema: {schema_json}"
    )
    converted = [_to_openai_message(message) for message in messages]
    converted.append({"role": "system", "content": instruction})
    return converted


def _error_message(status_code: int, body: bytes) -> str:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return f"Custom provider request failed with status {status_code}: {body.decode('utf-8', errors='replace')}"
    if isinstance(payload, dict) and payload.get("error"):
        return f"Custom provider request failed with status {status_code}: {payload['error']}"
    return f"Custom provider request failed with status {status_code}: {payload!r}"


__all__ = [
    "ChatCustom",
    "ChatCustomConfig",
    "ChatCustomResponseError",
    "ProviderCapabilities",
    "detect_provider_from_base_url",
    "get_custom_provider_config",
    "load_custom_provider_configs",
]
