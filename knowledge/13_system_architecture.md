---
doc_id: kb-13
title: System Architecture — how a question becomes an answer
scope: architecture
topic: pipeline
confidence: high (project definition)
source: project
last_updated: 2026-09-25
---

# Architecture

A question passes through seven stages. Each one can reject the query, and the stage that rejects it is named in the error, so a failure says where it happened.

```
question ─► translator ─► lexer ─► parser ─► validator ─► resolver ─► codegen ─► execution ─► answer
             (LLM)        tokens    AST       grounding    values      SQL         rows
```

## 1. Translator — the only place a model is used
Turns the question into one line of DSL. The prompt is assembled at startup from the semantic layer and the language specification, so it can never offer a metric the compiler does not have. The model's output is plain text: it is not a template, and nothing constrains it at generation time. It may return anything, including invalid DSL — which is why every later stage exists. If the question cannot be expressed in the vocabulary at all, the model says so and the system reports it as out of scope rather than answering something adjacent.

## 2. Lexer
Splits the DSL into tokens. Knows keywords, strings, numbers and punctuation, and nothing about meaning. Rejects malformed input, such as an unterminated string or an unsupported operator spelling.

## 3. Parser
Checks the shape of the query against the grammar and builds a syntax tree. It fails on the first problem, because after a syntax error the rest of the line is meaningless. It knows clause order and structure; it does not know whether `value` or `issuer` exist.

## 4. Validator — where the vocabulary becomes real
Grounds every name against the semantic layer: is this a metric, a dimension, an attribute; is it allowed in the clause it appears in; does the query select what it sorts by. It decides which joins the query needs, in a fixed order, and it re-checks the privacy rules rather than trusting configuration to be safe. Errors are collected rather than raised one at a time, so a single pass reports every problem.

## 5. Resolver — matching text to real values
A filter value is text the user or the model wrote; the database holds specific values. The resolver matches one to the other against indexes built from the database, and each value ends in one of five states: matched exactly, corrected to a near match, ambiguous between several, unknown, or passed through when no index applies. Ambiguity and unknowns stop the pipeline and become a question for the user; a correction is reported as an assumption.

## 6. Codegen — the only writer of SQL
Assembles SQL from fragments held in the semantic layer: the aggregate for each metric, the columns for each dimension, the join conditions, the date boundaries computed in advance. The same query always produces identical SQL. Text values leave as parameters rather than as literals, so a value can never change the structure of the statement.

## 7. Execution and presentation
The SQL runs read-only against the database. A row cap is applied when the query has no limit of its own, and the result is shown as a table or a chart chosen from its shape, alongside the assumptions, the DSL and the SQL.

## Supporting pieces
- **The semantic layer** is a configuration file holding measures, metrics, dimensions, joins, units, guardrails and assumption templates. It is read once at startup and checked for consistency, so a mistake in it stops the system rather than producing wrong SQL later. Adding a metric is a change here, not a change to the compiler.
- **Value indexes** are built from the database at startup and refreshed on a schedule, so the resolver matches against values that exist today.
- **The reference date** — "today" for relative periods — is the latest date in the data, cached with the indexes rather than looked up per query.

## The retry loop
Exactly one retry exists, and only for mistakes a model can fix: a syntax error, an unknown name, or a value outside a known list. The retry carries the exact errors, and the valid values where they are known. A second failure is reported. Ambiguity is never retried, because choosing between two real values is the user's decision, not a coin toss the model should make.
