from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

from .mcp import MCP_SERVER_NAME


IDE_WORKFLOW_VERSION = "cmu-ide-workflow/v1"


@dataclass(frozen=True)
class IdeWorkflowFile:
    path: Path
    content: str


@dataclass(frozen=True)
class IdeWorkflowReport:
    root: str
    ide: str
    output: Path
    wrote: bool
    files: list[IdeWorkflowFile] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "CMU IDE Workflow",
            f"Version: {IDE_WORKFLOW_VERSION}",
            "Mode: generated IDE workflow artifacts for calling CMU during real coding work.",
            f"IDE: {self.ide}",
            f"Root: {self.root}",
            f"Output: {self.output}",
            f"Wrote: {'yes' if self.wrote else 'no'}",
            "",
            "Files:",
        ]
        lines.extend(f"- {item.path}" for item in self.files)
        lines.extend(
            [
                "",
                "Proof Meaning: IDE/coding-agent setup now has runnable workflow artifacts, not only a setup manifest handoff.",
            ]
        )
        return "\n".join(lines)


def ide_workflow(root: Path | str, *, ide: str = "vscode", output: Path | str = ".vscode", write: bool = False) -> IdeWorkflowReport:
    normalized = ide.strip().lower() or "vscode"
    if normalized != "vscode":
        raise ValueError(f"unknown IDE workflow target: {ide}")
    root_path = Path(root)
    output_path = Path(output)
    target = output_path if output_path.is_absolute() else root_path / output_path
    files = build_vscode_files(root_path, target)
    if write:
        for item in files:
            item.path.parent.mkdir(parents=True, exist_ok=True)
            item.path.write_text(item.content, encoding="utf-8")
    return IdeWorkflowReport(root=str(root_path), ide=normalized, output=target, wrote=write, files=files)


def build_vscode_files(root: Path, output: Path) -> list[IdeWorkflowFile]:
    tasks = {
        "version": "2.0.0",
        "tasks": [
            {
                "label": "CMU: start work",
                "type": "shell",
                "command": "cmu",
                "args": ["--root", str(root), "start", "${input:cmuTaskPrompt}", "--area", "${input:cmuArea}", "--risk", "medium"],
                "problemMatcher": [],
            },
            {
                "label": "CMU: review inbox",
                "type": "shell",
                "command": "cmu",
                "args": ["--root", str(root), "review-inbox"],
                "problemMatcher": [],
            },
            {
                "label": "CMU: evidence session",
                "type": "shell",
                "command": "cmu",
                "args": ["--root", str(root), "evidence-session", "--apply", "--record"],
                "problemMatcher": [],
            },
        ],
        "inputs": [
            {"id": "cmuTaskPrompt", "type": "promptString", "description": "Task prompt for CMU start"},
            {"id": "cmuArea", "type": "promptString", "description": "CMU task area", "default": "repository"},
        ],
    }
    mcp = {"servers": {MCP_SERVER_NAME: {"command": "cmu-mcp", "args": ["--root", str(root)]}}}
    snippets = {
        "CMU Copilot event": {
            "prefix": "cmu-copilot-event",
            "body": [
                '{',
                '  "event": "copilot.chat.started",',
                '  "payload": {',
                '    "message": "$1",',
                '    "actor": "agent",',
                '    "area": "$2",',
                '    "files": ["$3"],',
                '    "risk": "medium"',
                "  }",
                "}",
            ],
            "description": "Copilot-style CMU runner event",
        }
    }
    return [
        IdeWorkflowFile(output / "tasks.json", json.dumps(tasks, indent=2, sort_keys=True) + "\n"),
        IdeWorkflowFile(output / "mcp.json", json.dumps(mcp, indent=2, sort_keys=True) + "\n"),
        IdeWorkflowFile(output / "cmu.code-snippets", json.dumps(snippets, indent=2, sort_keys=True) + "\n"),
    ]
