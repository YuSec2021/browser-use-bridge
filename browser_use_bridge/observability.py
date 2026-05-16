from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ObservabilityEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = ""
    timestamp: str = ""


ObservabilityHook = Callable[[ObservabilityEvent], Any]


class ObservabilityHub:
    """Fan out normalized trace events to local logs and optional provider hooks."""

    def __init__(
        self,
        trace_id: str | None = None,
        log_file: str | os.PathLike[str] | None = None,
        log_json: bool = False,
    ) -> None:
        self.trace_id = trace_id or os.getenv("BROWSER_USE_TRACE_ID") or str(uuid.uuid4())
        self.log_file = Path(log_file) if log_file else None
        self.log_json = log_json
        self._langsmith_hooks: list[ObservabilityHook] = []
        self._langfuse_hooks: list[ObservabilityHook] = []

    def add_langsmith_hook(self, hook: ObservabilityHook) -> None:
        self._langsmith_hooks.append(hook)

    def add_langfuse_hook(self, hook: ObservabilityHook) -> None:
        self._langfuse_hooks.append(hook)

    def emit(self, event: ObservabilityEvent) -> ObservabilityEvent:
        normalized = event.model_copy(
            update={
                "trace_id": event.trace_id or self.trace_id,
                "timestamp": event.timestamp or datetime.now(timezone.utc).isoformat(),
            }
        )
        if self.log_json and self.log_file is not None:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(normalized.model_dump(), ensure_ascii=False) + "\n")

        for hook in [*self._langsmith_hooks, *self._langfuse_hooks]:
            hook(normalized)
        return normalized


__all__ = ["ObservabilityEvent", "ObservabilityHub", "ObservabilityHook"]
