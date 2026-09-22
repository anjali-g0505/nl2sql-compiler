"""Codegen: turns a resolved query into MySQL, plus the assumptions that fired.

The last compiler stage. Everything it needs has already been decided upstream: the
validator grounded every name, chose the joins and the success-filter mode, and the
resolver replaced every filter value with one the database holds. Codegen does not
re-derive any rule; it only writes down what it is told, reading SQL fragments from
config.yaml:

    generate(resolve(validate(parse("SHOW value BY issuer"))), date(2025, 12, 27))
        -> CompiledQuery(sql="SELECT i.iss_name, SUM(t.amt) AS value\\nFROM ...", ...)

Design points:
  * Metrics are assembled from config `measures`, so a condition can be pushed into
    any metric mechanically (mixed metrics, E14) without a second copy of its SQL.
  * Two forms of the SQL come out: `sql` with values written in (audit panel, oracle
    tests) and `executable_sql` with `%s` placeholders plus `params` (what runs), so a
    filter value can never change the query's structure.
  * No database access. The caller passes `reference_date` (cached, refreshed with
    the value indexes), which keeps codegen deterministic: identical input gives
    byte-identical SQL.
  * No LIMIT is invented. The forced-limit guardrail is applied at execution.

Layout is fixed so the output matches grammar.md exactly: one clause per line, WHERE
conditions joined by "\\n  AND ", and a SELECT longer than LINE_WIDTH split into one
item per line (an item still too long is split once more at its top-level " / ").
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from compiler.ast import Assumption, CompiledQuery, Condition, Period
from compiler.resolver import ResolvedQuery, Status
from compiler.semantic_layer import SemanticLayer, split_columns

LINE_WIDTH = 100
SELECT_INDENT = " " * len("SELECT ")
DIVISION_INDENT = SELECT_INDENT + "  "  # a split ratio's "/ denominator" line
SQL_OPERATORS = {"!=": "<>"}  # DSL spelling -> SQL spelling; the rest are identical
PLAIN_COLUMN = re.compile(r"^[A-Za-z_]\w*\.[A-Za-z_]\w*$")
VALUE_MARKER = re.compile(r"\x00(\d+)\x00")  # stands in for a text value until the end
RELATIVE_PERIODS = ("FTD", "WTD", "MTD", "QTD", "YTD", "LAST_N_DAYS", "LAST_N_MONTHS")


class CodegenError(Exception):
    """Raised when a query can't be compiled (unresolved values, missing reference date)."""


def generate(
    resolved: ResolvedQuery,
    reference_date: Optional[date] = None,
    layer: Optional[SemanticLayer] = None,
) -> CompiledQuery:
    """Compile a resolved query. `reference_date` is required for relative periods."""
    if not resolved.ok:
        listed = ", ".join(f"{r.field} = {r.raw!r}" for r in resolved.unresolved)
        raise CodegenError(f"filter values still need attention: {listed}")
    return _Codegen(resolved, reference_date, layer or SemanticLayer.load()).run()


def render_assumptions(
    compiled: CompiledQuery, layer: Optional[SemanticLayer] = None
) -> Tuple[str, ...]:
    """The assumptions as display text, filled in from config templates."""
    templates = (layer or SemanticLayer.load()).assumptions
    return tuple(a.render(templates[a.key]) for a in compiled.assumptions)


def period_bounds(period: Period, reference_date: Optional[date]) -> Tuple[date, date]:
    """First and last day of a period, both inclusive."""
    if period.kind == "RANGE":
        return period.start, period.end
    if reference_date is None:
        raise CodegenError(f"PERIOD {period.kind} needs a reference date")
    ref = reference_date
    starts = {
        "FTD": ref,
        "WTD": ref - timedelta(days=ref.weekday()),  # Monday
        "MTD": ref.replace(day=1),
        "QTD": date(ref.year, 3 * ((ref.month - 1) // 3) + 1, 1),
        "YTD": date(ref.year, 1, 1),
    }
    if period.kind == "LAST_N_DAYS":
        return ref - timedelta(days=period.n - 1), ref
    if period.kind == "LAST_N_MONTHS":
        # whole calendar months ending with the reference month: 5 months from
        # 2025-12-31 is 2025-08-01 .. 2025-12-31 (not 150 days back)
        month = ref.month - (period.n - 1)
        year = ref.year + (month - 1) // 12
        return date(year, (month - 1) % 12 + 1, 1), ref
    return starts[period.kind], ref


def quote(value: str) -> str:
    """A MySQL string literal, for the display form only (execution uses params)."""
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def format_number(value: Decimal) -> str:
    """Plain decimal notation, no exponent and no trailing zeros: 1E+8 -> 100000000."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


class _Codegen:
    def __init__(self, resolved: ResolvedQuery, reference_date: Optional[date], layer: SemanticLayer):
        self.resolved = resolved
        self.validated = resolved.query
        self.ast = resolved.query.ast
        self.layer = layer
        self.reference_date = reference_date
        self.fact = layer.raw.get("fact", {})
        self.divisor = layer.units[self.ast.unit] if self.ast.unit else None
        self._values: List[str] = []  # text values, in the order they were marked

    def run(self) -> CompiledQuery:
        marked = "\n".join(self._statement()) + ";"
        order = [int(i) for i in VALUE_MARKER.findall(marked)]
        return CompiledQuery(
            sql=VALUE_MARKER.sub(lambda m: quote(self._values[int(m.group(1))]), marked),
            dsl=self.ast.raw_dsl,
            chart_type=self.ast.chart_type,
            assumptions=self._assumptions(),
            # a literal % would be read as a placeholder by the driver, so double it
            executable_sql=VALUE_MARKER.sub("%s", marked.replace("%", "%%")),
            params=tuple(self._values[i] for i in order),
        )

    def _text(self, value: str) -> str:
        """Mark a text value; it becomes a quoted literal or a %s placeholder later."""
        self._values.append(value)
        return f"\x00{len(self._values) - 1}\x00"

    # --- the statement --------------------------------------------------------

    def _statement(self) -> List[str]:
        lines = self._select()
        lines.append(f"FROM {self.fact['table']} {self.fact['alias']}")
        for name in self.validated.joins:
            join = self.layer.joins[name]
            lines.append(f"JOIN {name} {join['alias']} ON {join['on']}")
        lines += _clause("WHERE", self._where())
        if self.ast.dimensions:
            lines.append("GROUP BY " + ", ".join(
                self.layer.dimension(d.canonical).get("group_by")
                or self.layer.dimension(d.canonical)["select"]
                for d in self.ast.dimensions
            ))
        lines += _clause("HAVING", [
            f"{c.metric.canonical} {SQL_OPERATORS.get(c.op, c.op)} {format_number(c.value)}"
            for c in self.ast.having
        ])
        order = self._order_by()
        if order:
            lines.append("ORDER BY " + ", ".join(order))
        if self.ast.limit is not None:
            lines.append(f"LIMIT {self.ast.limit}")
        return lines

    # --- SELECT ---------------------------------------------------------------

    def _select(self) -> List[str]:
        items = [self._dimension_item(d.canonical) for d in self.ast.dimensions]
        items += [f"{self._metric(m.canonical)} AS {m.canonical}" for m in self.ast.metrics]

        one_line = "SELECT " + ", ".join(items)
        if len(one_line) <= LINE_WIDTH:
            return [one_line]

        lines = []
        for position, item in enumerate(items):
            prefix = "SELECT " if position == 0 else SELECT_INDENT
            suffix = "," if position < len(items) - 1 else ""
            line = prefix + item + suffix
            split = _split_division(item) if len(line) > LINE_WIDTH else None
            if split:
                lines += [prefix + split[0], DIVISION_INDENT + "/ " + split[1] + suffix]
            else:
                lines.append(line)
        return lines

    def _dimension_item(self, key: str) -> str:
        select = self.layer.dimension(key)["select"]
        return f"{select} AS {key}" if self._aliased(key) else select

    def _aliased(self, key: str) -> bool:
        """Expressions get an alias (SUBSTRING(...) AS month); plain columns keep their name."""
        columns = split_columns(self.layer.dimension(key)["select"])
        return len(columns) == 1 and not PLAIN_COLUMN.match(columns[0])

    def _metric(self, key: str) -> str:
        entry = self.layer.metric(key)
        pushed = None
        if self.validated.success_filter == "conditional" and entry.get("default_success_filter"):
            pushed = self.layer.success_condition

        if "sql" in entry:
            expression = entry["sql"]
        elif "measure" in entry:
            expression = self._measure(entry["measure"], pushed)
        else:
            numerator, denominator = entry["ratio"]
            expression = (
                f"{self._measure(numerator, pushed)} / "
                f"NULLIF({self._measure(denominator, pushed)},0)"
            )

        if self.divisor and entry.get("unit_scalable"):
            expression = f"{expression} / {self.divisor}"
        return expression

    def _measure(self, name: str, pushed: Optional[str]) -> str:
        """One aggregate, with its own filter and any pushed-in condition applied."""
        spec = self.layer.measures[name]
        agg, expr = spec["agg"], spec["expr"]
        condition = " AND ".join(c for c in (spec.get("filter"), pushed) if c)

        if agg == "SUM":
            return f"SUM(CASE WHEN {condition} THEN {expr} ELSE 0 END)" if condition else f"SUM({expr})"
        if agg == "COUNT" and expr == "*":
            return f"SUM(CASE WHEN {condition} THEN 1 ELSE 0 END)" if condition else "COUNT(*)"
        if agg == "COUNT":
            return f"COUNT(CASE WHEN {condition} THEN {expr} END)" if condition else f"COUNT({expr})"
        # COUNT_DISTINCT (the semantic layer rejects any other agg at load)
        if condition:
            return f"COUNT(DISTINCT CASE WHEN {condition} THEN {expr} END)"
        return f"COUNT(DISTINCT {expr})"

    # --- WHERE ----------------------------------------------------------------

    def _where(self) -> List[str]:
        """User filters as written, then the success filter, then the period."""
        conditions = [self._filter(c) for c in self.ast.filters]
        if self.validated.success_filter == "where":
            conditions.append(self.layer.success_condition)
        if self.ast.period:
            start, end = period_bounds(self.ast.period, self.reference_date)
            column = f"{self.fact['alias']}.{self.fact['date_column']}"
            conditions.append(
                f"{column} >= '{start.isoformat()}' AND {column} <= '{end.isoformat()}'"
            )
        return conditions

    def _filter(self, condition: Condition) -> str:
        key = condition.field.canonical
        op = SQL_OPERATORS.get(condition.op, condition.op)

        if isinstance(condition.value, Decimal):  # a numeric attribute
            attribute = self.layer.attribute(key)
            value = condition.value
            if attribute.get("money") and self.divisor:
                value = value * self.divisor  # thresholds are in the IN unit; amt is rupees
            return f"{attribute['column']} {op} {format_number(value)}"

        column = self.layer.filter_column(key)
        if isinstance(condition.value, tuple):
            listed = ", ".join(self._text(v) for v in condition.value)
            return f"{column} {op} ({listed})"
        return f"{column} {op} {self._text(condition.value)}"

    # --- ORDER BY -------------------------------------------------------------

    def _order_by(self) -> List[str]:
        """An explicit ORDER BY wins; otherwise a time dimension sorts chronologically."""
        order = self.ast.order
        if order:
            if self.validated.order_by_kind == "metric":
                return [f"{order.key.canonical} {order.direction}"]
            return self._dimension_order(order.key.canonical, order.direction)

        for name in self.ast.dimensions:
            entry = self.layer.dimension(name.canonical)
            if entry.get("time_dimension"):
                direction = str(entry.get("default_order", "asc")).upper()
                return self._dimension_order(name.canonical, direction)
        return []

    def _dimension_order(self, key: str, direction: str) -> List[str]:
        if self._aliased(key):
            return [f"{key} {direction}"]
        columns = split_columns(self.layer.dimension(key)["select"])
        return [f"{column} {direction}" for column in columns]

    # --- assumptions ----------------------------------------------------------

    def _assumptions(self) -> Tuple[Assumption, ...]:
        """In a fixed order: success, unit, period, metric definitions, corrections."""
        found: List[Assumption] = []
        mode = self.validated.success_filter
        if mode == "where":
            found.append(Assumption("success_default"))
        elif mode == "conditional":
            found.append(Assumption("mixed_metrics"))

        if self.ast.unit:
            found.append(Assumption(f"unit_{self.ast.unit.lower()}"))

        period = self.ast.period
        if period:
            start, end = period_bounds(period, self.reference_date)
            if period.kind in RELATIVE_PERIODS:
                found.append(Assumption("period_anchor", (("ref", end.isoformat()),)))
            if period.kind == "LAST_N_DAYS":
                found.append(Assumption("last_n_days_window", (
                    ("n", str(period.n)),
                    ("ref", end.isoformat()),
                    ("n_minus_1", str(period.n - 1)),
                    ("start", start.isoformat()),
                )))
            elif period.kind == "LAST_N_MONTHS":
                found.append(Assumption("last_n_months_window", (
                    ("n", str(period.n)),
                    ("ref", end.isoformat()),
                    ("start_month", f"{start:%B %Y}"),
                    ("end_month", f"{end:%B %Y}"),
                    ("start", start.isoformat()),
                )))
            elif period.kind == "RANGE":
                found.append(Assumption("date_range_inclusive", (
                    ("start", start.isoformat()),
                    ("end", end.isoformat()),
                )))

        for name in self.ast.metrics:
            emitted = self.layer.metric(name.canonical).get("emits_assumption", [])
            for key in [emitted] if isinstance(emitted, str) else emitted:
                found.append(Assumption(key))

        for resolution in self.resolved.resolutions:
            if resolution.status is Status.CORRECTED:
                shown = resolution.candidates[0].name if resolution.candidates else resolution.resolved
                found.append(Assumption("value_corrected", (
                    ("raw", resolution.raw),
                    ("resolved", str(shown)),
                    ("field", resolution.field),
                )))

        unique: List[Assumption] = []
        for assumption in found:
            if assumption not in unique:
                unique.append(assumption)
        return tuple(unique)


def _clause(keyword: str, conditions: Sequence[str]) -> List[str]:
    """WHERE/HAVING lines: the first condition after the keyword, the rest as '  AND'."""
    if not conditions:
        return []
    return [f"{keyword} {conditions[0]}"] + [f"  AND {c}" for c in conditions[1:]]


def _split_division(item: str) -> Optional[Tuple[str, str]]:
    """Split an item at its first top-level ' / ' (outside parentheses), if it has one."""
    depth = 0
    for index, char in enumerate(item):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and item.startswith(" / ", index):
            return item[:index], item[index + 3:]
    return None
