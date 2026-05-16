from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from browser_use_bridge.memory.store import MemoryEntry, MemoryType


class ChromaMemoryBackend:
    """Optional ChromaDB-backed memory store.

    This backend is loaded lazily so the default BM25 path has no extra
    dependency. Install `chromadb` to enable semantic vector retrieval.
    """

    def __init__(
        self,
        storage_path: str | Path | None = None,
        *,
        collection_name: str = "browser_use_memory",
        embedding_function: Any | None = None,
    ) -> None:
        try:
            import chromadb
        except ImportError as exc:
            raise ImportError("ChromaDB memory backend requires `pip install chromadb`.") from exc

        self.storage_path = Path(
            storage_path
            or os.getenv("BROWSER_USE_CHROMA_DIR")
            or Path.home() / ".browser-use-bridge" / "memory" / "chroma"
        ).expanduser()
        self.client = chromadb.PersistentClient(path=str(self.storage_path))
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=embedding_function,
        )

    def add(self, entry: MemoryEntry) -> MemoryEntry:
        metadata = entry.model_dump(mode="json", exclude={"score", "text", "metadata"})
        metadata["metadata_json"] = json.dumps(entry.metadata, ensure_ascii=False, sort_keys=True)
        self.collection.upsert(
            ids=[entry.entry_id],
            documents=[entry.text],
            metadatas=[metadata],
        )
        return entry

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        type: MemoryType | None = None,
        min_score: float | None = None,
    ) -> list[MemoryEntry]:
        where = {"type": type.value} if type is not None else None
        result = self.collection.query(query_texts=[query], n_results=top_k, where=where)
        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0] if result.get("distances") else [None] * len(ids)

        entries: list[MemoryEntry] = []
        for entry_id, document, metadata, distance in zip(ids, documents, metadatas, distances, strict=False):
            score = None if distance is None else 1.0 / (1.0 + float(distance))
            if min_score is not None and score is not None and score <= min_score:
                continue
            metadata = dict(metadata or {})
            entry_metadata = json.loads(str(metadata.pop("metadata_json", "{}")))
            entries.append(
                MemoryEntry.model_validate(
                    {
                        **metadata,
                        "entry_id": entry_id,
                        "text": document,
                        "metadata": entry_metadata,
                        "score": score,
                    }
                )
            )
        return entries

    def clear(self) -> None:
        result = self.collection.get(include=[])
        ids = result.get("ids", [])
        if ids:
            self.collection.delete(ids=ids)

    def stats(self) -> dict[str, Any]:
        return {
            "backend": "chromadb",
            "storage_path": str(self.storage_path),
            "count": self.collection.count(),
        }
