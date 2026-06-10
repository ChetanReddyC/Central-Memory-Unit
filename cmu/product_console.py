from __future__ import annotations

from dataclasses import dataclass, field
import json

from .analytics import UsefulnessAnalyticsCard, usefulness_analytics_report
from .graphview import GraphMemoryViewReport, graph_memory_view_report
from .models import Memory, MemoryRelationType, MemoryStatus, MemoryType
from .readiness import ReadinessIssue, readiness_report
from .review_queue import ReviewQueueCard, review_queue
from .team_directory import TeamScopeRecord
from .usage import MemoryUseReceipt


PRODUCT_CONSOLE_VERSION = "cmu-product-console/v1"


@dataclass(frozen=True)
class ProductGraphNode:
    memory_id: str
    title: str
    memory_type: str
    status: str
    relationships: int

    def render(self) -> str:
        return f"- {self.memory_id} [{self.memory_type}/{self.status}] {self.title} ({self.relationships} links)"

    def to_dict(self) -> dict:
        return {
            "memory_id": self.memory_id,
            "title": self.title,
            "type": self.memory_type,
            "status": self.status,
            "relationships": self.relationships,
        }


@dataclass(frozen=True)
class ProductNavigationPath:
    situation_id: str
    situation: str
    cause: str
    fix: str
    practices: list[str] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"- {self.situation_id} {self.situation}",
            f"  Cause: {self.cause or 'not recorded'}",
            f"  Fix: {self.fix or 'not recorded'}",
            f"  Practice: {format_list(self.practices)}",
            f"  Exception: {format_list(self.exceptions)}",
            f"  Warning: {format_list(self.warnings)}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "situation_id": self.situation_id,
            "situation": self.situation,
            "cause": self.cause,
            "fix": self.fix,
            "practices": self.practices,
            "exceptions": self.exceptions,
            "warnings": self.warnings,
        }


@dataclass
class ProductConsoleReport:
    root: str
    memories_reviewed: int
    receipts_reviewed: int
    graph: GraphMemoryViewReport
    graph_nodes: list[ProductGraphNode]
    review_cards: list[ReviewQueueCard]
    trust_cards: list[UsefulnessAnalyticsCard]
    cleanup_items: list[ReadinessIssue]
    navigation_paths: list[ProductNavigationPath]
    memory_filter: str = ""

    @property
    def urgent_reviews(self) -> int:
        return sum(1 for card in self.review_cards if card.priority in {"P0", "P1"})

    @property
    def evidence_gaps(self) -> int:
        return sum(1 for card in self.trust_cards if card.verdict == "evidence-gap")

    def render(self) -> str:
        lines = [
            "CMU Product Console",
            f"Version: {PRODUCT_CONSOLE_VERSION}",
            "Mode: read-only human/product surface over graph, review, evidence, cleanup, and navigation stores.",
            f"Root: {self.root}",
        ]
        if self.memory_filter:
            lines.append(f"Memory Filter: {self.memory_filter}")
        lines.extend(
            [
                "",
                "Summary:",
                f"- Memories Reviewed: {self.memories_reviewed}",
                f"- Use Receipts Reviewed: {self.receipts_reviewed}",
                f"- Graph Nodes: {len(self.graph_nodes)}",
                f"- Graph Relationships: {len(self.graph.edges)}",
                f"- Review Cards: {len(self.review_cards)}",
                f"- Urgent Reviews: {self.urgent_reviews}",
                f"- Trust/Evidence Cards: {len(self.trust_cards)}",
                f"- Evidence Gaps: {self.evidence_gaps}",
                f"- Cleanup Items: {len(self.cleanup_items)}",
                f"- Navigation Paths: {len(self.navigation_paths)}",
                "",
                "Memory Graph Tree:",
            ]
        )
        lines.extend(node.render() for node in self.graph_nodes) if self.graph_nodes else lines.append("- None")
        lines.extend(["", "Review Cards:"])
        lines.extend(card.render() for card in self.review_cards) if self.review_cards else lines.append("- None")
        lines.extend(["", "Trust And Evidence:"])
        lines.extend(render_trust_card(card) for card in self.trust_cards) if self.trust_cards else lines.append("- None")
        lines.extend(["", "Cleanup Queue:"])
        lines.extend(item.render() for item in self.cleanup_items) if self.cleanup_items else lines.append("- None")
        lines.extend(["", "Situation Paths:"])
        lines.extend(path.render() for path in self.navigation_paths) if self.navigation_paths else lines.append("- None")
        lines.extend(
            [
                "",
                "Proof Meaning: this console gives humans one inspectable product surface for memory graph/tree shape, review decisions, trust evidence, cleanup work, and situation-to-practice navigation without applying hidden mutations.",
            ]
        )
        return "\n".join(lines)

    def to_json(self) -> str:
        payload = {
            "schema": PRODUCT_CONSOLE_VERSION,
            "root": self.root,
            "read_only": True,
            "memory_filter": self.memory_filter,
            "summary": {
                "memories_reviewed": self.memories_reviewed,
                "receipts_reviewed": self.receipts_reviewed,
                "graph_nodes": len(self.graph_nodes),
                "graph_relationships": len(self.graph.edges),
                "review_cards": len(self.review_cards),
                "urgent_reviews": self.urgent_reviews,
                "trust_cards": len(self.trust_cards),
                "evidence_gaps": self.evidence_gaps,
                "cleanup_items": len(self.cleanup_items),
                "navigation_paths": len(self.navigation_paths),
            },
            "graph_tree": [node.to_dict() for node in self.graph_nodes],
            "review_cards": [review_card_to_dict(card) for card in self.review_cards],
            "trust_evidence": [trust_card_to_dict(card) for card in self.trust_cards],
            "cleanup": [cleanup_item_to_dict(item) for item in self.cleanup_items],
            "navigation_paths": [path.to_dict() for path in self.navigation_paths],
        }
        return json.dumps(payload, indent=2, sort_keys=True)


def product_console_report(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    team_scopes: list[TeamScopeRecord] | None = None,
    *,
    root: str = ".",
    memory_id: str = "",
    include_retired: bool = False,
) -> ProductConsoleReport:
    reviewed = [memory for memory in memories if include_retired or memory.status == MemoryStatus.ACTIVE]
    if memory_id:
        graph_report = graph_memory_view_report(reviewed, root_id=memory_id, include_retired=include_retired)
        graph_memory_ids = focused_graph_ids(graph_report)
        graph_nodes = graph_nodes_for([memory for memory in reviewed if memory.id in graph_memory_ids])
    else:
        graph_report = graph_memory_view_report(reviewed, include_retired=include_retired)
        graph_nodes = graph_nodes_for(reviewed)
    queue = review_queue(reviewed, receipts, team_scopes or [])
    analytics = usefulness_analytics_report(reviewed, receipts, memory_id=memory_id)
    readiness = readiness_report(reviewed, receipts, include_retired=include_retired)
    return ProductConsoleReport(
        root=root,
        memories_reviewed=len(reviewed),
        receipts_reviewed=len(receipts),
        graph=graph_report,
        graph_nodes=graph_nodes,
        review_cards=filter_review_cards(queue.cards, memory_id),
        trust_cards=analytics.cards,
        cleanup_items=filter_cleanup_items(readiness.issues, memory_id),
        navigation_paths=navigation_paths(reviewed, memory_id=memory_id),
        memory_filter=memory_id,
    )


def graph_nodes_for(memories: list[Memory]) -> list[ProductGraphNode]:
    incoming_counts: dict[str, int] = {}
    memory_ids = {memory.id for memory in memories}
    for memory in memories:
        for relationship in memory.relationships:
            if relationship.target_id in memory_ids:
                incoming_counts[relationship.target_id] = incoming_counts.get(relationship.target_id, 0) + 1
    nodes = [
        ProductGraphNode(
            memory_id=memory.id,
            title=memory.title,
            memory_type=memory.type.value,
            status=memory.status.value,
            relationships=len(memory.relationships) + incoming_counts.get(memory.id, 0),
        )
        for memory in memories
    ]
    return sorted(nodes, key=lambda node: (-node.relationships, node.memory_type, node.title.lower()))


def focused_graph_ids(report: GraphMemoryViewReport) -> set[str]:
    ids = {report.root_id} if report.root_id else set()
    for line in report.path_lines or []:
        ids.add(line.memory_id)
    return ids


def filter_review_cards(cards: list[ReviewQueueCard], memory_id: str) -> list[ReviewQueueCard]:
    if not memory_id:
        return cards
    return [card for card in cards if card.memory_id == memory_id or card.evidence.find(memory_id) >= 0]


def filter_cleanup_items(items: list[ReadinessIssue], memory_id: str) -> list[ReadinessIssue]:
    if not memory_id:
        return items
    return [item for item in items if item.subject_id == memory_id or memory_id in item.evidence]


def navigation_paths(memories: list[Memory], *, memory_id: str = "") -> list[ProductNavigationPath]:
    memory_by_id = {memory.id: memory for memory in memories}
    paths: list[ProductNavigationPath] = []
    for memory in memories:
        if memory_id and memory.id != memory_id:
            continue
        if memory.type not in {MemoryType.SITUATION, MemoryType.ANTI_PATTERN, MemoryType.QUESTION}:
            continue
        practices: list[str] = []
        exceptions: list[str] = []
        warnings: list[str] = []
        for relationship in memory.relationships:
            target = memory_by_id.get(relationship.target_id)
            label = target_label(target, relationship.target_id)
            if relationship.type == MemoryRelationType.RELATED_PRACTICE:
                practices.append(label)
            elif relationship.type == MemoryRelationType.EXCEPTION_TO:
                exceptions.append(label)
            elif relationship.type in {MemoryRelationType.CHALLENGES, MemoryRelationType.SUPPORTS}:
                warnings.append(f"{relationship.type.value}: {label}")
        for source in memories:
            for relationship in source.relationships:
                if relationship.target_id != memory.id:
                    continue
                label = target_label(source, source.id)
                if relationship.type == MemoryRelationType.EXCEPTION_TO:
                    exceptions.append(label)
                elif relationship.type == MemoryRelationType.RELATED_PRACTICE:
                    practices.append(label)
                elif relationship.type in {MemoryRelationType.CHALLENGES, MemoryRelationType.SUPPORTS}:
                    warnings.append(f"incoming {relationship.type.value}: {label}")
        paths.append(
            ProductNavigationPath(
                situation_id=memory.id,
                situation=memory.title,
                cause=memory.summary,
                fix=memory.use_this_path,
                practices=sorted(set(practices)),
                exceptions=sorted(set(exceptions)),
                warnings=sorted(set(warnings)),
            )
        )
    return sorted(paths, key=lambda path: path.situation.lower())


def target_label(memory: Memory | None, fallback_id: str) -> str:
    if memory is None:
        return f"{fallback_id} [missing]"
    return f"{memory.id} [{memory.type.value}] {memory.title}"


def render_trust_card(card: UsefulnessAnalyticsCard) -> str:
    return "\n".join(
        [
            f"- {card.memory_id} [{card.memory_type}] {card.title}",
            f"  Verdict: {card.verdict}",
            f"  Evidence: {card.linked_uses}/{card.total_uses} linked; {card.strong_committed} strong; {card.drag_signals} drag; {card.resolved_without_commit} resolved",
            f"  Governance: {card.governance_state}",
            f"  Next: {card.next_action}",
        ]
    )


def review_card_to_dict(card: ReviewQueueCard) -> dict:
    return {
        "priority": card.priority,
        "category": card.category,
        "memory_id": card.memory_id,
        "title": card.title,
        "reason": card.reason,
        "command": card.command,
        "evidence": card.evidence,
    }


def trust_card_to_dict(card: UsefulnessAnalyticsCard) -> dict:
    return {
        "memory_id": card.memory_id,
        "title": card.title,
        "type": card.memory_type,
        "verdict": card.verdict,
        "total_uses": card.total_uses,
        "linked_uses": card.linked_uses,
        "strong_committed": card.strong_committed,
        "drag_signals": card.drag_signals,
        "resolved_without_commit": card.resolved_without_commit,
        "governance": card.governance_state,
        "next_action": card.next_action,
    }


def cleanup_item_to_dict(item: ReadinessIssue) -> dict:
    return {
        "severity": item.severity,
        "category": item.category,
        "subject_id": item.subject_id,
        "title": item.title,
        "state": item.state,
        "evidence": item.evidence,
        "next_action": item.next_action,
    }


def format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "none"
