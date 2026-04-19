# Fintech-Review-Intelligence

## Project Purpose

Python data pipeline to analyze Play Store reviews for Indian fintech apps
(Groww, Jupiter, CRED, PhonePe, Paytm). Surfaces non-obvious product intelligence
via SQL analysis and a 4-stage LLM council adapted from Karpathy's council model.
Portfolio project targeting APM/BA roles at Indian fintech startups.

## Tech Stack

- Python 3.11+, SQLite (WAL mode), google-play-scraper
- Classification: Gemini 2.5 Flash Lite (gemini-2.5-flash-lite) via Google AI Studio free tier (1,000 req/day)
- Council Chairman: Gemini 3.1 Pro Preview (gemini-3.1-pro-preview) — Contrarian Chairman
  via Google AI Studio free tier — dynamic thinking enabled by default
- Council Member 1: DeepSeek R1 (deepseek/deepseek-r1:free) via OpenRouter :free — First Principles analyst
- Council Member 2: Qwen3-235B-A22B (qwen/qwen3-235b-a22b:free) via OpenRouter :free — Outsider analyst
- Council Member 3: Llama 4 Maverick (meta-llama/llama-4-maverick:free) via OpenRouter :free — Expansionist analyst
- All API keys via environment variables only — never hardcoded
- Estimated costs per full run: Classification ~₹32, Chairman ~₹22, Members ₹0 (free tier), Total ~₹54

## Council Architecture (Karpathy-adapted)

- 4 models total: Gemini (Contrarian Chairman) + DeepSeek R1 (First Principles) + Qwen3-235B (Outsider) + Llama 4 Maverick (Expansionist)
- Stage 0: Chairman reads the findings summary and produces a ≤100-word analytical frame (the sharpest question to answer)
- Stage 1: All 4 models generate insights independently in parallel; each non-chairman member receives their role mandate + analytical frame prepended
- Stage 2: Chairman acts as Contrarian — anonymized gap analysis (A/B/C/D) with Three Tensions:
  - TENSION 1: Outsider vs Domain Experts
  - TENSION 2: Expansionist vs First Principles
  - TENSION 3: Consensus vs Evidence
- Stage 3: Gemini chairman synthesizes Stage 1 outputs + Stage 2 gap analysis
- Total API calls per council run: ~7 (1 Stage 0 + 4 Stage 1 + 1 Stage 2 + 1 Stage 3)

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
- council-orchestrator — 3-stage council and all external API calls
- insight-reporter — markdown report generation, no API calls

## Gotchas (add failures here as you encounter them)

- google-play-scraper: add 0.5s sleep between per-app fetches to avoid rate limits
- OpenRouter :free models: 50 req/day shared across all free models on one account
- Qwen3-235B: higher latency than other council members — set a 90s timeout
- SQLite WAL mode required for concurrent reads during analysis phases
- Stage 2 prompt must constrain output length or Llama 4 Maverick gets verbose
- Qwen3-235B responses may include <think>...</think> reasoning blocks
  before the actual answer. Strip these before parsing council output:
  use re.sub(r'<think>.\*?</think>', '', response, flags=re.DOTALL)
