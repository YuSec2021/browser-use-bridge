from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from browser_use.memory.store import MemoryEntry, MemoryType


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


class BM25MemoryBackend:
    """Flat JSONL memory store with BM25 keyword retrieval."""

    def __init__(
        self,
        storage_path: str | Path | None = None,
        *,
        k1: float = 1.5,
        b: float = 0.75,
        min_score: float = 0.0,
        autosave_every: int = 1,
        decay_half_life_days: float = 30.0,
    ) -> None:
        self.storage_path = self._resolve_storage_path(storage_path)
        self.k1 = k1
        self.b = b
        self.min_score = min_score
        self.autosave_every = max(int(autosave_every), 1)
        self.decay_half_life_days = decay_half_life_days
        self.entries: list[MemoryEntry] = []
        self._pending_writes = 0
        self.load()

    def add(self, entry: MemoryEntry) -> MemoryEntry:
        self.entries.append(entry)
        self._pending_writes += 1
        if self._pending_writes >= self.autosave_every:
            self.save()
        return entry

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        type: MemoryType | None = None,
        min_score: float | None = None,
    ) -> list[MemoryEntry]:
        query_terms = self._tokenize(query)
        if not query_terms or top_k <= 0:
            return []

        threshold = self.min_score if min_score is None else min_score
        candidates = [entry for entry in self.entries if type is None or entry.type == type]
        scored = [
            (self._score(entry, query_terms, candidates), entry)
            for entry in candidates
        ]
        ranked = [
            (score, entry)
            for score, entry in scored
            if score > threshold
        ]
        ranked.sort(key=lambda item: (-item[0], item[1].created_at, item[1].entry_id))

        now = datetime.now(tz=UTC).isoformat()
        results: list[MemoryEntry] = []
        for score, entry in ranked[:top_k]:
            entry.access_count += 1
            entry.last_accessed_at = now
            results.append(entry.model_copy(update={"score": score}))
        if results:
            self.save()
        return results

    def clear(self) -> None:
        self.entries.clear()
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text("", encoding="utf-8")
        self._pending_writes = 0

    def stats(self) -> dict[str, Any]:
        by_type = {memory_type.value: 0 for memory_type in MemoryType}
        by_category: dict[str, int] = {}
        total_tokens = 0
        for entry in self.entries:
            by_type[entry.type.value] += 1
            category = str(entry.metadata.get("category") or "uncategorized")
            by_category[category] = by_category.get(category, 0) + 1
            total_tokens += len(self._tokenize(entry.text))
        return {
            "backend": "bm25",
            "storage_path": str(self.storage_path),
            "count": len(self.entries),
            "by_type": by_type,
            "by_category": by_category,
            "avg_tokens": total_tokens / len(self.entries) if self.entries else 0.0,
            "k1": self.k1,
            "b": self.b,
            "min_score": self.min_score,
        }

    def load(self) -> None:
        if not self.storage_path.exists():
            self.entries = []
            return
        entries: list[MemoryEntry] = []
        for line in self.storage_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entries.append(MemoryEntry.model_validate_json(line))
        self.entries = entries

    def save(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(
            json.dumps(entry.model_dump(mode="json", exclude={"score"}), ensure_ascii=False, sort_keys=True)
            for entry in self.entries
        )
        self.storage_path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")
        self._pending_writes = 0

    def _score(self, entry: MemoryEntry, query_terms: list[str], corpus: list[MemoryEntry]) -> float:
        documents = [self._tokenize(candidate.text) for candidate in corpus]
        document = self._tokenize(entry.text)
        if not document:
            return 0.0

        term_counts = Counter(document)
        doc_len = len(document)
        avg_doc_len = sum(len(doc) for doc in documents) / len(documents) if documents else 1.0
        score = 0.0
        for term in query_terms:
            frequency = term_counts.get(term, 0)
            if frequency <= 0:
                continue
            docs_with_term = sum(1 for doc in documents if term in doc)
            idf = math.log(1 + (len(documents) - docs_with_term + 0.5) / (docs_with_term + 0.5))
            denominator = frequency + self.k1 * (1 - self.b + self.b * doc_len / max(avg_doc_len, 1e-9))
            score += idf * (frequency * (self.k1 + 1)) / denominator

        if score <= 0:
            return 0.0
        return score * self._decay_multiplier(entry) * (1.0 + math.log1p(entry.access_count) * 0.05)

    def _decay_multiplier(self, entry: MemoryEntry) -> float:
        try:
            created = datetime.fromisoformat(entry.created_at)
        except ValueError:
            return 1.0
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        age_days = max((datetime.now(tz=UTC) - created).total_seconds() / 86400.0, 0.0)
        if self.decay_half_life_days <= 0:
            return 1.0
        return 0.5 ** (age_days / self.decay_half_life_days)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]

    @staticmethod
    def _resolve_storage_path(storage_path: str | Path | None) -> Path:
        raw = Path(
            storage_path
            or os.getenv("BROWSER_USE_MEMORY_PATH")
            or Path.home() / ".browser-use-bridge" / "memory" / "memory.jsonl"
        ).expanduser()
        if raw.suffix:
            return raw
        return raw / "memory.jsonl"
