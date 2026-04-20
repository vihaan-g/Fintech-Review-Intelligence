# Fintech-Review-Intelligence

## Project Purpose

Python data pipeline to analyze Play Store reviews for Indian fintech apps
(Groww, Jupiter, CRED, PhonePe, Paytm). Surfaces non-obvious product intelligence
via SQL analysis and a multi-stage LLM council adapted from Karpathy's council model.
Portfolio project targeting APM/BA roles at Indian fintech startups.

## Tech Stack

- Python 3.11+, SQLite (WAL mode), google-play-scraper
- Classification: Gemini 2.5 Flash Lite (google/gemini-2.5-flash-lite) via OpenRouter
- Council Chairman: Gemini 3.1 Pro Preview (google/gemini-3.1-pro-preview) — Contrarian Chairman
  via OpenRouter
- Council Member 1: Claude Opus 4.7 (anthropic/claude-opus-4.7) via OpenRouter — First Principles analyst
- Council Member 2: DeepSeek R1 (deepseek/deepseek-r1) via OpenRouter — Outsider analyst
- Council Member 3: Qwen 3.6 Plus (qwen/qwen3.6-plus) via OpenRouter — Expansionist analyst
- All API keys via environment variables only — never hardcoded

## Council Architecture (Karpathy-adapted)

- 4 models total: Gemini (Contrarian Chairman) + Claude Opus 4.7 (First Principles) + DeepSeek R1 (Outsider) + Qwen 3.6 Plus (Expansionist)
- Stage 0: Chairman reads the findings summary and produces a ≤100-word analytical frame (the sharpest question to answer)
- Stage 1: Only the 3 specialists generate insights independently in parallel; each receives their role mandate + analytical frame prepended
- Stage 2a: Chairman runs an independent contrarian pass over anonymized specialist outputs
- Stage 2b: Specialists audit the anonymized outputs for evidence quality, unsupported leaps, and missing evidence
- Stage 2c: Chairman synthesizes the contrarian pass and specialist audits into one audit synthesis
- Stage 3: Chairman synthesizes Stage 1 outputs + Stage 2c audit synthesis

## Pipeline Phases

1. Data Collection — ReviewCollector, DatabaseManager
2. SQL Analysis — SQLAnalyst, FindingsSummarizer
3. Classification — ReviewClassifier, BatchProcessor
4. LLM Council — CouncilMember, CouncilOrchestrator
5. Report — InsightReporter

## Class Rules (MANDATORY)

- Use a class for any component that: holds state, manages a resource
  (DB connection, API client), or represents a pipeline stage
- Every class requires: typed **init** parameters, class-level docstring,
  typed signatures on all public methods
- Never use bare functions for pipeline logic — wrap in a class
- Pure utility functions (stateless formatters, parsers) go in utils.py only
- One class per file. File name = class name in snake_case.
  Example: ReviewCollector lives in review_collector.py

## Coding Standards

- Type hints on every function and method signature — no exceptions
- Docstrings on every class and every public method
- No print() in src/ — use the logging module throughout
- All external calls (HTTP, DB, file I/O) wrapped in try/except with
  specific exception types — never bare except
- Secrets: os.getenv() only. A Config class validates all required env
  vars at startup and raises ValueError with a clear message if any are missing
- Max 400 lines per file. Split the class if approaching this limit.
- Never mutate input arguments. Return new objects.

## Context Management

- Run /compact between each pipeline phase
- Compact at 70% context utilization — do not wait for auto-compact
- Never load all 10,000 reviews into context. Work from DB queries and summaries.

## Hooks

- PreToolUse: block any file write containing hardcoded API key patterns
- PostToolUse on Write to src/: run pytest after every source file change

## Subagents

- data-collector — scraping and SQLite only
- sql-analyst — SQL queries and findings summary only
- council-orchestrator — multi-stage council (Stage 0, 1, 2a, 2b, 2c, 3) and all external API calls
- insight-reporter — markdown report generation, no API calls

## Gotchas (add failures here as you encounter them)

- google-play-scraper: add 0.5s sleep between per-app fetches to avoid rate limits
- SQLite WAL mode required for concurrent reads during analysis phases
- DeepSeek R1 responses may include <think>...</think> reasoning blocks
  before the actual answer. Strip these before parsing council output:
  use re.sub(r'<think>.\*?</think>', '', response, flags=re.DOTALL)
- Qwen 3.6 Plus: can produce verbose output — Stage 2 prompt constrains length
- OpenRouter paid models: preflight checks catalog existence only (not free-tier pricing)
