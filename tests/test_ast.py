"""AST type tests. Queries are built by hand: there is no parser here, only shapes."""
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pytest

from compiler.ast import (
    Assumption,
    CompiledQuery,
    Condition,
    MetricCondition,
    Name,
    Order,
    Period,
    QueryAST,
    ValidatedQuery,
)


def n(raw: str) -> Name:
    """A parse-time Name: canonical == raw, as the parser will produce."""
    return Name(raw=raw, canonical=raw)


# --- QueryAST shapes from grammar.md ------------------------------------------

def test_metrics_only_everything_else_default():
    dsl = "SHOW value"
    q = QueryAST(metrics=(n("value"),), raw_dsl=dsl)

    assert q.metrics == (Name("value", "value"),)
    assert q.dimensions == ()
    assert q.filters == ()
    assert q.period is None
    assert q.having == ()
    assert q.order is None
    assert q.limit is None
    assert q.unit is None
    assert q.chart_type is None
    assert q.raw_dsl == dsl


def test_multiple_dimensions_keep_order():
    q = QueryAST(
        metrics=(n("value"),),
        dimensions=(n("issuer"), n("acquirer")),
        raw_dsl="SHOW value BY issuer, acquirer",
    )

    assert [d.raw for d in q.dimensions] == ["issuer", "acquirer"]
    assert q.filters == ()


def test_filter_leaves_order_by_unset():
    q = QueryAST(
        metrics=(n("volume"),),
        dimensions=(n("month"),),
        filters=(Condition(field=n("card_type"), op="=", value="Credit"),),
        raw_dsl="SHOW volume BY month WHERE card_type = 'Credit'",
    )

    assert q.filters == (Condition(Name("card_type", "card_type"), "=", "Credit"),)
    assert q.filters[0].value == "Credit"  # no surrounding quotes
    # month's chronological default ordering is codegen's job, not recorded here
    assert q.order is None


def test_full_clause_set():
    q = QueryAST(
        metrics=(n("value"),),
        dimensions=(n("merchant"),),
        period=Period("MTD"),
        order=Order(n("value"), "DESC"),
        limit=25,
        unit="CRORE",
        raw_dsl="SHOW value BY merchant PERIOD MTD ORDER BY value DESC LIMIT 25 IN CRORE",
    )

    assert q.period == Period(kind="MTD", n=None)
    assert q.order == Order(Name("value", "value"), "DESC")
    assert q.limit == 25
    assert q.unit == "CRORE"
    assert q.chart_type is None


def test_last_n_days_period():
    q = QueryAST(
        metrics=(n("active_card_rate"),),
        period=Period(kind="LAST_N_DAYS", n=90),
        raw_dsl="SHOW active_card_rate PERIOD LAST 90 DAYS",
    )

    assert q.period.kind == "LAST_N_DAYS"
    assert q.period.n == 90


def test_alias_name_keeps_raw_and_canonical():
    category = Name(raw="category", canonical="mcc")  # as the validator would resolve it
    q = QueryAST(
        metrics=(n("volume"),),
        dimensions=(category,),
        raw_dsl="SHOW volume BY category",
    )

    assert q.dimensions[0].raw == "category"
    assert q.dimensions[0].canonical == "mcc"


def test_having_threshold_e15():
    q = QueryAST(
        metrics=(n("value"),),
        dimensions=(n("customer"),),
        having=(MetricCondition(metric=n("value"), op=">", value=Decimal("30000")),),
        order=Order(n("value"), "DESC"),
        raw_dsl="SHOW value BY customer HAVING value > 30000 ORDER BY value DESC",
    )

    assert q.having == (MetricCondition(Name("value", "value"), ">", Decimal("30000")),)
    assert q.filters == ()  # a metric threshold is HAVING, never WHERE
    assert q.limit is None


def test_having_threshold_is_in_the_display_unit_e16():
    # "more than 3 crore" is written as 3 with IN CRORE; the AST keeps the pair as typed
    q = QueryAST(
        metrics=(n("value"),),
        dimensions=(n("merchant"),),
        period=Period("MTD"),
        having=(MetricCondition(n("value"), ">", Decimal("3")),),
        unit="CRORE",
        raw_dsl="SHOW value BY merchant PERIOD MTD HAVING value > 3 IN CRORE",
    )

    assert q.having[0].value == Decimal("3")
    assert q.unit == "CRORE"


def test_multiple_having_conditions_with_decimal():
    q = QueryAST(
        metrics=(n("volume"), n("success_rate")),
        dimensions=(n("issuer"),),
        having=(
            MetricCondition(n("volume"), ">=", Decimal("100")),
            MetricCondition(n("success_rate"), "<", Decimal("0.9")),
        ),
    )

    assert [(h.metric.raw, h.op) for h in q.having] == [("volume", ">="), ("success_rate", "<")]
    assert str(q.having[1].value) == "0.9"


@pytest.mark.parametrize("op", ["=", "!=", ">", ">=", "<", "<="])
def test_metric_condition_accepts_every_operator(op):
    assert MetricCondition(n("value"), op, Decimal("1")).op == op


@pytest.mark.parametrize("op", ["<>", "==", "=>", "GT", "", "IN", "NOT IN"])
def test_metric_condition_rejects_unknown_operator(op):
    with pytest.raises(ValueError):
        MetricCondition(n("value"), op, Decimal("1"))


def test_explicit_date_range_e17():
    q = QueryAST(
        metrics=(n("value"),),
        period=Period("RANGE", start=date(2025, 5, 12), end=date(2025, 6, 14)),
        raw_dsl="SHOW value PERIOD FROM '2025-05-12' TO '2025-06-14'",
    )

    assert q.period.kind == "RANGE"
    assert (q.period.start, q.period.end) == (date(2025, 5, 12), date(2025, 6, 14))
    assert q.period.n is None
    assert q.period.start.isoformat() == "2025-05-12"  # what codegen will emit


def test_numeric_attribute_filter_e18():
    q = QueryAST(
        metrics=(n("value"),),
        dimensions=(n("txn"),),
        filters=(Condition(n("amount"), ">", Decimal("100000000")),),
        chart_type="TABLE",
        raw_dsl="SHOW value BY txn WHERE amount > 100000000 AS TABLE",
    )

    assert q.filters == (Condition(Name("amount", "amount"), ">", Decimal("100000000")),)
    assert q.having == ()  # a per-row threshold is WHERE, never HAVING


def test_text_and_numeric_conditions_mix_in_where():
    q = QueryAST(
        metrics=(n("volume"),),
        filters=(
            Condition(n("card_type"), "=", "Credit"),
            Condition(n("amount"), "<=", Decimal("5000.50")),
        ),
    )

    assert [type(c.value) for c in q.filters] == [str, Decimal]
    assert str(q.filters[1].value) == "5000.50"


def test_in_list_and_not_equal_e19():
    q = QueryAST(
        metrics=(n("volume"),),
        dimensions=(n("issuer"),),
        filters=(
            Condition(n("card_type"), "IN", ("Credit", "Debit")),
            Condition(n("status"), "!=", "Success"),
        ),
        raw_dsl="SHOW volume BY issuer WHERE card_type IN ('Credit', 'Debit') AND status != 'Success'",
    )

    assert q.filters[0].value == ("Credit", "Debit")  # order as written
    assert (q.filters[1].op, q.filters[1].value) == ("!=", "Success")


def test_not_in_list_with_one_value():
    c = Condition(n("card_type"), "NOT IN", ("Prepaid",))
    assert c.op == "NOT IN"
    assert c.value == ("Prepaid",)


def test_in_list_keeps_duplicates_as_written():
    assert Condition(n("card_type"), "IN", ("Credit", "Credit")).value == ("Credit", "Credit")


@pytest.mark.parametrize("op", ["=", "!=", ">", ">=", "<", "<="])
def test_numeric_condition_accepts_every_operator(op):
    assert Condition(n("amount"), op, Decimal("1")).op == op


@pytest.mark.parametrize("op", ["=", "!="])
def test_text_condition_accepts_equals_and_not_equals(op):
    assert Condition(n("card_type"), op, "Credit").op == op


@pytest.mark.parametrize("op", [">", ">=", "<", "<="])
def test_text_condition_rejects_ordering_operators(op):
    with pytest.raises(ValueError):
        Condition(n("card_type"), op, "Credit")


@pytest.mark.parametrize("op", ["<>", "==", "NOT", "in"])
def test_condition_rejects_unknown_operator(op):
    with pytest.raises(ValueError):
        Condition(n("amount"), op, Decimal("1"))


@pytest.mark.parametrize("op, value", [
    ("IN", ()),                              # empty list
    ("NOT IN", ()),
    ("IN", "Credit"),                        # a bare string, not a list
    ("IN", Decimal("1")),                    # a number, not a list
    ("IN", ("Credit", Decimal("1"))),        # lists hold text only
    ("NOT IN", (Decimal("1"), Decimal("2"))),
    ("=", ("Credit", "Debit")),              # a list without IN
    ("!=", ("Credit",)),
])
def test_bad_list_conditions_raise(op, value):
    with pytest.raises(ValueError):
        Condition(n("card_type"), op, value)


# --- Order validation ---------------------------------------------------------

def test_order_direction_defaults_to_desc():
    assert Order(n("value")).direction == "DESC"


@pytest.mark.parametrize("direction", ["ASC", "DESC"])
def test_order_accepts_both_directions(direction):
    assert Order(n("value"), direction).direction == direction


@pytest.mark.parametrize("direction", ["asc", "ascending", "UP", ""])
def test_order_rejects_unknown_direction(direction):
    with pytest.raises(ValueError):
        Order(n("value"), direction)


# --- Immutability -------------------------------------------------------------

def _instances():
    ast = QueryAST(metrics=(n("value"),))
    return [
        (Period("MTD"), "kind"),
        (n("value"), "canonical"),
        (Condition(n("card_type"), "=", "Credit"), "value"),
        (Condition(n("card_type"), "IN", ("Credit", "Debit")), "op"),
        (Order(n("value"), "ASC"), "direction"),
        (Period("RANGE", start=date(2025, 5, 12), end=date(2025, 6, 14)), "end"),
        (MetricCondition(n("value"), ">", Decimal("1")), "value"),
        (ast, "limit"),
        (ValidatedQuery(ast=ast, joins=()), "joins"),
        (CompiledQuery(sql="SELECT 1", dsl="SHOW value", chart_type=None, assumptions=()), "sql"),
    ]


@pytest.mark.parametrize(
    "instance, field", _instances(), ids=[type(i).__name__ for i, _ in _instances()]
)
def test_fields_cannot_be_reassigned(instance, field):
    with pytest.raises(FrozenInstanceError):
        setattr(instance, field, None)


def test_collections_are_tuples():
    q = QueryAST(metrics=(n("value"),))
    assert isinstance(q.metrics, tuple)
    assert isinstance(q.dimensions, tuple)
    assert isinstance(q.filters, tuple)
    assert isinstance(q.having, tuple)
    hash(q)  # fully immutable, so hashable


# --- Period validation --------------------------------------------------------

def test_last_n_days_without_n_raises():
    with pytest.raises(ValueError):
        Period(kind="LAST_N_DAYS")


def test_n_on_a_fixed_period_raises():
    with pytest.raises(ValueError):
        Period(kind="MTD", n=5)


def test_single_day_range_is_allowed():
    p = Period("RANGE", start=date(2025, 5, 12), end=date(2025, 5, 12))
    assert p.start == p.end


@pytest.mark.parametrize("start, end", [
    (None, None),
    (date(2025, 5, 12), None),
    (None, date(2025, 6, 14)),
])
def test_range_without_both_dates_raises(start, end):
    with pytest.raises(ValueError):
        Period("RANGE", start=start, end=end)


def test_range_start_after_end_raises():
    with pytest.raises(ValueError):
        Period("RANGE", start=date(2025, 6, 14), end=date(2025, 5, 12))


@pytest.mark.parametrize("kind, kwargs", [
    ("MTD", {"start": date(2025, 5, 12)}),
    ("MTD", {"end": date(2025, 5, 12)}),
    ("LAST_N_DAYS", {"n": 90, "start": date(2025, 5, 12), "end": date(2025, 6, 14)}),
])
def test_start_end_on_a_non_range_period_raises(kind, kwargs):
    with pytest.raises(ValueError):
        Period(kind, **kwargs)


def test_n_on_a_range_raises():
    with pytest.raises(ValueError):
        Period("RANGE", n=5, start=date(2025, 5, 12), end=date(2025, 6, 14))


# --- Pipeline composition -----------------------------------------------------

def test_validated_and_compiled_queries_compose():
    dsl = "SHOW volume BY category ORDER BY volume ASC AS PIE"
    ast = QueryAST(
        metrics=(n("volume"),),
        dimensions=(Name(raw="category", canonical="mcc"),),
        order=Order(n("volume"), "ASC"),
        chart_type="PIE",
        raw_dsl=dsl,
    )
    validated = ValidatedQuery(ast=ast, joins=("merchant_master",), order_by_kind="metric")
    compiled = CompiledQuery(
        sql="SELECT ...",
        dsl=validated.ast.raw_dsl,
        chart_type=validated.ast.chart_type,
        assumptions=(Assumption("success_default"), Assumption("unit_crore")),
    )

    assert validated.ast is ast
    assert validated.joins == ("merchant_master",)
    assert validated.order_by_kind == "metric"
    assert compiled.dsl == dsl
    assert compiled.chart_type == "PIE"
    assert [a.key for a in compiled.assumptions] == ["success_default", "unit_crore"]


def test_validated_query_order_by_kind_defaults_to_none():
    v = ValidatedQuery(ast=QueryAST(metrics=(n("value"),)), joins=())
    assert v.order_by_kind is None


# --- Assumption ---------------------------------------------------------------

def test_assumption_renders_its_template():
    a = Assumption("date_range_inclusive", (("start", "2025-05-12"), ("end", "2025-06-14")))
    assert a.render("Date range includes both ends: {start} to {end}, inclusive.") == (
        "Date range includes both ends: 2025-05-12 to 2025-06-14, inclusive."
    )


def test_assumption_is_frozen_and_hashable():
    a = Assumption("period_anchor", (("ref", "2025-12-27"),))
    assert {a, Assumption("period_anchor", (("ref", "2025-12-27"),))} == {a}
    with pytest.raises(FrozenInstanceError):
        a.key = "other"


def test_compiled_query_executable_form_defaults_to_empty():
    compiled = CompiledQuery(sql="SELECT 1", dsl="SHOW value", chart_type=None, assumptions=())
    assert compiled.executable_sql == "" and compiled.params == ()
