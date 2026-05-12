from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from .base import BaseChatModel


class ChatOpenAI(BaseChatModel):
    """OpenAI chat adapter with lazy SDK initialization."""

    async def ainvoke(self, messages: Sequence[dict[str, Any]] | Sequence[Any], **kwargs: Any) -> Any:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError("Install the openai package to invoke ChatOpenAI.") from exc

        client = AsyncOpenAI(api_key=self.api_key)
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
            raise RuntimeError("Install the openai package to stream ChatOpenAI.") from exc

        client = AsyncOpenAI(api_key=self.api_key)
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
        return request
