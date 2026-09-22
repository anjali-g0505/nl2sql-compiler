"""Parser tests. DSL strings come from the grammar.md oracle (E1-E19).

The parser checks SYNTAX only: unknown names and unknown config values parse fine here
and are the validator's problem, so these tests assert shape, never meaning.
"""
from datetime import date
from decimal import Decimal

import pytest

from compiler.ast import Condition, MetricCondition, Name, Order, Period, QueryAST
from compiler.parser import ParseError, parse


def n(raw: str) -> Name:
    """A parse-time Name: canonical == raw, exactly what the parser produces."""
    return Name(raw=raw, canonical=raw)


# --- E1-E19 oracle strings ----------------------------------------------------

def test_e1_scalar():
    dsl = "SHOW value"
    assert parse(dsl) == QueryAST(metrics=(n("value"),), raw_dsl=dsl)


def test_e2_group_by():
    assert parse("SHOW value BY issuer").dimensions == (n("issuer"),)


def test_e4_filter():
    dsl = "SHOW volume BY month WHERE card_type = 'Credit'"
    assert parse(dsl) == QueryAST(
        metrics=(n("volume"),),
        dimensions=(n("month"),),
        filters=(Condition(n("card_type"), "=", "Credit"),),
        raw_dsl=dsl,
    )


def test_e5_top_n():
    q = parse("SHOW volume BY customer WHERE status = 'Business Decline' "
              "ORDER BY volume DESC LIMIT 1")
    assert q.order == Order(n("volume"), "DESC")
    assert q.limit == 1


def test_e6_two_dimensions():
    assert parse("SHOW value BY issuer, acquirer").dimensions == (n("issuer"), n("acquirer"))


def test_e7_chart_override():
    assert parse("SHOW value BY issuer AS PIE").chart_type == "PIE"


def test_e8_period_and_unit():
    q = parse("SHOW value BY card_type PERIOD MTD IN CRORE")
    assert q.period == Period("MTD")
    assert q.unit == "CRORE"


def test_e10_rolling_window():
    assert parse("SHOW active_card_rate PERIOD LAST 90 DAYS").period == Period(
        "LAST_N_DAYS", n=90
    )


def test_e14_multi_metric():
    assert parse("SHOW volume, value, ats BY country").metrics == (
        n("volume"), n("value"), n("ats"),
    )


def test_e15_having():
    q = parse("SHOW value BY customer HAVING value > 30000 ORDER BY value DESC")
    assert q.having == (MetricCondition(n("value"), ">", Decimal("30000")),)
    assert q.order == Order(n("value"), "DESC")


def test_e16_having_with_period_and_unit():
    q = parse("SHOW value BY merchant PERIOD MTD HAVING value > 3 IN CRORE")
    assert q.period == Period("MTD")
    assert q.having == (MetricCondition(n("value"), ">", Decimal("3")),)
    assert q.unit == "CRORE"


def test_e17_explicit_date_range():
    q = parse("SHOW value PERIOD FROM '2025-05-12' TO '2025-06-14'")
    assert q.period == Period("RANGE", start=date(2025, 5, 12), end=date(2025, 6, 14))


def test_e18_numeric_where():
    q = parse("SHOW value BY txn WHERE amount > 100000000 AS TABLE")
    assert q.filters == (Condition(n("amount"), ">", Decimal("100000000")),)
    assert q.chart_type == "TABLE"


def test_e19_in_list_and_not_equal():
    q = parse("SHOW volume BY issuer WHERE card_type IN ('Credit', 'Debit') "
              "AND status != 'Success'")
    assert q.filters == (
        Condition(n("card_type"), "IN", ("Credit", "Debit")),
        Condition(n("status"), "!=", "Success"),
    )


def test_every_clause_at_once():
    dsl = ("SHOW value, volume BY merchant, issuer WHERE card_type = 'Credit' "
           "AND amount >= 500 PERIOD LAST 7 DAYS HAVING value > 1000 "
           "ORDER BY value ASC LIMIT 25 IN LAKH AS BAR")
    assert parse(dsl) == QueryAST(
        metrics=(n("value"), n("volume")),
        dimensions=(n("merchant"), n("issuer")),
        filters=(
            Condition(n("card_type"), "=", "Credit"),
            Condition(n("amount"), ">=", Decimal("500")),
        ),
        period=Period("LAST_N_DAYS", n=7),
        having=(MetricCondition(n("value"), ">", Decimal("1000")),),
        order=Order(n("value"), "ASC"),
        limit=25,
        unit="LAKH",
        chart_type="BAR",
        raw_dsl=dsl,
    )


# --- Parsing rules ------------------------------------------------------------

def test_order_direction_defaults_to_desc():
    assert parse("SHOW value BY issuer ORDER BY value").order == Order(n("value"), "DESC")


def test_order_direction_is_case_insensitive():
    assert parse("SHOW value BY issuer order by value asc").order.direction == "ASC"


def test_no_order_clause_means_no_order():
    # no orphan direction: the whole clause is absent, not a key of None beside "DESC"
    assert parse("SHOW value BY issuer").order is None


def test_config_values_are_uppercased_but_names_keep_case():
    q = parse("SHOW Spend_Per_Card BY Issuer PERIOD mtd IN crore AS pie")
    assert q.metrics[0].raw == "Spend_Per_Card"
    assert q.dimensions[0].raw == "Issuer"
    assert (q.period.kind, q.unit, q.chart_type) == ("MTD", "CRORE", "PIE")


def test_names_are_unresolved_at_parse_time():
    name = parse("SHOW volume BY category").dimensions[0]
    assert (name.raw, name.canonical) == ("category", "category")  # validator resolves


def test_unknown_names_and_values_still_parse():
    # semantics are the validator's job: nothing here is checked against config
    q = parse("SHOW nonsense BY nonsense_dimension PERIOD NEXTYEAR IN TONNES AS DONUT")
    assert q.metrics == (n("nonsense"),)
    assert (q.period.kind, q.unit, q.chart_type) == ("NEXTYEAR", "TONNES", "DONUT")


def test_duplicates_are_preserved_for_the_validator():
    q = parse("SHOW value, value BY issuer, issuer WHERE x = 'a' AND x = 'a'")
    assert len(q.metrics) == len(q.dimensions) == len(q.filters) == 2


def test_raw_dsl_is_kept_verbatim():
    dsl = "  SHOW   value\tBY issuer  "
    assert parse(dsl).raw_dsl == dsl


def test_integers_and_decimals_become_exact_decimals():
    q = parse("SHOW success_rate BY issuer HAVING success_rate < 0.9 AND volume >= 100")
    assert [str(h.value) for h in q.having] == ["0.9", "100"]
    assert all(isinstance(h.value, Decimal) for h in q.having)


def test_not_in_with_one_value():
    q = parse("SHOW volume WHERE card_type NOT IN ('Prepaid')")
    assert q.filters == (Condition(n("card_type"), "NOT IN", ("Prepaid",)),)


def test_single_day_range():
    q = parse("SHOW value PERIOD FROM '2025-05-12' TO '2025-05-12'")
    assert q.period.start == q.period.end == date(2025, 5, 12)


# --- Errors -------------------------------------------------------------------

def test_query_must_start_with_show():
    with pytest.raises(ParseError) as exc:
        parse("BY issuer")
    assert exc.value.position == 0
    assert "Expected SHOW" in str(exc.value)


def test_empty_input_raises():
    with pytest.raises(ParseError):
        parse("")


def test_metric_list_cannot_be_empty():
    with pytest.raises(ParseError) as exc:
        parse("SHOW BY issuer")
    assert "a metric name" in str(exc.value)


def test_trailing_comma_in_a_list_raises():
    with pytest.raises(ParseError):
        parse("SHOW value, BY issuer")


def test_clause_out_of_order_names_the_clause():
    with pytest.raises(ParseError) as exc:
        parse("SHOW value LIMIT 10 ORDER BY value")
    assert "ORDER BY clause is out of order" in str(exc.value)


def test_duplicate_clause_raises():
    with pytest.raises(ParseError) as exc:
        parse("SHOW value WHERE a = 'x' WHERE b = 'y'")
    assert "Duplicate WHERE clause" in str(exc.value)


def test_missing_by_after_order():
    with pytest.raises(ParseError) as exc:
        parse("SHOW value ORDER value")
    assert "Expected BY after ORDER" in str(exc.value)


@pytest.mark.parametrize("dsl", [
    "SHOW value WHERE card_type",              # no operator
    "SHOW value WHERE card_type = ",           # no value
    "SHOW value WHERE card_type = Credit",     # unquoted text
    "SHOW value WHERE = 'Credit'",             # no field
])
def test_malformed_condition_raises(dsl):
    with pytest.raises(ParseError):
        parse(dsl)


def test_ordering_operator_on_text_raises():
    with pytest.raises(ParseError) as exc:
        parse("SHOW value WHERE card_type > 'Credit'")
    assert "'='" in str(exc.value)  # the AST's own rule, re-raised with a position


@pytest.mark.parametrize("dsl", [
    "SHOW value WHERE card_type IN ()",                  # empty list
    "SHOW value WHERE card_type IN ('a',)",              # trailing comma
    "SHOW value WHERE card_type IN 'a'",                 # no parens
    "SHOW value WHERE card_type IN (1, 2)",              # numbers in a text list
    "SHOW value WHERE card_type NOT ('a')",              # NOT without IN
])
def test_malformed_in_list_raises(dsl):
    with pytest.raises(ParseError):
        parse(dsl)


def test_having_needs_a_number():
    with pytest.raises(ParseError) as exc:
        parse("SHOW value BY issuer HAVING value > 'big'")
    assert "a number" in str(exc.value)


@pytest.mark.parametrize("dsl, message", [
    ("SHOW value LIMIT 0", "LIMIT must be at least 1"),
    ("SHOW value PERIOD LAST 0 DAYS", "LAST needs at least 1 day"),
])
def test_non_positive_counts_raise(dsl, message):
    with pytest.raises(ParseError) as exc:
        parse(dsl)
    assert message in str(exc.value)


def test_limit_rejects_a_decimal():
    with pytest.raises(ParseError) as exc:
        parse("SHOW value LIMIT 2.5")
    assert "a whole number" in str(exc.value)


def test_last_n_days_needs_the_days_keyword():
    with pytest.raises(ParseError) as exc:
        parse("SHOW value PERIOD LAST 90")
    assert "Expected DAYS" in str(exc.value)


@pytest.mark.parametrize("bad_date, message", [
    ("2025-5-12", "not in 'YYYY-MM-DD' form"),
    ("12/05/2025", "not in 'YYYY-MM-DD' form"),
    ("2025-05-12 OR 1=1", "not in 'YYYY-MM-DD' form"),   # injection attempt
    ("2025-02-30", "not a real calendar date"),
])
def test_bad_dates_raise(bad_date, message):
    with pytest.raises(ParseError) as exc:
        parse(f"SHOW value PERIOD FROM '{bad_date}' TO '2025-06-14'")
    assert message in str(exc.value)


def test_reversed_date_range_raises():
    with pytest.raises(ParseError) as exc:
        parse("SHOW value PERIOD FROM '2025-06-14' TO '2025-05-12'")
    assert "is after end" in str(exc.value)


def test_unquoted_date_raises():
    with pytest.raises(ParseError) as exc:
        parse("SHOW value PERIOD FROM 2025 TO 2026")
    assert "quoted date" in str(exc.value)


def test_errors_point_at_the_offending_token():
    dsl = "SHOW value BY issuer LIMIT big"
    with pytest.raises(ParseError) as exc:
        parse(dsl)
    assert exc.value.position == dsl.index("big")
    assert exc.value.token.value == "big"


# --- LAST n MONTHS ------------------------------------------------------------

def test_last_n_months_is_its_own_period_kind():
    period = parse("SHOW value PERIOD LAST 5 MONTHS").period
    assert (period.kind, period.n) == ("LAST_N_MONTHS", 5)


@pytest.mark.parametrize("dsl, message", [
    ("SHOW value PERIOD LAST 5 YEARS", "Expected DAYS or MONTHS"),
    ("SHOW value PERIOD LAST 5", "Expected DAYS or MONTHS"),
    ("SHOW value PERIOD LAST 0 MONTHS", "at least 1 day or month"),
    ("SHOW value PERIOD LAST MONTHS", "a whole number of days or months"),
])
def test_bad_last_periods_are_rejected(dsl, message):
    with pytest.raises(ParseError) as exc:
        parse(dsl)
    assert message in str(exc.value)
