---
doc_id: kb-12
title: What IntentQL Is
scope: product
topic: product overview
confidence: high (project definition)
source: project
last_updated: 2026-09-25
---

# IntentQL

**From business questions to financial insights.** IntentQL answers questions about card-payment data asked in ordinary English, and shows its working: the query it wrote, the SQL it ran, and every assumption it made on the way.

## The problem it addresses
Analysts who live in payments data spend their time on a narrow set of recurring questions — value by issuer, declines by merchant, this month against last — and each one costs a request to someone who writes SQL, or a dashboard that answers only what it was built to answer. Tools that let a language model write SQL directly solve the speed problem and create a worse one: nobody can tell whether the number is right, and a model that quietly invents a column or drops a filter produces a plausible figure that is simply wrong.

IntentQL takes the position that **the model should not write SQL at all**.

## How it answers
A question is translated into a small, purpose-built query language — the DSL — and a compiler turns that into SQL. The model chooses *what to ask*; the compiler decides *how to compute it*. Because the DSL can only name metrics, dimensions and filters that exist in the semantic layer, a query that cannot be grounded is rejected instead of guessed, and the SQL is assembled from fragments that were written once and reviewed, not composed afresh for each question.

The result is an answer with a chain of custody: question → DSL → SQL → rows, all of it visible.

## Who it is for
Payments analysts, operations teams and the people they report to. The audience is assumed to be sceptical of "AI writes your SQL", and the product is built for that scepticism: every answer can be checked, and the parts that were inferred rather than asked for are stated rather than hidden.

## What it is not
- **Not a chatbot that browses a database.** It answers within a defined vocabulary and refuses outside it.
- **Not a dashboard.** There are no saved tiles; every answer is computed for the question asked.
- **Not a writer.** It reads. The database connection is read-only by construction, and the system refuses anything that is not a SELECT.
- **Not a forecaster.** It reports what the data holds. It does not predict, and it does not assert causes the data cannot evidence.

## What makes an answer trustworthy here
1. **The error surface is small.** A model writing SQL can go wrong in as many ways as SQL has features: a missed filter, a wrong join, a silently different grain, a column that does not exist. A model writing DSL has one line and a closed vocabulary to go wrong in, and only two ways to do it — a query that does not compile, which is caught mechanically, or a query that compiles and means something slightly different from the question, which is visible on the face of it. Narrowing what the model can say is what makes the rest of these guarantees possible.
2. **The DSL is readable without SQL.** `SHOW value BY issuer PERIOD MTD IN CRORE` can be checked by someone who knows the business and has never written a query. That matters more than it sounds: the person who can tell whether the question was understood correctly is usually not the person who can read a fifteen-line SQL statement with three joins and a conditional aggregate. Verification stops being an engineering task.
3. **The same DSL always produces the same SQL.** Compilation is deterministic — join order, clause order and formatting are fixed — so a query reviewed once stays reviewed, two people asking the same question get the same statement, and a change in output means a change in the question or the configuration, never a reroll of the model.
4. **Correctness reduces to one line.** The SQL is assembled from fragments defined once in the semantic layer and tested against a frozen set of worked examples, so **if the DSL faithfully represents the question, the SQL is right**. Checking an answer therefore means checking that one readable line, not auditing generated SQL.
5. **The vocabulary is fixed.** Metrics and dimensions come from a semantic layer, so "value" means the same thing in every answer.
6. **Inferences are stated.** Defaults the compiler applied — counting only successful transactions, resolving "this month", correcting a filter value — appear beside the result as assumptions.
7. **Ambiguity is a question, not a guess.** A value that matches several real ones stops and asks rather than picking.
8. **The SQL is on display.** Anyone who can read SQL can audit the answer without asking anyone.

## Where it stops
IntentQL reports; it does not explain causes on its own. It can say that declines are concentrated in one code, one merchant or one month, because that is in the data. It cannot say *why* an issuer's behaviour changed, because the data holds no record of outages, policy changes or incidents. Answers should stop at the edge of the evidence and say where that edge is.
