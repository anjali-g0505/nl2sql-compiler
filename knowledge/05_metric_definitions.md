---
doc_id: kb-05
title: Business Metric Definitions and the Implicit Success Rule
scope: business logic
topic: metrics
confidence: high (project-defined business logic)
source: project business logic
last_updated: 2026-09-23
---

# Metric definitions

## The implicit success rule
**Any metric described in revenue or throughput language counts only successful transactions**, even when the question never mentions success. "Revenue", "sales", "spend", "value", "volume", "throughput", "business done", "how much did we process" all carry an implied filter to approved transactions.

Declined transactions carry an amount, but no money moved. Summing amounts without filtering to success inflates every figure and is the single most common error in this domain.

Counter-examples where success must **not** be filtered:
- "attempted value / attempted volume" — all transactions regardless of outcome
- "decline rate", "success rate", "approval rate" — need both successes and declines in scope
- "declined value", "declined volume" — the declines are the subject
- "total transactions" — ambiguous; usually means attempts, so confirm rather than assume

## Core metrics
**Volume** — a *count* of transactions. Not a monetary amount. "High volume" means many transactions, not a large rupee figure. Successful volume counts approved transactions only.

**Value** — the *monetary sum* of transaction amounts, in rupees (₹). Successful value is the revenue figure; declined value is money attempted but not captured.

**ATS (Average Ticket Size)** — value divided by volume, for the same outcome class. Successful ATS = successful value / successful volume. It answers "how large is a typical transaction". Always guard against divide-by-zero: a group with zero successful transactions has an undefined ATS, not an ATS of zero.

**Success rate / approval rate** — successful volume divided by total attempted volume, expressed as a percentage.

**Decline rate** — declined volume divided by total attempted volume. The complement of the success rate. Can be computed on value instead of volume, and the two often differ sharply — see below.

**Technical decline rate / business decline rate** — the same ratio restricted to one `TD_BD` class. These sum to the overall decline rate.

## Volume-based vs value-based rates
A decline rate by *count* and a decline rate by *value* answer different questions:
- **By volume:** what share of attempts fail? A customer-experience measure.
- **By value:** what share of attempted money fails to land? A revenue-impact measure.

They diverge when failures concentrate in unusually large or small transactions. A 5% decline rate by volume that is 20% by value means the failures are clustered in high-ticket transactions — a much more expensive problem than the headline rate suggests. When a question asks about "impact" or "losses", prefer the value-based rate; when it asks about "reliability" or "experience", prefer the volume-based one.

## The exact metric names
The names a query may use, with their formulas and what each one counts, are in **kb-11, the metric catalogue**, which is generated from the compiler's own configuration. This document explains the ideas; that one is the list.

Two traps worth stating whenever a result is explained:
- **"Decline rate" unqualified means all declines.** `business_decline_rate` answers a narrower question, and the two can rank groups differently, because technical declines sit outside the narrower one. Say which was used.
- **The rates are fractions between 0 and 1.** A threshold written as `< 10` would match everything.

## Mixing outcome classes in one query
`volume` counts every attempt while `value` counts successful money, so a table holding both is mixing two populations on purpose. In that case ATS is successful value ÷ successful count, not value ÷ volume — dividing by the attempt count would understate the typical successful ticket. Any explanation of such a table should say which column counts what.

## Grouping dimensions
Metrics are normally requested per issuer, per acquirer, per merchant, per response code, or by time period — or by a combination. The finer the grouping, the smaller the denominators, which matters for interpretation (see the caveats document).
