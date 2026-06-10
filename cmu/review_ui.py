from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path

from .review_inbox import REVIEW_INBOX_VERSION, ReviewInboxReport


REVIEW_UI_VERSION = "cmu-review-ui/v1"
REVIEW_UI_ACTION_VERSION = "cmu-review-ui-action/v1"


@dataclass(frozen=True)
class ReviewUiReport:
    root: str
    inbox: ReviewInboxReport
    written: bool
    output_path: str
    html_text: str
    action_packets: list[dict]

    def render(self) -> str:
        lines = [
            "CMU Review UI",
            f"Version: {REVIEW_UI_VERSION}",
            "Mode: read-only UI-backed approval cards; no governance decisions are applied.",
            f"Root: {self.root}",
            f"Cards: {len(self.inbox.items)}",
            f"Urgent: {self.inbox.urgent}",
            f"Action Packets: {len(self.action_packets)}",
            f"Written: {'yes' if self.written else 'no'}",
        ]
        if self.output_path:
            lines.append(f"Output: {self.output_path}")
        lines.append(
            "Proof Meaning: review cards now have a local HTML surface backed by structured inbox data, exact follow-up commands, and controlled action packets for supported approval flows."
        )
        return "\n".join(lines)


def build_review_ui(root: Path | str, inbox: ReviewInboxReport, *, write: bool = False) -> ReviewUiReport:
    root_path = Path(root)
    action_packets = review_action_packets(inbox)
    html_text = render_review_html(inbox, action_packets)
    output_path = root_path / ".cmu" / "review-ui" / "index.html"
    if write:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html_text, encoding="utf-8")
        (output_path.parent / "actions.json").write_text(json.dumps(action_packets, indent=2, sort_keys=True), encoding="utf-8")
    return ReviewUiReport(
        root=str(root_path),
        inbox=inbox,
        written=write,
        output_path=str(output_path) if write else "",
        html_text=html_text,
        action_packets=action_packets,
    )


def render_review_html(inbox: ReviewInboxReport, action_packets: list[dict] | None = None) -> str:
    action_by_subject = {packet["subject_id"]: packet for packet in action_packets or []}
    cards = "\n".join(render_card(item, action_by_subject.get(item.subject_id)) for item in inbox.items) or '<p class="empty">No review cards.</p>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CMU Review UI</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f6f7f9; color: #1c2430; }}
    header {{ padding: 20px 24px; background: #ffffff; border-bottom: 1px solid #d9dee7; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 20px; }}
    h1 {{ margin: 0 0 6px; font-size: 24px; }}
    .summary {{ margin: 0; color: #526071; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }}
    .card {{ background: #ffffff; border: 1px solid #d9dee7; border-radius: 8px; padding: 14px; }}
    .meta {{ font-size: 12px; color: #526071; margin-bottom: 8px; }}
    .title {{ font-size: 16px; font-weight: 700; margin-bottom: 8px; }}
    code {{ display: block; white-space: pre-wrap; overflow-wrap: anywhere; background: #eef1f5; padding: 8px; border-radius: 6px; }}
    .action {{ margin-top: 10px; border-top: 1px solid #e6e9ef; padding-top: 10px; }}
    .empty {{ color: #526071; }}
  </style>
</head>
<body>
  <header>
    <h1>CMU Review UI</h1>
    <p class="summary">Schema {REVIEW_UI_VERSION}; inbox {REVIEW_INBOX_VERSION}; cards {len(inbox.items)}; urgent {inbox.urgent}; action packets {len(action_packets or [])}; controlled apply required.</p>
  </header>
  <main>
    <section class="grid">
{cards}
    </section>
  </main>
</body>
</html>
"""


def render_card(item, action_packet: dict | None = None) -> str:
    action_html = ""
    if action_packet is not None:
        action_html = f"""
        <div class="action">
          <div class="meta">Controlled action packet</div>
          <code>{html.escape(json.dumps(action_packet, sort_keys=True))}</code>
        </div>"""
    return f"""      <article class="card" data-source="{escape_attr(item.source)}" data-category="{escape_attr(item.category)}">
        <div class="meta">{html.escape(item.priority)} - {html.escape(item.source)} - {html.escape(item.category)} - {html.escape(item.subject_id or 'no-subject')}</div>
        <div class="title">{html.escape(item.title)}</div>
        <code>{html.escape(item.command or 'No follow-up command supplied.')}</code>
{action_html}
      </article>"""


def review_action_packets(inbox: ReviewInboxReport) -> list[dict]:
    packets: list[dict] = []
    for item in inbox.items:
        action = action_for_item(item)
        if action:
            packets.append(
                {
                    "schema": REVIEW_UI_ACTION_VERSION,
                    "source": item.source,
                    "category": item.category,
                    "subject_id": item.subject_id,
                    "title": item.title,
                    "priority": item.priority,
                    "action": action,
                    "requires_approval": True,
                    "args": {},
                }
            )
    return packets


def action_for_item(item) -> str:
    if item.category in {"stable-authority-handoff", "authority-approval"}:
        return "authority"
    if item.category == "team-scope-metadata":
        return "team-metadata"
    if item.category in {"active-challenge", "governance-review"}:
        return "challenge"
    if item.category in {"quality-decay", "quality-review"}:
        return "retire"
    return ""


def escape_attr(value: str) -> str:
    return html.escape(value, quote=True)
