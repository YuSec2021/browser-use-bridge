from __future__ import annotations

from typing import Any

from browser_use.llm._openai_compatible import OpenAICompatibleChatModel


class ChatGLM(OpenAICompatibleChatModel):
    """Zhipu ChatGLM adapter using the BigModel OpenAI-compatible endpoint."""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        temperature: float | None = None,
        base_url: str = "https://open.bigmodel.cn/api/paas/v4",
        **kwargs: Any,
    ) -> None:
        super().__init__(model=model, api_key=api_key, temperature=temperature, base_url=base_url, **kwargs)


__all__ = ["ChatGLM"]
