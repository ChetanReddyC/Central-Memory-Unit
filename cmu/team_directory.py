from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

from .json_store import read_json, update_json
from .models import Memory, MemoryStatus, utc_now


TEAM_DIRECTORY_VERSION = "cmu-team-directory/v1"


@dataclass
class TeamScopeRecord:
    id: str
    repo: str
    team: str
    owner: str
    code: list[str] = field(default_factory=list)
    workflow: list[str] = field(default_factory=list)
    environment: list[str] = field(default_factory=list)
    authority_role: str = ""
    consequence: str = ""
    created_at: str = field(default_factory=utc_now)

    @classmethod
    def create(
        cls,
        *,
        repo: str,
        team: str,
        owner: str,
        code: list[str] | None = None,
        workflow: list[str] | None = None,
        environment: list[str] | None = None,
        authority_role: str = "",
        consequence: str = "",
    ) -> "TeamScopeRecord":
        return cls(
            id=f"team_{uuid4().hex[:12]}",
            repo=repo.strip(),
            team=team.strip(),
            owner=owner.strip(),
            code=clean_list(code or []),
            workflow=clean_list(workflow or []),
            environment=clean_list(environment or []),
            authority_role=authority_role.strip(),
            consequence=consequence.strip(),
        )

    @classmethod
    def from_dict(cls, data: dict) -> "TeamScopeRecord":
        return cls(
            id=data["id"],
            repo=data.get("repo", ""),
            team=data.get("team", ""),
            owner=data.get("owner", ""),
            code=list(data.get("code", [])),
            workflow=list(data.get("workflow", [])),
            environment=list(data.get("environment", [])),
            authority_role=data.get("authority_role", ""),
            consequence=data.get("consequence", ""),
            created_at=data.get("created_at", utc_now()),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def render_summary(self) -> str:
        scope = []
        if self.code:
            scope.append(f"code={format_list(self.code)}")
        if self.workflow:
            scope.append(f"workflow={format_list(self.workflow)}")
        if self.environment:
            scope.append(f"environment={format_list(self.environment)}")
        authority = ""
        if self.authority_role or self.consequence:
            authority = f" authority={self.authority_role or 'unspecified'}/{self.consequence or 'unspecified'}"
        return f"{self.id} repo={self.repo} team={self.team} owner={self.owner} scope={'; '.join(scope) if scope else 'unscoped'}{authority}"


class TeamDirectoryStore:
    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root)
        self.store_file = self.root / ".cmu" / "team_scopes.json"

    def add(self, record: TeamScopeRecord) -> TeamScopeRecord:
        return update_json(
            self.store_file,
            {"version": 1, "team_scopes": []},
            lambda data: append_record(data, record),
        )

    def list(self) -> list[TeamScopeRecord]:
        return sorted(
            [TeamScopeRecord.from_dict(item) for item in self._read()["team_scopes"]],
            key=lambda item: (item.repo.lower(), item.team.lower(), item.owner.lower()),
        )

    def _read(self) -> dict:
        if not self.store_file.exists():
            return {"version": 1, "team_scopes": []}
        return read_json(self.store_file, {"version": 1, "team_scopes": []})


@dataclass(frozen=True)
class TeamMemoryCoverage:
    record: TeamScopeRecord
    matched_memory_ids: list[str]
    missing_axes: list[str]

    def render(self) -> str:
        matched = format_list(self.matched_memory_ids) if self.matched_memory_ids else "none"
        missing = format_list(self.missing_axes) if self.missing_axes else "none"
        return f"- {self.record.id} {self.record.repo}/{self.record.team}: owner={self.record.owner}; matched={matched}; missing={missing}"


@dataclass
class TeamDirectoryReport:
    records: list[TeamScopeRecord]
    coverage: list[TeamMemoryCoverage]

    def render(self) -> str:
        lines = [
            "CMU Team Scope Directory",
            f"Version: {TEAM_DIRECTORY_VERSION}",
            "Mode: local repo/team boundary map; no memories or receipts are mutated.",
            "",
            "Summary:",
            f"- Team Scope Records: {len(self.records)}",
            f"- Records With Matching Memory: {sum(1 for item in self.coverage if item.matched_memory_ids)}",
            f"- Records Missing Memory Coverage: {sum(1 for item in self.coverage if not item.matched_memory_ids)}",
            "",
            "Team Scopes:",
        ]
        if not self.records:
            lines.append("- None")
        else:
            lines.extend(f"- {record.render_summary()}" for record in self.records)
        lines.append("")
        lines.append("Memory Coverage:")
        if not self.coverage:
            lines.append("- None")
        else:
            lines.extend(item.render() for item in self.coverage)
        lines.extend(
            [
                "",
                "Proof Meaning: CMU can now keep explicit repo/team ownership boundaries next to memory scope so lessons do not silently expand across teams or repositories.",
            ]
        )
        return "\n".join(lines)


def team_directory_report(records: list[TeamScopeRecord], memories: list[Memory]) -> TeamDirectoryReport:
    active = [memory for memory in memories if memory.status == MemoryStatus.ACTIVE]
    coverage = [coverage_for_record(record, active) for record in records]
    return TeamDirectoryReport(records=records, coverage=coverage)


def coverage_for_record(record: TeamScopeRecord, memories: list[Memory]) -> TeamMemoryCoverage:
    matched = [memory.id for memory in memories if memory_matches_record(memory, record)]
    missing_axes = []
    if not record.owner:
        missing_axes.append("owner")
    if not record.code and not record.workflow and not record.environment:
        missing_axes.append("scope")
    if not record.authority_role:
        missing_axes.append("authority_role")
    if not record.consequence:
        missing_axes.append("consequence")
    return TeamMemoryCoverage(record=record, matched_memory_ids=matched, missing_axes=missing_axes)


def memory_matches_record(memory: Memory, record: TeamScopeRecord) -> bool:
    if conflicts(record.code, memory.scope.code):
        return False
    if conflicts(record.workflow, memory.scope.workflow):
        return False
    if conflicts(record.environment, memory.scope.environment):
        return False
    return (
        owner_matches(record.owner, memory.scope.ownership)
        or overlaps(record.code, memory.scope.code)
        or overlaps(record.workflow, memory.scope.workflow)
    )


def append_record(data: dict, record: TeamScopeRecord) -> TeamScopeRecord:
    data["team_scopes"].append(record.to_dict())
    return record


def overlaps(left: list[str], right: list[str]) -> bool:
    normalized_right = [item.lower() for item in right]
    return any(item.lower() in normalized_right for item in left)


def conflicts(left: list[str], right: list[str]) -> bool:
    return bool(left and right and not overlaps(left, right))


def owner_matches(owner: str, owners: list[str]) -> bool:
    if not owner:
        return False
    return owner.lower() in [item.lower() for item in owners]


def clean_list(values: list[str]) -> list[str]:
    return [item.strip() for item in values if item.strip()]


def format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "none"
