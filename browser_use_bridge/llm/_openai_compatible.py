from __future__ import annotations

import os
from collections.abc import AsyncIterator, Sequence
from typing import Any, Generic

from browser_use_bridge.agent.views import AgentOutput, AgentOutputSchema

from .base import BaseChatModel, StructuredModelT, _parse_structured_response


class OpenAICompatibleChatModel(BaseChatModel):
    """Base adapter for providers exposing OpenAI-compatible chat completions."""

    base_url: str
    api_key_env_var: str | None = None

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        temperature: float | None = None,
        base_url: str | None = None,
        **kwargs: Any,
    ) -> None:
        if api_key is None and self.api_key_env_var is not None:
            api_key = os.getenv(self.api_key_env_var)
        super().__init__(model=model, api_key=api_key, temperature=temperature, **kwargs)
        if base_url is not None:
            self.base_url = base_url

    async def ainvoke(self, messages: Sequence[dict[str, Any]] | Sequence[Any], **kwargs: Any) -> Any:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(f"Install the openai package to invoke {type(self).__name__}.") from exc

        self._require_api_key()
        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        request = self._request_kwargs(messages, stream=False, **kwargs)
        response = await client.chat.completions.create(**request)
        return response.choices[0].message.content

    async def astream(
        self,
        messages: Sequence[dict[str, Any]] | Sequence[Any],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(f"Install the openai package to stream {type(self).__name__}.") from exc

        self._require_api_key()
        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        request = self._request_kwargs(messages, stream=True, **kwargs)
        async for chunk in await client.chat.completions.create(**request):
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    def _request_kwargs(
        self,
        messages: Sequence[dict[str, Any]] | Sequence[Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            **self.model_kwargs,
            **kwargs,
        }
        if self.temperature is not None:
            request["temperature"] = self.temperature
        if self.tools:
            request["tools"] = list(self.tools)

        extra_body = self._extra_body()
        if extra_body:
            request["extra_body"] = {**extra_body, **(request.get("extra_body") or {})}
        return request

    def _extra_body(self) -> dict[str, Any]:
        return {}

    def _require_api_key(self) -> None:
        if self.api_key:
            return
        hint = f" or set {self.api_key_env_var}" if self.api_key_env_var else ""
        raise RuntimeError(f"API key is required for {type(self).__name__}. Pass api_key=...{hint}.")

    def with_structured_output(
        self,
        schema: type[StructuredModelT],
        **kwargs: Any,
    ) -> "_OpenAICompatibleStructuredOutputChatModel[StructuredModelT]":
        return _OpenAICompatibleStructuredOutputChatModel(self, schema, **kwargs)


class _OpenAICompatibleStructuredOutputChatModel(BaseChatModel, Generic[StructuredModelT]):
    def __init__(
        self,
        base_model: OpenAICompatibleChatModel,
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
        request_kwargs = self._schema_request_kwargs(kwargs)
        try:
            raw_response = await self.base_model.ainvoke(messages, **request_kwargs)
        except Exception as exc:
            if _is_agent_output_schema(self.schema):
                raise RuntimeError(f"OpenAI-compatible structured output request failed: {exc}") from exc
            raise
        return _parse_structured_response(raw_response, self.schema)

    async def astream(
        self,
        messages: Sequence[dict[str, Any]] | Sequence[Any],
        **kwargs: Any,
    ) -> AsyncIterator[StructuredModelT]:
        request_kwargs = self._schema_request_kwargs(kwargs)
        chunks: list[str] = []
        try:
            async for chunk in self.base_model.astream(messages, **request_kwargs):
                chunks.append(str(chunk))
        except Exception as exc:
            if _is_agent_output_schema(self.schema):
                raise RuntimeError(f"OpenAI-compatible structured output request failed: {exc}") from exc
            raise
        yield _parse_structured_response("".join(chunks), self.schema)

    def bind_tools(self, tools: Sequence[Any]) -> "_OpenAICompatibleStructuredOutputChatModel[StructuredModelT]":
        self.base_model.bind_tools(tools)
        self.tools = tools
        return self

    def _schema_request_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        request_kwargs = {
            **self.structured_output_kwargs,
            **kwargs,
        }
        if _is_agent_output_schema(self.schema):
            request_kwargs.setdefault("response_format", _agent_output_response_format())
        return request_kwargs


def _is_agent_output_schema(schema: type[Any]) -> bool:
    return schema in {AgentOutput, AgentOutputSchema}


def _agent_output_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "AgentOutput",
            "schema": AgentOutputSchema.model_json_schema(),
        },
    }
