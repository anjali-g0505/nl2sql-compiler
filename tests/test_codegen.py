"""Codegen tests: resolved queries -> SQL + assumptions.

The E1-E19 examples are read straight out of grammar.md, so the SQL there really is
the definition of "correct": if codegen or the file drift apart, these fail.
"""
import re
from datetime import date
from pathlib import Path

import pytest

from compiler.ast import Assumption
from compiler.codegen import CodegenError, generate, period_bounds, render_assumptions
from compiler.parser import parse
from compiler.resolver import build_index, resolve
from compiler.semantic_layer import SemanticLayer
from compiler.validator import validate

REF = date(2025, 12, 27)  # the reference date grammar.md assumes (a Saturday)
GRAMMAR = Path(__file__).resolve().parent.parent / "grammar.md"


def compile_dsl(dsl, reference_date=REF, indexes=None):
    return generate(resolve(validate(parse(dsl)), indexes=indexes or {}), reference_date)


def keys(compiled):
    return [a.key for a in compiled.assumptions]


def oracle_examples():
    text = GRAMMAR.read_text(encoding="utf-8")
    found = re.findall(
        r"\*\*(E\d+) .*?```\nDSL:\s+(.*?)\n```\n```sql\n(.*?)\n```\n(.*?)\n", text, re.S
    )
    examples = []
    for name, dsl, sql, meta in found:
        listed = re.search(r"assumptions: \[([^\]]*)\]", meta).group(1)
        assumptions = [a.strip() for a in listed.split(",") if a.strip()]
        examples.append(pytest.param(dsl, sql, assumptions, id=name))
    return examples


# --- the grammar.md oracle ----------------------------------------------------

def test_every_oracle_example_is_found():
    assert len(oracle_examples()) == 21


@pytest.mark.parametrize("dsl, sql, assumptions", oracle_examples())
def test_oracle_sql_is_reproduced_exactly(dsl, sql, assumptions):
    compiled = compile_dsl(dsl)
    assert compiled.sql == sql
    assert keys(compiled) == assumptions


@pytest.mark.parametrize("dsl, sql, assumptions", oracle_examples())
def test_every_emitted_assumption_renders_from_config(dsl, sql, assumptions):
    compiled = compile_dsl(dsl)
    rendered = render_assumptions(compiled)
    assert len(rendered) == len(compiled.assumptions)
    assert not any("{" in text for text in rendered)  # every placeholder was filled


def test_chart_type_and_dsl_are_carried_through():
    compiled = compile_dsl("SHOW value BY issuer AS PIE")
    assert compiled.chart_type == "PIE"
    assert compiled.dsl == "SHOW value BY issuer AS PIE"


# --- determinism --------------------------------------------------------------

def test_the_same_query_written_two_ways_gives_identical_sql():
    a = compile_dsl("SHOW VALUE BY Category WHERE card_type = 'credit'")
    b = compile_dsl("SHOW value BY mcc WHERE card_type = 'Credit'")
    assert a.sql == b.sql
    assert a.executable_sql == b.executable_sql


def test_no_limit_is_invented():
    # the forced-limit guardrail is applied at execution, never in the SQL
    assert "LIMIT" not in compile_dsl("SHOW value BY txn").sql
    assert compile_dsl("SHOW value BY txn LIMIT 50").sql.endswith("LIMIT 50;")


# --- values: display form vs executable form ----------------------------------

def test_text_values_become_placeholders_for_execution():
    compiled = compile_dsl("SHOW volume BY month WHERE card_type = 'Credit'")
    assert "b.card_type = 'Credit'" in compiled.sql
    assert "b.card_type = %s" in compiled.executable_sql
    assert compiled.params == ("Credit",)


def test_params_follow_the_order_of_the_placeholders():
    compiled = compile_dsl(
        "SHOW volume WHERE card_type IN ('Credit', 'Debit') AND card_variant != 'Classic'"
    )
    assert "b.card_type IN (%s, %s)" in compiled.executable_sql
    assert "b.card_variant <> %s" in compiled.executable_sql
    assert compiled.params == ("Credit", "Debit", "Classic")


def test_quotes_in_a_value_cannot_break_out_of_the_literal():
    # the DSL can't hold a quote inside a string, but a database name can
    index = build_index([("M1", "McDonald's"), ("M2", "x' OR '1'='1")])
    compiled = compile_dsl(
        "SHOW volume WHERE merchant IN ('M1', 'M2')", indexes={"merchant": index}
    )
    assert compiled.params == ("McDonald's", "x' OR '1'='1")
    assert "m.name IN (%s, %s)" in compiled.executable_sql
    assert "m.name IN ('McDonald''s', 'x'' OR ''1''=''1')" in compiled.sql  # escaped for display


def test_numbers_and_dates_are_written_in_not_parameterized():
    compiled = compile_dsl("SHOW value WHERE amount > 5 PERIOD FROM '2025-05-12' TO '2025-06-14'")
    assert compiled.params == ()
    assert compiled.sql == compiled.executable_sql


# --- metrics built from measures ----------------------------------------------

def test_mixed_metrics_push_the_condition_into_every_measure():
    compiled = compile_dsl("SHOW active_cards, spend_per_card, spend_per_customer")
    assert "COUNT(DISTINCT t.card_id) AS active_cards" in compiled.sql  # untouched
    assert ("SUM(CASE WHEN r.TD_BD = 'Success' THEN t.amt ELSE 0 END)\n"
            "         / NULLIF(COUNT(DISTINCT CASE WHEN r.TD_BD = 'Success' THEN t.card_id END),0)"
            " AS spend_per_card") in compiled.sql
    assert "COUNT(DISTINCT CASE WHEN r.TD_BD = 'Success' THEN t.customer_id END)" in compiled.sql
    assert "WHERE" not in compiled.sql
    assert keys(compiled) == ["mixed_metrics"]


def test_a_measure_with_its_own_filter():
    compiled = compile_dsl("SHOW business_decline_rate")
    assert compiled.sql == (  # 101 characters on one line, so the ratio wraps
        "SELECT SUM(CASE WHEN r.TD_BD = 'Business Decline' THEN 1 ELSE 0 END)\n"
        "         / NULLIF(COUNT(*),0) AS business_decline_rate\n"
        "FROM card_txns t\n"
        "JOIN response_master r ON t.response_code = r.response_code;"  # the measure's join
    )


def test_unit_divides_only_scalable_metrics():
    compiled = compile_dsl("SHOW volume, value BY issuer IN LAKH")
    assert "COUNT(*) AS volume" in compiled.sql
    assert "SUM(CASE WHEN r.TD_BD = 'Success' THEN t.amt ELSE 0 END) / 100000 AS value" in compiled.sql


# --- WHERE --------------------------------------------------------------------

def test_money_threshold_is_converted_back_to_rupees():
    compiled = compile_dsl("SHOW value BY txn WHERE amount >= 1.5 IN LAKH")
    assert "WHERE t.amt >= 150000\n" in compiled.sql


def test_filter_by_month_uses_its_filter_column():
    compiled = compile_dsl("SHOW volume BY issuer WHERE month = '2025-03'")
    assert "WHERE SUBSTRING(t.date,1,7) = '2025-03'" in compiled.sql


def test_catalog_filter_compares_the_code():
    compiled = compile_dsl("SHOW volume WHERE response = '05'")
    assert "WHERE r.response_code = '05'" in compiled.sql


# --- ORDER BY -----------------------------------------------------------------

@pytest.mark.parametrize("dsl, expected", [
    ("SHOW value BY issuer ORDER BY issuer ASC", "ORDER BY i.iss_name ASC"),
    ("SHOW value BY mcc ORDER BY mcc", "ORDER BY m.mcc_code DESC, m.mcc_description DESC"),
    ("SHOW value BY month ORDER BY month DESC", "ORDER BY month DESC"),   # explicit wins
    ("SHOW value BY month ORDER BY value DESC", "ORDER BY value DESC"),
    ("SHOW value BY issuer, month", "ORDER BY month ASC"),              # time default
])
def test_order_by(dsl, expected):
    lines = compile_dsl(dsl).sql.rstrip(";").split("\n")
    assert [l for l in lines if l.startswith("ORDER BY")] == [expected]


def test_no_order_by_without_a_time_dimension():
    assert "ORDER BY" not in compile_dsl("SHOW value BY issuer").sql


# --- HAVING -------------------------------------------------------------------

def test_several_having_conditions():
    compiled = compile_dsl("SHOW value BY merchant HAVING value > 3 AND value != 5 IN CRORE")
    assert "HAVING value > 3\n  AND value <> 5;" in compiled.sql


# --- periods ------------------------------------------------------------------

@pytest.mark.parametrize("dsl, start, end", [
    ("PERIOD FTD", "2025-12-27", "2025-12-27"),
    ("PERIOD WTD", "2025-12-22", "2025-12-27"),        # Monday of the reference week
    ("PERIOD MTD", "2025-12-01", "2025-12-27"),
    ("PERIOD QTD", "2025-10-01", "2025-12-27"),
    ("PERIOD YTD", "2025-01-01", "2025-12-27"),
    ("PERIOD LAST 1 DAYS", "2025-12-27", "2025-12-27"),  # == FTD
    ("PERIOD LAST 7 DAYS", "2025-12-21", "2025-12-27"),
])
def test_period_windows(dsl, start, end):
    compiled = compile_dsl(f"SHOW volume {dsl}")
    assert f"t.date >= '{start}' AND t.date <= '{end}'" in compiled.sql


def test_relative_period_needs_a_reference_date():
    with pytest.raises(CodegenError):
        compile_dsl("SHOW volume PERIOD MTD", reference_date=None)


def test_explicit_range_needs_no_reference_date():
    compiled = compile_dsl("SHOW volume PERIOD FROM '2025-05-12' TO '2025-06-14'", reference_date=None)
    assert "t.date >= '2025-05-12' AND t.date <= '2025-06-14'" in compiled.sql


def test_period_bounds_for_a_quarter_start():
    period = parse("SHOW volume PERIOD QTD").period
    assert period_bounds(period, date(2025, 4, 1)) == (date(2025, 4, 1), date(2025, 4, 1))


# --- assumptions --------------------------------------------------------------

def test_assumptions_carry_their_values():
    compiled = compile_dsl("SHOW active_card_rate PERIOD LAST 90 DAYS")
    assert compiled.assumptions[1] == Assumption("last_n_days_window", (
        ("n", "90"), ("ref", "2025-12-27"), ("n_minus_1", "89"), ("start", "2025-09-29"),
    ))
    assert render_assumptions(compiled)[1] == (
        "Last 90 days = 2025-12-27 (treated as today) plus the 89 days before it: "
        "2025-09-29 to 2025-12-27."
    )


def test_corrected_values_are_reported_last():
    compiled = compile_dsl("SHOW value WHERE card_type = 'Credt'")
    assert compiled.params == ("Credit",)
    assert compiled.assumptions[-1] == Assumption("value_corrected", (
        ("raw", "Credt"), ("resolved", "Credit"), ("field", "card_type"),
    ))
    assert keys(compiled) == ["success_default", "value_corrected"]


def test_every_emittable_assumption_exists_in_config():
    templates = SemanticLayer.load().assumptions
    emittable = {"success_default", "mixed_metrics", "unit_crore", "unit_lakh",
                 "period_anchor", "last_n_days_window", "date_range_inclusive",
                 "active_card_denom", "value_corrected"}
    assert emittable <= set(templates)


# --- refusing to compile ------------------------------------------------------

def test_unresolved_values_are_refused():
    resolved = resolve(validate(parse("SHOW volume WHERE card_type = 'Gold'")), indexes={})
    with pytest.raises(CodegenError) as exc:
        generate(resolved, REF)
    assert "card_type = 'Gold'" in str(exc.value)


# --- LAST n MONTHS ------------------------------------------------------------

@pytest.mark.parametrize("ref, n, start, end", [
    (date(2025, 12, 27), 5, "2025-08-01", "2025-12-27"),   # E21
    (date(2025, 12, 31), 1, "2025-12-01", "2025-12-31"),   # this month so far == MTD
    (date(2025, 2, 15), 5, "2024-10-01", "2025-02-15"),    # crosses the year end
    (date(2026, 1, 10), 13, "2025-01-01", "2026-01-10"),   # more than a year
])
def test_last_n_months_covers_whole_calendar_months(ref, n, start, end):
    compiled = compile_dsl(f"SHOW volume PERIOD LAST {n} MONTHS", reference_date=ref)
    assert f"t.date >= '{start}' AND t.date <= '{end}'" in compiled.sql


def test_last_1_months_equals_month_to_date():
    months = compile_dsl("SHOW volume PERIOD LAST 1 MONTHS")
    mtd = compile_dsl("SHOW volume PERIOD MTD")
    assert months.sql == mtd.sql


def test_last_n_months_states_the_window():
    compiled = compile_dsl("SHOW volume PERIOD LAST 5 MONTHS")
    assert keys(compiled) == ["period_anchor", "last_n_months_window"]
    assert render_assumptions(compiled)[1] == (
        "Last 5 months = the calendar months August 2025 to December 2025 "
        "(2025-08-01 to 2025-12-27); the last one may be partial."
    )


def test_months_and_days_windows_differ():
    assert compile_dsl("SHOW volume PERIOD LAST 5 MONTHS").sql != \
           compile_dsl("SHOW volume PERIOD LAST 150 DAYS").sql
