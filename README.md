# nl2sql-compiler
# project to use natural language to query the sql database

## Project layout

```
app/
  main.py        FastAPI app: /health, /test-query
  db.py          MySQL connection pool + execute_query() (raw SQL, no ORM)
  config.py      Settings read from environment / .env
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
