# Central Memory Unit: Implementation Progress

Last updated: 2026-06-10.

## Current Build Status

CMU now has the first working local memory loop for agent-first use.

Current checkpoint status:

- The `Central Memory Unit` folder is now its own standalone Git repository.
- The repository is connected to GitHub at `https://github.com/ChetanReddyC/Central-Memory-Unit`.
- The initial implementation checkpoint and follow-up focused verification commits have been pushed to `main`.
- Memory Use Receipts are now linked to real Git commits rather than sitting as ungrounded local events.
- The evidence loop has been verified end to end: `cmu preflight` surfaces memory, creates a receipt, work reaches a Git commit, `cmu use-link-auto --apply` links the receipt, and `cmu use-review --thresholds` reviews the evidence.
- Current receipt evidence shows the task-start preflight Practice has multiple strong focused linked uses, while the remaining drag signals come from the broad initial commit. `use-review` now distinguishes broad mixed-commit drag from true noisy-memory evidence and recommends inspecting the broad commit evidence before challenging the stable Practice. Some documentation-only receipts may remain unlinked because the strategic markdown files are intentionally ignored by Git.

Implemented core surfaces:

- `cmu preflight`: task-start memory check. It takes actor, task area, likely files, prompt, and risk, then returns either silence or a compact CMU Action Note. Actor match is supporting context only; it cannot by itself make memory enter active context.
- `cmu preflight` can now also accept explicit workflow and environment metadata through `--workflow` and `--env` / `--environment`, so retrieval can distinguish situations such as local debugging from production deployment.
- `cmu remember`: direct agent-compatible after-work memory call. It stores a Candidate Memory from structured fields such as situation, signals, what worked, what failed, future-use reason, evidence, liability, scope, and confidence.
- `cmu remember` duplicate detection now ignores common CMU vocabulary and requires stronger significant-term overlap, so distinct lessons are not rejected merely because they share words such as memory, evidence, challenge, root, or CMU.
- `cmu review`: tiny promotion review surface. With no id, it lists Candidate Memories; with an id, it shows the Candidate -> Situation gate card. With `--to practice` or `--to anchor`, it shows a proposal-only authority review card for Situation Memories.
- `cmu promote`: applies Candidate -> Situation promotion when the gate passes. It also applies Situation -> Practice or Situation -> Anchor promotion when the proposal gate passes and `--approved-by <owner-or-team>` is provided.
- `cmu challenge`: records a deliberate challenge to Practice or Anchor memory by naming the mismatch, expected benefit, risk, and rollback path. It stores a Candidate challenge record and does not rewrite stable memory automatically.
- `cmu resolve-challenge`: applies an explicitly approved challenge outcome. Supported outcomes are `exception`, `strengthen`, `update`, `retire`, and `split`. Mutating outcomes require explicit approval plus resolution details and evidence.
- `cmu preflight` now creates a Memory Use Receipt when memory actually surfaces as an Action Note. Receipts are stored separately from memories under `.cmu/uses.json`.
- `cmu use-link`: links a Memory Use Receipt to a Git commit/checkpoint signal. By default it inspects Git for commit hash, message, changed files, and commit time; manual message/file metadata can still be supplied as an override.
- `cmu use-link-latest`: links a Memory Use Receipt to `HEAD` or another Git ref by reading Git metadata directly.
- `cmu use-link-auto`: proposes matches between unlinked Memory Use Receipts and recent Git commits. It is dry-run by default and persists confident links only with `--apply`.
- `cmu use-list`: lists Memory Use Receipts and their current commit-link status.
- `cmu use-summary`: summarizes committed, checkpoint, reverted, low-confidence, mixed-commit, and receipt source signals for one memory.
- `cmu use-review`: reviews Memory Use Receipts across memories or for one memory, producing compact non-mutating cards for repeated usefulness, repeated drag, or missing linked evidence. Review cards now show receipt source counts such as `preflight=1, start=2` without changing scoring.
- `cmu use-review --thresholds`: shows a diagnostic-only threshold report across real Memory Use Receipts. It names the current auto-link, ambiguity, strong-use, drag-review, strengthen, and retrieval-adjustment thresholds, then summarizes observed receipt behavior per memory without mutating anything. It now separates functionality readiness from accuracy readiness, so receipts without commit/checkpoint links are treated as inspectable but not enough for accuracy claims. The threshold report also shows global and per-memory receipt source counts for provenance inspection.
- `cmu use-review --prepare`: turns one memory's use-review card into a prepared follow-up action. Supported actions are `strengthen`, `challenge`, and `scope-review`. Strengthen can apply approved evidence with `--apply --approved-by <owner-or-team>`, challenge can save a Candidate challenge with `--apply`, and scope-review can apply explicit approved scope-axis changes with `--apply --approved-by <owner-or-team> --scope-*` when use-review shows a scope signal such as repeated drag. Stable Practice/Anchor scope changes are limited to safe narrowing here; broadening or material scope shifts must use the challenge/split path.
- Local JSON memory store under `.cmu/memories.json`.
- Local JSON memory-use store under `.cmu/uses.json`.
- Local JSON store writes now use locked read-modify-write transactions with unique temp files, so concurrent receipt or memory writes preserve both updates instead of racing on `.tmp` or losing one writer's data.
- Memory model with types: Candidate, Situation, Anchor, Practice, Exception, Anti-Pattern, and Question.
- Memory model now includes first-slice graph relationship links with relation types such as `supports`, `challenges`, `exception_to`, `derived_from`, `same_situation`, and `related_practice`.
- Scope model with ownership, code, workflow, environment, actor, and time axes.
- Retrieval now has the first Hybrid Retrieval v1 graph expansion slice: directly linked memories can be added to preflight ranking only after a grounded primary match exists.
- `cmu relate` can create a graph relationship between two existing memories without manual JSON editing.
- `cmu relations` can inspect outgoing and incoming links for one memory.
- `cmu preflight --show-matches` now explains graph-expanded matches with the source memory, relationship type, and relationship reason.
- Graph expansion now has scope and authority guardrails: graph links expand only from primary matches that already cross the action threshold, relation types must be compatible with target memory types, target scope must not clearly conflict on actor/code/workflow/environment axes, and Practice/Anchor graph targets must have grounded action scope before they can guide preflight.
- `cmu preflight --show-matches` now shows explainable score breakdowns for matched memories, including text overlap, hard scope signals, actor signal, liability, confidence, type weight, graph-link contribution, source-score carry, and use-evidence adjustment. The compact CMU Action Note stays unchanged.
- Retrieval now has an embedding-ready semantic signal provider/index boundary plus an opt-in local persistent semantic index. Normal CLI use still defaults to semantic off and reports `semantic signal: unavailable -> +0.00`; `cmu preflight --semantic local` builds `.cmu/semantic_index.json`, stores deterministic local hashing vectors, refreshes them by memory fingerprint, and contributes a bounded explainable semantic score only after the existing grounded candidate gates have already admitted the memory.
- The local semantic retrieval slice was committed and pushed as `46c30fe Add grounded local semantic retrieval`. Manual verification confirmed default preflight stayed zero-impact while `--semantic local` produced an explainable semantic contribution.
- Semantic candidate proposal with strict grounding gates now exists for the opt-in local semantic path. `--semantic local` can admit a semantically proposed memory with no direct token overlap only when the semantic score is available and the memory is grounded by non-conflicting action scope plus evidence or stable authority. Ungrounded semantic-only proposals still stay quiet.
- Stable-memory semantic proposal authority has been verified directly. Approved Practice and Anchor memories can surface through semantic proposal when grounded by action scope plus authority, while unapproved stable memories are rejected even if they have similar semantic content and evidence. `cmu add` now accepts `--approved-by` so approved stable memories can be authored through the CLI instead of only through direct model/store construction.
- The first Onboarding Seed core now exists in `cmu.onboarding`. It builds a tiny task-bound seed from actor/task/area and the same grounded retrieval path, producing only where the actor is working, what must not be violated, the default path, the trap to avoid, and when to call CMU again. If no memory crosses the action threshold, it returns a compact fallback seed instead of dumping memory.
- `cmu onboard` now exposes Onboarding Seed through the CLI. It accepts the same actor, area, file, workflow, environment, risk, and opt-in `--semantic local` inputs as preflight, then renders the compact seed without creating a Memory Use Receipt.
- Onboarding Seed now has explicit compactness enforcement. Each field is word-clamped, normal-risk seeds are capped to the documented normal budget, and high-risk seeds keep the larger budget while still avoiding handbook-style expansion.
- The first Call Trigger Layer core now exists in `cmu.triggers`. It classifies task situations as `must-call`, `should-call`, or `silent-skip` using risk, high-risk domains, repeated errors, uncertainty, shared-contract impact, irreversible changes, unfamiliar work, and multi-file scope.
- `cmu trigger` now exposes the Call Trigger Layer through CLI. It reports whether a task is `must-call`, `should-call`, or `silent-skip` and explains the trigger reasons without running retrieval or writing receipts.
- `cmu start` now exposes the first task-start Work Cycle entrypoint. It runs the trigger decision first, keeps `silent-skip` explicit without running onboarding/preflight or creating receipts, and for `should-call` / `must-call` tasks it renders the Onboarding Seed, runs the same grounded preflight path, and creates a Memory Use Receipt only when an Action Note actually surfaces.
- Real `cmu start` inspection against stored memories confirmed the trigger -> Onboarding Seed -> Action Note -> receipt handoff. The matched seed now uses `Must Not Violate` for scope/authority guardrails instead of echoing the memory challenge condition; challenge wording stays in the Action Note under `Challenge Only If`.
- Memory Use Receipts now record their source command, currently `preflight` or `start`; `cmu use-list`, `cmu use-summary`, `cmu use-review`, and `cmu use-review --thresholds` display that provenance so usefulness/drag review can distinguish direct preflight use from the full Work Cycle entrypoint.
- `cmu preflight` and `cmu start` now support `--show-semantic-proposals`, a diagnostic-only view that explains semantic proposal decisions without changing retrieval ranking, Action Notes, or receipt creation. It shows available semantic candidates, whether they were admissible, rejected, or already treated as direct grounded matches, and why. This makes real semantic proposal inspection repeatable before any threshold tuning or external embedding work.
- `cmu onboard` now also supports `--show-semantic-proposals`, so onboarding-oriented semantic behavior can be inspected without creating Memory Use Receipts.
- `cmu semantic-status` now inspects the local semantic index without refreshing it, reporting provider, dimensions, memory/vector counts, and missing/stale/extra vectors. This gives a safe way to verify local index health before changing semantic behavior.
- Memory Use Receipts now preserve semantic provenance for surfaced memory: semantic mode, semantic label, semantic score, and whether the match came through semantic proposal or a direct grounded match. `cmu use-list` shows `semantic=local` for semantic-assisted receipts.
- `cmu use-summary`, `cmu use-review`, and `cmu use-review --thresholds` now surface semantic provenance from Memory Use Receipts. They show semantic mode counts and semantic match/proposal counts, and use-review interpretations call out when strong committed uses or drag signals came from semantic-assisted receipts. This closes the first evidence loop for judging whether semantic retrieval is helping, staying neutral, or creating drag.
- Prepared use-review follow-ups now carry semantic provenance into their evidence text. `--prepare strengthen`, `--prepare challenge`, and `--prepare scope-review` include semantic mode/match counts when semantic-assisted receipts contributed to the review card, plus semantic-assisted strong-use or drag counts where relevant. This keeps approval moments aware of whether evidence came from direct grounded matches, semantic proposals, or mixed retrieval evidence.
- Approved strengthen follow-ups now have explicit test coverage proving semantic provenance is persisted onto the strengthened memory evidence when `--prepare strengthen --apply --approved-by <owner-or-team>` is used.
- `cmu semantic-audit` now provides a read-only semantic evidence review across all Memory Use Receipts. It reports semantic-assisted totals, linked receipts, strong committed uses, drag signals, semantic proposal/direct-match counts, memories with semantic-assisted strong evidence, memories with semantic-assisted drag, and a conservative recommended next action without mutating memory or receipts.
- `cmu semantic-audit --memory <memory-id>` now narrows that same read-only semantic evidence review to one memory. This lets CMU inspect whether a specific memory's semantic-assisted receipts are unlinked, strong, neutral, or drag before changing thresholds or broadening semantic behavior.
- `cmu semantic-audit --recommendations` now groups semantic evidence into read-only next actions: link receipts first, inspect semantic drag, positive semantic signal, neutral linked evidence, and no semantic evidence. It remains diagnostic-only and refuses `--memory` so the grouped view stays global.
- `cmu semantic-audit --recommendations --details` now expands grouped recommendations with receipt-level semantic evidence and auto-link diagnostics. For unlinked semantic-assisted receipts it shows the receipt id, source command, semantic mode/status/score, auto-link refusal reason, candidate commits with score/message/time/file overlap/reasons, and exact `cmu use-link <use-id> --commit <hash>` commands. This keeps the next semantic decision grounded in real receipt and Git evidence without mutating memories, receipts, thresholds, or retrieval behavior.
- `cmu use-resolve` now resolves a Memory Use Receipt without a Git commit when the correct evidence judgment is that no checkpoint should be linked. It requires an explicit outcome (`no-checkpoint`, `not-applicable`, or `superseded`) plus a note, refuses receipts that already have commit evidence, records `resolved_without_commit`, and does not count the receipt as committed usefulness.
- `cmu use-list`, `cmu use-summary`, and `cmu use-review` now surface resolved-without-commit receipts so missing evidence can be distinguished from deliberately closed no-commit cases.
- `cmu semantic-audit`, `cmu semantic-audit --memory`, and `cmu semantic-audit --recommendations --details` now count and render semantic-assisted receipts resolved without commit evidence. These receipts move out of the Link Receipts First bucket into neutral observation instead of creating false drag or false positive usefulness.
- `cmu semantic-audit --recommendations` now has a separate `Resolve Remaining Semantic Evidence` bucket for memories that have some linked semantic evidence but still have unresolved semantic-assisted receipts. This prevents a memory with one strong semantic-linked use from looking fully positive while other semantic receipts still need `use-link` or `use-resolve`.
- Semantic audit recommendation ordering now stays conservative: no semantic receipts stay quiet, fully unlinked semantic receipts say link first, semantic drag takes priority, partial evidence gaps ask for resolution, and only fully closed positive evidence appears as a positive semantic signal.
- `cmu semantic-audit --recommendations --details` now shows linked, unlinked, and resolved semantic receipts together inside the partial-evidence group, so reviewers can see exactly what remains open before changing retrieval thresholds or broadening semantic proposal behavior.
- Semantic audit reports and recommendation lines now show explicit unresolved semantic-assisted receipt counts. This makes partial evidence measurable instead of implied by subtracting linked receipts from totals.
- `cmu semantic-audit --recommendations --details` now prints no-commit resolution command options for each unresolved semantic-assisted receipt: `no-checkpoint`, `not-applicable`, and `superseded`. This puts `cmu use-resolve` beside `cmu use-link` so evidence closure is actionable in either direction.
- `cmu semantic-audit --recommendations --details --open-only` now filters receipt-level details to unresolved semantic-assisted receipts only. Group summaries still show linked/resolved/unresolved/strong/drag counts, but closed receipt details are hidden so the operator can focus on evidence closure.
- `semantic-audit --open-only` is deliberately valid only with `--recommendations --details`, because it is a detail filter rather than a different audit judgment.
- `cmu semantic-audit --recommendations --details --open-only --commands-only` now renders a compact command-only closure view for unresolved semantic-assisted receipts. It includes candidate `cmu use-link` commands when Git commits are plausible, `cmu use-resolve` commands for no-commit outcomes, and a reminder to choose one closure path per receipt. It remains read-only.
- `semantic-audit --commands-only` is deliberately valid only with `--recommendations --details --open-only`, because it is an operator view for closing unresolved semantic evidence rather than a separate audit judgment.
- `semantic-audit --recommendations --details` now exposes candidate-window tuning through `--limit`, `--hours`, and `--min-score`, so the same Git inspection knobs used internally can be adjusted during evidence closure without changing code.
- `semantic-audit --recommendations --details` now supports `--receipt <use-id>` as a focused detail filter. In open command-only mode this narrows the closure checklist to one semantic-assisted receipt while leaving the audit read-only.
- `semantic-audit --recommendations` now supports `--action link|partial|drag|positive|neutral|none` so the audit can render one recommendation bucket at a time. In command-only mode this makes closure work focus on one evidence class instead of mixing every open receipt together.
- `semantic-audit --recommendations --details` now supports `--candidate-limit <count>` to cap plausible Git commit suggestions per unresolved receipt while still showing no-commit resolution options. This keeps command-only closure output compact when several commits are plausible.
- `semantic-audit --recommendations --details --open-only --commands-only` now supports `--command-type all|link|resolve` so operators can render only Git-link commands or only no-commit resolution commands while closing semantic evidence.
- `semantic-audit --recommendations --details --open-only --commands-only` now supports `--resolve-outcome all|no-checkpoint|not-applicable|superseded` so resolve-only checklists can focus on one no-commit judgment at a time.
- `cmu evaluate-scenario` now provides the first structural Scenario/Evaluation Harness. It runs the real trigger, onboarding, preflight/ranking, Action Note, receipt-signal, candidate-memory expectation, and expectation-check logic in read-only mode, then reports whether the scenario supports a CMU assumption, exposes a gap, or needs human judgment.
- `cmu lifecycle` now provides the first Core Memory Lifecycle structural view. It connects Candidate readiness, Situation stable-proposal readiness, Practice/Anchor governance, use-review evidence, active challenge Candidates, Exception memories, Anti-Pattern memories, Question memories, and retired memories in one read-only report.
- `cmu trace-add` and `cmu trace-distill` now provide the first Raw Trace -> Distilled Memory Pipeline. Raw task activity is captured under `.cmu/raw_traces.json`, distillation previews whether the trace has reusable future value, and `trace-distill --apply` creates Candidate Memory only through the real `remember_candidate` gate.
- `cmu gravity` now provides the first Memory Gravity structural view. It scores placement pressure from scope axes, graph relationships, memory evidence, use-receipt evidence, liability, and stable-memory authority, then reports pressures such as promotion, stable promotion, merge, split, decay, governance review, and settle.
- `cmu retrieval-pipeline` now provides the first full Hybrid Retrieval Pipeline inspection surface. It connects candidate search, hard grounding, graph expansion, semantic support, use-evidence/liability ranking, and Action Note selection/rejection in one read-only report.
- `cmu governance` now provides the first Practice/Anchor Governance Loop view. It connects stable-memory authority, linked use evidence, active challenge pressure, allowed follow-up paths, and next governance action in one read-only report.
- `cmu analytics` now provides the first Usefulness and Drag Analytics view. It connects receipts, linked/unresolved evidence, strong committed uses, drag signals, retrieval adjustment, semantic provenance, and stable-memory governance state in one read-only report.
- `cmu work-cycle` now provides the first Full Work Cycle Integration proof view. It connects trigger, onboarding, preflight, Action Note or silence, receipt planning, after-work Candidate Memory decision, and matched-memory analytics/governance feedback in one read-only report.
- `cmu anti-pattern` now provides the first Anti-Pattern workflow view. It connects tempting paths to avoid, safer replacement paths, scope, retrieval fit for a task prompt, evidence, relationships, use receipts, and review pressure in one read-only report.
- `cmu question` now provides the first Question workflow view. It connects unresolved uncertainty, task-prompt surfacing, ownership, evidence, relationships, investigation paths, premature-assumption warnings, resolution conditions, and retired-history inspection.
- `cmu resolve-question` now explicitly answers and retires Question memories. It can retire a question directly or create a derived Situation/Exception memory while preserving the original Question as history.
- `cmu graph` now provides the first Graph Memory View. It reports connected components, isolated memories, dangling links, optional retired-history inclusion, and bounded bidirectional root-path traversal with explicit cycle/reference markers.
- `cmu.agent_api.AgentIntegration` now provides the first versioned Agent Integration Boundary. `cmu agent-tools` exposes its machine-readable manifest, and `cmu agent-call` invokes the same structured service from a runtime adapter. The first stable tools are `cmu_task_start`, `cmu_after_work`, `cmu_link_checkpoint`, and `cmu_review`.
- Manual agent-boundary validation proved the full direct runtime loop: matched task-start guidance created an `agent.task-start` receipt, after-work learning created a Candidate through the real quality gate, checkpoint metadata linked, review surfaced the linked evidence, and low-risk styling stayed silent with no receipt.
- `cmu authority` and `cmu authority-set` now provide the first Team and Authority Model. Authority is inspectable as accountable owner, approver, approver role, consequence level, permission result, approval time, and review expiry. Low/medium/high/critical consequence policy maps to agent/member/owner/org authority, while legacy `approved_by` memories remain readable and are explicitly marked for metadata enrichment.
- `cmu quality` and `cmu decay-apply` now provide the first Memory Quality and Decay Model. CMU scores scope, evidence, confidence, strong uses, drag, unresolved receipts, staleness, and authority expiry, then supports explicit weaken, demote, or retire actions. Stable-memory decay requires sufficient consequence-based approval and never mutates itself silently.
- `cmu readiness` now provides the first Memory Base Cleanup and Readiness workflow. It combines stable-memory authority blockers, orphan/unresolved Memory Use Receipts, graph isolates and dangling relationships, quality/decay pressure, missing Anti-Pattern coverage, missing Question coverage, and safe next actions into one read-only operator queue.
- `cmu doc-curate` now provides the first pre-memory Document History Curation gate. It scans markdown as evidence rather than truth, rejects stale documents by default, rejects superseded/out-of-date documents, requires reusable CMU memory signals before drafting, previews read-only by default, and only saves passing items as Candidate Memories with `--apply`. This prevents the first real memory-base seeding pass from blindly converting old project docs into active guidance.
- `cmu doc-curate --select <path-or-title> --apply` now supports selected apply, so a curation batch can review several candidate-ready docs while persisting only explicitly chosen Candidate Memories. The duplicate guard still blocks already persisted candidates, but batch preview no longer suppresses separate docs merely because they share common curation safety wording.
- `cmu seed-plan` now provides the first read-only Memory Seeding Workbench. It combines existing Candidate Memories, readiness coverage gaps, optional doc-curation preview decisions, rejected-doc Anti-Pattern/Question draft suggestions, and graph relationship suggestions into concrete next commands without mutating memories, receipts, relationships, or authority.
- `cmu portable-export` and `cmu portable-import` now provide the first Import/Export and Portability Layer. The versioned `cmu-portable-bundle/v1` format moves memories and use-receipt evidence across stores while preserving authority metadata, relationships, statuses, confidence/decay changes, and checkpoint evidence. Imports are dry-run by default, require `--apply` to write, and block conflicting existing records unless `--update-existing` is explicit.
- Manual portability validation proved the intended boundary: a portable bundle round-tripped into a separate store, restored records and receipts, previewed safely before writing, and refused to overwrite a changed local memory without explicit update intent.
- Manual graph validation showed the current real CMU store has four active memories but zero relationships, so all four memories are isolated. A temporary CLI-built fixture confirmed Situation -> Practice traversal, incoming Exception and Anti-Pattern branches, and explicit cycle/reference handling.
- Manual question validation showed the current real CMU store has no Question memories yet, which is another memory-base gap. A temporary manual fixture confirmed Question creation, relevant surfacing, explicit resolution into a Situation, original-question retirement, and retired-history inspection.
- Manual anti-pattern validation showed the current real CMU store has no Anti-Pattern memories yet, which is itself a memory-base gap. A temporary manual fixture confirmed an anti-pattern can become an active warning for a matching dependency-debugging prompt and render its trap, safer path, evidence, and review state.
- Manual work-cycle validation showed the current CMU implementation task flows through must-call trigger, Onboarding Seed, Action Note, planned receipt, candidate-ready after-work memory decision, and mixed analytics/governance feedback. A low-risk styling task correctly stayed silent with no receipt or memory draft.
- Manual analytics validation showed the current task-start preflight Practice is mixed rather than cleanly proven: 38 total uses, 28 linked uses, 10 unresolved uses, 16 strong uses, 4 drag signals, positive retrieval adjustment, and blocked stable governance because explicit authority is still missing.
- Manual governance validation showed the current task-start preflight Practice has substantial linked use evidence, but it is still blocked from broader stable trust because it lacks explicit approval metadata. This is the intended governance behavior: evidence alone does not silently upgrade stable authority.
- Manual retrieval-pipeline validation showed the current task-start preflight Practice correctly survives hard grounding, receives use-evidence score adjustment, receives semantic support when `--semantic local` is enabled, and produces an Action Note preview while unrelated Candidate memories are rejected by hard scope grounding.
- Manual gravity validation showed the task-start preflight Practice has very strong scope/use gravity but still carries governance review pressure and decay/review pressure because it lacks explicit authority and has mixed evidence. That means Memory Gravity is useful in the planned sense: it does not just say "this memory is strong"; it explains where it belongs and what must be resolved before broader trust.
- Manual trace validation showed the intended boundary working: a high-risk checkout rollback trace with evidence, future-use reason, and worked/failed lesson became a Candidate ready for Situation promotion, while a routine low-risk typo cleanup trace was rejected as noise and did not pollute memory.
- Manual lifecycle validation showed three active Candidate memories ready for Situation promotion and one active Practice with strong linked evidence plus mixed/drag signals and missing explicit approval. That means the lifecycle view is already exposing real structural work: promote the ready Candidates, review the Practice governance/evidence state, and avoid blindly trusting a stable memory without approval metadata.
- Manual harness validation produced three first proof data points: the known CMU preflight Practice surfaced as expected, a low-risk styling task stayed quiet as expected, and an unknown high-risk billing migration scenario exposed a missing-memory gap. This is the first concrete structural evidence that CMU can both help and reveal where its memory base is incomplete.
- Hardened tests now also cover consequence-permission enforcement, authority assignment persistence, legacy authority inspection, enriched stable-promotion permission refusal without mutation, expired-review governance blocking, quality scoring, decay-ready classification, stable decay authority refusal, controlled stable demotion, and persisted weakening. Current full test suite: 204 tests.

Important correction made:

- A temporary JSON-report-first `draft --agent-report` path was removed from the core workflow.
- The real core path is now direct agent tool-call style: `preflight` before work, `remember` after work.
- JSON reports may be useful later for batch import or debugging, but they are not the main product path.

## Current Product Flow

1. Agent begins meaningful work.
2. Agent runtimes can call `cmu_task_start` through `cmu.agent_api.AgentIntegration` or the `cmu agent-call` JSON adapter. The existing human-readable `cmu start` CLI remains available for inspection. Both evaluate the trigger layer first; if the decision is `silent-skip`, CMU stays quiet without onboarding, Action Notes, or receipts.
3. For `should-call` or `must-call`, `cmu start` renders the tiny Onboarding Seed and runs grounded preflight. Agents may still call `cmu trigger`, `cmu onboard`, or `cmu preflight` separately when they need one specific surface.
4. CMU returns a compact Action Note only if memory changes the next action.
5. When an Action Note is surfaced, CMU creates a Memory Use Receipt with `use_<id>` and records whether it came from `preflight`, `start`, or `agent.task-start`.
6. Agent performs the work.
7. If the work reaches a Git commit/checkpoint, the receipt can be linked with `cmu use-link-latest <use-id>` or `cmu use-link <use-id> --commit <hash>`. CMU can also propose recent commit matches with `cmu use-link-auto` and persist confident matches with `cmu use-link-auto --apply`. CMU reads commit metadata from Git instead of relying on the agent to report changed files.
8. CMU treats the commit as a grounded usefulness signal, with guardrails for mixed commits, WIP/checkpoint commits, delayed commits, low file overlap, missing file context, and reverts.
9. `cmu use-review` can review linked use evidence and produce non-mutating cards: strengthen evidence, review/narrow scope, use the challenge path, or link receipts before judging usefulness.
10. `cmu use-review --thresholds` can inspect the current threshold behavior across real receipts before changing auto-link or review tuning.
11. `cmu use-review <memory-id> --prepare strengthen` prepares evidence from repeated high-confidence committed uses; `--apply --approved-by <owner-or-team>` adds that evidence to the memory.
12. `cmu use-review <memory-id> --prepare challenge` prepares a stable-memory challenge from repeated drag evidence; `--apply` saves it as a Candidate challenge while leaving the stable memory unchanged.
13. `cmu use-review <memory-id> --prepare scope-review` produces a scope review proposal. With a use-review scope signal such as repeated drag, explicit `--scope-*` axes, and `--apply --approved-by <owner-or-team>`, it can update scope. Practice and Anchor updates through this path are limited to safe narrowing; broadening or material shifts must use challenge/split.
14. If the work produced reusable situational intelligence, the agent can call `cmu trace-add` to preserve raw task activity without immediately trusting it as memory.
15. `cmu trace-distill` previews whether raw traces contain enough future-use value to become Candidate Memory. With `--apply`, only traces that pass the real Candidate Memory gate create memory; routine noise is marked rejected.
16. Agents may still call `cmu remember` directly when they already have a structured Candidate Memory.
17. CMU validates minimum Candidate Memory quality: situation, evidence or outcome, scope, future-use reason, and at least one worked/failed lesson.
18. CMU saves Candidate Memory for future retrieval.
19. Candidate Memories can be reviewed with `cmu review`.
20. Candidate Memories can become Situation Memories with `cmu promote <id> --to situation` only when promotion gates pass.
21. Situation Memories can be reviewed with `cmu review <situation-id> --to practice` or `cmu review <situation-id> --to anchor` to produce proposal cards.
22. Situation Memories can become Practice or Anchor Memories with `cmu promote <situation-id> --to practice --approved-by <owner-or-team>` or `cmu promote <situation-id> --to anchor --approved-by <owner-or-team>` only when the proposal gate passes.
23. Practice and Anchor Memories can be challenged with `cmu challenge <practice-or-anchor-id> --mismatch <reason> --benefit <expected-benefit> --risk <risk> --rollback <rollback-path>`. The challenge is saved as a Candidate Memory for later review; the stable memory stays unchanged.
24. Challenge Candidates can be resolved with `cmu resolve-challenge <challenge-candidate-id> --outcome exception --approved-by <owner-or-team>` to create a scoped Exception Memory and retire the challenge Candidate.
25. Challenge Candidates can be resolved with `cmu resolve-challenge <challenge-candidate-id> --outcome strengthen --approved-by <owner-or-team>` to keep the stable memory and add approved evidence that strengthens the precedent.
26. Challenge Candidates can be resolved with `cmu resolve-challenge <challenge-candidate-id> --outcome update --approved-by <owner-or-team>` only when replacement summary/path/avoid/challenge details and resolution evidence are provided. The stable memory is updated in place and keeps rollback evidence from the challenge.
27. Challenge Candidates can be resolved with `cmu resolve-challenge <challenge-candidate-id> --outcome retire --approved-by <owner-or-team>` only when a retirement reason and resolution evidence are provided. The stable memory is retired, and rollback evidence remains attached.
28. Challenge Candidates can be resolved with `cmu resolve-challenge <challenge-candidate-id> --outcome split --approved-by <owner-or-team>` only when a split title, summary, path, avoid warning, challenge condition, scoped axes, and resolution evidence are provided. CMU creates a new scoped stable memory and records the split on the original.

## Manual Verification Commands

Use the bundled Python runtime on this machine:

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu --help
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu agent-tools
```

```powershell
ConvertTo-Json @{ prompt = "adjust button spacing"; actor = "agent"; area = "ui"; risk = "low" } -Compress |
  & "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu agent-call cmu_task_start --input-file -
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu list
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu authority
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu quality
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu preflight "implement the CMU task-start preflight action note behavior" --actor agent --area cmu --file cmu/retrieval.py --workflow implementation --env local --risk high --show-matches
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu retrieval-pipeline "implement CMU preflight behavior" --actor agent --area cmu --workflow implementation --risk high
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu retrieval-pipeline "implement CMU preflight behavior" --actor agent --area cmu --workflow implementation --risk high --semantic local
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu start "implement the CMU task-start work cycle" --actor agent --area cmu --file cmu/cli.py --workflow implementation --env local --risk high --show-matches
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu evaluate-scenario "implement CMU preflight behavior" --actor agent --area cmu --workflow implementation --risk high --expect-trigger must-call --expect-action action-note --expect-memory mem_8b1b78919fe6 --expect-candidate draft-recommended --learning-signal structural-proof --worked "The harness reused the real trigger/onboarding/preflight path." --future-use "Use this scenario to verify CMU task-start memory behavior." --evidence "Expected Practice memory surfaced."
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu evaluate-scenario "adjust button spacing" --actor agent --area frontend --file settings.css --workflow styling --risk low --expect-trigger silent-skip --expect-action quiet --expect-memory none --expect-candidate not-recommended
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu evaluate-scenario "debug unknown billing migration failure" --actor agent --area billing --workflow debugging --risk high --expect-trigger must-call --expect-action action-note
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu lifecycle
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu lifecycle --memory <memory-id>
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu gravity
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu gravity --memory <memory-id>
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu governance
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu governance --memory <memory-id>
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu analytics
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu analytics --memory <memory-id>
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu work-cycle "implement CMU full work cycle integration" --actor agent --area cmu --workflow implementation --risk high --learning-signal "new convention" --outcome "The command connected trigger onboarding preflight receipt planning and after-work memory decision." --worked "Use one read-only report to inspect the whole Work Cycle before automation." --future-use "Use when validating CMU task loop integration." --evidence "Manual work-cycle validation ran against the real CMU store."
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu work-cycle "adjust button spacing" --actor agent --area frontend --file settings.css --workflow styling --risk low
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu anti-pattern
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu anti-pattern "rerun failing tests without checking dependency versions" --actor agent --area tests --workflow debugging --risk high
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu question
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu question "debug checkout rollback release marker retry" --actor agent --area checkout --workflow deployment --risk high
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu resolve-question <question-id> --outcome situation --answer <answer> --resolved-by <owner-or-team> --evidence <evidence>
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu trace-add "Checkout rollback failed because release marker verification was skipped before retrying deployment" --actor agent --area checkout --file checkout/deploy.py --workflow deployment --risk high --learning-signal "explained failure" --outcome "Rollback passed after release marker verification was restored." --worked "Verify release markers before retrying checkout rollback deployment." --failed "Retrying deployment without marker verification repeated the rollback failure." --future-use "Use when checkout rollback or release marker deployment logic changes." --evidence "Manual trace verification showed candidate-ready distillation."
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu trace-distill --apply
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu trace-distill <trace-id> --apply
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu use-list
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu use-link-latest <use-id>
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu use-link <use-id> --commit <hash>
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu use-link-auto
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu use-link-auto --apply
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu use-resolve <use-id> --outcome no-checkpoint --note "<why no Git checkpoint should be linked>" --resolved-by "<owner-or-agent>"
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu use-summary <memory-id>
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu use-review
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu use-review <memory-id>
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu use-review --thresholds
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu semantic-audit
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu semantic-audit --memory <memory-id>
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu semantic-audit --recommendations
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu semantic-audit --recommendations --details
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu semantic-audit --recommendations --details --open-only
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu semantic-audit --recommendations --details --open-only --commands-only
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu semantic-audit --recommendations --details --open-only --commands-only --receipt <use-id>
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu semantic-audit --recommendations --details --open-only --commands-only --min-score 99
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu semantic-audit --recommendations --details --open-only --commands-only --candidate-limit 1
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu semantic-audit --recommendations --details --open-only --commands-only --action partial
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu semantic-audit --recommendations --details --open-only --commands-only --command-type link --candidate-limit 1
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu semantic-audit --recommendations --details --open-only --commands-only --command-type resolve --resolve-outcome superseded
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu use-review <memory-id> --prepare strengthen
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu use-review <memory-id> --prepare strengthen --apply --approved-by "<owner-or-team>"
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu use-review <memory-id> --prepare challenge --apply
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu use-review <memory-id> --prepare scope-review
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu use-review <memory-id> --prepare scope-review --apply --approved-by "<owner-or-team>" --scope-code "<narrower-code-scope>"
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu remember --situation "A dependency version quirk caused repeated test errors after failed attempts." --signal "repeated error" --signal "tooling quirk" --worked "Pin the tool version before rerunning tests." --future-use "Use when the same dependency version mismatch appears." --evidence "Tests passed after pinning the version." --liability 4 --scope-code tools --scope-workflow testing --scope-actor agent --confidence 0.8
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu review
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu review <candidate-id>
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu promote <candidate-id> --to situation
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu promote <situation-id> --to practice --approved-by "<owner-or-team>"
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu challenge <practice-or-anchor-id> --mismatch "<reason>" --benefit "<expected-benefit>" --risk "<risk>" --rollback "<rollback-path>"
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu resolve-challenge <challenge-candidate-id> --outcome exception --approved-by "<owner-or-team>"
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu resolve-challenge <challenge-candidate-id> --outcome update --approved-by "<owner-or-team>" --replacement-summary "<new stable summary>" --replacement-use-path "<new default path>" --replacement-avoid "<new warning>" --replacement-challenge "<new challenge condition>" --evidence "<resolution evidence>"
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu resolve-challenge <challenge-candidate-id> --outcome retire --approved-by "<owner-or-team>" --retirement-reason "<why this stable memory no longer guides work>" --evidence "<resolution evidence>"
```

```powershell
& "C:\Users\chait\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m cmu resolve-challenge <challenge-candidate-id> --outcome split --approved-by "<owner-or-team>" --split-title "<new scoped stable memory>" --split-summary "<split-off situation>" --split-use-path "<split-off default path>" --split-avoid "<split-off warning>" --split-challenge "<when to challenge this split>" --scope-code "<code scope>" --scope-workflow "<workflow scope>" --evidence "<resolution evidence>"
```

## Next Best Task

Shift from tiny polish slices to the CMU structural skeleton.

Why this is next:

- The recent semantic-audit controls are useful, but they are refinement features.
- CMU now needs a broad structural proof base so the project can test whether the full concept is true in practice.
- Every major concept should become live enough to produce outputs and evidence: Work Cycle, retrieval, candidate drafting, memory lifecycle, governance, usefulness/drag, graph structure, Anti-Pattern, Question, authority, decay, and portability.
- Once the skeleton produces evidence, smaller polish features can be chosen with much better clarity.

Current large implementation slice completed:

The first Scenario/Evaluation Harness now exists through `cmu evaluate-scenario`. It is CMU's first measuring instrument: a repeatable way to run or model a task scenario, invoke trigger/onboarding/preflight, observe whether an Action Note appears or CMU stays quiet, track candidate-memory and evidence expectations, and produce a compact report about what CMU proved or failed to prove.

Current large implementation slice completed:

The first Core Memory Lifecycle structural view now exists through `cmu lifecycle`. Candidate -> Situation -> Practice/Anchor/Exception, challenge/resolve, use evidence, and retirement visibility are now connected in one read-only proof report. This does not yet automate the whole lifecycle, but it makes the lifecycle legible and measurable end to end.

Current large implementation slice completed:

The first Raw Trace -> Distilled Memory Pipeline now exists through `cmu trace-add` and `cmu trace-distill`. CMU can preserve raw task activity separately from trusted memory, preview whether it contains reusable future value, and only create Candidate Memory through the existing Candidate quality/duplicate gate. Manual verification confirmed both sides of the boundary: a high-risk deployment trace became a Candidate ready for Situation promotion, while routine typo cleanup was rejected as noise.

Current large implementation slice completed:

The first Memory Gravity structural view now exists through `cmu gravity`. It makes placement and settling pressure visible before mutation: scope center, graph pull, evidence pull, use-evidence pull, liability pull, authority pull, and next action. Manual verification showed real gravity signals on the current CMU store, especially the main Practice's strong use gravity plus unresolved authority and mixed-evidence pressure.

Current large implementation slice completed:

The first Full Hybrid Retrieval Pipeline inspection surface now exists through `cmu retrieval-pipeline`. It shows the whole retrieval path without creating receipts: candidate search, hard grounding, graph expansion, semantic support, use-evidence/liability ranking, below-threshold outcomes, rejected memories, selected Action Note, and Action Note preview.

Current large implementation slice completed:

The first Practice/Anchor Governance Loop now exists through `cmu governance`. It reads stable Practice and Anchor memory, shows explicit authority state, linked use evidence, active challenge pressure, allowed follow-up paths, and the next governance action without mutating memories or receipts. Manual verification showed the current task-start preflight Practice is still blocked by missing explicit authority even though it has strong linked use evidence, which is the correct governance result.

Current large implementation slice completed:

The first Usefulness and Drag Analytics view now exists through `cmu analytics`. It classifies memory evidence as useful, mixed, drag, neutral, or evidence-gap by using real Memory Use Receipts, linked checkpoint evidence, unresolved receipt counts, use-review judgment, retrieval adjustment, semantic provenance, and stable-memory governance state. Manual verification showed the current CMU Practice is mixed, not cleanly proven: strong evidence exists, but unresolved receipts, drag signals, and missing authority still require closure.

Current large implementation slice completed:

The first Full Work Cycle Integration proof view now exists through `cmu work-cycle`. It runs the full task loop in read-only form: trigger decision, task-bound onboarding, grounded preflight, Action Note or silence, receipt planning, after-work Candidate Memory decision, and review/analytics feedback for the matched memory. Manual verification showed the high-risk CMU implementation path reaches Action Note plus candidate-ready after-work memory decision, while a low-risk styling path stays silent with no receipt or memory draft.

Current large implementation slice completed:

The first Anti-Pattern workflow now exists through `cmu anti-pattern`. It gives tempting paths to avoid a dedicated view instead of leaving them as passive records: trap, avoid warning, safer path, review condition, scope, task retrieval fit, evidence, relationships, use evidence, and review pressure. Manual verification showed the real store has no anti-pattern memories yet, and a manual fixture proved a dependency retry anti-pattern becomes an active warning for a matching debugging task.

Current large implementation slice completed:

The first Question workflow now exists through `cmu question` and `cmu resolve-question`. Unresolved uncertainty has a dedicated workflow: task-prompt surfacing, ownership/evidence/relationship gaps, investigation path, premature-assumption warning, resolution condition, explicit answer evidence, retirement, optional derived Situation/Exception creation, and retired-history inspection. Manual verification showed the real store has no Question memories yet, and a manual fixture proved the full create -> surface -> resolve -> derived Situation -> retired-history path.

Current large implementation slice completed:

The first Graph Memory View now exists through `cmu graph`. It makes graph-backed memory inspectable beyond flat records and one-hop relationship lists: global component/isolate/dangling diagnostics, optional retired-history inclusion, and bounded bidirectional root-path traversal with explicit cycle/reference markers. Manual verification showed the real store currently has four isolated memories and no relationships, while a temporary CLI-built fixture proved Situation -> Practice traversal with Exception and Anti-Pattern branches.

Current large implementation slice completed:

The first Agent Integration Boundary now exists through the versioned `cmu.agent_api.AgentIntegration` service plus `cmu agent-tools` and `cmu agent-call`. Agents and runtime adapters can invoke four stable structured tools without parsing hand-operated CLI output: `cmu_task_start`, `cmu_after_work`, `cmu_link_checkpoint`, and `cmu_review`. The boundary reuses the real trigger/retrieval/onboarding path, Candidate quality gate, Git/manual checkpoint linker, and usefulness/drag review cards. Manual and automated verification proved the full direct loop: matched task-start guidance created an `agent.task-start` receipt, after-work learning created a Candidate Memory, checkpoint evidence linked, review surfaced the linked receipt, and a low-risk styling task stayed silent with no receipt.

Current large implementation slice completed:

The first Team and Authority Model now exists through `cmu authority` and `cmu authority-set`. Memory records can carry accountable owner, approver role, consequence level, approval time, and review expiry alongside backward-compatible `approved_by`. New stable adds/promotions can record the full authority envelope immediately, governance blocks expired reviews, and explicit assignment refuses underpowered approvers. Manual verification showed the real CMU Practice is currently missing owner and approval metadata, which is now visible as a concrete authority gap.

Current large implementation slice completed:

The first Memory Quality and Decay Model now exists through `cmu quality` and `cmu decay-apply`. Quality scores connect scope, evidence, confidence, linked use strength, drag, unresolved receipts, stale age, and authority expiry. Controlled actions can weaken, demote, or retire memory only from evidence-backed review/decay states; stable memory requires sufficient consequence authority. Manual verification classified the current CMU Practice as `decay-ready`: its 16 strong uses are outweighed by 4 drag signals and 10 unresolved receipts, so CMU now asks for evidence closure and governance review instead of silently trusting or deleting it.

Recent large implementation slice completed:

The first Import/Export and Portability Layer now exists through `cmu portable-export` and `cmu portable-import`. CMU can emit a versioned `cmu-portable-bundle/v1` JSON bundle containing memories and use-receipt evidence, with integrity metadata and warnings for non-exported relationship/receipt references. Import produces a dry-run plan by default, writes only with `--apply`, blocks conflicting existing records by default, and requires `--update-existing` for replacement. Manual verification round-tripped a bundle into a separate store and confirmed conflict protection.

Recent hardening/package slice completed:

The first Scenario Library hardening slice now exists through `cmu scenario-add`, `cmu scenario-list`, and `cmu scenario-run`. Saved scenarios live under `.cmu/scenarios.json`, preserve the same trigger/action/memory/candidate expectations used by `cmu evaluate-scenario`, and can be filtered by tag or run as a read-only regression suite. `scenario-run --strict` exits non-zero when any saved scenario needs review, which gives CMU a practical route toward fixture-backed CI and longitudinal behavior checks without creating Memory Use Receipts.

Automated verification now covers saving a scenario, listing it by tag, running it through the real evaluator with a passing expected Practice match, preserving read-only receipt behavior, and returning strict failure when a saved gap still expects an Action Note. Current full test suite: 210 tests.

Recent hardening/package slice completed:

The first Quickstart Demo flow now exists through `cmu quickstart-demo`. Dry-run mode explains the proof loop without mutating state. `cmu quickstart-demo --apply` requires a Git repository, seeds a scoped Practice memory, runs the real task-start trigger/onboarding/retrieval path, creates a Memory Use Receipt, writes a tiny demo checkpoint file, commits it, links the receipt to the Git checkpoint, and renders the resulting use summary. This gives CMU the five-minute proof path described in the product direction: memory checks before work, changes the next action, reaches a checkpoint, and shows usefulness evidence.

Manual verification in an isolated Git repo under `.manual` confirmed the intended loop: dry-run stayed non-mutating, apply surfaced the demo Practice, created receipt `use_ba3512bfec12`, committed `CMU quickstart demo proof`, linked the receipt to commit `7e09a17...`, and rendered `Committed: 1`, `Average Confidence: 0.85`, and `Retrieval Adjustment: +0.25`. Automated verification now covers dry-run non-mutation and applied Git-backed receipt linking. Current full test suite: 212 tests.

Recent hardening/package slice completed:

The first Python SDK facade now exists through `cmu.sdk.CentralMemoryUnit`, exported from `cmu`. It wraps the stable `AgentIntegration` boundary with named methods: `tools()`, `task_start(...)`, `after_work(...)`, `link_checkpoint(...)`, and `review(...)`. This is the Mem0-style ergonomics slice from the hardening plan: Python runtimes can integrate CMU without shelling out, parsing human CLI output, or manually passing raw tool names everywhere.

Manual verification in an isolated `.manual` store confirmed the intended SDK loop: `CentralMemoryUnit.task_start` surfaced an Action Note and created receipt `use_9d441501f1dd`; `after_work` saved one Candidate Memory for a reusable manual-verification lesson; `link_checkpoint` linked manual commit evidence with outcome `committed`; and `review` returned one linked use. Automated verification covers the named-method loop and silent-skip behavior. Current full test suite: 214 tests.

Current hardening/package slice completed:

The first Portable Bundle Compatibility Gate now exists through `cmu portable-validate` and `cmu.portable.validate_portable_bundle`. It validates bundle schema, integrity counts, content digest, duplicate memory/use IDs, parseability, and missing receipt-memory references without importing anything. Bundle loading now accepts UTF-8 BOM JSON files as well, because Windows/manual tooling can produce them during portability checks.

Manual verification in an isolated `.manual` store confirmed the intended compatibility discipline: a clean exported `cmu-portable-bundle/v1` passed validation with 1 memory, 0 receipts, 0 errors, and 0 warnings; a tampered bundle failed before import with `integrity.memory_count expected 42; actual 1` and `integrity.contents_sha256 mismatch`. Automated verification covers clean CLI validation, BOM-tolerant validation, tampered count/digest failure, and duplicate memory ID failure. Current full test suite: 216 tests.

Current hardening/package slice completed:

The first production-quality local MCP adapter now exists through `cmu.mcp`, `cmu mcp`, and the `cmu-mcp` script entrypoint. It exposes exactly the stable CMU agent tools as MCP tools: `cmu_task_start`, `cmu_after_work`, `cmu_link_checkpoint`, and `cmu_review`. The adapter implements MCP stdio JSON-RPC discovery/calls (`initialize`, `tools/list`, `tools/call`, and `ping`) and returns MCP content plus structured JSON-friendly CMU responses.

The MCP layer is deliberately thin: every tool call delegates to `AgentIntegration.invoke(...)`, so task-start trigger/retrieval behavior, silent-skip receipt behavior, Candidate quality gates, duplicate rejection, checkpoint linking, read-only review cards, authority rules, and stable-memory safety gates remain owned by the existing CMU core boundary. The adapter supports configurable project root through `--root`, defaulting to the current working directory.

The integration notes are kept in this progress file instead of a separate MCP-only markdown file. Run locally with `cmu --root <project-root> mcp` or the installed script `cmu-mcp --root <project-root>`. A generic MCP host should configure command `cmu-mcp` with args `["--root", "<project-root>"]`. Recommended workflow: call `cmu_task_start` before meaningful work, do the work, call `cmu_after_work` only when reusable learning appeared, call `cmu_link_checkpoint` after a checkpoint or commit, then call `cmu_review` for read-only usefulness/drag evidence.

Manual verification used isolated temporary CMU stores and direct MCP JSON-RPC calls. It confirmed tool discovery, a matching `cmu_task_start` Action Note with `agent.task-start` receipt creation, `cmu_after_work` Candidate Memory creation through the quality gate, and `cmu_review` read-only evidence output. Automated verification now covers MCP discovery/schema metadata, silent-skip no-receipt behavior, matching Practice Action Note and receipt creation, Candidate save and weak/noisy rejection through the existing gate, manual checkpoint linking, read-only review evidence, structured invalid-input failures, and explicit proof that MCP tool calls delegate to `AgentIntegration.invoke(...)`. Current full test suite: 224 tests.

Current hardening/package slice completed:

The first Memory Base Cleanup and Readiness workflow now exists through `cmu readiness`. It is deliberately read-only and operator-facing: it gathers authority gaps from governance, unresolved/orphan receipt state from the real receipt store, graph isolates/dangling links from the graph view, quality/decay pressure from the quality model, and missing Anti-Pattern/Question coverage into one prioritized cleanup queue with safe next actions.

Manual verification against the current workspace store ran `cmu --root . readiness`. Because the real `.cmu` store is currently empty, the command correctly reported 0 memories, 0 receipts, and only the two coverage gaps for missing active Anti-Pattern and Question memories. Automated verification covers report prioritization from real persisted memories/receipts, CLI rendering with retired-history inclusion, quality/decay detection, coverage-gap suppression when Anti-Pattern/Question records exist, and read-only behavior. Current full test suite before the document-curation slice: 227 tests.

The first pre-memory Document History Curation gate now exists through `cmu doc-curate`, and the first Memory Seeding Workbench now exists through `cmu seed-plan`. The curation gate is deliberately conservative: markdown is treated as evidence, not authority; stale markdown is rejected by default; document-level superseded/out-of-date markers block candidate drafting; reusable signal markers are required; preview mode is read-only; and `--apply` persists only Candidate Memories that must still pass normal review/promotion/authority gates. Selected apply through `--select` now lets operators persist one chosen candidate from a larger curation batch.

`cmu seed-plan` stays read-only and bridges curation into governed cleanup: it reports Candidate -> Situation review commands, missing Anti-Pattern/Question coverage commands, rejected-doc Anti-Pattern/Question draft suggestions, and graph relationship suggestions. Focused automated tests now verify read-only preview, selected apply persistence, stale rejection, superseded rejection, seed-plan promotion/coverage/graph suggestions, and rejected-doc manual draft suggestions. Manual verification ran real CLI commands against current docs and a fresh temp CMU root: current strategic docs were candidate-ready in preview, `seed-plan --doc CMU_Major_Unfinished_Work.md` reported missing Anti-Pattern/Question coverage plus a Question draft, selected apply persisted exactly one chosen Candidate to the temp root, and `cmu list --type candidate` confirmed only that selected Candidate existed. Current full test suite: 231 tests.

Current cleanup/data slice completed:

The first real workspace memory-base seeding pass has been applied to the actual `.cmu` store. The pass deliberately used the existing governed surfaces rather than direct JSON editing: `cmu doc-curate` previewed current strategic markdown, `cmu seed-plan` identified coverage gaps and rejected-doc draft suggestions, `cmu doc-curate --apply --select CMU_Decisions_And_Assumptions.md` persisted one selected Candidate, `cmu review` confirmed the Candidate -> Situation gate passed, and `cmu promote` converted it to a Situation.

The cleanup pass also created two explicit active coverage memories with `cmu add`: an Anti-Pattern warning against blind strategic-markdown import, grounded in `doc-curate` rejecting `CMU_Major_Unfinished_Work.md` and `CMU_Implementation_Progress.md` as superseded-marker documents, and a Question tracking which hardening priority should follow once cleanup readiness is clear. No Practice or Anchor memory was created or approved, so no stable authority metadata was assigned.

Graph cleanup was applied with `cmu relate`: the Situation, Anti-Pattern, and Question now form one connected component with three relationships, zero isolated active memories, and zero dangling relationships. Manual verification after the pass showed `cmu readiness` reviewing 3 memories, 0 receipts, 1 Anti-Pattern, 1 Question, and no missing coverage gaps. The only remaining readiness items are P3 quality watch cards because the new memories have no linked use receipts yet. `cmu governance` correctly reports 0 stable memories and no authority blockers.

Current hardening/package slice completed:

The first host setup guidance surface now exists through `cmu setup-guide`. It is read-only and generates CLI, Python SDK, MCP, and Codex-style MCP setup guidance from real project state instead of static prose. The report checks whether the root has `.cmu/memories.json` and `.cmu/uses.json`, whether Git is available for `quickstart-demo --apply`, which script entrypoints are declared in `pyproject.toml`, which tools the live `AgentIntegration` manifest exposes, and which tools the MCP schema exposes.

`cmu setup-guide --host cli|python-sdk|mcp|codex|all` lets a human or agent narrow the setup output to the adoption surface they are wiring. The command deliberately does not initialize stores, create memories, create receipts, or write Git checkpoints; it recommends `cmu init`, `cmu readiness`, `cmu quickstart-demo`, `cmu quickstart-demo --apply`, SDK calls, and MCP host config based on actual readiness.

Automated verification now covers the core setup guide and CLI command through real filesystem, `.cmu` store, Git repository, and `pyproject.toml` paths. The tests assert that reported Agent/MCP tool names are derived from the actual `AgentIntegration` manifest and `mcp_tool_definitions()` rather than copied fixtures, and that the command does not mutate memory or receipt stores. Manual verification ran `cmu --root . setup-guide --host all`, `--host mcp`, and `--host python-sdk` against the workspace and confirmed initialized store state, Git readiness, `cmu`/`cmu-mcp` scripts, exact tool names, and setup instructions. Current full test suite: 233 tests.

Current hardening/package slice completed:

The first README-level quickstart and editable-install discipline slice now exists. The root `README.md` gives a fresh-checkout path through editable install, `cmu init`, `cmu readiness`, `cmu quickstart-demo`, `cmu quickstart-demo --apply`, and `cmu setup-guide --host all`, then documents the CLI work cycle, Python SDK facade, MCP host config, Codex-style MCP setup, stable MCP tools, and trust rules.

`pyproject.toml` now declares `README.md`, setuptools build metadata, package discovery for `cmu*`, and the existing `cmu` / `cmu-mcp` script entrypoints. `cmu setup-guide` now uses `python -m cmu --root <project-root> mcp` as the primary local-development fallback while still noting the Windows `py` launcher when available.

Automated verification now includes README/package adoption tests that read the actual repository README and `pyproject.toml`, then compare them with the live `setup_guide(...)` report, `pyproject.toml` script table, MCP server name, and `mcp_tool_definitions()` output. Manual verification ran `cmu --root . setup-guide --host all`, `cmu --root . quickstart-demo`, the full unittest suite, and an isolated `.manual` Git repository with `cmu quickstart-demo --apply`; the applied proof created a Practice memory, task-start receipt, Git checkpoint, linked receipt evidence, and rendered a usefulness summary. Current full test suite: 236 tests.

Current hardening/package slice completed:

The first install/adoption validation gate now exists through `cmu install-check` and `cmu.install_check.install_check`. It is read-only and validates the real adoption boundary: root README presence and required quickstart commands, `pyproject.toml` README binding, setuptools build backend, package discovery, `cmu` and `cmu-mcp` console script targets, SDK import, `python -m cmu` module entrypoint, setup-guide consistency, and MCP server/tool schema.

The command returns a compact pass/fail report with one line per checked surface and specific failure details when a checkout is incomplete. It deliberately does not initialize stores, create memories, create receipts, or write Git checkpoints.

Automated verification covers the real checkout passing against live README/pyproject/setup-guide/MCP data, CLI read-only behavior against the actual workspace `.cmu` store files, and a deliberately incomplete checkout fixture that fails for missing README commands, wrong README binding, and incomplete console scripts. Manual verification ran `cmu --root . install-check` and confirmed a clean pass. Current focused adoption suite: 10 tests.

Current hardening/package slice completed:

The first scripted local demo walkthrough now exists through `cmu demo-walkthrough` and `cmu.demo_walkthrough.demo_walkthrough`. It composes the real package/adoption gate, host setup guidance, quickstart proof loop, and next work-cycle handoff into one operator-facing report.

The default walkthrough is read-only: it runs `install-check`, summarizes `setup-guide --host all`, renders the dry quickstart proof plan, and shows the next real `cmu start` handoff without initializing stores, creating memories, creating receipts, or writing Git checkpoints. `cmu demo-walkthrough --apply` deliberately delegates to the existing `quickstart-demo --apply` path, so the mutating walkthrough creates the same demo Practice memory, task-start receipt, Git checkpoint, linked receipt evidence, and usefulness summary as the established proof loop.

Automated verification now covers dry walkthrough composition against the real checkout, CLI dry-run read-only behavior against actual `.cmu` store files, and applied walkthrough behavior in a temporary Git checkout with real README/pyproject adoption files. Manual verification ran `cmu --root . demo-walkthrough` and confirmed a clean pass. Current focused adoption/demo suite: 13 tests.

Current hardening/package slice completed:

The first isolated built-distribution validation gate now exists through `cmu dist-check` and `cmu.dist_check.dist_check`. It creates a temporary validation venv, installs CMU from the checkout as a built package, and validates installed behavior from outside the source working directory.

The gate verifies the installed `cmu` console script, installed `python -m cmu` module entrypoint, installed `cmu install-check`, installed `cmu demo-walkthrough`, and installed `cmu-mcp` MCP tool discovery. It uses `--no-build-isolation` plus local build-backend access so validation does not need network access while still proving wheel build/install and entrypoint behavior.

Automated verification now includes a real distribution test that builds/installs CMU in a temporary validation workspace and checks installed CLI/module/MCP behavior. Manual verification ran `cmu --root . dist-check` and confirmed the temporary venv install passed, installed adoption commands worked, and MCP discovery returned `cmu_task_start`, `cmu_after_work`, `cmu_link_checkpoint`, and `cmu_review`. Current focused adoption/demo suite: 14 tests.

Current autonomous-runner integration slice completed:

The first autonomous-runner hook surface now exists through `cmu.runner_hooks.AutonomousRunnerHooks`, `cmu.runner_hooks.runner_hooks_report`, and `cmu runner-hooks`. The Python hook facade exposes event-shaped calls for `before_task`, `after_task`, `after_checkpoint`, and `review`, while delegating to the existing `CentralMemoryUnit` / `AgentIntegration` boundary rather than recreating task-start, Candidate Memory, checkpoint, or review logic.

`before_task` calls the real task-start path and creates a receipt only when an Action Note surfaces. `after_task` explicitly skips routine work unless reusable learning is declared, then uses the existing Candidate Memory quality gate. `after_checkpoint` links receipt evidence through the existing checkpoint path, and `review` reads usefulness/drag cards without mutating stable trust. The `cmu runner-hooks` command renders the hook contract read-only with no prompt and can execute the real `before_task` hook, including JSON output, when supplied a task prompt.

Automated verification covers the hook manifest, real action-note receipt creation, silent-skip no-receipt behavior, no-learning after-task skip, reusable-learning Candidate Memory persistence, manual checkpoint linking, review evidence, CLI contract rendering, CLI JSON execution, and read-only no-prompt report behavior against real temporary stores. The README and `install-check` adoption validator now include `cmu runner-hooks`. Focused verification ran `AutonomousRunnerHooksTests` plus the adoption/quickstart test class successfully.

Current runner scenario evidence slice completed:

The first runner-hook scenario evidence surface now exists through `cmu.runner_scenarios.run_runner_scenario`, `RunnerScenarioRequest`, and `cmu runner-scenario`. It copies the source active memories and existing receipts into a temporary isolated CMU store under `.manual`, executes the real autonomous runner hooks there, and reports before-task, after-task, checkpoint, review, and expectation-check outcomes without creating source memories or source receipts.

The command supports start expectations (`action-note`, `quiet`, `silent-skip`), surfaced-memory expectations, Candidate outcomes (`candidate-saved`, `candidate-not-saved`, `skipped-no-reusable-learning`, `not-run`), checkpoint outcomes, strict non-zero failure, optional after-task reusable-learning fields, and optional manual checkpoint metadata. This gives CMU a regression-style way to prove runner lifecycle behavior without using the mutating `cmu runner-hooks <task>` path against the real store.

Automated verification covers full isolated lifecycle proof with action note, Candidate save, checkpoint link, and review; silent-skip plus after-task no-learning skip; CLI strict pass without source receipt mutation; and CLI strict failure when expectations miss. The implementation also avoids initializing a missing source `uses.json` while snapshotting receipts, preserving the read-only source-store claim.

Current Codex runner adapter slice completed:

The first host-specific autonomous-runner adapter now exists through `cmu.codex_adapter.CodexRunnerAdapter`, `codex_runner_report`, and `cmu codex-runner`. The adapter accepts Codex-style JSON events for `codex.task_started`, `codex.task_finished`, `codex.checkpoint_created`, and `codex.review_requested`, normalizes shorthand event names, and routes them into `AutonomousRunnerHooks` rather than duplicating CMU memory logic.

`cmu codex-runner` renders the adapter contract read-only with no input. With `--input`, `--input-file`, or stdin, it executes one event and returns either a human report or JSON. Task-start events can create receipts only when the real `before_task` hook surfaces memory, task-finished events save Candidate Memory only through the existing after-work quality gate, checkpoint events link real receipt evidence, and review events remain read-only. The input-file path now accepts UTF-8 BOM files because manual Windows verification exposed that as a real host-wiring issue.

Automated verification covers read-only manifest behavior, event-to-hook mapping, action-note receipt creation through `MemoryUseStore`, Candidate Memory persistence through `MemoryStore`, manual checkpoint linking, read-only review evidence, CLI JSON execution, invalid-event failure, and UTF-8 BOM input-file handling. Manual verification ran `cmu codex-runner`, a BOM-backed low-risk `task_started` event that correctly returned `silent-skip` with no receipt, `cmu install-check`, and the full unittest suite. Current full test suite: 256 tests.

Current scenario comparison hardening slice completed:

The first before/after scenario comparison surface now exists through `cmu scenario-compare` and `cmu.scenarios.compare_scenario_library`. It loads saved scenario-library cases from the current root, evaluates each case against a baseline CMU root and the current CMU root, then classifies behavior as regressed, improved, changed, or unchanged based on real trigger/retrieval/Candidate expectation results.

`scenario-compare --strict` returns non-zero only when a passing baseline becomes a current review case. That gives CMU a practical regression gate for memory-base changes, retrieval changes, and fixture-backed scenario suites without creating Memory Use Receipts or mutating either store's memory evidence.

Automated verification covers improvement detection from real baseline/current `MemoryStore` and `MemoryUseStore` data, CLI strict regression failure against real saved scenarios, report rendering, and no receipt creation in either compared root. Manual verification created a baseline/current pair under `.manual/scenario-compare-20260609001237`, seeded only the baseline with a real Practice memory, saved a current scenario expecting that memory, ran `cmu scenario-compare --strict`, observed the expected regression/non-zero result, and confirmed `cmu use-list` reported no receipts in either root. Current full test suite: 258 tests.

Current automatic evidence-loop slice completed:

The first checkpoint monitor now exists through `cmu.evidence_monitor.monitor_checkpoints` and `cmu evidence-monitor`. It inspects recent Git commits, reuses the existing auto-link scorer to find plausible receipt/checkpoint matches, then applies only clean high-confidence committed links when `--apply` is supplied. Dry-run is the default.

The monitor deliberately refuses to auto-apply WIP/checkpoint, reverted, mixed, delayed, no-overlap, low-confidence, ambiguous, or otherwise risky evidence. Those cases are reported as `needs-review`, keeping background-style evidence automation conservative while still reducing manual work for clean checkpoint links.

Automated verification covers direct monitor dry-run/apply behavior against a real Git commit and persisted `MemoryUseStore`, CLI `evidence-monitor --apply` clean-link behavior, and WIP checkpoint review behavior that leaves the original receipt unmutated. Manual verification created a temporary Git repo under `.manual/evidence-monitor-proof`, added a real Practice memory, ran `cmu start` to create receipt `use_6d0fb79e720d`, committed `billing/deploy.py`, ran `cmu evidence-monitor --apply`, and confirmed `cmu use-list` showed the receipt linked as committed to commit `de3a23b...`. Current full test suite: 260 tests.

Current governance/review UX slice completed:

The first compact review queue now exists through `cmu.review_queue.review_queue` and `cmu review-queue`. It gathers Candidate -> Situation promotion cards, Situation -> Practice/Anchor authority cards, stable-memory authority cards, team-scope coverage cards, strengthen approval cards, governance review cards, challenge-resolution cards, and quality/decay review cards into one read-only operator queue.

The queue deliberately does not apply approvals or edits. Instead, each card points at the existing controlled command path: `cmu promote`, `cmu authority-set`, `cmu use-review --prepare`, `cmu resolve-challenge`, `cmu decay-apply`, or `cmu quality`. This keeps the new UX compact without weakening CMU's authority rule that stable Practice/Anchor memory must be changed deliberately.

Automated verification covers review-queue cards for candidate promotion, Practice/Anchor stable approval, missing authority, strengthen approval, active challenge resolution, and decay review through real `MemoryStore`, `MemoryUseStore`, challenge creation, strong/drag receipt evidence, and CLI rendering. Manual verification ran `cmu --root . review-queue` against the current workspace store and showed two read-only approval cards for the curated Situation memory. Current full test suite: 262 tests.

Current lifecycle automation slice completed:

The first controlled lifecycle apply path now exists through `cmu.lifecycle_apply.apply_lifecycle_candidates` and `cmu lifecycle-apply --candidate-ready`. It previews by default and, with `--apply`, promotes only Candidate memories that already pass the existing Candidate -> Situation promotion gate. Blocked Candidates remain unchanged and report their missing gate fields.

This slice intentionally avoids automating Situation -> Practice/Anchor promotion; those stable-memory transitions still require explicit authority through `cmu promote --to practice|anchor --approved-by ...`. The point is to make low-risk Candidate settling available without weakening the stable-memory authority rule.

Automated verification covers dry-run no-mutation behavior, apply promotion for gate-passing Candidates, blocked Candidate preservation, and CLI rendering through real persisted memory stores. Manual verification ran `cmu --root . lifecycle-apply --candidate-ready` on the workspace store and reported no eligible Candidate memories with no mutations. Current full test suite: 264 tests.

Current team-scope directory slice completed:

The first local Multi-Repo/Team/Org Memory boundary now exists through `cmu.team_directory.TeamDirectoryStore`, `cmu team-scope-add`, and `cmu team-scope`. Operators can record local repo/team ownership boundaries with owner, code/workflow/environment scope, authority role, and consequence, then inspect whether active memories cover those boundaries.

The slice is deliberately conservative. `cmu team-scope-add` writes only explicit team-scope boundary records under `.cmu/team_scopes.json`; `cmu team-scope` is read-only and compares those records against active memory scope without changing memories, receipts, authority, or graph relationships. `cmu review-queue` also reads the same team-scope records and surfaces uncovered boundaries as P1 review cards. This gives CMU a first local guardrail against false transfer across repos and teams while leaving cross-repo authority and richer delegation for later.

Automated verification covers persisted team-scope records, matching active memory coverage, uncovered-boundary reporting, false environment-only coverage rejection, review-queue team-scope coverage cards, read-only empty inspection, and CLI rendering through real `TeamDirectoryStore` and `MemoryStore` paths. Manual verification under `.manual/team-scope-proof` created a checkout-service/Release boundary, added a scoped Practice memory, confirmed `cmu team-scope` reported one matching memory with no missing coverage, then added an uncovered billing-service/Billing boundary and confirmed `cmu review-queue` reported it as a P1 `team-scope-coverage` card rather than falsely matching on `prod` environment alone. `cmu --root . install-check` passed and the current full test suite is 269 tests.

Current portable compatibility fixture slice completed:

The first portable bundle compatibility fixture gate now exists through `cmu.portable_compat.portable_compat_report` and `cmu portable-compat --fixture-dir <dir>`. It scans saved portable bundle JSON fixtures, validates current-schema fixtures, verifies intentionally invalid fixtures fail, and verifies future-schema fixtures fail safely as unsupported.

This slice makes portability more than a one-off export/import demo. Fixture names encode expectations: `valid-*.json` must pass the current `cmu-portable-bundle/v1` validator, `invalid-*.json` must fail validation, and `future-*.json` must fail because the schema is unsupported. That gives future schema work a simple regression gate: a migration can intentionally change which fixtures are accepted, but unknown future bundles are not silently trusted.

Automated verification builds compatibility fixtures from real `MemoryStore` exports, then checks valid, invalid, future-schema, CLI pass, and CLI fail behavior through the actual portable validation path. Manual verification under `.manual/portable-compat-proof` created a real memory through `cmu add`, exported a valid fixture through `cmu portable-export`, derived invalid and future-schema fixtures from that bundle, and confirmed `cmu portable-compat` passed all expected fixture outcomes.

Current review reminder slice completed:

The first lightweight expiry/review reminder surface now exists through `cmu.review_reminders.review_reminders` and `cmu review-reminders`. It turns expired stable-memory authority reviews, due-soon authority reviews, approved stable memories with no review date, and open high-priority review-queue cards into small read-only reminders.

This slice keeps governance lightweight without making it automatic. Reminders point back to explicit existing commands such as `cmu authority`, `cmu authority-set`, `cmu promote`, and other review-queue follow-ups; they do not renew authority, promote memory, resolve challenges, or mutate receipts.

Automated verification covers expired, due-soon, unscheduled, and open Candidate-promotion reminders through real `MemoryStore`, `MemoryUseStore`, and CLI paths, including a read-only persisted-store check. Manual verification under `.manual/review-reminders-proof` created a stable Practice memory with expired authority through `cmu add`, then confirmed `cmu review-reminders --days 30` reported a P0 `authority-review-expired` reminder without mutation.

Current machine-readable reminder delivery slice completed:

The review reminder surface now has a non-CLI delivery contract through `cmu review-reminders --json` and `ReviewRemindersReport.to_delivery_payload()`. The payload includes schema version, read-only mode, delivery readiness, priority counts, urgent count, due metadata, reminder categories, subject ids, and exact follow-up commands, so schedulers, host adapters, or notification bridges can consume reminder handoffs without scraping human text.

`cmu hardening-cycle` now treats review-reminder delivery as a machine-readable proof surface and points its fifth item at `cmu review-reminders --json`. The gate still stays read-only: it validates that every emitted reminder has a subject id and follow-up command, but it does not renew authority, promote memories, resolve challenges, apply decay, create receipts, link commits, or send notifications.

Automated verification covers the JSON payload shape, summary counts, urgent categories from both direct authority reminders and review-queue handoffs, CLI JSON parsing, read-only persisted-store behavior, and the hardening-cycle handoff command through real `MemoryStore`, `MemoryUseStore`, `TeamDirectoryStore`, portable fixtures, and Git-backed temporary roots. Manual verification under `.manual/reminder-delivery-proof` created a real expired stable Practice memory, confirmed `cmu review-reminders --json` emitted a valid P0 delivery payload without mutating `.cmu/memories.json`, and confirmed `cmu hardening-cycle --portable-fixture-dir <fixtures> --strict` passed all five surfaces with the reminder-delivery item pointing at JSON.

Current owner/team review outcome cycle completed:

This cycle implemented five concrete unfinished CMU capabilities inside the controlled owner/team handoff path:

- `cmu team-review-action --action challenge` stores a real stable-memory challenge Candidate from handoff input.
- `cmu team-review-action --action strengthen` resolves an approved challenge by strengthening the original stable memory and retiring the challenge Candidate.
- `cmu team-review-action --action retire` resolves an approved challenge by retiring obsolete stable memory with required evidence and retirement reason.
- `cmu team-review-action --action split` resolves an approved challenge by creating a new scoped stable memory while annotating the original.
- `cmu team-review-action --action narrow-scope` applies approved safe Practice/Anchor narrowing and rejects broadening or scope shifts that belong on the challenge/split path.

Automated verification now covers 289 tests, including the new owner/team review outcome test through real `MemoryStore` persistence and CLI dispatch. Manual verification under `.manual/team-review-action-five-proof` created real Practice memories through the CLI, stored challenge Candidates, strengthened one stable memory, retired one obsolete stable memory, split a new scoped Practice memory, narrowed a stable Practice scope, and inspected the resulting active/retired store through real CLI surfaces.

Previous five-slice workflow hardening cycle completed:

This cycle implemented five separate unfinished CMU capabilities rather than another hardening-cycle meta-check:

- `cmu evidence-watch` runs a bounded scheduler/host loop around `evidence-session`, applies only clean high-confidence links, records every cycle when requested, and refreshes receipt state between cycles.
- `cmu openai-runner` adds an OpenAI Agents-style host adapter for `openai.run.started`, `openai.run.completed`, `openai.checkpoint.created`, and `openai.review.requested` events through the existing autonomous runner hooks.
- `cmu host-path-suite` now checks OpenAI-style host events alongside saved scenarios, isolated runner hooks, Codex-style events, and before/after scenario comparison for every generated fixture.
- `cmu portable-fixture-seed --historical` creates a historical current-schema portable export fixture, and `cmu portable-compat` now validates `historical-*.json` fixtures instead of treating them as unknown files.
- `cmu team-review-action` gives owner/team handoff cards a controlled apply path for stable-memory authority metadata and team-scope owner/review metadata.

Automated verification now covers 288 tests, including new OpenAI adapter tests and product-hardening workflow tests against real stores, Git-backed receipts, generated fixtures, CLI dispatch, portable JSON fixtures, and team-scope records. Manual verification ran `openai-runner --input-file`, `evidence-watch --apply --record`, `team-review-action`, `portable-fixture-seed --historical`, `portable-compat`, and `host-path-suite --strict` under `.manual/five-slice-proof`. The Git-backed proof linked receipt `use_3cbcb25eb439` to commit `7206c14`, the review-action proof updated memory `mem_909041ccdc28` and team scope `team_d63d4a08397b`, and the host-path suite passed checkout-release and billing-incident with `openai=pass`.

Current lifecycle operations cycle completed:

This cycle implemented five separate unfinished CMU lifecycle capabilities rather than another hardening-cycle meta-check:

- `cmu lifecycle-proposals` provides assisted Situation -> Practice/Anchor proposal cards from the real promotion gates and keeps stable promotion under explicit authority review.
- `cmu lifecycle-merge` applies approved memory merges by combining source evidence/signals/relationships into the target and retiring the source.
- `cmu lifecycle-demote` applies explicit demotion, including sufficient authority checks and authority metadata clearing for stable Practice/Anchor memory.
- `cmu lifecycle-archive` writes retired memories to `.cmu/memory_archive.json` so archival has a durable local workflow.
- `cmu lifecycle-scope-record` records broad or ambiguous scope changes as Candidate Memories with target/current/proposed-scope evidence instead of silently broadening stable memory.

Automated verification now covers 294 tests, including five new lifecycle operation tests through real CLI dispatch and persisted memory/archive stores. Manual verification under `.manual/lifecycle-ops-proof-20260609` ran proposal, merge, scope-record, demotion, archive, list, and archive inspection paths against a real `.cmu` store.

Current five-workflow product hardening cycle completed:

This cycle implemented five separate unfinished CMU capabilities rather than another hardening-cycle meta-check:

- `cmu team-review-handoff` provides focused owner/team handoff cards for missing team-scope metadata, uncovered repo/team boundaries, and stable-memory authority gaps.
- `cmu evidence-session` runs the conservative checkpoint monitor as a session workflow that can apply clean links and record `.cmu/evidence_sessions.json` summaries.
- `cmu reminder-delivery` writes review reminders to a local JSONL outbox only with `--apply`, giving schedulers a durable non-CLI delivery adapter without applying governance decisions.
- `cmu portable-fixture-seed` derives valid, invalid, future-schema, and legacy-schema portable fixtures from a real CMU store; `cmu portable-compat` now verifies `legacy-*.json` fixtures fail safely.
- `cmu host-path-suite` generates fixture repositories and checks each through saved scenarios, isolated runner hooks, the Codex runner adapter, and before/after scenario comparison.

Automated verification now covers 283 tests, including five new product-hardening workflow tests against real stores, Git-backed receipts, generated fixtures, CLI dispatch, and outbox files. Manual verification ran `team-review-handoff`, `reminder-delivery --apply`, `portable-fixture-seed`, `portable-compat`, `host-path-suite --strict`, and a Git-backed `evidence-session --apply --record` proof that linked `use_edcad18f6949` to commit `1c1e19c` under `.manual/evidence-session-proof`.

Current fixture repository slice completed:

The first generated fixture repository now exists through `cmu.fixture_repos.create_fixture_repo` and `cmu fixture-repo-create --kind checkout-release --output <dir>`. The checkout-release fixture creates real source/test files, initializes Git when available, seeds a scoped Practice memory, and saves a strict scenario-library case tagged for fixture and runner-host-path use.

This gives Scenario/Evaluation Maturity a concrete repo-shaped target instead of only in-memory or temporary snippets. Future host adapters, runner scenarios, and before/after comparisons can now run against a repeatable local checkout fixture with code paths, workflow/environment scope, memory, and scenario expectations already connected.

Automated verification covers fixture creation through the Python API and CLI, real generated files, real `MemoryStore` and `ScenarioLibraryStore` contents, saved `scenario-run --strict` pass behavior, receipt-free scenario execution, and refusal to write over non-empty output directories. Manual verification under `.manual/fixture-repo-proof/checkout-release` created the fixture and confirmed `cmu --root <fixture> scenario-run --tag fixture --strict` passed with the seeded Practice memory.

Current five-capability product operation cycle completed:

This cycle implemented five separate unfinished CMU capabilities, each with real CLI surfaces, store-backed behavior, tests, and manual verification:

- `cmu evidence-service` runs the real evidence-session monitor as a background service loop until a stop file appears, with durable `.cmu/evidence_service_runs.json` service state and optional bounded `--max-cycles` for supervised runs.
- `cmu host-setup-manifest` writes a machine-readable IDE/coding-agent setup contract generated from live MCP tools, `AgentIntegration`, and Codex/OpenAI adapter manifests.
- `cmu review-export` writes the real review queue, owner/team handoffs, and review reminders as a structured non-CLI JSON review payload without applying governance decisions.
- `cmu lifecycle-settle` applies controlled Memory Gravity settling evidence to active memories when settle pressure is present.
- `cmu lifecycle-scope-suggest` creates Candidate scope-refinement records from real Memory Use Receipt pressure such as no-file-overlap, checkpoints, reverts, or low-confidence committed evidence.

Automated verification now covers 299 tests, including new product-operation tests for the evidence service, host setup manifest, review export, lifecycle settling, and lifecycle scope suggestions against real stores and CLI paths. Manual verification under `.manual/five-capability-proof-20260610` initialized a real CMU/Git proof root, settled `mem_2804382cc216`, surfaced and linked receipt `use_b7333db6b205` through the normal work-cycle/use-link path, created a scope-refinement Candidate for `mem_81dbb7399787`, wrote `.cmu/review_export.json`, wrote `.cmu/host_setup_manifest.json`, and recorded one `evidence-service` cycle.

Current five-bullet unfinished-work burndown cycle completed:

This cycle removed the five concrete Product/UI unfinished chunks from `CMU_Major_Unfinished_Work.md` by implementing `cmu product-console` as a real read-only human/product surface over existing CMU stores:

- Human-facing memory graph/tree views now render as a product console graph summary with connected memory nodes, relationship counts, and focused memory filtering.
- Review cards for promotion, authority, challenge, quality, and decay decisions are pulled from the real review queue instead of duplicated UI-only logic.
- Trust and evidence inspection is backed by real Memory Use Receipts and usefulness/drag analytics, including linked-use, strong-use, drag, resolved, governance, and next-action fields.
- Product cleanup surfaces are backed by the real readiness queue, including authority, receipt, graph, quality, and coverage issues.
- Situation navigation now shows situation -> cause -> fix -> related practice -> exception/warning paths from stored summaries, use paths, and graph relationships.

The console supports readable CLI output plus `--json` as a structured `cmu-product-console/v1` payload for future UI/workflow adapters, and `--memory <id>` focuses the view without mutating stores. Automated tests verify the Python report, CLI JSON path, focus filtering, and read-only behavior through real `MemoryStore`, `MemoryUseStore`, graph relationships, review cards, readiness cleanup items, and linked receipts. Manual verification under `.manual/product-console-proof-20260610` seeded a real `.cmu` store, linked a real receipt, and confirmed `cmu product-console` rendered graph/tree, review, trust/evidence, cleanup, and situation-path sections.

Previous five-bullet unfinished-work burndown cycle completed:

This cycle removed five concrete unfinished chunks from `CMU_Major_Unfinished_Work.md` by implementing real production-hardening surfaces with tests and manual CLI verification:

- `cmu evidence-service-install` generates preview-first service-manager wrapper artifacts for `cmu evidence-service`, including user systemd units, Windows Task Scheduler PowerShell wrappers, launchd plist files, and a wrapper manifest.
- `cmu host-examples` generates Codex MCP, OpenAI runner event, and MCP tool-call examples from the live `host-setup-manifest` contract.
- `cmu review-inbox` renders a read-only non-CLI inbox from live stores or a saved `cmu-review-export/v1` payload, with JSON output for UI/workflow adapters.
- `cmu fixture-repo-create --kind inventory-migration` adds a third repo-shaped fixture with real source/tests, scoped Practice memory, and strict saved scenario coverage; `cmu host-path-suite` now exercises checkout, billing, and inventory fixture domains.
- `cmu portable-fixture-seed --historical` now writes two historical current-schema exports plus a migration-plan fixture, and `cmu portable-compat` validates `migration-*.json` fixtures as safe failures pending explicit migration support.

Automated verification covers all five new surfaces through real stores and CLI dispatch, including host-path suite coverage across three fixture kinds and portable compatibility across seven seeded fixtures. Manual verification under `.manual/five-burndown-service`, `.manual/five-burndown-host`, `.manual/five-burndown-review`, `.manual/five-burndown-fixture`, and `.manual/five-burndown-portable` confirmed wrapper generation, host example generation, review export plus inbox JSON, strict inventory fixture scenario pass, and portable migration corpus compatibility pass.

Current five-surface hardening cycle slice completed:

The first product-hardening operator gate now exists through `cmu.hardening_cycle.hardening_cycle_report` and `cmu hardening-cycle`. It composes the five current hardening directions into one read-only report: owner/team review metadata from `team-scope`, dry-run checkpoint evidence monitoring from `evidence-monitor`, fixture-host-path catalog coverage from `fixture-repo-create`, portable migration compatibility from `portable-compat`, and review-reminder delivery readiness from `review-reminders`.

This cycle also expands the fixture repository catalog with `cmu fixture-repo-create --kind billing-incident --output <dir>`. The billing fixture creates real reconciliation source/test files, seeds a critical scoped Practice memory with owner/org authority, and saves a strict scenario tagged for fixture, runner-host-path, and owner-review use. The fixture catalog now has checkout rollback and billing incident replay domains instead of a single example.

The hardening gate is intentionally strict but non-mutating. With `--strict`, it exits non-zero unless all five proof surfaces pass; without `--strict`, it still reports review items instead of treating missing fixtures or missing authority metadata as success. It does not apply follow-up commands, link receipts, change team scopes, import portable fixtures, or write Git checkpoints.

Automated verification covers the billing fixture's generated files, authority metadata, saved scenario expectations, and strict scenario pass behavior. It also covers `hardening-cycle` strict pass/fail behavior through real `TeamDirectoryStore`, `MemoryStore`, `MemoryUseStore`, Git initialization, exported valid/invalid/future portable fixtures, CLI rendering, and explicit source-store no-mutation checks. Manual verification created `.manual/billing-incident-proof`, ran its strict fixture scenario successfully, created `.manual/hardening-cycle-proof` with real team scope, critical Practice memory, portable fixtures, and Git repository, confirmed `cmu portable-compat` passed, and confirmed `cmu hardening-cycle --strict` passed all five checks. Current full test suite: 283 tests.

Next large implementation direction:

The first-pass structural skeleton is complete, and the scenario-library, expanded generated fixture repository catalog, five-surface hardening-cycle gate, quickstart-demo, Python SDK, portable-compatibility, MCP hardening, readiness workflow, document-curation gate, memory seeding workbench, first real workspace cleanup pass, setup-guide adoption surface, README/editable-install path, install-check validation gate, scripted local demo walkthrough, isolated built-distribution validation, portable fixture seeding, migration-oriented portable fixture corpus, first autonomous-runner hooks, first runner-scenario evidence surface, first Codex host adapter, first OpenAI host adapter, first host-path suite with two host adapters across three generated fixture domains, first before/after scenario comparison, first conservative checkpoint monitor, first evidence-session workflow, first bounded evidence-watch loop, first background evidence service, first service-manager wrapper generation, first compact review queue, first owner/team handoff surface, first owner/team handoff apply path, first owner/team challenge/strengthen/retire/split/narrow apply path, first structured non-CLI review export, first review inbox, first product console, first lightweight review reminder surface, first outbox reminder delivery adapter, first controlled lifecycle apply path, first lifecycle settling/scope-suggestion paths, first host/IDE setup manifest, first manifest-derived host examples, and first local team-scope directory with review-queue coverage cards are now in place. The next large work should continue deepening one of these workflow surfaces toward richer operation, especially long-session receipt linking policy, IDE/coding-agent integration beyond generated examples, actual scheduling/notification delivery, controlled lifecycle merge/split/decay automation informed by settling and scope-suggestion evidence, or production retrieval durability. The new cleanup memories should remain on quality watch until normal CMU work-cycle receipts prove usefulness or drag.

Large-slice trigger instruction:

When the user says "implement our next large slice", "implement our next massive slice", "implement our next huge slice", "next big structural slice", or similar, treat it as a request to pick the next unfinished structural skeleton item from this markdown, implement it end to end, write real tests, manually verify it, update the markdown, and explain how it advances the skeleton. Do not choose tiny semantic-audit polish unless the user explicitly asks for polish or the structural slice is blocked.

Structural skeleton backlog:

1. Scenario/evaluation harness (first slice implemented through `cmu evaluate-scenario`; first persisted scenario-library hardening slice implemented through `cmu scenario-add`, `cmu scenario-list`, and `cmu scenario-run`; first runner-hook scenario evidence slice implemented through `cmu runner-scenario`; first before/after comparison slice implemented through `cmu scenario-compare`; expand with richer fixture repositories and host-path suites later).
2. Core memory lifecycle (first structural view implemented through `cmu lifecycle`; first apply path through `cmu lifecycle-apply`; first operations path through `cmu lifecycle-proposals`, `lifecycle-merge`, `lifecycle-demote`, `lifecycle-archive`, and `lifecycle-scope-record`; expand later with settling automation and richer review UX).
3. Raw trace to distilled memory pipeline (first slice implemented through `cmu trace-add` and `cmu trace-distill`; expand later with background/agent-runtime capture).
4. Memory Gravity (first structural view implemented through `cmu gravity`; expand later by feeding gravity pressure into controlled settling, merge, split, demotion, decay, and scope-refinement workflows).
5. Full Hybrid Retrieval pipeline (first inspection surface implemented through `cmu retrieval-pipeline`; expand later by feeding pipeline diagnostics into preflight/start controls).
6. Practice/Anchor governance loop (first read-only governance view implemented through `cmu governance`; expand later with controlled approval/review UX).
7. Usefulness and drag analytics (first read-only analytics view implemented through `cmu analytics`; expand later with longitudinal scenario metrics).
8. Full Work Cycle integration (first read-only integration view implemented through `cmu work-cycle`; expand later with controlled execution/apply paths).
9. Anti-Pattern workflow (first read-only workflow view implemented through `cmu anti-pattern`; expand later with controlled creation/review UX).
10. Question workflow (first read-only tracking plus explicit resolution path implemented through `cmu question` and `cmu resolve-question`; expand later with richer review UX).
11. Graph memory view (first read-only path view implemented through `cmu graph`; expand later with durable graph storage and richer path semantics).
12. Agent integration boundary (first direct tool-call service implemented through `cmu.agent_api.AgentIntegration`, `cmu agent-tools`, and `cmu agent-call`; first SDK, MCP, setup, packaging, autonomous-runner hook surfaces, Codex runner adapter, and OpenAI runner adapter implemented; expand later with additional host adapters and IDE setup polish).
13. Team and authority model (first policy/report/apply slice implemented through `cmu authority` and `cmu authority-set`; first local team-scope directory implemented through `cmu team-scope-add` and `cmu team-scope`; first owner/team handoff apply path implemented through `cmu team-review-action`, now including authority, team metadata, challenge, strengthen, retire, split, and narrow-scope outcomes).
14. Memory quality and decay model (first scoring and controlled mutation slice implemented through `cmu quality` and `cmu decay-apply`).
15. Import/export and portability layer (first export/import and validation slices implemented).
16. Memory base cleanup/readiness workflow (first read-only operator queue implemented through `cmu readiness`; first real workspace seeding/cleanup records now applied, with future use evidence still needed).

## Large-Scope Features Still Left

- Scenario/evaluation harness: first read-only CLI slice implemented through `cmu evaluate-scenario`, persisted scenario-library runs now exist through `cmu scenario-add`, `cmu scenario-list`, and `cmu scenario-run`, runner-hook lifecycle proof now exists through `cmu runner-scenario`, before/after behavior comparison now exists through `cmu scenario-compare`, the generated fixture repository catalog now includes checkout-release, billing-incident, and inventory-migration through `cmu fixture-repo-create`, and `cmu host-path-suite` now checks Codex and OpenAI host adapters across all three fixture domains; still needs richer longitudinal evidence cases later.
- Demo/quickstart flow: first Git-backed proof loop implemented through `cmu quickstart-demo`, setup guidance now points hosts at that proof loop through `cmu setup-guide`, the root README now documents the fresh-checkout proof path, `cmu install-check` validates adoption/package readiness, `cmu demo-walkthrough` ties those surfaces into one local walkthrough, and `cmu dist-check` validates installed distribution behavior; still needs richer hosted/autonomous-runner examples later.
- Core memory lifecycle: first read-only structural view, Candidate -> Situation apply path, assisted stable proposals, controlled merge, controlled demotion, retired-memory archival, broad/ambiguous scope-change records, gravity-backed settling, and receipt-pressure scope suggestions are implemented; still needs richer merge/split/decay automation and harness scenarios later.
- Raw trace vs distilled memory pipeline: first local CLI slice implemented through `cmu trace-add` and `cmu trace-distill`; still needs deeper scenario integration and agent-runtime/background capture later.
- Memory Gravity: first read-only structural view implemented through `cmu gravity`; still needs direct integration with settling, split, decay, and scope-evolution automation later.
- Hybrid Retrieval v1: first full inspection surface implemented through `cmu retrieval-pipeline`; still needs deeper control integration with preflight/start and richer rejection metrics later.
- Practice/Anchor governance loop: first read-only stable governance view implemented through `cmu governance`; still needs richer approval UX and controlled automation later.
- Usefulness and drag analytics: first read-only analytics view implemented through `cmu analytics`; still needs richer longitudinal scenario metrics, time/turn estimates, and product-level dashboards later.
- Embedding-backed semantic retrieval: vector search for situation similarity, grounded by graph and metadata before surfacing.
- Graph-backed memory store: durable relationship model beyond flat local JSON, with tree-like human/agent views.
- Call Trigger Layer automation beyond the first `cmu start` CLI orchestration and `cmu_task_start` direct boundary, including deeper runtime hooks.
- Onboarding Seed integration beyond CLI, including new-human, new-agent, and swarm onboarding flows.
- Agent/runtime integration: first versioned direct tool-call boundary implemented, first Python SDK facade implemented through `cmu.sdk.CentralMemoryUnit`, first local MCP stdio adapter implemented through `cmu.mcp`, `cmu mcp`, and `cmu-mcp`, first host setup guide implemented through `cmu setup-guide`, first autonomous-runner event hooks implemented through `cmu.runner_hooks` / `cmu runner-hooks`, first Codex host adapter implemented through `cmu.codex_adapter` / `cmu codex-runner`, first OpenAI host adapter implemented through `cmu.openai_adapter` / `cmu openai-runner`, first host/IDE setup manifest implemented through `cmu host-setup-manifest`, and first manifest-derived host examples implemented through `cmu host-examples`; still needs additional host adapters and richer IDE/coding-agent integration.
- Background Git watcher or checkpoint monitor: bounded `cmu evidence-watch`, background `cmu evidence-service`, and service-manager wrapper generation through `cmu evidence-service-install` now exist; still needs richer long-session policy.
- Candidate scope-change records: first explicit broad/ambiguous scope-change record path now exists through `cmu lifecycle-scope-record`; still needs richer review and resolution UX.
- Practice/Anchor review UX: compact approval moments, lightweight reminders, first structured review export, first review inbox, and first authority/team-metadata handoff application now exist; still needs richer non-CLI approval, narrowing, challenge, split, retirement, or strengthening surfaces.
- Team/org authority model: first policy/report/apply slice, first local team-scope directory, and first owner/team handoff apply path are implemented; still needs richer delegation, owner/team review, and cross-repo authority later.
- Multi-repo and organization-level memory: local repo/team boundary records now exist; still needs cross-repo memory scope, org-wide patterns, and evidence-backed expansion rules without false transfer.
- Anti-Pattern and Question workflows: first-class creation, retrieval, review, and resolution paths for avoidances and unresolved uncertainty.
- Memory quality and decay model: first scoring and controlled weaken/demote/retire slice implemented; still needs longitudinal decay automation and richer archival policy later.
- Import/export and portability layer: first portable bundle boundary implemented through `cmu portable-export` and `cmu portable-import`; first bundle validation gate implemented through `cmu portable-validate`; first fixture compatibility gate implemented through `cmu portable-compat`; `cmu portable-fixture-seed --historical` now seeds multiple historical current-schema fixtures plus a migration-plan fixture; `cmu portable-compat` now validates migration fixtures as safe failures; `cmu hardening-cycle` now requires portable fixture proof for strict hardening passes; still needs real migration import/apply support later.
- Product/UI surface: first read-only product console implemented through `cmu product-console`, combining graph/tree, review cards, trust/evidence, cleanup, and situation-path navigation; still needs a richer interactive UI shell later.

## Non-Negotiable Direction

Build the real core path, not workaround-shaped core.

The main workflow should stay agent-first:

- agents call CMU directly;
- CMU stays quiet unless memory changes action or needs trust;
- active context remains lean;
- Candidate Memory is created only from reusable situational intelligence;
- broader scope and higher authority are earned through evidence and review.

## Known Gaps

- Retrieval is still local scoring with graph expansion, scope guardrails, explainable score breakdowns, and opt-in local semantic scoring. It now has persistent local vector storage for `--semantic local`, but it does not yet use an external embedding model or durable graph store.
- Host-specific autonomous agent runner adapters now exist for Codex-style and OpenAI Agents-style events, but additional runtimes and IDE/coding-agent setup polish are still missing.
- No unbounded background Git watcher yet; commit linking and use review remain command-driven through `cmu use-link-auto`, `cmu use-link-latest`, `cmu use-link`, `cmu use-review`, `cmu evidence-session`, and bounded `cmu evidence-watch`.
- No durable graph store yet; relationship behavior exists only as local JSON links plus first-slice graph expansion.
- Semantic candidate proposal exists only for the opt-in local semantic path and only through strict grounding gates. It is not yet backed by an external embedding model or a dedicated vector database.
- Direct autonomous agent runner hooks now exist through `cmu.runner_hooks.AutonomousRunnerHooks`; runtimes can integrate through those hooks, `cmu.agent_api.AgentIntegration`, the Codex/OpenAI JSON adapters, the Python SDK facade, or the MCP stdio adapter instead of parsing `cmu start`. More host-specific adapters are still future work.
- No interactive UI shell or rich team directory/delegation layer yet; the first product console, authority policy, controlled assignment surface, and owner/team handoff apply path now exist.
- Scenario/evaluation harness now has direct read-only evaluation, a persisted scenario library, isolated runner-hook lifecycle proof, before/after comparison, and a first generated checkout-release fixture repository. It can validate trigger/onboarding/preflight/action/candidate expectations across saved cases and runner hook start/Candidate/checkpoint outcomes, but it does not yet have a rich fixture catalog or automatically evaluate real time/token/risk reduction.
- Practice/Anchor governance is no longer purely diagnostic for authority metadata: `cmu team-review-action` can apply controlled stable authority handoffs, team metadata, challenge creation, approved strengthening, approved retirement, approved split, and safe narrowing. Richer non-CLI approval UX is still future work.
- Memory-base cleanup now has a read-only readiness queue through `cmu readiness`, a conservative markdown curation gate through `cmu doc-curate`, selected candidate apply, a read-only seeding workbench through `cmu seed-plan`, and the first real workspace memories: one Situation, one Anti-Pattern, and one Question connected by graph relationships. These records still need future linked-use receipts before quality can move beyond watch state; no stable Practice/Anchor authority was assigned during this cleanup pass.
