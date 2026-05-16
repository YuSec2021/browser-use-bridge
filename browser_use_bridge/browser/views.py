from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BrowserStateSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    url: str = ""
    title: str = ""
    elements: list[Any] = Field(default_factory=list)
