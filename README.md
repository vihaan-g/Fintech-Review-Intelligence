# Fintech Review Intelligence

A Python data pipeline that scrapes Play Store reviews for five Indian fintech
apps (Groww, Jupiter, CRED, PhonePe, Paytm) and surfaces non-obvious product
intelligence via SQL analysis and a 4-model LLM council adapted from
Karpathy's council model.

Built as a portfolio project targeting APM / product-analyst roles at Indian
fintech startups.

## What This Is

The pipeline does five things in order:

1. **Collect** — scrape Play Store reviews (India, English) for five apps
   using `google-play-scraper`, store them in a single SQLite database
   with WAL mode enabled.
2. **Analyze (SQL)** — run 8 analytical queries (rating distribution,
   high-signal low-rating reviews, developer reply impact, keyword
   frequency, review volume by week, cross-app summary, classification
   breakdown, top classified complaints) and compile a structured
   findings summary.
3. **Classify** — batch-classify every review into product areas
   (onboarding, UX, transactions, support, performance, trust) using
   Gemini 2.5 Flash Lite via OpenRouter.
4. **Council** — run a multi-stage LLM deliberation:
   - Stage 0: chairman frames the analytical question (≤100 words).
   - Stage 1: three specialist models generate independent insights in parallel.
   - Stage 2a: chairman runs an anonymized contrarian pass on specialist outputs.
   - Stage 2b: specialists audit the anonymized outputs for evidence quality.
   - Stage 2c: chairman synthesizes the audit phase into one audit synthesis.
   - Stage 3: chairman writes the final report.
5. **Report** — write `outputs/findings_report.md`,
   `outputs/linkedin_snippet.txt`, and `outputs/README.md` (an auto-generated
   companion summary, separate from this hand-written file).

## How to Run

```bash
cp .env.example .env            # add OPENROUTER_API_KEY
pip install -r requirements.txt
python src/main.py              # full pipeline
python src/main.py --dry-run    # wiring check, no API calls
python src/main.py --phase analysis   # single phase (collection / analysis / classification / council / report)
```

Each phase checkpoints to the `pipeline_state` table — a killed run resumes
where it left off on the next invocation.

## Architecture

```
Play Store (5 apps)
      ↓ google-play-scraper
SQLite DB (reviews.db)
      ↓ SQLAnalyst (8 queries)
Findings Summary (outputs/findings_summary.json)
      ↓ Gemini 2.5 Flash Lite via OpenRouter (batch classification)
Classification Results
      ↓ 4-Model Council (Karpathy-adapted)
      │  Stage 0: Contrarian Chairman analytical framing
      │  Stage 1: Specialist insights
      │  Stage 2a: Chairman contrarian pass
      │  Stage 2b: Anonymized evidence audits
      │  Stage 2c: Chairman audit synthesis
      │  Stage 3: Chairman final report
outputs/findings_report.md
outputs/linkedin_snippet.txt
```

## Tech Stack

- Python 3.11, SQLite (WAL mode), `google-play-scraper`
- Classification: Gemini 2.5 Flash Lite via OpenRouter
- Council chairman: Gemini 3.1 Pro Preview — Contrarian Chairman via OpenRouter
- Council members: Claude Opus 4.7 (First Principles), DeepSeek R1 (Outsider),
  Qwen 3.6 Plus (Expansionist) — all via OpenRouter (paid)
- All API keys via environment variables — never hardcoded.

## SQL Queries

See [queries/analysis_queries.sql](queries/analysis_queries.sql) for all 8
analytical queries, each annotated with what it measures and why it matters
for product analysis.

## Project Layout

```
src/
  config.py                          # env var validation
  main.py                            # pipeline entry point
  data_collection/
    review_collector.py              # google-play-scraper wrapper
    database_manager.py              # SQLite schema + CRUD
  analysis/
    sql_analyst.py                   # 8 analytical queries
    findings_summarizer.py           # compiles structured findings
  classification/
    review_classifier.py             # OpenRouter classifier (retries, parse-safe)
    batch_processor.py               # rate-limited batching + checkpointing
  council/
    council_member.py                # one LLM, one API call path
    council_orchestrator.py          # staged Karpathy-adapted council (Stage 0, 1, 2a, 2b, 2c, 3)
    council_prompts.py               # central prompt library for council stages
  agents/
    insight_reporter.py              # generates report / snippet / companion README
queries/
  analysis_queries.sql               # SQL portfolio artifact
tests/
  test_*.py                          # subsystem-focused unit / integration tests
```

## Running Tests

```bash
python3 -m pytest tests/ -v
```

## Notes

- Generated artifacts under `outputs/` (findings report, LinkedIn snippet,
  generated README companion) are intentionally git-ignored. Only the
  hand-written top-level README is committed.
- Council results are deterministic in structure but not in content — each
  real run produces a different synthesis. Re-run without `--dry-run` and
  check `outputs/findings_report.md` for the latest output.
