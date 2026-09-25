---
doc_id: kb-14
title: The DSL — a purpose-built query language
scope: reference
topic: dsl
confidence: high (project definition)
source: project grammar specification
last_updated: 2026-09-25
---

# The DSL

## What it is
A small query language designed for this system. It is not SQL, not a standard, and not borrowed from anywhere: it exists so that a language model has something narrow to write and a compiler has something safe to read. One line expresses one question.

```
SHOW value BY issuer PERIOD MTD IN CRORE
```

Its whole purpose is what it **cannot** say. There is no way to write a join, a subquery, a table name, a function call, an `OR`, a `LIKE`, or anything that modifies data. A query either names things the semantic layer knows or it is rejected. That is what makes a model's output safe to compile: the worst a bad answer can do is fail to parse.

## Shape of a query
Clauses appear in a fixed order, and only `SHOW` is required.

```
SHOW <metrics>
  [ BY <dimensions> ]
  [ WHERE <conditions> ]
  [ PERIOD <period> ]
  [ HAVING <metric condition> ]
  [ ORDER BY <key> [ASC|DESC] ]
  [ LIMIT <n> ]
  [ IN <unit> ]
  [ AS <chart> ]
```

- **SHOW** — one or more metrics. The catalogue of metrics is kb-11.
- **BY** — the grouping. Each dimension adds a column and splits the rows.
- **WHERE** — row-level filters, before aggregation: a dimension compared with quoted text (`=`, `!=`, `IN`, `NOT IN`), or a numeric attribute compared with a number.
- **PERIOD** — the date window: a named period such as month-to-date or year-to-date, a rolling window of days or calendar months, or an explicit range between two dates. Dates are filtered *only* here, so two date filters can never contradict each other.
- **HAVING** — a threshold on an aggregated metric, after grouping. Requires `BY`, and the metric must also be in `SHOW`.
- **ORDER BY / LIMIT** — ranking. The sort key must be something the query selected.
- **IN** — display unit for money metrics; it rescales what is shown, not what is computed.
- **AS** — a chart preference, which never reaches the SQL.

## Rules worth knowing when reading a query
- **Names come from the semantic layer.** Metrics, dimensions and attributes are whatever configuration defines, which is why a new KPI is a configuration change and not a language change.
- **WHERE and HAVING are not interchangeable.** WHERE filters transactions; HAVING filters groups. "Transactions over X" is a WHERE; "customers whose total is over X" is a HAVING.
- **Rates are fractions.** A threshold of ten percent is written `0.1`.
- **Money thresholds follow the display unit.** With `IN CRORE`, a threshold of `3` means three crore, and the compiler converts it back to rupees for the comparison.
- **Not-equal is `!=`.** The compiler emits the SQL spelling itself.

## Why a DSL rather than SQL
- **It can be validated.** Every name in it is checked against configuration before anything runs, which is not practical for arbitrary SQL.
- **It is small enough to teach in a prompt.** The whole language and vocabulary fit in about two thousand tokens, so the model sees the complete specification on every request.
- **It is reviewable.** A one-line query is something a user can read and confirm; a fifteen-line SQL statement usually is not.
- **It decouples the question from the schema.** The same DSL keeps working if a join changes or a metric's definition is corrected, because those live in configuration.

## Its limits
The DSL deliberately cannot express: comparisons between two periods in one query, window functions or running totals, arbitrary arithmetic between metrics, `OR` across different fields, or anything about tables it has no dimension for. A question needing one of those is out of scope, and the honest answer says so rather than approximating it.
