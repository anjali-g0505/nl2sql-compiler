"""FastAPI entrypoint. Step 2 scaffold: proves HTTP -> FastAPI -> MySQL -> JSON."""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.db import DatabaseError, execute_query

app = FastAPI(title="nl2sql-compiler", version="0.1.0")


@app.exception_handler(DatabaseError)
async def database_error_handler(request: Request, exc: DatabaseError) -> JSONResponse:
    # Details go to the server log (see app/db.py); the client gets a clean message.
    return JSONResponse(status_code=500, content={"error": "database_error", "detail": str(exc)})


@app.get("/health")
def health():
    return {"status": "ok"}


TEST_SQL = """\
SELECT SUM(t.amt) AS value
FROM card_txns t
JOIN response_master r ON t.response_code = r.response_code
WHERE r.TD_BD = 'Success';"""


@app.get("/test-query")
def test_query():
    rows = execute_query(TEST_SQL)
    return {"sql": TEST_SQL, "rows": rows}
