from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

from .host_setup_manifest import host_setup_manifest


HOST_EXAMPLES_VERSION = "cmu-host-examples/v1"
EXAMPLE_KINDS = {"codex", "openai", "mcp", "all"}


@dataclass(frozen=True)
class HostExampleFile:
    path: Path
    content: str


@dataclass(frozen=True)
class HostExamplesReport:
    root: str
    host: str
    output: Path
    wrote: bool
    files: list[HostExampleFile] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "CMU Host Examples",
            f"Version: {HOST_EXAMPLES_VERSION}",
            "Mode: generated integration examples from the live host-setup-manifest contract.",
            f"Host: {self.host}",
            f"Root: {self.root}",
            f"Output: {self.output}",
            f"Wrote: {'yes' if self.wrote else 'no'}",
            "",
            "Files:",
        ]
        lines.extend(f"- {item.path}" for item in self.files) if self.files else lines.append("- None")
        lines.extend(
            [
                "",
                "Proof Meaning: common agent runtimes can now consume manifest-derived examples instead of hand-translating CMU setup prose.",
            ]
        )
        return "\n".join(lines)


def host_examples(
    root: Path | str,
    *,
    host: str = "all",
    output: Path | str = ".cmu/host-examples",
    write: bool = False,
) -> HostExamplesReport:
    normalized = host.strip().lower() or "all"
    if normalized not in EXAMPLE_KINDS:
        raise ValueError(f"unknown host example kind: {host}")
    root_path = Path(root)
    output_path = Path(output)
    target = output_path if output_path.is_absolute() else root_path / output_path
    files = build_example_files(root_path, target, normalized)
    if write:
        target.mkdir(parents=True, exist_ok=True)
        for item in files:
            item.path.parent.mkdir(parents=True, exist_ok=True)
            item.path.write_text(item.content, encoding="utf-8")
    return HostExamplesReport(root=str(root_path), host=normalized, output=target, wrote=write, files=files)


def build_example_files(root: Path, output: Path, host: str) -> list[HostExampleFile]:
    manifest_report = host_setup_manifest(root, host=host, write=False)
    files = [
        HostExampleFile(
            output / "README.md",
            "\n".join(
                [
                    "# CMU Host Examples",
                    "",
                    "These examples are generated from the live `cmu host-setup-manifest` contract.",
                    "",
                    f"- Root: `{root}`",
                    f"- Manifest preview command: `cmu --root {root} host-setup-manifest --host {host}`",
                    f"- Tools: {', '.join(manifest_report.tools)}",
                    "",
                ]
            ),
        )
    ]
    if host in {"all", "mcp", "codex"}:
        files.append(
            HostExampleFile(
                output / "codex-mcp.json",
                json.dumps(
                    {
                        "schema": HOST_EXAMPLES_VERSION,
                        "host": "codex",
                        "mcpServers": {
                            "central-memory-unit": {
                                "command": "cmu-mcp",
                                "args": ["--root", str(root)],
                            }
                        },
                        "taskStart": "cmu codex-runner --input-file <event.json>",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )
        )
    if host in {"all", "openai"}:
        files.append(
            HostExampleFile(
                output / "openai-runner-event.json",
                json.dumps(
                    {
                        "schema": HOST_EXAMPLES_VERSION,
                        "event": "openai.run.started",
                        "payload": {
                            "input": "replace with the task prompt",
                            "actor": "agent",
                            "area": "repository area",
                            "files": ["path/to/file.py"],
                            "workflow": ["implementation"],
                            "environment": ["local"],
                            "risk": "medium",
                        },
                        "run": f"cmu --root {root} openai-runner --input-file openai-runner-event.json",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )
        )
    if host in {"all", "mcp"}:
        files.append(
            HostExampleFile(
                output / "mcp-tool-call.json",
                json.dumps(
                    {
                        "schema": HOST_EXAMPLES_VERSION,
                        "server": "central-memory-unit",
                        "tool": "cmu_task_start",
                        "arguments": {
                            "task": "replace with the task prompt",
                            "actor": "agent",
                            "area": "repository area",
                            "files": ["path/to/file.py"],
                            "risk": "medium",
                        },
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )
        )
    return files
