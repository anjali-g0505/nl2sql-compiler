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
              [ HAVING having_list ]
              [ ORDER BY sort_key [ ASC | DESC ] ]      (default DESC)
              [ LIMIT integer ]
              [ IN unit ]
              [ AS chart_type ]

metric_list    := metric (',' metric)*
dimension_list := dimension (',' dimension)*
condition_list := condition (AND condition)*
condition      := dimension ('=' | '!=') string_literal
                | dimension [NOT] IN '(' string_literal (',' string_literal)* ')'
                | attribute comp_op number
having_list    := having_cond (AND having_cond)*
having_cond    := metric comp_op number
comp_op        := '=' | '!=' | '>' | '>=' | '<' | '<='
number         := integer | decimal          (decimal = digits '.' digits, e.g. 0.9)
sort_key       := metric | dimension
period_spec    := period_name                 (FTD | WTD | MTD | QTD | YTD)
                | LAST integer DAYS
                | FROM date_literal TO date_literal
date_literal   := string_literal in 'YYYY-MM-DD' form, a real calendar date

metric      := <any key under metrics: in config.yaml>
dimension   := <any key/alias under dimensions: in config.yaml>
attribute   := <any key/alias under attributes: in config.yaml>
period_name := <any key under modifiers.period.specs: in config.yaml>
unit        := <any key under modifiers.unit: in config.yaml>   (CRORE | LAKH)
chart_type  := <any type under modifiers.chart.types: in config.yaml>
                                              (TABLE | KPI | BAR | LINE | PIE)
```

Everything in that last block is **vocabulary, not syntax**: the lexer reads all of it as
plain identifiers, the parser only checks the shape, and the validator checks the values
against `config.yaml`. So a new chart type or period spec is a config change alone, and a
dimension may be named `table` or `line`. Only the structural keywords (`SHOW`, `BY`,
`WHERE`, `AND`, `NOT`, `PERIOD`, `HAVING`, `ORDER`, `LIMIT`, `IN`, `AS`, `ASC`, `DESC`,
`LAST`, `DAYS`, `FROM`, `TO`) are reserved and cannot be used as names.

The parser tells the `condition` forms apart by what follows the name (quoted text, a
parenthesised list, or a number), since it doesn't read config; the validator then checks
the name really is a dimension or an attribute respectively.

`IN` has two uses and the parser tells them apart by position: directly after a field in
`WHERE`, followed by `(`, it opens a list (`card_type IN ('Credit', 'Debit')`); as its own
clause near the end, followed by `CRORE`/`LAKH`, it is the unit.

Not-equal is written only as `!=`; `<>` is rejected by the lexer with a hint, so each
operator has exactly one spelling. Codegen emits the standard SQL `<>`. `OR`, `BETWEEN`,
`LIKE` and `IS [NOT] NULL` are deliberately not supported: `IN` covers the common
same-field `OR`; a numeric range is `amount >= x AND amount <= y` and a date range is
`PERIOD FROM … TO`; `LIKE` would invite guessed values; and every column is `NOT NULL`.

Only `SHOW <metric_list>` is required. Every other clause is optional. Clause order is
fixed as written above.

The parser produces an **AST** with two kinds of information:
- **what to compute** — `metrics`, `dimensions`, `filters`, `period`, `having`, `order`, `limit`, `unit`
- **how to present** — `chart_type` (from the `AS` clause, or `null` if absent)

Codegen consumes only the first kind. The `chart_type` field is carried on the AST but
**ignored by SQL generation** and read only by the response layer.

---

## 2. The 6 modifiers

| Modifier | Clause | Effect |
|---|---|---|
| filter | `WHERE dim = '...'` / `WHERE attr > n` `[AND ...]` | adds `WHERE` (row-level, before aggregation), pulls in any join the dimension needs |
| period | `PERIOD MTD` etc. / `PERIOD FROM '...' TO '...'` | adds a date-range filter: relative specs anchor to `reference_date`, `FROM … TO` is absolute |
| threshold | `HAVING metric > n [AND ...]` | adds `HAVING`: keeps only groups whose aggregated metric passes |
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

**Counts have no success default.** `volume`/`active_cards` count all attempts. For
*successful* or *valid* transactions the NL→DSL step writes the filter explicitly:
`WHERE status = 'Success'`. That references an outcome dimension, so no implicit filter
or `success_default` assumption is added: the condition is visible in the DSL itself.

**Mixed metrics.** When a query combines a success-defaulted metric with a
non-defaulted one (e.g. `volume, value`), do **not** apply the success filter in `WHERE`
(that would wrongly shrink `volume`). Apply it via conditional aggregation inside the
money metric only: `SUM(CASE WHEN r.TD_BD='Success' THEN t.amt ELSE 0 END)`. Assumption:
*"Value-based metrics count only successful transactions; volume counts all attempts."*
Metrics are built from config `measures` (`SUM(t.amt)`, `COUNT(*)`,
`COUNT(DISTINCT t.card_id)`, ...), and codegen pushes the condition into **every**
measure of the metric, so a ratio's denominator is filtered too: `ats` becomes success
value / success count (E14), and `spend_per_card` counts
`COUNT(DISTINCT CASE WHEN r.TD_BD='Success' THEN t.card_id END)`.

**Period anchoring.** `reference_date = MAX(card_txns.date)` (the data is historical, so
wall-clock "MTD" would be empty). Because `date` is a string column, codegen
**pre-computes** the period's boundary dates as string literals in Python and emits plain
string comparisons (`t.date >= '2025-12-01'`) rather than SQL date functions. Every
window includes both ends; `LAST n DAYS` covers exactly n days ending on the reference
date (start = reference_date − (n−1) days). The `period_anchor` assumption records the
anchor date. *(Examples below assume `reference_date = '2025-12-27'`; your real value is
whatever `MAX(date)` is.)*

**Window size is always stated.** "Last n days" and "from X to Y" are easy to read as off
by one, so the audit panel spells out the exact window:
- `LAST n DAYS` also emits `last_n_days_window`: *"Last 90 days = 2025-12-27 (treated as
  today) plus the 89 days before it: 2025-09-29 to 2025-12-27."*
- `FROM … TO` emits `date_range_inclusive`: *"Date range includes both ends: 2025-05-12
  to 2025-06-14, inclusive."*

**Explicit date ranges.** `PERIOD FROM '2025-05-12' TO '2025-06-14'` is absolute: it is
**not** anchored to `reference_date`, so it emits no `period_anchor` assumption (only
`date_range_inclusive`). Both ends are inclusive. Each date must be a real calendar date in strict `YYYY-MM-DD` form, and
start must not be after end (`'2025-02-30'`, `'12/05/2025'` and reversed ranges are
errors). The parser converts the strings to dates and codegen re-emits them with
`isoformat()`, so only a well-formed date literal can reach the SQL — a value like
`'2025-05-12 OR 1=1'` fails conversion instead of being injected. For the NL→DSL step:
*"in May 2025"* → `FROM '2025-05-01' TO '2025-05-31'`; a date without a year takes the
year of `reference_date`; relative phrases ("this month", "last 30 days") stay relative
specs (`MTD`, `LAST 30 DAYS`), because the LLM doesn't know `reference_date`.

**Units.** `IN CRORE|LAKH` divides each `unit_scalable` metric by 1e7 / 1e5. Assumption
records it.

**WHERE vs HAVING.** The split is *before vs after aggregation*:
- `WHERE` filters **rows**, by row-level fields: **dimensions** with `=` and quoted text
  (`card_type = 'Credit'`), and numeric **attributes** with any `comp_op`
  (`amount > 100000000`). Attributes (config `attributes:`) are per-transaction numbers:
  never grouped by, never aggregated.
- `HAVING` filters **groups**, by **aggregated metrics** after `GROUP BY`
  (`value > 30000` is a `SUM`).
- Never the reverse: a metric in `WHERE` is rejected with a hint to use `HAVING`, and a
  dimension or attribute in `HAVING` with a hint to use `WHERE`. *"Transactions over 10
  crore"* is `WHERE amount > …` (each row); *"customers whose total is over 10 crore"* is
  `HAVING value > …` (each group).
- Text dimensions allow `=`, `!=`, `IN (...)` and `NOT IN (...)`; attributes allow every
  `comp_op`. Attributes take numbers, dimensions take text (`amount > 'abc'`,
  `card_type = 5`, `card_type > 'Credit'` are rejected).
- `status != 'Success'` or `status NOT IN (...)` still references an outcome dimension, so
  the implicit success filter is suppressed — "show me the declines" works as expected.
- **Dates are filtered only by `PERIOD`**, never in `WHERE`, so two date filters can't
  contradict each other.

Further rules for `HAVING`, enforced by the validator:
- `HAVING` requires `BY` (with no dimensions there are no groups to filter).
- Each `HAVING` metric must also appear in `SHOW`, so codegen emits it by its alias
  (`HAVING value > 30000`, as MySQL allows) and the user sees the value being filtered.

**Money thresholds are in the query's display unit** — in `WHERE` and `HAVING` alike, so
the NL→DSL step has a single rule: every money number in a query is written in the `IN`
unit. *"More than 3 crore"* → `> 3 ... IN CRORE`; *"more than 30000000 rupees"* →
`> 30000000` with no `IN`; *"above 3 crore, shown in lakh"* → `> 300 ... IN LAKH`.
- In `HAVING` this falls out of the alias, which is already scaled.
- In `WHERE`, the `amt` column is always stored in rupees, so codegen multiplies the
  threshold by the divisor in Python (with `Decimal`, exactly) and emits the rupee value:
  `WHERE amount > 10 ... IN CRORE` → `t.amt > 100000000`.

**Customer is PII-safe.** The `customer` dimension resolves to `t.customer_id` (a masked
id), never to name/phone/address. Those columns are blocked by guardrails.

**Chart type.** If `AS` is absent, infer from shape, first match wins: 0 dims + 1 metric →
KPI; any other single-row result → TABLE, overriding the dimension rules; 2+ dims or
2+ metrics → TABLE; 1 time dim → LINE; 1 dim → BAR (PIE if ≤ 8 categories). If `AS` is present and
compatible with the shape it wins; otherwise fall back and record a `chart_fallback`
assumption.

**Guardrails.** SELECT-only; queries that don't ground to known
metrics/dimensions/attributes are rejected. When no `LIMIT` is given, the row cap
(config `forced_limit`) is applied **at execution**, not in the SQL: at most that many
rows are fetched, and the `forced_limit` assumption is added only if rows were actually
cut off. The SQL therefore shows exactly what was asked, which is why the examples below
have no `LIMIT` unless the DSL has one.

**SQL layout and conventions.** Codegen output is deterministic, so identical DSL gives
byte-identical SQL, and the examples below are matched character for character by the
tests:
- One clause per line: `SELECT`, `FROM`, one `JOIN` per table (config declaration
  order), `WHERE`, `GROUP BY`, `HAVING`, `ORDER BY`, `LIMIT`, then `;`.
- `WHERE`/`HAVING` put the first condition after the keyword and each further one on its
  own `  AND` line. `WHERE` order: the user's filters as written, then the success
  filter, then the period.
- `SELECT` stays on one line up to 100 characters. Longer, each item goes on its own
  line (indented under the first), and an item still over 100 is split once at its
  top-level ` / ` (E3, E14).
- A dimension whose select is an expression gets an alias (`SUBSTRING(t.date,1,7) AS
  month`); plain columns keep their own name (`i.iss_name`). Metrics are always aliased
  by their key, which is how `HAVING` and `ORDER BY` refer to them.
- `ORDER BY`: an explicit clause wins. Otherwise a time dimension sorts by its
  `default_order` (`ORDER BY month ASC`, E4). Ordering by a dimension uses its alias,
  or else each of its columns (`ORDER BY m.mcc_code DESC, m.mcc_description DESC`).
- Text values are written into the displayed SQL as quoted literals (as below). The
  SQL that actually runs has a `%s` placeholder for each, with the values passed
  separately, so a value (e.g. a merchant named `McDonald's`) can never change the
  query's structure. Numbers and dates are written in directly: the parser has already
  turned them into `Decimal`s and real dates.

---

## 4. Assumptions

The response returns `{ result, dsl, sql, chart_type, assumptions[] }`. The
`assumptions[]` list is appended to whenever the compiler injects a default, converts a
unit, anchors a period, forces a limit, or falls back a chart. It renders in the audit
panel so every inference is visible. Templates live in `config.yaml → assumptions`.

Each assumption is stored as its key plus the values its template needs (e.g.
`last_n_days_window` with `n`, `ref`, `n_minus_1`, `start`), so it can be re-rendered or
explained later. Who adds what:
- **codegen**, in this order: `success_default` or `mixed_metrics`, the unit,
  the period ones, metric definitions (`active_card_denom`), then one
  `value_corrected` per filter value the resolver corrected ("Interpreted Credt as
  Credit for card_type").
- **execution**: `forced_limit`, only when rows were cut off.
- **response layer**: `chart_fallback`, since chart choice needs the result's shape.

The assumption lists in the examples below are codegen's.

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
WHERE t.date >= '2025-09-29' AND t.date <= '2025-12-27';
```
chart_type: `KPI` (inferred) · assumptions: [period_anchor, last_n_days_window, active_card_denom]

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

**E15 — threshold on an aggregate.** *"Customers who made payments of more than 30000."*
```
DSL:  SHOW value BY customer HAVING value > 30000 ORDER BY value DESC
```
```sql
SELECT t.customer_id, SUM(t.amt) AS value
FROM card_txns t
JOIN response_master r ON t.response_code = r.response_code
WHERE r.TD_BD = 'Success'
GROUP BY t.customer_id
HAVING value > 30000
ORDER BY value DESC;
```
chart_type: `BAR` (inferred; >8 categories) · assumptions: [success_default]
*`value` is a metric, so the threshold goes in `HAVING`, after grouping — each customer's total, not each transaction.*

---

**E16 — threshold in the display unit.** *"Merchants with more than 3 crore this month, in crore."*
```
DSL:  SHOW value BY merchant PERIOD MTD HAVING value > 3 IN CRORE
```
```sql
SELECT m.name, SUM(t.amt) / 10000000 AS value
FROM card_txns t
JOIN merchant_master m ON t.merchant_id = m.merchant_id
JOIN response_master r ON t.response_code = r.response_code
WHERE r.TD_BD = 'Success'
  AND t.date >= '2025-12-01' AND t.date <= '2025-12-27'
GROUP BY m.name
HAVING value > 3;
```
chart_type: `BAR` (inferred) · assumptions: [success_default, unit_crore, period_anchor]
*`IN CRORE` scales the threshold too: the alias `value` is already in crore, so `> 3` means more than ₹3 crore.*

---

**E17 — explicit date range.** *"Total value from 12 May to 14 June 2025."*
```
DSL:  SHOW value PERIOD FROM '2025-05-12' TO '2025-06-14'
```
```sql
SELECT SUM(t.amt) AS value
FROM card_txns t
JOIN response_master r ON t.response_code = r.response_code
WHERE r.TD_BD = 'Success'
  AND t.date >= '2025-05-12' AND t.date <= '2025-06-14';
```
chart_type: `KPI` (inferred) · assumptions: [success_default, date_range_inclusive]
*No `period_anchor`: an explicit range is absolute. Both ends are included, and
`date_range_inclusive` says so.*

---

**E18 — row-level numeric filter.** *"List the transactions above 10 crore."*
```
DSL:  SHOW value BY txn WHERE amount > 100000000 AS TABLE
```
```sql
SELECT t.txn_id, SUM(t.amt) AS value
FROM card_txns t
JOIN response_master r ON t.response_code = r.response_code
WHERE t.amt > 100000000
  AND r.TD_BD = 'Success'
GROUP BY t.txn_id;
```
chart_type: `TABLE` (explicit; compatible) · assumptions: [success_default]
*`amount` is an attribute, so it goes in `WHERE` (each transaction), not `HAVING`. Grouping
by the primary key gives one row per transaction. Written in crore —
`SHOW value BY txn WHERE amount > 10 IN CRORE AS TABLE` — the `WHERE` line is identical
(codegen converts 10 crore back to rupees); only the displayed `value` becomes
`SUM(t.amt) / 10000000`, plus the `unit_crore` assumption.*

---

**E19 — list membership and not-equal.** *"Declined volume on credit and debit cards, by issuer."*
```
DSL:  SHOW volume BY issuer WHERE card_type IN ('Credit', 'Debit') AND status != 'Success'
```
```sql
SELECT i.iss_name, COUNT(*) AS volume
FROM card_txns t
JOIN issuer_master i ON t.issuer_id = i.id
JOIN BIN_master b ON t.BIN = b.BIN
JOIN response_master r ON t.response_code = r.response_code
WHERE b.card_type IN ('Credit', 'Debit')
  AND r.TD_BD <> 'Success'
GROUP BY i.iss_name;
```
chart_type: `BAR` (inferred) · assumptions: [] *(volume, no default; `status` referenced)*
*`!=` becomes SQL's `<>`. The negated form works the same way:
`WHERE card_type NOT IN ('Prepaid')` → `b.card_type NOT IN ('Prepaid')`.*

---

## 6. Coverage check

Between E1–E19 the oracle exercises every mechanism at least once: no-join scalar, single
join, two joins, ≥3 joins, month key, all 6 modifiers (filter, period, threshold, top-N,
unit, chart), `HAVING` thresholds in rupees and in the display unit, relative periods and an
explicit `FROM … TO` range, a numeric attribute in `WHERE`, `IN` lists and `!=`, the implicit-success default **and** its suppression, no-default counts,
mixed-metric conditional aggregation, PII-safe customer, and every chart path (KPI, LINE,
BAR, PIE, TABLE) including a fallback-eligible override. Card-level (`spend_per_card`,
`active_card_rate`) and rate metrics are covered. New KPIs from the wider list are then
just new DSL strings over this same machinery — no compiler change.
