from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _default_memory_id() -> str:
    return f"mem-{uuid.uuid4().hex}"


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


class MemoryType(str, Enum):
    WORKING = "WORKING"
    EPISODIC = "EPISODIC"
    SEMANTIC = "SEMANTIC"


class MemoryEntry(BaseModel):
    """Serializable memory unit stored by MemoryStore backends."""

    model_config = ConfigDict(extra="allow")

    text: str
    type: MemoryType = MemoryType.SEMANTIC
    entry_id: str = Field(default_factory=_default_memory_id)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utc_now)
    last_accessed_at: str | None = None
    access_count: int = 0
    score: float | None = None

    @property
    def id(self) -> str:
        return self.entry_id


class MemoryStore:
    """Long-term memory facade with a BM25 JSONL backend by default."""

    def __init__(
        self,
        storage_path: str | Path | None = None,
        *,
        backend: str | Any = "bm25",
        k1: float = 1.5,
        b: float = 0.75,
        min_score: float = 0.0,
        autosave_every: int = 1,
        collection_name: str = "browser_use_memory",
        embedding_function: Any | None = None,
    ) -> None:
        if isinstance(backend, str):
            backend_name = backend.lower()
            if backend_name in {"bm25", "json", "jsonl"}:
                from browser_use_bridge.memory.bm25_backend import BM25MemoryBackend

                self.backend = BM25MemoryBackend(
                    storage_path=storage_path,
                    k1=k1,
                    b=b,
                    min_score=min_score,
                    autosave_every=autosave_every,
                )
            elif backend_name in {"chroma", "chromadb"}:
                from browser_use_bridge.memory.chromadb_backend import ChromaMemoryBackend

                self.backend = ChromaMemoryBackend(
                    storage_path=storage_path,
                    collection_name=collection_name,
                    embedding_function=embedding_function,
                )
            else:
                raise ValueError(f"Unknown memory backend: {backend!r}")
        else:
            self.backend = backend

    def add(
        self,
        text: str,
        *,
        type: MemoryType | str = MemoryType.SEMANTIC,
        metadata: dict[str, Any] | None = None,
        entry_id: str | None = None,
    ) -> MemoryEntry:
        entry = MemoryEntry(
            text=text,
            type=_coerce_memory_type(type),
            metadata=metadata or {},
            entry_id=entry_id or _default_memory_id(),
        )
        return self.backend.add(entry)

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        type: MemoryType | str | None = None,
        min_score: float | None = None,
    ) -> list[MemoryEntry]:
        memory_type = _coerce_memory_type(type) if type is not None else None
        return self.backend.search(query, top_k=top_k, type=memory_type, min_score=min_score)

    def clear(self) -> None:
        self.backend.clear()

    def stats(self) -> dict[str, Any]:
        return self.backend.stats()

    def add_from_agent_step(self, step: Any, *, task_id: str | None = None) -> list[MemoryEntry]:
        entries = extract_memory_entries(step, task_id=task_id)
        return [
            self.add(entry.text, type=entry.type, metadata=entry.metadata, entry_id=entry.entry_id)
            for entry in entries
        ]

    def extract_from_step(self, step: Any, *, task_id: str | None = None) -> list[MemoryEntry]:
        return self.add_from_agent_step(step, task_id=task_id)

    def remember_step(self, step: Any, *, task_id: str | None = None) -> list[MemoryEntry]:
        return self.add_from_agent_step(step, task_id=task_id)


def extract_memory_entries(step: Any, *, task_id: str | None = None) -> list[MemoryEntry]:
    """Extract durable memories from an AgentHistory-like step."""

    data = _to_plain(step)
    state = _to_plain(data.get("state")) if isinstance(data, dict) else {}
    output = _to_plain(data.get("model_output")) if isinstance(data, dict) else {}
    error_summary = _to_plain(data.get("error_summary")) if isinstance(data, dict) else {}

    entries: list[MemoryEntry] = []
    base_metadata = {"task_id": task_id} if task_id else {}

    if isinstance(state, dict):
        url = str(state.get("url") or "").strip()
        title = str(state.get("title") or "").strip()
        if url:
            text = f"Visited {url}"
            if title:
                text += f" ({title})"
            entries.append(
                MemoryEntry(
                    text=text,
                    type=MemoryType.EPISODIC,
                    metadata={
                        **base_metadata,
                        "category": "navigation_memory",
                        "url": url,
                        "title": title,
                    },
                )
            )

    if isinstance(output, dict):
        memory_text = str(output.get("memory") or "").strip()
        if memory_text:
            entries.append(
                MemoryEntry(
                    text=memory_text,
                    type=MemoryType.SEMANTIC,
                    metadata={**base_metadata, "category": "extracted_data"},
                )
            )

        evaluation = str(output.get("evaluation") or "").strip()
        if evaluation and _looks_like_failure(evaluation):
            entries.append(
                MemoryEntry(
                    text=f"Failed attempt: {evaluation}",
                    type=MemoryType.EPISODIC,
                    metadata={**base_metadata, "category": "failed_attempts"},
                )
            )

        actions = output.get("actions")
        if actions:
            entries.append(
                MemoryEntry(
                    text=f"Action sequence: {actions}",
                    type=MemoryType.WORKING,
                    metadata={**base_metadata, "category": "navigation_sequence"},
                )
            )

    if error_summary:
        entries.append(
            MemoryEntry(
                text=f"Error summary: {error_summary}",
                type=MemoryType.EPISODIC,
                metadata={**base_metadata, "category": "failed_attempts"},
            )
        )

    return entries


def _looks_like_failure(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ["fail", "failed", "error", "blocked", "unable", "retry"])


def _to_plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return {}


def _coerce_memory_type(value: MemoryType | str) -> MemoryType:
    if isinstance(value, MemoryType):
        return value
    normalized = str(value).strip()
    try:
        return MemoryType(normalized)
    except ValueError:
        return MemoryType(normalized.upper())
