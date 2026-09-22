"""Validator tests: a parsed query checked against the real config.yaml.

Joins and success-filter expectations come from the grammar.md oracle (E1-E19), so
these tests fail if config.yaml and the oracle ever drift apart.
"""
import pytest
import yaml

from compiler.parser import parse
from compiler.semantic_layer import ConfigError, SemanticLayer
from compiler.validator import ValidationError, validate


def check(dsl):
    return validate(parse(dsl))


def errors(dsl):
    with pytest.raises(ValidationError) as exc:
        check(dsl)
    return list(exc.value.errors)


# --- joins, in config declaration order, per the oracle -----------------------

@pytest.mark.parametrize("dsl, expected", [
    ("SHOW value", ("response_master",)),                                        # E1
    ("SHOW value BY issuer", ("issuer_master", "response_master")),              # E2
    ("SHOW success_rate BY issuer", ("issuer_master", "response_master")),       # E3
    ("SHOW volume BY month WHERE card_type = 'Credit'", ("BIN_master",)),        # E4
    ("SHOW volume BY customer WHERE status = 'Business Decline' "
     "ORDER BY volume DESC LIMIT 1", ("response_master",)),                      # E5
    ("SHOW value BY issuer, acquirer",
     ("issuer_master", "acquirer_master", "response_master")),                   # E6
    ("SHOW value BY card_type PERIOD MTD IN CRORE",
     ("BIN_master", "response_master")),                                         # E8
    ("SHOW active_card_rate PERIOD LAST 90 DAYS", ()),                           # E10
    ("SHOW value BY status PERIOD MTD IN CRORE", ("response_master",)),          # E11
    ("SHOW volume, value, ats BY country", ("response_master",)),                # E14
    ("SHOW volume BY issuer WHERE card_type IN ('Credit', 'Debit') "
     "AND status != 'Success'",
     ("issuer_master", "BIN_master", "response_master")),                        # E19
])
def test_joins_match_the_oracle(dsl, expected):
    assert check(dsl).joins == expected


def test_join_order_is_config_order_not_query_order():
    # the same two dimensions, written both ways -> identical join order
    one = check("SHOW value BY issuer, acquirer").joins
    other = check("SHOW value BY acquirer, issuer").joins
    assert one == other == ("issuer_master", "acquirer_master", "response_master")


def test_a_join_needed_twice_appears_once():
    assert check("SHOW value BY card_type, card_variant").joins == (
        "BIN_master", "response_master",
    )


# --- implicit success filter --------------------------------------------------

@pytest.mark.parametrize("dsl, mode", [
    ("SHOW value BY issuer", "where"),                 # money metric -> WHERE
    ("SHOW volume BY issuer", "none"),                 # counts every attempt
    ("SHOW volume, value, ats BY country", "conditional"),   # E14, mixed metrics
    ("SHOW value BY status", "none"),                  # E11, suppressed by outcome dim
    ("SHOW value BY issuer WHERE status = 'Success'", "none"),   # suppressed in WHERE
    ("SHOW value BY response", "none"),
    ("SHOW success_rate BY issuer", "none"),           # rate metric, never pre-filtered
])
def test_success_filter_decision(dsl, mode):
    assert check(dsl).success_filter == mode


def test_suppression_removes_nothing_from_joins():
    # response_master is still needed: it is where TD_BD comes from
    assert "response_master" in check("SHOW value BY status").joins


# --- name resolution ----------------------------------------------------------

def test_aliases_resolve_to_canonical_keys():
    dims = check("SHOW value BY category").ast.dimensions
    assert (dims[0].raw, dims[0].canonical) == ("category", "mcc")


def test_names_are_case_insensitive_but_raw_is_kept():
    metrics = check("SHOW Value BY ISSUER").ast.metrics
    assert (metrics[0].raw, metrics[0].canonical) == ("Value", "value")


def test_duplicates_are_dropped():
    q = check("SHOW value, value BY issuer, issuer").ast
    assert len(q.metrics) == 1 and len(q.dimensions) == 1


def test_repeated_identical_filters_are_dropped():
    q = check("SHOW value WHERE card_type = 'Credit' AND card_type = 'Credit'").ast
    assert len(q.filters) == 1


@pytest.mark.parametrize("dsl, message", [
    ("SHOW revenue", "unknown metric 'revenue'"),
    ("SHOW value BY shop", "unknown dimension 'shop'"),
    ("SHOW value WHERE nonsense = 'x'", "unknown dimension 'nonsense'"),
])
def test_unknown_names_are_rejected(dsl, message):
    assert any(message in e for e in errors(dsl))


def test_unknown_name_suggests_the_closest_one():
    assert any("did you mean 'issuer'" in e for e in errors("SHOW value BY isuer"))


def test_all_errors_are_collected_not_just_the_first():
    assert len(errors("SHOW revenue, profit BY shop, outlet")) == 4


# --- kind mix-ups -------------------------------------------------------------

@pytest.mark.parametrize("dsl, message", [
    ("SHOW issuer", "dimension 'issuer' cannot be used in SHOW"),
    ("SHOW value BY volume", "metric 'volume' cannot be used in BY"),
    ("SHOW value WHERE value = 'x'", "metric 'value' cannot be used in a text WHERE"),
    ("SHOW value WHERE amount = 'big'", "attribute 'amount' cannot be used in a text WHERE"),
    ("SHOW value WHERE card_type > 5", "dimension 'card_type' cannot be used in a numeric"),
    ("SHOW value BY issuer HAVING issuer > 5", "dimension 'issuer' cannot be used in HAVING"),
])
def test_wrong_kind_for_the_clause(dsl, message):
    assert any(message in e for e in errors(dsl))


# --- clause rules -------------------------------------------------------------

def test_having_requires_by():
    assert any("HAVING needs a BY clause" in e for e in errors("SHOW value HAVING value > 5"))


def test_having_metric_must_be_selected():
    dsl = "SHOW volume BY issuer HAVING value > 5"
    assert any("must also appear in SHOW" in e for e in errors(dsl))


def test_having_on_a_selected_metric_passes():
    q = check("SHOW value BY customer HAVING value > 30000").ast   # E15
    assert q.having[0].metric.canonical == "value"


def test_order_by_must_be_selected():
    dsl = "SHOW value BY issuer ORDER BY volume"
    assert any("not selected by this query" in e for e in errors(dsl))


@pytest.mark.parametrize("dsl, kind", [
    ("SHOW value BY issuer ORDER BY value", "metric"),
    ("SHOW value BY issuer ORDER BY issuer ASC", "dimension"),
    ("SHOW value BY category ORDER BY merchant_category", "dimension"),  # via alias
])
def test_order_by_kind_is_recorded(dsl, kind):
    assert check(dsl).order_by_kind == kind


def test_no_order_clause_means_no_kind():
    assert check("SHOW value").order_by_kind is None


def test_contradictory_equality_filters_are_rejected():
    dsl = "SHOW value WHERE card_type = 'Credit' AND card_type = 'Debit'"
    assert any("use card_type IN (" in e for e in errors(dsl))


def test_a_range_on_one_attribute_is_fine():
    q = check("SHOW value WHERE amount >= 100 AND amount <= 500").ast
    assert len(q.filters) == 2


# --- unit, period, chart type -------------------------------------------------

def test_unit_needs_a_scalable_metric():
    assert any("only money metrics are scaled" in e for e in errors("SHOW volume IN CRORE"))


def test_unit_applies_when_any_metric_is_scalable():
    assert check("SHOW volume, value BY issuer IN CRORE").ast.unit == "CRORE"


def test_unknown_unit_is_rejected():
    assert any("unknown unit 'TONNES'" in e for e in errors("SHOW value IN TONNES"))


@pytest.mark.parametrize("dsl", [
    "SHOW value PERIOD MTD",
    "SHOW value PERIOD LAST 22 DAYS",
    "SHOW value PERIOD FROM '2025-05-12' TO '2025-06-14'",
])
def test_valid_periods_pass(dsl):
    assert check(dsl).ast.period is not None


def test_unknown_period_is_rejected():
    assert any("unknown period 'NEXTYEAR'" in e for e in errors("SHOW value PERIOD NEXTYEAR"))


def test_unknown_chart_type_is_rejected():
    assert any("unknown chart type 'DONUT'" in e for e in errors("SHOW value AS DONUT"))


def test_known_chart_type_passes():
    assert check("SHOW value BY issuer AS PIE").ast.chart_type == "PIE"


# --- PII guardrail ------------------------------------------------------------

def test_customer_dimension_is_pii_safe():
    # resolves to the masked id only; name/phone/address are never reachable
    assert check("SHOW volume BY customer").ast.dimensions[0].canonical == "customer"


def test_blocked_column_is_rejected(tmp_path):
    document = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    document["dimensions"]["leaky"] = {          # a config mistake, caught here
        "select": "cm.name",
        "group_by": "cm.name",
        "requires_join": ["customer_master"],
    }
    document["joins"]["customer_master"] = {"alias": "cm", "on": "t.customer_id = cm.id"}
    layer = SemanticLayer.from_dict(document)

    with pytest.raises(ValidationError) as exc:
        validate(parse("SHOW volume BY leaky"), layer)
    assert any("blocked column customer_master.name" in e for e in exc.value.errors)


def test_unknown_alias_in_config_is_rejected():
    document = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    document["dimensions"]["typo"] = {"select": "zz.thing", "group_by": "zz.thing"}
    layer = SemanticLayer.from_dict(document)

    with pytest.raises(ValidationError) as exc:
        validate(parse("SHOW volume BY typo"), layer)
    assert any("unknown table alias 'zz'" in e for e in exc.value.errors)


# --- the config file itself ---------------------------------------------------

def test_month_can_be_filtered_despite_commas_in_its_column():
    # SUBSTRING(t.date,1,7) is one column; its commas are inside parentheses
    query = check("SHOW volume BY issuer WHERE month = '2025-03'")
    assert query.ast.filters[0].field.canonical == "month"


def test_metric_joins_include_its_measures_joins():
    layer = SemanticLayer.load()
    assert list(layer.requires_join("success_rate", "metric")) == ["response_master"]
    assert list(layer.requires_join("value", "metric")) == []


def test_real_config_loads_and_self_checks():
    layer = SemanticLayer.load()
    assert "value" in layer.metrics and "issuer" in layer.dimensions
    assert layer.units == {"CRORE": 10000000, "LAKH": 100000}
    assert layer.period_names == ("FTD", "WTD", "MTD", "QTD", "YTD")


def test_every_multi_column_dimension_has_a_filter_column():
    layer = SemanticLayer.load()
    for key, entry in layer.dimensions.items():
        if "," in entry.get("select", ""):
            assert entry.get("filter_column"), f"{key} needs a filter_column"


@pytest.mark.parametrize("broken, message", [
    ({"metrics": {"x": {"requires_join": ["nope"]}}}, "unknown join 'nope'"),
    ({"dimensions": {"a": {"select": "t.one, t.two"}}}, "needs a filter_column"),
    ({"dimensions": {"a": {"select": "t.x", "resolution": "enum"}}}, "lists no values"),
    ({"metrics": {"x": {"aliases": ["dup"]}}, "dimensions": {"y": {"aliases": ["dup"]}}},
     "is claimed by"),
    # metrics are built from measures, exactly one way
    ({"metrics": {"x": {"label": "no shape"}}}, "needs exactly one of"),
    ({"metrics": {"x": {"measure": "m", "sql": "COUNT(*)"}},
      "measures": {"m": {"agg": "SUM", "expr": "t.amt"}}}, "needs exactly one of"),
    ({"metrics": {"x": {"measure": "nope"}}}, "unknown measure 'nope'"),
    ({"metrics": {"x": {"ratio": ["m"]}}}, "ratio needs [numerator, denominator]"),
    ({"measures": {"m": {"agg": "AVG", "expr": "t.amt"}}}, "has agg 'AVG'"),
    ({"measures": {"m": {"agg": "SUM"}}}, "has no expr"),
    ({"metrics": {"x": {"sql": "SUM(t.amt)", "default_success_filter": True}}},
     "build it from measures instead"),
    ({"metrics": {"x": {"sql": "COUNT(*)", "emits_assumption": "nope"}}},
     "emits unknown assumption 'nope'"),
    ({"metrics": {"x": {"measure": "m", "default_success_filter": True}},
      "measures": {"m": {"agg": "SUM", "expr": "t.amt"}}}, "needs a condition"),
])
def test_bad_config_fails_at_load(broken, message):
    with pytest.raises(ConfigError) as exc:
        SemanticLayer.from_dict(broken)
    assert message in str(exc.value)
