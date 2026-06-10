from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .models import utc_now
from .review_queue import ReviewQueueReport
from .review_reminders import ReviewRemindersReport
from .team_review_handoff import TeamReviewHandoffReport


REVIEW_EXPORT_VERSION = "cmu-review-export/v1"


@dataclass(frozen=True)
class ReviewExportReport:
    output: Path
    wrote: bool
    queue_cards: int
    handoff_cards: int
    reminders: int

    def render(self) -> str:
        return "\n".join(
            [
                "CMU Review Export",
                f"Version: {REVIEW_EXPORT_VERSION}",
                "Mode: non-CLI review payload for UI, inbox, or workflow adapters; no governance decision is applied.",
                f"Output: {self.output}",
                f"Wrote: {'yes' if self.wrote else 'no'}",
                f"Queue Cards: {self.queue_cards}",
                f"Handoff Cards: {self.handoff_cards}",
                f"Reminders: {self.reminders}",
                "",
                "Proof Meaning: human review moments can now leave CLI prose as a structured payload without promoting, approving, retiring, or mutating memory.",
            ]
        )


def export_review_payload(
    *,
    root: Path | str,
    output: Path | str,
    queue: ReviewQueueReport,
    handoffs: TeamReviewHandoffReport,
    reminders: ReviewRemindersReport,
    write: bool = False,
) -> ReviewExportReport:
    root_path = Path(root)
    output_path = Path(output)
    target_path = output_path if output_path.is_absolute() else root_path / output_path
    payload = {
        "schema": REVIEW_EXPORT_VERSION,
        "created_at": utc_now(),
        "root": str(root_path),
        "read_only": True,
        "summary": {
            "queue_cards": len(queue.cards),
            "handoff_cards": len(handoffs.cards),
            "reminders": len(reminders.reminders),
            "urgent_reminders": reminders.priority_counts()["P0"] + reminders.priority_counts()["P1"],
        },
        "review_queue": [card.__dict__ for card in queue.cards],
        "team_handoffs": [card.__dict__ for card in handoffs.cards],
        "reminders": [reminder.to_dict() for reminder in reminders.reminders],
    }
    if write:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return ReviewExportReport(
        output=target_path,
        wrote=write,
        queue_cards=len(queue.cards),
        handoff_cards=len(handoffs.cards),
        reminders=len(reminders.reminders),
    )
