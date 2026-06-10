from __future__ import annotations

from dataclasses import dataclass

from .models import Memory, MemoryRelationType


@dataclass(frozen=True)
class GraphEdge:
    source_id: str
    relation_type: MemoryRelationType
    target_id: str
    reason: str = ""

    def key(self) -> tuple[str, str, str, str]:
        return (self.source_id, self.relation_type.value, self.target_id, self.reason)


@dataclass
class GraphPathLine:
    depth: int
    direction: str
    relation_type: str
    memory_id: str
    title: str = ""
    memory_type: str = ""
    status: str = ""
    reason: str = ""
    marker: str = ""

    def render(self) -> str:
        indent = "  " * self.depth
        if self.memory_type:
            target = f"{self.memory_id} [{self.memory_type}/{self.status}] {self.title}"
        else:
            target = f"{self.memory_id} [missing]"
        reason = f" - {self.reason}" if self.reason else ""
        return f"{indent}- {self.direction} {self.relation_type}: {target}{self.marker}{reason}"


@dataclass
class GraphComponent:
    memory_ids: list[str]

    def render(self, memory_by_id: dict[str, Memory]) -> str:
        labels = [
            f"{memory_id} [{memory_by_id[memory_id].type.value}/{memory_by_id[memory_id].status.value}]"
            for memory_id in self.memory_ids
        ]
        return f"- {len(self.memory_ids)} memories: {', '.join(labels)}"


@dataclass
class GraphMemoryViewReport:
    memories: list[Memory]
    edges: list[GraphEdge]
    components: list[GraphComponent]
    isolated_ids: list[str]
    dangling_edges: list[GraphEdge]
    root_id: str = ""
    max_depth: int = 3
    include_retired: bool = False
    path_lines: list[GraphPathLine] | None = None

    def render(self) -> str:
        memory_by_id = {memory.id: memory for memory in self.memories}
        connected_count = len(self.memories) - len(self.isolated_ids)
        history = "active + retired" if self.include_retired else "active only"
        lines = [
            "CMU Graph Memory View",
            "Mode: read-only graph path proof; no memories or relationships are mutated.",
            f"History: {history}",
            "",
            "Summary:",
            f"- Memories: {len(self.memories)}",
            f"- Relationships: {len(self.edges)}",
            f"- Connected Components: {len(self.components)}",
            f"- Connected Memories: {connected_count}",
            f"- Isolated Memories: {len(self.isolated_ids)}",
            f"- Dangling Relationships: {len(self.dangling_edges)}",
        ]
        if self.root_id:
            root = memory_by_id[self.root_id]
            lines.extend(
                [
                    "",
                    "Root Path:",
                    f"- {root.id} [{root.type.value}/{root.status.value}] {root.title}",
                    f"- Depth Limit: {self.max_depth}",
                    "Paths:",
                ]
            )
            if self.path_lines:
                lines.extend(line.render() for line in self.path_lines)
            else:
                lines.append("- None")
        else:
            lines.extend(["", "Connected Components:"])
            if self.components:
                lines.extend(component.render(memory_by_id) for component in self.components)
            else:
                lines.append("- None")
            lines.extend(["", "Isolated Memories:"])
            if self.isolated_ids:
                lines.extend(
                    f"- {memory_id} [{memory_by_id[memory_id].type.value}/{memory_by_id[memory_id].status.value}] "
                    f"{memory_by_id[memory_id].title}"
                    for memory_id in self.isolated_ids
                )
            else:
                lines.append("- None")
        lines.extend(["", "Dangling Relationships:"])
        if self.dangling_edges:
            lines.extend(format_dangling(edge, memory_by_id) for edge in self.dangling_edges)
        else:
            lines.append("- None")
        lines.extend(
            [
                "",
                f"Next: {next_action(self.isolated_ids, self.dangling_edges, self.root_id)}",
                "",
                "Proof Meaning: this report makes connected memory paths, component boundaries, isolated records, "
                "retired history, dangling links, and traversal cycles inspectable before CMU adopts a durable graph store.",
            ]
        )
        return "\n".join(lines)


def graph_memory_view_report(
    memories: list[Memory],
    *,
    root_id: str = "",
    max_depth: int = 3,
    include_retired: bool = False,
    graph_edges: list[GraphEdge] | None = None,
) -> GraphMemoryViewReport:
    if max_depth < 1:
        raise ValueError("graph depth must be at least 1")
    memory_by_id = {memory.id: memory for memory in memories}
    if root_id and root_id not in memory_by_id:
        raise KeyError(f"Memory not found: {root_id}")
    memory_edges = [
        GraphEdge(
            source_id=memory.id,
            relation_type=relationship.type,
            target_id=relationship.target_id,
            reason=relationship.reason,
        )
        for memory in memories
        for relationship in memory.relationships
    ]
    edges = sorted({edge.key(): edge for edge in [*memory_edges, *(graph_edges or [])]}.values(), key=lambda edge: edge.key())
    dangling_edges = [edge for edge in edges if edge.target_id not in memory_by_id]
    adjacency = build_adjacency(memories, edges)
    isolated_ids = sorted(
        memory_id
        for memory_id, neighbors in adjacency.items()
        if not any(neighbor_id in adjacency for _, _, neighbor_id in neighbors)
    )
    components = connected_components(adjacency)
    path_lines = traverse_paths(root_id, adjacency, memory_by_id, max_depth) if root_id else None
    return GraphMemoryViewReport(
        memories=sorted(memories, key=lambda memory: memory.id),
        edges=edges,
        components=components,
        isolated_ids=isolated_ids,
        dangling_edges=dangling_edges,
        root_id=root_id,
        max_depth=max_depth,
        include_retired=include_retired,
        path_lines=path_lines,
    )


def build_adjacency(
    memories: list[Memory],
    edges: list[GraphEdge],
) -> dict[str, list[tuple[GraphEdge, str, str]]]:
    memory_ids = {memory.id for memory in memories}
    adjacency: dict[str, list[tuple[GraphEdge, str, str]]] = {memory_id: [] for memory_id in memory_ids}
    for edge in edges:
        adjacency[edge.source_id].append((edge, "->", edge.target_id))
        if edge.target_id in memory_ids:
            adjacency[edge.target_id].append((edge, "<-", edge.source_id))
    for neighbors in adjacency.values():
        neighbors.sort(key=lambda item: (item[1] != "->", item[0].relation_type.value, item[2], item[0].reason))
    return adjacency


def connected_components(adjacency: dict[str, list[tuple[GraphEdge, str, str]]]) -> list[GraphComponent]:
    visited: set[str] = set()
    components: list[GraphComponent] = []
    for memory_id in sorted(adjacency):
        if memory_id in visited:
            continue
        pending = [memory_id]
        component_ids: list[str] = []
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            component_ids.append(current)
            pending.extend(
                neighbor_id
                for _, _, neighbor_id in adjacency[current]
                if neighbor_id in adjacency and neighbor_id not in visited
            )
        components.append(GraphComponent(memory_ids=sorted(component_ids)))
    return sorted(components, key=lambda component: (-len(component.memory_ids), component.memory_ids))


def traverse_paths(
    root_id: str,
    adjacency: dict[str, list[tuple[GraphEdge, str, str]]],
    memory_by_id: dict[str, Memory],
    max_depth: int,
) -> list[GraphPathLine]:
    lines: list[GraphPathLine] = []
    visited_nodes = {root_id}
    visited_edges: set[tuple[str, str, str, str]] = set()

    def visit(memory_id: str, depth: int) -> None:
        if depth >= max_depth:
            return
        for edge, direction, neighbor_id in adjacency[memory_id]:
            if edge.key() in visited_edges:
                continue
            visited_edges.add(edge.key())
            neighbor = memory_by_id.get(neighbor_id)
            marker = ""
            if neighbor is None:
                marker = ""
            elif neighbor_id in visited_nodes:
                marker = " [cycle/reference]"
            lines.append(
                GraphPathLine(
                    depth=depth + 1,
                    direction=direction,
                    relation_type=edge.relation_type.value,
                    memory_id=neighbor_id,
                    title=neighbor.title if neighbor else "",
                    memory_type=neighbor.type.value if neighbor else "",
                    status=neighbor.status.value if neighbor else "",
                    reason=edge.reason,
                    marker=marker,
                )
            )
            if neighbor is not None and neighbor_id not in visited_nodes:
                visited_nodes.add(neighbor_id)
                visit(neighbor_id, depth + 1)

    visit(root_id, 0)
    return lines


def format_dangling(edge: GraphEdge, memory_by_id: dict[str, Memory]) -> str:
    source = memory_by_id[edge.source_id]
    reason = f" - {edge.reason}" if edge.reason else ""
    return f"- {source.id} [{source.type.value}/{source.status.value}] {source.title} -> {edge.relation_type.value} -> {edge.target_id} [missing]{reason}"


def next_action(isolated_ids: list[str], dangling_edges: list[GraphEdge], root_id: str) -> str:
    if dangling_edges:
        return "repair dangling relationships or include retired history before trusting these paths"
    if isolated_ids:
        return "relate isolated memories where evidence supports a reusable path"
    if not root_id:
        return "inspect a focused path with `cmu graph <memory-id>`"
    return "use this path view to validate structure before adding broader graph-backed automation"
