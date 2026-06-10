from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .graphview import GraphEdge
from .json_store import read_json, update_json
from .models import Memory, MemoryRelationType, utc_now


GRAPH_STORE_VERSION = "cmu-graph-store/v1"


@dataclass(frozen=True)
class GraphStoreSyncReport:
    root: str
    edge_count: int
    written: bool
    path: str

    def render(self) -> str:
        return "\n".join(
            [
                "CMU Durable Graph Store",
                f"Version: {GRAPH_STORE_VERSION}",
                f"Root: {self.root}",
                f"Path: {self.path}",
                f"Edges: {self.edge_count}",
                f"Written: {'yes' if self.written else 'no'}",
                "Proof Meaning: memory relationships are materialized into a durable normalized graph edge store for graph and retrieval-adjacent inspection.",
            ]
        )


class GraphStore:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root)
        self.path = self.root / ".cmu" / "graph_edges.json"

    def list_edges(self) -> list[GraphEdge]:
        data = read_json(self.path, {"version": 1, "edges": []})
        edges = []
        for item in data.get("edges", []):
            try:
                edges.append(
                    GraphEdge(
                        source_id=str(item["source_id"]),
                        relation_type=MemoryRelationType(str(item["relation_type"])),
                        target_id=str(item["target_id"]),
                        reason=str(item.get("reason", "")),
                    )
                )
            except (KeyError, ValueError):
                continue
        return sorted(edges, key=lambda edge: edge.key())

    def sync_from_memories(self, memories: list[Memory]) -> GraphStoreSyncReport:
        edges = materialize_edges(memories)

        def replace(data: dict) -> GraphStoreSyncReport:
            data["version"] = 1
            data["schema"] = GRAPH_STORE_VERSION
            data["updated_at"] = utc_now()
            data["edges"] = [edge_to_dict(edge) for edge in edges]
            return GraphStoreSyncReport(str(self.root), len(edges), True, str(self.path))

        return update_json(self.path, {"version": 1, "schema": GRAPH_STORE_VERSION, "edges": []}, replace)

    def status(self, memories: list[Memory]) -> GraphStoreSyncReport:
        expected = materialize_edges(memories)
        existing = self.list_edges()
        return GraphStoreSyncReport(str(self.root), len(existing or expected), False, str(self.path))


def materialize_edges(memories: list[Memory]) -> list[GraphEdge]:
    edges = [
        GraphEdge(
            source_id=memory.id,
            relation_type=relationship.type,
            target_id=relationship.target_id,
            reason=relationship.reason,
        )
        for memory in memories
        for relationship in memory.relationships
    ]
    return sorted({edge.key(): edge for edge in edges}.values(), key=lambda edge: edge.key())


def edge_to_dict(edge: GraphEdge) -> dict:
    return {
        "source_id": edge.source_id,
        "relation_type": edge.relation_type.value,
        "target_id": edge.target_id,
        "reason": edge.reason,
    }
