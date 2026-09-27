"""Assistant tests: routing, the last-result memory, and the fall-through to documentation.

No model and no retrieval: the answerer is a stand-in that records what it was asked, so
these check the orchestration rather than the prose.
"""
from datetime import date

import pytest

from app.answerer import Answer
from app.assistant import Assistant
from app.clarifications import MemoryStore
from app.groq import ModelError
from app.pipeline import Pipeline, Unanswerable
from tests.test_pipeline import FakeExecute, FakeRegistry, ScriptedTranslator


class FakeAnswerer:
    """Stands in for retrieval plus a model."""

    def __init__(self, grounded=True, fail=False):
        self.grounded = grounded
        self.fail = fail
        self.knowledge_calls = []
        self.explain_calls = []

    def knowledge(self, question):
        self.knowledge_calls.append(question)
        if self.fail:
            raise ModelError("the model is rate limited")
        if not self.grounded:
            return Answer(text="That isn't covered in the documentation.", grounded=False)
        return Answer(text="A generic issuer refusal.", citations=["kb-03 > Reading the classes"],
                      passages=["kb-03#4"])

    def explain(self, result, question=None):
        self.explain_calls.append((result, question))
        if self.fail:
            raise ModelError("the model timed out")
        return Answer(text="Three issuers, HDFC highest.", citations=["kb-09 > Column conventions"])


def make(translator=None, answerer=None, execute=None):
    store = MemoryStore()
    pipeline = Pipeline(registry=FakeRegistry(), execute=execute or FakeExecute(),
                        translator=translator, store=store)
    return Assistant(pipeline=pipeline, answerer=answerer or FakeAnswerer(), results=MemoryStore())


# --- the data path ---------------------------------------------------------------

def test_a_query_runs_and_is_remembered():
    assistant = make(ScriptedTranslator("SHOW value BY issuer"))
    outcome = assistant.ask("session-1", "value by issuer")
    assert outcome.body["status"] == "ok"
    assert assistant._last_result("session-1")["dsl"] == "SHOW value BY issuer"


def test_results_are_remembered_per_session():
    assistant = make(ScriptedTranslator("SHOW value BY issuer"))
    assistant.ask("session-1", "value by issuer")
    assert assistant._last_result("session-2") is None


def test_a_failed_query_is_not_remembered():
    assistant = make(ScriptedTranslator("SHOW revenue", "SHOW revenue"))
    assert assistant.ask("session-1", "revenue by issuer").http_status == 422
    assert assistant._last_result("session-1") is None


# --- explaining ------------------------------------------------------------------

def test_a_follow_up_explains_the_last_result():
    answerer = FakeAnswerer()
    assistant = make(ScriptedTranslator("SHOW value BY issuer"), answerer)
    assistant.ask("session-1", "value by issuer")

    outcome = assistant.ask("session-1", "why is that so high?")
    assert outcome.body["status"] == "answer"
    assert outcome.body["explains"]["dsl"] == "SHOW value BY issuer"
    result, question = answerer.explain_calls[0]
    assert result["rows"] and question == "why is that so high?"   # the rows are the evidence


def test_explaining_without_a_result_says_so():
    outcome = make().ask("session-1", "explain this")
    assert outcome.http_status == 422
    assert "no result to explain" in outcome.body["errors"][0]


def test_a_clarified_result_is_remembered_too():
    answerer = FakeAnswerer()
    assistant = make(answerer=answerer)
    asked = assistant.pipeline.ask(dsl="SHOW volume BY issuer WHERE status = 'decline'").body
    assistant.answer_clarification("session-1", asked["clarification_id"], {"q1": "Business Decline"})
    assert assistant.ask("session-1", "explain this").body["status"] == "answer"


# --- the documentation path ------------------------------------------------------

def test_a_definitional_question_never_reaches_the_translator():
    translator = ScriptedTranslator()          # would raise IndexError if called
    answerer = FakeAnswerer()
    outcome = make(translator, answerer).ask("session-1", "what does do not honor mean")
    assert outcome.body["status"] == "answer"
    assert translator.calls == []
    assert answerer.knowledge_calls == ["what does do not honor mean"]


def test_an_out_of_scope_question_falls_through_to_the_documentation():
    # the compiler is the classifier: CANNOT means it was not a data question
    translator = ScriptedTranslator(Unanswerable("unsupported domain"))
    answerer = FakeAnswerer()
    outcome = make(translator, answerer).ask("session-1", "how do card networks route a payment")
    assert outcome.body["status"] == "answer"
    assert answerer.knowledge_calls


def test_when_neither_path_can_answer_the_first_error_is_returned():
    # routed to data, the compiler cannot express it, and the corpus has nothing either
    translator = ScriptedTranslator(Unanswerable("unsupported domain"))
    answerer = FakeAnswerer(grounded=False)
    outcome = make(translator, answerer).ask("session-1", "sales by region last quarter")
    assert outcome.body["status"] == "error" and outcome.body["stage"] == "scope"


def test_a_documentation_question_with_nothing_retrieved_says_so_politely():
    # routed to the docs, so the polite "not covered" answer is the right reply
    outcome = make(answerer=FakeAnswerer(grounded=False)).ask("s", "what is the weather in Mumbai")
    assert outcome.body["status"] == "answer" and outcome.body["grounded"] is False


def test_citations_come_back_with_the_answer():
    outcome = make(answerer=FakeAnswerer()).ask("s", "what does do not honor mean")
    assert outcome.body["citations"] == ["kb-03 > Reading the classes"]
    assert outcome.body["passages"] == ["kb-03#4"]


# --- failure modes ---------------------------------------------------------------

def test_a_model_failure_on_the_documentation_path_is_a_bad_gateway():
    outcome = make(answerer=FakeAnswerer(fail=True)).ask("s", "what is IntentQL")
    assert outcome.http_status == 502 and outcome.body["stage"] == "knowledge"


def test_without_an_answerer_documentation_questions_are_unavailable():
    assistant = Assistant(pipeline=Pipeline(registry=FakeRegistry(), execute=FakeExecute()),
                          answerer=None, results=MemoryStore())
    outcome = assistant.ask("s", "what does do not honor mean")
    assert outcome.http_status == 503 and outcome.body["stage"] == "knowledge"


@pytest.mark.parametrize("reference_date", [date(2025, 12, 31)])
def test_the_pipeline_is_untouched_by_routing(reference_date):
    # a plain query still goes through exactly the path /query uses
    assistant = make(ScriptedTranslator("SHOW value BY issuer PERIOD MTD"))
    body = assistant.ask("s", "value by issuer this month").body
    assert body["status"] == "ok" and "t.date >=" in body["sql"]


def test_citations_are_not_repeated():
    """A section can be retrieved through more than one child; a reader should see it once."""
    from app.answerer import Answerer
    from rag.index import Passage

    class TwoHitsOnOneSection:
        def search(self, *args, **kwargs):
            common = dict(text="kb-09 > Column conventions\n\nbody", doc_id="kb-09",
                          heading="Column conventions", scope="interpretation aid",
                          confidence="high", score=0.03, distance=0.2)
            return [Passage(chunk_id="kb-09#2", matched_child="kb-09#2:c1", **common),
                    Passage(chunk_id="kb-09#2", matched_child="kb-09#2:c2", **common)]

    class Chat:
        def complete(self, messages, **kwargs):
            return "Value is a monetary sum [kb-09 > Column conventions]."

    answer = Answerer(retriever=TwoHitsOnOneSection(), chat=Chat()).knowledge("what is value")
    assert answer.citations == ["kb-09 > Column conventions"]
    assert answer.passages == ["kb-09#2"]
