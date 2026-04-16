-- =============================================================================
-- Fintech Review Intelligence — Analysis Queries
-- =============================================================================
-- Portfolio artifact. Each query answers a specific product-intelligence
-- question about Play Store reviews for Fi Money, Jupiter, CRED, PhonePe.
-- All queries use parameterized placeholders (?) where user-supplied values
-- appear. No f-string interpolation is used in the Python layer.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Query 1: Rating Distribution Over Time
-- -----------------------------------------------------------------------------
-- What it measures:
--   Monthly average rating and review volume per app for the trailing 12 months.
--
-- Why it matters for product analysis:
--   A sudden drop in average rating in a given month pinpoints a bad release
--   or incident (e.g. a forced KYC re-verification, a UPI downtime). A rising
--   trend after a dip signals a successful fix. Volume spikes in the same period
--   amplify the signal — 500 reviews in one week vs. 50 in a normal week is
--   newsworthy regardless of sentiment.
-- -----------------------------------------------------------------------------
SELECT
    app_name,
    strftime('%Y-%m', date)  AS month,
    ROUND(AVG(rating), 2)    AS avg_rating,
    COUNT(*)                 AS review_count
FROM reviews
WHERE date >= date('now', '-12 months')
GROUP BY app_name, month
ORDER BY app_name ASC, month ASC;


-- -----------------------------------------------------------------------------
-- Query 2: High-Signal Low-Rating Reviews
-- -----------------------------------------------------------------------------
-- What it measures:
--   Reviews where other users clicked "helpful" (thumbs_up >= ?) AND the
--   reviewer gave 1 or 2 stars. The threshold is a runtime parameter.
--
-- Why it matters for product analysis:
--   A one-star review with 0 thumbs-up is anecdotal. A one-star review with
--   200 thumbs-up is a validated pain point — the crowd agreed the complaint
--   was legitimate. This filter surfaces the highest-priority bug reports and
--   UX failures that the product team should act on first.
-- -----------------------------------------------------------------------------
SELECT
    app_name,
    review_id,
    rating,
    thumbs_up,
    text,
    date
FROM reviews
WHERE thumbs_up >= ?
  AND rating <= 2
ORDER BY thumbs_up DESC;


-- -----------------------------------------------------------------------------
-- Query 3: Developer Reply Impact
-- -----------------------------------------------------------------------------
-- What it measures:
--   For 1-2 star reviews, the reply rate per app, plus the average rating of
--   reviews that received a developer reply vs. those that did not.
--
-- Why it matters for product analysis:
--   A high reply rate on negative reviews signals an engaged, accountable team.
--   If avg_rating_with_reply is higher than avg_rating_without_reply, it
--   suggests that the replied-to cohort had a somewhat better experience
--   (or that developers target less-severe negatives). Either way, it is a
--   customer-success lever that varies widely across Indian fintech apps.
--
-- Note: COALESCE is used on AVG(...) to return 0.0 instead of NULL when no
-- reviews exist for a given has_dev_reply value (e.g. an app with zero
-- developer replies would produce NULL for avg_rating_with_reply without it).
-- Division-by-zero for reply_rate_pct is guarded in the Python layer.
-- -----------------------------------------------------------------------------
SELECT
    app_name,
    COUNT(*)                                        AS total_low_ratings,
    SUM(has_dev_reply)                              AS replied_count,
    ROUND(
        COALESCE(AVG(CASE WHEN has_dev_reply = 1 THEN CAST(rating AS FLOAT) END), 0.0), 2
    )                                               AS avg_rating_with_reply,
    ROUND(
        COALESCE(AVG(CASE WHEN has_dev_reply = 0 THEN CAST(rating AS FLOAT) END), 0.0), 2
    )                                               AS avg_rating_without_reply
FROM reviews
WHERE rating <= 2
GROUP BY app_name;


-- -----------------------------------------------------------------------------
-- Query 4: Keyword Frequency
-- -----------------------------------------------------------------------------
-- What it measures:
--   Count of reviews per app that contain a given keyword (case-insensitive).
--   Run once per keyword; the Python layer iterates over the keyword list.
--   The keyword is a runtime parameter passed as a LIKE pattern.
--
-- Why it matters for product analysis:
--   Keyword frequency converts free-text reviews into quantifiable signals.
--   "crash" spiking for one app in a given month = probable regression.
--   "kyc" high across all apps = industry-wide onboarding friction, not a
--   single-app problem. "refund" concentrated on one app = payment ops issue.
-- -----------------------------------------------------------------------------
SELECT
    app_name,
    COUNT(*) AS mention_count
FROM reviews
WHERE LOWER(text) LIKE '%' || LOWER(?) || '%'
GROUP BY app_name;


-- -----------------------------------------------------------------------------
-- Query 5: Review Volume by Week
-- -----------------------------------------------------------------------------
-- What it measures:
--   Total review count and average rating per app, grouped by ISO week
--   (YYYY-WW format using SQLite's strftime).
--
-- Why it matters for product analysis:
--   Weekly granularity captures short-lived incidents that monthly averages
--   would smooth away. A 3x volume spike in week 2026-14 correlates with
--   an app update or a viral complaint thread. Cross-referencing volume spikes
--   with average rating drops in the same week gives a precise incident
--   timeline — useful for the LLM council's root-cause analysis.
--
-- NOTE: SQLite strftime('%W') counts weeks from first Monday.
-- Days in early January before the first Monday appear in week 00.
-- This is a known SQLite limitation — treat week 00 data with caution.
-- -----------------------------------------------------------------------------
SELECT
    app_name,
    strftime('%Y-%W', date)  AS week,
    COUNT(*)                 AS review_count,
    ROUND(AVG(rating), 2)    AS avg_rating
FROM reviews
GROUP BY app_name, week
ORDER BY app_name ASC, week ASC;


-- -----------------------------------------------------------------------------
-- Query 6: Cross-App Summary
-- -----------------------------------------------------------------------------
-- What it measures:
--   High-level per-app statistics: total reviews, average rating, percentage
--   of one-star and five-star reviews, and developer reply rate across all
--   reviews (not just low-rated ones).
--
-- Why it matters for product analysis:
--   The cross-app summary is the "executive snapshot" that anchors the council
--   input. A 70% five-star rate is meaningless without knowing the 15% one-star
--   companion. The reply_rate_pct across all reviews (not just negatives) shows
--   overall developer engagement. most_common_rating (computed separately via
--   subquery) reveals whether the distribution is bimodal (1s and 5s
--   with few 2-3-4s), which is typical for apps with strong advocates and
--   strong detractors but little middle ground.
--
-- Note: SQLite has no MODE() aggregate function. most_common_rating is
-- computed via a separate subquery per app:
--   SELECT rating FROM reviews WHERE app_name = ?
--   GROUP BY rating ORDER BY COUNT(*) DESC LIMIT 1
-- The window function form below achieves the same result across all apps
-- in a single query pass.
-- -----------------------------------------------------------------------------
SELECT
    app_name,
    COUNT(*)                                                              AS total_reviews,
    ROUND(AVG(rating), 2)                                                 AS avg_rating,
    ROUND(100.0 * SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) / COUNT(*), 2)
                                                                          AS pct_one_star,
    ROUND(100.0 * SUM(CASE WHEN rating = 5 THEN 1 ELSE 0 END) / COUNT(*), 2)
                                                                          AS pct_five_star,
    ROUND(100.0 * SUM(has_dev_reply) / COUNT(*), 2)                       AS reply_rate_pct
FROM reviews
GROUP BY app_name;

-- -----------------------------------------------------------------------------
-- Query: Most Common Rating (per cross_app_summary)
-- -----------------------------------------------------------------------------
-- What it measures:
--   The modal (most frequently occurring) rating per app.
--
-- Why it matters for product analysis:
--   Average rating can be misleading with bimodal distributions
--   (e.g. many 1-stars and many 5-stars). The mode reveals which end of
--   the distribution dominates user sentiment.
--
-- SQLite limitation: No MODE() aggregate function. This subquery implements
--   the equivalent via GROUP BY + ORDER BY COUNT(*) DESC + LIMIT 1.
-- -----------------------------------------------------------------------------
-- most_common_rating subquery workaround (no MODE() in SQLite):
-- Single-app form: SELECT rating FROM reviews WHERE app_name = ?
--                  GROUP BY rating ORDER BY COUNT(*) DESC LIMIT 1
-- Multi-app form using window function (used in Python layer):
SELECT app_name, rating AS most_common_rating
FROM (
    SELECT
        app_name,
        rating,
        COUNT(*) AS cnt,
        RANK() OVER (PARTITION BY app_name ORDER BY COUNT(*) DESC) AS rnk
    FROM reviews
    GROUP BY app_name, rating
) ranked
WHERE rnk = 1;
