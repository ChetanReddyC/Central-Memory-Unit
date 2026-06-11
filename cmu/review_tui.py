from __future__ import annotations

from dataclasses import dataclass, field
import sys
from typing import Callable

from .models import Memory, MemoryType
from .promotion import PromotionDecision, promote_memory
from .review_queue import ReviewQueueCard, ReviewQueueReport, review_queue
from .store import MemoryStore
from .team_directory import TeamScopeRecord
from .usage import MemoryUseReceipt


REVIEW_TUI_VERSION = "cmu-review-tui/v1"


@dataclass(frozen=True)
class ReviewTuiApplyResult:
    applied: bool
    reason: str
    changed_memory: Memory | None = None
    missing: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReviewTuiReport:
    cards: list[ReviewQueueCard]
    selected_index: int = 0
    apply: bool = False
    result: ReviewTuiApplyResult | None = None

    def render(self) -> str:
        lines = [
            "CMU Terminal Review",
            f"Version: {REVIEW_TUI_VERSION}",
            "Mode: terminal approval surface; preview by default, apply only when explicitly requested.",
            "",
            "Summary:",
            f"- Cards: {len(self.cards)}",
            f"- Selected: {self.selected_index if self.selected_index else 'none'}",
            f"- Apply: {'yes' if self.apply else 'no'}",
            "",
            "Cards:",
        ]
        if not self.cards:
            lines.append("- None")
        for index, card in enumerate(self.cards, start=1):
            marker = ">" if index == self.selected_index else " "
            lines.extend(
                [
                    f"{marker} [{index}] {card.priority} {card.category}: {card.title}",
                    f"    Subject: {card.memory_id}",
                    f"    Reason: {card.reason}",
                    f"    Evidence: {card.evidence or 'none'}",
                    f"    Suggested: {card.command}",
                    f"    Terminal Action: {action_help(card)}",
                ]
            )
        if self.result is not None:
            lines.extend(
                [
                    "",
                    "Apply Result:",
                    f"- Applied: {'yes' if self.result.applied else 'no'}",
                    f"- Reason: {self.result.reason}",
                ]
            )
            if self.result.changed_memory is not None:
                memory = self.result.changed_memory
                lines.append(f"- Memory: {memory.id} [{memory.type.value}] {memory.title}")
            if self.result.missing:
                lines.append(f"- Missing: {', '.join(self.result.missing)}")
        lines.extend(
            [
                "",
                "Proof Meaning: promotion and authority review can now happen from a focused terminal surface while still using the same explicit gates and approval metadata as the lower-level commands.",
            ]
        )
        return "\n".join(lines)


def terminal_review(
    store: MemoryStore,
    receipts: list[MemoryUseReceipt],
    team_scopes: list[TeamScopeRecord],
    *,
    select: int = 0,
    apply: bool = False,
    approved_by: str = "",
    authority_owner: str = "",
    approver_role: str = "",
    consequence: str = "",
    review_due: str = "",
    interactive: bool = False,
    input_fn: Callable[[str], str] | None = None,
    key_reader: Callable[[], str] | None = None,
    output_fn: Callable[[str], None] | None = None,
) -> ReviewTuiReport:
    memories = store.list()
    queue = review_queue(memories, receipts, team_scopes)
    if interactive:
        selected, result = interactive_review_session(
            store,
            queue,
            approved_by=approved_by,
            authority_owner=authority_owner,
            approver_role=approver_role,
            consequence=consequence,
            review_due=review_due,
            input_fn=input_fn,
            key_reader=key_reader,
            output_fn=output_fn,
        )
        return ReviewTuiReport(cards=queue.cards, selected_index=selected, apply=bool(result and result.applied), result=result)
    selected = select or interactive_select(queue, input_fn, interactive=False, key_reader=key_reader, output_fn=output_fn)
    result = None
    if selected:
        result = apply_selected_card(
            store,
            queue,
            selected,
            apply=apply,
            approved_by=approved_by,
            authority_owner=authority_owner,
            approver_role=approver_role,
            consequence=consequence,
            review_due=review_due,
        )
    return ReviewTuiReport(cards=queue.cards, selected_index=selected, apply=apply, result=result)


def interactive_review_session(
    store: MemoryStore,
    queue: ReviewQueueReport,
    *,
    approved_by: str = "",
    authority_owner: str = "",
    approver_role: str = "",
    consequence: str = "",
    review_due: str = "",
    input_fn: Callable[[str], str] | None = None,
    key_reader: Callable[[], str] | None = None,
    output_fn: Callable[[str], None] | None = None,
) -> tuple[int, ReviewTuiApplyResult | None]:
    if not queue.cards:
        return 0, None
    write = output_fn or (lambda text: print(text, end="", flush=True))
    selected = arrow_key_select(queue, key_reader=key_reader, output_fn=write)
    if not selected:
        return 0, ReviewTuiApplyResult(False, "cancelled from review inbox")
    card = queue.cards[selected - 1]
    action = arrow_key_action_select(card, key_reader=key_reader, output_fn=write)
    if action in {"back", "cancel"}:
        return selected, ReviewTuiApplyResult(False, "cancelled before applying")
    if action == "skip":
        return selected, ReviewTuiApplyResult(False, "skipped; no memory was changed")
    if action == "keep":
        return selected, ReviewTuiApplyResult(False, "kept as Situation; no stable approval applied")
    if action != "approve":
        return selected, ReviewTuiApplyResult(False, f"unsupported terminal action: {action}")
    approval = collect_approval_fields(
        card,
        approved_by=approved_by,
        authority_owner=authority_owner,
        approver_role=approver_role,
        consequence=consequence,
        review_due=review_due,
        input_fn=input_fn,
        output_fn=write,
    )
    if approval is None:
        return selected, ReviewTuiApplyResult(False, "approval metadata entry cancelled")
    if not arrow_key_confirm(card, approval, key_reader=key_reader, output_fn=write):
        return selected, ReviewTuiApplyResult(False, "confirmation cancelled; no memory was changed")
    return selected, apply_selected_card(store, queue, selected, apply=True, **approval)


def interactive_select(
    queue: ReviewQueueReport,
    input_fn: Callable[[str], str] | None,
    *,
    interactive: bool = False,
    key_reader: Callable[[], str] | None = None,
    output_fn: Callable[[str], None] | None = None,
) -> int:
    if not queue.cards:
        return 0
    if interactive:
        return arrow_key_select(queue, key_reader=key_reader, output_fn=output_fn)
    if input_fn is None:
        return 0
    raw = input_fn("Select review card number, or press Enter to preview only: ").strip()
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


def arrow_key_select(
    queue: ReviewQueueReport,
    *,
    key_reader: Callable[[], str] | None = None,
    output_fn: Callable[[str], None] | None = None,
) -> int:
    if not queue.cards:
        return 0
    reader = key_reader or read_key
    write = output_fn or (lambda text: print(text, end="", flush=True))
    selected = 0
    while True:
        write(render_selector(queue, selected))
        key = reader()
        if key in {"up", "k"}:
            selected = (selected - 1) % len(queue.cards)
        elif key in {"down", "j"}:
            selected = (selected + 1) % len(queue.cards)
        elif key in {"enter", "space"}:
            write("\n")
            return selected + 1
        elif key in {"quit", "escape"}:
            write("\n")
            return 0


def render_selector(queue: ReviewQueueReport, selected: int) -> str:
    lines = [
        "\033[2J\033[H",
        "CMU Review",
        "",
    ]
    for index, card in enumerate(queue.cards):
        prefix = ">" if index == selected else " "
        text = f"{prefix} {card.priority} {card.category:<20} {card.title}"
        if index == selected:
            text = f"\033[7m{text}\033[0m"
        lines.append(text)
    card = queue.cards[selected]
    lines.extend(
        [
            "",
            "Selected Card",
            f"Subject: {card.memory_id}",
            f"Reason: {card.reason}",
            f"Evidence: {card.evidence or 'none'}",
            "",
            "Keys: up/down or j/k move  Enter select  q/Esc cancel",
        ]
    )
    return "\n".join(lines)


def read_key() -> str:
    if sys.platform.startswith("win"):
        return read_windows_key()
    return read_posix_key()


def read_windows_key() -> str:
    import msvcrt

    key = msvcrt.getwch()
    if key in {"\x00", "\xe0"}:
        code = msvcrt.getwch()
        return {"H": "up", "P": "down"}.get(code, "")
    return normalize_key(key)


def read_posix_key() -> str:
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        key = sys.stdin.read(1)
        if key == "\x1b":
            rest = sys.stdin.read(2)
            return {"[A": "up", "[B": "down"}.get(rest, "escape")
        return normalize_key(key)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def normalize_key(key: str) -> str:
    if key in {"\r", "\n"}:
        return "enter"
    if key == " ":
        return "space"
    if key in {"q", "Q"}:
        return "quit"
    if key == "\x1b":
        return "escape"
    if key in {"j", "J"}:
        return "j"
    if key in {"k", "K"}:
        return "k"
    return key


def arrow_key_action_select(
    card: ReviewQueueCard,
    *,
    key_reader: Callable[[], str] | None = None,
    output_fn: Callable[[str], None] | None = None,
) -> str:
    actions = actions_for_card(card)
    reader = key_reader or read_key
    write = output_fn or (lambda text: print(text, end="", flush=True))
    selected = 0
    while True:
        write(render_action_selector(card, actions, selected))
        key = reader()
        if key in {"up", "k"}:
            selected = (selected - 1) % len(actions)
        elif key in {"down", "j"}:
            selected = (selected + 1) % len(actions)
        elif key in {"enter", "space"}:
            write("\n")
            return actions[selected][0]
        elif key in {"quit", "escape"}:
            write("\n")
            return "cancel"


def actions_for_card(card: ReviewQueueCard) -> list[tuple[str, str]]:
    if card.category == "candidate-promotion":
        return [("approve", "Promote to Situation"), ("skip", "Skip"), ("back", "Back")]
    if card.category == "practice-approval":
        return [("approve", "Approve as Practice"), ("keep", "Keep as Situation"), ("skip", "Skip"), ("back", "Back")]
    if card.category == "anchor-approval":
        return [("approve", "Approve as Anchor"), ("keep", "Keep as Situation"), ("skip", "Skip"), ("back", "Back")]
    return [("skip", "Skip"), ("back", "Back")]


def render_action_selector(card: ReviewQueueCard, actions: list[tuple[str, str]], selected: int) -> str:
    lines = [
        "\033[2J\033[H",
        "CMU Review Card",
        "",
        f"{card.priority} {card.category}: {card.title}",
        f"Subject: {card.memory_id}",
        f"Reason: {card.reason}",
        f"Evidence: {card.evidence or 'none'}",
        f"Suggested: {card.command}",
        "",
        "Actions:",
    ]
    for index, (_, label) in enumerate(actions):
        prefix = ">" if index == selected else " "
        text = f"{prefix} {label}"
        if index == selected:
            text = f"\033[7m{text}\033[0m"
        lines.append(text)
    lines.extend(["", "Keys: up/down or j/k move  Enter choose  q/Esc cancel"])
    return "\n".join(lines)


def collect_approval_fields(
    card: ReviewQueueCard,
    *,
    approved_by: str,
    authority_owner: str,
    approver_role: str,
    consequence: str,
    review_due: str,
    input_fn: Callable[[str], str] | None,
    output_fn: Callable[[str], None],
) -> dict[str, str] | None:
    values = {
        "approved_by": approved_by,
        "authority_owner": authority_owner,
        "approver_role": approver_role,
        "consequence": consequence,
        "review_due": review_due,
    }
    if card.category == "candidate-promotion":
        return values
    read = input_fn or input
    output_fn(
        "\033[2J\033[H"
        "Approval Details\n\n"
        f"{card.category}: {card.title}\n"
        "Press Enter to accept defaults. Type q to cancel.\n\n"
    )
    prompts = [
        ("approved_by", "Approved by", "owner-or-team"),
        ("authority_owner", "Authority owner", "owner-or-team"),
        ("approver_role", "Approver role", "owner"),
        ("consequence", "Consequence", "high"),
        ("review_due", "Review due", ""),
    ]
    for name, label, fallback in prompts:
        current = values[name] or fallback
        raw = read(f"{label} [{current}]: ").strip()
        if raw.lower() == "q":
            return None
        values[name] = raw or current
    return values


def arrow_key_confirm(
    card: ReviewQueueCard,
    approval: dict[str, str],
    *,
    key_reader: Callable[[], str] | None = None,
    output_fn: Callable[[str], None] | None = None,
) -> bool:
    options = [("apply", "Apply"), ("cancel", "Cancel")]
    reader = key_reader or read_key
    write = output_fn or (lambda text: print(text, end="", flush=True))
    selected = 0
    while True:
        write(render_confirm(card, approval, options, selected))
        key = reader()
        if key in {"up", "down", "j", "k"}:
            selected = (selected + 1) % len(options)
        elif key in {"enter", "space"}:
            write("\n")
            return options[selected][0] == "apply"
        elif key in {"quit", "escape"}:
            write("\n")
            return False


def render_confirm(card: ReviewQueueCard, approval: dict[str, str], options: list[tuple[str, str]], selected: int) -> str:
    lines = [
        "\033[2J\033[H",
        "Confirm Review Action",
        "",
        f"Card: {card.category} - {card.title}",
        f"Subject: {card.memory_id}",
        f"Approved By: {approval.get('approved_by') or 'not required'}",
        f"Authority Owner: {approval.get('authority_owner') or 'not required'}",
        f"Approver Role: {approval.get('approver_role') or 'not required'}",
        f"Consequence: {approval.get('consequence') or 'not required'}",
        "",
    ]
    for index, (_, label) in enumerate(options):
        prefix = ">" if index == selected else " "
        text = f"{prefix} {label}"
        if index == selected:
            text = f"\033[7m{text}\033[0m"
        lines.append(text)
    lines.extend(["", "Keys: up/down move  Enter choose  q/Esc cancel"])
    return "\n".join(lines)


def apply_selected_card(
    store: MemoryStore,
    queue: ReviewQueueReport,
    selected: int,
    *,
    apply: bool,
    approved_by: str,
    authority_owner: str,
    approver_role: str,
    consequence: str,
    review_due: str,
) -> ReviewTuiApplyResult:
    if selected < 1 or selected > len(queue.cards):
        return ReviewTuiApplyResult(False, f"selected card {selected} is out of range")
    card = queue.cards[selected - 1]
    if card.category == "candidate-promotion":
        return apply_promotion_card(store, card, MemoryType.SITUATION, apply=apply)
    if card.category == "practice-approval":
        return apply_promotion_card(
            store,
            card,
            MemoryType.PRACTICE,
            apply=apply,
            approved_by=approved_by,
            authority_owner=authority_owner,
            approver_role=approver_role,
            consequence=consequence,
            review_due=review_due,
        )
    if card.category == "anchor-approval":
        return apply_promotion_card(
            store,
            card,
            MemoryType.ANCHOR,
            apply=apply,
            approved_by=approved_by,
            authority_owner=authority_owner,
            approver_role=approver_role,
            consequence=consequence,
            review_due=review_due,
        )
    return ReviewTuiApplyResult(False, f"terminal apply is not implemented for {card.category}; use the suggested command")


def apply_promotion_card(
    store: MemoryStore,
    card: ReviewQueueCard,
    target_type: MemoryType,
    *,
    apply: bool,
    approved_by: str = "",
    authority_owner: str = "",
    approver_role: str = "",
    consequence: str = "",
    review_due: str = "",
) -> ReviewTuiApplyResult:
    if not apply:
        return ReviewTuiApplyResult(False, "preview only; pass --apply to mutate the selected card")
    decision: PromotionDecision = promote_memory(
        store.list(),
        card.memory_id,
        target_type,
        approved_by=approved_by,
        authority_owner=authority_owner,
        approver_role=approver_role,
        consequence=consequence,
        review_due_at=review_due,
    )
    if decision.promoted and decision.memory is not None:
        store.update(decision.memory)
        return ReviewTuiApplyResult(True, decision.reason, changed_memory=decision.memory)
    return ReviewTuiApplyResult(False, decision.reason, changed_memory=decision.memory)


def action_help(card: ReviewQueueCard) -> str:
    if card.category == "candidate-promotion":
        return f"cmu review-tui --select <n> --apply  # promotes {card.memory_id} to Situation"
    if card.category in {"practice-approval", "anchor-approval"}:
        target = card.category.split("-", 1)[0]
        return (
            "cmu review-tui --select <n> --apply "
            "--approved-by <owner-or-team> --authority-owner <owner-or-team> "
            "--approver-role owner --consequence high "
            f"# promotes {card.memory_id} to {target.title()}"
        )
    return "use suggested command"
