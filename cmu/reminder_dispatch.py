from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

from .models import utc_now


REMINDER_DISPATCH_VERSION = "cmu-reminder-dispatch/v1"


@dataclass(frozen=True)
class ReminderDispatchItem:
    delivery_id: str
    reminder_count: int
    urgent_count: int


@dataclass(frozen=True)
class ReminderDispatchReport:
    outbox: Path
    dispatch_log: Path
    apply: bool
    dispatched: list[ReminderDispatchItem] = field(default_factory=list)
    skipped: int = 0

    def render(self) -> str:
        lines = [
            "CMU Reminder Dispatch",
            f"Version: {REMINDER_DISPATCH_VERSION}",
            "Mode: local notification dispatch adapter from reminder outbox to durable dispatch log.",
            f"Outbox: {self.outbox}",
            f"Dispatch Log: {self.dispatch_log}",
            f"Applied: {'yes' if self.apply else 'no'}",
            f"Dispatched: {len(self.dispatched)}",
            f"Skipped Already Delivered: {self.skipped}",
        ]
        if self.dispatched:
            lines.append("")
            lines.extend(
                f"- {item.delivery_id}: reminders={item.reminder_count} urgent={item.urgent_count}"
                for item in self.dispatched
            )
        lines.extend(
            [
                "",
                "Proof Meaning: reminder notifications now have an idempotent local dispatch step beyond writing machine-readable payloads to an outbox.",
            ]
        )
        return "\n".join(lines)


def dispatch_reminder_outbox(
    root: Path | str,
    *,
    outbox: Path | str | None = None,
    dispatch_log: Path | str | None = None,
    apply: bool = False,
) -> ReminderDispatchReport:
    root_path = Path(root)
    outbox_path = Path(outbox) if outbox is not None else root_path / ".cmu" / "reminder_outbox.jsonl"
    log_path = Path(dispatch_log) if dispatch_log is not None else root_path / ".cmu" / "reminder_dispatch.jsonl"
    delivered = read_delivered_ids(log_path)
    dispatched: list[ReminderDispatchItem] = []
    skipped = 0
    for event in read_jsonl(outbox_path):
        delivery_id = str(event.get("delivery_id", ""))
        if not delivery_id:
            continue
        if delivery_id in delivered:
            skipped += 1
            continue
        payload = event.get("payload", {}) if isinstance(event.get("payload"), dict) else {}
        summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
        item = ReminderDispatchItem(
            delivery_id=delivery_id,
            reminder_count=int(summary.get("total", 0)),
            urgent_count=int(summary.get("urgent", 0)),
        )
        dispatched.append(item)
        if apply:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "schema": REMINDER_DISPATCH_VERSION,
                "delivery_id": delivery_id,
                "dispatched_at": utc_now(),
                "channel": event.get("channel", "local-jsonl"),
                "summary": {"total": item.reminder_count, "urgent": item.urgent_count},
            }
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
            delivered.add(delivery_id)
    return ReminderDispatchReport(outbox=outbox_path, dispatch_log=log_path, apply=apply, dispatched=dispatched, skipped=skipped)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
    return events


def read_delivered_ids(path: Path) -> set[str]:
    return {str(item.get("delivery_id", "")) for item in read_jsonl(path) if item.get("delivery_id")}
