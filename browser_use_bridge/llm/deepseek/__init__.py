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
        thinking: bool = False,
        **kwargs: Any,
    ) -> None:
        self.thinking = thinking
        super().__init__(
            model=model,
            api_key=api_key,
            temperature=temperature,
            base_url=self._BASE_URL,
            **kwargs,
        )

    def _extra_body(self) -> dict[str, Any]:
        # DeepSeek reasoning is generally selected by model id; pass through the
        # normalized flag for compatible OpenAI-style gateways that expose it.
        return {"thinking": self.thinking}


__all__ = ["ChatDeepSeek"]
