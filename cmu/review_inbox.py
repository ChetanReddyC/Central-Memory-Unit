from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from .review_export import REVIEW_EXPORT_VERSION
from .review_queue import ReviewQueueReport
from .review_reminders import ReviewRemindersReport
from .team_review_handoff import TeamReviewHandoffReport


REVIEW_INBOX_VERSION = "cmu-review-inbox/v1"
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


@dataclass(frozen=True)
class ReviewInboxItem:
    source: str
    priority: str
    category: str
    subject_id: str
    title: str
    command: str

    def render(self) -> str:
        command = f" -> {self.command}" if self.command else ""
        return f"- [{self.priority}] {self.source}/{self.category}: {self.title} ({self.subject_id or 'no-subject'}){command}"

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "priority": self.priority,
            "category": self.category,
            "subject_id": self.subject_id,
            "title": self.title,
            "command": self.command,
        }


@dataclass(frozen=True)
class ReviewInboxReport:
    root: str
    source: str
    items: list[ReviewInboxItem] = field(default_factory=list)

    @property
    def urgent(self) -> int:
        return sum(1 for item in self.items if item.priority in {"P0", "P1"})

    def render(self) -> str:
        lines = [
            "CMU Review Inbox",
            f"Version: {REVIEW_INBOX_VERSION}",
            "Mode: read-only non-CLI inbox over review export, owner handoffs, and reminders.",
            f"Root: {self.root}",
            f"Source: {self.source}",
            "",
            "Summary:",
            f"- Items: {len(self.items)}",
            f"- Urgent: {self.urgent}",
            "",
            "Inbox:",
        ]
        lines.extend(item.render() for item in self.items) if self.items else lines.append("- None")
        lines.extend(
            [
                "",
                "Proof Meaning: review-export now has a practical inbox surface with clear human review moments outside raw CLI queue text.",
            ]
        )
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema": REVIEW_INBOX_VERSION,
                "root": self.root,
                "source": self.source,
                "read_only": True,
                "summary": {"items": len(self.items), "urgent": self.urgent},
                "items": [item.to_dict() for item in self.items],
            },
            indent=2,
            sort_keys=True,
        )


def review_inbox_from_reports(
    *,
    root: Path | str,
    queue: ReviewQueueReport,
    handoffs: TeamReviewHandoffReport,
    reminders: ReviewRemindersReport,
) -> ReviewInboxReport:
    items: list[ReviewInboxItem] = []
    for card in queue.cards:
        items.append(
            ReviewInboxItem(
                source="review_queue",
                priority=getattr(card, "priority", "P2"),
                category=getattr(card, "category", ""),
                subject_id=getattr(card, "subject_id", getattr(card, "memory_id", "")),
                title=getattr(card, "title", ""),
                command=getattr(card, "command", ""),
            )
        )
    for card in handoffs.cards:
        items.append(
            ReviewInboxItem(
                source="team_handoff",
                priority=getattr(card, "priority", "P2"),
                category=getattr(card, "category", ""),
                subject_id=getattr(card, "subject_id", ""),
                title=getattr(card, "title", ""),
                command=getattr(card, "command", ""),
            )
        )
    for reminder in reminders.reminders:
        items.append(
            ReviewInboxItem(
                source="reminder",
                priority=reminder.priority,
                category=reminder.category,
                subject_id=reminder.subject_id,
                title=reminder.title,
                command=reminder.command,
            )
        )
    return ReviewInboxReport(root=str(Path(root)), source="live-stores", items=sort_items(items))


def review_inbox_from_export(path: Path | str) -> ReviewInboxReport:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != REVIEW_EXPORT_VERSION:
        raise ValueError("review inbox input must be a cmu-review-export/v1 payload")
    items: list[ReviewInboxItem] = []
    items.extend(export_items(payload.get("review_queue", []), "review_queue"))
    items.extend(export_items(payload.get("team_handoffs", []), "team_handoff"))
    items.extend(export_items(payload.get("reminders", []), "reminder"))
    return ReviewInboxReport(root=payload.get("root", ""), source=str(Path(path)), items=sort_items(items))


def export_items(records: list[dict[str, Any]], source: str) -> list[ReviewInboxItem]:
    items: list[ReviewInboxItem] = []
    for record in records:
        items.append(
            ReviewInboxItem(
                source=source,
                priority=str(record.get("priority", "P2")),
                category=str(record.get("category", "")),
                subject_id=str(record.get("subject_id", record.get("memory_id", ""))),
                title=str(record.get("title", record.get("message", ""))),
                command=str(record.get("command", record.get("follow_up_command", ""))),
            )
        )
    return items


def sort_items(items: list[ReviewInboxItem]) -> list[ReviewInboxItem]:
    return sorted(items, key=lambda item: (PRIORITY_ORDER.get(item.priority, 9), item.source, item.category, item.title))
