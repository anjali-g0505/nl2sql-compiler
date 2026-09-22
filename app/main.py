"""FastAPI entrypoint.

POST /query takes a question (or DSL) and returns rows, a clarification to answer, or an
error; POST /clarifications/{id} answers a clarification. The rules for which is which
live in app/pipeline.py; this module only wires HTTP to it.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, Optional

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db import DatabaseError, execute_query
from app.pipeline import Outcome, Pipeline
from compiler.indexes import IndexRegistry, refresh_nightly

logger = logging.getLogger(__name__)

# Value-resolution indexes: built once at startup, refreshed nightly, and rebuildable
# by hand when new merchants are onboarded. A failed build is logged, not fatal —
# unresolvable dimensions simply pass their values through until the next refresh.
index_registry = IndexRegistry()

# translator=None until the LLM stage: questions get a 503, DSL works end to end.
pipeline = Pipeline(registry=index_registry, execute=execute_query, translator=None)


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


class QueryRequest(BaseModel):
    question: Optional[str] = None  # natural language, translated to DSL by the LLM
    dsl: Optional[str] = None       # or DSL directly (no LLM, no retry)


class ClarificationAnswers(BaseModel):
    answers: Dict[str, str]  # question id ("q1") -> an option id, or a typed value


def _respond(outcome: Outcome) -> JSONResponse:
    # jsonable_encoder: rows hold Decimals and dates straight from MySQL
    return JSONResponse(status_code=outcome.http_status, content=jsonable_encoder(outcome.body))


@app.post("/query")
def query(request: QueryRequest) -> JSONResponse:
    """status "ok" (rows), "needs_clarification" (answer via /clarifications/{id}), or "error"."""
    return _respond(pipeline.ask(question=request.question, dsl=request.dsl))


@app.post("/clarifications/{clarification_id}")
def answer_clarification(clarification_id: str, body: ClarificationAnswers) -> JSONResponse:
    """Resume a parked query with the user's answers. Same response shape as /query."""
    return _respond(pipeline.answer(clarification_id, body.answers))


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
