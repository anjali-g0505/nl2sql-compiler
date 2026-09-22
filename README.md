# nl2sql-compiler
# project to use natural language to query the sql database

## Project layout

```
app/
  main.py        FastAPI app: /health, /test-query
  db.py          MySQL connection pool + execute_query() (raw SQL, no ORM)
  config.py      Settings read from environment / .env
compiler/
  lexer.py       DSL string -> token list
  parser.py      Token list -> QueryAST (recursive descent, syntax only)
  semantic_layer.py  Loads + self-checks config.yaml; name/alias/join lookups
  validator.py   QueryAST -> ValidatedQuery (grounding, joins, guardrails)
  resolver.py    ValidatedQuery -> ResolvedQuery (filter values matched to real data)
  indexes.py     DB-backed value indexes: build, atomic refresh, nightly schedule
aliases.yaml     Hand-maintained value aliases (SBI -> ISS001), merged into indexes
  ast.py         Frozen pipeline types: QueryAST -> ValidatedQuery -> CompiledQuery
tests/
  test_lexer.py  pytest suite for the lexer
  test_ast.py    pytest suite for the AST types
  test_parser.py pytest suite for the parser
  test_validator.py  pytest suite for the validator + semantic layer
  test_resolver.py   pytest suite for value resolution
  test_indexes.py    pytest suite for the index registry (fake DB, no MySQL needed)
config.yaml      Semantic layer (metrics, dimensions, joins) — source of truth
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
| `tests/test_ast.py` | Hand-built `QueryAST` shapes for representative DSL queries (including `HAVING` thresholds, numeric `WHERE` filters, `IN` lists, `!=` and date ranges), frozen-ness of every type, `Period` (incl. `RANGE`), `Condition` and `MetricCondition` validation, and `ValidatedQuery`/`CompiledQuery` composition |
| `tests/test_validator.py` | Join sets and success-filter decisions checked against the `grammar.md` oracle, alias/case resolution, kind mix-ups, `HAVING`/`ORDER BY`/unit/period/chart rules, contradictory filters, the PII guardrail, and `config.yaml` self-checks |
| `tests/test_indexes.py` | Sources read from config, index building and one-source refresh, aliases merged in, a failed source keeping its previous index while others rebuild, snapshot semantics, refresh-age reporting, and the nightly schedule |
| `tests/test_resolver.py` | Normalization, the five resolution outcomes (success, corrected, ambiguous, unknown, skipped), enum values from config, catalogue/entity resolution through a fake index, `IN` lists, and that numeric conditions and the rest of the query are untouched |
| `tests/test_parser.py` | `QueryAST` for the `grammar.md` oracle strings and an all-clauses query, optional `ORDER BY` direction, case handling, unresolved names, and `ParseError` cases (clause order and duplicates, malformed conditions and `IN` lists, bad dates, non-positive `LIMIT`/`LAST`, error positions) |
