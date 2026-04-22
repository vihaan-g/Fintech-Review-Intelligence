# Fintech Review Intelligence

A product intelligence project that analyzes Play Store reviews across major Indian fintech apps to surface user pain points, trust breakdowns, and cross-app product patterns.

I built this to answer a more useful product question than simple sentiment analysis: **what kinds of failures actually damage trust in fintech products, and which complaints users tolerate when the core product still works?**

The project analyzes **11,000 Play Store reviews** across **Groww, Jupiter, CRED, PhonePe, and Paytm** using a pipeline that combines **SQLite-based analysis, complaint classification, and structured LLM synthesis** to generate product-facing findings.

---

## Why I Built This

Most review-analysis projects stop at sentiment scores, star distributions, or keyword counts. That is useful, but not enough for real product decision-making.

I wanted to build something closer to a practical product intelligence workflow:

- collect large-scale user feedback from public app reviews
- identify high-signal negative feedback
- classify complaint patterns
- compare apps at a cross-product level
- surface findings that matter for product, trust, and support teams

The goal was not just to analyze reviews, but to turn unstructured user feedback into structured insight.

---

## Problem Statement

For consumer fintech products, not all complaints matter equally.

Some issues create friction but do not significantly damage long-term trust. Others — especially around onboarding, account access, and financial control — can trigger severe rating collapse and reputational damage.

This project was built to identify:

- which complaint types are most destructive to user trust
- how complaint patterns differ across major Indian fintech apps
- whether high support responsiveness actually improves user outcomes
- what product teams should prioritize based on review evidence

---

## Scope

**Apps analyzed**

- Groww
- Jupiter
- CRED
- PhonePe
- Paytm

**Dataset**

- 11,000 Play Store reviews
- 2,200 recent reviews per app
- English-language reviews
- India-focused fintech app set

---

## Key Findings

### 1) Trust and account-access failures are far more damaging than UX friction

Jupiter showed severe patterns around onboarding, trust, and account access, along with significantly weaker ratings than the rest of the app set.

This suggests that in fintech, users may tolerate interface friction or clutter, but react very strongly when they feel their money, identity, or access is at risk.

### 2) High developer reply rates do not automatically repair user sentiment

Jupiter had extremely high developer reply coverage on low-rated reviews, but that did not meaningfully improve rating outcomes.

The practical implication is that reactive support cannot compensate for broken core product flows.

### 3) Strong products can absorb UX complaints when core reliability remains intact

Apps like PhonePe and Groww showed that users can still rate a product highly despite complaints about interface clutter or friction, as long as the primary product utility remains dependable.

### 4) Complaint mix matters more than complaint volume alone

This project was designed not just to count negative feedback, but to distinguish between different failure types — such as UX friction, transaction issues, trust breakdowns, onboarding failures, and support-heavy complaints.

That distinction makes the output more useful for prioritization.

---

## What I Built

I built a five-phase Python pipeline:

1. **Collection**  
   Scrapes Play Store reviews and stores them in SQLite.

2. **Analysis**  
   Runs SQL-based analysis on review patterns, ratings, time trends, and high-signal negative feedback.

3. **Classification**  
   Applies structured complaint labeling to reviews so patterns can be grouped semantically instead of only through keywords.

4. **Council**  
   Uses a staged multi-model review process to challenge weak interpretations and synthesize stronger findings.

5. **Report**  
   Produces recruiter-readable and product-readable output artifacts from the findings.

---

## Analytical Approach

The analysis layer combines broad quantitative signals with more targeted complaint diagnosis.

### Base analysis

- cross-app summary statistics
- high-signal low-rating reviews
- keyword frequency patterns
- rating trends over time
- weekly review volume / anomaly signals
- developer reply behavior

### Classification enrichment

- complaint category breakdowns
- over-indexing by app
- top classified complaint examples
- stronger semantic interpretation of low-rated feedback

This design helped the project move beyond a dashboard-style summary toward sharper product insight.

---

## Council Design

Instead of asking one model to generate a final report directly, I used a staged council system with multiple cognitive roles.

### Council roles

- **Chairman** — frames the problem, challenges weak logic, and writes the final synthesis
- **First Principles** — focuses on root-cause reasoning
- **Outsider** — looks for uncomfortable or non-obvious interpretations
- **Expansionist** — explores broader product and strategy implications

### Council stages

- Stage 0: chairman analytical frame
- Stage 1: independent specialist analyses
- Stage 2a: chairman contrarian pass
- Stage 2b: anonymized evidence audits by specialists
- Stage 2c: chairman audit synthesis
- Stage 3: final synthesis

This structure was designed to reduce shallow pattern-matching, unsupported claims, and one-model bias.

---

## Outputs

The project generates a set of structured outputs, including:

- `reviews.db`
- `findings_summary.json`
- `council_result.json`
- `findings_report.md`
- `linkedin_snippet.txt`

These outputs support both technical inspection and business-facing communication.

---

## Skills Demonstrated

This project demonstrates:

- product thinking through complaint prioritization and cross-app interpretation
- data analysis using SQL and structured review data
- Python pipeline design
- working with SQLite for real project storage and checkpointing
- converting unstructured text into structured analytical outputs
- communicating findings in a decision-useful format
- using LLMs for synthesis with evidence checks instead of naive one-shot prompting

---

## Why This Matters for Product Teams

For fintech products, review data is not just sentiment data. It can reveal:

- where trust is breaking
- which workflows create the most damaging user pain
- whether support systems are masking deeper operational problems
- what kinds of complaints are survivable versus structurally dangerous

This project was built around that idea.

---

## Tech Stack

- **Python**
- **SQLite**
- **SQL**
- **Play Store review scraping**
- **LLM-based complaint classification and synthesis**

---

## Repository Goal

This repository is meant to demonstrate a profile I want to keep building toward:

**someone who can combine business thinking, product judgment, and technical execution to generate useful insight from real-world data.**
