# nl2sql-compiler
# project to use natural language to query the sql database

## Project layout

```
app/
  main.py        FastAPI app: /query, /clarifications/{id}, /admin/refresh-indexes, /health
  pipeline.py    The app contract: question/DSL -> ok | needs_clarification | error, one LLM retry
  db.py          MySQL connection pool + execute_query() (raw SQL, no ORM)
  config.py      Settings read from environment / .env
compiler/
  lexer.py       DSL string -> token list
  parser.py      Token list -> QueryAST (recursive descent, syntax only)
  semantic_layer.py  Loads + self-checks config.yaml; name/alias/join lookups
  validator.py   QueryAST -> ValidatedQuery (grounding, joins, guardrails)
  resolver.py    ValidatedQuery -> ResolvedQuery (filter values matched to real data)
  codegen.py     ResolvedQuery -> CompiledQuery (MySQL + assumptions, oracle-exact layout)
  indexes.py     DB-backed value indexes + cached reference date: build, atomic refresh, nightly schedule
aliases.yaml     Hand-maintained value aliases (SBI -> ISS001), merged into indexes
  ast.py         Frozen pipeline types: QueryAST -> ValidatedQuery -> CompiledQuery, Assumption
tests/
  test_lexer.py  pytest suite for the lexer
  test_ast.py    pytest suite for the AST types
  test_parser.py pytest suite for the parser
  test_validator.py  pytest suite for the validator + semantic layer
  test_resolver.py   pytest suite for value resolution
  test_indexes.py    pytest suite for the index registry (fake DB, no MySQL needed)
  test_codegen.py    pytest suite for codegen (E1-E19 read straight from grammar.md)
  test_pipeline.py   pytest suite for the app contract (scripted LLM, fake DB)
config.yaml      Semantic layer (measures, metrics, dimensions, joins) — source of truth
grammar.md       Frozen DSL spec + NL -> DSL -> SQL oracle
generate_data.py Deterministic synthetic data -> output/*.csv, output/*.sql
docker-compose.yml  MySQL 8, seeded from output/*.sql on first boot
```

## Running locally

Commands below are for PowerShell from the repo root. The API runs on the host and
talks to MySQL in Docker.

### 1. Start MySQL

```powershell
python generate_data.py        # only if output/*.sql is missing or you changed the generator
docker compose up -d
docker compose logs -f mysql   # wait for "ready for connections", then Ctrl+C
```

The container publishes MySQL on **host port 3307** (3306 is used by a local MySQL
install). The seed SQL only runs when the `mysql_data` volume is empty — after
regenerating data, run `docker compose down -v` first (this wipes the database).

### 2. Configure

```powershell
Copy-Item .env.example .env    # then set MYSQL_PASSWORD to match docker-compose.yml
```

Real environment variables override `.env`.

### 3. Install dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> **Python 3.9 on Windows with a space in the user path** (e.g. `C:\Users\First Last`):
> the venv's `python.exe` launcher fails with *"Unable to create process using …"*.
> Workaround — copy the real interpreter over the launcher, then install as above:
> ```powershell
> $base = Split-Path (Get-Command python).Source
> Copy-Item "$base\python.exe","$base\pythonw.exe","$base\python39.dll","$base\python3.dll","$base\vcruntime140.dll","$base\vcruntime140_1.dll" .venv\Scripts\ -Force
> ```
> Using Python 3.10+ avoids this.

### 4. Run the API

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

### 5. Check it

```powershell
curl.exe http://127.0.0.1:8000/health       # {"status":"ok"}
curl.exe http://127.0.0.1:8000/test-query   # {"sql": "...", "rows": [{"value": ...}]}
```

Interactive docs: http://127.0.0.1:8000/docs

If MySQL is unreachable or the query fails, `/test-query` returns HTTP 500 with
`{"error": "database_error", "detail": "..."}`; the full error is in the uvicorn log.

### 6. Ask a query

`POST /query` takes `{"question": "..."}` (translated to DSL by the LLM) or
`{"dsl": "..."}` (run as-is). Until the LLM stage is wired in, send DSL; a question
returns 503. Every response has a `status`:

| `status` | HTTP | Meaning |
|---|---|---|
| `ok` | 200 | `dsl`, `sql` (display form), `chart_type`, `assumptions` (`key` + `text`), `columns`, `rows`, `row_count`, `attempts` |
| `needs_clarification` | 200 | A value was ambiguous or isn't a known entity. Answer each of `questions` (`id`, `message`, `options`) via `POST /clarifications/{clarification_id}` with `{"answers": {"q1": "<option id or typed value>"}}`. Expires after 30 minutes; answerable once. |
| `error` | 422 / 404 / 502 / 503 | `stage` (`request`, `translate`, `parse`, `validate`, `resolve`, `clarification`, `compile`) and `errors` |

A question's DSL gets **one** retry: syntax errors, unknown names and unknown
enum/catalogue values go back to the LLM with the exact errors (and the valid values).
Ambiguous values and unknown entities are asked of the user instead, and answering
resumes the same query without another LLM call.

```powershell
curl.exe -X POST http://127.0.0.1:8000/query -H "content-type: application/json" -d '{\"dsl\": \"SHOW volume BY issuer WHERE status = ''decline''\"}'
```

## Running the tests

The tests are pure Python and need neither the database nor a running API, so steps
1, 2, 4 and 5 above are not required.

### Install dev dependencies (once)

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` pulls in `requirements.txt` plus pytest, so this also covers
step 3. Pytest is kept out of `requirements.txt` so production installs stay lean.

### Run

From the repo root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q                                  # everything
.\.venv\Scripts\python.exe -m pytest tests/test_lexer.py -v              # one file, per-test output
.\.venv\Scripts\python.exe -m pytest tests/test_lexer.py::test_e4_filter # one test
.\.venv\Scripts\python.exe -m pytest -k period -v                        # tests whose name matches
```

Use `python -m pytest` rather than bare `pytest`: running it as a module from the repo
root puts the root on `sys.path`, which is what lets the tests `import compiler.lexer`.
Bare `pytest` can fail with `ModuleNotFoundError: No module named 'app'`.

### What's covered

| File | Covers |
|---|---|
| `tests/test_lexer.py` | Token sequences for the `grammar.md` oracle strings (E1, E2, E4–E8, E10, E14–E19), comparison operators incl. `!=`, `IN`/`NOT IN` lists, integers vs exact decimals, `FROM`/`TO` date ranges, case handling, positions, whitespace, string literals, and `LexError` cases (unterminated strings, malformed numbers, unquoted dates, `<>`, unexpected characters) |
| `tests/test_ast.py` | Hand-built `QueryAST` shapes for representative DSL queries (including `HAVING` thresholds, numeric `WHERE` filters, `IN` lists, `!=` and date ranges), frozen-ness of every type, `Period` (incl. `RANGE`), `Condition` and `MetricCondition` validation, `Assumption` rendering, and `ValidatedQuery`/`CompiledQuery` composition |
| `tests/test_validator.py` | Join sets and success-filter decisions checked against the `grammar.md` oracle, alias/case resolution, kind mix-ups, `HAVING`/`ORDER BY`/unit/period/chart rules, contradictory filters, filtering by `month`, the PII guardrail, and `config.yaml` self-checks (incl. measure/metric shapes) |
| `tests/test_indexes.py` | Sources read from config, index building and one-source refresh, aliases merged in, a failed source keeping its previous index while others rebuild, snapshot semantics, refresh-age reporting, the nightly schedule, and the cached reference date (refreshed with the indexes, kept on failure, pinnable in config) |
| `tests/test_resolver.py` | Normalization, the five resolution outcomes (success, corrected, ambiguous, unknown, skipped), enum values from config, catalogue/entity resolution through a fake index, `IN` lists, and that numeric conditions and the rest of the query are untouched |
| `tests/test_parser.py` | `QueryAST` for the `grammar.md` oracle strings and an all-clauses query, optional `ORDER BY` direction, case handling, unresolved names, and `ParseError` cases (clause order and duplicates, malformed conditions and `IN` lists, bad dates, non-positive `LIMIT`/`LAST`, error positions) |
| `tests/test_codegen.py` | The SQL and assumption keys of every `grammar.md` oracle example (E1–E19, parsed from the file itself), identical SQL for the same query written two ways, no invented `LIMIT`, display SQL vs `%s` placeholders + params (incl. quotes in database values), the success condition pushed into every measure, unit scaling, money thresholds back in rupees, `month` filters, `ORDER BY` defaults and dimension ordering, multiple `HAVING` conditions, every period window, assumption values and rendering, and refusing unresolved values |
| `tests/test_pipeline.py` | Request shape, DSL end to end (display SQL vs executed SQL + params), the forced limit applied at execution (and its assumption only when rows were cut), one LLM retry with the exact errors and valid enum/catalogue values, no second retry, translator failures, DSL input never retried, ambiguous/unknown-entity clarifications, answering by option id or typed value, re-asking when still unclear, missing answers, one-time and expiring ids |
