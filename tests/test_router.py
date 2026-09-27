"""Routing tests: data, explanation or documentation."""
import pytest

from app.router import Route, fallback_route, route


@pytest.mark.parametrize("message", [
    "total value by issuer this month",
    "top 10 merchants by volume",
    "decline rate by issuer for the past 5 months",
    "how many transactions did we process in May",          # "how many" is a count, not a definition
    "what is the value by issuer this month",               # "what is" but clearly a query
    "success rate for credit cards",
])
def test_queries_go_to_the_data_path(message):
    assert route(message, has_result=False) is Route.DATA


@pytest.mark.parametrize("message", [
    "what does do not honor mean",
    "what is the difference between a soft and a hard decline",
    "how is ats calculated",
    "what is IntentQL",
    "can the system write to the database",
    "how does the system decide what to compute",
    "why does the model not write SQL directly",
    "tell me about response code 92",
    "define average ticket size",
])
def test_definitional_questions_go_to_the_documentation(message):
    assert route(message, has_result=False) is Route.KNOWLEDGE


@pytest.mark.parametrize("message", [
    "explain this",
    "why is that so high?",
    "what does this mean",
    "walk me through the result",
    "interpret these numbers",
    "what am I looking at",
])
def test_follow_ups_explain_the_result_on_screen(message):
    assert route(message, has_result=True) is Route.EXPLAIN


def test_an_explicit_explain_routes_even_with_nothing_on_screen():
    # the assistant then says there is no result yet, which beats inventing a query
    assert route("explain this", has_result=False) is Route.EXPLAIN


def test_an_explicit_explain_is_an_explanation_either_way():
    assert route("why is that so high?", has_result=True) is Route.EXPLAIN
    assert route("why is that so high?", has_result=False) is Route.EXPLAIN


def test_a_bare_demonstrative_needs_a_result_to_point_at():
    # no explain verb, just a pronoun: only a follow-up when there is something to follow
    assert route("is it higher than the others", has_result=True) is Route.EXPLAIN
    assert route("is it higher than the others", has_result=False) is Route.DATA


def test_a_long_question_mentioning_that_is_a_fresh_question():
    message = ("why is the decline rate for prepaid cards higher than credit cards "
               "in the last quarter for that issuer")
    assert route(message, has_result=True) is Route.DATA


def test_a_new_query_is_not_hijacked_by_an_existing_result():
    assert route("value by merchant this month", has_result=True) is Route.DATA


def test_an_unanswerable_data_question_falls_back_to_documentation():
    assert fallback_route(Route.DATA) is Route.KNOWLEDGE


def test_documentation_has_no_fallback():
    # nothing retrieved means unanswered; inventing a query would answer another question
    assert fallback_route(Route.KNOWLEDGE) is None
    assert fallback_route(Route.EXPLAIN) is None


@pytest.mark.parametrize("message", [
    "value by issuer this month",
    "decline rate by merchant this quarter",
    "volume this week by card type",
])
def test_a_date_phrase_is_not_a_reference_to_the_last_answer(message):
    # "this month" points at a period, not at the result on screen
    assert route(message, has_result=True) is Route.DATA
