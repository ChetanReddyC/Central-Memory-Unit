from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path


EVIDENCE_SERVICE_INSTALL_VERSION = "cmu-evidence-service-install/v1"
INSTALL_TARGETS = {"systemd-user", "windows-task", "launchd"}


@dataclass(frozen=True)
class EvidenceServiceInstallFile:
    path: Path
    content: str


@dataclass(frozen=True)
class EvidenceServiceInstallReport:
    root: str
    target: str
    output: Path
    wrote: bool
    files: list[EvidenceServiceInstallFile] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "CMU Evidence Service Install",
            f"Version: {EVIDENCE_SERVICE_INSTALL_VERSION}",
            "Mode: service-manager wrapper generation for cmu evidence-service; no OS service is registered by preview.",
            f"Root: {self.root}",
            f"Target: {self.target}",
            f"Output: {self.output}",
            f"Wrote: {'yes' if self.wrote else 'no'}",
            "",
            "Files:",
        ]
        lines.extend(f"- {item.path}" for item in self.files) if self.files else lines.append("- None")
        lines.extend(
            [
                "",
                "Proof Meaning: evidence-service now has concrete OS/service-manager wrapper artifacts that supervisors can install instead of hand-typing daemon commands.",
            ]
        )
        return "\n".join(lines)


def evidence_service_install(
    root: Path | str,
    *,
    target: str = "systemd-user",
    output: Path | str = ".cmu/service-wrappers",
    interval_seconds: float = 60.0,
    apply: bool = True,
    record: bool = True,
    write: bool = False,
) -> EvidenceServiceInstallReport:
    normalized = target.strip().lower()
    if normalized not in INSTALL_TARGETS:
        raise ValueError(f"unknown evidence service install target: {target}")
    if interval_seconds < 0:
        raise ValueError("evidence service install interval cannot be negative")
    root_path = Path(root)
    output_path = Path(output)
    target_dir = output_path if output_path.is_absolute() else root_path / output_path
    files = wrapper_files(root_path, target_dir, normalized, interval_seconds=interval_seconds, apply=apply, record=record)
    if write:
        target_dir.mkdir(parents=True, exist_ok=True)
        for item in files:
            item.path.parent.mkdir(parents=True, exist_ok=True)
            item.path.write_text(item.content, encoding="utf-8")
    return EvidenceServiceInstallReport(root=str(root_path), target=normalized, output=target_dir, wrote=write, files=files)


def wrapper_files(
    root: Path,
    output: Path,
    target: str,
    *,
    interval_seconds: float,
    apply: bool,
    record: bool,
) -> list[EvidenceServiceInstallFile]:
    command = evidence_command(root, interval_seconds=interval_seconds, apply=apply, record=record)
    metadata = {
        "schema": EVIDENCE_SERVICE_INSTALL_VERSION,
        "target": target,
        "root": str(root),
        "command": command,
        "install_note": install_note(target, output),
    }
    files = [EvidenceServiceInstallFile(output / "cmu-evidence-service.install.json", json.dumps(metadata, indent=2, sort_keys=True) + "\n")]
    if target == "systemd-user":
        files.append(
            EvidenceServiceInstallFile(
                output / "cmu-evidence-service.service",
                "\n".join(
                    [
                        "[Unit]",
                        "Description=CMU evidence service",
                        "",
                        "[Service]",
                        f"WorkingDirectory={root}",
                        f"ExecStart={command}",
                        "Restart=on-failure",
                        "RestartSec=30",
                        "",
                        "[Install]",
                        "WantedBy=default.target",
                        "",
                    ]
                ),
            )
        )
    elif target == "windows-task":
        files.append(
            EvidenceServiceInstallFile(
                output / "cmu-evidence-service-task.ps1",
                "\n".join(
                    [
                        "$ErrorActionPreference = 'Stop'",
                        f"Set-Location -LiteralPath {quote_powershell(str(root))}",
                        command,
                        "",
                    ]
                ),
            )
        )
    elif target == "launchd":
        files.append(
            EvidenceServiceInstallFile(
                output / "com.cmu.evidence-service.plist",
                "\n".join(
                    [
                        '<?xml version="1.0" encoding="UTF-8"?>',
                        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
                        '<plist version="1.0">',
                        "<dict>",
                        "  <key>Label</key><string>com.cmu.evidence-service</string>",
                        "  <key>ProgramArguments</key>",
                        "  <array>",
                        "    <string>python</string>",
                        "    <string>-m</string>",
                        "    <string>cmu</string>",
                        "    <string>--root</string>",
                        f"    <string>{root}</string>",
                        "    <string>evidence-service</string>",
                        "  </array>",
                        f"  <key>WorkingDirectory</key><string>{root}</string>",
                        "  <key>RunAtLoad</key><true/>",
                        "  <key>KeepAlive</key><true/>",
                        "</dict>",
                        "</plist>",
                        "",
                    ]
                ),
            )
        )
    return files


def evidence_command(root: Path, *, interval_seconds: float, apply: bool, record: bool) -> str:
    parts = ["python", "-m", "cmu", "--root", str(root), "evidence-service", "--interval", f"{interval_seconds:g}"]
    if apply:
        parts.append("--apply")
    if not record:
        parts.extend(["--no-session-record", "--no-service-record"])
    return " ".join(quote_command_part(part) for part in parts)


def install_note(target: str, output: Path) -> str:
    if target == "systemd-user":
        return f"Copy {output / 'cmu-evidence-service.service'} to the user systemd unit directory, then enable it with systemctl --user."
    if target == "windows-task":
        return f"Register {output / 'cmu-evidence-service-task.ps1'} with Task Scheduler under the user account that owns this CMU store."
    return f"Load {output / 'com.cmu.evidence-service.plist'} with launchctl for the user that owns this CMU store."


def quote_command_part(value: str) -> str:
    if not value or any(char.isspace() for char in value):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def quote_powershell(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
