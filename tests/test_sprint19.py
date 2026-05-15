from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from PIL import Image

from browser_use import HistoryExporter
from browser_use.checkpoint import CheckpointManager
from browser_use.history.exporter import HistoryExporter as ModuleHistoryExporter


def test_history_exporter_json_and_dom_diff(tmp_path: Path) -> None:
    assert HistoryExporter is ModuleHistoryExporter

    manager = CheckpointManager(storage_dir=tmp_path / "checkpoints")
    manager.save(
        task_id="task-replay",
        checkpoint_id="cp-replay",
        step_counter=2,
        current_url="https://example.test/final",
        dom_state_snapshot={},
        agent_history={
            "histories": [
                {
                    "state": {
                        "url": "https://example.test/start",
                        "title": "Start",
                        "elements": [{"index": 0, "tag_name": "button", "text": "Next"}],
                    },
                    "model_output": {"actions": [{"click": {"index": 0}}]},
                    "timestamp": "2026-05-15T00:00:00Z",
                    "duration_ms": 120,
                    "token_count": 42,
                    "llm_model": "mock-model",
                    "screenshots": ["step-0.jpg"],
                },
                {
                    "state": {
                        "url": "https://example.test/final",
                        "title": "Final",
                        "elements": [{"index": 0, "tag_name": "button", "text": "Done"}],
                    },
                    "model_output": {"actions": [{"done": {"text": "complete"}}]},
                    "timestamp": "2026-05-15T00:00:01Z",
                },
            ]
        },
        pending_actions_queue=[],
        label="completed-run",
    )

    payload = json.loads(
        HistoryExporter(checkpoint_manager=manager, output_dir=tmp_path / "exports")
        .to_json("cp-replay", task_id="task-replay")
        .read_text(encoding="utf-8")
    )

    assert payload["checkpoint"]["label"] == "completed-run"
    assert payload["history"]["histories"][0]["model_output"]["actions"] == [{"click": {"index": 0}}]
    first, second = payload["steps"]
    assert first["token_count"] == 42
    assert first["llm_model"] == "mock-model"
    assert first["dom_diff"] == {"added": [], "removed": [], "modified": []}
    assert second["actions_executed"] == [{"done": {"text": "complete"}}]
    assert second["dom_diff"]["modified"][0]["before"]["text"] == "Next"
    assert second["dom_diff"]["modified"][0]["after"]["text"] == "Done"


def test_history_exporter_html_gif_and_task_isolation(tmp_path: Path) -> None:
    screenshot = tmp_path / "shot.jpg"
    Image.new("RGB", (120, 80), "white").save(screenshot)
    manager = CheckpointManager(storage_dir=tmp_path / "checkpoints")

    for task_id, title in [("task-a", "Task A"), ("task-b", "Task B")]:
        manager.save(
            task_id=task_id,
            checkpoint_id="same-id",
            step_counter=1,
            current_url=f"https://example.test/{task_id}",
            dom_state_snapshot={},
            agent_history={
                "histories": [
                    {
                        "state": {"url": f"https://example.test/{task_id}", "title": title, "elements": []},
                        "model_output": {"actions": [{"done": {"task": task_id}}]},
                        "screenshots": [str(screenshot)],
                    }
                ]
            },
            pending_actions_queue=[],
        )

    exporter = HistoryExporter(checkpoint_manager=manager, output_dir=tmp_path / "exports")
    html_path = exporter.to_html("same-id", task_id="task-a")
    gif_path = exporter.to_gif("same-id", task_id="task-a", fps=4, resolution=(96, 60), loop=1)
    task_a = exporter.to_json("same-id", task_id="task-a").read_text(encoding="utf-8")
    task_b = exporter.to_json("same-id", task_id="task-b").read_text(encoding="utf-8")

    html = html_path.read_text(encoding="utf-8")
    assert "AgentHistory Replay" in html
    assert "data-replay-payload" in html
    assert "step-timeline" in html
    assert "action-timeline" in html
    assert "dom-diff-viewer" in html
    assert "screenshot-gallery" in html
    assert "data:image/" in html
    assert "http://" not in html and "<script src=" not in html and "<link rel=" not in html
    assert gif_path.exists()
    assert gif_path.with_suffix(".json").exists()
    assert '"fps": 4' in gif_path.with_suffix(".json").read_text(encoding="utf-8")
    assert "Task A" in task_a and "task-a" in task_a
    assert "Task B" in task_b and "task-b" in task_b
    assert task_a != task_b


def test_replay_cli_outputs_structured_paths(tmp_path: Path) -> None:
    checkpoint_dir = tmp_path / "checkpoints"
    output_dir = tmp_path / "exports"
    screenshot = tmp_path / "shot.jpg"
    Image.new("RGB", (100, 70), "white").save(screenshot)
    CheckpointManager(storage_dir=checkpoint_dir).save(
        task_id="task-cli-replay",
        checkpoint_id="cp-cli-replay",
        step_counter=1,
        current_url="https://example.test/cli",
        dom_state_snapshot={},
        agent_history={
            "histories": [
                {
                    "state": {"url": "https://example.test/cli", "title": "CLI", "elements": []},
                    "model_output": {"actions": [{"done": {}}]},
                    "screenshots": [str(screenshot)],
                }
            ]
        },
        pending_actions_queue=[],
    )

    env = dict(os.environ, BROWSER_USE_CHECKPOINT_DIR=str(checkpoint_dir))
    output = subprocess.check_output(
        [
            "./browser-use-bridge",
            "replay",
            "cp-cli-replay",
            "--task-id",
            "task-cli-replay",
            "--format",
            "all",
            "--output-dir",
            str(output_dir),
            "--json",
        ],
        text=True,
        env=env,
    )
    payload = json.loads(output)

    assert payload["checkpoint_id"] == "cp-cli-replay"
    assert payload["format"] == "all"
    assert set(payload["paths"]) == {"json", "html", "gif"}
    assert all(Path(path).exists() for path in payload["paths"].values())
