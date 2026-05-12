from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from .base import BaseChatModel


class ChatAnthropic(BaseChatModel):
    """Anthropic chat adapter with lazy SDK initialization."""

    async def ainvoke(self, messages: Sequence[dict[str, Any]] | Sequence[Any], **kwargs: Any) -> Any:
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:
            raise RuntimeError("Install the anthropic package to invoke ChatAnthropic.") from exc

        client = AsyncAnthropic(api_key=self.api_key)
        request = self._request_kwargs(messages, stream=False, **kwargs)
        response = await client.messages.create(**request)
        return "".join(getattr(block, "text", "") for block in response.content)

    async def astream(
        self,
        messages: Sequence[dict[str, Any]] | Sequence[Any],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:
            raise RuntimeError("Install the anthropic package to stream ChatAnthropic.") from exc

        client = AsyncAnthropic(api_key=self.api_key)
        request = self._request_kwargs(messages, stream=True, **kwargs)
        async with client.messages.stream(**request) as stream:
            async for text in stream.text_stream:
                yield text

    def _request_kwargs(
        self,
        messages: Sequence[dict[str, Any]] | Sequence[Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        model_kwargs = dict(self.model_kwargs)
        request: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "max_tokens": kwargs.pop("max_tokens", model_kwargs.pop("max_tokens", 1024)),
            **model_kwargs,
            **kwargs,
        }
        if self.temperature is not None:
            request["temperature"] = self.temperature
        if self.tools:
            request["tools"] = list(self.tools)
        return request
