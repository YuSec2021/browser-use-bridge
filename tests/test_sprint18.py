from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from browser_use import MemoryEntry, MemoryStore, MemoryType
from browser_use.agent.message_manager import MessageManager
from browser_use.agent.views import AgentHistory, AgentOutput
from browser_use.browser.views import BrowserStateSummary
from browser_use.memory import extract_memory_entries


def test_memory_public_api_bm25_json_backend(tmp_path: Path) -> None:
    path = tmp_path / "memory.jsonl"
    memory = MemoryStore(storage_path=path)

    first = memory.add(
        "Use the billing dashboard path /settings/billing for invoices.",
        type=MemoryType.SEMANTIC,
        metadata={"category": "user_preferences"},
    )
    second = memory.add(
        "Checkout page failed when the coupon field was empty.",
        type=MemoryType.EPISODIC,
        metadata={"category": "failed_attempts"},
    )

    assert isinstance(first, MemoryEntry)
    assert path.exists()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2

    reloaded = MemoryStore(storage_path=path)
    results = reloaded.search("billing invoices", top_k=1)

    assert results[0].entry_id == first.entry_id
    assert results[0].score is not None and results[0].score > 0
    assert reloaded.search("coupon", type=MemoryType.EPISODIC)[0].entry_id == second.entry_id

    stats = reloaded.stats()
    assert stats["backend"] == "bm25"
    assert stats["count"] == 2
    assert stats["by_type"]["SEMANTIC"] == 1
    assert stats["by_type"]["EPISODIC"] == 1

    reloaded.clear()
    assert reloaded.stats()["count"] == 0
    assert path.read_text(encoding="utf-8") == ""


def test_auto_memory_extraction_and_message_context(tmp_path: Path) -> None:
    history = AgentHistory(
        state=BrowserStateSummary(url="https://example.test/orders", title="Orders", elements=[]),
        model_output=AgentOutput(
            memory="Order export lives under the Reports menu.",
            evaluation="failed because the export button was disabled",
            actions=[{"click": {"index": 2}}],
        ),
    )

    extracted = extract_memory_entries(history, task_id="task-memory")
    assert [entry.type for entry in extracted] == [
        MemoryType.EPISODIC,
        MemoryType.SEMANTIC,
        MemoryType.EPISODIC,
        MemoryType.WORKING,
    ]
    assert extracted[0].metadata["category"] == "navigation_memory"
    assert extracted[1].metadata["category"] == "extracted_data"

    store = MemoryStore(storage_path=tmp_path / "memory.jsonl")
    added = store.add_from_agent_step(history, task_id="task-memory")
    assert len(added) == 4

    manager = MessageManager(task="Where is the order export?", memory_store=store)
    messages = manager.build_messages(
        BrowserStateSummary(url="https://example.test", title="Home", elements=[]),
    )

    assert "Relevant memory:" in messages[1]["content"]
    assert "Order export lives under the Reports menu." in messages[1]["content"]


def test_memory_cli_commands(tmp_path: Path) -> None:
    env = dict(os.environ, BROWSER_USE_MEMORY_PATH=str(tmp_path / "memory.jsonl"))

    added = subprocess.check_output(
        [
            "./browser-use-bridge",
            "memory",
            "add",
            "Remember that invoices are under billing settings.",
            "--type",
            "SEMANTIC",
            "--metadata",
            "category=user_preferences",
            "--json",
        ],
        text=True,
        env=env,
    )
    assert json.loads(added)["type"] == "SEMANTIC"

    searched = subprocess.check_output(
        ["./browser-use-bridge", "memory", "search", "billing invoices", "--top-k", "1", "--json"],
        text=True,
        env=env,
    )
    payload = json.loads(searched)
    assert payload["memories"][0]["text"] == "Remember that invoices are under billing settings."

    stats = subprocess.check_output(["./browser-use-bridge", "memory", "stats", "--json"], text=True, env=env)
    assert json.loads(stats)["count"] == 1

    cleared = subprocess.check_output(["./browser-use-bridge", "memory", "clear", "--json"], text=True, env=env)
    assert json.loads(cleared) == {"cleared": True}
