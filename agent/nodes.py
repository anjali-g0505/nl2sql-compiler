"""The graph's nodes. Each takes the state and returns the fields it changes.

Tools the nodes use, none of them modified for the agent:
    pipeline.ask(question=...)   the data path: NL -> DSL (120b, one retry) -> SQL -> rows
    pipeline.ask(dsl=...)        an investigation step: no model call, 0 tokens
    pipeline.answer(id, answers) resume a clarification
    retriever.search(q, k, scope) hybrid retrieval over knowledge/
    chat.complete(messages)      gpt-oss-120b, for prose and plans
    classifier.complete(...)     gpt-oss-20b, for routing

Every model call asks the token budget first. A call that would go over this minute's
limit is not made; the user gets a 429 with how long to wait, instead of a 429 from
Groq halfway through a turn.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from fastapi.encoders import jsonable_encoder
from langgraph.types import interrupt

from agent import investigate as inv
from agent.budget import TokenBudget, estimate
from agent.routing import (CLASSIFIER_MAX_TOKENS, DATA, EXPLAIN, KNOWLEDGE, classifier_messages,
                           decide, parse_vote, regex_vote)
from agent.state import PER_TURN_RESET, AgentState
from app.answerer import (EXPLAIN_SCOPES, EXPLAIN_SYSTEM, KNOWLEDGE_SYSTEM, MAX_PASSAGES,
                          NOT_IN_DOCS, render_passages, render_result)
from app.groq import ModelError, messages
from app.pipeline import Outcome, Pipeline
from app.translator import build_system_prompt
from rag.index import Passage

MAX_INTERRUPTS = 2
TRANSLATE_COST = 2300        # one NL -> DSL call: ~1.9K prompt + question + reply
ANSWER_OUTPUT_ESTIMATE = 600
ANSWER_MAX_TOKENS = 700
COULD_NOT_ANSWER = "This query could not be answered."

# Documentation sections written as instructions to the explain step, not as answers
# for a user: retrieved for a knowledge question they read back the explain prompt's
# contract ("every figure must appear in the payload ..."). They stay available to the
# explain branch. tests/test_agent.py checks each one still exists in knowledge/, so a
# renamed heading fails a test instead of silently letting the section back in.
EXPLAIN_ONLY_SECTIONS = frozenset({
    "kb-09 > What you are given when asked to explain a result",
})


@dataclass
class Deps:
    """What the nodes need. Anything optional may be None; the nodes degrade."""

    pipeline: Pipeline
    retriever: Any = None            # rag.index.Retriever
    chat: Any = None                 # app.groq.GroqChat on the 120b
    classifier: Any = None           # app.groq.GroqChat on the 20b
    budget: TokenBudget = field(default_factory=TokenBudget)
    chat_model: str = "openai/gpt-oss-120b"
    classifier_model: str = "openai/gpt-oss-20b"


def _body(outcome: Outcome) -> Dict[str, Any]:
    """A pipeline outcome as plain JSON types (Decimals and dates from MySQL)."""
    return jsonable_encoder(outcome.body)


def _out(http_status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"outcome": body, "http_status": http_status}


def _error(http_status: int, stage: str, errors: Sequence[str], **extra) -> Dict[str, Any]:
    return _out(http_status, {"status": "error", "stage": stage, "errors": list(errors), **extra})


def _passages(state: AgentState) -> List[Passage]:
    keep = Passage.__dataclass_fields__
    return [Passage(**{k: v for k, v in p.items() if k in keep}) for p in state.get("passages") or []]


class Nodes:
    def __init__(self, deps: Deps):
        self.d = deps

    # --- budget ---------------------------------------------------------------

    def _over_budget(self, model: str, tokens: int) -> Optional[Dict[str, Any]]:
        """None if the call may go ahead (and is recorded); else the 429 to return."""
        if self.d.budget.try_spend(model, tokens):
            return None
        wait = self.d.budget.retry_after(model, tokens)
        return _error(429, "budget", [
            f"The model's token budget for this minute is used up; try again in {wait} seconds."
        ], retry_after=wait)

    def _complete(self, model: str, prompt_messages, max_tokens: int, output_estimate: int,
                  chat=None) -> str:
        """Budget check, then the call. Raises _Busy or ModelError."""
        over = self._over_budget(model, estimate(*(m["content"] for m in prompt_messages)) + output_estimate)
        if over:
            raise _Busy(over)
        return (chat or self.d.chat).complete(prompt_messages, max_tokens=max_tokens)

    # --- router ---------------------------------------------------------------

    def router(self, state: AgentState) -> Dict[str, Any]:
        """Two votes and the rules (agent/routing.py), then a clean slate for the turn.

        Runs before the per-turn reset, so `route` and `outcome` still hold the previous
        turn's, as the checkpointer saved them: that is how "the previous answer was a
        data result" is known.
        """
        message = state["message"]
        previous = state.get("outcome") or {}
        explainable = state.get("route") == DATA and previous.get("status") == "ok"
        regex = regex_vote(message, explainable)

        llm = None
        if self.d.classifier is not None:
            try:
                reply = self._complete(self.d.classifier_model, classifier_messages(message, explainable),
                                       CLASSIFIER_MAX_TOKENS, CLASSIFIER_MAX_TOKENS // 2,
                                       chat=self.d.classifier)
                llm = parse_vote(reply)
            except (_Busy, ModelError):
                llm = None  # routing never blocks a message: the regex vote stands
        route, reason = decide(regex, llm, message, explainable)
        return {**PER_TURN_RESET, "explainable": explainable,
                "route_votes": {"regex": regex.as_dict(), "classifier": llm.as_dict() if llm else None},
                "route_regex": regex.route, "route_llm": llm.route if llm else None,
                "route": route, "route_reason": reason}

    # --- data -----------------------------------------------------------------

    def data(self, state: AgentState) -> Dict[str, Any]:
        """The compiler pipeline, as is. Rows and chart go straight to the user."""
        if self.d.pipeline.translator is not None:
            over = self._over_budget(self.d.chat_model, TRANSLATE_COST)
            if over:
                return over
        outcome = self.d.pipeline.ask(question=state["message"])
        body = _body(outcome)
        if body.get("attempts") == 2:   # the pipeline's own retry cost a second call
            self.d.budget.record(self.d.chat_model, TRANSLATE_COST)

        update = _out(outcome.http_status, body)
        out_of_scope = body.get("status") == "error" and body.get("stage") == "scope"
        if out_of_scope and self.d.retriever is not None and self.d.chat is not None:
            # the compiler says the data can't express this: the documentation gets a turn
            update.update(route=KNOWLEDGE, fallback_from=body,
                          route_reason=state.get("route_reason", "") + "; out of scope, knowledge fallback")
        return update

    def clarify(self, state: AgentState) -> Dict[str, Any]:
        """Ask the user (interrupt), then resume the parked query. At most twice a turn.

        LangGraph re-runs this node from the top on resume, so everything before
        interrupt() only reads state. The checkpointer keeps the pause for as long as
        the user takes; if the pipeline's own 30-minute parking has expired by then, the
        answer is applied to the parked DSL and run again (see _resume).
        """
        used = state.get("interrupts_used", 0)
        pending = state["outcome"]
        if used >= MAX_INTERRUPTS:
            unresolved = ", ".join(repr(q.get("value")) for q in pending.get("questions", []))
            return _error(422, "clarification", [
                f"{COULD_NOT_ANSWER} {unresolved} could not be matched after "
                f"{MAX_INTERRUPTS} clarifications; try rephrasing with the exact name."
            ], question=pending.get("question"), dsl=pending.get("dsl"))

        answers = interrupt({
            "clarification_id": pending["clarification_id"],
            "question": pending.get("question"),
            "dsl": pending.get("dsl"),
            "questions": pending["questions"],
            "interrupts_left": MAX_INTERRUPTS - used - 1,
        })
        outcome = self._resume(pending, answers if isinstance(answers, dict) else {})
        return {**outcome, "interrupts_used": used + 1}

    def _resume(self, pending: Dict[str, Any], answers: Dict[str, str]) -> Dict[str, Any]:
        outcome = self.d.pipeline.answer(pending["clarification_id"], answers)
        if outcome.http_status != 404:
            return _out(outcome.http_status, _body(outcome))
        # the pipeline forgot it (30-minute TTL, or a restart without Redis); the
        # checkpoint did not. Apply the answers to the parked DSL the way the pipeline
        # does, using the option's label, and compile it again: no model call.
        dsl = pending["dsl"]
        for q in pending["questions"]:
            chosen = str(answers.get(q["id"], "")).strip()
            label = next((o["label"] for o in q.get("options", []) if o["id"] == chosen), chosen)
            dsl = dsl.replace(f"'{q['value']}'", f"'{label}'")
        body = _body(self.d.pipeline.ask(dsl=dsl))
        body["question"] = pending.get("question")
        return _out(200 if body.get("status") != "error" else 422, body)

    def remember(self, state: AgentState) -> Dict[str, Any]:
        return {"last_result": state["outcome"]}

    # --- retrieval ------------------------------------------------------------

    def retrieve(self, state: AgentState) -> Dict[str, Any]:
        """Hybrid retrieval; the passages go into state for the next node and for audit."""
        route, message = state["route"], state["message"]
        if self.d.chat is None:
            return _error(503, route, ["the language model is not configured (GROQ_API_KEY)"])
        if route == EXPLAIN and not (state.get("explainable") and state.get("last_result")):
            return _error(422, EXPLAIN, ["there is nothing to explain: the previous answer was not "
                                         "a data result. Ask a data question first."])
        if self.d.retriever is None:
            if route == KNOWLEDGE:
                return _error(503, KNOWLEDGE, ["the documentation index is not built (scripts/build_index.py)"])
            return {"passages": []}

        if route == KNOWLEDGE:
            # ask for a few extra, since the explain-only sections are dropped below
            q, k, scope = message, MAX_PASSAGES + len(EXPLAIN_ONLY_SECTIONS), None
        elif route == EXPLAIN:
            last = state["last_result"]
            q = " ".join(filter(None, [message, last.get("question") or last.get("dsl", "")]))
            k, scope = MAX_PASSAGES, EXPLAIN_SCOPES
        else:
            q, k, scope = message, inv.PLAYBOOK_K, inv.PLAYBOOK_SCOPES
        found = self.d.retriever.search(q, k=k, scope=scope)
        if route == KNOWLEDGE:
            found = [p for p in found if p.citation not in EXPLAIN_ONLY_SECTIONS][:MAX_PASSAGES]
        return {"passages": [{**asdict(p), "citation": p.citation} for p in found]}

    # --- knowledge and explain ------------------------------------------------

    def generate(self, state: AgentState) -> Dict[str, Any]:
        passages = _passages(state)
        if not passages:  # nothing relevant: say so without a model call
            answer = {"status": "answer", "text": NOT_IN_DOCS, "citations": [], "passages": [],
                      "grounded": False}
            if state.get("fallback_from"):   # neither path could answer; report the first
                return _out(422, state["fallback_from"])
            return _out(200, answer)
        prompt = f"Question: {state['message']}\n\nPassages:\n\n{render_passages(passages)}"
        return self._answer(KNOWLEDGE_SYSTEM, prompt, passages, KNOWLEDGE)

    def explain(self, state: AgentState) -> Dict[str, Any]:
        last = state["last_result"]
        passages = _passages(state)
        prompt = (
            f"{render_result(last)}\n\nThe analyst asks: {state['message']}\n\n"
            + (f"Documentation:\n\n{render_passages(passages)}" if passages else
               "No documentation passage was relevant; explain from the result alone.")
        )
        update = self._answer(EXPLAIN_SYSTEM, prompt, passages, EXPLAIN)
        if update["outcome"].get("status") == "answer":
            update["outcome"]["explains"] = {"dsl": last.get("dsl"), "question": last.get("question")}
        return update

    def _answer(self, system: str, prompt: str, passages: Sequence[Passage], stage: str) -> Dict[str, Any]:
        try:
            text = self._complete(self.d.chat_model, messages(system, prompt),
                                  ANSWER_MAX_TOKENS, ANSWER_OUTPUT_ESTIMATE).strip()
        except _Busy as busy:
            return busy.update
        except ModelError as exc:
            return _error(502, stage, [str(exc)])
        text = normalize_citations(text, passages)
        return _out(200, {"status": "answer", "text": text, "grounded": True,
                          "citations": _citations(text, passages),
                          "passages": list(dict.fromkeys(p.chunk_id for p in passages))})

    # --- investigation --------------------------------------------------------

    def find_entities(self, state: AgentState) -> Dict[str, Any]:
        """Resolve names before planning, so the plan uses the database's spelling."""
        layer, registry = self.d.pipeline.layer, self.d.pipeline.registry
        return {"entities": inv.find_entities(state["message"], layer, registry.as_mapping()),
                "focus_metric": inv.find_metric(state["message"], layer)}

    def plan(self, state: AgentState) -> Dict[str, Any]:
        """One 120b call -> at most 5 DSL steps. Reserves the synthesis budget too, so an
        investigation never stops between its plan and its answer for lack of tokens."""
        reference = self.d.pipeline.registry.reference_date
        system = (build_system_prompt(self.d.pipeline.layer, year=reference.year if reference else None)
                  + "\n\n" + inv.PLANNER_INSTRUCTIONS)
        playbook = render_passages(_passages(state))
        user = inv.planner_user_prompt(state["message"], state.get("entities") or [],
                                       reference.isoformat() if reference else None, playbook,
                                       state.get("focus_metric"))
        over = self._over_budget(self.d.chat_model,
                                 estimate(system, user) + inv.PLAN_OUTPUT_ESTIMATE + inv.SYNTH_ESTIMATE)
        if over:
            return over
        try:
            reply = self.d.chat.complete(messages(system, user), max_tokens=inv.PLAN_MAX_TOKENS)
        except ModelError as exc:
            return _error(502, "plan", [str(exc)])

        parsed = inv.parse_plan(reply)
        if not parsed["steps"]:
            return {**_error(422, "plan", [f"{COULD_NOT_ANSWER} {parsed['reason']}"],
                             question=state["message"]), "plan_note": parsed["reason"]}
        steps = parsed["steps"]
        notes = [inv.ensure_confirm_step(steps, state.get("focus_metric"), state.get("entities") or [],
                                         self.d.pipeline.layer),
                 inv.narrow_decline_class(steps, state.get("focus_metric"))]
        if parsed["dropped"]:
            notes.append(f"{parsed['dropped']} extra step(s) dropped (limit {inv.MAX_STEPS})")
        return {"plan": steps, "plan_note": "; ".join(n for n in notes if n) or None}

    def execute_step(self, state: AgentState) -> Dict[str, Any]:
        """Run the next planned step through the compiler. No model call."""
        evidence = list(state.get("evidence") or [])
        planned = state["plan"][len(evidence)]
        step = {**planned, "dsl": inv.reorder_clauses(planned["dsl"])}
        record = inv.evidence_from(step, _body(self.d.pipeline.ask(dsl=step["dsl"])))
        if step["dsl"] != planned["dsl"]:
            record["dsl_planned"] = planned["dsl"]   # what the model wrote, before reordering
        evidence.append(record)
        update: Dict[str, Any] = {"evidence": evidence}
        if len(evidence) == 1:
            note = inv.outlier_check(record, state.get("entities") or [], inv.direction(state["message"]))
            if note:
                update["stop_note"] = note
        return update

    def synthesize(self, state: AgentState) -> Dict[str, Any]:
        """One 120b call over all the evidence. Its budget was reserved by plan()."""
        evidence = state.get("evidence") or []
        passages = _passages(state)
        if not any(e.get("status") == "ok" for e in evidence):
            return _error(422, "investigate", [f"{COULD_NOT_ANSWER} None of the planned queries ran."],
                          steps=evidence)
        user = inv.synth_user_prompt(state["message"], evidence, state.get("stop_note"),
                                     render_passages(passages), state.get("entities") or [])
        try:
            text = self.d.chat.complete(messages(inv.SYNTH_SYSTEM, user),
                                        max_tokens=inv.SYNTH_MAX_TOKENS).strip()
        except ModelError as exc:
            return _error(502, "investigate", [str(exc)], steps=evidence)
        text = normalize_citations(text, passages)
        return _out(200, {
            "status": "investigation",
            "question": state["message"],
            "text": text,
            "steps": evidence,
            "stopped_early": state.get("stop_note"),
            "plan_note": state.get("plan_note"),
            "entities": state.get("entities") or [],
            "citations": _citations(text, passages),
            "passages": list(dict.fromkeys(p.chunk_id for p in passages)),
            "grounded": True,
        })


class _Busy(Exception):
    def __init__(self, update: Dict[str, Any]):
        self.update = update


HYPHENS = re.compile(r"[‐‑‒–—−]")   # ‐ ‑ ‒ – — −
MARKER = re.compile(r"[\[【]([^\]】\n]{2,200})[\]】]")   # [..] and the full-width 【..】


def normalize_citations(text: str, passages: Sequence[Passage]) -> str:
    """Citation markers rewritten to the exact `[doc > heading]` the UI links.

    Models drift from the format they were shown: a non-breaking hyphen in "kb‑07",
    just the document id, `[kb-07]`, or a heading cut short, `[kb-07 > Worked method]`.
    Those are mapped back to the passage they mean (a bare id to that document's first
    passage; a cut heading only when exactly one passage starts that way). Anything else
    in brackets is left alone, so an unrecognised marker stays visible rather than being
    made to fit.
    """
    by_citation = {p.citation: p.citation for p in passages}
    by_doc: Dict[str, str] = {}
    for p in passages:
        by_doc.setdefault(p.doc_id, p.citation)

    def fix(match: "re.Match[str]") -> str:
        inner = " ".join(HYPHENS.sub("-", match.group(1)).split())
        found = by_citation.get(inner) or by_doc.get(inner)
        if not found and " > " in inner:
            starts = {c for c in by_citation if c.startswith(inner)}
            found = starts.pop() if len(starts) == 1 else None
        return f"[{found}]" if found else match.group(0)

    return MARKER.sub(fix, text)


def _citations(text: str, passages: Sequence[Passage]) -> List[str]:
    """What the model cited, in order; if nothing recognisable, what it was given."""
    cited = [p.citation for p in passages if f"[{p.citation}]" in text]
    return list(dict.fromkeys(cited or [p.citation for p in passages]))
