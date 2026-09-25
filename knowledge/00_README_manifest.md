---
doc_id: kb-00
title: Corpus Manifest — EXCLUDE FROM EMBEDDING
scope: meta
topic: manifest
confidence: n/a
last_updated: 2026-09-23
---

# Knowledge base manifest

Fifteen documents forming the retrieval corpus for the natural-language analytics layer. **This manifest is metadata about the corpus and should be excluded from chunking and embedding** — it contains no domain knowledge and would pollute retrieval.

| Doc | File | Covers | Serves |
|---|---|---|---|
| kb-01 | 01_entity_glossary.md | Issuer, acquirer, merchant, network, four-party model | Knowledge-only |
| kb-02 | 02_transaction_lifecycle.md | Authorization flow, where response codes originate | Knowledge-only |
| kb-03 | 03_response_codes_reference.md | Response code table with meanings | Knowledge-only + explanation of results |
| kb-04 | 04_decline_taxonomy.md | Technical vs business, soft vs hard | Knowledge-only + explanation of results |
| kb-05 | 05_metric_definitions.md | Volume, value, ATS, rates, implicit success rule | Query generation + explanation |
| kb-06 | 06_schema_and_grain.md | Tables, joins, grain, what the data does not contain | Query generation + explanation |
| kb-07 | 07_playbook_decline_analysis.md | How to reason about a high decline rate | Explanation of results |
| kb-08 | 08_playbook_revenue_analysis.md | Ranking, concentration, value vs volume | Query generation + explanation |
| kb-09 | 09_reading_result_tables.md | Column conventions, sort order, truncation | Explanation of results |
| kb-10 | 10_analytical_caveats.md | Denominators, mix effects, retries, attribution | Explanation of results |
| kb-11 | 11_metric_catalogue.md | Every metric: formula, population, pitfalls — **generated from config.yaml** | Query generation + explanation |
| kb-12 | 12_intentql_product.md | What IntentQL is, who it is for, where it stops | Product questions |
| kb-13 | 13_system_architecture.md | The seven stages, the semantic layer, the retry loop | Product and method questions |
| kb-14 | 14_dsl_reference.md | The DSL: shape, rules, why it exists, what it cannot say | Query generation + product questions |
| kb-15 | 15_guardrails_and_trust.md | Read-only, PII, value resolution, stated assumptions | Product questions + framing every answer |

## Confidence field
Each document carries a `confidence` value in its frontmatter. Carry it into chunk metadata so a retrieved chunk's reliability travels with it.

kb-03 and kb-06 were verified against the live database on 2026-09-23 and are now `high`: kb-03 lists the thirteen codes `response_master` actually holds (with their share of attempts) and says plainly that no other code can appear; kb-06 matches the live column list, including `BIN_master`, `card_master` and the deliberately unreachable `customer_master`. kb-04 remains `MEDIUM` on its TD/BD mapping, which `response_master` settles per code.

## No figures in the corpus
**No document states a number taken from the data** — no counts, no shares, no rates, no rankings. Those change as the data grows, and a frozen figure in a retrieved chunk would be asserted with total confidence long after it stopped being true.

Where a comparison is needed, the documents give the **query to run** instead of the answer: the portfolio rate, a peer group, a month-by-month range. The numbers in an answer should come from the compiler on the day the question is asked, and a retrieved passage should only ever explain what they mean.

Figures that are *definitions* rather than measurements — a formula, a range of 0 to 1, the fact that a rate is a fraction — are not data and are fine.

## Generated documents
kb-11 is produced by `scripts/build_metric_catalogue.py` from `config.yaml`, so metric names, formulas and ranges cannot drift from what the compiler accepts. Regenerate it whenever metrics change; do not edit it by hand. It reads configuration only, never the database.

## Suggested chunk metadata
`doc_id`, `topic`, `scope`, `confidence`, and the nearest heading. `scope` is useful as a retrieval filter: `definitions` and `reference` for lookup-style questions, `reasoning playbook` and `guardrails` for explanation-style questions, `business logic` and `schema` for query-generation prompts.

## Note on the metric and schema documents
kb-05 and kb-06 overlap with the compiler's own configuration. They exist here so the explanation model can describe *why* a figure was computed a certain way, which the configuration alone does not express. Keep them consistent — if a metric definition changes in configuration, change it here too.
