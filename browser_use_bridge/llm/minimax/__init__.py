from __future__ import annotations

from typing import Any

from browser_use_bridge.llm._openai_compatible import OpenAICompatibleChatModel


class ChatMiniMax(OpenAICompatibleChatModel):
    """MiniMax chat adapter for M1/M2 reasoning models."""

    api_key_env_var = "MINIMAX_API_KEY"

    _ENDPOINTS = {
        "international": "https://api.minimax.io/v1",
        "intl": "https://api.minimax.io/v1",
        "global": "https://api.minimax.io/v1",
        "cn": "https://api.minimax.io/v1",
        "china": "https://api.minimax.io/v1",
    }

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        temperature: float | None = None,
        endpoint: str = "international",
        thinking: bool = False,
        reasoning: bool | None = None,
        **kwargs: Any,
    ) -> None:
        self.endpoint = endpoint
        self.thinking = reasoning if reasoning is not None else thinking
        self.reasoning = self.thinking
        super().__init__(
            model=model,
            api_key=api_key,
            temperature=temperature,
            base_url=self._resolve_endpoint(endpoint),
            **kwargs,
        )

    @classmethod
    def _resolve_endpoint(cls, endpoint: str) -> str:
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        try:
            return cls._ENDPOINTS[endpoint]
        except KeyError as exc:
            supported = ", ".join(sorted(cls._ENDPOINTS))
            raise ValueError(f"Unsupported MiniMax endpoint {endpoint!r}. Expected one of: {supported}.") from exc

    def _extra_body(self) -> dict[str, Any]:
        return {"reasoning_split": self.thinking}


__all__ = ["ChatMiniMax"]
