from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
import venv
from dataclasses import dataclass
from pathlib import Path


DIST_CHECK_VERSION = "cmu-dist-check/v1"


@dataclass(frozen=True)
class DistCheckItem:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class DistCheckReport:
    root: str
    work_dir: str
    items: list[DistCheckItem]

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.items)

    def render(self) -> str:
        lines = [
            "CMU Distribution Check",
            f"Version: {DIST_CHECK_VERSION}",
            f"Root: {self.root}",
            f"Work Dir: {self.work_dir}",
            f"Status: {'pass' if self.passed else 'fail'}",
            "Mode: builds and installs CMU into a temporary venv, then validates installed CLI/module/MCP behavior.",
            "",
            "Checks:",
        ]
        for item in self.items:
            marker = "pass" if item.passed else "fail"
            lines.append(f"- [{marker}] {item.name}: {item.detail}")
        lines.extend(
            [
                "",
                "Proof Meaning: dist-check validates the package after installation outside the source cwd, "
                "including console scripts, `python -m cmu`, README/package adoption gates, demo walkthrough, "
                "and MCP tool discovery.",
            ]
        )
        return "\n".join(lines)


def dist_check(
    root: Path | str = ".",
    *,
    python_executable: str | None = None,
    work_dir: Path | str | None = None,
    keep_work_dir: bool = False,
) -> DistCheckReport:
    root_path = Path(root).resolve()
    base_work_dir = Path(work_dir) if work_dir is not None else root_path / ".manual"
    run_dir = base_work_dir / f"dist-check-{uuid.uuid4().hex}"
    run_dir.mkdir(parents=True, exist_ok=True)
    venv_dir = run_dir / "venv"
    validation_cwd = run_dir / "validation-cwd"
    validation_cwd.mkdir(parents=True, exist_ok=True)
    items: list[DistCheckItem] = []
    py = python_executable or sys.executable

    try:
        items.append(create_venv(venv_dir, py))
        venv_python = venv_python_path(venv_dir)
        scripts_dir = venv_scripts_dir(venv_dir)
        if items[-1].passed:
            items.append(install_package(root_path, venv_python, validation_cwd))
        else:
            items.extend(skipped_items())
            return DistCheckReport(root=str(root_path), work_dir=str(run_dir), items=items)

        if items[-1].passed:
            items.extend(
                [
                    check_console_script(scripts_dir / script_name("cmu"), validation_cwd),
                    check_module_entrypoint(venv_python, validation_cwd),
                    check_install_check(scripts_dir / script_name("cmu"), root_path, validation_cwd),
                    check_demo_walkthrough(scripts_dir / script_name("cmu"), root_path, validation_cwd),
                    check_mcp_discovery(scripts_dir / script_name("cmu-mcp"), root_path, validation_cwd),
                ]
            )
        else:
            items.extend(skipped_items())
        return DistCheckReport(root=str(root_path), work_dir=str(run_dir), items=items)
    finally:
        if not keep_work_dir:
            shutil.rmtree(run_dir, ignore_errors=True)


def create_venv(venv_dir: Path, python_executable: str) -> DistCheckItem:
    try:
        builder = venv.EnvBuilder(with_pip=True, system_site_packages=True)
        builder.create(venv_dir)
    except Exception as error:
        return DistCheckItem("create venv", False, f"{Path(python_executable).name} venv failed: {error}")
    return DistCheckItem("create venv", True, "temporary venv created with pip and local build backend access")


def install_package(root: Path, venv_python: Path, cwd: Path) -> DistCheckItem:
    result = run_command(
        [str(venv_python), "-m", "pip", "install", "--no-build-isolation", str(root)],
        cwd=cwd,
        timeout=90,
    )
    if result.returncode != 0:
        return DistCheckItem("install package", False, summarize_process(result))
    return DistCheckItem("install package", True, "package built as wheel and installed into temporary venv")


def check_console_script(cmu_script: Path, cwd: Path) -> DistCheckItem:
    result = run_command([str(cmu_script), "--help"], cwd=cwd)
    passed = result.returncode == 0 and "demo-walkthrough" in result.stdout and "install-check" in result.stdout
    return DistCheckItem(
        "installed cmu console script",
        passed,
        "installed `cmu --help` exposes adoption commands" if passed else summarize_process(result),
    )


def check_module_entrypoint(venv_python: Path, cwd: Path) -> DistCheckItem:
    result = run_command([str(venv_python), "-m", "cmu", "--help"], cwd=cwd)
    passed = result.returncode == 0 and "demo-walkthrough" in result.stdout and "install-check" in result.stdout
    return DistCheckItem(
        "installed module entrypoint",
        passed,
        "installed `python -m cmu --help` exposes adoption commands" if passed else summarize_process(result),
    )


def check_install_check(cmu_script: Path, root: Path, cwd: Path) -> DistCheckItem:
    result = run_command([str(cmu_script), "--root", str(root), "install-check"], cwd=cwd)
    passed = result.returncode == 0 and "Status: pass" in result.stdout
    return DistCheckItem(
        "installed install-check",
        passed,
        "installed CLI validates source adoption/package surfaces" if passed else summarize_process(result),
    )


def check_demo_walkthrough(cmu_script: Path, root: Path, cwd: Path) -> DistCheckItem:
    result = run_command([str(cmu_script), "--root", str(root), "demo-walkthrough"], cwd=cwd)
    passed = result.returncode == 0 and "Status: pass" in result.stdout and "Mode: read-only walkthrough" in result.stdout
    return DistCheckItem(
        "installed demo-walkthrough",
        passed,
        "installed CLI renders read-only adoption walkthrough" if passed else summarize_process(result),
    )


def check_mcp_discovery(cmu_mcp_script: Path, root: Path, cwd: Path) -> DistCheckItem:
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    payload = json.dumps(request).encode("utf-8")
    framed = b"Content-Length: " + str(len(payload)).encode("ascii") + b"\r\n\r\n" + payload
    result = subprocess.run(
        [str(cmu_mcp_script), "--root", str(root)],
        cwd=cwd,
        input=framed,
        capture_output=True,
        timeout=30,
    )
    response = parse_mcp_response(result.stdout)
    tools = [tool.get("name") for tool in response.get("result", {}).get("tools", [])]
    expected = ["cmu_task_start", "cmu_after_work", "cmu_link_checkpoint", "cmu_review"]
    passed = result.returncode == 0 and tools == expected
    return DistCheckItem(
        "installed MCP discovery",
        passed,
        "installed `cmu-mcp` exposes stable CMU tools" if passed else f"return={result.returncode}, tools={tools}, stderr={result.stderr.decode('utf-8', errors='replace')[:300]}",
    )


def skipped_items() -> list[DistCheckItem]:
    return [
        DistCheckItem("installed cmu console script", False, "skipped because package installation did not pass"),
        DistCheckItem("installed module entrypoint", False, "skipped because package installation did not pass"),
        DistCheckItem("installed install-check", False, "skipped because package installation did not pass"),
        DistCheckItem("installed demo-walkthrough", False, "skipped because package installation did not pass"),
        DistCheckItem("installed MCP discovery", False, "skipped because package installation did not pass"),
    ]


def run_command(args: list[str], *, cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=False, capture_output=True, text=True, timeout=timeout)


def summarize_process(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stderr.strip() or result.stdout.strip()).replace("\r\n", "\n")
    if len(text) > 300:
        text = text[:297] + "..."
    return f"return={result.returncode}; {text or 'no output'}"


def parse_mcp_response(raw: bytes) -> dict[str, object]:
    if b"\r\n\r\n" not in raw:
        return {}
    _, body = raw.split(b"\r\n\r\n", 1)
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def venv_python_path(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def venv_scripts_dir(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts"
    return venv_dir / "bin"


def script_name(name: str) -> str:
    if sys.platform == "win32":
        return name + ".exe"
    return name
