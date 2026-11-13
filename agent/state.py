"""The graph's state: one conversation (thread) per session.

Everything a node decides is written here, so a checkpoint shows the whole story of a
turn: both routing votes, the passages retrieved, the plan, every step's evidence.

Fields marked "per turn" are reset by the router at the start of each message;
`last_result` deliberately survives, so "explain this" has something to point at.
Values are plain JSON types (bodies pass through jsonable_encoder), so any
checkpointer can store them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class AgentState(TypedDict, total=False):
    # input
    session_id: str
    message: str

    # routing (per turn)
    explainable: bool                 # was the previous answer a data result? (gates EXPLAIN)
    route_votes: Dict[str, Any]       # {"regex": Vote, "classifier": Vote | None}, with confidences
    route_regex: str                  # the regex vote's route
    route_llm: Optional[str]          # the 20b classifier's route (None: unavailable)
    route: str                        # the decision: data | knowledge | explain | investigate
    route_reason: str                 # which vote won, what was ignored, which rule applied

    # retrieval (per turn): asdict(rag.index.Passage) + its citation
    passages: List[Dict[str, Any]]

    # data path (per turn)
    fallback_from: Optional[Dict[str, Any]]   # the out-of-scope error, if knowledge took over
    interrupts_used: int                      # clarifications asked this turn (max 2)

    # investigation (per turn)
    entities: List[Dict[str, str]]            # {"field", "value", "matched"}
    focus_metric: Optional[Dict[str, str]]    # {"metric", "label"} the question names
    plan: List[Dict[str, str]]                # {"purpose", "dsl"}, at most 5
    plan_note: Optional[str]                  # why the planner declined, if it did
    evidence: List[Dict[str, Any]]            # one entry per executed step
    stop_note: Optional[str]                  # why execution stopped early

    # the answer (per turn)
    outcome: Optional[Dict[str, Any]]         # the response body
    http_status: int

    # survives across turns
    last_result: Optional[Dict[str, Any]]     # the last successful data result


PER_TURN_RESET: Dict[str, Any] = {
    "route_llm": None,
    "route_reason": "",
    "passages": [],
    "fallback_from": None,
    "interrupts_used": 0,
    "entities": [],
    "focus_metric": None,
    "plan": [],
    "plan_note": None,
    "evidence": [],
    "stop_note": None,
    "outcome": None,
    "http_status": 200,
}
