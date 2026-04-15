# fintech-review-intelligence

## Project Purpose
Python data pipeline to analyze Play Store reviews for Indian fintech apps
(Fi Money, Jupiter, CRED, PhonePe). Surfaces non-obvious product intelligence
via SQL analysis and a 3-stage LLM council adapted from Karpathy's council model.
Portfolio project targeting APM/BA roles at Indian fintech startups.

## Tech Stack
- Python 3.11+, SQLite (WAL mode), google-play-scraper
- Classification: Gemini 2.5 Flash via Google AI Studio free tier (1,500 req/day)
- Council Chairman: Gemini 2.5 Flash via Google AI Studio free tier
- Council Member 1: DeepSeek R1 via OpenRouter :free (Chinese/RL-trained reasoning)
- Council Member 2: Qwen3-235B-A22B via OpenRouter :free (Alibaba/MoE)
- Council Member 3: Llama 4 Maverick via OpenRouter :free (Meta/Western MoE)
- All API keys via environment variables only — never hardcoded

## Council Architecture (Karpathy-adapted)
- 4 models total: Gemini (chairman) + DeepSeek R1 + Qwen3-235B + Llama 4 Maverick
- Stage 1: All 4 models generate insights independently and in parallel
- Stage 2: All 4 models review each other with identities anonymized
  (responses labelled A/B/C/D — no model knows who wrote what)
- Stage 3: Gemini chairman synthesizes Stage 1 outputs + Stage 2 gap analysis
- Total API calls per council run: ~13 (well within all free tier limits)

## Pipeline Phases
1. Data Collection    — ReviewCollector, DatabaseManager
2. SQL Analysis       — SQLAnalyst, FindingsSummarizer
3. Classification     — ReviewClassifier, BatchProcessor
4. LLM Council        — CouncilMember, CouncilOrchestrator
5. Report             — InsightReporter

## Class Rules (MANDATORY)
- Use a class for any component that: holds state, manages a resource
  (DB connection, API client), or represents a pipeline stage
- Every class requires: typed __init__ parameters, class-level docstring,
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
- data-collector       — scraping and SQLite only
- sql-analyst          — SQL queries and findings summary only
- council-orchestrator — 3-stage council and all external API calls
- insight-reporter     — markdown report generation, no API calls

## Gotchas (add failures here as you encounter them)
- google-play-scraper: add 0.5s sleep between per-app fetches to avoid rate limits
- OpenRouter :free models: 50 req/day shared across all free models on one account
- Qwen3-235B: higher latency than other council members — set a 90s timeout
- SQLite WAL mode required for concurrent reads during analysis phases
- Stage 2 prompt must constrain output length or Llama 4 Maverick gets verbose
