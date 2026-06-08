# Central Memory Unit

Central Memory Unit is an agent-first organizational memory layer. It preserves reusable situational intelligence: repeated situations, hard-won fixes, accepted practices, costly traps, ownership, unresolved questions, and evidence about whether memory helped or created drag.

CMU is not a notes app, transcript archive, handbook dump, or generic RAG store. It should stay quiet unless memory changes the next action, prevents likely harm, improves judgment, or needs explicit review.

## Five-Minute Quickstart

From a fresh checkout:

```powershell
python -m pip install -e .
cmu init
cmu readiness
cmu quickstart-demo
cmu quickstart-demo --apply
cmu setup-guide --host all
cmu install-check
```

Use `cmu quickstart-demo` first for a dry run. Use `cmu quickstart-demo --apply` inside a Git repository when you want the proof loop to create a scoped Practice memory, run task-start retrieval, create a Memory Use Receipt, write a tiny Git checkpoint, link the receipt to that checkpoint, and show usefulness evidence.

If the `cmu` script is not installed yet, run the same commands through the module entrypoint:

```powershell
python -m cmu init
python -m cmu readiness
python -m cmu quickstart-demo
python -m cmu setup-guide --host all
python -m cmu install-check
```

On Windows, the Python launcher form also works when `py` is available:

```powershell
py -m cmu setup-guide --host all
```

## CLI Work Cycle

For normal agent or developer work, keep CMU task-bound:

```powershell
cmu start --actor agent --area <area> --file <path> "<task>"
cmu remember --help
cmu use-link-auto
cmu use-review
```

`cmu start` runs the trigger layer first. Small, obvious, low-risk work can remain silent. Meaningful work gets task-bound onboarding and preflight retrieval, and CMU creates a receipt only when memory actually surfaces as an Action Note.

Use `cmu remember` only when current work produced reusable situational intelligence. Candidate Memory still has to pass review and promotion gates before it becomes stable behavior-guiding memory.

## Python SDK

The SDK facade delegates to the same `AgentIntegration` boundary used by CLI and MCP paths.

```python
from cmu import CentralMemoryUnit

cmu = CentralMemoryUnit(root=".")
start = cmu.task_start(
    actor="agent",
    task="debug repeated checkout rollback failure",
    area="release",
    files=["quickstart_demo/rollback_notes.txt"],
    workflow=["debugging", "rollback"],
    environment=["local"],
    risk="high",
)
```

After work, call `cmu.after_work(...)` only if a reusable lesson appeared, then use `cmu.link_checkpoint(...)` and `cmu.review(...)` to keep usefulness evidence grounded in real checkpoints.

## MCP Host Setup

Install the package so the script entrypoints are available:

```powershell
python -m pip install -e .
```

Configure an MCP host with:

```json
{
  "command": "cmu-mcp",
  "args": ["--root", "<project-root>"]
}
```

The server name is `central-memory-unit`. The stable MCP tools are:

- `cmu_task_start`
- `cmu_after_work`
- `cmu_link_checkpoint`
- `cmu_review`

During local development, the fallback command is:

```json
{
  "command": "python",
  "args": ["-m", "cmu", "--root", "<project-root>", "mcp"]
}
```

Use `cmu setup-guide --host mcp` or `cmu setup-guide --host codex` to inspect the live package scripts, AgentIntegration manifest, and MCP tool schema expected by this checkout.

## Trust Rules

- Candidate Memory is created only from reusable situational intelligence.
- Situation Memory can describe a scoped lesson without becoming a default rule.
- Practice and Anchor Memory require explicit authority before they guide high-consequence behavior.
- Scope starts narrow and expands only when evidence proves broader applicability.
- Usefulness and drag matter more than memory volume.

## Verification

Useful local checks:

```powershell
cmu setup-guide --host all
cmu install-check
cmu quickstart-demo
python -m unittest tests.test_cmu_spine.QuickstartDemoTests
```

`cmu setup-guide` and `cmu install-check` are read-only. They should not initialize stores, create memories, create receipts, or write Git checkpoints. `cmu install-check` validates the README, package metadata, SDK import, module entrypoint, setup-guide consistency, and MCP schema against the live checkout. `cmu quickstart-demo --apply` intentionally mutates the local Git repository by creating the small demo proof checkpoint.
