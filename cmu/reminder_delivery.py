from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .models import utc_now
from .review_reminders import ReviewRemindersReport


REMINDER_DELIVERY_VERSION = "cmu-reminder-delivery/v1"


@dataclass(frozen=True)
class ReminderDeliveryReport:
    path: Path
    channel: str
    apply: bool
    delivery_id: str
    reminder_count: int
    urgent_count: int

    def render(self) -> str:
        lines = [
            "CMU Reminder Delivery",
            f"Version: {REMINDER_DELIVERY_VERSION}",
            "Mode: file-outbox notification adapter; review decisions are not applied.",
            f"Channel: {self.channel}",
            f"Outbox: {self.path}",
            f"Delivery ID: {self.delivery_id}",
            f"Applied: {'yes' if self.apply else 'no'}",
            f"Reminders: {self.reminder_count}",
            f"Urgent: {self.urgent_count}",
            "",
            "Proof Meaning: reminder delivery can now hand scheduler/notification systems a durable outbox event without scraping CLI text or mutating governance state.",
        ]
        return "\n".join(lines)


def deliver_reminders_to_outbox(
    report: ReviewRemindersReport,
    *,
    root: Path | str,
    channel: str = "local-jsonl",
    outbox: Path | str | None = None,
    apply: bool = False,
) -> ReminderDeliveryReport:
    root_path = Path(root)
    target = Path(outbox) if outbox is not None else root_path / ".cmu" / "reminder_outbox.jsonl"
    payload = report.to_delivery_payload()
    delivery_id = f"del_{uuid4().hex[:12]}"
    event = {
        "schema": REMINDER_DELIVERY_VERSION,
        "delivery_id": delivery_id,
        "created_at": utc_now(),
        "channel": channel,
        "payload": payload,
    }
    if apply:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
    return ReminderDeliveryReport(
        path=target,
        channel=channel,
        apply=apply,
        delivery_id=delivery_id,
        reminder_count=int(payload["summary"]["total"]),
        urgent_count=int(payload["summary"]["urgent"]),
    )
