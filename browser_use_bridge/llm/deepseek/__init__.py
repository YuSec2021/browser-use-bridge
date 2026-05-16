from __future__ import annotations

from typing import Any

from browser_use_bridge.llm._openai_compatible import OpenAICompatibleChatModel


class ChatDeepSeek(OpenAICompatibleChatModel):
    """DeepSeek chat adapter using OpenAI-compatible endpoint."""

    api_key_env_var = "DEEPSEEK_API_KEY"

    _BASE_URL = "https://api.deepseek.com/v1"

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model=model,
            api_key=api_key,
            temperature=temperature,
            base_url=self._BASE_URL,
            **kwargs,
        )


__all__ = ["ChatDeepSeek"]
