from __future__ import annotations

from typing import Any

from browser_use_bridge.llm._openai_compatible import OpenAICompatibleChatModel


class ChatGLM(OpenAICompatibleChatModel):
    """Zhipu ChatGLM adapter using the BigModel OpenAI-compatible endpoint."""

    api_key_env_var = "ZHIPU_API_KEY"

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        temperature: float | None = None,
        base_url: str = "https://open.bigmodel.cn/api/paas/v4",
        thinking: bool = False,
        **kwargs: Any,
    ) -> None:
        self.thinking = thinking
        super().__init__(model=model, api_key=api_key, temperature=temperature, base_url=base_url, **kwargs)

    def _extra_body(self) -> dict[str, Any]:
        return {"thinking": self.thinking}


__all__ = ["ChatGLM"]
