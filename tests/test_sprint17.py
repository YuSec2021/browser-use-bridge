from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from browser_use import Checkpoint, CheckpointManager
from browser_use.agent.views import AgentHistoryList
from browser_use.browser import DomUpdatedEvent, EventBus
from browser_use.browser.views import BrowserStateSummary
from browser_use.checkpoint import resume_from_checkpoint


def test_checkpoint_public_api_and_storage(tmp_path: Path) -> None:
    manager = CheckpointManager(storage_dir=tmp_path)
    checkpoint = manager.save(
        Checkpoint(
            task_id="task-one",
            checkpoint_id="cp-one",
            step_counter=3,
            current_url="https://example.test",
            dom_state_snapshot={"url": "https://example.test"},
            agent_history={"histories": []},
            pending_actions_queue=[{"done": {}}],
            label="manual",
        )
    )

    assert (tmp_path / "task-one" / "cp-one.json").exists()
    assert manager.load("cp-one", task_id="task-one") == checkpoint
    assert manager.list_checkpoints(task_id="task-one")[0].label == "manual"
    assert manager.delete("cp-one", task_id="task-one") is True
    assert manager.delete("cp-one", task_id="task-one") is False


def test_periodic_and_event_autosave(tmp_path: Path) -> None:
    manager = CheckpointManager(storage_dir=tmp_path, autosave_every_steps=2)

    saved_steps = [
        manager.auto_save_periodic(
            task_id="task-periodic",
            step_counter=step,
            current_url=f"https://example.test/{step}",
            dom_state_snapshot={"step": step},
            agent_history={"histories": []},
            pending_actions_queue=[],
        )
        for step in range(1, 5)
    ]

    assert [checkpoint.step_counter if checkpoint else None for checkpoint in saved_steps] == [None, 2, None, 4]
    assert [checkpoint.label for checkpoint in manager.list_checkpoints(task_id="task-periodic")] == [
        "auto-periodic",
        "auto-periodic",
    ]

    calls = 0

    def state_provider() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "task_id": "task-events",
            "step_counter": calls,
            "current_url": f"https://example.test/event-{calls}",
            "dom_state_snapshot": {"event": calls},
            "agent_history": {"histories": []},
            "pending_actions_queue": [],
        }

    event_manager = CheckpointManager(storage_dir=tmp_path)
    bus = EventBus()
    event_manager.enable_event_autosave(bus, state_provider)
    bus.emit(DomUpdatedEvent(session="fake", url="https://example.test/event-1", title="Event 1"))

    [event_checkpoint] = event_manager.list_checkpoints(task_id="task-events")
    assert event_checkpoint.label == "auto-event"
    assert event_checkpoint.step_counter == 1


def test_resume_from_checkpoint_rehydrates_agent(tmp_path: Path) -> None:
    class FakeAgent:
        def __init__(self, task: str, llm: str | None = None) -> None:
            self.task = task
            self.llm = llm

    checkpoint = CheckpointManager(storage_dir=tmp_path).save(
        task_id="task-resume",
        checkpoint_id="resume-me",
        step_counter=5,
        current_url="https://example.test/resume",
        dom_state_snapshot={"url": "https://example.test/resume", "title": "Resume"},
        agent_history={"histories": [{"model_output": {"next_goal": "continue"}}]},
        pending_actions_queue=[{"click": {"index": 1}}],
    )

    resumed = resume_from_checkpoint(checkpoint, agent_factory=FakeAgent, llm="fake")

    assert resumed.task == "task-resume"
    assert resumed.llm == "fake"
    assert resumed.step_counter == 5
    assert resumed.pending_actions_queue == [{"click": {"index": 1}}]
    assert isinstance(resumed.history, AgentHistoryList)
    assert isinstance(resumed.dom_state, BrowserStateSummary)
    assert resumed.dom_state.url == "https://example.test/resume"


def test_cli_checkpoint_commands(tmp_path: Path) -> None:
    env = dict(os.environ, BROWSER_USE_CHECKPOINT_DIR=str(tmp_path))
    CheckpointManager(storage_dir=tmp_path).save(
        task_id="task-cli",
        checkpoint_id="cli-one",
        step_counter=3,
        current_url="https://example.test/cli",
        dom_state_snapshot={},
        agent_history={"histories": []},
        pending_actions_queue=[],
        label="cli-label",
    )

    listed = subprocess.check_output(
        ["./browser-use-bridge", "checkpoint", "list", "--task-id", "task-cli", "--json"],
        text=True,
        env=env,
    )
    assert json.loads(listed)["checkpoints"][0]["checkpoint_id"] == "cli-one"

    resumed = subprocess.check_output(
        ["./browser-use-bridge", "resume", "cli-one", "--task-id", "task-cli", "--dry-run", "--json"],
        text=True,
        env=env,
    )
    assert json.loads(resumed)["status"] == "ready_to_resume"

    deleted = subprocess.check_output(
        ["./browser-use-bridge", "checkpoint", "delete", "cli-one", "--task-id", "task-cli", "--json"],
        text=True,
        env=env,
    )
    assert json.loads(deleted) == {"deleted": True, "checkpoint_id": "cli-one"}
