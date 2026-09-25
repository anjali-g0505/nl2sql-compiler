---
doc_id: kb-08
title: Interpretation Playbook — Revenue, Ranking and Concentration
scope: reasoning playbook
topic: revenue analysis
confidence: high (analytical method)
source: project analytical conventions
last_updated: 2026-09-23
---

# Playbook: revenue, ranking and mix questions

Applies to questions of the form "where do we get the most revenue", "which merchants are biggest", "which issuer drives the most value", "how is our business distributed".

## Defaults for these questions
- **Success is implied.** Revenue means approved transactions only. See the implicit success rule.
- **Rank by value unless volume is explicitly requested.** "Biggest" and "most revenue" mean rupees; "busiest" and "most transactions" mean counts.
- **Descending order.** A "top" or "most" question sorts highest first. This is worth stating explicitly because ascending order is a common and silent error.
- **Limit the result.** "Top" implies a cutoff, typically ten, unless a number is given.

## Reading a ranking
A ranked list answers "who is largest", which is rarely the whole question. Add at least one of:
- **Concentration** — what share of total value do the top few account for? Heavy concentration is a dependency risk; a flat distribution is a diversified book.
- **Value against volume** — a merchant high in value but low in volume has a large average ticket, which is a different business from one processing many small transactions. Compare rankings by both and name entities that move between them.
- **ATS** — makes the value/volume contrast explicit in a single number.

## Value and volume can rank differently
Utility, travel and electronics merchants tend to have high ATS and rank higher by value than by volume. Grocery, pharmacy and fuel merchants tend to have low ATS and rank higher by volume than by value. When the two rankings disagree, that disagreement is usually the most interesting part of the answer.

## Geographic and segment questions
When the question asks about a location, region, or country, the same rules apply — successful value, grouped by the geography dimension, ranked descending. If no geography column exists in the schema, say so plainly rather than substituting a different dimension; acquirer and issuer are not proxies for where a transaction occurred.

## Combining revenue with quality
"Which merchant is biggest" and "which merchant is healthiest" are different questions. A merchant can rank first by successful value while having the worst decline rate — meaning it is large *despite* leaking revenue. When a revenue ranking is requested for a merchant or issuer that also appears in a decline analysis, noting the overlap is usually worth a sentence.

## Measuring concentration
Concentration is computed, never assumed. `SHOW value BY merchant ORDER BY value DESC` gives the ranking; the leader's share of the total and the share held by the top five are the two figures worth quoting. Interpret them as a dependency question: a leader holding a small share means the book is spread and the top merchant is just the largest, while a leader holding a large share means the business depends on it, and that is a risk statement, not a compliment.

## Volume and value rank differently
`SHOW volume BY merchant ORDER BY volume DESC` and `SHOW value BY merchant ORDER BY value DESC` routinely produce different leaders: many small transactions versus fewer large ones. Ranking by volume answers "where do customers transact most often", ranking by value answers "where does the money come from". Say which question was asked, and if the two rankings disagree, that disagreement is itself the finding — check ATS (`SHOW ats BY merchant`) to name the reason.

## Decline rate by value versus by volume
The same split applies to failure. `SHOW decline_rate BY <dimension>` is a count-based rate; the money-based equivalent is the declined value against the attempted value, which is what `SHOW decline_value, value BY <dimension>` supports. If the value-based rate is the lower of the two, failures are concentrated in smaller transactions and a count-based rate overstates the money at stake; if it is higher, failures are clustered in high-ticket transactions and the headline rate understates the damage. Quote the value-based figure whenever the question is about impact or losses.

## Acquirer-level rankings need care
Because a merchant belongs to one acquirer, an acquirer's numbers are the sum of its merchants and can be driven by one of them. Before attributing anything to an acquirer's platform, run `SHOW decline_rate BY acquirer, merchant` and see whether the pattern survives the drill-down.
