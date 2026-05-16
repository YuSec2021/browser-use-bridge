from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from typing import Any, Generic, TypeVar

from pydantic import BaseModel


StructuredModelT = TypeVar("StructuredModelT", bound=BaseModel)


class BaseChatModel(ABC):
    """Provider-neutral async chat model interface."""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.model_kwargs = kwargs
        self.tools: Sequence[Any] = ()

    @abstractmethod
    async def ainvoke(self, messages: Sequence[dict[str, Any]] | Sequence[Any], **kwargs: Any) -> Any:
        """Return a complete response for the supplied chat messages."""

    @abstractmethod
    async def astream(
        self,
        messages: Sequence[dict[str, Any]] | Sequence[Any],
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Yield response chunks for the supplied chat messages."""
        if False:
            yield None

    def with_structured_output(
        self,
        schema: type[StructuredModelT],
        **kwargs: Any,
    ) -> "StructuredOutputChatModel[StructuredModelT]":
        return StructuredOutputChatModel(self, schema, **kwargs)

    def bind_tools(self, tools: Sequence[Any]) -> "BaseChatModel":
        self.tools = tools
        return self


class StructuredOutputChatModel(BaseChatModel, Generic[StructuredModelT]):
    """Chat-model wrapper that validates provider output against a Pydantic schema."""

    def __init__(
        self,
        base_model: BaseChatModel,
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
        raw_response = await self.base_model.ainvoke(messages, **kwargs)
        return _parse_structured_response(raw_response, self.schema)

    async def astream(
        self,
        messages: Sequence[dict[str, Any]] | Sequence[Any],
        **kwargs: Any,
    ) -> AsyncIterator[StructuredModelT]:
        chunks: list[str] = []
        async for chunk in self.base_model.astream(messages, **kwargs):
            chunks.append(str(_extract_content(chunk)))
        yield _parse_structured_response("".join(chunks), self.schema)

    def bind_tools(self, tools: Sequence[Any]) -> "StructuredOutputChatModel[StructuredModelT]":
        self.base_model.bind_tools(tools)
        self.tools = tools
        return self


def _parse_structured_response(raw_response: Any, schema: type[StructuredModelT]) -> StructuredModelT:
    content = _extract_content(raw_response)
    if isinstance(content, schema):
        return content
    if isinstance(content, str):
        return schema.model_validate_json(content)
    if isinstance(content, bytes | bytearray):
        return schema.model_validate_json(bytes(content))
    if isinstance(content, dict):
        return schema.model_validate(content)
    return schema.model_validate(json.loads(str(content)))


def _extract_content(raw_response: Any) -> Any:
    if isinstance(raw_response, dict):
        if "content" in raw_response:
            return raw_response["content"]
        return raw_response

    content = getattr(raw_response, "content", None)
    if content is not None:
        return content

    choices = getattr(raw_response, "choices", None)
    if choices:
        first_choice = choices[0]
        message = getattr(first_choice, "message", None)
        if message is not None and getattr(message, "content", None) is not None:
            return message.content
        if isinstance(first_choice, dict):
            message = first_choice.get("message", {})
            if isinstance(message, dict) and "content" in message:
                return message["content"]

    return raw_response
