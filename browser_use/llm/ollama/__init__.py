from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, Field

from browser_use.llm.base import BaseChatModel, StructuredModelT, _parse_structured_response


DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"


class OllamaConnectionError(Exception):
    """Raised when the local Ollama server cannot be reached."""


class OllamaModelNotFoundError(Exception):
    """Raised when a requested model is not available from Ollama."""


class OllamaResponseError(Exception):
    """Raised when Ollama returns an invalid or failed generation response."""


class ChatOllamaConfig(BaseModel):
    """Configuration for the local Ollama chat adapter."""

    base_url: str = DEFAULT_OLLAMA_BASE_URL
    model_name: str = "llama3"
    timeout: float = 120
    keep_alive: str | int | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)


@dataclass
class OllamaStatus:
    """Connection and model inventory returned by Ollama health checks."""

    connected: bool
    base_url: str
    available_models: list[str] = field(default_factory=list)
    error: str | None = None


class OllamaHealthChecker:
    """Probe a local Ollama server and expose available model names."""

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        timeout: float = 5,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.extra_headers = dict(extra_headers or {})
        self.available_models: list[str] = []
        self.status = OllamaStatus(connected=False, base_url=self.base_url)

    async def check(self) -> OllamaStatus:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self.extra_headers) as client:
                response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            payload = response.json()
            models = [model.get("name", "") for model in payload.get("models", []) if model.get("name")]
            self.available_models = models
            self.status = OllamaStatus(connected=True, base_url=self.base_url, available_models=models)
            return self.status
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            self.available_models = []
            self.status = OllamaStatus(
                connected=False,
                base_url=self.base_url,
                available_models=[],
                error=str(exc) or type(exc).__name__,
            )
            return self.status

    async def ensure_connected(self) -> OllamaStatus:
        status = await self.check()
        if not status.connected:
            raise OllamaConnectionError(
                f"Unable to connect to Ollama at {self.base_url}: {status.error or 'connection failed'}"
            )
        return status

    async def is_model_available(self, model_name: str) -> bool:
        status = await self.ensure_connected()
        return _model_name_matches(model_name, status.available_models)

    async def ensure_model_available(self, model_name: str) -> None:
        status = await self.ensure_connected()
        if not _model_name_matches(model_name, status.available_models):
            available = ", ".join(status.available_models) or "none"
            raise OllamaModelNotFoundError(f"Ollama model {model_name!r} is not available. Available models: {available}.")


class ChatOllama(BaseChatModel):
    """Ollama `/api/chat` adapter for local text and vision models."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        temperature: float | None = None,
        config: ChatOllamaConfig | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        keep_alive: str | int | None = None,
        extra_headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        env_base_url = os.getenv("BROWSER_USE_OLLAMA_BASE_URL")
        resolved_model = model or (config.model_name if config else "llama3")
        self.config = config or ChatOllamaConfig(
            base_url=base_url or env_base_url or DEFAULT_OLLAMA_BASE_URL,
            model_name=resolved_model,
            timeout=120 if timeout is None else timeout,
            keep_alive=keep_alive,
            extra_headers=dict(extra_headers or {}),
        )
        if config is not None:
            update: dict[str, Any] = {"model_name": resolved_model}
            if base_url is not None:
                update["base_url"] = base_url
            elif env_base_url is not None:
                update["base_url"] = env_base_url
            if timeout is not None:
                update["timeout"] = timeout
            if keep_alive is not None:
                update["keep_alive"] = keep_alive
            if extra_headers is not None:
                update["extra_headers"] = dict(extra_headers)
            self.config = config.model_copy(update=update)

        super().__init__(model=self.config.model_name, api_key=api_key, temperature=temperature, **kwargs)
        self.base_url = str(self.config.base_url).rstrip("/")

    def set_model(self, new_model_name: str) -> None:
        self.model = new_model_name
        self.config = self.config.model_copy(update={"model_name": new_model_name})

    async def supports_vision(self) -> bool:
        if _looks_like_vision_model(self.model):
            return True

        checker = OllamaHealthChecker(
            base_url=self.base_url,
            timeout=min(float(self.config.timeout), 5.0),
            extra_headers=self.config.extra_headers,
        )
        status = await checker.check()
        return status.connected and _model_name_matches(self.model, _vision_model_names(status.available_models))

    async def ainvoke(self, messages: Sequence[dict[str, Any]] | Sequence[Any], **kwargs: Any) -> Any:
        request = self._request_payload(messages, stream=False, **kwargs)
        try:
            async with httpx.AsyncClient(
                timeout=self.config.timeout,
                headers=self.config.extra_headers,
            ) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=request)
        except httpx.HTTPError as exc:
            raise OllamaConnectionError(f"Unable to connect to Ollama at {self.base_url}: {exc}") from exc

        payload = self._decode_response(response)
        message = payload.get("message")
        if isinstance(message, dict):
            return message.get("content", "")
        if "response" in payload:
            return payload["response"]
        raise OllamaResponseError(f"Ollama response did not contain assistant content: {payload!r}")

    async def astream(
        self,
        messages: Sequence[dict[str, Any]] | Sequence[Any],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        request = self._request_payload(messages, stream=True, **kwargs)
        try:
            async with httpx.AsyncClient(
                timeout=self.config.timeout,
                headers=self.config.extra_headers,
            ) as client:
                async with client.stream("POST", f"{self.base_url}/api/chat", json=request) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise OllamaResponseError(_error_message(response.status_code, body))
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise OllamaResponseError(f"Invalid Ollama stream chunk: {line!r}") from exc
                        if payload.get("error"):
                            raise OllamaResponseError(str(payload["error"]))
                        message = payload.get("message")
                        if isinstance(message, dict):
                            token = message.get("content")
                            if token:
                                yield token
                        elif payload.get("response"):
                            yield str(payload["response"])
                        if payload.get("done"):
                            break
        except httpx.HTTPError as exc:
            raise OllamaConnectionError(f"Unable to connect to Ollama at {self.base_url}: {exc}") from exc

    def with_structured_output(
        self,
        schema: type[StructuredModelT],
        **kwargs: Any,
    ) -> "_OllamaStructuredOutputChatModel[StructuredModelT]":
        return _OllamaStructuredOutputChatModel(self, schema, **kwargs)

    def _request_payload(
        self,
        messages: Sequence[dict[str, Any]] | Sequence[Any],
        *,
        stream: bool,
        **kwargs: Any,
    ) -> dict[str, Any]:
        options = dict(kwargs.pop("options", {}) or {})
        if self.temperature is not None and "temperature" not in options:
            options["temperature"] = self.temperature

        request: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_ollama_message(message) for message in messages],
            "stream": stream,
            **self.model_kwargs,
            **kwargs,
        }
        if options:
            request["options"] = options
        if self.config.keep_alive is not None:
            request["keep_alive"] = self.config.keep_alive
        if self.tools:
            request["tools"] = list(self.tools)
        return request

    @staticmethod
    def _decode_response(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            raise OllamaResponseError(_error_message(response.status_code, response.content))
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise OllamaResponseError(f"Invalid Ollama response JSON: {response.text}") from exc
        if payload.get("error"):
            raise OllamaResponseError(str(payload["error"]))
        return payload


class _OllamaStructuredOutputChatModel(BaseChatModel):
    def __init__(
        self,
        base_model: ChatOllama,
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
        request_messages = _inject_json_instruction(messages, self.schema)
        raw_response = await self.base_model.ainvoke(request_messages, format="json", **kwargs)
        return _parse_structured_response(raw_response, self.schema)

    async def astream(
        self,
        messages: Sequence[dict[str, Any]] | Sequence[Any],
        **kwargs: Any,
    ) -> AsyncIterator[StructuredModelT]:
        chunks: list[str] = []
        request_messages = _inject_json_instruction(messages, self.schema)
        async for chunk in self.base_model.astream(request_messages, format="json", **kwargs):
            chunks.append(str(chunk))
        yield _parse_structured_response("".join(chunks), self.schema)

    def bind_tools(self, tools: Sequence[Any]) -> "_OllamaStructuredOutputChatModel":
        self.base_model.bind_tools(tools)
        self.tools = tools
        return self


def _to_ollama_message(message: Any) -> dict[str, Any]:
    if not isinstance(message, dict):
        return {"role": "user", "content": str(message)}

    role = str(message.get("role", "user"))
    content = message.get("content", "")
    if isinstance(content, list):
        text_parts: list[str] = []
        images: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                text_parts.append(str(part))
                continue
            if part.get("type") == "text":
                text_parts.append(str(part.get("text", "")))
                continue
            if part.get("type") == "image_url":
                image_url = part.get("image_url")
                url = image_url.get("url") if isinstance(image_url, dict) else image_url
                if isinstance(url, str):
                    images.append(_strip_data_url(url))
                continue
            if "image" in part:
                images.append(_strip_data_url(str(part["image"])))
        converted = {"role": role, "content": "\n".join(text for text in text_parts if text)}
        if images:
            converted["images"] = images
        return converted

    converted = dict(message)
    converted["role"] = role
    converted["content"] = "" if content is None else str(content)
    return converted


def _inject_json_instruction(
    messages: Sequence[dict[str, Any]] | Sequence[Any],
    schema: type[BaseModel],
) -> list[dict[str, Any]]:
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    instruction = (
        "Respond only with valid JSON matching this Pydantic schema. "
        f"Do not include markdown or explanatory text. Schema: {schema_json}"
    )
    converted = [_to_ollama_message(message) for message in messages]
    converted.append({"role": "system", "content": instruction})
    return converted


def _strip_data_url(value: str) -> str:
    if value.startswith("data:") and "," in value:
        return value.split(",", 1)[1]
    return value


def _error_message(status_code: int, body: bytes) -> str:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return f"Ollama request failed with status {status_code}: {body.decode('utf-8', errors='replace')}"
    if isinstance(payload, dict) and payload.get("error"):
        return str(payload["error"])
    return f"Ollama request failed with status {status_code}: {payload!r}"


def _model_name_matches(requested: str, available_models: Sequence[str]) -> bool:
    requested_base = requested.split(":", 1)[0]
    for model in available_models:
        if requested == model or requested_base == model.split(":", 1)[0]:
            return True
    return False


def _looks_like_vision_model(model_name: str) -> bool:
    lowered = model_name.lower()
    return any(marker in lowered for marker in ["llava", "bakllava", "moondream", "minicpm-v", "granite3.2-vision"])


def _vision_model_names(model_names: Sequence[str]) -> list[str]:
    return [model_name for model_name in model_names if _looks_like_vision_model(model_name)]


__all__ = [
    "ChatOllama",
    "ChatOllamaConfig",
    "OllamaConnectionError",
    "OllamaHealthChecker",
    "OllamaModelNotFoundError",
    "OllamaResponseError",
    "OllamaStatus",
]
