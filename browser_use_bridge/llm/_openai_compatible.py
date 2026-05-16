from __future__ import annotations

import os
from collections.abc import AsyncIterator, Sequence
from typing import Any

from .base import BaseChatModel


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
