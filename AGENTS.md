# Fintech Review Intelligence

Python pipeline for analyzing Play Store reviews for five Indian fintech apps:
- Groww
- Jupiter
- CRED
- PhonePe
- Paytm

The project’s main goal is high-quality final product-intelligence output, not elaborate infrastructure.

## Working Style

1. Edit the current codebase in place unless a subsystem clearly needs a rewrite.
2. Prefer the smallest correct change.
3. Preserve existing architecture, DB compatibility, output compatibility, and classification schema unless explicitly changing them.
4. Keep code simple, typed, class-based, and well logged.
5. No `print()` in `src/`; use `logging`.
6. All external calls need specific exception handling.
7. Do not silently assume ambiguous requirements.

## Editing Principles

1. Think before coding: if an assumption matters, surface it instead of guessing silently.
2. Simplicity first: prefer the minimum code that solves the problem.
3. Surgical changes: touch only what the task requires.
4. Goal-driven execution: define verification steps and check them.
5. Remove redundancy created by your changes, but do not do unrelated cleanup.

## Critical Project Constraints

1. Use OpenRouter only for all LLM interactions.
2. Do not use direct Google AI Studio integration.
3. Keep the intended model roster unchanged unless explicitly requested.
4. Keep the classification schema unchanged:
   - `product_area`
   - `specific_feature_request`
   - `workflow_breakdown`
   - `confidence`
   - `parse_failed`
5. Preserve SQLite compatibility and `pipeline_state` usage.
6. Preserve the five pipeline phases:
   - collection
   - analysis
   - classification
   - council
   - report

## Repo Structure

- `src/data_collection/` : scraping and database
- `src/analysis/` : SQL analysis and findings summary
- `src/classification/` : review classification and batching
- `src/council/` : LLM council orchestration
- `src/agents/` : report generation
- `queries/analysis_queries.sql` : documented analytical SQL
- `.claude/` : Claude-compatible skills, hooks, and agents
- `.opencode/agents/` : OpenCode-native project agents

## Testing And Validation

Use pytest.

Run targeted tests after relevant edits and run the broader suite before concluding major work.

Common command:

```bash
python -m pytest tests/ -q
```

## AI Workflow Expectations

Use the project-specific OpenCode agents when their subsystem matches the task:
1. `@data-collector`
2. `@sql-analyst`
3. `@council-orchestrator`
4. `@insight-reporter`

Prefer these project agents over generic agents when the task fits their scope.

Load relevant skills before major subsystem work:
1. `cost-aware-llm-pipeline`
2. `multi-agent-patterns`
3. `context-optimization`
4. `evaluation`

`.claude/skills` is part of the project and should be used intentionally.

## Important Guidance Sources

Treat these as important project instructions:
1. `CLAUDE.md`
2. `.claude/rules/common/*.md`
3. `.claude/rules/python/*.md`

Keep behavior aligned across `AGENTS.md`, `CLAUDE.md`, `.claude`, and `.opencode`.
