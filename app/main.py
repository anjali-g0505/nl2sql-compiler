"""FastAPI entrypoint. Step 2 scaffold: proves HTTP -> FastAPI -> MySQL -> JSON."""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.db import DatabaseError, execute_query
from compiler.indexes import IndexRegistry, refresh_nightly

logger = logging.getLogger(__name__)

# Value-resolution indexes: built once at startup, refreshed nightly, and rebuildable
# by hand when new merchants are onboarded. A failed build is logged, not fatal —
# unresolvable dimensions simply pass their values through until the next refresh.
index_registry = IndexRegistry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    report = await asyncio.to_thread(index_registry.refresh)
    logger.info(
        "Value indexes built: %s",
        ", ".join(f"{s.name}={s.rows}" for s in report.statuses) or "none",
    )
    nightly = asyncio.create_task(refresh_nightly(index_registry))
    try:
        yield
    finally:
        nightly.cancel()


app = FastAPI(title="nl2sql-compiler", version="0.1.0", lifespan=lifespan)


@app.post("/admin/refresh-indexes")
async def refresh_indexes(source: Optional[str] = None):
    """Rebuild the value indexes now (all of them, or one named source)."""
    report = await asyncio.to_thread(index_registry.refresh, source)
    return {
        "ok": report.ok,
        "sources": [
            {
                "name": s.name,
                "kind": s.kind,
                "rows": s.rows,
                "last_refreshed": s.last_refreshed.isoformat() if s.last_refreshed else None,
                "error": s.error,
            }
            for s in report.statuses
        ],
    }


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
