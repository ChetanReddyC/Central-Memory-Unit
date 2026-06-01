from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .authority import STABLE_TYPES, role_can_approve, review_is_expired
from .models import Memory, MemoryStatus, MemoryType, utc_now
from .usage import MemoryUseReceipt, is_drag_signal


@dataclass
class QualityCard:
    memory_id: str
    title: str
    memory_type: str
    status: str
    score: float
    state: str
    signals: list[str]
    recommended_action: str

    def render(self) -> str:
        return "\n".join(
            [
                f"- {self.memory_id} [{self.memory_type}/{self.status}] {self.title}",
                f"  Quality Score: {self.score:.2f}/10",
                f"  State: {self.state}",
                f"  Signals: {format_list(self.signals)}",
                f"  Next: {self.recommended_action}",
            ]
        )


@dataclass
class QualityReport:
    cards: list[QualityCard] = field(default_factory=list)
    memory_filter: str = ""
    include_retired: bool = False

    def render(self) -> str:
        lines = [
            "CMU Memory Quality and Decay",
            "Mode: read-only quality/decay proof; use decay-apply for explicit controlled mutation.",
        ]
        if self.memory_filter:
            lines.append(f"Memory Filter: {self.memory_filter}")
        lines.extend(
            [
                "",
                "Summary:",
                f"- Memories Reviewed: {len(self.cards)}",
                f"- Healthy: {sum(1 for card in self.cards if card.state == 'healthy')}",
                f"- Watch: {sum(1 for card in self.cards if card.state == 'watch')}",
                f"- Review: {sum(1 for card in self.cards if card.state == 'review')}",
                f"- Decay Ready: {sum(1 for card in self.cards if card.state == 'decay-ready')}",
                "",
                "Quality Cards:",
            ]
        )
        lines.extend(card.render() for card in self.cards)
        if not self.cards:
            lines.append("- None")
        lines.extend(
            [
                "",
                "Proof Meaning: CMU now makes weakening, expiry, drag, evidence gaps, and controlled demotion/retirement pressure inspectable instead of treating memory as permanently trusted.",
            ]
        )
        return "\n".join(lines)


@dataclass
class DecayDecision:
    applied: bool
    reason: str
    action: str
    memory: Memory | None = None
    card: QualityCard | None = None

    def render(self) -> str:
        if not self.applied or self.memory is None:
            return "\n".join(["CMU Decay Action Not Applied", f"Action: {self.action}", f"Reason: {self.reason}"])
        return "\n".join(
            [
                "CMU Decay Action Applied",
                f"Action: {self.action}",
                f"Memory: {self.memory.id} [{self.memory.type.value}/{self.memory.status.value}] {self.memory.title}",
                f"Confidence: {self.memory.confidence:.2f}",
                f"Reason: {self.reason}",
            ]
        )


def quality_report(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    memory_id: str = "",
    include_retired: bool = False,
) -> QualityReport:
    filtered = [
        memory
        for memory in memories
        if (include_retired or memory.status == MemoryStatus.ACTIVE) and (not memory_id or memory.id == memory_id)
    ]
    return QualityReport(
        cards=[quality_card(memory, receipts) for memory in sorted(filtered, key=lambda item: (quality_card(item, receipts).score, item.title))],
        memory_filter=memory_id,
        include_retired=include_retired,
    )


def quality_card(memory: Memory, receipts: list[MemoryUseReceipt], *, now: datetime | None = None) -> QualityCard:
    relevant = [receipt for receipt in receipts if receipt.memory_id == memory.id]
    linked = [receipt for receipt in relevant if receipt.commit_hash or receipt.outcome_signal]
    unresolved = [receipt for receipt in relevant if not receipt.commit_hash and not receipt.outcome_signal]
    strong = [receipt for receipt in linked if receipt.outcome_signal == "committed" and receipt.link_confidence >= 0.75]
    drag = [receipt for receipt in linked if is_drag_signal(receipt)]
    score = 2.0
    signals: list[str] = []
    if memory.scope.flattened():
        score += 1.2
        signals.append("scoped")
    else:
        signals.append("scope gap")
    if memory.evidence:
        score += min(1.5, 0.35 * len(memory.evidence))
        signals.append(f"{len(memory.evidence)} evidence item(s)")
    else:
        signals.append("evidence gap")
    score += 1.5 * memory.confidence
    if strong:
        score += min(2.0, 0.5 * len(strong))
        signals.append(f"{len(strong)} strong use(s)")
    if drag:
        score -= min(3.5, 0.9 * len(drag))
        signals.append(f"{len(drag)} drag signal(s)")
    if unresolved:
        score -= min(1.5, 0.25 * len(unresolved))
        signals.append(f"{len(unresolved)} unresolved receipt(s)")
    if review_is_expired(memory, now=now):
        score -= 2.0
        signals.append("authority review expired")
    age_days = memory_age_days(memory, now=now)
    if age_days >= 180 and not linked:
        score -= 1.5
        signals.append(f"stale {age_days} day(s) without linked use")
    if memory.status == MemoryStatus.RETIRED:
        signals.append("retired history")
    score = round(max(0.0, min(score, 10.0)), 2)
    state = quality_state(memory, score, drag=len(drag), expired=review_is_expired(memory, now=now))
    return QualityCard(
        memory_id=memory.id,
        title=memory.title,
        memory_type=memory.type.value,
        status=memory.status.value,
        score=score,
        state=state,
        signals=signals,
        recommended_action=quality_next_action(memory, state),
    )


def apply_decay_action(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    memory_id: str,
    *,
    action: str,
    reason: str,
    approved_by: str = "",
    approver_role: str = "",
) -> DecayDecision:
    memory = find_memory(memories, memory_id)
    normalized_action = action.strip().lower()
    if normalized_action not in {"weaken", "demote", "retire"}:
        return DecayDecision(applied=False, reason=f"unsupported decay action: {action}", action=normalized_action, memory=memory)
    if not reason.strip():
        return DecayDecision(applied=False, reason="decay action requires an explicit evidence-backed reason", action=normalized_action, memory=memory)
    card = quality_card(memory, receipts)
    if card.state not in {"review", "decay-ready"}:
        return DecayDecision(applied=False, reason=f"quality state {card.state} does not justify decay mutation", action=normalized_action, memory=memory, card=card)
    if normalized_action == "retire" and card.state != "decay-ready":
        return DecayDecision(applied=False, reason="retirement requires decay-ready evidence; use weaken or demote while review is still needed", action=normalized_action, memory=memory, card=card)
    if memory.type in STABLE_TYPES:
        consequence = memory.authority_consequence or ("critical" if memory.type == MemoryType.ANCHOR else "high")
        if not approved_by.strip() or not role_can_approve(approver_role, consequence):
            return DecayDecision(
                applied=False,
                reason=f"stable-memory decay requires explicit approval with sufficient {consequence} consequence authority",
                action=normalized_action,
                memory=memory,
                card=card,
            )
    original_type = memory.type
    if normalized_action == "weaken":
        memory.confidence = round(max(0.05, memory.confidence - 0.15), 2)
    elif normalized_action == "demote":
        memory.type = demoted_type(memory.type)
        memory.confidence = round(max(0.05, memory.confidence - 0.1), 2)
        if original_type in STABLE_TYPES:
            memory.approved_by = ""
            memory.authority_role = ""
            memory.authority_approved_at = ""
    else:
        memory.status = MemoryStatus.RETIRED
    memory.updated_at = utc_now()
    memory.evidence = dedupe(
        memory.evidence
        + [
            f"Decay action {normalized_action}: {reason.strip()}",
            f"Decay state before action: {card.state}; quality score {card.score:.2f}/10",
            *([f"Decay approved by: {approved_by.strip()} ({approver_role.strip().lower()})"] if approved_by.strip() else []),
        ]
    )
    return DecayDecision(applied=True, reason="Applied explicit evidence-backed decay action.", action=normalized_action, memory=memory, card=card)


def quality_state(memory: Memory, score: float, *, drag: int, expired: bool) -> str:
    if memory.status == MemoryStatus.RETIRED:
        return "retired"
    if score <= 3.0 or drag >= 3:
        return "decay-ready"
    if score <= 5.0 or drag or expired:
        return "review"
    if score <= 7.0:
        return "watch"
    return "healthy"


def quality_next_action(memory: Memory, state: str) -> str:
    if state == "decay-ready":
        return "review evidence, then explicitly weaken, demote, or retire; stable memory requires authority"
    if state == "review":
        return "inspect drag, expiry, and scope before applying weaken or demote"
    if state == "watch":
        return "collect focused linked uses and resolve evidence gaps"
    if state == "retired":
        return "keep as history unless archival/export policy removes it"
    return "keep using within scope and renew authority before expiry"


def demoted_type(memory_type: MemoryType) -> MemoryType:
    if memory_type in STABLE_TYPES:
        return MemoryType.SITUATION
    if memory_type in {MemoryType.SITUATION, MemoryType.EXCEPTION, MemoryType.ANTI_PATTERN, MemoryType.QUESTION}:
        return MemoryType.CANDIDATE
    return MemoryType.CANDIDATE


def memory_age_days(memory: Memory, *, now: datetime | None = None) -> int:
    try:
        updated = datetime.fromisoformat(memory.updated_at.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return max(0, ((now or datetime.now(timezone.utc)) - updated).days)


def find_memory(memories: list[Memory], memory_id: str) -> Memory:
    for memory in memories:
        if memory.id == memory_id:
            return memory
    raise KeyError(f"Memory not found: {memory_id}")


def dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))


def format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "none"
