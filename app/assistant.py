"""One entry point for a message: query the data, explain a result, or answer from docs.

    assistant.ask(session_id, "top 10 merchants by value this month")   -> rows
    assistant.ask(session_id, "why is that so high?")                   -> an explanation
    assistant.ask(session_id, "what does do not honor mean")            -> documentation

Routing is described in app/router.py. Two details belong here rather than there:

  * **The last result is remembered per session**, so "explain this" has something to
    explain. It is kept in the same store as parked clarifications — Redis in a
    deployment, a dictionary otherwise — and expires on its own.
  * **A data question the compiler cannot express falls through to the documentation.**
    The translator saying "CANNOT" is the signal: the question was not about the data,
    so the corpus gets a turn before the user is told no.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, Optional

from app.answerer import Answerer
from app.clarifications import MemoryStore, Pending, Store
from app.groq import ModelError
from app.pipeline import Outcome, Pipeline
from app.router import Route, fallback_route, route

RESULT_TTL = timedelta(hours=2)     # long enough to come back to an answer, short enough to forget
RESULT_PREFIX = "result:"


@dataclass
class Assistant:
    """The chat-level object: routing plus memory of the last result."""

    pipeline: Pipeline
    answerer: Optional[Answerer] = None
    results: Store = field(default_factory=MemoryStore)

    # --- entry point ----------------------------------------------------------

    def ask(self, session_id: str, message: str) -> Outcome:
        last = self._last_result(session_id)
        destination = route(message, has_result=last is not None)

        if destination is Route.EXPLAIN:
            return self._explain(message, last)
        if destination is Route.KNOWLEDGE:
            return self._knowledge(message)

        outcome = self.pipeline.ask(question=message)
        if _is_out_of_scope(outcome) and fallback_route(Route.DATA) is Route.KNOWLEDGE:
            # the compiler says the data cannot express this, so it was not a data question
            answered = self._knowledge(message)
            if answered.body.get("grounded"):
                return answered
            return outcome                       # neither path could answer; report the first
        if outcome.body.get("status") == "ok":
            self._remember(session_id, outcome.body)
        return outcome

    def answer_clarification(self, session_id: str, clarification_id: str,
                             answers: Dict[str, str]) -> Outcome:
        outcome = self.pipeline.answer(clarification_id, answers)
        if outcome.body.get("status") == "ok":
            self._remember(session_id, outcome.body)
        return outcome

    # --- the two answering paths ----------------------------------------------

    def _knowledge(self, message: str) -> Outcome:
        if self.answerer is None:
            return Outcome(503, {"status": "error", "stage": "knowledge",
                                 "errors": ["the documentation assistant is not configured"]})
        try:
            return Outcome(200, self.answerer.knowledge(message).to_dict())
        except ModelError as exc:
            return Outcome(502, {"status": "error", "stage": "knowledge", "errors": [str(exc)]})

    def _explain(self, message: str, last: Optional[Dict[str, Any]]) -> Outcome:
        if self.answerer is None:
            return Outcome(503, {"status": "error", "stage": "explain",
                                 "errors": ["the documentation assistant is not configured"]})
        if last is None:
            return Outcome(422, {"status": "error", "stage": "explain",
                                 "errors": ["there is no result to explain yet; ask a question first"]})
        try:
            answer = self.answerer.explain(last, message).to_dict()
        except ModelError as exc:
            return Outcome(502, {"status": "error", "stage": "explain", "errors": [str(exc)]})
        answer["explains"] = {"dsl": last.get("dsl"), "question": last.get("question")}
        return Outcome(200, answer)

    # --- the last result ------------------------------------------------------

    def _remember(self, session_id: str, result: Dict[str, Any]) -> None:
        """Keep the result so a follow-up has something to point at.

        Stored in the Pending shape the store already speaks: the DSL identifies the
        query, and the body carries the rows the explanation will quote.
        """
        self.results.put(
            RESULT_PREFIX + session_id,
            Pending(question=result.get("question"), dsl=result.get("dsl", ""),
                    questions=[result], attempts=result.get("attempts", 1)),
            RESULT_TTL,
        )

    def _last_result(self, session_id: str) -> Optional[Dict[str, Any]]:
        stored = self.results.peek(RESULT_PREFIX + session_id)
        return stored.questions[0] if stored and stored.questions else None


def _is_out_of_scope(outcome: Outcome) -> bool:
    return outcome.body.get("status") == "error" and outcome.body.get("stage") == "scope"
