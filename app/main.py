"""FastAPI entrypoint for IntentQL — from business questions to financial insights.

POST /query takes a question (or DSL) and returns rows, a clarification to answer, or an
error; POST /clarifications/{id} answers a clarification. The rules for which is which
live in app/pipeline.py; this module only wires HTTP to it.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import settings
from app.db import DatabaseError, execute_query
from app.answerer import Answerer
from app.assistant import Assistant
from app.clarifications import MemoryStore, RedisStore
from app.groq import GroqChat
from app.pipeline import Outcome, Pipeline
from app.translator import GroqTranslator
from compiler.indexes import IndexRegistry, refresh_nightly

logger = logging.getLogger(__name__)

# Value-resolution indexes: built once at startup, refreshed nightly, and rebuildable
# by hand when new merchants are onboarded. A failed build is logged, not fatal —
# unresolvable dimensions simply pass their values through until the next refresh.
index_registry = IndexRegistry()

# NL -> DSL on Groq. Without GROQ_API_KEY questions get a 503; DSL still works end to end.
translator = (
    GroqTranslator(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        fallback_model=settings.groq_fallback_model or None,
        reference_date=lambda: index_registry.reference_date,
    )
    if settings.groq_api_key
    else None
)
# Parked clarifications outlive the request that created them, so with more than one
# worker they must live outside the process. Without REDIS_URL the app still works, but
# only with a single worker: another worker cannot see what this one parked.
clarifications = RedisStore.from_url(settings.redis_url) if settings.redis_url else MemoryStore()

pipeline = Pipeline(
    registry=index_registry,
    execute=execute_query,
    translator=translator,
    store=clarifications,
)


def _build_answerer():
    """Retrieval + a model, for documentation answers and explanations.

    Missing pieces are not fatal: without an API key or a built index the data path
    still works, and knowledge questions report that the assistant is unavailable.
    """
    if not settings.groq_api_key:
        return None
    try:
        from rag.index import Retriever

        return Answerer(
            retriever=Retriever(),
            chat=GroqChat(api_key=settings.groq_api_key, model=settings.groq_model,
                          fallback_model=settings.groq_fallback_model or None),
        )
    except Exception as exc:  # no index yet, or a stale one
        logger.warning("documentation answers unavailable: %s", exc)
        return None


assistant = Assistant(
    pipeline=pipeline,
    answerer=_build_answerer(),
    results=RedisStore.from_url(settings.redis_url) if settings.redis_url else MemoryStore(),
)

# The React app (frontend/), once built with `npm run build`, is served from /.
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


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


app = FastAPI(title="IntentQL", version="0.1.0", lifespan=lifespan)


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
    session_id: Optional[str] = None


class Message(BaseModel):
    message: str
    session_id: str          # the browser's own id; scopes "explain this" to one user


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
    if body.session_id:
        return _respond(assistant.answer_clarification(body.session_id, clarification_id, body.answers))
    return _respond(pipeline.answer(clarification_id, body.answers))


@app.post("/ask")
def ask(body: Message) -> JSONResponse:
    """One entry point for a chat message.

    Routes to the data path (rows), an explanation of the last result, or an answer from
    the documentation. Responses carry the same `status` values as /query, plus "answer"
    for prose, which additionally has `text`, `citations` and `grounded`.
    """
    return _respond(assistant.ask(body.session_id, body.message))


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


# Registered last so the API routes above take precedence over the static files.
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
