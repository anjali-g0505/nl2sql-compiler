"""HTTP for the agent, added to the existing app without changing it.

    uvicorn agent.server:app --port 8001

Everything in app/main.py still works (/query, /ask, /clarifications/{id}, ...), and the
agent's routes sit beside them under /agent:

    POST /agent/ask        {"session_id", "message"}   -> an answer, or needs_clarification
    POST /agent/clarify    {"session_id", "answers"}   -> resumes the paused turn
    GET  /agent/state/{session_id}                     -> the checkpoint: route votes,
                                                          passages, plan, evidence

One LangGraph thread per session. Checkpoints live in SQLite (.agent_state/), so a
clarification can be answered after a restart, an hour later, or tomorrow. A new message
on a session replaces a pending clarification: the turn starts over from the router.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import BaseModel

from agent.budget import TokenBudget
from agent.graph import build_graph
from agent.nodes import Deps
from app.config import settings
from app.groq import GroqChat
from agent.recovery import retry_failed_sources
from app.main import app, index_registry, pipeline
from compiler.semantic_layer import REPO_ROOT

logger = logging.getLogger(__name__)

CHECKPOINTS = Path(os.getenv("AGENT_CHECKPOINTS", REPO_ROOT / ".agent_state" / "checkpoints.sqlite"))
CLASSIFIER_MODEL = os.getenv("GROQ_CLASSIFIER_MODEL", "openai/gpt-oss-20b")
TPM_LIMIT = int(os.getenv("GROQ_TPM_LIMIT", "8000"))


def _retriever():
    try:
        from rag.index import Retriever

        return Retriever()
    except Exception as exc:  # no index yet, or a stale one
        logger.warning("agent: documentation retrieval unavailable: %s", exc)
        return None


def build_deps() -> Deps:
    key = settings.groq_api_key
    # no fallback model on either: the 20b's quota belongs to the classifier, and the
    # budget, not a silent switch of model, is what handles a busy minute
    chat = GroqChat(api_key=key, model=settings.groq_model, fallback_model=None) if key else None
    classifier = GroqChat(api_key=key, model=CLASSIFIER_MODEL, fallback_model=None) if key else None
    return Deps(pipeline=pipeline, retriever=_retriever(), chat=chat, classifier=classifier,
                budget=TokenBudget(TPM_LIMIT), chat_model=settings.groq_model,
                classifier_model=CLASSIFIER_MODEL)


CHECKPOINTS.parent.mkdir(parents=True, exist_ok=True)
checkpointer = SqliteSaver(sqlite3.connect(CHECKPOINTS, check_same_thread=False))
agent = build_graph(build_deps(), checkpointer=checkpointer)


class AgentMessage(BaseModel):
    session_id: str
    message: str


class AgentAnswers(BaseModel):
    session_id: str
    answers: Dict[str, str]   # question id ("q1") -> an option id, or a typed value


def _config(session_id: str) -> Dict[str, Any]:
    return {"configurable": {"thread_id": session_id}}


def _pending(session_id: str):
    """The interrupt this session is paused on, or None."""
    snapshot = agent.get_state(_config(session_id))
    return snapshot.interrupts[0] if snapshot.interrupts else None


def _respond(session_id: str, result: Dict[str, Any]) -> JSONResponse:
    interrupts = result.get("__interrupt__")
    if interrupts:
        body = {"status": "needs_clarification", "session_id": session_id, **interrupts[0].value}
        return JSONResponse(status_code=200, content=jsonable_encoder(body))
    body = result.get("outcome") or {"status": "error", "stage": "agent", "errors": ["no outcome"]}
    return JSONResponse(status_code=result.get("http_status", 200),
                        content=jsonable_encoder({**body, "route": result.get("route")}))


router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/ask")
def ask(body: AgentMessage) -> JSONResponse:
    result = agent.invoke({"session_id": body.session_id, "message": body.message},
                          _config(body.session_id))
    return _respond(body.session_id, result)


@router.post("/clarify")
def clarify(body: AgentAnswers) -> JSONResponse:
    pending = _pending(body.session_id)
    if pending is None:
        return JSONResponse(status_code=409, content={
            "status": "error", "stage": "clarification",
            "errors": ["nothing is waiting for an answer on this session"]})
    missing = [q["id"] for q in pending.value["questions"] if not str(body.answers.get(q["id"], "")).strip()]
    if missing:  # stay paused, so the user can answer again
        return JSONResponse(status_code=422, content={
            "status": "error", "stage": "clarification",
            "errors": [f"no answer for {', '.join(missing)}"]})
    result = agent.invoke(Command(resume=body.answers), _config(body.session_id))
    return _respond(body.session_id, result)


@router.get("/state/{session_id}")
def state(session_id: str) -> JSONResponse:
    """The checkpoint, for debugging: what was decided, retrieved and run."""
    snapshot = agent.get_state(_config(session_id))
    return JSONResponse(content=jsonable_encoder({
        "values": snapshot.values,
        "next": list(snapshot.next),
        "pending": snapshot.interrupts[0].value if snapshot.interrupts else None,
    }))


app.include_router(router)

# app/main.py's startup builds the value indexes once. If MySQL isn't up yet (e.g. after
# Docker restarts the containers in the wrong order), keep retrying the failed ones in
# the background rather than running without them until the nightly refresh.
_main_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _lifespan_with_index_retry(application):
    async with _main_lifespan(application):
        retry = asyncio.create_task(retry_failed_sources(index_registry))
        try:
            yield
        finally:
            retry.cancel()


app.router.lifespan_context = _lifespan_with_index_retry
# app/main.py mounts the built frontend at "/" last; keep it last so it can't shadow /agent
app.router.routes.sort(key=lambda r: getattr(r, "name", "") == "frontend")
