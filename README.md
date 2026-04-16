# fintech-review-intelligence

## What I Found

- DRY RUN MOCK: Council synthesis placeholder.
- This is a dry-run output with no real LLM calls.
- Run without --dry-run to generate real insights from the 4-model council.

## What This Is

A Python data pipeline that scrapes Play Store reviews for four Indian fintech apps (Fi Money, Jupiter, CRED, PhonePe) and surfaces non-obvious product intelligence via SQL analysis and a 4-model LLM council adapted from Karpathy's council model. Built as a portfolio project targeting APM/BA roles at Indian fintech startups.

## How to Run

1. Clone the repo
2. `cp .env.example .env` and add your API keys
3. `pip install -r requirements.txt`
4. `python src/main.py`

## Architecture

```
Play Store (4 apps)
      ↓ google-play-scraper
SQLite DB (reviews.db)
      ↓ SQLAnalyst (6 queries)
Findings Summary
      ↓ Gemini 2.5 Flash (batch classification)
Classification Results
      ↓ 4-Model Council (Karpathy-adapted)
      │  Stage 1: Parallel independent insights
      │  Stage 2: Anonymized gap-finding review
      │  Stage 3: Gemini 3 Flash chairman synthesis
findings_report.md
```

## SQL Queries

See [queries/analysis_queries.sql](queries/analysis_queries.sql) for all 6 analytical queries with commentary.

## Tech Stack

- Python 3.11, SQLite, google-play-scraper
- Classification: Gemini 2.5 Flash (Google AI Studio free tier)
- Council: Gemini 3 Flash Preview (chairman) + DeepSeek R1 + Qwen3-235B-A22B + Llama 4 Maverick (all OpenRouter :free)