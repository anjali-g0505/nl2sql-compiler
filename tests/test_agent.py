"""Agent tests: routing votes, each branch, the clarification limit, the step limit, budget.

No model, no database, no index: the pipeline is the real one over the fakes from
tests/test_pipeline.py, the models are scripted, retrieval returns fixed passages, and
the checkpointer is in memory.
"""
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent import investigate as inv
from agent.budget import TokenBudget
from agent.graph import build_graph
from agent.nodes import COULD_NOT_ANSWER, Deps
from agent.routing import Vote, decide, parse_route, parse_vote, regex_route, regex_vote
from app.groq import ModelError
from app.pipeline import Pipeline, Unanswerable
from compiler.semantic_layer import SemanticLayer
from rag.index import Passage
from tests.test_pipeline import FakeExecute, FakeRegistry, ScriptedTranslator

PLAYBOOK = Passage(chunk_id="kb-07#3", text="kb-07 Decline analysis > Reasoning sequence\n\nCompare rates.",
                   doc_id="kb-07", heading="Reasoning sequence", scope="reasoning playbook",
                   confidence="high", score=0.03, distance=0.3, matched_child="kb-07#3.1")


class FakeChat:
    """Scripted replies, in order; records the messages of every call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, messages, max_tokens=1024, temperature=0.0):
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeRetriever:
    def __init__(self, passages=(PLAYBOOK,)):
        self.passages = list(passages)
        self.calls = []   # (q, k, scope)

    def search(self, q, k=4, scope=None):
        self.calls.append((q, k, scope))
        return list(self.passages)


def make(translator=None, chat=None, classifier=None, retriever=None, rows=None, budget=None):
    execute = FakeExecute(rows)
    pipeline = Pipeline(registry=FakeRegistry(), execute=execute, translator=translator)
    deps = Deps(pipeline=pipeline, retriever=retriever, chat=chat, classifier=classifier,
                budget=budget or TokenBudget(8000))
    graph = build_graph(deps, checkpointer=InMemorySaver())
    graph.pipeline = pipeline  # for tests that need to reach behind the graph
    return graph, execute


def run(graph, message, session="s1"):
    return graph.invoke({"session_id": session, "message": message},
                        {"configurable": {"thread_id": session}})


def resume(graph, answers, session="s1"):
    return graph.invoke(Command(resume=answers), {"configurable": {"thread_id": session}})


# --- routing --------------------------------------------------------------------

@pytest.mark.parametrize("message, has_result, expected", [
    ("why is the issuer business decline rate for hdfc so high", False, "investigate"),
    ("what is driving the drop in SBI success rate", False, "investigate"),
    ("why does the system reject unknown merchants", False, "knowledge"),   # product question
    ("why is that so high", True, "explain"),                               # a follow-up
    ("what does do not honor mean", False, "knowledge"),
    ("value by issuer this month", False, "data"),
])
def test_regex_vote(message, has_result, expected):
    assert regex_route(message, has_result) == expected


def test_classifier_reply_parsing():
    assert parse_route("investigate") == "investigate"
    assert parse_route("Route: Data.") == "data"
    assert parse_route("data or knowledge") is None      # undecided is no vote
    assert parse_route("") is None


def vote(route, confidence, basis="classifier"):
    return Vote(route, confidence, basis)


def test_the_more_confident_vote_wins_and_a_tie_goes_to_regex():
    regex = vote("knowledge", 0.85, "definitional phrasing")
    assert decide(regex, vote("data", 0.90), "what is the value for HDFC", False)[0] == "data"
    assert decide(regex, vote("data", 0.60), "what is the value for HDFC", False)[0] == "knowledge"
    assert decide(regex, vote("data", 0.85), "what is the value for HDFC", False)[0] == "knowledge"
    assert decide(vote("data", 0.5, "no rule matched"), None, "value by issuer", False)[0] == "data"


def test_explain_is_ignored_unless_the_previous_answer_was_data():
    regex = vote("investigate", 0.85, "diagnostic phrasing")
    llm = vote("explain", 0.99)
    question = "why is HSBC's average ticket size so low compared to the other issuers"
    route, reason = decide(regex, llm, question, explainable=False)
    assert route == "investigate" and "classifier explain (0.99) ignored" in reason
    assert decide(regex, llm, question, explainable=True)[0] == "explain"   # allowed, and more confident


def test_a_why_question_goes_to_investigate_unless_it_is_knowledge():
    message = "tell me why HDFC Bank has more declines than SBI"
    route, reason = decide(vote("data", 0.5, "no rule matched"), vote("data", 0.9), message, False)
    assert route == "investigate" and "why-question rule: data -> investigate" in reason
    product = "why does the system reject unknown merchants"
    assert decide(vote("knowledge", 0.85), vote("knowledge", 0.9), product, False)[0] == "knowledge"
    # "why is that so high" after a non-data answer: nothing to explain, so investigate
    assert decide(vote("explain", 0.95), vote("explain", 0.9), "why is that so high", False)[0] == "investigate"
    # "explain this" (no why) after a non-data answer: the explain node says there's nothing
    assert decide(vote("explain", 0.95), None, "explain this", False)[0] == "explain"


def test_classifier_confidence_parsing():
    assert parse_vote('{"route": "data", "confidence": 0.9}') == Vote("data", 0.9, "classifier")
    assert parse_vote('```json\n{"route": "explain", "confidence": 85}\n```').confidence == 0.85
    assert parse_vote('{"route": "data", "confidence": "high"}').confidence == 0.5
    assert parse_vote("investigate") == Vote("investigate", 0.5, "classifier")   # format ignored
    assert parse_vote('{"route": "weather"}') is None


def test_regex_rules_carry_their_confidence():
    assert regex_vote("explain this", False) == Vote("explain", 0.95, "explicit request to explain")
    assert regex_vote("why is the value so high", False).confidence == 0.85
    assert regex_vote("value by issuer", False) == Vote("data", 0.5, "no rule matched")


def test_both_votes_and_confidences_are_kept_in_state():
    graph, _ = make(ScriptedTranslator("SHOW value BY issuer"),
                    classifier=FakeChat('{"route": "data", "confidence": 0.9}'))
    state = run(graph, "what is the value for HDFC")
    assert (state["route_regex"], state["route_llm"], state["route"]) == ("knowledge", "data", "data")
    assert state["route_votes"]["regex"]["confidence"] == 0.85
    assert state["route_votes"]["classifier"]["confidence"] == 0.9


def test_explain_after_a_knowledge_answer_is_refused():
    chat = FakeChat("A generic refusal [kb-07 > Reasoning sequence].")
    graph, _ = make(ScriptedTranslator("SHOW value BY issuer"), chat=chat, retriever=FakeRetriever(),
                    rows=[{"iss_name": "HDFC Bank", "value": 10}, {"iss_name": "HSBC Bank", "value": 4}])
    run(graph, "value by issuer")                       # data: explainable
    run(graph, "what does do not honor mean")           # knowledge: no longer explainable
    state = run(graph, "explain this")
    assert state["explainable"] is False and state["route"] == "explain"
    assert state["http_status"] == 422 and "not a data result" in state["outcome"]["errors"][0]


def test_a_failing_classifier_falls_back_to_regex():
    graph, _ = make(ScriptedTranslator("SHOW value BY issuer"),
                    classifier=FakeChat(ModelError("rate limited")))
    state = run(graph, "value by issuer")
    assert state["route_llm"] is None and state["route"] == "data"
    assert state["outcome"]["status"] == "ok"


# --- data -----------------------------------------------------------------------

def test_data_returns_rows_and_chart_without_a_model_call():
    chat = FakeChat()  # would fail if called
    graph, _ = make(ScriptedTranslator("SHOW value BY issuer"), chat=chat,
                    rows=[{"iss_name": "HDFC Bank", "value": 10}, {"iss_name": "HSBC Bank", "value": 4}])
    state = run(graph, "value by issuer")
    assert state["outcome"]["status"] == "ok" and state["outcome"]["chart_type"] == "BAR"
    assert chat.calls == []
    assert state["last_result"]["dsl"] == "SHOW value BY issuer"


def test_out_of_scope_falls_back_to_documentation():
    chat = FakeChat("Do not honor is a generic refusal [kb-07 > Reasoning sequence].")
    graph, _ = make(ScriptedTranslator(Unanswerable("not about the data")), chat=chat,
                    retriever=FakeRetriever())
    state = run(graph, "tell me something")
    assert state["outcome"]["status"] == "answer"
    assert state["outcome"]["citations"] == ["kb-07 > Reasoning sequence"]


def test_out_of_scope_with_nothing_in_the_docs_reports_the_scope_error():
    graph, _ = make(ScriptedTranslator(Unanswerable("weather")), chat=FakeChat(),
                    retriever=FakeRetriever(passages=()))
    state = run(graph, "weather in Mumbai")
    assert state["outcome"]["stage"] == "scope" and state["http_status"] == 422


# --- clarifications ------------------------------------------------------------

def test_a_clarification_pauses_and_resumes():
    graph, execute = make(ScriptedTranslator("SHOW volume BY issuer WHERE status = 'decline'"))
    paused = run(graph, "declined volume by issuer")
    [pending] = paused["__interrupt__"]
    assert pending.value["interrupts_left"] == 1

    state = resume(graph, {"q1": "Business Decline"})
    assert state["outcome"]["status"] == "ok"
    assert execute.calls[0][1] == ("Business Decline",)
    assert state["last_result"]["dsl"].endswith("'Business Decline'")


def test_two_clarifications_at_most_then_could_not_answer():
    graph, execute = make(ScriptedTranslator("SHOW value WHERE issuer = 'Kotak Bank'"))
    run(graph, "value for kotak")
    resume(graph, {"q1": "Bank"})                 # still unclear: asked a second time
    state = resume(graph, {"q1": "Some Bank"})    # and again: the limit
    assert state["outcome"]["status"] == "error"
    assert state["outcome"]["errors"][0].startswith(COULD_NOT_ANSWER)
    assert state["interrupts_used"] == 2 and execute.calls == []


def test_an_expired_clarification_resumes_from_the_checkpoint():
    graph, execute = make(ScriptedTranslator("SHOW value WHERE issuer = 'Kotak Bank'"))
    paused = run(graph, "value for kotak")
    graph.pipeline.store.take(paused["__interrupt__"][0].value["clarification_id"])  # expire it

    state = resume(graph, {"q1": "SBI"})
    assert state["outcome"]["status"] == "ok"
    assert execute.calls[0][1] == ("State Bank of India",)


def test_a_new_message_replaces_a_pending_clarification():
    graph, _ = make(ScriptedTranslator("SHOW volume WHERE status = 'decline'", "SHOW value BY issuer"))
    run(graph, "declined volume")
    state = run(graph, "value by issuer")
    assert state["outcome"]["status"] == "ok" and state["interrupts_used"] == 0
    assert not graph.get_state({"configurable": {"thread_id": "s1"}}).interrupts


# --- knowledge and explain --------------------------------------------------------

def test_knowledge_keeps_the_passages_in_state():
    retriever = FakeRetriever()
    graph, _ = make(chat=FakeChat("Compare rates first [kb-07 > Reasoning sequence]."),
                    retriever=retriever)
    state = run(graph, "what does do not honor mean")
    assert state["route"] == "knowledge"
    assert state["passages"][0]["citation"] == "kb-07 > Reasoning sequence"
    assert retriever.calls == [("what does do not honor mean", 4, None)]


def test_nothing_retrieved_means_no_model_call():
    chat = FakeChat()
    graph, _ = make(chat=chat, retriever=FakeRetriever(passages=()))
    state = run(graph, "what does sourdough mean")
    assert state["outcome"]["grounded"] is False and chat.calls == []


def test_explain_uses_the_last_result():
    chat = FakeChat("HDFC Bank is the only row.")
    graph, _ = make(ScriptedTranslator("SHOW value BY issuer"), chat=chat, retriever=FakeRetriever(),
                    rows=[{"iss_name": "HDFC Bank", "value": 10}])
    run(graph, "value by issuer")
    state = run(graph, "why is that so high")
    assert state["route"] == "explain"
    assert state["outcome"]["explains"]["dsl"] == "SHOW value BY issuer"
    assert "HDFC Bank" in chat.calls[0][1]["content"]


# --- investigation ------------------------------------------------------------------

def plan_json(n):
    steps = ", ".join(
        f'{{"purpose": "step {i}", "dsl": "SHOW business_decline_rate BY issuer"}}' for i in range(1, n + 1))
    return f'{{"steps": [{steps}]}}'


PEERS_HIGH = [{"iss_name": "HDFC Bank", "business_decline_rate": 0.20},
              {"iss_name": "State Bank of India", "business_decline_rate": 0.05},
              {"iss_name": "HSBC Bank", "business_decline_rate": 0.06}]


def test_investigation_plans_once_runs_at_most_five_steps_and_answers():
    chat = FakeChat(plan_json(7), "HDFC Bank's rate is 20%, against 5-6% for peers.")
    graph, execute = make(chat=chat, retriever=FakeRetriever(), rows=PEERS_HIGH)
    state = run(graph, "why is HDFC Bank's business decline rate so high")

    assert state["route"] == "investigate"
    assert state["entities"] == [{"field": "issuer", "value": "HDFC Bank", "matched": "hdfc bank"}]
    assert len(state["plan"]) == 5 and "2 extra step(s) dropped" in state["plan_note"]
    assert len(execute.calls) == 5 and len(state["evidence"]) == 5
    assert len(chat.calls) == 2                                   # plan + synthesize, no more
    assert "issuer = 'HDFC Bank'" in chat.calls[0][1]["content"]  # resolved before planning
    assert state["outcome"]["status"] == "investigation"
    assert state["passages"][0]["scope"] == "reasoning playbook"


def test_investigation_stops_early_when_the_entity_is_not_an_outlier():
    rows = [{"iss_name": "HDFC Bank", "business_decline_rate": 0.05},
            {"iss_name": "State Bank of India", "business_decline_rate": 0.06}]
    chat = FakeChat(plan_json(5), "HDFC Bank is not an outlier.")
    graph, execute = make(chat=chat, retriever=FakeRetriever(), rows=rows)
    state = run(graph, "why is HDFC Bank's business decline rate so high")
    assert len(execute.calls) == 1
    assert "premise does not hold" in state["stop_note"]
    assert "Stopped early" in chat.calls[1][1]["content"]


def test_a_failing_step_is_recorded_not_guessed():
    chat = FakeChat('{"steps": [{"purpose": "a", "dsl": "SHOW business_decline_rate BY issuer"},'
                    ' {"purpose": "b", "dsl": "SHOW revenue BY shop"}]}', "Answer.")
    graph, _ = make(chat=chat, retriever=FakeRetriever(), rows=PEERS_HIGH)
    state = run(graph, "why is HDFC Bank's business decline rate so high")
    assert [e["status"] for e in state["evidence"]] == ["ok", "error"]
    assert "Not answered (error)" in chat.calls[1][1]["content"]


def test_a_planner_that_declines_ends_with_could_not_answer():
    chat = FakeChat('{"steps": [], "reason": "no weather data"}')
    graph, execute = make(chat=chat, retriever=FakeRetriever())
    state = run(graph, "why are temperatures so high in Mumbai")
    assert state["outcome"]["errors"] == [f"{COULD_NOT_ANSWER} no weather data"]
    assert execute.calls == []


def test_parse_plan_is_lenient_about_wrapping():
    reply = 'Here you go:\n```json\n{"steps": [{"purpose": "p", "dsl": "SHOW  value"}, {"x": 1}]}\n```'
    assert inv.parse_plan(reply)["steps"] == [{"purpose": "p", "dsl": "SHOW value"}]
    assert inv.parse_plan("no json")["steps"] == []


def test_entities_need_an_exact_name_or_alias():
    layer, indexes = SemanticLayer.load(), FakeRegistry().as_mapping()
    assert inv.find_entities("is SBI worse than HDFC Bank?", layer, indexes) == [
        {"field": "issuer", "value": "HDFC Bank", "matched": "hdfc bank"},
        {"field": "issuer", "value": "State Bank of India", "matched": "sbi"},
    ]
    assert inv.find_entities("why is it so high in Indore", layer, indexes) == []


# --- budget ---------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_budget_window_slides():
    clock = Clock()
    budget = TokenBudget(1000, headroom=1.0, clock=clock)
    assert budget.try_spend("m", 700)
    assert not budget.try_spend("m", 400)
    assert budget.retry_after("m", 400) == 60
    clock.now = 61
    assert budget.try_spend("m", 400)


def test_an_investigation_over_budget_is_refused_before_any_call():
    chat = FakeChat()
    graph, execute = make(chat=chat, retriever=FakeRetriever(), budget=TokenBudget(1000))
    state = run(graph, "why is HDFC Bank's business decline rate so high")
    assert state["http_status"] == 429 and state["outcome"]["stage"] == "budget"
    assert chat.calls == [] and execute.calls == []


# --- fixes from the first real run ---------------------------------------------------

from agent.nodes import normalize_citations  # noqa: E402


@pytest.mark.parametrize("planned, fixed", [
    # the step that failed in the first real run: PERIOD after LIMIT
    ("SHOW volume BY response WHERE issuer = 'HDFC Bank' AND status != 'Success' "
     "ORDER BY volume DESC LIMIT 10 PERIOD YTD",
     "SHOW volume BY response WHERE issuer = 'HDFC Bank' AND status != 'Success' "
     "PERIOD YTD ORDER BY volume DESC LIMIT 10"),
    ("SHOW value IN LAKH BY issuer", "SHOW value BY issuer IN LAKH"),
    ("SHOW value WHERE card_type IN ('Credit', 'Debit') BY issuer",       # IN (...) is not a unit
     "SHOW value BY issuer WHERE card_type IN ('Credit', 'Debit')"),
    ("SHOW value BY issuer PERIOD MTD", "SHOW value BY issuer PERIOD MTD"),  # already right
    ("SHOW value PERIOD MTD PERIOD YTD", "SHOW value PERIOD MTD PERIOD YTD"),  # repeated: left alone
])
def test_clauses_are_put_back_in_grammar_order(planned, fixed):
    assert inv.reorder_clauses(planned) == fixed


def test_a_step_out_of_order_still_runs_and_records_what_was_planned():
    chat = FakeChat('{"steps": [{"purpose": "a", "dsl": "SHOW business_decline_rate BY issuer '
                    'ORDER BY business_decline_rate DESC LIMIT 5 PERIOD MTD"}]}', "Answer.")
    graph, _ = make(chat=chat, retriever=FakeRetriever(), rows=PEERS_HIGH)
    [step] = run(graph, "why is HDFC Bank's business decline rate so high")["evidence"]
    assert step["status"] == "ok"
    assert step["dsl"].endswith("PERIOD MTD ORDER BY business_decline_rate DESC LIMIT 5")
    assert step["dsl_planned"].endswith("LIMIT 5 PERIOD MTD")


def test_the_planner_is_told_the_metric_order_and_period_rules():
    assert "PERIOD always comes before ORDER BY" in inv.PLANNER_INSTRUCTIONS
    assert "business_decline_rate" in inv.PLANNER_INSTRUCTIONS
    assert "write no PERIOD at all" in inv.PLANNER_INSTRUCTIONS


def test_citation_markers_are_normalized_to_the_passages_given():
    # a non-breaking hyphen, a bare document id, and something that isn't a citation
    text = "High [kb\u201107 > Reasoning sequence]. Method [kb-07]. Rate [0.12]. Other [kb-99]."
    fixed = normalize_citations(text, [PLAYBOOK])
    assert fixed == ("High [kb-07 > Reasoning sequence]. Method [kb-07 > Reasoning sequence]. "
                     "Rate [0.12]. Other [kb-99].")


def test_synthesis_citations_survive_a_drifting_hyphen():
    chat = FakeChat(plan_json(1), "Above peers [kb\u201107 > Reasoning sequence].")
    graph, _ = make(chat=chat, retriever=FakeRetriever(), rows=PEERS_HIGH)
    outcome = run(graph, "why is HDFC Bank's business decline rate so high")["outcome"]
    assert outcome["text"] == "Above peers [kb-07 > Reasoning sequence]."
    assert outcome["citations"] == ["kb-07 > Reasoning sequence"]


# --- fixes from the second real run ---------------------------------------------------

def test_rates_reach_the_synthesizer_as_percentages():
    text = inv.render_evidence([{"purpose": "p", "dsl": "SHOW business_decline_rate", "status": "ok",
                                 "rows": [{"business_decline_rate": 0.08, "volume": 12}], "row_count": 1}])
    assert '"business_decline_rate": "8.00%"' in text and '"volume": 12' in text


def test_a_cut_short_heading_links_only_when_it_is_unambiguous():
    other = Passage(chunk_id="kb-07#4", text="x", doc_id="kb-07", heading="Reading common decline profiles",
                    scope="reasoning playbook", confidence="high", score=0.02, distance=0.3,
                    matched_child="kb-07#4.1")
    assert normalize_citations("[kb-07 > Reasoning]", [PLAYBOOK, other]) == "[kb-07 > Reasoning sequence]"
    assert normalize_citations("[kb-07 > Re]", [PLAYBOOK, other]) == "[kb-07 > Re]"   # two match: leave it


def test_the_planner_is_told_to_keep_breakdowns_to_the_decline_class():
    assert "WHERE status = 'Business Decline'" in inv.PLANNER_INSTRUCTIONS


# --- fixes from the third real run ----------------------------------------------------

@pytest.mark.parametrize("message, metric", [
    ("why is the business decline rate for hdfc so high", "business_decline_rate"),
    ("why is HDFC's decline rate so high", "decline_rate"),
    ("why did SBI's success rate drop", "success_rate"),
    ("why is HDFC Bank so bad", None),
])
def test_the_metric_the_question_names_is_found_longest_first(message, metric):
    found = inv.find_metric(message, SemanticLayer.load())
    assert (found or {}).get("metric") == metric


def test_a_plan_that_copies_the_playbook_metric_gets_its_first_step_rebuilt():
    chat = FakeChat('{"steps": [{"purpose": "peers", "dsl": "SHOW decline_rate BY issuer PERIOD MTD"}]}',
                    "Answer.")
    graph, _ = make(chat=chat, retriever=FakeRetriever(), rows=PEERS_HIGH)
    state = run(graph, "why is the business decline rate for HDFC Bank so high")
    assert state["focus_metric"]["metric"] == "business_decline_rate"
    assert state["plan"][0]["dsl"] == "SHOW business_decline_rate BY issuer PERIOD MTD"   # period kept
    assert "rebuilt" in state["plan_note"]
    assert "Metric the question asks about: business_decline_rate" in chat.calls[0][1]["content"]


def test_comparisons_are_computed_not_left_to_the_model():
    step = {"status": "ok", "rows": PEERS_HIGH, "row_count": 3, "dimension_columns": ["iss_name"],
            "metric_columns": ["business_decline_rate"]}
    [line] = inv.entity_comparisons(step, ["HDFC Bank"])
    assert line == ("HDFC Bank: business_decline_rate 20.00%, rank 1 of 3 (1 = highest); "
                    "peer average 5.50%; above it by 14.50 percentage points")
    values = {**step, "metric_columns": ["decline_value"],
              "rows": [{"iss_name": "HDFC Bank", "decline_value": 3.168},
                       {"iss_name": "SBI", "decline_value": 3.392}, {"iss_name": "HSBC", "decline_value": 2.104}]}
    assert "rank 2 of 3" in inv.entity_comparisons(values, ["HDFC Bank"])[0]   # the run that said "higher"
    assert inv.entity_comparisons({**step, "row_count": 9}, ["HDFC Bank"]) == []   # partial result: no rank


def test_the_synthesizer_gets_the_computed_lines():
    chat = FakeChat(plan_json(1), "Answer.")
    graph, _ = make(chat=chat, retriever=FakeRetriever(), rows=PEERS_HIGH)
    run(graph, "why is HDFC Bank's business decline rate so high")
    assert "Computed: HDFC Bank: business_decline_rate 20.00%, rank 1 of 3" in chat.calls[1][1]["content"]


def test_breakdowns_are_narrowed_to_the_decline_class_asked_about():
    steps = [{"purpose": "codes", "dsl": "SHOW volume BY response WHERE issuer = 'HDFC Bank' "
                                         "AND status != 'Success' ORDER BY volume DESC LIMIT 10"}]
    note = inv.narrow_decline_class(steps, {"metric": "business_decline_rate"})
    assert "status = 'Business Decline'" in steps[0]["dsl"] and note.startswith("1 step(s)")
    mixed = [{"purpose": "codes", "dsl": "SHOW volume BY response WHERE status != 'Success'"}]
    assert inv.narrow_decline_class(mixed, {"metric": "decline_rate"}) is None      # all declines: keep
    assert mixed[0]["dsl"].endswith("status != 'Success'")
