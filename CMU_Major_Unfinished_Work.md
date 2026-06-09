# CMU Major Unfinished Work

Last updated: 2026-06-09.

## Why This Exists

Central Memory Unit is not meant to become a notes app, transcript archive, generic RAG layer, or dashboard of stored facts. The mission is to preserve the living intelligence of an organization: repeated situations, decisions, mistakes, accepted practices, unresolved questions, ownership, and the reasoning that should improve future work.

The deeper motive is simple: humans, agents, new team members, and future agent swarms should not restart from zero. CMU should quietly recognize familiar situations, guide work toward trusted paths, warn against costly mistakes, draft new reusable learning only when it matters, and ask for review only when memory becomes behavior-guiding or high-consequence.

The first-pass structural skeleton is now largely complete. The next work is no longer about proving that each concept can exist in isolation. The next work is about making CMU dependable, easier to integrate, more automatic where safe, stricter where authority matters, and clearer for real teams to trust.

## Current State In One Line

CMU has working local CLI, SDK, MCP, memory lifecycle, retrieval, receipts, authority, analytics, quality, portability, scenario, and demo slices, but it is still mostly local, command-driven, diagnostic-heavy, and not yet a polished product or production-grade organizational memory platform.

## Major Things Still To Implement Completely

### 1. Real Production Retrieval

Motive: CMU must surface memory only when it changes the next action, prevents harm, or improves judgment. Weak retrieval would either miss costly lessons or create context drag.

Still needed:

- External embedding model support beyond local hashing vectors.
- Dedicated vector database or durable semantic index.
- Durable graph-backed memory store instead of only local JSON relationships.
- Stronger precision, recall, rejection, and grounding metrics.
- Retrieval benchmarks against generic vector memory, graph memory, and no-memory baselines.

### 2. Autonomous Agent Integration

Motive: CMU is agent-first. Agents should call memory naturally before meaningful work, during uncertainty, after reusable learning, and after checkpoints without manually reconstructing CLI flows.

First autonomous-runner hook slice now implemented:

- `cmu.runner_hooks.AutonomousRunnerHooks` provides event-shaped hooks for `before_task`, `after_task`, `after_checkpoint`, and `review`.
- The hook layer delegates to the real `CentralMemoryUnit` / `AgentIntegration` boundary, so autonomous runners do not parse human CLI output or duplicate trigger, retrieval, receipt, Candidate Memory, checkpoint, or review logic.
- `cmu runner-hooks` renders the hook contract read-only by default and can execute the real `before_task` hook when given a task prompt.
- Automated tests verify the runner hook manifest, action-note receipt creation, silent-skip no-receipt behavior, after-task Candidate Memory gating, manual checkpoint linking, review evidence, CLI contract rendering, CLI JSON execution, and read-only no-prompt behavior against real stores.

First Codex host-adapter slice now implemented:

- `cmu.codex_adapter.CodexRunnerAdapter` translates Codex-style JSON events into the existing autonomous runner hooks.
- `cmu codex-runner` renders the adapter contract read-only with no input and executes `codex.task_started`, `codex.task_finished`, `codex.checkpoint_created`, and `codex.review_requested` events with JSON input.
- The adapter delegates to `AutonomousRunnerHooks`, so Codex host wiring still uses the real trigger, retrieval, receipt, Candidate Memory, checkpoint, and review paths.
- Automated tests verify read-only manifest behavior, action-note receipt creation, Candidate persistence, manual checkpoint linking, read-only review, CLI JSON execution, invalid-event handling, and UTF-8 BOM input-file handling against real stores.
- Manual verification confirmed the adapter contract, low-risk `silent-skip` event behavior, BOM-tolerant input-file execution, and `install-check` adoption validation.

First before/after scenario comparison slice now implemented:

- `cmu scenario-compare` runs saved scenario-library cases against a baseline CMU root and the current CMU root, then classifies each case as regressed, improved, changed, or unchanged.
- `--strict` exits non-zero only when a passing baseline becomes a current review case, giving memory-base and retrieval changes a practical regression gate.
- The comparison is read-only with respect to memory/use evidence: it evaluates real persisted stores without creating Memory Use Receipts.

Still needed:

- Additional host-specific runner adapters for common autonomous agent runtimes.
- Host-specific MCP setup polish.
- Deeper IDE/coding-agent integration.
- Integration examples for common agent runtimes.
- Runtime behavior where CMU becomes part of the work loop, not an optional manual command.

### 3. Automatic Evidence Loop

Motive: CMU should measure usefulness by whether memory changed work for the better. Receipts and Git checkpoints are the first evidence loop, but evidence should not depend forever on manual linking.

First checkpoint monitor slice now implemented:

- `cmu evidence-monitor` inspects recent Git checkpoints, reuses the existing auto-link scoring path, and applies only clean high-confidence committed matches when `--apply` is used.
- The monitor is dry-run by default and leaves WIP, reverted, mixed, delayed, ambiguous, no-overlap, low-confidence, or otherwise risky checkpoint evidence for human review instead of silently linking it.
- Automated tests verify clean-match dry-run/apply behavior and WIP checkpoint review behavior against real Git repositories, real Memory Use Receipts, and real persisted stores.
- Manual verification created a temp Git repo under `.manual/evidence-monitor-proof`, surfaced a real receipt through `cmu start`, committed a matching file, ran `cmu evidence-monitor --apply`, and confirmed `cmu use-list` showed the linked committed receipt.

Still needed:

- Background daemon/watch mode beyond on-demand `cmu evidence-monitor`.
- Broader automatic receipt-to-checkpoint linking policy across long-running work sessions.
- Richer documentation-only and multi-commit handling beyond the current clean/risky monitor gate.
- Longitudinal evidence that tracks whether memory saved time, reduced mistakes, or created drag.

### 4. Governance And Review UX

Motive: stable Practice and Anchor memory must be dependable by default. Evidence can inform trust, but high-consequence authority should not be silently granted.

First compact review-queue slice now implemented:

- `cmu review-queue` gathers promotion, stable authority, team-scope coverage, active challenge, strengthen, governance review, and decay review moments into compact human approval cards.
- The queue is read-only and preserves existing controlled mutation paths by showing exact follow-up commands such as `cmu promote`, `cmu authority-set`, `cmu use-review --prepare`, `cmu resolve-challenge`, and `cmu decay-apply`.
- Automated tests verify candidate promotion cards, Practice/Anchor approval cards, missing-authority cards, strengthen approval cards, challenge-resolution cards, and decay-review cards through real persisted memories, receipts, and CLI output.
- Manual verification on the current workspace store showed two stable-promotion approval cards for the curated Situation memory and did not mutate memory or receipts.

Still needed:

- Richer interactive or UI-backed approval cards beyond the read-only CLI queue.
- Deeper controlled UX flows for approve, narrow, split, retire, strengthen, or challenge outcomes beyond command handoff cards.
- Clear human review moments in non-CLI surfaces.
- Better owner/team review flows.
- Expiry and review reminders that feel lightweight, not bureaucratic.

### 5. Memory Lifecycle Automation

Motive: CMU should let lower-level memory evolve while protecting stable memory from casual rewrite. The lifecycle should become a working loop, not only an inspection surface.

First controlled lifecycle apply slice now implemented:

- `cmu lifecycle-apply --candidate-ready` previews safe Candidate -> Situation promotion through the existing `cmu promote` gate.
- `--apply` persists only Candidate memories that already pass the Situation promotion gate; blocked Candidates remain unchanged and report the missing gate fields.
- Stable Practice/Anchor promotion is deliberately not automated in this slice and still requires explicit `cmu promote --to practice|anchor --approved-by ...` authority review.
- Automated tests verify dry-run no-mutation behavior, apply behavior for eligible Candidates, and blocked Candidate preservation through real persisted memory stores and CLI output.
- Manual verification on the current workspace store showed a read-only dry run with no eligible Candidate memories and no mutations.

Still needed:

- Assisted Situation -> Practice/Anchor proposal generation.
- Controlled merge, split, settling, demotion, decay, and archival flows.
- Scope refinement automation based on evidence and Memory Gravity.
- Explicit scope-change records for broad or ambiguous changes.

### 6. Real Memory Base Cleanup

Motive: CMU should preserve what future work cannot afford to forget, but the current local memory base still has quality gaps that reduce trust.

First cleanup/readiness slice now implemented:

- `cmu readiness` provides a read-only operator queue that combines authority blockers, unresolved/orphan receipts, graph isolates and dangling links, quality/decay pressure, missing Anti-Pattern coverage, missing Question coverage, and safe next actions.
- Automated tests verify the command against real persisted memory and receipt stores, including read-only behavior and retired-history inclusion.
- Manual verification against the current workspace store correctly reported an empty active memory base with missing Anti-Pattern and Question coverage, rather than inventing evidence or claiming readiness.

First pre-memory curation slice now implemented:

- `cmu doc-curate` treats markdown as evidence, not authority, before memory seeding.
- The command previews by default, rejects stale documents unless explicitly allowed, rejects superseded/out-of-date documents, requires reusable CMU memory signals, and only persists passing drafts as Candidate Memories with `--apply`.
- This protects the first real memory-base seeding pass from converting old or superseded project docs into active guidance.

First memory seeding workbench slice now implemented:

- `cmu doc-curate --select <path-or-title> --apply` lets operators persist only explicitly chosen curation candidates from a larger batch.
- `cmu seed-plan` provides a read-only workbench that combines Candidate promotion commands, missing Anti-Pattern/Question coverage, optional doc-curation preview decisions, rejected-doc Anti-Pattern/Question draft suggestions, and graph relationship suggestions.
- Automated tests verify selected apply, stale/superseded rejection, seed-plan promotion/coverage/graph suggestions, rejected-doc draft suggestions, and read-only behavior through real CLI/store paths.
- Manual verification against current docs and a fresh temp CMU root confirmed preview, seed-plan, selected apply, and candidate listing behavior.

First real memory-base seeding and cleanup pass now implemented:

- The real workspace `.cmu` store now has three active memories: one Situation created from selected `doc-curate --apply --select` output, one Anti-Pattern warning against blind strategic-markdown import, and one Question about the next hardening priority after cleanup.
- `CMU_Major_Unfinished_Work.md` and `CMU_Implementation_Progress.md` were rejected by `doc-curate` as superseded-marker documents and were used only as evidence for manual drafts, not imported as authority.
- The curated Candidate from `CMU_Decisions_And_Assumptions.md` passed `cmu review` and was promoted to Situation through `cmu promote`; no Practice or Anchor authority was assigned because there was no explicit stable-memory approval moment.
- `cmu relate` connected the Situation, Anti-Pattern, and Question into one graph component with no isolates or dangling relationships.
- Manual verification showed `cmu readiness` no longer reports missing Anti-Pattern or Question coverage. The remaining readiness items are P3 quality watch items because the new memories have no future linked-use evidence yet.

Still needed:

- Add missing authority metadata to stable memories.
- Resolve unlinked or unresolved Memory Use Receipts.
- Collect focused use evidence for the new Situation, Anti-Pattern, and Question memories through normal CMU work-cycle receipts.
- Re-run `quality`, `governance`, `analytics`, `graph`, and `lifecycle` outputs after real use evidence exists.

### 7. Multi-Repo, Team, And Organization Memory

Motive: the long-term vision is organizational memory, not only a single local repository. Memory must carry scope so lessons do not falsely transfer across teams, repos, or environments.

First local team-scope directory slice now implemented:

- `cmu team-scope-add` records explicit local repo/team ownership boundaries in `.cmu/team_scopes.json`, including repo, team, owner, code/workflow/environment scope, authority role, and consequence.
- `cmu team-scope` reports those boundaries next to active memory coverage so operators can see which repo/team scopes have matching memory and which remain uncovered.
- The command is intentionally local and conservative: adding a scope writes only the boundary record, while inspection does not mutate memories or receipts.
- `cmu review-queue` now also surfaces uncovered team-scope boundaries as compact review cards, so owner/team coverage gaps are visible beside the existing approval queue.
- Matching is deliberately strict enough to avoid false transfer: environment overlap can filter a match, but it is not enough by itself to claim that a repo/team boundary has memory coverage.
- Automated tests verify persistence, matching active memory coverage, uncovered-boundary reporting, false environment-only coverage rejection, review-queue cards, read-only empty inspection, and CLI rendering through real `TeamDirectoryStore` and `MemoryStore` paths.
- Manual verification under `.manual/team-scope-proof` created a real team boundary, added a scoped Practice memory, and confirmed `cmu team-scope` reported one matching memory and no missing coverage.
- Manual review-queue verification then added an uncovered billing-service/Billing boundary in the same proof root and confirmed `cmu review-queue` reported it as a P1 `team-scope-coverage` card rather than falsely matching the checkout/prod memory.

Still needed:

- Multi-repo memory boundaries beyond local team-scope records.
- Richer delegation and owner/team review workflows.
- Cross-repo authority and ownership.
- Organization-level patterns with narrow, evidence-backed scope.
- Clear rules for when memory can expand beyond one repo/module/team.

### 8. Scenario And Evaluation Maturity

Motive: CMU should prove that memory improves execution. The scenario library exists, but it must grow into a serious evaluation system.

Still needed:

- Runner-scenario evidence that uses the autonomous hook surface. First slice now implemented through `cmu runner-scenario`, which copies the source store into a temporary isolated store, executes real runner hooks there, and checks start/memory/Candidate/checkpoint expectations without mutating source memory or receipts.
- Rich fixture repositories.
- Before/after comparisons of agent behavior with and without CMU.
- Longitudinal scenario suites.
- Measurable usefulness and drag metrics.
- Evaluation cases for retrieval misses, bad matches, governance blocks, and challenge outcomes.

### 9. Product And UI Surface

Motive: CMU should feel like a quiet senior teammate, but humans still need inspectable trust, evidence, and review paths.

Still needed:

- Human-facing memory graph/tree views.
- Review cards for promotion, authority, challenge, and decay decisions.
- Trust and evidence inspection.
- Product surfaces for memory cleanup.
- Navigation through situation -> cause -> fix -> practice -> exception paths.

### 10. Packaging, Install, And Documentation

Motive: once the core engine works, CMU needs to be easy to adopt. A powerful memory layer that is hard to install will not become part of daily work.

First host setup guidance slice now implemented:

- `cmu setup-guide` provides a read-only adoption surface for CLI, Python SDK, MCP, and Codex-style MCP host setup.
- The guide inspects the real project root for `.cmu` store files, Git readiness, `pyproject.toml` script entrypoints, the live `AgentIntegration` manifest, and MCP tool schemas.
- `--host cli|python-sdk|mcp|codex|all` lets humans or agents focus the output without parsing unrelated setup prose.
- Automated tests verify the command through real CLI/store/Git/pyproject paths and assert that tool lists come from the actual AgentIntegration and MCP definitions rather than duplicated fixtures.
- Manual verification against the workspace confirmed the guide reports initialized store state, Git readiness, `cmu` and `cmu-mcp` scripts, expected MCP tools, SDK usage, and quickstart proof commands without mutating memory or receipts.

First README and editable-install discipline slice now implemented:

- The repository now has a root `README.md` that gives a five-minute fresh-checkout path through editable install, `cmu init`, `cmu readiness`, `cmu quickstart-demo`, `cmu quickstart-demo --apply`, and `cmu setup-guide --host all`.
- The README documents the actual CLI work cycle, Python SDK facade, MCP host configuration, Codex-style MCP setup, stable MCP tools, and trust rules without turning CMU into a handbook or context dump.
- `pyproject.toml` now declares the README, setuptools build backend, package discovery, and the existing `cmu` / `cmu-mcp` script entrypoints so editable installs have a clear package boundary.
- `cmu setup-guide` now names `python -m cmu --root <project-root> mcp` as the local-development fallback while still noting the Windows `py` launcher when available.
- Automated tests verify the README against the live `setup-guide`, `pyproject.toml` script table, MCP server name, and `mcp_tool_definitions()` so adoption docs cannot silently drift from the code boundary.
- Manual verification ran the full setup guide, dry-run quickstart, full unittest suite, and an isolated applied quickstart proof that created and linked a Git-backed receipt/checkpoint.

First install/adoption validation slice now implemented:

- `cmu install-check` provides a read-only package/adoption gate for fresh checkouts and local installs.
- The command validates the root README, required quickstart commands, `pyproject.toml` README binding, setuptools build backend, package discovery, `cmu` and `cmu-mcp` console scripts, SDK import, `python -m cmu` module entrypoint, setup-guide consistency, and MCP server/tool schema.
- The report returns pass/fail with specific failed checks and does not initialize stores, create memories, create receipts, or write Git checkpoints.
- Automated tests verify the command against the real checkout and an intentionally incomplete checkout fixture, checking actual README/pyproject/setup-guide/MCP data rather than mocked package state.
- Manual verification confirmed `cmu --root . install-check` reports a clean pass on the workspace.

First scripted local demo walkthrough slice now implemented:

- `cmu demo-walkthrough` provides a single operator-facing path that composes the real `install-check`, `setup-guide`, and `quickstart-demo` surfaces.
- The default walkthrough is read-only: it validates adoption readiness, summarizes host setup from live package state, renders the quickstart proof plan, and gives the next real work-cycle handoff command without creating memories, receipts, or Git checkpoints.
- `cmu demo-walkthrough --apply` delegates to the existing Git-backed quickstart proof, creating the demo Practice memory, task-start receipt, Git checkpoint, linked receipt evidence, and usefulness summary through the same core path as `cmu quickstart-demo --apply`.
- Automated tests verify the dry walkthrough against the real checkout, verify CLI read-only behavior against actual `.cmu` store files, and verify applied walkthrough behavior in a temporary Git checkout with real README/pyproject adoption files.
- Manual verification confirmed the dry walkthrough reports a clean pass and the focused adoption/demo test class passes end to end.

First isolated built-distribution validation slice now implemented:

- `cmu dist-check` creates a temporary validation venv, installs CMU from the checkout as a built package, and validates behavior from outside the source working directory.
- The command verifies the installed `cmu` console script, installed `python -m cmu` module entrypoint, installed `cmu install-check`, installed `cmu demo-walkthrough`, and installed `cmu-mcp` MCP tool discovery.
- The validation uses `--no-build-isolation` plus a venv with local build-backend access to avoid network dependency while still proving that wheel build/install and installed entrypoints work.
- Automated tests run the real distribution check in a temporary validation workspace and assert the installed CLI/module/MCP checks pass.
- Manual verification confirmed `cmu --root . dist-check` builds and installs the package, validates the installed adoption commands, and discovers the stable MCP tools.

First portable compatibility fixture gate now implemented:

- `cmu portable-compat --fixture-dir <dir>` runs saved portable bundle JSON fixtures without importing them.
- Fixture names encode expectations: `valid-*.json` must validate under the current bundle schema, `invalid-*.json` must fail validation, and `future-*.json` must fail safely as an unsupported schema.
- This gives portability a repeatable compatibility gate instead of relying only on one-off `portable-validate` checks.
- Automated tests build fixtures from real exported bundles, then verify valid, invalid, future-schema, and failing-valid fixture behavior through the real CLI and validation path.
- Manual verification under `.manual/portable-compat-proof` created a real memory through `cmu add`, exported a current bundle through `cmu portable-export`, derived invalid and future-schema fixtures from that bundle, and confirmed `cmu portable-compat` passed all three expected fixture outcomes.

Still needed:

- Published/package workflow beyond local built-distribution validation.
- Broader portable bundle fixture corpus across future schema migrations and historical real-world bundles.

## Strategic Priority Order

1. Clean and strengthen the real memory base, because the current store is the first proof of whether CMU can govern itself.
2. Improve packaging and docs, because SDK/MCP/demo slices already exist and should become easy to try.
3. Add autonomous-runner and host-specific integrations, because CMU must operate inside real agent work.
4. Build background evidence automation, because usefulness and drag should be measured continuously.
5. Mature production retrieval and durable graph storage, because CMU's long-term value depends on accurate, grounded memory surfacing.
6. Build the human-facing review and graph UI, because trust decisions need a better surface than CLI reports.

## Non-Negotiables To Preserve

- CMU stays quiet unless memory changes action or needs trust.
- Active context remains lean.
- Candidate Memory is created only from reusable situational intelligence.
- Stable Practice and Anchor memory require authority, not just repeated use.
- Retrieval must be grounded by scope, graph, metadata, evidence, and authority before memory reaches active work.
- Memory value is judged by usefulness and drag, not vanity metrics.
- Scope starts narrow and expands only when evidence proves broader applicability.

## Practical Next Move

The next implementation phase should be hardening and packaging, not more tiny semantic-audit polish.

Most recent completed implementation slice:

`cmu portable-compat` now provides the first portable bundle compatibility fixture gate. It validates current-schema fixtures, requires intentionally invalid fixtures to fail, and proves future-schema bundles fail safely as unsupported instead of being imported silently.

Next best implementation slice:

The next product-hardening slice should continue turning unfinished areas into cautious operator workflows: either deepen local team-scope records into owner/team review flows, expand evidence monitoring toward session/watch workflows, build richer fixture repositories and host-path suites around `cmu runner-scenario`, `cmu codex-runner`, and `cmu scenario-compare`, or grow the new portable compatibility gate with historical/future migration fixtures. The cleanup memories should stay on quality watch until future work creates linked receipts proving usefulness or drag.

Maintenance rule:

This file should be updated automatically whenever a meaningful CMU task or implementation slice is completed, especially when a listed unfinished item moves from "still needed" to "first slice implemented" or when the practical next move changes.
