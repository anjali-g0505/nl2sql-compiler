"""App contract tests: question/DSL in -> ok / needs_clarification / error out.

No database and no LLM: the translator is scripted, execution is a fake that records
what it was asked to run, and the registry serves in-memory indexes.
"""
from datetime import date, datetime, timedelta

import pytest

from app.pipeline import Feedback, Pipeline, TranslatorError
from compiler.indexes import SourceStatus
from compiler.resolver import build_index

REF = date(2025, 12, 27)


class ScriptedTranslator:
    """Returns the given DSL strings in order; records the feedback it was given."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []  # (question, feedback)

    def translate(self, question, feedback=None):
        self.calls.append((question, feedback))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeExecute:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else [{"value": 1}]
        self.calls = []  # (sql, params)

    def __call__(self, sql, params):
        self.calls.append((sql, tuple(params)))
        return list(self.rows)


class FakeRegistry:
    def __init__(self, reference_date=REF):
        self.reference_date = reference_date
        self.indexes = {
            "issuer": build_index(
                [("ISS001", "State Bank of India"), ("ISS002", "HDFC Bank"), ("ISS003", "HSBC Bank")],
                aliases={"ISS001": ["SBI"]},
            ),
            "mcc": build_index([("5411", "Grocery Stores"), ("5812", "Restaurants")]),
        }

    def as_mapping(self):
        return dict(self.indexes)

    def status(self, name=None):
        return SourceStatus(name, "entity", rows=3, last_refreshed=datetime(2025, 12, 27, 2, 0))


class Clock:
    def __init__(self):
        self.now = datetime(2026, 1, 1, 12, 0)

    def __call__(self):
        return self.now


@pytest.fixture
def execute():
    return FakeExecute()


def make(translator=None, execute=None, **kwargs):
    return Pipeline(
        registry=kwargs.pop("registry", FakeRegistry()),
        execute=execute or FakeExecute(),
        translator=translator,
        **kwargs,
    )


# --- request shape ------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [{}, {"question": "q", "dsl": "SHOW value"}])
def test_exactly_one_of_question_or_dsl(kwargs):
    outcome = make().ask(**kwargs)
    assert outcome.http_status == 422
    assert outcome.body["stage"] == "request"


def test_a_question_without_a_translator_is_unavailable():
    outcome = make().ask(question="total value?")
    assert outcome.http_status == 503
    assert outcome.body["stage"] == "translate"


# --- the happy path -----------------------------------------------------------

def test_dsl_runs_end_to_end(execute):
    outcome = make(execute=execute).ask(dsl="SHOW value BY issuer WHERE card_type = 'credit'")

    assert outcome.http_status == 200
    body = outcome.body
    assert body["status"] == "ok"
    assert "b.card_type = 'Credit'" in body["sql"]                  # display form
    sql, params = execute.calls[0]
    assert "b.card_type = %s" in sql and params == ("Credit",)     # what actually ran
    assert body["assumptions"] == [
        {"key": "success_default", "text": "Only successful transactions considered."}
    ]
    assert body["rows"] == [{"value": 1}] and body["columns"] == ["value"]
    assert body["attempts"] == 1


def test_forced_limit_is_applied_at_execution_not_in_the_sql():
    execute = FakeExecute(rows=[{"v": i} for i in range(3)])
    outcome = make(execute=execute, forced_limit=2).ask(dsl="SHOW value BY issuer")

    sql, _ = execute.calls[0]
    assert sql.endswith("\nLIMIT 3;")                 # one extra row, to detect the cut
    assert "LIMIT" not in outcome.body["sql"]          # the shown SQL is what was asked
    assert outcome.body["row_count"] == 2
    assert outcome.body["assumptions"][-1] == {
        "key": "forced_limit", "text": "No limit given; results capped at 2 rows."
    }


def test_no_forced_limit_assumption_when_nothing_was_cut():
    execute = FakeExecute(rows=[{"v": 1}])
    outcome = make(execute=execute, forced_limit=2).ask(dsl="SHOW value BY issuer")
    assert "forced_limit" not in [a["key"] for a in outcome.body["assumptions"]]


def test_an_explicit_limit_is_left_alone(execute):
    make(execute=execute, forced_limit=2).ask(dsl="SHOW value BY issuer LIMIT 10")
    sql, _ = execute.calls[0]
    assert sql.endswith("LIMIT 10;") and sql.count("LIMIT") == 1


def test_missing_reference_date_is_reported_not_crashed(execute):
    pipeline = make(execute=execute, registry=FakeRegistry(reference_date=None))
    outcome = pipeline.ask(dsl="SHOW value PERIOD MTD")
    assert outcome.http_status == 503 and outcome.body["stage"] == "compile"
    assert execute.calls == []


# --- one LLM retry ------------------------------------------------------------

def test_a_bad_first_answer_is_retried_once_with_the_errors(execute):
    translator = ScriptedTranslator("SHOW revenue BY issuer", "SHOW value BY issuer")
    outcome = make(translator, execute).ask(question="revenue by bank")

    assert outcome.body["status"] == "ok"
    assert outcome.body["attempts"] == 2
    assert outcome.body["dsl"] == "SHOW value BY issuer"
    _, feedback = translator.calls[1]
    assert feedback == Feedback(dsl="SHOW revenue BY issuer", errors=("unknown metric 'revenue'",))


def test_a_second_failure_is_returned_not_retried_again():
    translator = ScriptedTranslator("SHOW value BY", "SHOW revenue")
    outcome = make(translator).ask(question="?")

    assert outcome.http_status == 422
    assert outcome.body["attempts"] == 2
    assert outcome.body["stage"] == "validate"
    assert len(translator.calls) == 2


def test_a_syntax_error_is_retried():
    translator = ScriptedTranslator("SHOW value BY", "SHOW value")
    outcome = make(translator).ask(question="total value")
    _, feedback = translator.calls[1]
    assert outcome.body["status"] == "ok"
    assert "position" in feedback.errors[0]


def test_unknown_enum_value_is_sent_back_with_the_valid_values():
    translator = ScriptedTranslator(
        "SHOW volume WHERE card_type = 'Gold'", "SHOW volume WHERE card_type = 'Credit'"
    )
    outcome = make(translator).ask(question="gold card volume")

    _, feedback = translator.calls[1]
    assert feedback.errors == (
        "card_type has no value 'Gold'; valid values: Credit, Debit, Prepaid",
    )
    assert outcome.body["status"] == "ok"


def test_unknown_catalog_value_lists_the_catalog():
    translator = ScriptedTranslator("SHOW volume WHERE mcc = 'Petrol'", "SHOW volume")
    make(translator).ask(question="petrol volume")
    _, feedback = translator.calls[1]
    assert feedback.errors == ("mcc has no value 'Petrol'; valid values: Grocery Stores, Restaurants",)


def test_translator_failure_is_a_bad_gateway():
    outcome = make(ScriptedTranslator(TranslatorError("rate limited"))).ask(question="?")
    assert outcome.http_status == 502
    assert outcome.body["errors"] == ["rate limited"]


def test_dsl_input_is_never_retried():
    translator = ScriptedTranslator()
    outcome = make(translator).ask(dsl="SHOW volume WHERE card_type = 'Gold'")
    assert outcome.http_status == 422
    assert outcome.body["stage"] == "resolve"
    assert "valid values: Credit, Debit, Prepaid" in outcome.body["errors"][0]
    assert translator.calls == []


# --- clarifications -----------------------------------------------------------

def test_ambiguous_value_asks_the_user_not_the_llm():
    translator = ScriptedTranslator("SHOW volume WHERE status = 'decline'")
    outcome = make(translator).ask(question="declined volume")

    body = outcome.body
    assert body["status"] == "needs_clarification"
    assert len(translator.calls) == 1                       # no retry on a coin flip
    [question] = body["questions"]
    assert question["id"] == "q1" and question["reason"] == "ambiguous"
    assert [o["id"] for o in question["options"]] == ["Business Decline", "Technical Decline"]


def test_answering_a_clarification_resumes_the_query(execute):
    pipeline = make(execute=execute)
    asked = pipeline.ask(dsl="SHOW volume BY issuer WHERE status = 'decline'").body

    outcome = pipeline.answer(asked["clarification_id"], {"q1": "Business Decline"})

    assert outcome.body["status"] == "ok"
    assert outcome.body["dsl"] == "SHOW volume BY issuer WHERE status = 'Business Decline'"
    assert execute.calls[0][1] == ("Business Decline",)


def test_unknown_entity_asks_the_user_with_the_list_age():
    pipeline = make()
    body = pipeline.ask(dsl="SHOW value WHERE issuer = 'Kotak Bank'").body

    [question] = body["questions"]
    assert question["reason"] == "unknown"
    assert "last refreshed on 2025-12-27" in question["message"]


def test_a_typed_answer_is_resolved_like_any_value(execute):
    pipeline = make(execute=execute)
    body = pipeline.ask(dsl="SHOW value WHERE issuer = 'Kotak Bank'").body

    outcome = pipeline.answer(body["clarification_id"], {"q1": "SBI"})  # an alias
    assert outcome.body["status"] == "ok"
    assert execute.calls[0][1] == ("State Bank of India",)


def test_an_answer_that_is_still_unclear_asks_again():
    pipeline = make()
    first = pipeline.ask(dsl="SHOW value WHERE issuer = 'Kotak Bank'").body
    second = pipeline.answer(first["clarification_id"], {"q1": "Bank"}).body
    assert second["status"] == "needs_clarification"
    assert second["clarification_id"] != first["clarification_id"]


def test_missing_answers_keep_the_clarification_open():
    pipeline = make()
    body = pipeline.ask(dsl="SHOW volume WHERE status = 'decline'").body

    missing = pipeline.answer(body["clarification_id"], {})
    assert missing.http_status == 422
    assert pipeline.answer(body["clarification_id"], {"q1": "Technical Decline"}).http_status == 200


def test_a_clarification_can_be_answered_only_once():
    pipeline = make()
    body = pipeline.ask(dsl="SHOW volume WHERE status = 'decline'").body
    pipeline.answer(body["clarification_id"], {"q1": "Technical Decline"})
    assert pipeline.answer(body["clarification_id"], {"q1": "Technical Decline"}).http_status == 404


def test_unknown_or_expired_ids_are_not_found():
    clock = Clock()
    pipeline = make(clock=clock)
    body = pipeline.ask(dsl="SHOW volume WHERE status = 'decline'").body

    assert pipeline.answer("nope", {"q1": "x"}).http_status == 404
    clock.now += timedelta(minutes=31)
    assert pipeline.answer(body["clarification_id"], {"q1": "Technical Decline"}).http_status == 404


def test_one_value_used_twice_is_asked_once():
    pipeline = make()
    body = pipeline.ask(dsl="SHOW volume WHERE status IN ('decline', 'decline')").body
    assert [q["id"] for q in body["questions"]] == ["q1"]
