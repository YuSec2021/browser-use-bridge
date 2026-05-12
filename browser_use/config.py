from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BrowserViewport(BaseModel):
    model_config = ConfigDict(extra="allow")

    width: int = 1280
    height: int = 720


class BrowserProfile(BaseModel):
    model_config = ConfigDict(extra="allow")

    headless: bool = True
    viewport: BrowserViewport = Field(default_factory=BrowserViewport)
    user_data_dir: str | None = None
    proxy: str | dict[str, Any] | None = None
    allowed_domains: list[str] = Field(default_factory=list)
    cdp_port: int | None = None


class BrowserUseConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    browser: BrowserProfile = Field(default_factory=BrowserProfile)


def load_config(path: str | os.PathLike[str] | None = None) -> BrowserUseConfig:
    _load_dotenv()
    config_path = Path(path or os.getenv("BROWSER_USE_CONFIG", "")).expanduser()
    raw: dict[str, Any] = {}

    if str(config_path) not in {"", "."} and config_path.exists():
        with config_path.open("r", encoding="utf-8") as config_file:
            raw = json.load(config_file)

    _apply_env_overrides(raw)
    return BrowserUseConfig.model_validate(raw)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv as dotenv_load
    except ImportError:
        return
    dotenv_load()


def _apply_env_overrides(raw: dict[str, Any]) -> None:
    browser = raw.setdefault("browser", {})
    if "BROWSER_USE_BROWSER_HEADLESS" in os.environ:
        browser["headless"] = _parse_bool(os.environ["BROWSER_USE_BROWSER_HEADLESS"])

    viewport = browser.setdefault("viewport", {})
    if "BROWSER_USE_BROWSER_VIEWPORT_WIDTH" in os.environ:
        viewport["width"] = int(os.environ["BROWSER_USE_BROWSER_VIEWPORT_WIDTH"])
    if "BROWSER_USE_BROWSER_VIEWPORT_HEIGHT" in os.environ:
        viewport["height"] = int(os.environ["BROWSER_USE_BROWSER_VIEWPORT_HEIGHT"])


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


__all__ = ["BrowserViewport", "BrowserProfile", "BrowserUseConfig", "load_config"]
