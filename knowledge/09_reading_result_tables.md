---
doc_id: kb-09
title: Reading Query Result Tables and Charts
scope: interpretation aid
topic: result interpretation
confidence: high (project conventions)
source: project output conventions
last_updated: 2026-09-23
---

# Reading a result set

Query results arrive as a table, whatever visual form the user sees. A bar chart, line chart or ranked list is the same underlying table. Interpretation should always be grounded in the returned rows, never in outside assumptions about the entities named.

## Column conventions
- **Value (₹)** — a monetary sum in rupees. Figures in the lakhs are normal for merchant-level aggregates.
- **Volume (transactions / txns)** — a count, not an amount.
- **ATS** — value divided by volume for the same outcome class.
- **Percentages** — usually a rate over attempted transactions. Check whether the denominator is volume or value before describing it; they are not interchangeable.
- **Split outcome columns** — result sets frequently carry parallel columns per outcome, e.g. successful value, business-declined value, technical-declined value, and matching volume and ATS columns. Each triplet describes a different outcome class for the same group, and they should be compared with each other rather than read in isolation.
- **Response code + description** — appear together; always use the description, not the bare code, in an explanation.

## Grouping keys
A result grouped by issuer, merchant and acquirer together describes **one routing path**, not an entity. A row reading issuer / merchant / acquirer is that issuer's cardholders paying that merchant through that acquirer. Conclusions about the issuer overall cannot be drawn from one such row — the same issuer appears across many rows, and its overall figure is a separate query.

## Row counts and truncation
The row count shown (for example, 185 rows or 60 rows) is the full result size; a display may show only the first several. When only part of a result is visible, an answer should describe what is visible and say that it reflects the top rows by the query's sort order, not the entire population.

## Sort order
Ranked outputs are only meaningful if the sort direction is known. If the ordering is ascending, the visible rows are the *smallest*, not the largest — a common and easily missed inversion. Confirm direction before describing anything as "top" or "highest".

## Ordering within a decline breakdown
A response-code breakdown sorted by value and one sorted by volume can look completely different. A rare code attached to high-ticket transactions can dominate by value while being negligible by count. Name which measure the ordering reflects.

## What a result set cannot tell you
- **Not causation.** A correlation between an acquirer and high declines does not establish that the acquirer caused them.
- **Not customers.** Counts are attempts; retries inflate them.
- **Not settlement.** These are authorization outcomes; approved does not mean settled or free of later chargebacks.
- **Not context outside the returned rows.** If a question needs a comparison period, a peer group, or a dimension the query did not return, the honest answer is that another query is needed.

## Conventions this system uses
How results arrive, so an explanation describes what the user is actually looking at.

- **Headers are business labels, not column names.** The issuer column is headed "Issuer", the merchant's name "Merchant", and a multi-column dimension carries its label plus the part it holds, such as "Merchant category (MCC) code" and "… description". Use those words in an answer.
- **An empty aggregate is displayed as 0.** A sum over no matching rows is NULL underneath. For a sum or a count, zero is the right reading; for an average or a rate it means *undefined*, and "0%" would be wrong. Check whether the group had any attempts before describing a zero — the attempted volume is a separate query away.
- **Units are display-only.** "in crore" or "in lakh" divides the displayed figure; the money is unchanged, and any threshold in the query is written in the same unit.
- **The row limit is display-only too.** The row-count control changes how many rows are drawn, never what the query returned or the SQL that ran.
- **Default ordering.** Month-based results sort chronologically unless the query says otherwise; everything else arrives in whatever order the database returned. **An unordered result is not a ranking** — never describe its first row as the largest.
- **Assumptions accompany every result** and state what the compiler inferred: that value counts only successful transactions, what "this month" resolved to, that a filter value was corrected to a known one. An explanation that contradicts an assumption is wrong about what was computed.

## Grain: how many rows to expect
The row count follows from the grouping, not from the size of the business: one row per combination that has data. Adding a dimension that is determined by one already present — a merchant's acquirer, for instance — adds columns but no rows, and a grouping that returns the same number of rows as before has added no information. Say what one row represents before interpreting the table.

## Mixed-outcome tables
When a table holds `volume` beside `value` and `ats`, volume counts every attempt while the money columns count successful ones only, and ATS is successful value divided by successful count. **ATS will therefore not equal value ÷ volume in that table**, and that is not an error: dividing money that only successes earned by a count that includes declines would understate the typical ticket. Say which population each column describes.

## What you are given when asked to explain a result
An explanation is never written from the knowledge base alone. When a user asks "what does this mean" or "explain this", the request carries the answer that is on screen:

- **the question** as it was asked, and **the DSL** it compiled to
- **the SQL** that ran
- **the rows themselves**, with their column names and business labels — the whole result when it is small, otherwise the head of it plus the row count
- **the assumptions** the compiler recorded
- **the unit**, and which columns are dimensions and which are metrics

That payload is the evidence. This document explains how to read it; kb-11 says what each metric means; kb-03, kb-04, kb-07 and kb-08 say what the pattern in it might indicate.

**Rules that follow from this:**
- **Every figure in an explanation must appear in the payload.** Do not recall a number from anywhere else, and do not compute a new one beyond simple arithmetic on the rows shown — a share, a difference, a ratio between two columns of the same row.
- **If only part of a large result is provided, say so** and describe it as the visible rows in the query's sort order.
- **A comparison that is not in the payload requires another query.** "Is this high?" cannot be answered from a single result; name the query that would answer it (the portfolio figure, the same metric for peers, or the same group by month) rather than guessing.
- **The assumptions bound the explanation.** If they say the figure counts only successful transactions, an explanation that treats it as all attempts is describing a computation that did not happen.
- **What is absent is an answer too.** A group missing from the rows had no data under the filters applied, which is different from having zero.
