from __future__ import annotations

from pathlib import Path

from .json_store import read_json, update_json
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
        read_json(self.store_file, {"version": 1, "memories": []})
        return self.store_file

    def add(self, memory: Memory) -> Memory:
        return update_json(
            self.store_file,
            {"version": 1, "memories": []},
            lambda data: append_memory(data, memory),
        )

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
        memory.updated_at = utc_now()
        return update_json(
            self.store_file,
            {"version": 1, "memories": []},
            lambda data: replace_memory(data, memory),
        )

    def _read(self) -> dict:
        return read_json(self.store_file, {"version": 1, "memories": []})

def append_memory(data: dict, memory: Memory) -> Memory:
    data["memories"].append(memory.to_dict())
    return memory


def replace_memory(data: dict, memory: Memory) -> Memory:
    memories = data["memories"]
    for index, current in enumerate(memories):
        if current["id"] == memory.id:
            memories[index] = memory.to_dict()
            return memory
    raise KeyError(f"Memory not found: {memory.id}")
