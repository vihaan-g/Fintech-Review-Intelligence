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
2. **Analyse (SQL)** — run 8 analytical queries (rating distribution,
   high-signal low-rating reviews, developer reply impact, keyword
   frequency, review volume by week, cross-app summary, classification
   breakdown, top classified complaints) and compile a structured
   findings summary.
3. **Classify** — batch-classify every review into product areas
   (onboarding, UX, transactions, support, performance, trust) using
   Gemini 2.5 Flash Lite. Rate-limited to stay inside the 14 RPM / 1000-per-day
   Paid tier (Google AI Studio); ~₹32 per full run.
4. **Council** — run a 4-stage LLM deliberation:
   - Stage 0: chairman frames the analytical question (≤100 words).
   - Stage 1: four models generate insights independently, in parallel,
     each with a cognitive role mandate (First Principles / Outsider / Expansionist).
   - Stage 2: chairman as Contrarian — anonymised gap analysis (A/B/C/D)
     with Three Tensions: Outsider vs Experts, Expansionist vs First Principles,
     Consensus vs Evidence.
   - Stage 3: chairman synthesises a final report.
5. **Report** — write `outputs/findings_report.md`,
   `outputs/linkedin_snippet.txt`, and `outputs/README.md` (an auto-generated
   companion summary, separate from this hand-written file).

## How to Run

```bash
cp .env.example .env            # add GEMINI_API_KEY and OPENROUTER_API_KEY
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
      ↓ Gemini 2.5 Flash Lite (batch classification)
Classification Results
      ↓ 4-Model Council (Karpathy-adapted)
      │  Stage 0: Contrarian Chairman analytical framing
      │  Stage 1: Role-mandated parallel insights
      │  Stage 2: Contrarian Three Tensions gap analysis
      │  Stage 3: Chairman synthesis
outputs/findings_report.md
outputs/linkedin_snippet.txt
```

## Tech Stack

- Python 3.11, SQLite (WAL mode), `google-play-scraper`
- Classification: Gemini 2.5 Flash Lite (Google AI Studio, paid — ~₹32/run)
- Council chairman: Gemini 3.1 Pro Preview — Contrarian Chairman (Google AI Studio, paid — ~₹22/run)
- Council members: Claude Opus 4.7 (First Principles), DeepSeek R1 (Outsider),
  Qwen 3.6 Plus (Expansionist) — all via OpenRouter (paid)
- Estimated cost per council run: ~$0.059 (Opus 4.7: ~$0.050, DeepSeek R1: ~$0.005, Qwen 3.6 Plus: ~$0.004)
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
    review_classifier.py             # Gemini classifier (retries, parse-safe)
    batch_processor.py               # rate-limited batching + checkpointing
  council/
    council_member.py                # one LLM, one API call path
    council_orchestrator.py          # 4-stage Karpathy council (Stage 0–3)
  agents/
    insight_reporter.py              # generates report / snippet / companion README
queries/
  analysis_queries.sql               # SQL portfolio artifact
tests/
  test_smoke.py                      # 30+ unit / integration tests
```

## Running Tests

```bash
python -m pytest tests/ -v
```

## Notes

- Generated artifacts under `outputs/` (findings report, LinkedIn snippet,
  generated README companion) are intentionally git-ignored. Only the
  hand-written top-level README is committed.
- Council results are deterministic in structure but not in content — each
  real run produces a different synthesis. Re-run without `--dry-run` and
  check `outputs/findings_report.md` for the latest output.
