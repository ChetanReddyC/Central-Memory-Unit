from __future__ import annotations

import json
from pathlib import Path

from .models import Memory, MemoryStatus, MemoryType, utc_now


DEFAULT_STORE_DIR = ".cmu"
DEFAULT_STORE_FILE = "memories.json"


class MemoryStore:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root)
        self.store_dir = self.root / DEFAULT_STORE_DIR
        self.store_file = self.store_dir / DEFAULT_STORE_FILE

    def init(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        if not self.store_file.exists():
            self._write({"version": 1, "memories": []})
        return self.store_file

    def add(self, memory: Memory) -> Memory:
        data = self._read()
        data["memories"].append(memory.to_dict())
        self._write(data)
        return memory

    def list(
        self,
        *,
        type: MemoryType | None = None,
        status: MemoryStatus = MemoryStatus.ACTIVE,
    ) -> list[Memory]:
        memories = [Memory.from_dict(item) for item in self._read()["memories"]]
        filtered = [memory for memory in memories if memory.status == status]
        if type is not None:
            filtered = [memory for memory in filtered if memory.type == type]
        return sorted(filtered, key=lambda item: item.updated_at, reverse=True)

    def update(self, memory: Memory) -> Memory:
        data = self._read()
        memory.updated_at = utc_now()
        memories = data["memories"]
        for index, current in enumerate(memories):
            if current["id"] == memory.id:
                memories[index] = memory.to_dict()
                self._write(data)
                return memory
        raise KeyError(f"Memory not found: {memory.id}")

    def _read(self) -> dict:
        self.init()
        with self.store_file.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write(self, data: dict) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        temp_file = self.store_file.with_suffix(".tmp")
        with temp_file.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
        temp_file.replace(self.store_file)
