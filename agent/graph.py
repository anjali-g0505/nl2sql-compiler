"""The wiring: which node follows which, and on what condition.

    START → router ─┬─ data ──────────┬─ ok ─────────────────→ remember → END
                    │                 ├─ needs_clarification → clarify ─┬─ ok → remember
                    │                 │                                 ├─ again → clarify
                    │                 │                                 └─ else → END
                    │                 ├─ out of scope ───────→ retrieve (knowledge)
                    │                 └─ error ──────────────→ END
                    ├─ knowledge ─────→ retrieve → generate → END
                    ├─ explain ───────→ retrieve → explain → END
                    └─ investigate ───→ find_entities → retrieve → plan
                                        → execute_step ↺ (until the plan is done, at most
                                          5, or step 1 shows no outlier) → synthesize → END

Loops are bounded in code, not by the model: clarify by MAX_INTERRUPTS (2), execute_step
by the plan length (cut to 5 in parse_plan). A node that sets an error outcome ends the
turn.
"""
from __future__ import annotations

from typing import Any, Optional

from langgraph.graph import END, START, StateGraph

from agent.nodes import Deps, Nodes
from agent.routing import DATA, EXPLAIN, INVESTIGATE, KNOWLEDGE
from agent.state import AgentState


def _status(state: AgentState) -> Optional[str]:
    return (state.get("outcome") or {}).get("status")


def after_router(state: AgentState) -> str:
    return {DATA: "data", INVESTIGATE: "find_entities"}.get(state["route"], "retrieve")


def after_data(state: AgentState) -> str:
    status = _status(state)
    if status == "ok":
        return "remember"
    if status == "needs_clarification":
        return "clarify"
    if state.get("fallback_from"):
        return "retrieve"
    return END


def after_clarify(state: AgentState) -> str:
    status = _status(state)
    if status == "ok":
        return "remember"
    if status == "needs_clarification":
        return "clarify"
    return END


def after_retrieve(state: AgentState) -> str:
    if state.get("outcome") and not state.get("fallback_from"):
        return END  # retrieve refused (no model, no index, nothing to explain)
    return {KNOWLEDGE: "generate", EXPLAIN: "explain", INVESTIGATE: "plan"}[state["route"]]


def after_plan(state: AgentState) -> str:
    return "execute_step" if state.get("plan") else END


def after_step(state: AgentState) -> str:
    done = len(state.get("evidence") or []) >= len(state.get("plan") or [])
    return "synthesize" if done or state.get("stop_note") else "execute_step"


def build_graph(deps: Deps, checkpointer: Any = None):
    """The compiled graph. Pass a checkpointer for clarifications and memory across turns."""
    nodes = Nodes(deps)
    graph = StateGraph(AgentState)
    for name in ("router", "data", "clarify", "remember", "retrieve", "generate", "explain",
                 "find_entities", "plan", "execute_step", "synthesize"):
        graph.add_node(name, getattr(nodes, name))

    graph.add_edge(START, "router")
    graph.add_conditional_edges("router", after_router, ["data", "find_entities", "retrieve"])
    graph.add_conditional_edges("data", after_data, ["remember", "clarify", "retrieve", END])
    graph.add_conditional_edges("clarify", after_clarify, ["remember", "clarify", END])
    graph.add_edge("remember", END)
    graph.add_edge("find_entities", "retrieve")
    graph.add_conditional_edges("retrieve", after_retrieve, ["generate", "explain", "plan", END])
    graph.add_edge("generate", END)
    graph.add_edge("explain", END)
    graph.add_conditional_edges("plan", after_plan, ["execute_step", END])
    graph.add_conditional_edges("execute_step", after_step, ["execute_step", "synthesize"])
    graph.add_edge("synthesize", END)
    return graph.compile(checkpointer=checkpointer)
