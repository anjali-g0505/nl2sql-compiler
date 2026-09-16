# DSL grammar & compiler oracle

The frozen spec for the NL→DSL→SQL compiler. `config.yaml` is the machine-readable
source of truth for the vocabulary (metrics, dimensions, joins); this file defines the
**syntax**, the **semantic rules**, and the **worked examples** the compiler must
reproduce exactly. The example SQL below *is* the definition of "correct" — codegen is
done when it produces these strings.

Dialect: MySQL 8. Dates are strings `'YYYY-MM-DD'`. Table names are case-sensitive
(`BIN_master`, not `bin_master`).

---

## 1. Grammar (EBNF)

```
query      := SHOW metric_list
              [ BY dimension_list ]
              [ WHERE condition_list ]
              [ PERIOD period_spec ]
              [ ORDER BY sort_key (ASC | DESC) ]
              [ LIMIT integer ]
              [ IN unit ]
              [ AS chart_type ]

metric_list    := metric (',' metric)*
dimension_list := dimension (',' dimension)*
condition_list := condition (AND condition)*
condition      := dimension '=' string_literal
sort_key       := metric | dimension
period_spec    := FTD | WTD | MTD | QTD | YTD | LAST integer DAYS
unit           := CRORE | LAKH
chart_type     := TABLE | KPI | BAR | LINE | PIE

metric     := <any key under metrics: in config.yaml>
dimension  := <any key/alias under dimensions: in config.yaml>
```

Only `SHOW <metric_list>` is required. Every other clause is optional. Clause order is
fixed as written above.

The parser produces an **AST** with two kinds of information:
- **what to compute** — `metrics`, `dimensions`, `filters`, `period`, `order`, `limit`, `unit`
- **how to present** — `chart_type` (from the `AS` clause, or `null` if absent)

Codegen consumes only the first kind. The `chart_type` field is carried on the AST but
**ignored by SQL generation** and read only by the response layer.

---

## 2. The 5 modifiers

| Modifier | Clause | Effect |
|---|---|---|
| filter | `WHERE dim = '...' [AND ...]` | adds `WHERE`, pulls in any join the dimension needs |
| period | `PERIOD MTD` etc. | adds a date-range filter, anchored to `reference_date` |
| top-N | `ORDER BY <key> DESC LIMIT n` | ranking |
| unit | `IN CRORE` / `IN LAKH` | divides each `unit_scalable` metric's value |
| chart | `AS PIE` etc. | overrides the inferred chart type (if compatible) |

---

## 3. Semantic rules codegen must apply

**Implicit success filter.** Metrics flagged `default_success_filter: true` in config
(`value`, `ats`, `spend_per_card`, `spend_per_customer`) are silently restricted to
`TD_BD = 'Success'` — because declined transactions moved no money. This adds a
`response_master` join and a `WHERE r.TD_BD='Success'`, and appends the assumption
*"Only successful transactions considered."*

**Suppression.** The implicit filter is **switched off** whenever the query references an
outcome dimension (`status` or `response`) in `BY` or `WHERE`. This is what makes
"value by status" (success vs declines) possible — otherwise it could only ever return
the success slice.

**Opt-in success for counts.** `volume`/`active_cards` have no default filter (they count
all attempts), but if the user explicitly asks for *successful* or *valid* transactions,
add the success filter anyway (and the same assumption).

**Mixed metrics.** When a query combines a success-defaulted metric with a
non-defaulted one (e.g. `volume, value`), do **not** apply the success filter in `WHERE`
(that would wrongly shrink `volume`). Apply it via conditional aggregation inside the
money metric only: `SUM(CASE WHEN r.TD_BD='Success' THEN t.amt ELSE 0 END)`. Assumption:
*"Value-based metrics count only successful transactions; volume counts all attempts."*

**Period anchoring.** `reference_date = MAX(card_txns.date)` (the data is historical, so
wall-clock "MTD" would be empty). Because `date` is a string column, codegen
**pre-computes** the period's boundary dates as string literals in Python and emits plain
string comparisons (`t.date >= '2025-12-01'`) rather than SQL date functions. Assumption
records the anchor date. *(Examples below assume `reference_date = '2025-12-27'`; your
real value is whatever `MAX(date)` is.)*

**Units.** `IN CRORE|LAKH` divides each `unit_scalable` metric by 1e7 / 1e5. Assumption
records it.

**Customer is PII-safe.** The `customer` dimension resolves to `t.customer_id` (a masked
id), never to name/phone/address. Those columns are blocked by guardrails.

**Chart type.** If `AS` is absent, infer from shape: a single-row result → TABLE (or KPI if it's one value), overriding the dimension rules; otherwise 0 dims → KPI, 1 time dim → LINE,
1 dim → BAR (PIE if ≤ 8 categories), 2+ dims or 2+ metrics → TABLE. If `AS` is present and
compatible with the shape it wins; otherwise fall back and record a `chart_fallback`
assumption.

**Guardrails.** SELECT-only; a `LIMIT` is forced (config `forced_limit`) when none is
given; queries that don't ground to known metrics/dimensions are rejected.

---

## 4. Assumptions

The response returns `{ result, dsl, sql, chart_type, assumptions[] }`. The
`assumptions[]` list is appended to whenever the compiler injects a default, converts a
unit, anchors a period, forces a limit, or falls back a chart. It renders in the audit
panel so every inference is visible. Templates live in `config.yaml → assumptions`.

---

## 5. Worked examples (the oracle)

Each example: natural language → DSL → SQL → resulting `chart_type` and `assumptions`.

---

**E1 — scalar, no join.** *"What's the total transaction value?"*
```
DSL:  SHOW value
```
```sql
SELECT SUM(t.amt) AS value
FROM card_txns t
JOIN response_master r ON t.response_code = r.response_code
WHERE r.TD_BD = 'Success';
```
chart_type: `KPI` (inferred) · assumptions: [success_default]

---

**E2 — single join, group by.** *"Total value by issuer."*
```
DSL:  SHOW value BY issuer
```
```sql
SELECT i.iss_name, SUM(t.amt) AS value
FROM card_txns t
JOIN issuer_master i ON t.issuer_id = i.id
JOIN response_master r ON t.response_code = r.response_code
WHERE r.TD_BD = 'Success'
GROUP BY i.iss_name;
```
chart_type: `BAR` (inferred; 3 categories → PIE also allowed) · assumptions: [success_default]

---

**E3 — two joins, a rate metric.** *"Success rate by issuer."*
```
DSL:  SHOW success_rate BY issuer
```
```sql
SELECT i.iss_name,
       SUM(CASE WHEN r.TD_BD = 'Success' THEN 1 ELSE 0 END) / NULLIF(COUNT(*),0) AS success_rate
FROM card_txns t
JOIN issuer_master i ON t.issuer_id = i.id
JOIN response_master r ON t.response_code = r.response_code
GROUP BY i.iss_name;
```
chart_type: `BAR` (inferred) · assumptions: [] *(rate metric has no success default)*

---

**E4 — month key + filter forcing a join.** *"Monthly transaction volume for credit cards."*
```
DSL:  SHOW volume BY month WHERE card_type = 'Credit'
```
```sql
SELECT SUBSTRING(t.date,1,7) AS month, COUNT(*) AS volume
FROM card_txns t
JOIN BIN_master b ON t.BIN = b.BIN
WHERE b.card_type = 'Credit'
GROUP BY SUBSTRING(t.date,1,7)
ORDER BY month ASC;
```
chart_type: `LINE` (inferred; time dimension) · assumptions: [] *(volume, no default)*

---

**E5 — top-N + PII-safe customer.** *"Which customer has the most business declines?"*
```
DSL:  SHOW volume BY customer WHERE status = 'Business Decline' ORDER BY volume DESC LIMIT 1
```
```sql
SELECT t.customer_id, COUNT(*) AS volume
FROM card_txns t
JOIN response_master r ON t.response_code = r.response_code
WHERE r.TD_BD = 'Business Decline'
GROUP BY t.customer_id
ORDER BY volume DESC
LIMIT 1;
```
chart_type: `TABLE` (single-row result → TABLE, not a lone bar) · assumptions: []
*Returns the masked `customer_id`, never the name — implicit success is suppressed because `status` is referenced.*

---

**E6 — two-dimension cross-tab.** *"Value by issuer and acquirer."*
```
DSL:  SHOW value BY issuer, acquirer
```
```sql
SELECT i.iss_name, a.acq_name, SUM(t.amt) AS value
FROM card_txns t
JOIN issuer_master i ON t.issuer_id = i.id
JOIN acquirer_master a ON t.acquirer_id = a.id
JOIN response_master r ON t.response_code = r.response_code
WHERE r.TD_BD = 'Success'
GROUP BY i.iss_name, a.acq_name;
```
chart_type: `TABLE` (inferred; 2 dimensions) · assumptions: [success_default]

---

**E7 — explicit chart override.** *"Show value by issuer as a pie chart."*
```
DSL:  SHOW value BY issuer AS PIE
```
```sql
SELECT i.iss_name, SUM(t.amt) AS value
FROM card_txns t
JOIN issuer_master i ON t.issuer_id = i.id
JOIN response_master r ON t.response_code = r.response_code
WHERE r.TD_BD = 'Success'
GROUP BY i.iss_name;
```
chart_type: `PIE` (explicit; compatible — 3 categories) · assumptions: [success_default]
*SQL is identical to E2: the `AS` clause never reaches codegen.*

---

**E8 — period + unit + implicit success.** *"Value by card type this month, in crore."*
```
DSL:  SHOW value BY card_type PERIOD MTD IN CRORE
```
```sql
SELECT b.card_type, SUM(t.amt) / 10000000 AS value
FROM card_txns t
JOIN BIN_master b ON t.BIN = b.BIN
JOIN response_master r ON t.response_code = r.response_code
WHERE r.TD_BD = 'Success'
  AND t.date >= '2025-12-01' AND t.date <= '2025-12-27'
GROUP BY b.card_type;
```
chart_type: `BAR` (inferred) · assumptions: [success_default, unit_crore, period_anchor]

---

**E9 — spend per card, filtered, MTD.** *"Spend per card for debit cards, month to date."*
```
DSL:  SHOW spend_per_card WHERE card_type = 'Debit' PERIOD MTD
```
```sql
SELECT SUM(t.amt) / NULLIF(COUNT(DISTINCT t.card_id),0) AS spend_per_card
FROM card_txns t
JOIN BIN_master b ON t.BIN = b.BIN
JOIN response_master r ON t.response_code = r.response_code
WHERE b.card_type = 'Debit'
  AND r.TD_BD = 'Success'
  AND t.date >= '2025-12-01' AND t.date <= '2025-12-27';
```
chart_type: `KPI` (inferred) · assumptions: [success_default, period_anchor]

---

**E10 — active-card rate, rolling window.** *"Active card rate over the last 90 days."*
```
DSL:  SHOW active_card_rate PERIOD LAST 90 DAYS
```
```sql
SELECT COUNT(DISTINCT t.card_id) / (SELECT COUNT(*) FROM card_master) AS active_card_rate
FROM card_txns t
WHERE t.date >= '2025-09-28' AND t.date <= '2025-12-27';
```
chart_type: `KPI` (inferred) · assumptions: [period_anchor, active_card_denom]

---

**E11 — value share, success vs declines (suppression in action).** *"Value split by success vs declines this month, in crore."*
```
DSL:  SHOW value BY status PERIOD MTD IN CRORE
```
```sql
SELECT r.TD_BD, SUM(t.amt) / 10000000 AS value
FROM card_txns t
JOIN response_master r ON t.response_code = r.response_code
WHERE t.date >= '2025-12-01' AND t.date <= '2025-12-27'
GROUP BY r.TD_BD;
```
chart_type: `BAR` (inferred; 3 categories known after the execution → PIE eligible) · assumptions: [unit_crore, period_anchor]
*No success assumption: `status` is an outcome dimension, so the implicit filter is suppressed and all three outcome classes are returned.*

---

**E12 — full modifier stack: top-N merchants.** *"Top 25 merchants by value this month, in crore."*
```
DSL:  SHOW value BY merchant PERIOD MTD ORDER BY value DESC LIMIT 25 IN CRORE
```
```sql
SELECT m.name, SUM(t.amt) / 10000000 AS value
FROM card_txns t
JOIN merchant_master m ON t.merchant_id = m.merchant_id
JOIN response_master r ON t.response_code = r.response_code
WHERE r.TD_BD = 'Success'
  AND t.date >= '2025-12-01' AND t.date <= '2025-12-27'
GROUP BY m.name
ORDER BY value DESC
LIMIT 25;
```
chart_type: `BAR` (inferred; >8 categories) · assumptions: [success_default, unit_crore, period_anchor]

---

**E13 — error distribution.** *"Error distribution this month."*
```
DSL:  SHOW volume BY status PERIOD MTD
```
```sql
SELECT r.TD_BD, COUNT(*) AS volume
FROM card_txns t
JOIN response_master r ON t.response_code = r.response_code
WHERE t.date >= '2025-12-01' AND t.date <= '2025-12-27'
GROUP BY r.TD_BD;
```
chart_type: `BAR` (inferred) · assumptions: [period_anchor]
*For code-level detail use `BY response`; add `WHERE status = 'Business Decline'` etc. to isolate a decline class.*

---

**E14 — multi-metric, mixed default filters.** *"Country-wise volume, value and ATS."*
```
DSL:  SHOW volume, value, ats BY country
```
```sql
SELECT t.country,
       COUNT(*) AS volume,
       SUM(CASE WHEN r.TD_BD = 'Success' THEN t.amt ELSE 0 END) AS value,
       SUM(CASE WHEN r.TD_BD = 'Success' THEN t.amt ELSE 0 END)
         / NULLIF(SUM(CASE WHEN r.TD_BD = 'Success' THEN 1 ELSE 0 END),0) AS ats
FROM card_txns t
JOIN response_master r ON t.response_code = r.response_code
GROUP BY t.country;
```
chart_type: `TABLE` (inferred; 3 metrics) · assumptions: [mixed_metrics]
*The success default becomes conditional aggregation so `value`/`ats` see only successful txns while `volume` still counts every attempt in the same query.*

---

## 6. Coverage check

Between E1–E14 the oracle exercises every mechanism at least once: no-join scalar, single
join, two joins, ≥3 joins, month key, all 5 modifiers (filter, period, top-N, unit,
chart), the implicit-success default **and** its suppression, opt-in vs no-default counts,
mixed-metric conditional aggregation, PII-safe customer, and every chart path (KPI, LINE,
BAR, PIE, TABLE) including a fallback-eligible override. Card-level (`spend_per_card`,
`active_card_rate`) and rate metrics are covered. New KPIs from the wider list are then
just new DSL strings over this same machinery — no compiler change.
