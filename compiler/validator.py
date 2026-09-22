"""Semantic validator: checks a parsed QueryAST against config.yaml.

This is where `guardrails.scope` becomes real — anything that cannot be grounded in
config is REJECTED, never guessed at or silently dropped:

    parse("SHOW revenue BY shop")   -> fine, syntactically
    validate(that)                 -> ValidationError:
                                      unknown metric 'revenue'
                                      unknown dimension 'shop'

What it does:
  * resolves every name (alias- and case-insensitive) to its config key
  * checks each name is the right KIND for where it appears: metrics in SHOW and
    HAVING, dimensions in BY and text filters, attributes in numeric filters
  * enforces ORDER BY over something the query actually selects, HAVING needing BY
    and a selected metric, units only on unit_scalable metrics, known period names
    and chart types
  * decides the implicit success filter, because the join set depends on it
  * collects every join into config declaration order (deterministic SQL)
  * re-checks the PII guardrail rather than trusting the config to be safe

Errors are COLLECTED, not raised one at a time: an LLM retry should be able to fix
the whole query in one pass. (The parser fails fast instead — a syntax error makes
everything after it meaningless, while grounding failures are independent.)

Values inside filters are NOT checked here; matching 'Grocery' to a real database
value is the value-resolution stage, which runs next.
"""
from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from compiler.ast import Condition, MetricCondition, Name, Order, QueryAST, ValidatedQuery
from compiler.semantic_layer import SemanticLayer

# "alias.column" inside a config SQL fragment, e.g. m.mcc_code in "m.mcc_code, m.name"
QUALIFIED_COLUMN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")

KIND_CLAUSE = {  # where a kind is allowed to appear, for error messages
    "metric": "SHOW or HAVING",
    "dimension": "BY or a text WHERE condition",
    "attribute": "a numeric WHERE condition",
}


class ValidationError(Exception):
    """Raised when a query cannot be grounded in config.yaml. Carries every problem."""

    def __init__(self, errors: Sequence[str]):
        self.errors = tuple(errors)
        super().__init__("; ".join(errors))


def validate(ast: QueryAST, layer: Optional[SemanticLayer] = None) -> ValidatedQuery:
    """Ground a QueryAST against config.yaml. Raises ValidationError with all problems."""
    return _Validator(ast, layer or SemanticLayer.load()).run()


class _Validator:
    def __init__(self, ast: QueryAST, layer: SemanticLayer):
        self.ast = ast
        self.layer = layer
        self.errors: List[str] = []

    # --- entry point ----------------------------------------------------------

    def run(self) -> ValidatedQuery: #this function is the main entry point for the validation process. It takes a QueryAST and a SemanticLayer as input, and it performs various validation checks on the query. If any validation errors are found, it raises a ValidationError with a list of all the problems encountered during validation. If the query is valid, it returns a ValidatedQuery object that contains the resolved and validated components of the query.
        metrics = self._names(self.ast.metrics, "metric", "SHOW") #takes a list of names from the ast for metric and resolves them to their canonical keys using the _names method. It checks if each name is a valid metric and collects any errors encountered during the resolution process. The resolved metrics are stored in the metrics variable for further validation.
        dimensions = self._names(self.ast.dimensions, "dimension", "BY")
        filters = self._filters()
        having = self._having(metrics)
        order, order_by_kind = self._order(metrics, dimensions)

        self._period()
        self._unit(metrics)
        self._chart_type()

        filter_dimensions = tuple(
            c.field for c in filters if self.layer.resolve(c.field.canonical, "dimension")
        )
        success_filter = self._success_filter(metrics, dimensions + filter_dimensions)
        joins = self._joins(metrics, dimensions, filters, having, order, success_filter)
        self._check_pii(dimensions + filter_dimensions, metrics)

        if self.errors:
            raise ValidationError(self.errors)

        return ValidatedQuery(
            ast=replace(
                self.ast,
                metrics=metrics,
                dimensions=dimensions,
                filters=filters,
                having=having,
                order=order,
            ),
            joins=joins,
            order_by_kind=order_by_kind,
            success_filter=success_filter,
        )

    def fail(self, message: str) -> None:
        if message not in self.errors:  # the same mistake twice reads as one problem
            self.errors.append(message)

    # --- names ----------------------------------------------------------------

    def _resolve(self, name: Name, kind: str, clause: str) -> Optional[Name]:
        """Resolve one name to its config key, or record why it can't be."""
        #Ex. If the name is "revenue" and the kind is "metric", it will look up "revenue" in the SemanticLayer to find its canonical key. If it finds a match, it returns a new Name object with the raw name and the canonical key. If it doesn't find a match, it checks if the name belongs to a different kind (e.g., if "revenue" is actually a dimension or attribute) and records an error message indicating that the name cannot be used in the specified clause. If no match is found at all, it suggests a similar known name and records an error message indicating that the name is unknown.
        #Examples:
        """Input: value, metric, SHOW -> Output: Name(raw='value', canonical='value') since canonical for value is value in the config.
         2. Input: VALUE, metric, SHOW-> Output: Name(raw='VALUE', canonical='value'). Matching ignores case; raw keeps what was typed.
         3. Input: Error_Code, dimension, BY -> Output: Name(raw='Error_Code', canonical='response') 
         4. Input: amt, attribute, WHERE -> Output: Name(raw='amt', canonical='amount')"""
        #2. The name exists, but as a different kind. It returns None and records that the name is in the wrong clause 
        """ Input:issuer, metric, SHOW --> Output: Error- dimension 'issuer' cannot be used in SHOW; dimensions belong in BY or a text WHERE condition"""
        #3. The name isn't known at all. It returns None and records "unknown", adding a "did you mean" hint if layer.suggest finds a match scoring 75 or more:
        """ Input: vlaue, metric, SHOW -> Output: Error- unknown metric 'vlaue'; did you mean 'value'? """
        key = self.layer.resolve(name.raw, kind)
        if key:
            return Name(raw=name.raw, canonical=key)

        actual = self.layer.kind_of(name.raw)
        if actual:
            self.fail(
                f"{actual} {name.raw!r} cannot be used in {clause}; "
                f"{actual}s belong in {KIND_CLAUSE[actual]}"
            )
            return None

        suggestion = self.layer.suggest(name.raw, kind)
        hint = f"; did you mean {suggestion!r}?" if suggestion else ""
        self.fail(f"unknown {kind} {name.raw!r}{hint}")
        return None

    def _names(self, names: Sequence[Name], kind: str, clause: str) -> Tuple[Name, ...]:
        """Resolve a list of names, dropping duplicates if the list contains different terms that resolve to the same value(harmless, so not an error)."""
        resolved: List[Name] = []
        #Ex. Input: [value, volume], metric, SHOW -> Output: (Name('value','value'), Name('volume','volume'))
        #Ex. Input: [value, VALUE], metric, SHOW -> Ouput: (Name('value','value'),). The second is the same metric in different case, so it's dropped.
        seen = set()
        for name in names: #removes duplicates
            found = self._resolve(name, kind, clause)
            if found and found.canonical not in seen:
                seen.add(found.canonical)
                resolved.append(found)
        return tuple(resolved)

    # --- WHERE ----------------------------------------------------------------

    def _filters(self) -> Tuple[Condition, ...]:
        resolved: List[Condition] = []
        """ Input: card_type = 'Credit', Output:('card_type','card_type','=','Credit')
            Input: category = 'Grocery', Output: ('category','mcc','=','Grocery'). The alias is resolved; the value is untouched.
            Input: card_type IN ('Credit','Debit'), Output: ('card_type','card_type','IN',('Credit','Debit'))
            Input: card_type = 'Credit' AND card_type = 'Debit', Output: Error- card_type cannot equal 'Credit' and 'Debit' at the same time; use card_type IN ('Credit','Debit') to match any of them. The two conditions are contradictory, so an error is raised.
            input: amount = 'big', Output: attribute 'amount' cannot be used in a text WHERE condition; …
            input: card_type = 'Credit' AND card_type = 'Credit', Output: one condition. The exact repeat is dropped silently., No errror 
        """
        seen = set()
        for condition in self.ast.filters: #condition consists of field, operator and value.
            is_number = isinstance(condition.value, Decimal)
            kind = "attribute" if is_number else "dimension"
            clause = (
                "a numeric WHERE condition" if is_number else "a text WHERE condition"
            )
            field = self._resolve(condition.field, kind, clause)
            if not field:
                continue 
            if not is_number and not self._filter_column(field.canonical):
                self.fail(
                    f"dimension {field.canonical!r} cannot be filtered: "
                    f"it needs a filter_column in config"
                )
                continue
            new = replace(condition, field=field)
            key = (field.canonical, new.op, new.value)
            if key not in seen:  # an exact repeat is redundant, not wrong
                seen.add(key)
                resolved.append(new)

        self._check_contradictions(resolved)
        return tuple(resolved)

    def _filter_column(self, key: str) -> Optional[str]:
        return self.layer.filter_column(key)

    def _check_contradictions(self, filters: Sequence[Condition]) -> None:
        """`x = 'a' AND x = 'b'` can never match: AND applies to one row. """
        equals: Dict[str, set] = {}
        for condition in filters:
            if condition.op == "=" and isinstance(condition.value, str):
                equals.setdefault(condition.field.canonical, set()).add(condition.value)
        for field, values in equals.items():
            if len(values) > 1:
                listed = ", ".join(repr(v) for v in sorted(values))
                self.fail(
                    f"{field} cannot equal {listed} at the same time; "
                    f"use {field} IN ({listed}) to match any of them"
                )

    # --- HAVING ---------------------------------------------------------------

    def _having(self, metrics: Sequence[Name]) -> Tuple[MetricCondition, ...]:
        """this function checks the HAVING clause of the query. It ensures that if there is a HAVING clause, there must also be a BY clause (dimensions) present in the query. 
        If there is no BY clause, it raises a validation error indicating that HAVING needs a BY clause. 
        It then resolves each metric in the HAVING clause to its canonical key and checks if it is also present in the selected metrics from the SHOW clause.
        If a metric in HAVING is not selected in SHOW, it raises a validation error. It also ensures that duplicate conditions are not added to the resolved list."""
        if self.ast.having and not self.ast.dimensions:
            self.fail("HAVING needs a BY clause: with no groups there is nothing to filter")

        selected = {m.canonical for m in metrics}
        resolved: List[MetricCondition] = []
        seen = set()
        for condition in self.ast.having:
            metric = self._resolve(condition.metric, "metric", "HAVING")
            if not metric:
                continue
            if metric.canonical not in selected:
                self.fail(
                    f"HAVING metric {metric.canonical!r} must also appear in SHOW"
                )
                continue
            new = replace(condition, metric=metric)
            key = (metric.canonical, new.op, new.value)
            if key not in seen:
                seen.add(key)
                resolved.append(new)
        return tuple(resolved)

    # --- ORDER BY -------------------------------------------------------------

    def _order( #this function checks the ORDER BY clause of the query. It ensures that if there is an ORDER BY clause, the specified key must be present in either the selected metrics or dimensions. If the key is not found in either, it raises a validation error indicating that the ORDER BY key is not selected by the query and suggests available options for sorting. It returns a tuple containing the resolved Order object and the kind of the key (metric or dimension) if valid, or None if invalid.
        self, metrics: Sequence[Name], dimensions: Sequence[Name]
    ) -> Tuple[Optional[Order], Optional[str]]:
        if not self.ast.order:
            return None, None

        raw = self.ast.order.key.raw
        for kind, selected in (("metric", metrics), ("dimension", dimensions)):
            key = self.layer.resolve(raw, kind)
            if key and any(name.canonical == key for name in selected):
                return replace(self.ast.order, key=Name(raw, key)), kind

        available = ", ".join(
            sorted(name.canonical for name in list(metrics) + list(dimensions))
        )
        self.fail(
            f"ORDER BY {raw!r} is not selected by this query; "
            f"sort by one of: {available or 'nothing selected'}"
        )
        return None, None

    # --- PERIOD / IN unit / AS chart ------------------------------------------

    def _period(self) -> None: #this function checks the period specified in the query. It ensures that if a period is specified, it must be one of the known period names defined in the SemanticLayer. If the period is not recognized, it raises a validation error indicating that the period is unknown and provides a list of expected period names. If no period is specified or if the period is of kind "LAST_N_DAYS" or "RANGE", it does not perform any validation.
        period = self.ast.period
        if period is None or period.kind in ("LAST_N_DAYS", "RANGE"):
            return
        if period.kind not in self.layer.period_names: #layer is the semantic layer and it specifies the known period names.
            known = ", ".join(self.layer.period_names)
            self.fail(f"unknown period {period.kind!r}; expected one of: {known}")

    def _unit(self, metrics: Sequence[Name]) -> None: #this function checks the unit specified in the query. It ensures that if a unit is specified, it must be one of the known units defined in the SemanticLayer. If the unit is not recognized, it raises a validation error indicating that the unit is unknown and provides a list of expected units. It also checks if any of the selected metrics are scalable (i.e., they can be scaled by the specified unit). If there are selected metrics but none of them are scalable, it raises a validation error indicating that the specified unit does not apply to the selected metrics and provides a list of scalable metrics.
        unit = self.ast.unit
        if unit is None:
            return
        if unit not in self.layer.units:
            known = ", ".join(sorted(self.layer.units))
            self.fail(f"unknown unit {unit!r}; expected one of: {known}")
            return
        scalable = [
            m.canonical
            for m in metrics
            if self.layer.metric(m.canonical).get("unit_scalable")
        ]
        if metrics and not scalable:
            listed = ", ".join(m.canonical for m in metrics)
            self.fail(
                f"IN {unit} does not apply to {listed}: only money metrics are scaled"
            )

    def _chart_type(self) -> None: #this function checks the chart type specified in the query. It ensures that if a chart type is specified, it must be one of the known chart types defined in the SemanticLayer. If the chart type is not recognized, it raises a validation error indicating that the chart type is unknown and provides a list of expected chart types. If no chart type is specified or if the chart type is recognized, it does not perform any validation.
        chart = self.ast.chart_type
        if chart is None or chart in self.layer.chart_types:
            return
        known = ", ".join(self.layer.chart_types)
        self.fail(f"unknown chart type {chart!r}; expected one of: {known}")

    # --- implicit success filter ----------------------------------------------

    def _success_filter(self, metrics: Sequence[Name], referenced: Sequence[Name]) -> str:
        """Suppressed by an outcome dimension; otherwise driven by the metrics."""
        for name in referenced:
            if self.layer.dimension(name.canonical).get("outcome_dimension"):
                return "none"

        defaults = [
            bool(self.layer.metric(m.canonical).get("default_success_filter"))
            for m in metrics
        ]
        if defaults and all(defaults):
            return "where"
        if any(defaults):
            return "conditional"  # mixed metrics: filter inside the money metric only
        return "none"

    # --- joins ----------------------------------------------------------------

    def _joins( #this function determines the necessary joins for the query based on the selected metrics, dimensions, filters, having conditions, order by clause, and the implicit success filter. It collects all the required joins for each component of the query and ensures that they are ordered in a deterministic manner based on their declaration order in the SemanticLayer configuration. The function returns a tuple of join names that are needed to execute the query.
        self,
        metrics: Sequence[Name],
        dimensions: Sequence[Name],
        filters: Sequence[Condition],
        having: Sequence[MetricCondition],
        order: Optional[Order],
        success_filter: str,
    ) -> Tuple[str, ...]:
        needed: List[str] = []
        for name in metrics:
            needed += list(self.layer.requires_join(name.canonical, "metric"))
        for name in dimensions:
            needed += list(self.layer.requires_join(name.canonical, "dimension"))
        for condition in filters:
            kind = self.layer.kind_of(condition.field.canonical)
            if kind:
                needed += list(self.layer.requires_join(condition.field.canonical, kind))
        for condition in having:
            needed += list(self.layer.requires_join(condition.metric.canonical, "metric"))
        if order:
            kind = self.layer.kind_of(order.key.canonical)
            if kind:
                needed += list(self.layer.requires_join(order.key.canonical, kind))
        if success_filter != "none":
            needed += list(self.layer.success_joins)  # TD_BD lives in response_master
        return self.layer.order_joins(needed)

    # --- PII guardrail --------------------------------------------------------

    def _check_pii(self, dimensions: Sequence[Name], metrics: Sequence[Name]) -> None:
        """Re-check that nothing reaches a blocked column, rather than trusting config.

        Fragments are alias-qualified (`m.name`), blocked columns are table-qualified
        (`customer_master.name`), so aliases are mapped back to their tables first.
        A fragment using an unknown alias is a config bug and fails here too.
        """
        blocked = {str(c).lower() for c in self.layer.guardrails.get("pii_blocked_columns", [])}
        alias_to_table = {
            str(cfg.get("alias")): table for table, cfg in self.layer.joins.items()
        }
        fact = self.layer.raw.get("fact", {})
        alias_to_table[str(fact.get("alias", "t"))] = str(fact.get("table", ""))

        for name in list(dimensions) + list(metrics):
            kind = self.layer.kind_of(name.canonical)
            for fragment in self.layer.sql_fragments(name.canonical, kind):
                for alias, column in QUALIFIED_COLUMN.findall(fragment):
                    table = alias_to_table.get(alias)
                    if table is None:
                        self.fail(
                            f"{kind} {name.canonical!r} uses unknown table alias {alias!r}"
                        )
                    elif f"{table}.{column}".lower() in blocked:
                        self.fail(
                            f"{kind} {name.canonical!r} reads blocked column "
                            f"{table}.{column}"
                        )
