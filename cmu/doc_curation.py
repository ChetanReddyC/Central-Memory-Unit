from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess

from .models import Memory, MemoryScope, MemoryType, utc_now
from .remembering import RememberDecision, RememberRequest, remember_candidate


SUPERSEDED_MARKERS = {
    "obsolete",
    "superseded",
    "deprecated",
    "no longer current",
    "out of date",
    "old plan",
    "replaced by",
}

REUSABLE_MARKERS = {
    "decision",
    "practice",
    "anti-pattern",
    "question",
    "known gap",
    "unfinished",
    "next best",
    "authority",
    "evidence",
    "governance",
    "lifecycle",
    "retrieval",
    "integration",
    "drag",
    "readiness",
}


@dataclass(frozen=True)
class DocumentEvidence:
    path: str
    title: str
    excerpt: str
    last_changed_at: str
    age_days: int | None
    source: str
    superseded_markers: list[str]
    reusable_markers: list[str]


@dataclass
class DocumentCurationDecision:
    evidence: DocumentEvidence
    status: str
    reason: str
    decision: RememberDecision | None = None
    applied: bool = False

    @property
    def memory(self) -> Memory | None:
        return self.decision.memory if self.decision else None

    def render(self) -> str:
        memory_id = self.memory.id if self.memory is not None else "none"
        age = f"{self.evidence.age_days}d" if self.evidence.age_days is not None else "unknown"
        markers = ", ".join(self.evidence.reusable_markers[:5]) if self.evidence.reusable_markers else "none"
        lines = [
            f"- {self.evidence.path}: {self.status}",
            f"  Title: {self.evidence.title or 'Untitled markdown'}",
            f"  Age: {age} via {self.evidence.source}",
            f"  Reusable Signals: {markers}",
            f"  Reason: {self.reason}",
            f"  Candidate: {memory_id}",
        ]
        if self.status == "candidate-ready":
            lines.append(f"  Applied: {'yes' if self.applied else 'no'}")
        if self.evidence.superseded_markers:
            lines.append(f"  Supersession Markers: {', '.join(self.evidence.superseded_markers)}")
        return "\n".join(lines)


@dataclass
class DocumentCurationReport:
    decisions: list[DocumentCurationDecision]
    apply: bool = False
    stale_days: int = 120
    selected: list[str] | None = None

    def render(self) -> str:
        selected = clean_selectors(self.selected or [])
        lines = [
            "CMU Document History Curation",
            "Mode: apply" if self.apply else "Mode: preview; no Candidate Memories are mutated.",
            f"Stale Gate: documents older than {self.stale_days} days are rejected unless explicitly allowed.",
            "",
            "Summary:",
            f"- Documents Reviewed: {len(self.decisions)}",
            f"- Candidate Ready: {sum(1 for item in self.decisions if item.status == 'candidate-ready')}",
            f"- Stale Rejected: {sum(1 for item in self.decisions if item.status == 'stale-rejected')}",
            f"- Superseded Rejected: {sum(1 for item in self.decisions if item.status == 'superseded-rejected')}",
            f"- Noise Rejected: {sum(1 for item in self.decisions if item.status == 'noise-rejected')}",
            f"- Duplicates: {sum(1 for item in self.decisions if item.status == 'duplicate')}",
            f"- Applied Candidates: {sum(1 for item in self.decisions if item.applied)}",
        ]
        if selected:
            lines.append(f"- Selection Filter: {', '.join(selected)}")
        lines.extend(
            [
            "",
            "Curation Lines:",
            ]
        )
        if not self.decisions:
            lines.append("- None")
        else:
            for decision in self.decisions:
                lines.append(decision.render())
        lines.extend(
            [
                "",
                "Proof Meaning: markdown is treated as evidence, not authority; stale or superseded docs do not become active guidance without deliberate review.",
            ]
        )
        return "\n".join(lines)


def curate_documents(
    root: Path | str,
    paths: list[str],
    existing_memories: list[Memory],
    *,
    stale_days: int = 120,
    allow_stale: bool = False,
) -> list[DocumentCurationDecision]:
    root_path = Path(root)
    documents = discover_markdown_documents(root_path, paths)
    decisions: list[DocumentCurationDecision] = []
    for document in documents:
        evidence = inspect_document(root_path, document)
        curation = curate_document_evidence(
            evidence,
            existing_memories,
            stale_days=stale_days,
            allow_stale=allow_stale,
        )
        decisions.append(curation)
    return decisions


def apply_selected_curation_decisions(
    decisions: list[DocumentCurationDecision],
    selectors: list[str] | None = None,
) -> list[Memory]:
    selected = clean_selectors(selectors or [])
    applied: list[Memory] = []
    for decision in decisions:
        if decision.status != "candidate-ready" or decision.memory is None:
            continue
        if selected and not decision_matches_selector(decision, selected):
            continue
        decision.applied = True
        applied.append(decision.memory)
    return applied


def curate_document_evidence(
    evidence: DocumentEvidence,
    existing_memories: list[Memory],
    *,
    stale_days: int = 120,
    allow_stale: bool = False,
) -> DocumentCurationDecision:
    if evidence.superseded_markers:
        return DocumentCurationDecision(
            evidence=evidence,
            status="superseded-rejected",
            reason="Document carries supersession/staleness markers; use it only as historical evidence unless a newer source revalidates it.",
        )
    if evidence.age_days is not None and evidence.age_days > stale_days and not allow_stale:
        return DocumentCurationDecision(
            evidence=evidence,
            status="stale-rejected",
            reason="Document is older than the stale gate; cross-check newer docs or Git evidence before drafting memory.",
        )
    if len(evidence.reusable_markers) < 2:
        return DocumentCurationDecision(
            evidence=evidence,
            status="noise-rejected",
            reason="Document does not contain enough durable CMU memory signals for candidate drafting.",
        )

    request = evidence_to_remember_request(evidence)
    decision = remember_candidate(existing_memories, request)
    if not decision.saved:
        status = "duplicate" if "duplicate" in decision.reason.lower() else "noise-rejected"
        return DocumentCurationDecision(evidence=evidence, status=status, reason=decision.reason, decision=decision)
    if decision.memory is not None:
        decision.memory.evidence = [
            *decision.memory.evidence,
            f"Curated from markdown document: {evidence.path}",
            f"Document last changed: {evidence.last_changed_at or 'unknown'} ({evidence.source})",
        ]
    return DocumentCurationDecision(
        evidence=evidence,
        status="candidate-ready",
        reason="Document passed recency, supersession, and reusable-signal gates; saved only as Candidate Memory.",
        decision=decision,
    )


def evidence_to_remember_request(evidence: DocumentEvidence) -> RememberRequest:
    return RememberRequest(
        situation=f"{evidence.title}: {evidence.excerpt}",
        title=f"Curated doc evidence: {evidence.title}"[:96],
        signals=signals_from_markers(evidence.reusable_markers),
        outcome="Markdown curation found reusable project memory evidence that still needs normal CMU review.",
        worked="Treat markdown as evidence to cross-check against current implementation, Git history, and authority before promotion.",
        failed="Do not bulk-import document claims into stable memory or let old markdown guide work without recency and supersession checks.",
        future_use="Use when converting project documentation or historical implementation notes into CMU memory without creating stale-context drag.",
        evidence=[
            f"{evidence.path}: {evidence.excerpt}",
            f"Reusable markers: {', '.join(evidence.reusable_markers)}",
        ],
        liability_score=4,
        suggested_next_type=MemoryType.SITUATION,
        scope=MemoryScope(
            code=[evidence.path],
            workflow=["documentation-curation", "memory-base-cleanup"],
            actor=["developer", "agent"],
            time=[evidence.last_changed_at] if evidence.last_changed_at else [],
        ),
        confidence=0.65,
    )


def inspect_document(root: Path, path: Path) -> DocumentEvidence:
    text = read_text(path)
    normalized = text.lower()
    relative = relative_path(root, path)
    title = markdown_title(text) or path.stem
    excerpt = first_meaningful_excerpt(text)
    last_changed_at, source = last_changed(root, path)
    age_days = age_in_days(last_changed_at)
    superseded = superseded_markers_for(text)
    reusable = sorted(marker for marker in REUSABLE_MARKERS if marker in normalized)
    return DocumentEvidence(
        path=relative,
        title=title,
        excerpt=excerpt,
        last_changed_at=last_changed_at,
        age_days=age_days,
        source=source,
        superseded_markers=superseded,
        reusable_markers=reusable,
    )


def discover_markdown_documents(root: Path, paths: list[str]) -> list[Path]:
    raw_paths = paths or ["."]
    documents: list[Path] = []
    for raw_path in raw_paths:
        candidate = (root / raw_path).resolve() if not Path(raw_path).is_absolute() else Path(raw_path).resolve()
        if candidate.is_file() and candidate.suffix.lower() == ".md":
            documents.append(candidate)
        elif candidate.is_dir():
            documents.extend(
                path
                for path in candidate.rglob("*.md")
                if ".git" not in path.parts and ".cmu" not in path.parts
            )
    return sorted(set(documents), key=lambda item: str(item).lower())


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def markdown_title(text: str) -> str:
    for line in text.splitlines():
        value = line.strip()
        if value.startswith("#"):
            return value.lstrip("#").strip()
    return ""


def first_meaningful_excerpt(text: str, *, limit: int = 220) -> str:
    for line in text.splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        cleaned = re.sub(r"\s+", " ", value)
        return cleaned[:limit]
    return "No meaningful excerpt found."


def superseded_markers_for(text: str) -> list[str]:
    markers: set[str] = set()
    for index, line in enumerate(text.splitlines()):
        without_code = re.sub(r"`[^`]+`", "", line)
        normalized = re.sub(r"\s+", " ", without_code.strip().lower())
        if not normalized:
            continue
        if index > 40 and not any(marker in normalized for marker in SUPERSEDED_MARKERS):
            continue
        if normalized.startswith("#") and any(marker in normalized for marker in SUPERSEDED_MARKERS):
            markers.update(marker for marker in SUPERSEDED_MARKERS if marker in normalized)
            continue
        if any(skip in normalized for skip in {"reject stale", "rejects stale", "reject superseded", "rejects superseded"}):
            continue
        for marker in SUPERSEDED_MARKERS:
            if marker not in normalized:
                continue
            marker_pattern = re.escape(marker)
            if re.search(rf"\b(this|the)\s+(document|doc|file|plan|decision|note|notes)\b.{{0,80}}\b{marker_pattern}\b", normalized):
                markers.add(marker)
            elif re.search(rf"\b{marker_pattern}\b.{{0,80}}\b(document|doc|file|plan|decision|note|notes)\b", normalized):
                markers.add(marker)
    return sorted(markers)


def last_changed(root: Path, path: Path) -> tuple[str, str]:
    git_time = git_last_changed(root, path)
    if git_time:
        return git_time, "git"
    timestamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return timestamp.isoformat(timespec="seconds"), "filesystem"


def git_last_changed(root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return ""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", str(relative)],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def age_in_days(timestamp: str) -> int | None:
    if not timestamp:
        return None
    try:
        changed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if changed.tzinfo is None:
        changed = changed.replace(tzinfo=timezone.utc)
    now = datetime.fromisoformat(utc_now())
    return max(0, (now - changed).days)


def signals_from_markers(markers: list[str]) -> list[str]:
    signals: list[str] = []
    marker_set = set(markers)
    if marker_set & {"decision", "practice", "governance", "authority"}:
        signals.append("tradeoff decision")
    if marker_set & {"anti-pattern", "drag"}:
        signals.append("unsafe path avoided")
    if marker_set & {"question", "known gap", "unfinished"}:
        signals.append("ownership ambiguity")
    if marker_set & {"integration", "retrieval", "lifecycle", "readiness"}:
        signals.append("new convention")
    return signals or ["new convention"]


def relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def clean_selectors(selectors: list[str]) -> list[str]:
    return [selector.strip() for selector in selectors if selector.strip()]


def decision_matches_selector(decision: DocumentCurationDecision, selectors: list[str]) -> bool:
    haystacks = {
        decision.evidence.path.lower(),
        decision.evidence.title.lower(),
    }
    if decision.memory is not None:
        haystacks.add(decision.memory.id.lower())
        haystacks.add(decision.memory.title.lower())
    for selector in selectors:
        normalized = selector.lower()
        if normalized in haystacks:
            return True
        if any(normalized in value for value in haystacks):
            return True
    return False
