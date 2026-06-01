from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from .models import Memory, MemoryStatus, utc_now
from .store import MemoryStore
from .usage import MemoryUseReceipt, MemoryUseStore


PORTABLE_BUNDLE_VERSION = "cmu-portable-bundle/v1"


@dataclass
class PortableBundle:
    schema: str
    exported_at: str
    memories: list[dict[str, Any]]
    uses: list[dict[str, Any]]
    integrity: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "exported_at": self.exported_at,
            "contents": {
                "memories": self.memories,
                "uses": self.uses,
            },
            "integrity": self.integrity,
            "warnings": self.warnings,
        }

    def render_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=True) + "\n"


@dataclass
class PortabilityReport:
    mode: str
    schema: str
    memory_count: int
    use_count: int
    memory_adds: list[str] = field(default_factory=list)
    memory_updates: list[str] = field(default_factory=list)
    memory_skips: list[str] = field(default_factory=list)
    use_adds: list[str] = field(default_factory=list)
    use_updates: list[str] = field(default_factory=list)
    use_skips: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    applied: bool = False

    def render(self) -> str:
        lines = [
            "CMU Import/Export Portability",
            f"Mode: {self.mode}",
            f"Schema: {self.schema}",
            f"Bundle Memories: {self.memory_count}",
            f"Bundle Use Receipts: {self.use_count}",
            f"Memory Adds: {len(self.memory_adds)}",
            f"Memory Updates: {len(self.memory_updates)}",
            f"Memory Skips: {len(self.memory_skips)}",
            f"Use Adds: {len(self.use_adds)}",
            f"Use Updates: {len(self.use_updates)}",
            f"Use Skips: {len(self.use_skips)}",
            f"Conflicts: {len(self.conflicts)}",
            f"Applied: {'yes' if self.applied else 'no'}",
        ]
        if self.conflicts:
            lines.append("Conflict Details:")
            lines.extend(f"- {item}" for item in self.conflicts[:10])
        if self.warnings:
            lines.append("Warnings:")
            lines.extend(f"- {item}" for item in self.warnings[:10])
        if not self.applied:
            lines.append("Dry Run: pass --apply to write this import plan.")
        return "\n".join(lines)


def export_portable_bundle(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    include_retired: bool = False,
    memory_id: str = "",
    include_uses: bool = True,
) -> PortableBundle:
    selected_memories = list(memories)
    if memory_id:
        selected_memories = [memory for memory in selected_memories if memory.id == memory_id]
    selected_ids = {memory.id for memory in selected_memories}
    selected_receipts = [receipt for receipt in receipts if include_uses and receipt.memory_id in selected_ids]
    memory_payloads = sorted((memory.to_dict() for memory in selected_memories), key=lambda item: item["id"])
    use_payloads = sorted((receipt.to_dict() for receipt in selected_receipts), key=lambda item: item["id"])
    contents = {"memories": memory_payloads, "uses": use_payloads}
    warnings = export_warnings(memory_payloads, use_payloads)
    return PortableBundle(
        schema=PORTABLE_BUNDLE_VERSION,
        exported_at=utc_now(),
        memories=memory_payloads,
        uses=use_payloads,
        integrity={
            "memory_count": len(memory_payloads),
            "use_count": len(use_payloads),
            "contents_sha256": stable_digest(contents),
        },
        warnings=warnings,
    )


def export_bundle_from_root(
    root: Path | str,
    *,
    include_retired: bool = False,
    memory_id: str = "",
    include_uses: bool = True,
) -> PortableBundle:
    store = MemoryStore(root)
    memories = store.list()
    if include_retired:
        memories.extend(store.list(status=MemoryStatus.RETIRED))
    return export_portable_bundle(
        memories,
        MemoryUseStore(root).list(),
        include_retired=include_retired,
        memory_id=memory_id,
        include_uses=include_uses,
    )


def load_portable_bundle(path: Path | str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def import_portable_bundle(
    root: Path | str,
    bundle: dict[str, Any],
    *,
    apply: bool = False,
    update_existing: bool = False,
) -> PortabilityReport:
    memories, receipts, warnings = parse_bundle(bundle)
    store = MemoryStore(root)
    use_store = MemoryUseStore(root)
    existing_memories = {memory.id: memory for memory in store.list()}
    existing_memories.update({memory.id: memory for memory in store.list(status=MemoryStatus.RETIRED)})
    existing_uses = {receipt.id: receipt for receipt in use_store.list()}
    report = build_import_report(
        memories,
        receipts,
        existing_memories=existing_memories,
        existing_uses=existing_uses,
        update_existing=update_existing,
        warnings=warnings,
    )
    if apply and not report.conflicts:
        for memory in memories:
            current = existing_memories.get(memory.id)
            if current is None:
                store.add(memory)
            elif update_existing and current.to_dict() != memory.to_dict():
                store.update(memory)
        for receipt in receipts:
            current = existing_uses.get(receipt.id)
            if current is None:
                use_store.add(receipt)
            elif update_existing and current.to_dict() != receipt.to_dict():
                use_store.update(receipt)
        report.applied = True
        report.mode = "apply"
    elif apply and report.conflicts:
        report.mode = "blocked"
    return report


def parse_bundle(bundle: dict[str, Any]) -> tuple[list[Memory], list[MemoryUseReceipt], list[str]]:
    schema = bundle.get("schema", "")
    if schema != PORTABLE_BUNDLE_VERSION:
        raise ValueError(f"Unsupported portable bundle schema: {schema or 'missing'}")
    contents = bundle.get("contents", {})
    memory_payloads = list(contents.get("memories", []))
    use_payloads = list(contents.get("uses", []))
    warnings = list(bundle.get("warnings", []))
    integrity = bundle.get("integrity", {})
    expected_digest = integrity.get("contents_sha256", "")
    actual_digest = stable_digest({"memories": memory_payloads, "uses": use_payloads})
    if expected_digest and expected_digest != actual_digest:
        warnings.append("integrity digest mismatch; inspect bundle before applying")
    return (
        [Memory.from_dict(item) for item in memory_payloads],
        [MemoryUseReceipt.from_dict(item) for item in use_payloads],
        warnings,
    )


def build_import_report(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    existing_memories: dict[str, Memory],
    existing_uses: dict[str, MemoryUseReceipt],
    update_existing: bool,
    warnings: list[str],
) -> PortabilityReport:
    report = PortabilityReport(
        mode="dry-run-update" if update_existing else "dry-run",
        schema=PORTABLE_BUNDLE_VERSION,
        memory_count=len(memories),
        use_count=len(receipts),
        warnings=list(warnings),
    )
    imported_ids = {memory.id for memory in memories}
    for memory in memories:
        current = existing_memories.get(memory.id)
        if current is None:
            report.memory_adds.append(memory.id)
        elif current.to_dict() == memory.to_dict():
            report.memory_skips.append(memory.id)
        elif update_existing:
            report.memory_updates.append(memory.id)
        else:
            report.conflicts.append(f"memory {memory.id} already exists with different content")
    for receipt in receipts:
        current = existing_uses.get(receipt.id)
        if receipt.memory_id not in imported_ids and receipt.memory_id not in existing_memories:
            report.warnings.append(f"use receipt {receipt.id} references missing memory {receipt.memory_id}")
        if current is None:
            report.use_adds.append(receipt.id)
        elif current.to_dict() == receipt.to_dict():
            report.use_skips.append(receipt.id)
        elif update_existing:
            report.use_updates.append(receipt.id)
        else:
            report.conflicts.append(f"use receipt {receipt.id} already exists with different content")
    return report


def export_warnings(memories: list[dict[str, Any]], receipts: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    memory_ids = {memory["id"] for memory in memories}
    for memory in memories:
        for relationship in memory.get("relationships", []):
            target_id = relationship.get("target_id", "")
            if target_id and target_id not in memory_ids:
                warnings.append(f"memory {memory['id']} has relationship to non-exported memory {target_id}")
    for receipt in receipts:
        memory_id = receipt.get("memory_id", "")
        if memory_id and memory_id not in memory_ids:
            warnings.append(f"use receipt {receipt.get('id', '')} references non-exported memory {memory_id}")
    return warnings


def stable_digest(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(payload.encode("utf-8")).hexdigest()
