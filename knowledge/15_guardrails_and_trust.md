---
doc_id: kb-15
title: Guardrails — what the system refuses, and what it always tells you
scope: guardrails
topic: trust
confidence: high (project definition)
source: project
last_updated: 2026-09-25
---

# Guardrails

These are the properties that make an answer checkable. Each is enforced somewhere specific, not merely intended.

## Read-only, by construction
The language has no statement that modifies data, the compiler emits only SELECT, and the pipeline refuses to run anything that does not begin with SELECT. A request to delete or change data fails at the language before it reaches any of that. The last line of defence belongs outside the application: the database account should hold SELECT rights only.

## Nothing is invented
A query is compiled only if every name in it exists in the semantic layer. An unknown metric or dimension is rejected with the closest known name as a suggestion, and the model gets one chance to correct itself. Nothing is silently dropped, and nothing is approximated: a question that cannot be grounded is reported as out of scope.

## Personal data is unreachable
Customers appear as masked identifiers. The table holding names, phone numbers and addresses is not joinable, and the compiler re-checks every column a query would touch against a blocked list rather than trusting configuration to be safe. "Who is this customer" has no answer here by design, and that is worth stating plainly when it is asked.

## Values are matched, never guessed
Filter values are matched against indexes built from the database. A value that matches exactly is used; a near match is corrected and the correction is reported; **a value matching several real ones stops and asks**; an unknown value is reported with the valid ones where the list is short enough to be useful. Very short values are never fuzzy-matched, so a two-letter word cannot silently become a country.

## Every inference is visible
Whenever the compiler supplies something the question did not, it says so beside the result:
- that a money metric counts only successful transactions
- what a relative period resolved to, and against which date
- that a displayed figure was rescaled to lakh or crore
- that a filter value was corrected
- that a chart type was changed because the result did not suit the one requested
- that the row cap was applied

An explanation that contradicts one of these is describing a different computation than the one that ran.

## Results are bounded
A query without a limit is capped when it runs, and the cap is reported rather than applied silently. The SQL shown is the query that was asked for, so the displayed statement and the displayed figures always correspond.

## The model's blast radius
The model writes one line of DSL and nothing else. It never sees the database, never writes SQL, never chooses a join, and never decides how a metric is computed. Its mistakes have three possible outcomes: a query that does not parse, a query that does not ground, or a query that is valid and means something slightly different from the question. The first two are caught mechanically; the third is why the DSL is shown to the user in plain sight, and why an answer should restate what was actually computed rather than only the number.

## What the system will not claim
- **It does not assert causes.** The data records outcomes, not reasons. An explanation may name where a difference sits — a code, a merchant, a month — and should stop there unless evidence for a cause is in the result.
- **It does not report people.** Counts are attempts, and retries are invisible, so a decline count is never a count of affected customers.
- **It does not compare against periods it was not given.** "Higher than usual" requires a second query, and an answer should say which comparison it used.
