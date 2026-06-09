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
cmu demo-walkthrough
cmu setup-guide --host all
cmu runner-hooks
cmu codex-runner
cmu runner-scenario "adjust local label spacing" --area ui --risk low --expect-start silent-skip --strict
cmu fixture-repo-create --kind checkout-release --output .manual/fixtures/checkout-release
cmu evidence-monitor
cmu review-queue
cmu review-reminders
cmu lifecycle-apply --candidate-ready
cmu team-scope
cmu portable-compat --fixture-dir tests/fixtures/portable
cmu hardening-cycle --portable-fixture-dir tests/fixtures/portable
cmu install-check
cmu dist-check
```

Use `cmu quickstart-demo` first for a dry run. Use `cmu quickstart-demo --apply` inside a Git repository when you want the proof loop to create a scoped Practice memory, run task-start retrieval, create a Memory Use Receipt, write a tiny Git checkpoint, link the receipt to that checkpoint, and show usefulness evidence.

Use `cmu demo-walkthrough` when you want the whole adoption path in one report: install validation, setup guidance, quickstart proof plan, and the next real work-cycle handoff. Use `cmu demo-walkthrough --apply` inside a Git repository to run the same Git-backed proof loop as part of that walkthrough.

Use `cmu runner-hooks` when wiring an autonomous runner. With no prompt, it renders the event hook contract for `before_task`, `after_task`, `after_checkpoint`, and `review` without mutating memory. With a prompt, it runs the real `before_task` hook through the same AgentIntegration path as the SDK and MCP surfaces.

Use `cmu codex-runner` for a Codex-style host adapter. With no input, it renders the supported event contract. With JSON input, it translates `codex.task_started`, `codex.task_finished`, `codex.checkpoint_created`, and `codex.review_requested` events into the real runner hooks.

Use `cmu runner-scenario` when you want scenario evidence for runner behavior without mutating the source memory base. It copies the current memories and receipts into a temporary isolated store, executes the real runner hooks there, then checks expectations such as start status, surfaced memory, Candidate outcome, and checkpoint linking.

Use `cmu fixture-repo-create` when you need a repeatable local fixture repository for scenario, runner, and host-path tests. The first fixture kind, `checkout-release`, creates real files plus a seeded CMU memory and saved scenario.

Use `cmu evidence-monitor` when you want CMU to inspect recent Git checkpoints and link only clean high-confidence receipt matches. It is dry-run by default; `--apply` persists only safe committed matches and leaves WIP, reverted, mixed, delayed, ambiguous, or weak evidence for review.

Use `cmu review-queue` when you want compact human approval cards across promotion, stable authority, team-scope coverage, challenge resolution, strengthen, governance review, and decay decisions without mutating memory.

Use `cmu review-reminders` when you want a lightweight reminder list for expired or due-soon stable-memory authority reviews plus open high-priority review-queue cards.

Use `cmu lifecycle-apply --candidate-ready` to preview safe Candidate -> Situation promotion through the existing promotion gate. Add `--apply` only when you want eligible Candidate memories persisted as Situation memories; Practice and Anchor promotion still requires explicit authority.

Use `cmu team-scope-add` and `cmu team-scope` when you need local repo/team ownership boundaries. Team scope records make ownership, code/workflow/environment boundaries, authority role, and consequence visible next to memory coverage so lessons do not silently expand across repositories or teams.

Use `cmu portable-compat --fixture-dir <dir>` when you want saved portable bundle fixtures to guard compatibility. Fixture filenames beginning with `valid-`, `invalid-`, and `future-` prove that current bundles validate, intentionally bad bundles fail, and future schemas fail safely as unsupported.

Use `cmu hardening-cycle --portable-fixture-dir <dir>` when you want the five current product-hardening items checked together: team owner review metadata, dry-run checkpoint evidence monitoring, fixture-host-path catalog coverage, portable compatibility fixtures, and review-reminder delivery readiness. Add `--strict` when this should fail automation unless all five proofs pass.

Use `cmu dist-check` when you want a stronger packaging proof. It creates a temporary validation environment, installs CMU as a built package, then checks installed `cmu`, `python -m cmu`, `cmu install-check`, `cmu demo-walkthrough`, and `cmu-mcp` tool discovery from outside the source checkout.

If the `cmu` script is not installed yet, run the same commands through the module entrypoint:

```powershell
python -m cmu init
python -m cmu readiness
python -m cmu quickstart-demo
python -m cmu demo-walkthrough
python -m cmu setup-guide --host all
python -m cmu runner-hooks
python -m cmu codex-runner
python -m cmu runner-scenario "adjust local label spacing" --area ui --risk low --expect-start silent-skip --strict
python -m cmu fixture-repo-create --kind checkout-release --output .manual/fixtures/checkout-release
python -m cmu evidence-monitor
python -m cmu review-queue
python -m cmu review-reminders
python -m cmu lifecycle-apply --candidate-ready
python -m cmu team-scope
python -m cmu portable-compat --fixture-dir tests/fixtures/portable
python -m cmu hardening-cycle --portable-fixture-dir tests/fixtures/portable
python -m cmu install-check
python -m cmu dist-check
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

## Autonomous Runner Hooks

Use the Python hook facade when an autonomous runner wants event-shaped integration instead of raw tool names:

```python
from cmu import AutonomousRunnerHooks

hooks = AutonomousRunnerHooks(root=".")
start = hooks.before_task(
    "debug repeated checkout rollback failure",
    actor="agent",
    area="release",
    files=["quickstart_demo/rollback_notes.txt"],
    workflow=["debugging", "rollback"],
    risk="high",
)
```

The hook facade delegates to the same `AgentIntegration` tools as the SDK and MCP adapter. `before_task` may create a receipt only when an Action Note surfaces. `after_task` should be called with reusable learning only, `after_checkpoint` links receipt evidence, and `review` reads usefulness/drag cards without changing stable trust.

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

Use `cmu setup-guide --host mcp` or `cmu setup-guide --host codex` to inspect the live package scripts, AgentIntegration manifest, MCP tool schema, and Codex runner adapter expected by this checkout.

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
cmu demo-walkthrough
cmu dist-check
cmu runner-hooks
cmu codex-runner
cmu runner-scenario "adjust local label spacing" --area ui --risk low --expect-start silent-skip --strict
cmu fixture-repo-create --kind checkout-release --output .manual/fixtures/checkout-release
cmu evidence-monitor
cmu review-queue
cmu review-reminders
cmu lifecycle-apply --candidate-ready
cmu team-scope
cmu portable-compat --fixture-dir tests/fixtures/portable
cmu hardening-cycle --portable-fixture-dir tests/fixtures/portable
cmu quickstart-demo
python -m unittest tests.test_cmu_spine.QuickstartDemoTests
python -m unittest tests.test_cmu_spine.AutonomousRunnerHooksTests
python -m unittest tests.test_cmu_spine.CodexRunnerAdapterTests
python -m unittest tests.test_cmu_spine.RunnerScenarioEvidenceTests
```

`cmu setup-guide`, `cmu install-check`, `cmu runner-hooks` without a prompt, `cmu codex-runner` without input, `cmu runner-scenario`, `cmu evidence-monitor` without `--apply`, `cmu review-queue`, `cmu review-reminders`, `cmu lifecycle-apply --candidate-ready` without `--apply`, `cmu team-scope`, `cmu portable-compat`, `cmu hardening-cycle`, and `cmu demo-walkthrough` without `--apply` are read-only with respect to the source CMU memory base. They should not initialize source stores, create source memories, create source receipts, or write Git checkpoints. `cmu runner-hooks <task>` and `cmu codex-runner --input ...` execute real hooks against the source store and can create a Memory Use Receipt when memory surfaces. `cmu runner-scenario` executes hooks only inside a temporary isolated copy under `.manual`. `cmu fixture-repo-create` writes a separate explicit output directory for repeatable host-path fixtures. `cmu evidence-monitor --apply` updates only existing unlinked receipts when recent Git evidence is clean and high-confidence. `cmu review-queue` gathers existing promotion, authority, team-scope coverage, challenge, use-review, and decay commands into compact approval cards without applying them. `cmu review-reminders` turns stable-memory authority expiry and high-priority review cards into a small reminder list without applying anything. `cmu lifecycle-apply --candidate-ready --apply` promotes only Candidate memories that already pass the existing Situation promotion gate. `cmu team-scope-add` writes local repo/team boundary records, while `cmu team-scope` only inspects them. `cmu portable-compat` validates saved bundle fixtures without importing them. `cmu hardening-cycle` composes those five current hardening checks without applying any of their follow-up commands. `cmu install-check` validates the README, package metadata, SDK import, module entrypoint, setup-guide consistency, and MCP schema against the live checkout. `cmu dist-check` writes only temporary validation files under `.manual` by default. `cmu quickstart-demo --apply` and `cmu demo-walkthrough --apply` intentionally mutate the local Git repository by creating the small demo proof checkpoint.
