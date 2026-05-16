from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from .base import BaseChatModel


class ChatGoogle(BaseChatModel):
    """Google Gemini chat adapter with lazy SDK initialization."""

    async def ainvoke(self, messages: Sequence[dict[str, Any]] | Sequence[Any], **kwargs: Any) -> Any:
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("Install google-genai to invoke ChatGoogle.") from exc

        client = genai.Client(api_key=self.api_key)
        response = await client.aio.models.generate_content(
            model=self.model,
            contents=self._contents(messages),
            **self.model_kwargs,
            **kwargs,
        )
        return response.text

    async def astream(
        self,
        messages: Sequence[dict[str, Any]] | Sequence[Any],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("Install google-genai to stream ChatGoogle.") from exc

        client = genai.Client(api_key=self.api_key)
        stream = await client.aio.models.generate_content_stream(
            model=self.model,
            contents=self._contents(messages),
            **self.model_kwargs,
            **kwargs,
        )
        async for chunk in stream:
            if getattr(chunk, "text", None):
                yield chunk.text

    def _contents(self, messages: Sequence[dict[str, Any]] | Sequence[Any]) -> list[Any]:
        contents: list[Any] = []
        for message in messages:
            if isinstance(message, dict):
                contents.append(message.get("content", ""))
            else:
                contents.append(message)
        return contents
