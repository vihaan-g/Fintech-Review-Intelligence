# Fintech Review Intelligence Edit Prompt

Use this prompt to **edit the current codebase in place**, not rebuild it from scratch.

The goal is to preserve the existing project structure, reuse the good parts of the current implementation, and surgically improve the parts that matter most for final output quality.

This prompt is written for an AI coding assistant operating inside the existing repository.

---

## Core Instruction

Edit the existing `Fintech-Review-Intelligence` codebase in place.

Do **not** perform a greenfield rebuild.
Do **not** unnecessarily rename, move, or redesign working parts of the project.
Do **not** discard existing architecture just to make it cleaner.

Preserve the current project identity and implementation shape wherever that remains compatible with the goals below.

Your job is to:
1. keep what is already good
2. refactor what is structurally sound but outdated
3. rewrite only the parts that materially need redesign
4. preserve compatibility with the existing database, outputs, and classification schema
5. improve the final analysis and council output quality substantially

This project is a portfolio-grade Python pipeline for generating product intelligence from Play Store reviews of Indian fintech apps:
- Groww
- Jupiter
- CRED
- PhonePe
- Paytm

The final output quality matters more than code cleverness.

---

## Non-Negotiable Goals

1. Keep the existing repo structure and edit the current modules rather than recoding the whole project.
2. Use OpenRouter only for all LLM interactions.
3. Remove direct Google AI Studio / separate Gemini provider integration.
4. Keep the current model roster unchanged.
5. Keep the current classification schema unchanged.
6. Keep the existing SQLite database schema compatible.
7. Keep the existing pipeline phases and overall project identity intact.
8. Improve the analysis layer without overcomplicating it.
9. Redesign the council substantially, while preserving the project’s intent.
10. Keep the code simple, typed, well-logged, and easy to operate.
11. Preserve and meaningfully adapt the `.claude` layer so the repo demonstrates AI-native engineering workflow knowledge.

---

## Mandatory Context To Read First

Before making edits, read and use:
1. `CLAUDE.md`
2. the existing `.claude/` folder
3. the current source files under `src/`
4. `queries/analysis_queries.sql`
5. `requirements.txt`
6. any existing tests

Treat the current implementation as the starting point and source of truth for what should be preserved unless explicitly superseded below.

Also incorporate the practical behavior guidance from Andrej Karpathy-inspired coding rules:
1. Think Before Coding
2. Simplicity First
3. Surgical Changes
4. Goal-Driven Execution

Apply these principles materially during editing:
1. surface assumptions instead of silently guessing
2. prefer the minimum sufficient code
3. only touch files and lines that need to change
4. define success criteria and verify changes

---

## Mandatory Skill And Agent Invocation

`CLAUDE.md` and rules are assumed to be read by default, but skills and project agents must be explicitly invoked before implementing the relevant subsystem.

Because this prompt is intended to be run in OpenCode, prefer the OpenCode-native project agents under `.opencode/agents/` and invoke them explicitly with `@...` mentions when appropriate.

### Required Skills

Explicitly load and use:

1. `cost-aware-llm-pipeline`
   - before editing classification request flow, council request flow, retries, batching, checkpointing, and cost tracking

2. `multi-agent-patterns`
   - before redesigning the council architecture, stage flow, role isolation, anonymized review, and chairman/non-chairman coordination

3. `context-optimization`
   - before editing findings-summary handoff, long prompts, prompt layout, prompt stability, or council context budgeting

4. `evaluation`
   - before editing quality gates, council sanity checks, output validation, confidence calibration, or evaluation-style tests

Optional when useful:
1. `prompt-optimizer`
2. `write-judge-prompt`

These skills must influence implementation decisions materially, not just be mentioned.

### Required Project Agents

Explicitly invoke these project-specific agents before editing the relevant subsystem:

1. `data-collector`
   - scope: `ReviewCollector`, `DatabaseManager`

2. `sql-analyst`
   - scope: `SQLAnalyst`, `FindingsSummarizer`

3. `council-orchestrator`
   - scope: `CouncilMember`, `CouncilOrchestrator`

4. `insight-reporter`
   - scope: `InsightReporter`

Agent usage must be real:
1. use the matching agent before major implementation in that area
2. keep scope boundaries clean
3. reflect useful agent findings in the edits
4. prefer these project agents over generic global agents when the scope matches

---

## Editing Strategy

This is an **edit/refactor project**, not a rewrite project.

### Keep and Adapt

Preserve the existing structure of these files/modules and refactor them in place unless a very specific section needs replacement:

1. `src/data_collection/database_manager.py`
2. `src/data_collection/review_collector.py`
3. `src/config.py`
4. `src/main.py`
5. `src/analysis/sql_analyst.py`
6. `src/analysis/findings_summarizer.py`
7. `src/classification/review_classifier.py`
8. `src/classification/batch_processor.py`
9. `src/agents/insight_reporter.py`

### Rewrite Heavily But In Place

These should likely be substantially reworked while staying in their current file/module boundaries:

1. `src/council/council_member.py`
2. `src/council/council_orchestrator.py`

### Preserve `.claude` But Refresh It Deliberately

Keep the `.claude` architecture meaningful and project-specific.
Do not replace it with generic agent boilerplate.

At the same time, make the repo cleanly usable in OpenCode as well.
Do not assume that all Claude-specific conventions are automatically supported by OpenCode.
Account for compatibility explicitly.

---

## Existing Project Identity To Preserve

Preserve these project characteristics:

1. Python pipeline architecture
2. Five main phases:
   - collection
   - analysis
   - classification
   - council
   - report
3. SQLite in WAL mode
4. One class per file convention
5. Typed method signatures everywhere
6. Logging instead of print
7. Outputs stored under `outputs/`
8. Review data stored in `outputs/reviews.db`
9. Classification stored in `reviews.classification` as JSON text
10. Pipeline checkpoints stored in SQLite `pipeline_state`
11. Portfolio framing as Indian fintech product intelligence

Also preserve and strengthen the repo’s AI-native workflow signaling by making the project coherent across both Claude-style and OpenCode-style conventions.

---

## Critical Constraints

### 1. Do Not Change Model Roster

Do not change the intended models.
If the code currently has outdated provider wiring, update the wiring, but keep the actual model choices unchanged.

### 2. Do Not Change Classification Schema

Keep compatibility with the existing classification JSON shape.

The schema must remain compatible with fields like:
1. `product_area`
2. `specific_feature_request`
3. `workflow_breakdown`
4. `confidence`
5. `parse_failed`

Do not redesign taxonomy or output shape.

### 3. Do Not Break Existing Database Compatibility

The edited code must remain compatible with the current database and outputs.
Do not casually change schema assumptions.

### 4. Keep It Simple

Do not add:
1. unnecessary abstraction layers
2. excessive framework machinery
3. heavy statistical systems
4. advanced clustering pipelines
5. decorative AI scaffolding
6. a full dependency graph engine

### 5. Remove Redundant Code While Editing

While editing, clean up redundancy created by the changes.

Do remove:
1. imports made unused by your edits
2. old provider-specific branches that become obsolete after OpenRouter-only migration
3. stale comments and docstrings made inaccurate by your edits
4. dead helper paths made unnecessary by council redesign
5. duplicate prompt-building or serialization logic if they can be consolidated simply

Do not remove:
1. unrelated pre-existing code unless clearly superseded by the requested changes
2. working code that is merely stylistically imperfect

The cleanup must follow the “surgical changes” principle: remove redundancy that your edit path makes obsolete, not unrelated historical debris.

---

## Files And Areas To Account For

Think through ripple effects across **all** of these:

### Source Files
1. `src/config.py`
2. `src/main.py`
3. `src/utils.py`
4. `src/data_collection/database_manager.py`
5. `src/data_collection/review_collector.py`
6. `src/analysis/sql_analyst.py`
7. `src/analysis/findings_summarizer.py`
8. `src/classification/review_classifier.py`
9. `src/classification/batch_processor.py`
10. `src/council/council_member.py`
11. `src/council/council_orchestrator.py`
12. `src/agents/insight_reporter.py`

### Supporting Project Files
1. `requirements.txt`
2. `.env.example`
3. `queries/analysis_queries.sql`
4. `README.md` if necessary
5. tests under `tests/`
6. `AGENTS.md` if added for OpenCode compatibility
7. `opencode.json` if added for OpenCode compatibility

### OpenCode-Native Project Files To Consider Adding

Because OpenCode supports `CLAUDE.md` and `.claude/skills` only partially, consider adding or updating these files so the project works cleanly in OpenCode-native workflows too:

1. `AGENTS.md`
   - project rules for OpenCode
2. `opencode.json`
   - project-specific OpenCode configuration if needed
3. `.opencode/agents/*.md`
   - project-specific agents mirrored from the `.claude/agents` layer where useful
4. `.opencode/skills/*/SKILL.md`
   - only if you want explicit OpenCode-native skill storage in addition to `.claude/skills`

### `.claude` Files
1. `.claude/settings.json`
2. `.claude/hooks/secrets_check.py`
3. `.claude/agents/data-collector.md`
4. `.claude/agents/sql-analyst.md`
5. `.claude/agents/council-orchestrator.md`
6. `.claude/agents/insight-reporter.md`
7. relevant `.claude/skills/*`
8. relevant `.claude/rules/*`

### OpenCode Compatibility Reality To Account For

Account for the following facts while editing the repo guidance/configuration:

1. OpenCode supports `CLAUDE.md` as a fallback for project rules if no `AGENTS.md` exists.
2. OpenCode supports `.claude/skills/*/SKILL.md` discovery.
3. OpenCode does not automatically use `.claude/agents/` as its native project-agent directory.
4. OpenCode does not automatically use `.claude/settings.json` as its native project config.
5. OpenCode’s native project rules file is `AGENTS.md`.
6. OpenCode’s native project config file is `opencode.json`.
7. OpenCode’s native project agent directory is `.opencode/agents/`.

Therefore, if you want this repo to demonstrate strong AI workflow knowledge in OpenCode too, do not rely only on the `.claude` folder. Add OpenCode-native compatibility where useful.

### Outputs / Compatibility Assumptions
1. `outputs/findings_summary.json`
2. `outputs/council_result.json`
3. `outputs/findings_report.md`
4. `outputs/linkedin_snippet.txt`
5. `outputs/README.md`
6. `outputs/reviews.db`

Do not forget to account for import paths, prompt text references, model/provider wording, error messages, recovery hints, test expectations, and output structure.

---

## Best Order Of Work

Follow this editing order so changes cascade cleanly and efficiently.

### Phase A: Read, Map, And Plan

Before editing:
1. read the current relevant source files
2. read `CLAUDE.md`
3. read the current `.claude` layer
4. identify what is preserved vs refactored vs rewritten
5. map all provider-related assumptions still tied to Gemini direct access
6. identify all files affected by OpenRouter-only migration
7. identify all files affected by council redesign
8. identify all files affected by analysis upgrades
9. identify all files affected by reporting changes
10. identify any tests that must be updated
11. identify what Claude-specific workflow assets should be mirrored or adapted for OpenCode-native use

Do not start with blind code changes.

### Phase B: Foundation / Config / Dependencies

Edit first:
1. `requirements.txt`
2. `src/config.py`
3. `.env.example`

Goals:
1. make OpenRouter the only LLM provider config
2. remove direct Google AI Studio key dependency
3. update dependency list for OpenRouter SDK
4. keep strict env validation
5. keep logging setup

Also account for downstream changes in:
1. imports
2. recovery hints
3. README/config text
4. tests expecting old env vars
5. OpenCode-facing project instructions/config if added

### Phase C: Preserve Collection And Database Layer

Use `data-collector` agent before major edits here.

Edit minimally and carefully:
1. `src/data_collection/database_manager.py`
2. `src/data_collection/review_collector.py`

Goals:
1. preserve schema compatibility
2. preserve WAL mode
3. preserve per-app collection checkpointing
4. preserve duplicate-safe inserts
5. preserve rate-limit sleeps
6. preserve partial collection failure handling

Only change what is necessary for consistency with other phases.

### Phase D: Classification Transport Cleanup

Use `cost-aware-llm-pipeline` before editing this area.

Edit in place:
1. `src/classification/review_classifier.py`
2. `src/classification/batch_processor.py`

Goals:
1. migrate classification to OpenRouter-only request flow
2. keep classification schema the same
3. keep batch-oriented operation
4. keep parse-failure-safe behavior
5. keep simple resume/checkpoint behavior
6. add simple cost tracking
7. preserve compatibility with existing classification data and downstream analysis

Do not redesign classification semantics.

### Phase E: Analysis Upgrade

Use `sql-analyst` agent and `evaluation` skill before major edits here.

Edit:
1. `queries/analysis_queries.sql`
2. `src/analysis/sql_analyst.py`
3. `src/analysis/findings_summarizer.py`

Goals:
1. keep the current analysis shape
2. improve analytical quality without exploding complexity
3. use time-series outputs meaningfully in the summary
4. normalize keyword signals
5. add classification over-indexing
6. reframe reply analysis as reply behavior rather than causal impact
7. produce a stronger, sharper findings summary for the council

### Phase F: Council Rewrite In Place

Use `council-orchestrator` agent plus:
1. `multi-agent-patterns`
2. `cost-aware-llm-pipeline`
3. `context-optimization`
4. `evaluation`

Edit heavily:
1. `src/council/council_member.py`
2. `src/council/council_orchestrator.py`

Goals:
1. move all LLM access to OpenRouter only
2. preserve model roster
3. implement the revised council flow
4. keep checkpointing simple but effective
5. keep cost tracking
6. reduce chairman bias
7. restore distributed anonymized review
8. improve confidence calibration and output usefulness

### Phase G: Reporting And Final Integration

Use `insight-reporter` agent and `evaluation` skill before major edits here.

Edit:
1. `src/agents/insight_reporter.py`
2. `src/main.py`
3. possibly `src/utils.py`

Goals:
1. align reports to the new council outputs
2. preserve outputs flow
3. improve artifact quality
4. keep CLI simple and operator-friendly
5. ensure council/report dependency handling is correct

Also ensure repository-level documentation and AI workflow guidance remain consistent with the edited implementation.

### Phase H: `.claude` Refresh

Edit the `.claude` layer only after you understand the real code boundaries.

Goals:
1. preserve project-specific agents
2. update council agent to match the revised council
3. preserve meaningful hooks
4. keep relevant skills in place
5. keep rules lightweight and relevant

Do not let the `.claude` layer drift away from the actual codebase.

### Phase H2: OpenCode Compatibility Layer

After `.claude` is refreshed, add or update an OpenCode-native compatibility layer so the project is cleanly usable in OpenCode.

Goals:
1. create or update `AGENTS.md` with concise project-specific rules
2. optionally add `opencode.json` if project-specific config is useful
3. mirror project-specific agents into `.opencode/agents/` if you want OpenCode-native agent discovery
4. preserve the `.claude` layer while making the project work well in OpenCode too
5. avoid duplicating large volumes of instructions unnecessarily

Important:
1. keep guidance consistent across `CLAUDE.md`, `AGENTS.md`, `.claude`, and `.opencode`
2. do not let one instruction layer contradict another
3. do not create decorative duplicate files with drift risk unless they add real compatibility value
4. keep `opencode.json` at the project root; do not move it into `.opencode/`

### Phase I: Tests, Validation, And Cleanup

At the end:
1. update tests that rely on old config/provider assumptions
2. add or adjust tests for council flow where feasible
3. add or adjust tests for upgraded analysis summary logic
4. verify imports and execution paths
5. verify output shape compatibility
6. verify recovery hints and logging

### Phase J: Commit Discipline

After each major phase is implemented and validated, create a git commit before moving on.

Commit boundaries should roughly follow:
1. foundation / config / dependency changes
2. collection / database edits
3. classification transport / cost-tracking edits
4. analysis edits
5. council edits
6. reporting / integration edits
7. `.claude` / OpenCode compatibility edits if they are substantial enough to stand on their own

Commit rules:
1. do not commit broken code
2. run the relevant tests for that phase before committing
3. use concise commit messages that match the repo’s existing style
4. prefer multiple focused commits over one giant commit when phases are meaningfully separable
5. if the user explicitly asks for a single final commit instead, follow that request

---

## Detailed Change Requirements

## Karpathy-Inspired Editing Principles To Implement In Repo Guidance

Incorporate these principles into the repo’s instruction/guidance layer so future agent sessions behave better:

1. Think Before Coding
   - do not assume silently
   - surface tradeoffs
   - ask when genuinely unclear

2. Simplicity First
   - minimum code that solves the problem
   - no speculative abstractions
   - no feature creep

3. Surgical Changes
   - touch only what is necessary
   - do not refactor unrelated areas
   - clean up only the redundancy created by your changes

4. Goal-Driven Execution
   - define success criteria
   - verify with tests/checks
   - use small plan -> verify loops

Implement these principles in whichever project instruction files make the most sense:
1. `CLAUDE.md`
2. `AGENTS.md`
3. lightweight rules files

Do not paste them blindly if they conflict with stronger existing project-specific instructions. Merge them intelligently.

### Config / Environment

Edit `src/config.py` so that:
1. only `OPENROUTER_API_KEY` is required for LLM access
2. config remains fail-fast
3. logging setup remains intact
4. references to separate Gemini API key are removed or adapted

Also update:
1. `.env.example`
2. README/config text if needed
3. `main.py` recovery hints if they reference old env vars

### Requirements / Dependencies

Update `requirements.txt` to reflect the edited project.
Account for:
1. adding OpenRouter SDK dependency
2. removing no-longer-needed direct HTTP/provider dependencies if obsolete
3. preserving test/runtime dependencies

### Main Pipeline

Edit `src/main.py` carefully.
Preserve:
1. phase ordering
2. phase-specific execution
3. `--phase`
4. `--dry-run`
5. helpful logging
6. operator-facing recovery hints

Update it to account for:
1. OpenRouter-only config
2. revised council stage/checkpoint logic
3. updated report sections
4. cost tracking outputs if appropriate
5. any revised analysis outputs

Make sure no old provider assumptions remain.

### Database Manager

Edit `src/data_collection/database_manager.py` only as needed.
Preserve:
1. schema compatibility
2. SQLite WAL setup
3. pipeline_state storage
4. duplicate-safe inserts
5. classification storage

Add only what is necessary for simple artifact hash and cost/state metadata support.
Do not overengineer.

### Review Collector

Edit `src/data_collection/review_collector.py` minimally.
Preserve:
1. app IDs
2. pacing logic
3. partial collection handling
4. normalization behavior
5. per-app checkpointing

Only adjust if needed for consistency or bug fixes.

### Classification

Edit `src/classification/review_classifier.py` and `batch_processor.py` so that:
1. provider wiring becomes OpenRouter-only
2. model usage remains effectively the same in intent
3. schema remains unchanged
4. parse handling remains defensive
5. batch processing remains simple
6. cost tracking is added
7. checkpointing remains simple and practical

Do not redesign the taxonomy, output fields, or downstream compatibility.

### Analysis

Edit `src/analysis/sql_analyst.py`, `findings_summarizer.py`, and `queries/analysis_queries.sql`.

Required improvements:
1. actually use time-series outputs in the written summary
2. add simple incident-like signal extraction
3. normalize keyword signals
4. add complaint-category over-indexing
5. reframe developer reply logic as behavior, not impact
6. sharpen narrative summary quality

Required summary qualities:
1. findings summary should read like a sharp analyst memo, not a dashboard dump
2. every claim must be tied to data
3. strongest cross-app asymmetries should be surfaced explicitly
4. strongest app-specific issues should be surfaced explicitly

Avoid overcomplication.

### Council

Edit `src/council/council_member.py` and `council_orchestrator.py` heavily.

Required council design:

1. Stage 0: chairman frame
2. Stage 1: specialist analysis by First Principles, Outsider, Expansionist only
3. Stage 2a: independent chairman contrarian pass
4. Stage 2b: anonymized distributed review by non-chairman specialists
5. Stage 2c: chairman audit synthesis
6. Stage 3: final chairman report

Required council properties:
1. chairman does not participate in Stage 1
2. all LLM calls go through OpenRouter only
3. prompts remain evidence-grounded
4. confidence is rubric-based
5. distributed review is about evidence audit, not generic ranking
6. checkpointing remains simple and useful
7. cost tracking is included

Preserve the project’s product-intelligence-specific framing.
Do not turn it into a generic LLM debate app.

### Reporting

Edit `src/agents/insight_reporter.py` so that:
1. the strongest findings lead the artifacts
2. report structure matches the new council outputs
3. output quality is stronger and more PM-useful
4. the artifact set remains the same
5. generated report language is grounded, not generic

Update output sections to account for:
1. analytical frame
2. new audit / confidence material
3. PM actions
4. app-specific signals

### `.claude`

Edit the `.claude` folder deliberately.

Required outcomes:
1. preserve project-specific agents
2. update the council-orchestrator agent to match the revised stage flow
3. preserve data-collector, sql-analyst, and insight-reporter agent boundaries
4. preserve the secrets hook
5. preserve the source-edit testing hook
6. keep the relevant skills present and aligned to actual implementation
7. keep a lightweight but real rules layer

Do not create decorative `.claude` content unrelated to the actual repo.

### OpenCode Compatibility

Because OpenCode supports Claude compatibility only partially, implement OpenCode-native support where it adds real value.

Required outcomes:
1. add or update `AGENTS.md` so OpenCode reads project rules directly
2. ensure `AGENTS.md` is concise and aligned with `CLAUDE.md`
3. consider adding `.opencode/agents/` versions of the project-specific agents if you want them discoverable natively in OpenCode
4. consider adding `opencode.json` to reference extra instruction files or agent configuration if it improves real usability
5. do not assume `.claude/agents/` or `.claude/settings.json` are automatically honored by OpenCode
6. keep `opencode.json` in the project root, which is OpenCode’s documented project config location

Prefer a clean dual-support setup:
1. `CLAUDE.md` for Claude-compatible tools
2. `AGENTS.md` for OpenCode-native rules
3. `.claude/skills` retained because OpenCode can discover Claude-compatible skills
4. `.opencode/agents` added if you want OpenCode-native project agents

---

## Pipeline State Requirements

Keep pipeline state simple and useful.

Required:
1. phase-level checkpointing for collection, analysis, classification, council, report
2. council stage checkpointing for:
   - `council_stage0_frame`
   - `council_stage1_outputs`
   - `council_stage2_audit`
   - `council_stage3_final`
3. lightweight freshness / hash tracking for findings summary, council input, and report dependencies
4. cost tracking metadata

Do not build a full-blown dependency engine.

---

## Cost Tracking Requirements

Keep cost tracking simple but real.

Track for classification and council where available:
1. model
2. stage
3. prompt tokens
4. completion tokens
5. estimated cost
6. cumulative run cost

This must be wired through the actual code paths, not just documented.

---

## Risks And Gaps You Must Account For

Think through all ripple effects before and during editing.

You must account for:
1. imports that still reference old provider logic
2. config validation and `.env.example` consistency
3. recovery hints in `main.py`
4. prompt text that still names old provider paths incorrectly
5. report text that still describes the old council flow
6. tests that assume old env vars or old council semantics
7. checkpoint names that may conflict with new stage structure
8. stale comments/docstrings referring to Google AI Studio or direct Gemini calls
9. `.claude` agent descriptions drifting out of sync with real code boundaries
10. hooks/settings still referencing outdated assumptions
11. output JSON shape changes that would break reporter integration
12. council prompt lengths and long-context layout
13. cost tracking not being surfaced or persisted clearly enough
14. analysis summary structure not matching what the new council expects
15. failure handling for partial council stages
16. accidental taxonomy drift in classification
17. wording that overclaims analytical causality
18. existing database compatibility
19. duplicated instruction layers drifting apart (`CLAUDE.md`, `AGENTS.md`, `.claude`, `.opencode`)
20. assuming OpenCode uses `.claude/agents` or `.claude/settings.json` automatically
21. keeping redundant old provider code paths after OpenRouter migration
22. leaving duplicate council logic or stale prompts around after the redesign

Be exhaustive. Think before editing.

---

## Validation Requirements

After editing:
1. run relevant tests
2. add/update tests where necessary for changed behavior
3. verify imports and basic execution
4. verify output structures still connect cleanly across phases
5. verify `.claude` files are aligned to the edited codebase

### Test Structure Requirements

Keep the test suite clean, scoped, and aligned with project structure.

Required testing rules:
1. do not put the whole test suite into a single catch-all file
2. split tests by subsystem or concern
3. prefer files such as:
   - config tests
   - database / collection tests
   - analysis tests
   - classification tests
   - council tests
   - reporting tests
4. use shared helpers or fixtures for repeated seed data instead of duplicating setup inline everywhere
5. keep test names specific and behavior-focused
6. keep tests small and readable
7. when changing one subsystem, update only the relevant test modules unless a real cross-phase behavior changed
8. if a helper is shared across tests, place it in a clear helper module or `conftest.py`
9. preserve pytest as the framework
10. avoid giant regression files that mix unrelated concerns

Testing should follow the same project principles as production code:
1. simplicity first
2. surgical changes
3. explicit verification of the changed behavior
4. no unnecessary abstraction

At minimum validate:
1. config import/setup
2. DB schema compatibility
3. analysis summary generation
4. classification serialization compatibility
5. council stage flow and serialization
6. report generation against council output
7. instruction-layer coherence across `CLAUDE.md`, `AGENTS.md`, `.claude`, and any `.opencode` additions

---

## Final Deliverable Expectations

When done, the edited codebase should:
1. still clearly be the same project
2. be simpler than a rebuild would have been
3. preserve compatibility with existing data and outputs
4. produce stronger findings summaries
5. produce a much better council output
6. generate stronger final artifacts
7. use OpenRouter only
8. keep model roster unchanged
9. keep classification schema unchanged
10. include meaningful `.claude` agents, skills, hooks, and rules
11. be cleanly usable in OpenCode with an `AGENTS.md`-based project instruction layer
12. remove redundant provider code and obsolete helper logic introduced by earlier iterations

---

## Final Execution Style

Do the work in the best practical order.
Do not jump randomly between files.
Make edits efficiently.
Minimize unnecessary churn.
Carry ripple fixes through all affected files as you go.
Prefer the smallest correct change when preserving existing good structure, and larger rewrites only where architecture truly needs it.

At the end, provide:
1. a concise summary of what was preserved
2. a concise summary of what was refactored
3. a concise summary of what was rewritten
4. the list of affected files
5. any assumptions or follow-ups
