from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .models import Memory, MemoryScope, MemoryType
from .onboarding import build_onboarding_seed
from .retrieval import PreflightQuery, build_action_note, rank_memories
from .store import MemoryStore
from .triggers import decide_trigger
from .usage import MemoryUseReceipt, MemoryUseStore, apply_usage_adjustments, link_git_commit, use_summary


DEMO_MEMORY_TITLE = "Quickstart rollback marker check"
DEMO_PROMPT = "debug repeated checkout rollback failure"
DEMO_FILE = "quickstart_demo/rollback_notes.txt"


@dataclass
class QuickstartDemoReport:
    applied: bool
    memory_id: str = ""
    receipt_id: str = ""
    commit_hash: str = ""
    steps: list[str] = field(default_factory=list)
    reason: str = ""
    summary_text: str = ""

    def render(self) -> str:
        lines = [
            "CMU Quickstart Demo",
            "Purpose: prove the memory loop in a small local Git-backed flow.",
            f"Applied: {'yes' if self.applied else 'no'}",
        ]
        if self.reason:
            lines.append(f"Reason: {self.reason}")
        if self.steps:
            lines.append("Steps:")
            lines.extend(f"- {step}" for step in self.steps)
        if self.memory_id:
            lines.append(f"Memory: {self.memory_id}")
        if self.receipt_id:
            lines.append(f"Use Receipt: {self.receipt_id}")
        if self.commit_hash:
            lines.append(f"Git Checkpoint: {self.commit_hash}")
        if self.summary_text:
            lines.extend(["", self.summary_text])
        if not self.applied:
            lines.append("Run with --apply inside a Git repository to create the demo memory, receipt, checkpoint, and link.")
        return "\n".join(lines)


def quickstart_demo(root: Path | str, *, apply: bool = False) -> QuickstartDemoReport:
    root_path = Path(root)
    if not apply:
        return QuickstartDemoReport(
            applied=False,
            reason="dry run",
            steps=[
                "seed one scoped Practice memory",
                "run the real task-start trigger, onboarding, retrieval, and Action Note path",
                "create one Memory Use Receipt",
                "write and commit a tiny demo checkpoint",
                "link the receipt to the Git checkpoint and show usefulness evidence",
            ],
        )
    if not is_git_repository(root_path):
        return QuickstartDemoReport(
            applied=False,
            reason="quickstart demo requires an existing Git repository so receipt evidence can link to a checkpoint",
        )

    store = MemoryStore(root_path)
    use_store = MemoryUseStore(root_path)
    store.init()
    use_store.init()

    memory = ensure_demo_memory(store)
    query = demo_query()
    trigger = decide_trigger(query, repeated_error=True)
    seed = build_onboarding_seed(store.list(), query)
    raw_matches = rank_memories(store.list(), query)
    actionable = [match for match in raw_matches if match.memory.id == memory.id]
    matches = apply_usage_adjustments(actionable, use_store.list())
    if not matches:
        return QuickstartDemoReport(
            applied=False,
            memory_id=memory.id,
            reason="demo memory did not cross retrieval gates; quickstart proof cannot continue",
        )

    note = build_action_note(matches[0])
    receipt = MemoryUseReceipt.create(matches[0].memory, query, matches[0], source_command="start")
    use_store.add(receipt)

    write_demo_checkpoint(root_path, memory, receipt)
    commit_hash = create_demo_commit(root_path)
    if not commit_hash:
        return QuickstartDemoReport(
            applied=False,
            memory_id=memory.id,
            receipt_id=receipt.id,
            reason="Git commit failed; configure Git identity or inspect repository state",
        )
    link = link_git_commit(receipt, root=root_path, ref=commit_hash, note="Linked by cmu quickstart-demo.")
    if not link.linked or link.receipt is None:
        return QuickstartDemoReport(
            applied=False,
            memory_id=memory.id,
            receipt_id=receipt.id,
            commit_hash=commit_hash,
            reason=link.reason,
        )
    use_store.update(link.receipt)
    summary = use_summary(use_store.list(), memory.id)
    return QuickstartDemoReport(
        applied=True,
        memory_id=memory.id,
        receipt_id=receipt.id,
        commit_hash=commit_hash,
        steps=[
            f"trigger decided {trigger.level}",
            f"onboarding used source memory {seed.source_memory_id or 'none'}",
            f"Action Note recognized: {note.recognized_situation}",
            "demo checkpoint committed and linked",
            "usefulness summary rendered from linked receipt evidence",
        ],
        summary_text=summary.render(),
    )


def ensure_demo_memory(store: MemoryStore) -> Memory:
    for memory in store.list(type=MemoryType.PRACTICE):
        if memory.title == DEMO_MEMORY_TITLE:
            return memory
    memory = Memory.create(
        type=MemoryType.PRACTICE,
        title=DEMO_MEMORY_TITLE,
        summary="Repeated checkout rollback failures should verify the shared release marker before retrying.",
        signals=["checkout", "rollback", "release marker", "repeated failure"],
        scope=MemoryScope(code=["quickstart_demo", "release"], workflow=["debugging", "rollback"], actor=["agent"]),
        evidence=["Quickstart fixture encodes the known rollback-marker failure pattern."],
        use_this_path="Inspect the release marker first, then retry rollback only after marker state is understood.",
        avoid_this="Do not keep retrying rollback before checking whether marker state is stale.",
        challenge_only_if="The rollback path no longer reads or writes release marker state.",
        liability_score=4,
        confidence=0.9,
        approved_by="CMU quickstart",
        authority_owner="CMU demo",
        authority_role="owner",
        authority_consequence="high",
    )
    store.add(memory)
    return memory


def demo_query() -> PreflightQuery:
    return PreflightQuery(
        prompt=DEMO_PROMPT,
        actor="agent",
        area="release",
        files=[DEMO_FILE],
        workflow=["debugging", "rollback"],
        environment=["local"],
        risk="high",
    )


def write_demo_checkpoint(root: Path, memory: Memory, receipt: MemoryUseReceipt) -> None:
    path = root / DEMO_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "CMU quickstart demo checkpoint",
                f"memory={memory.id}",
                f"receipt={receipt.id}",
                "action=verified release marker before retrying rollback",
                "",
            ]
        ),
        encoding="utf-8",
    )


def create_demo_commit(root: Path) -> str:
    ensure_demo_git_identity(root)
    run_git(root, ["add", DEMO_FILE])
    result = run_git(root, ["commit", "-m", "CMU quickstart demo proof"], check=False)
    if result.returncode != 0:
        return ""
    return run_git(root, ["rev-parse", "HEAD"]).stdout.strip()


def is_git_repository(root: Path) -> bool:
    return run_git(root, ["rev-parse", "--is-inside-work-tree"], check=False).returncode == 0


def ensure_demo_git_identity(root: Path) -> None:
    if run_git(root, ["config", "user.email"], check=False).returncode != 0:
        run_git(root, ["config", "user.email", "cmu-demo@example.test"])
    if run_git(root, ["config", "user.name"], check=False).returncode != 0:
        run_git(root, ["config", "user.name", "CMU Demo"])


def run_git(root: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], cwd=root, check=False, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result
