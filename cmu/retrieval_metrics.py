from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import Memory, MemoryRelationType, MemoryRelationship, MemoryScope, MemoryType
from .retrieval import HashingEmbeddingProvider, PreflightQuery, action_threshold, rank_memories
from .scenarios import ScenarioDefinition, ScenarioLibraryStore, evaluate_scenario
from .usage import MemoryUseReceipt


RETRIEVAL_METRICS_VERSION = "cmu-retrieval-metrics/v1"
RETRIEVAL_BENCHMARK_VERSION = "cmu-retrieval-benchmark/v1"
EVALUATION_CASE_TAG = "retrieval-evaluation"


@dataclass(frozen=True)
class RetrievalMetricsItem:
    scenario_id: str
    name: str
    expected_memory: str
    actual_memory: str
    expected_action: str
    actual_action: str

    @property
    def is_positive(self) -> bool:
        return bool(self.expected_memory and self.expected_memory != "none")

    @property
    def true_positive(self) -> bool:
        return self.is_positive and self.actual_memory == self.expected_memory

    @property
    def false_negative(self) -> bool:
        return self.is_positive and self.actual_memory != self.expected_memory

    @property
    def false_positive(self) -> bool:
        return not self.is_positive and bool(self.actual_memory)

    @property
    def true_rejection(self) -> bool:
        return not self.is_positive and not self.actual_memory

    @property
    def grounded(self) -> bool:
        if not self.actual_memory:
            return self.expected_action == "quiet"
        return self.actual_memory == self.expected_memory

    def render(self) -> str:
        status = "tp" if self.true_positive else "fn" if self.false_negative else "fp" if self.false_positive else "tr"
        return (
            f"- {status}: {self.scenario_id} {self.name} "
            f"expected={self.expected_action}/{self.expected_memory or 'none'} "
            f"actual={self.actual_action}/{self.actual_memory or 'none'}"
        )


@dataclass(frozen=True)
class RetrievalMetricsReport:
    root: str
    tag: str
    items: list[RetrievalMetricsItem]

    @property
    def true_positive_count(self) -> int:
        return sum(1 for item in self.items if item.true_positive)

    @property
    def false_negative_count(self) -> int:
        return sum(1 for item in self.items if item.false_negative)

    @property
    def false_positive_count(self) -> int:
        return sum(1 for item in self.items if item.false_positive)

    @property
    def true_rejection_count(self) -> int:
        return sum(1 for item in self.items if item.true_rejection)

    @property
    def precision(self) -> float:
        denominator = self.true_positive_count + self.false_positive_count
        return self.true_positive_count / denominator if denominator else 1.0

    @property
    def recall(self) -> float:
        denominator = self.true_positive_count + self.false_negative_count
        return self.true_positive_count / denominator if denominator else 1.0

    @property
    def rejection_rate(self) -> float:
        denominator = self.true_rejection_count + self.false_positive_count
        return self.true_rejection_count / denominator if denominator else 1.0

    @property
    def grounding_rate(self) -> float:
        return sum(1 for item in self.items if item.grounded) / len(self.items) if self.items else 0.0

    @property
    def passed(self) -> bool:
        return bool(self.items) and self.false_negative_count == 0 and self.false_positive_count == 0

    def render(self) -> str:
        lines = [
            "CMU Retrieval Metrics",
            f"Version: {RETRIEVAL_METRICS_VERSION}",
            "Mode: read-only precision/recall/rejection/grounding metrics over saved scenario expectations.",
            f"Root: {self.root}",
            f"Filter: tag={self.tag}" if self.tag else "Filter: all scenarios",
            (
                "Summary: "
                f"total={len(self.items)} precision={self.precision:.2f} recall={self.recall:.2f} "
                f"rejection={self.rejection_rate:.2f} grounding={self.grounding_rate:.2f} "
                f"tp={self.true_positive_count} fn={self.false_negative_count} "
                f"fp={self.false_positive_count} tr={self.true_rejection_count}"
            ),
        ]
        if self.items:
            lines.append("")
            lines.extend(item.render() for item in self.items)
        else:
            lines.extend(["", "No scenarios with memory expectations matched."])
        lines.extend(
            [
                "",
                "Proof Meaning: retrieval quality is now measured against explicit saved expectations, including misses, bad matches, clean rejections, and grounded matches.",
            ]
        )
        return "\n".join(lines)


def retrieval_metrics_report(
    root: Path | str,
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    tag: str = "",
) -> RetrievalMetricsReport:
    root_path = Path(root)
    scenarios = [
        scenario
        for scenario in ScenarioLibraryStore(root_path).list(tag=tag or "")
        if scenario.expect_memory or scenario.expect_action
    ]
    items = []
    for scenario in scenarios:
        report = evaluate_scenario(memories, receipts, scenario.request())
        items.append(
            RetrievalMetricsItem(
                scenario_id=scenario.id,
                name=scenario.name,
                expected_memory=scenario.expect_memory or ("none" if scenario.expect_action == "quiet" else ""),
                actual_memory=report.matched_memory_id,
                expected_action=scenario.expect_action,
                actual_action=report.action,
            )
        )
    return RetrievalMetricsReport(root=str(root_path), tag=tag, items=items)


@dataclass(frozen=True)
class BenchmarkItem:
    scenario_id: str
    name: str
    current: str
    generic_vector: str
    graphless: str
    no_memory: str
    expected: str

    def render(self) -> str:
        return (
            f"- {self.scenario_id} {self.name}: expected={self.expected or 'none'} "
            f"current={self.current or 'none'} generic_vector={self.generic_vector or 'none'} "
            f"graphless={self.graphless or 'none'} no_memory={self.no_memory or 'none'}"
        )


@dataclass(frozen=True)
class BenchmarkReport:
    root: str
    tag: str
    items: list[BenchmarkItem]

    @property
    def current_hits(self) -> int:
        return sum(1 for item in self.items if item.expected and item.expected != "none" and item.current == item.expected)

    @property
    def generic_vector_hits(self) -> int:
        return sum(1 for item in self.items if item.expected and item.expected != "none" and item.generic_vector == item.expected)

    @property
    def graphless_hits(self) -> int:
        return sum(1 for item in self.items if item.expected and item.expected != "none" and item.graphless == item.expected)

    @property
    def no_memory_hits(self) -> int:
        return sum(1 for item in self.items if item.expected and item.expected != "none" and item.no_memory == item.expected)

    @property
    def passed(self) -> bool:
        return bool(self.items) and self.current_hits >= max(self.generic_vector_hits, self.graphless_hits, self.no_memory_hits)

    def render(self) -> str:
        lines = [
            "CMU Retrieval Benchmark",
            f"Version: {RETRIEVAL_BENCHMARK_VERSION}",
            "Mode: read-only benchmark against current CMU retrieval, generic vector memory, graphless memory, and no-memory baselines.",
            f"Root: {self.root}",
            f"Filter: tag={self.tag}" if self.tag else "Filter: all scenarios",
            (
                "Summary: "
                f"total={len(self.items)} current_hits={self.current_hits} "
                f"generic_vector_hits={self.generic_vector_hits} graphless_hits={self.graphless_hits} "
                f"no_memory_hits={self.no_memory_hits}"
            ),
        ]
        if self.items:
            lines.append("")
            lines.extend(item.render() for item in self.items)
        else:
            lines.extend(["", "No scenarios with positive memory expectations matched."])
        lines.extend(
            [
                "",
                "Proof Meaning: retrieval changes can now be compared to weaker memory baselines before trusting production retrieval behavior.",
            ]
        )
        return "\n".join(lines)


def retrieval_benchmark_report(root: Path | str, memories: list[Memory], *, tag: str = "") -> BenchmarkReport:
    root_path = Path(root)
    scenarios = [
        scenario
        for scenario in ScenarioLibraryStore(root_path).list(tag=tag or "")
        if scenario.expect_memory and scenario.expect_memory != "none"
    ]
    provider = HashingEmbeddingProvider()
    items = [
        BenchmarkItem(
            scenario_id=scenario.id,
            name=scenario.name,
            current=top_current_memory(memories, scenario.query()),
            generic_vector=top_generic_vector_memory(memories, scenario.query(), provider),
            graphless=top_current_memory(strip_relationships(memories), scenario.query()),
            no_memory="",
            expected=scenario.expect_memory,
        )
        for scenario in scenarios
    ]
    return BenchmarkReport(root=str(root_path), tag=tag, items=items)


def top_current_memory(memories: list[Memory], query: PreflightQuery) -> str:
    matches = [match for match in rank_memories(memories, query) if match.score >= action_threshold(query.risk)]
    return matches[0].memory.id if matches else ""


def top_generic_vector_memory(memories: list[Memory], query: PreflightQuery, provider: HashingEmbeddingProvider) -> str:
    query_vector = provider.embed(query.text())
    best_id = ""
    best_score = 0.0
    for memory in memories:
        score = cosine(query_vector, provider.embed(memory_benchmark_text(memory)))
        if score > best_score:
            best_id = memory.id
            best_score = score
    return best_id if best_score >= 0.20 else ""


def strip_relationships(memories: list[Memory]) -> list[Memory]:
    stripped = []
    for memory in memories:
        clone = Memory.from_dict(memory.to_dict())
        clone.relationships = []
        stripped.append(clone)
    return stripped


def memory_benchmark_text(memory: Memory) -> str:
    return " ".join(
        [
            memory.title,
            memory.summary,
            memory.use_this_path,
            memory.avoid_this,
            " ".join(memory.signals),
            " ".join(memory.scope.flattened()),
            " ".join(memory.evidence),
        ]
    )


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(left_value * right_value for left_value, right_value in zip(left, right))


@dataclass(frozen=True)
class EvaluationCaseSeedReport:
    root: str
    written: bool
    scenarios: list[ScenarioDefinition]

    def render(self) -> str:
        lines = [
            "CMU Retrieval Evaluation Cases",
            "Mode: seedable scenario cases for retrieval misses, bad matches, governance blocks, and challenge outcomes.",
            f"Root: {self.root}",
            f"Written: {'yes' if self.written else 'no'}",
            f"Cases: {len(self.scenarios)}",
        ]
        lines.extend(f"- {scenario.name} ({', '.join(scenario.tags)}) expects {scenario.expect_action}/{scenario.expect_memory}" for scenario in self.scenarios)
        lines.extend(
            [
                "",
                "Proof Meaning: scenario evaluation now has concrete saved case shapes for misses, false matches, governance blocks, and challenge/exception outcomes.",
            ]
        )
        return "\n".join(lines)


def seed_retrieval_evaluation_cases(root: Path | str, memories: list[Memory], *, write: bool = False) -> EvaluationCaseSeedReport:
    root_path = Path(root)
    cases = build_evaluation_cases(memories)
    if write:
        store = ScenarioLibraryStore(root_path)
        existing_names = {scenario.name for scenario in store.list()}
        for case in cases:
            if case.name not in existing_names:
                store.add(case)
    return EvaluationCaseSeedReport(root=str(root_path), written=write, scenarios=cases)


def build_evaluation_cases(memories: list[Memory]) -> list[ScenarioDefinition]:
    positive = first_memory(memories)
    stable = first_stable_memory(memories) or positive
    challenge_target = first_challenge_target(memories) or positive
    positive_id = positive.id if positive else "none"
    stable_id = stable.id if stable and stable.approved_by else "none"
    challenge_id = challenge_target.id if challenge_target else "none"
    scope = positive.scope if positive else MemoryScope(code=["cmu"], workflow=["retrieval"], actor=["agent"])
    return [
        ScenarioDefinition.create(
            name="Retrieval miss guard",
            prompt="Investigate a familiar scoped issue and make sure the stored lesson appears.",
            actor=first_or_default(scope.actor, "agent"),
            area=first_or_default(scope.code, "cmu"),
            workflow=[first_or_default(scope.workflow, "retrieval")],
            risk="high",
            expect_trigger="should-call",
            expect_action="action-note" if positive else "quiet",
            expect_memory=positive_id,
            tags=[EVALUATION_CASE_TAG, "retrieval-miss"],
        ),
        ScenarioDefinition.create(
            name="Bad match rejection guard",
            prompt="Tune an unrelated icon label in a different low-risk area.",
            actor="agent",
            area="unrelated-ui-labels",
            files=["ui/unrelated_labels.css"],
            workflow=["cosmetic-ui"],
            risk="low",
            expect_trigger="silent-skip",
            expect_action="quiet",
            expect_memory="none",
            tags=[EVALUATION_CASE_TAG, "bad-match"],
        ),
        ScenarioDefinition.create(
            name="Governance block guard",
            prompt="Apply a high consequence stable rule only if authority is present.",
            actor=first_or_default(stable.scope.actor if stable else [], "agent"),
            area=first_or_default(stable.scope.code if stable else [], "cmu"),
            workflow=[first_or_default(stable.scope.workflow if stable else [], "governance")],
            risk="high",
            irreversible=True,
            expect_trigger="must-call",
            expect_action="action-note" if stable_id != "none" else "quiet",
            expect_memory=stable_id,
            tags=[EVALUATION_CASE_TAG, "governance-block"],
        ),
        ScenarioDefinition.create(
            name="Challenge outcome guard",
            prompt="Follow the exception or challenged-path guidance for this scoped situation.",
            actor=first_or_default(challenge_target.scope.actor if challenge_target else [], "agent"),
            area=first_or_default(challenge_target.scope.code if challenge_target else [], "cmu"),
            workflow=[first_or_default(challenge_target.scope.workflow if challenge_target else [], "challenge-review")],
            risk="high",
            uncertainty=True,
            expect_trigger="must-call",
            expect_action="action-note" if challenge_target else "quiet",
            expect_memory=challenge_id,
            tags=[EVALUATION_CASE_TAG, "challenge-outcome"],
        ),
    ]


def first_memory(memories: list[Memory]) -> Memory | None:
    return next((memory for memory in memories if memory.type != MemoryType.CANDIDATE), None)


def first_stable_memory(memories: list[Memory]) -> Memory | None:
    return next((memory for memory in memories if memory.type in {MemoryType.PRACTICE, MemoryType.ANCHOR}), None)


def first_challenge_target(memories: list[Memory]) -> Memory | None:
    for memory in memories:
        if any(relationship.type == MemoryRelationType.CHALLENGES for relationship in memory.relationships):
            return memory
    return next((memory for memory in memories if memory.type in {MemoryType.EXCEPTION, MemoryType.ANTI_PATTERN}), None)


def first_or_default(values: list[str], default: str) -> str:
    return values[0] if values else default
