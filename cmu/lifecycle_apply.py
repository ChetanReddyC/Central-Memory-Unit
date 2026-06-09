from __future__ import annotations

from dataclasses import dataclass, field

from .models import Memory, MemoryType
from .promotion import promote_memory, review_promotion


LIFECYCLE_APPLY_VERSION = "cmu-lifecycle-apply/v1"


@dataclass(frozen=True)
class LifecycleApplyItem:
    memory_id: str
    title: str
    action: str
    status: str
    reason: str

    def render(self) -> str:
        return f"- {self.status}: {self.memory_id} {self.title} action={self.action} - {self.reason}"


@dataclass
class LifecycleApplyReport:
    applied: bool
    items: list[LifecycleApplyItem] = field(default_factory=list)

    @property
    def promoted_count(self) -> int:
        return sum(1 for item in self.items if item.status in {"promoted", "would-promote"})

    @property
    def blocked_count(self) -> int:
        return sum(1 for item in self.items if item.status == "blocked")

    def render(self) -> str:
        header = "CMU Lifecycle Apply Applied" if self.applied else "CMU Lifecycle Apply Dry Run"
        lines = [
            header,
            f"Version: {LIFECYCLE_APPLY_VERSION}",
            "Mode: controlled lifecycle mutation; only explicit safe gates are eligible.",
            f"Summary: total={len(self.items)} promoted={self.promoted_count} blocked={self.blocked_count}",
            "",
            "Lifecycle Actions:",
        ]
        if self.items:
            lines.extend(item.render() for item in self.items)
        else:
            lines.append("- None")
        lines.extend(
            [
                "",
                "Proof Meaning: CMU can automate Candidate -> Situation only when the existing promotion gate already passes; stable Practice/Anchor promotion still requires explicit authority review.",
            ]
        )
        return "\n".join(lines)


def apply_lifecycle_candidates(
    memories: list[Memory],
    *,
    apply: bool = False,
    limit: int = 50,
) -> LifecycleApplyReport:
    candidates = [memory for memory in memories if memory.type == MemoryType.CANDIDATE][: max(1, limit)]
    items: list[LifecycleApplyItem] = []
    for memory in candidates:
        review = review_promotion(memories, memory.id, MemoryType.SITUATION)
        if not review.gate_passed:
            reasons = list(review.missing)
            if review.duplicate is not None:
                reasons.append(f"duplicate:{review.duplicate.id}")
            items.append(
                LifecycleApplyItem(
                    memory_id=memory.id,
                    title=memory.title,
                    action="candidate-to-situation",
                    status="blocked",
                    reason=", ".join(reasons) or "promotion gate blocked",
                )
            )
            continue
        if apply:
            decision = promote_memory(memories, memory.id, MemoryType.SITUATION)
            status = "promoted" if decision.promoted else "blocked"
            reason = decision.reason
        else:
            status = "would-promote"
            reason = "Candidate passes the Situation promotion gate."
        items.append(
            LifecycleApplyItem(
                memory_id=memory.id,
                title=memory.title,
                action="candidate-to-situation",
                status=status,
                reason=reason,
            )
        )
    return LifecycleApplyReport(applied=apply, items=items)
