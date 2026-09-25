# Verified queries

Queries checked against the real database, one entry each. "Verified" means the result
was compared with an **independent** SQL query (written differently from the generated
one — a subquery or a separate aggregate — so a bug in codegen can't hide in both).

Add an entry whenever a query is checked, whether or not it was right. Wrong ones are
the most useful: each names the bug and the fix. Questions that failed are also worth
adding to `HELD_OUT` in `scripts/eval_translator.py`, so the translator is scored on
them from then on.

Reference date for these runs: **2025-12-31** (MAX(card_txns.date); the data is 2025).

Template:

```
## N. <question>
- **Date checked:** YYYY-MM-DD
- **DSL:** `...`
- **Verdict:** correct / wrong (what was wrong)
- **SQL:** the generated statement (skip for a repeat of an earlier shape)
- **Output:** the rows, or the shape plus a few rows when there are many
- **Assumptions shown:** what the audit panel said
- **Checked by:** how the numbers were confirmed, independently of that SQL
- **Notes / fixes:** anything found, and what changed
```

---

## 1. "What is the value of the transactions made by customer with customer id CUST0028 for the current month"

- **Date checked:** 2026-09-22
- **DSL:** `SHOW value WHERE customer = 'CUST0028' PERIOD MTD`
- **Verdict:** correct
- **Output:** one KPI, ₹0 (1 row). Assumptions: "Only successful transactions
  considered." · "Period computed relative to the latest transaction date (2025-12-31),
  as the dataset is historical."
- **Checked by:** all of CUST0028's transactions grouped by month and outcome. In
  December 2025 the customer made exactly one transaction, a Business Decline of
  ₹235.07. `value` counts successful transactions only, so the answer is ₹0.
- **Notes / fixes:** the card first showed "—", because `SUM()` over no rows returns
  NULL. Fixed in `frontend/src/format.ts`: an empty value now displays as 0. Caveat
  kept in mind: averages and rates now also show 0 when nothing matched, which reads as
  a real zero.

## 2. "Show the top 10 merchants with <10% business decline rate."

- **Date checked:** 2026-09-22
- **DSL (before):** `SHOW business_decline_rate BY merchant HAVING business_decline_rate < 10 ORDER BY business_decline_rate DESC LIMIT 10`
- **Verdict:** **wrong** — the threshold was off by 100×
- **Checked by:** the decline rates of all 20 merchants (0.0305 to 0.1284). `< 10` means
  "below 1000%", so every merchant passed and the top 3 rows (12.84%, 11.39%, 11.11%)
  should have been excluded. The correct filter is `< 0.1`, which 17 merchants pass.
- **Output after the fix** (10 rows, chart BAR): CityLink Metro Rail 9.68%, SkyWave
  Cable Network 9.55%, MediPlus Chemists 9.27%, GreenLine Service Station 9.20%, Gadget
  Hub Electronics 8.63%, WellCare Pharmacy 7.78%, Highway Fuel Point 7.58%, Central
  Bazaar Stores 7.55%, Metro Lifestyle Department Store 7.53%, Curio Corner Specialty
  Retail 7.45%. No assumptions (rate metrics carry no implicit filter).
- **Notes / fixes:** two layers added.
  1. `config.yaml`: the rate metrics carry `range: [0, 1]`, and the validator rejects a
     HAVING threshold outside it — *"business_decline_rate is a fraction between 0 and 1
     (10% = 0.1), so 10 is out of range; did you mean 0.1?"* The model gets this on its
     one retry; hand-typed DSL gets it directly.
  2. The translator prompt now states that those metrics are fractions.
  Re-run afterwards: the model wrote `< 0.1` first try and the 10 merchants matched the
  hand-checked list (9.68% down to 7.45%).
- **Still open:** "top 10" is read as the highest rates under the limit. Ask for
  "lowest" to sort the other way.

## 3. "Show: volume, value and ats for each issuer, merchant and acquirer group."

- **Date checked:** 2026-09-22
- **DSL:** `SHOW volume, value, ats BY issuer, merchant, acquirer`
- **Verdict:** correct
- **Output:** 60 rows, chart TABLE, columns Issuer / Merchant / Acquirer / Volume /
  Value / ATS. E.g. SBI · CityLink Metro Rail · Razorpay → 52 · ₹1,10,177.49 · ₹2,448.39.
  Assumption: "Value-based metrics count only successful transactions; volume counts all
  attempts."
- **Checked by:** an independent query (a correlated subquery for the successful value)
  on three rows: volume, value and ATS matched exactly. 60 groups = 3 issuers × 20
  merchants (each merchant has exactly one acquirer), covering all 3,000 transactions
  once.
- **Notes / fixes:** ATS is not value ÷ volume here, by design: mixing `volume` (all
  attempts) with money metrics (successes only) makes ATS = successful value ÷
  successful transactions, per grammar.md E14. E.g. SBI / CityLink Metro Rail:
  1,10,177.49 ÷ 45 successes = 2,448.39, not ÷ 52 attempts = 2,118.80. The
  `mixed_metrics` assumption states this.
  The three columns were all headed "Name" (`iss_name`, `name`, `acq_name`). Fixed: the
  backend now sends each dimension's config label, so they read Issuer / Merchant /
  Acquirer.

## 4. "show the value, volume and ats for each issuer, merchant, acquirer group ordered by value. Show this as a table."

- **Date checked:** 2026-09-22
- **DSL:** `SHOW value, volume, ats BY issuer, merchant, acquirer ORDER BY value DESC AS TABLE`
- **Verdict:** correct
- **Output:** the same 60 rows as entry 3, sorted by value: top HSBC · GreenLine Service
  Station · RBL Bank → ₹5,41,130.94 · 51 · ₹12,025.13; bottom HDFC · Curio Corner ·
  Razorpay → ₹47,922.63 · 54 · ₹1,064.95. Same single assumption as entry 3.
- **Checked by:** independent query on the top and bottom rows — HSBC / GreenLine
  Service Station (₹5,41,130.94, 51 txns, ATS 12,025.13) and HDFC / Curio Corner
  (₹47,922.63, 54 txns, ATS 1,064.95) — both matched exactly. Totals also matched:
  3,000 transactions and ₹1,08,45,624.84 of successful value across the 60 groups.
- **Notes / fixes:** none. Sorting is by the `value` alias, descending, and the rows are
  ordered correctly. Column order follows the DSL (value before volume), `AS TABLE` is
  kept (a table always fits), and the headers read Issuer / Merchant / Acquirer.

## 5. "show the successful value, business declined value, technical declined value, successful volume, business declined volume, technical declined volume and successful ats, business declined ats, technical declined ats for each issuer, merchant, acquirer group ordered by value. Show this as a table."

- **Date checked:** 2026-09-23
- **DSL:** `SHOW success_value, business_decline_value, technical_decline_value, success_volume, business_decline_volume, technical_decline_volume, success_ats, business_decline_ats, technical_decline_ats BY issuer, merchant, acquirer ORDER BY success_value DESC AS TABLE`
- **Verdict:** **rejected at first** (out of scope), then correct after config gained the
  outcome-split metrics
- **Output:** 60 rows, chart TABLE, 12 columns (3 dimensions + value/volume/ATS per
  outcome). Top row HSBC · GreenLine Service Station · RBL Bank → success ₹5,41,130.94 /
  45 / ₹12,025.13, business decline ₹4,728.00 / 5 / ₹945.60, technical decline ₹562.79 /
  1 / ₹562.79. No assumptions: the outcome is part of each metric.
- **Checked by:** every outcome column of the top group (HSBC / GreenLine Service
  Station) against a `GROUP BY r.TD_BD` query — 45 / 5 / 1 transactions, ₹5,41,130.94 /
  ₹4,728.00 / ₹562.79, ATS 12,025.13 / 945.60 / 562.79, all exact. Column totals over
  the 60 groups also matched the whole table: 2,640 / 240 / 120 transactions and
  ₹1,08,45,624.84 / ₹5,57,996.44 / ₹3,08,473.12.
- **Notes / fixes:** the first attempt returned
  *"cannot represent multiple status-specific metrics in a single query with given
  vocabulary"*. **No compiler change was needed.** Measures already carry an optional
  `filter`, so `config.yaml` gained three measures (`success_amount`,
  `business_decline_amount`, `technical_decline_amount`) and nine metrics
  (`<outcome>_value`, `<outcome>_volume`, `<outcome>_ats`). Codegen renders them as
  `SUM(CASE WHEN r.TD_BD = '...' THEN t.amt ELSE 0 END)`, with matching conditional
  denominators for the ratios. Added to grammar.md as **E20**, which also puts the
  pattern in the translator's prompt; the model then produced the DSL first try and read
  "ordered by value" as `ORDER BY success_value DESC`.
- **Also worth knowing:** `BY status` answers the same question as three *rows* per
  group; these metrics answer it as three *columns*. No implicit-success assumption
  fires, because the outcome is part of each metric.

## 6. "Show me the value for every card_type and card_variant group for the past 5 months."

- **Date checked:** 2026-09-23
- **DSL:** `SHOW value BY card_type, card_variant PERIOD LAST 5 MONTHS`
- **Verdict:** **rejected at first** (`CANNOT: unsupported period specification for past
  5 months`), then correct once the DSL gained `LAST n MONTHS`
- **SQL:**
  ```sql
  SELECT b.card_type, b.card_variant, SUM(t.amt) AS value
  FROM card_txns t
  JOIN BIN_master b ON t.BIN = b.BIN
  JOIN response_master r ON t.response_code = r.response_code
  WHERE r.TD_BD = 'Success'
    AND t.date >= '2025-08-01' AND t.date <= '2025-12-31'
  GROUP BY b.card_type, b.card_variant;
  ```
- **Output** (9 rows, chart TABLE; run on 2026-09-23, reference date 2025-12-31):

  | Card type | Card variant | Value (₹) |
  |---|---|---:|
  | Debit | Business | 3,92,819.84 |
  | Debit | Classic | 6,20,661.72 |
  | Credit | Business | 10,28,557.13 |
  | Debit | Platinum | 5,45,702.89 |
  | Credit | Classic | 4,13,244.57 |
  | Credit | Platinum | 6,43,713.12 |
  | Prepaid | Classic | 8,49,404.44 |
  | Prepaid | Business | 5,07,689.35 |
  | Prepaid | Platinum | 3,24,736.52 |
  | **Total** | | **53,26,529.58** |

- **Assumptions shown:** "Only successful transactions considered." · "Period computed
  relative to the latest transaction date (2025-12-31), as the dataset is historical." ·
  "Last 5 months = the calendar months August 2025 to December 2025 (2025-08-01 to
  2025-12-31); the last one may be partial."
- **Checked by:** all 9 card_type × card_variant groups against a hand-written query over
  2025-08-01 .. 2025-12-31 — every value matched, and the window covers exactly the five
  months 2025-08 to 2025-12. Row order is arbitrary (no ORDER BY in the query).
- **Notes / fixes:** the period grammar had only `LAST n DAYS`, so "past 5 months" could
  not be expressed; `LAST 150 DAYS` is not the same window. Added `LAST n MONTHS` through
  the whole stack: lexer keyword, parser (`expect_one_of`), a `LAST_N_MONTHS` period kind,
  codegen bounds, config spec and the `last_n_months_window` assumption, plus grammar.md
  **E21** and a prompt rule. Defined as **whole calendar months ending with the reference
  month**: 5 months from 2025-12-31 is 2025-08-01 .. 2025-12-31, with the current month
  possibly partial — the assumption says so. `LAST 1 MONTHS` is identical to `MTD`.
- **Also worth knowing:** for a window that must end on a specific date, use
  `PERIOD FROM '...' TO '...'`. `LAST n WEEKS` still doesn't exist; it would follow the
  same code path if wanted.

## 7. "What is the succesful volume and successful value for every issuer, merchant, acquirer group for the card type credt, debit and card variant bussines for the past 10 months."

- **Date checked:** 2026-09-23
- **DSL:** `SHOW success_volume, success_value BY issuer, merchant, acquirer WHERE card_type IN ('Credit', 'Debit') AND card_variant = 'Business' PERIOD LAST 10 MONTHS`
- **Verdict:** correct, first try, despite the typos ("succesful", "credt", "bussines")
- **SQL:**
  ```sql
  SELECT i.iss_name,
         m.name,
         a.acq_name,
         SUM(CASE WHEN r.TD_BD = 'Success' THEN 1 ELSE 0 END) AS success_volume,
         SUM(CASE WHEN r.TD_BD = 'Success' THEN t.amt ELSE 0 END) AS success_value
  FROM card_txns t
  JOIN issuer_master i ON t.issuer_id = i.id
  JOIN acquirer_master a ON t.acquirer_id = a.id
  JOIN BIN_master b ON t.BIN = b.BIN
  JOIN merchant_master m ON t.merchant_id = m.merchant_id
  JOIN response_master r ON t.response_code = r.response_code
  WHERE b.card_type IN ('Credit', 'Debit')
    AND b.card_variant = 'Business'
    AND t.date >= '2025-03-01' AND t.date <= '2025-12-31'
  GROUP BY i.iss_name, m.name, a.acq_name;
  ```
- **Output:** 60 rows, chart TABLE, columns Issuer / Merchant / Acquirer / Successful
  volume / Successful value (₹). First rows: HSBC · CityLink Metro Rail · Razorpay →
  8 · ₹78,772.32; SBI · Spice Route Kitchen · ICICI Bank → 11 · ₹11,455.52; HDFC ·
  Artisan Gift Emporium · Razorpay → 9 · ₹21,957.17. Totals across the 60 groups:
  **561 transactions, ₹21,68,471.80**.
- **Assumptions shown:** "Period computed relative to the latest transaction date
  (2025-12-31), as the dataset is historical." · "Last 10 months = the calendar months
  March 2025 to December 2025 (2025-03-01 to 2025-12-31); the last one may be partial."
  No success assumption: the outcome is inside `success_volume` / `success_value`.
- **Checked by:** all 60 groups compared with a hand-written query that filters
  `r.TD_BD='Success'` in WHERE (rather than inside conditional sums) — zero mismatches,
  identical totals, and the window covers exactly the ten months 2025-03 to 2025-12.
  1,954 transactions in that window are prepaid or non-Business and were correctly
  excluded.
- **Notes / fixes:** none. Worth noting the typos never reached the resolver: the model
  wrote `'Credit'` and `'Business'` itself, so no `value_corrected` assumption fired.
  Had it copied the typo through, the enum index would have corrected it and said so.
  Filters land in WHERE (row-level) while the outcome stays inside the metrics, so the
  two mechanisms compose without interfering.

## 8. "Can you tell me the issuer with the highest decline rate for the current month?"

- **Date checked:** 2026-09-23
- **DSL (before):** `SHOW business_decline_rate BY issuer PERIOD MTD ORDER BY business_decline_rate DESC LIMIT 1`
- **Verdict:** **wrong for the question asked** — it answered "highest *business* decline
  rate", which ranks issuers differently from "highest decline rate"
- **Output (before):** HDFC Bank, 8.85%
- **Checked by:** December 2025 per issuer, from a `GROUP BY r.TD_BD` query:

  | Issuer | Attempts | Business | Technical | Business rate | All declines |
  |---|---:|---:|---:|---:|---:|
  | HDFC Bank | 113 | 10 | 1 | **8.85%** | 9.73% |
  | HSBC Bank | 102 | 7 | 5 | 6.86% | 11.76% |
  | State Bank of India (SBI) | 92 | 6 | 5 | 6.52% | **11.96%** |

  HDFC leads on business declines only; SBI has the highest overall rate, because its
  technical declines are five times HDFC's.
- **Notes / fixes:** config had `success_rate`, `business_decline_rate` and
  `technical_decline_rate` but **no overall `decline_rate`**, so the model could not express
  the question. Added `decline_count` / `decline_amount` measures (filter
  `r.TD_BD <> 'Success'`) and the metrics `decline_rate`, `decline_volume`, `decline_value`
  and `decline_ats`. Config only — no compiler change.
- **Output (after):** asked as a question, the model now writes
  `SHOW decline_rate BY issuer PERIOD MTD ORDER BY decline_rate DESC LIMIT 1` and returns
  **State Bank of India (SBI), 11.96%**, matching the hand-checked figure.
- **Also worth knowing:** business and technical declines answer different questions —
  cardholder and limit problems versus infrastructure — so both narrow metrics remain, and
  kb-05 now warns that the two rankings can disagree.
