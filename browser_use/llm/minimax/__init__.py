from __future__ import annotations

from typing import Any

from browser_use.llm._openai_compatible import OpenAICompatibleChatModel


class ChatMiniMax(OpenAICompatibleChatModel):
    """MiniMax chat adapter for M1/M2 reasoning models."""

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
        reasoning: bool | None = None,
        **kwargs: Any,
    ) -> None:
        self.endpoint = endpoint
        self.reasoning = reasoning
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
        if self.reasoning is None:
            return {}
        return {"reasoning_split": self.reasoning}


__all__ = ["ChatMiniMax"]
