from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from browser_use_bridge.agent.views import AgentHistoryList
from browser_use_bridge.browser.events import DomUpdatedEvent, TabSwitchedEvent
from browser_use_bridge.browser.views import BrowserStateSummary


def _default_checkpoint_id() -> str:
    return f"cp-{uuid.uuid4().hex}"


def _default_timestamp() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class Checkpoint(BaseModel):
    """Serializable execution snapshot for interrupt/resume workflows."""

    model_config = ConfigDict(extra="allow")

    task_id: str
    checkpoint_id: str = Field(default_factory=_default_checkpoint_id)
    step_counter: int = 0
    current_url: str = ""
    dom_state_snapshot: dict[str, Any] = Field(default_factory=dict)
    agent_history: dict[str, Any] = Field(default_factory=dict)
    pending_actions_queue: list[dict[str, Any]] = Field(default_factory=list)
    timestamp: str = Field(default_factory=_default_timestamp)
    label: str = ""


class CheckpointManager:
    """Stores checkpoints as one JSON file per checkpoint, isolated by task id."""

    def __init__(self, storage_dir: str | Path | None = None, autosave_every_steps: int | None = None) -> None:
        self.storage_dir = Path(
            storage_dir
            or os.getenv("BROWSER_USE_CHECKPOINT_DIR")
            or Path.home() / ".browser-use-bridge" / "checkpoints"
        ).expanduser()
        self.autosave_every_steps = autosave_every_steps

    def save(self, checkpoint: Checkpoint | None = None, **checkpoint_fields: Any) -> Checkpoint:
        if checkpoint is not None and checkpoint_fields:
            raise TypeError("Pass either a Checkpoint or checkpoint fields, not both.")

        saved = checkpoint if checkpoint is not None else Checkpoint(**checkpoint_fields)
        path = self._checkpoint_path(saved.task_id, saved.checkpoint_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._to_json(saved), encoding="utf-8")
        return saved

    def load(self, checkpoint_id: str, task_id: str | None = None) -> Checkpoint:
        path = self._find_checkpoint_path(checkpoint_id, task_id=task_id)
        return Checkpoint.model_validate_json(path.read_text(encoding="utf-8"))

    def list_checkpoints(self, task_id: str | None = None) -> list[Checkpoint]:
        roots = [self.storage_dir / task_id] if task_id else sorted(path for path in self.storage_dir.glob("*") if path.is_dir())
        checkpoints: list[Checkpoint] = []
        for root in roots:
            if not root.exists():
                continue
            for path in sorted(root.glob("*.json")):
                checkpoints.append(Checkpoint.model_validate_json(path.read_text(encoding="utf-8")))
        return sorted(checkpoints, key=lambda checkpoint: (checkpoint.timestamp, checkpoint.checkpoint_id))

    def delete(self, checkpoint_id: str, task_id: str | None = None) -> bool:
        try:
            path = self._find_checkpoint_path(checkpoint_id, task_id=task_id)
        except FileNotFoundError:
            return False
        path.unlink()
        return True

    def auto_save_periodic(self, **checkpoint_fields: Any) -> Checkpoint | None:
        if not self.autosave_every_steps:
            return None
        step_counter = int(checkpoint_fields.get("step_counter", 0))
        if step_counter <= 0 or step_counter % self.autosave_every_steps != 0:
            return None
        checkpoint_fields.setdefault("label", "auto-periodic")
        return self.save(**checkpoint_fields)

    def enable_event_autosave(self, event_bus: Any, state_provider: Callable[[], dict[str, Any] | Checkpoint]) -> None:
        def _handle_event(event: Any) -> None:
            if not isinstance(event, (DomUpdatedEvent, TabSwitchedEvent)):
                return
            state = state_provider()
            if isinstance(state, Checkpoint):
                checkpoint = state.model_copy(update={"label": state.label or "auto-event"})
                self.save(checkpoint)
                return
            payload = dict(state)
            payload.setdefault("label", "auto-event")
            self.save(**payload)

        event_bus.subscribe(_handle_event)

    def _checkpoint_path(self, task_id: str, checkpoint_id: str) -> Path:
        self._validate_path_part(task_id, "task_id")
        self._validate_path_part(checkpoint_id, "checkpoint_id")
        return self.storage_dir / task_id / f"{checkpoint_id}.json"

    def _find_checkpoint_path(self, checkpoint_id: str, task_id: str | None = None) -> Path:
        self._validate_path_part(checkpoint_id, "checkpoint_id")
        if task_id is not None:
            path = self._checkpoint_path(task_id, checkpoint_id)
            if path.exists():
                return path
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint_id}")

        matches = sorted(self.storage_dir.glob(f"*/{checkpoint_id}.json"))
        if not matches:
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint_id}")
        return matches[0]

    @staticmethod
    def _validate_path_part(value: str, name: str) -> None:
        if not value or "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError(f"{name} must be a non-empty path-safe identifier")

    @staticmethod
    def _to_json(checkpoint: Checkpoint) -> str:
        payload = checkpoint.model_dump(mode="json")
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def resume_from_checkpoint(
    checkpoint: Checkpoint | str,
    *,
    manager: CheckpointManager | None = None,
    task_id: str | None = None,
    agent_factory: Callable[..., Any] | None = None,
    **agent_kwargs: Any,
) -> Any:
    loaded = checkpoint
    if isinstance(loaded, str):
        loaded = (manager or CheckpointManager()).load(loaded, task_id=task_id)

    factory = agent_factory
    if factory is None:
        from browser_use_bridge.agent import Agent

        factory = Agent

    agent = factory(task=loaded.task_id, **agent_kwargs)
    agent.step_counter = loaded.step_counter
    agent.current_url = loaded.current_url
    agent.pending_actions_queue = list(loaded.pending_actions_queue)
    agent.history = AgentHistoryList.model_validate(loaded.agent_history or {"histories": []})
    agent.dom_state = BrowserStateSummary.model_validate(
        {"url": loaded.current_url, **loaded.dom_state_snapshot}
    )
    agent.checkpoint = loaded
    return agent


__all__ = ["Checkpoint", "CheckpointManager", "resume_from_checkpoint"]
