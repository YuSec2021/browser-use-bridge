from __future__ import annotations

from typing import Any

from browser_use_bridge.llm._openai_compatible import OpenAICompatibleChatModel


class ChatQwen(OpenAICompatibleChatModel):
    """Alibaba DashScope Qwen adapter using OpenAI-compatible regional endpoints."""

    api_key_env_var = "DASHSCOPE_API_KEY"

    _REGIONS = {
        "cn": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "china": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "international": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "intl": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "global": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    }

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        temperature: float | None = None,
        region: str = "cn",
        thinking: bool = False,
        thinking_budget: int | None = None,
        **kwargs: Any,
    ) -> None:
        self.region = region
        self.thinking = thinking
        self.thinking_budget = thinking_budget
        super().__init__(
            model=model,
            api_key=api_key,
            temperature=temperature,
            base_url=self._resolve_region(region),
            **kwargs,
        )

    @classmethod
    def _resolve_region(cls, region: str) -> str:
        if region.startswith("http://") or region.startswith("https://"):
            return region
        try:
            return cls._REGIONS[region]
        except KeyError as exc:
            supported = ", ".join(sorted(cls._REGIONS))
            raise ValueError(f"Unsupported Qwen region {region!r}. Expected one of: {supported}.") from exc

    def _extra_body(self) -> dict[str, Any]:
        extra_body: dict[str, Any] = {"enable_thinking": self.thinking}
        if self.thinking_budget is not None:
            extra_body["thinking_budget"] = self.thinking_budget
        return extra_body


__all__ = ["ChatQwen"]
