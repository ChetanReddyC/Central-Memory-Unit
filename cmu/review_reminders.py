from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .authority import parse_iso, review_expiry_state
from .models import Memory, MemoryStatus, MemoryType
from .review_queue import ReviewQueueCard, review_queue
from .team_directory import TeamScopeRecord
from .usage import MemoryUseReceipt


REVIEW_REMINDERS_VERSION = "cmu-review-reminders/v1"
STABLE_TYPES = {MemoryType.PRACTICE, MemoryType.ANCHOR}


@dataclass(frozen=True)
class ReviewReminder:
    priority: str
    category: str
    subject_id: str
    title: str
    reason: str
    command: str
    due: str = ""

    def render(self) -> str:
        lines = [
            f"- [{self.priority}] {self.category}: {self.subject_id} {self.title}",
            f"  Reason: {self.reason}",
            f"  Command: {self.command}",
        ]
        if self.due:
            lines.append(f"  Due: {self.due}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, str]:
        return {
            "priority": self.priority,
            "category": self.category,
            "subject_id": self.subject_id,
            "title": self.title,
            "reason": self.reason,
            "command": self.command,
            "due": self.due,
        }


@dataclass
class ReviewRemindersReport:
    reminders: list[ReviewReminder] = field(default_factory=list)
    days: int = 14

    @property
    def delivery_ready(self) -> bool:
        return all(reminder.command.strip() and reminder.subject_id.strip() for reminder in self.reminders)

    def priority_counts(self) -> dict[str, int]:
        return {
            "P0": sum(1 for reminder in self.reminders if reminder.priority == "P0"),
            "P1": sum(1 for reminder in self.reminders if reminder.priority == "P1"),
            "P2": sum(1 for reminder in self.reminders if reminder.priority == "P2"),
        }

    def to_delivery_payload(self) -> dict[str, object]:
        counts = self.priority_counts()
        return {
            "schema": REVIEW_REMINDERS_VERSION,
            "mode": "read-only-reminder-delivery",
            "due_window_days": self.days,
            "delivery_ready": self.delivery_ready,
            "summary": {
                "total": len(self.reminders),
                "p0": counts["P0"],
                "p1": counts["P1"],
                "p2": counts["P2"],
                "urgent": counts["P0"] + counts["P1"],
            },
            "reminders": [reminder.to_dict() for reminder in self.reminders],
            "commands": [reminder.command for reminder in self.reminders],
        }

    def render(self) -> str:
        counts = self.priority_counts()
        lines = [
            "CMU Review Reminders",
            f"Version: {REVIEW_REMINDERS_VERSION}",
            "Mode: lightweight read-only review reminders; no memories or receipts are mutated.",
            f"Due Window Days: {self.days}",
            "",
            "Summary:",
            f"- Total Reminders: {len(self.reminders)}",
            f"- P0: {counts['P0']}",
            f"- P1: {counts['P1']}",
            f"- P2: {counts['P2']}",
            f"- Delivery Ready: {'yes' if self.delivery_ready else 'no'}",
            "",
            "Reminders:",
        ]
        if not self.reminders:
            lines.append("- None")
        else:
            lines.extend(reminder.render() for reminder in self.reminders)
        lines.extend(
            [
                "",
                "Proof Meaning: stable-memory review expiry and open approval moments can now be surfaced as small reminders without turning review into background mutation.",
            ]
        )
        return "\n".join(lines)


def review_reminders(
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    team_scopes: list[TeamScopeRecord] | None = None,
    days: int = 14,
    now: datetime | None = None,
) -> ReviewRemindersReport:
    current = now or datetime.now(timezone.utc)
    bounded_days = max(0, days)
    reminders: list[ReviewReminder] = []
    reminders.extend(authority_review_reminders(memories, days=bounded_days, now=current))
    reminders.extend(queue_card_reminders(review_queue(memories, receipts, team_scopes or []).cards))
    return ReviewRemindersReport(reminders=sorted(reminders, key=reminder_sort_key), days=bounded_days)


def authority_review_reminders(memories: list[Memory], *, days: int, now: datetime) -> list[ReviewReminder]:
    reminders: list[ReviewReminder] = []
    due_window = now + timedelta(days=days)
    for memory in memories:
        if memory.status != MemoryStatus.ACTIVE or memory.type not in STABLE_TYPES:
            continue
        due_text = memory.authority_review_due_at.strip()
        due = parse_iso(due_text)
        if due_text and due is None:
            reminders.append(
                ReviewReminder(
                    priority="P1",
                    category="authority-review-invalid-date",
                    subject_id=memory.id,
                    title=memory.title,
                    reason="Authority review due date is not parseable; renew metadata before relying on expiry reminders.",
                    command=f"cmu authority-set {memory.id} --owner <owner-or-team> --approved-by <owner-or-team> --approver-role owner --consequence high --review-due <iso-date>",
                    due=due_text,
                )
            )
            continue
        if due is None:
            if memory.approved_by:
                reminders.append(
                    ReviewReminder(
                        priority="P2",
                        category="authority-review-not-scheduled",
                        subject_id=memory.id,
                        title=memory.title,
                        reason="Stable memory has approval but no lightweight review due date.",
                        command=f"cmu authority-set {memory.id} --owner <owner-or-team> --approved-by {quote(memory.approved_by)} --approver-role owner --consequence high --review-due <iso-date>",
                        due=review_expiry_state(memory, now=now),
                    )
                )
            continue
        if due < now:
            reminders.append(
                ReviewReminder(
                    priority="P0",
                    category="authority-review-expired",
                    subject_id=memory.id,
                    title=memory.title,
                    reason="Stable-memory authority review is expired; governance should not treat it as settled.",
                    command=f"cmu authority-set {memory.id} --owner <owner-or-team> --approved-by <owner-or-team> --approver-role owner --consequence {memory.authority_consequence or 'high'} --review-due <iso-date>",
                    due=due_text,
                )
            )
        elif due <= due_window:
            reminders.append(
                ReviewReminder(
                    priority="P1",
                    category="authority-review-due-soon",
                    subject_id=memory.id,
                    title=memory.title,
                    reason=f"Stable-memory authority review is due within {days} day(s).",
                    command=f"cmu authority --memory {memory.id}",
                    due=due_text,
                )
            )
    return reminders


def queue_card_reminders(cards: list[ReviewQueueCard]) -> list[ReviewReminder]:
    reminders: list[ReviewReminder] = []
    for card in cards:
        if card.priority not in {"P0", "P1"}:
            continue
        reminders.append(
            ReviewReminder(
                priority=card.priority,
                category=f"open-{card.category}",
                subject_id=card.memory_id,
                title=card.title,
                reason=card.reason,
                command=card.command,
            )
        )
    return reminders


def reminder_sort_key(reminder: ReviewReminder) -> tuple[int, str, str]:
    priority_rank = {"P0": 0, "P1": 1, "P2": 2}.get(reminder.priority, 9)
    return priority_rank, reminder.category, reminder.title.lower()


def quote(value: str) -> str:
    if " " in value:
        return f'"{value}"'
    return value
