from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .evidence_monitor import EvidenceMonitorReport, monitor_checkpoints
from .fixture_repos import FIXTURE_KINDS
from .models import Memory
from .portable_compat import PortableCompatReport, portable_compat_report
from .review_reminders import ReviewRemindersReport, review_reminders
from .team_directory import TeamScopeRecord, team_directory_report
from .usage import MemoryUseReceipt


HARDENING_CYCLE_VERSION = "cmu-hardening-cycle/v1"


@dataclass(frozen=True)
class HardeningCycleItem:
    name: str
    status: str
    detail: str
    command: str

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    def render(self) -> str:
        return f"- [{self.status}] {self.name}: {self.detail}\n  Command: {self.command}"


@dataclass
class HardeningCycleReport:
    root: str
    items: list[HardeningCycleItem] = field(default_factory=list)
    evidence: EvidenceMonitorReport | None = None
    portable: PortableCompatReport | None = None
    reminders: ReviewRemindersReport | None = None

    @property
    def passed(self) -> bool:
        return bool(self.items) and all(item.passed for item in self.items)

    def render(self) -> str:
        lines = [
            "CMU Hardening Cycle",
            f"Version: {HARDENING_CYCLE_VERSION}",
            "Mode: read-only five-surface operator gate; no memories, receipts, team scopes, portable fixtures, or Git checkpoints are mutated.",
            f"Root: {self.root}",
            f"Status: {'pass' if self.passed else 'review'}",
            "",
            "Cycle Items:",
        ]
        lines.extend(item.render() for item in self.items) if self.items else lines.append("- None")
        if self.evidence is not None:
            lines.extend(
                [
                    "",
                    "Evidence Monitor Snapshot:",
                    f"- linked={self.evidence.linked_count} needs_review={self.evidence.review_count} skipped={self.evidence.skipped_count}",
                ]
            )
        if self.portable is not None:
            lines.extend(
                [
                    "",
                    "Portable Compatibility Snapshot:",
                    f"- fixtures={len(self.portable.fixtures)} status={'pass' if self.portable.passed else 'fail'}",
                ]
            )
        if self.reminders is not None:
            lines.extend(
                [
                    "",
                    "Review Reminder Snapshot:",
                    f"- reminders={len(self.reminders.reminders)} p0={sum(1 for reminder in self.reminders.reminders if reminder.priority == 'P0')} p1={sum(1 for reminder in self.reminders.reminders if reminder.priority == 'P1')}",
                ]
            )
        lines.extend(
            [
                "",
                "Proof Meaning: CMU can now run one cautious hardening gate across team ownership review, "
                "checkpoint evidence monitoring, fixture-host-path coverage, portable migration fixtures, "
                "and reminder delivery readiness without using shortcuts or mutating trust state.",
            ]
        )
        return "\n".join(lines)


def hardening_cycle_report(
    root: Path | str,
    memories: list[Memory],
    receipts: list[MemoryUseReceipt],
    *,
    team_scopes: list[TeamScopeRecord],
    portable_fixture_dir: Path | str | None = None,
    evidence_limit: int = 20,
    evidence_hours: int = 72,
    reminder_days: int = 14,
) -> HardeningCycleReport:
    root_path = Path(root)
    team_report = team_directory_report(team_scopes, memories)
    evidence_report = monitor_checkpoints(root_path, memories, receipts, limit=evidence_limit, hours=evidence_hours, apply=False)
    portable_report = portable_compat_report(portable_fixture_dir) if portable_fixture_dir else None
    reminder_report = review_reminders(memories, receipts, team_scopes=team_scopes, days=reminder_days)
    items = [
        team_owner_review_item(team_report),
        evidence_monitor_item(evidence_report),
        fixture_catalog_item(),
        portable_compat_item(portable_report),
        reminder_delivery_item(reminder_report),
    ]
    return HardeningCycleReport(
        root=str(root_path),
        items=items,
        evidence=evidence_report,
        portable=portable_report,
        reminders=reminder_report,
    )


def team_owner_review_item(report) -> HardeningCycleItem:
    if not report.records:
        return HardeningCycleItem(
            name="team-owner-review",
            status="review",
            detail="no local team-scope records exist, so owner/team review flow cannot be checked",
            command="cmu team-scope-add --repo <repo> --team <team> --owner <owner> --authority-role owner --consequence high",
        )
    incomplete = [coverage.record.id for coverage in report.coverage if any(axis in coverage.missing_axes for axis in ["owner", "authority_role", "consequence"])]
    if incomplete:
        return HardeningCycleItem(
            name="team-owner-review",
            status="review",
            detail="team-scope records missing owner or authority review metadata: " + ", ".join(incomplete),
            command="cmu team-scope",
        )
    return HardeningCycleItem(
        name="team-owner-review",
        status="pass",
        detail=f"{len(report.records)} team-scope record(s) carry owner, authority role, and consequence metadata",
        command="cmu team-scope",
    )


def evidence_monitor_item(report: EvidenceMonitorReport) -> HardeningCycleItem:
    if report.error:
        return HardeningCycleItem(
            name="evidence-session-monitor",
            status="review",
            detail=f"checkpoint monitor could not inspect Git evidence: {report.error}",
            command="cmu evidence-monitor",
        )
    if report.review_count:
        return HardeningCycleItem(
            name="evidence-session-monitor",
            status="review",
            detail=f"{report.review_count} receipt(s) need human review before automatic linking",
            command="cmu evidence-monitor",
        )
    return HardeningCycleItem(
        name="evidence-session-monitor",
        status="pass",
        detail=f"dry-run monitor completed with {report.linked_count} clean link candidate(s) and no review blockers",
        command="cmu evidence-monitor",
    )


def fixture_catalog_item() -> HardeningCycleItem:
    kinds = sorted(FIXTURE_KINDS)
    if len(kinds) < 2:
        return HardeningCycleItem(
            name="fixture-host-path-catalog",
            status="review",
            detail="fixture catalog still has fewer than two host-path fixture kinds",
            command="cmu fixture-repo-create --kind <kind> --output <dir>",
        )
    return HardeningCycleItem(
        name="fixture-host-path-catalog",
        status="pass",
        detail="fixture catalog includes: " + ", ".join(kinds),
        command="cmu fixture-repo-create --kind billing-incident --output .manual/fixtures/billing-incident",
    )


def portable_compat_item(report: PortableCompatReport | None) -> HardeningCycleItem:
    if report is None:
        return HardeningCycleItem(
            name="portable-migration-fixtures",
            status="review",
            detail="portable fixture directory was not supplied, so compatibility cannot be proven",
            command="cmu hardening-cycle --portable-fixture-dir <fixture-dir>",
        )
    if not report.passed:
        return HardeningCycleItem(
            name="portable-migration-fixtures",
            status="review",
            detail=f"portable compatibility has {len(report.errors)} error(s)",
            command=f"cmu portable-compat --fixture-dir {report.fixture_dir}",
        )
    return HardeningCycleItem(
        name="portable-migration-fixtures",
        status="pass",
        detail=f"{len(report.fixtures)} portable fixture(s) passed current, invalid, and future-schema expectations",
        command=f"cmu portable-compat --fixture-dir {report.fixture_dir}",
    )


def reminder_delivery_item(report: ReviewRemindersReport) -> HardeningCycleItem:
    urgent = sum(1 for reminder in report.reminders if reminder.priority in {"P0", "P1"})
    if not report.delivery_ready:
        return HardeningCycleItem(
            name="review-reminder-delivery",
            status="review",
            detail="reminder digest has at least one reminder without a subject id or follow-up command",
            command="cmu review-reminders --json",
        )
    return HardeningCycleItem(
        name="review-reminder-delivery",
        status="pass",
        detail=f"machine-readable reminder digest generated with {len(report.reminders)} reminder(s), including {urgent} urgent item(s)",
        command="cmu review-reminders --json",
    )
