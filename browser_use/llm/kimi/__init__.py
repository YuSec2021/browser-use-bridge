from __future__ import annotations

from typing import Any

from browser_use.llm._openai_compatible import OpenAICompatibleChatModel


class ChatKimi(OpenAICompatibleChatModel):
    """Moonshot Kimi chat adapter using OpenAI-compatible endpoints."""

    _ENDPOINTS = {
        "international": "https://api.moonshot.ai/v1",
        "intl": "https://api.moonshot.ai/v1",
        "global": "https://api.moonshot.ai/v1",
        "cn": "https://api.moonshot.cn/v1",
        "china": "https://api.moonshot.cn/v1",
    }

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        temperature: float | None = None,
        endpoint: str = "international",
        **kwargs: Any,
    ) -> None:
        self.endpoint = endpoint
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
            raise ValueError(f"Unsupported Kimi endpoint {endpoint!r}. Expected one of: {supported}.") from exc


__all__ = ["ChatKimi"]
