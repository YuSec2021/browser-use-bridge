from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict


class HumanizeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    max_time: float = 1.5
    min_time: float = 0.3
    typing: bool = True
    type_cps_mean: float = 8.0
    type_cps_jitter: float = 0.4
    scrolling: bool = True
    scroll_steps: int = 8

    @classmethod
    def from_env(cls) -> "HumanizeConfig":
        return cls(
            enabled=_env_bool("HUMANIZE", False),
            max_time=_env_float("HUMANIZE_MAX_TIME", 1.5),
            min_time=_env_float("HUMANIZE_MIN_TIME", 0.3),
            typing=_env_bool("HUMANIZE_TYPING", True),
            type_cps_mean=_env_float("HUMANIZE_TYPE_CPS", 8.0),
            scrolling=_env_bool("HUMANIZE_SCROLLING", True),
            scroll_steps=_env_int("HUMANIZE_SCROLL_STEPS", 8),
        )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    return float(value)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    return int(value)
