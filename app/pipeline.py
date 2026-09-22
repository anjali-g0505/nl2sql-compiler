"""The app contract: question or DSL in, rows / a clarification / an error out.

Runs the whole pipeline and decides what happens when a query can't be compiled as-is:

    question -> Translator (LLM) -> DSL -> parse -> validate -> resolve -> codegen -> run

Who fixes what (see compiler/resolver.py for the outcomes):
  * Syntax errors, unknown names and UNKNOWN enum/catalog values are the LLM's mistakes.
    It gets ONE retry, told exactly what was wrong (and, for values, the valid ones).
    A second failure is returned as an error; the loop never spins.
  * AMBIGUOUS values and UNKNOWN entities (a merchant that isn't in the index) are
    questions only the user can answer. The query is parked under a clarification id,
    the user picks an option (or types a value), and the query resumes from where it
    stopped: no new LLM call, so the rest of the query can't change under them.
  * DSL sent directly has no LLM behind it, so its errors come straight back.

Responses carry `status`: "ok" | "needs_clarification" | "error", and an HTTP code the
endpoint returns as-is. Nothing here imports the database or FastAPI: execution, the
indexes and the translator are injected, which is how the tests run it.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from app.charts import choose_chart
from compiler.ast import Assumption, ValidatedQuery
from compiler.codegen import PLAIN_COLUMN, CodegenError, generate
from compiler.parser import LexError, ParseError, parse
from compiler.resolver import Resolution, ResolvedQuery, Status, resolve
from compiler.semantic_layer import SemanticLayer, split_columns
from compiler.validator import ValidationError, validate

CLARIFICATION_TTL = timedelta(minutes=30)
MAX_LISTED_VALUES = 30  # valid values shown to the LLM or user; catalogues can be long

Execute = Callable[[str, Sequence[Any]], List[Dict[str, Any]]]


class TranslatorError(Exception):
    """The NL -> DSL step failed (network, quota, empty answer). Not the user's fault."""


class Unanswerable(Exception):
    """The translator judged the question out of scope for this data (e.g. weather)."""


@dataclass(frozen=True)
class Feedback:
    """Why the previous DSL was rejected, for the translator's one retry."""

    dsl: str
    errors: Tuple[str, ...]


class Translator(Protocol):
    """NL -> DSL. The LLM lives behind this; tests use a scripted fake."""

    def translate(self, question: str, feedback: Optional[Feedback] = None) -> str:
        """Return one DSL string. With feedback, fix the previous attempt."""


class Registry(Protocol):
    """What the pipeline needs from compiler.indexes.IndexRegistry."""

    reference_date: Any

    def as_mapping(self) -> Mapping[str, Any]: ...

    def status(self, name: Optional[str] = None): ...


@dataclass(frozen=True)
class Outcome:
    """One response: the HTTP status and the JSON body."""

    http_status: int
    body: Dict[str, Any]


class _Rejected(Exception):
    """A stage refused the DSL; the errors are what the LLM (or user) must fix."""

    def __init__(self, stage: str, errors: Sequence[str]):
        self.stage = stage
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


@dataclass
class _Pending:
    """A query parked until the user answers its clarification questions."""

    question: Optional[str]
    dsl: str
    validated: ValidatedQuery
    questions: List[Dict[str, Any]]
    attempts: int
    expires_at: datetime


@dataclass
class Pipeline:
    registry: Registry
    execute: Execute
    translator: Optional[Translator] = None
    layer: SemanticLayer = field(default_factory=SemanticLayer.load)
    forced_limit: Optional[int] = None  # defaults to config guardrails.forced_limit
    clock: Callable[[], datetime] = datetime.now

    def __post_init__(self) -> None:
        if self.forced_limit is None:
            self.forced_limit = self.layer.guardrails.get("forced_limit")
        self._pending: Dict[str, _Pending] = {}
        self._lock = threading.Lock()

    # --- entry points ---------------------------------------------------------

    def ask(self, question: Optional[str] = None, dsl: Optional[str] = None) -> Outcome:
        """Answer a natural-language question, or run DSL directly."""
        if bool(question) == bool(dsl):
            return _error(422, "request", ["send exactly one of 'question' or 'dsl'"])

        if dsl:
            try:
                validated, resolved = self._attempt(dsl)
            except _Rejected as rejected:
                return _error(422, rejected.stage, rejected.errors, dsl=dsl, attempts=1)
            return self._finish(None, dsl, validated, resolved, attempts=1)

        if self.translator is None:
            return _error(503, "translate", ["no translator is configured; send 'dsl' instead"])

        feedback: Optional[Feedback] = None
        for attempt in (1, 2):  # the first try, plus exactly one retry
            try:
                dsl = self.translator.translate(question, feedback)
            except Unanswerable as exc:
                return _error(422, "scope", [str(exc)], attempts=attempt, question=question)
            except TranslatorError as exc:
                return _error(502, "translate", [str(exc)], attempts=attempt)
            try:
                validated, resolved = self._attempt(dsl)
            except _Rejected as rejected:
                if attempt == 2:
                    return _error(422, rejected.stage, rejected.errors, dsl=dsl, attempts=2,
                                  question=question)
                feedback = Feedback(dsl=dsl, errors=rejected.errors)
                continue
            return self._finish(question, dsl, validated, resolved, attempts=attempt)
        raise AssertionError("unreachable")

    def answer(self, clarification_id: str, answers: Mapping[str, str]) -> Outcome:
        """Resume a parked query with the user's answers (option ids or typed values)."""
        with self._lock:
            self._purge_expired()
            pending = self._pending.get(clarification_id)
        if pending is None:
            return _error(404, "clarification", ["unknown or expired clarification id"])

        missing = [q["id"] for q in pending.questions if not str(answers.get(q["id"], "")).strip()]
        if missing:  # keep it parked so the user can answer again
            return _error(422, "clarification", [f"no answer for {', '.join(missing)}"])

        with self._lock:
            if self._pending.pop(clarification_id, None) is None:
                return _error(404, "clarification", ["unknown or expired clarification id"])

        validated, dsl = pending.validated, pending.dsl
        for q in pending.questions:
            chosen = str(answers[q["id"]]).strip()
            validated = _substitute(validated, q["field"], q["value"], chosen)
            dsl = dsl.replace(f"'{q['value']}'", f"'{chosen}'")

        resolved = resolve(validated, self.layer, self.registry.as_mapping())
        return self._finish(pending.question, dsl, validated, resolved, pending.attempts)

    # --- one attempt ----------------------------------------------------------

    def _attempt(self, dsl: str) -> Tuple[ValidatedQuery, ResolvedQuery]:
        """Parse, validate and resolve. Raises _Rejected with what the LLM must fix."""
        try:
            ast = parse(dsl)
        except (LexError, ParseError) as exc:
            raise _Rejected("parse", [str(exc)]) from None
        try:
            validated = validate(ast, self.layer)
        except ValidationError as exc:
            raise _Rejected("validate", exc.errors) from None

        resolved = resolve(validated, self.layer, self.registry.as_mapping())
        fixable = [r for r in resolved.unresolved if self._llm_can_fix(r)]
        if fixable:
            raise _Rejected("resolve", [self._unknown_value_message(r) for r in fixable])
        return validated, resolved

    def _llm_can_fix(self, resolution: Resolution) -> bool:
        """UNKNOWN in a small closed list is a wrong guess; the valid values fix it."""
        kind = self.layer.dimension(resolution.field).get("resolution")
        return resolution.status is Status.UNKNOWN and kind in ("enum", "catalog")

    def _unknown_value_message(self, resolution: Resolution) -> str:
        values = self._valid_values(resolution.field)
        listed = ", ".join(values[:MAX_LISTED_VALUES])
        more = f" (and {len(values) - MAX_LISTED_VALUES} more)" if len(values) > MAX_LISTED_VALUES else ""
        return f"{resolution.field} has no value {resolution.raw!r}; valid values: {listed}{more}"

    def _valid_values(self, field_key: str) -> List[str]:
        entry = self.layer.dimension(field_key)
        if entry.get("resolution") == "enum":
            return [str(v) for v in entry.get("values", [])]
        index = self.registry.as_mapping().get(entry.get("catalog") or entry.get("entity"))
        return [c.name for c in getattr(index, "rows", ())]

    # --- after resolution: ask the user, or compile and run --------------------

    def _finish(
        self,
        question: Optional[str],
        dsl: str,
        validated: ValidatedQuery,
        resolved: ResolvedQuery,
        attempts: int,
    ) -> Outcome:
        if not resolved.ok:
            return self._clarify(question, dsl, validated, resolved, attempts)
        try:
            compiled = generate(resolved, self.registry.reference_date, self.layer)
        except CodegenError as exc:  # e.g. no reference date yet: the data isn't loaded
            return _error(503, "compile", [str(exc)], dsl=dsl, attempts=attempts)

        sql, assumptions = compiled.executable_sql, list(compiled.assumptions)
        if not sql.lstrip().upper().startswith("SELECT"):  # read-only, whatever codegen did
            return _error(500, "compile", ["refusing to run a statement that is not a SELECT"],
                          dsl=dsl, attempts=attempts)
        capped = resolved.query.ast.limit is None and bool(self.forced_limit)
        if capped:  # the guardrail: fetch one extra row to know whether anything was cut
            sql = sql.rstrip(";") + f"\nLIMIT {self.forced_limit + 1};"
        rows = self.execute(sql, compiled.params)
        if capped and len(rows) > self.forced_limit:
            rows = rows[: self.forced_limit]
            assumptions.append(Assumption("forced_limit", (("limit", str(self.forced_limit)),)))

        ast = resolved.query.ast
        metrics = [m.canonical for m in ast.metrics]
        dimensions = [d.canonical for d in ast.dimensions]
        chart_type, fallback = choose_chart(self.layer, dimensions, metrics, rows, ast.chart_type)
        if fallback:
            assumptions.append(fallback)

        columns = list(rows[0]) if rows else []
        templates = self.layer.assumptions
        return Outcome(200, {
            "status": "ok",
            "question": question,
            "dsl": dsl,
            "sql": compiled.sql,
            "chart_type": chart_type,
            # params too: the UI renders a corrected value as "ICIC -> ICICI Bank"
            "assumptions": [
                {"key": a.key, "text": a.render(templates[a.key]), "params": dict(a.params)}
                for a in assumptions
            ],
            # which columns are categories and which are numbers, for drawing the chart
            "metric_columns": [m for m in metrics if m in columns] if rows else metrics,
            "dimension_columns": [c for c in columns if c not in metrics],
            "labels": {
                **self._dimension_labels(dimensions),
                **{m: self.layer.metric(m).get("label", m) for m in metrics},
            },
            "unit": ast.unit,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "attempts": attempts,
        })

    def _dimension_labels(self, dimensions: Sequence[str]) -> Dict[str, str]:
        """Result column -> header, from config labels: iss_name -> "Issuer".

        A column is named by its alias (codegen aliases expressions, e.g. month) or by
        the part after the dot (i.iss_name -> iss_name). A dimension with several
        columns gets the label plus the column's last word: "Merchant category (MCC) code".
        """
        labels: Dict[str, str] = {}
        for key in dimensions:
            entry = self.layer.dimension(key)
            label = entry.get("label", key)
            columns = split_columns(entry.get("select", ""))
            if len(columns) == 1 and not PLAIN_COLUMN.match(columns[0]):
                labels[key] = label
                continue
            for column in columns:
                name = column.split(".")[-1]
                suffix = f" {name.split('_')[-1]}" if len(columns) > 1 else ""
                labels[name] = label + suffix
        return labels

    def _clarify(
        self,
        question: Optional[str],
        dsl: str,
        validated: ValidatedQuery,
        resolved: ResolvedQuery,
        attempts: int,
    ) -> Outcome:
        questions: List[Dict[str, Any]] = []
        seen = set()
        for resolution in resolved.unresolved:
            if (resolution.field, resolution.raw) in seen:  # same value twice: ask once
                continue
            seen.add((resolution.field, resolution.raw))
            questions.append(self._question(f"q{len(questions) + 1}", resolution))

        clarification_id = uuid.uuid4().hex
        with self._lock:
            self._purge_expired()
            self._pending[clarification_id] = _Pending(
                question=question,
                dsl=dsl,
                validated=validated,
                questions=questions,
                attempts=attempts,
                expires_at=self.clock() + CLARIFICATION_TTL,
            )
        return Outcome(200, {
            "status": "needs_clarification",
            "clarification_id": clarification_id,
            "question": question,
            "dsl": dsl,
            "questions": questions,
            "expires_in_seconds": int(CLARIFICATION_TTL.total_seconds()),
            "attempts": attempts,
        })

    def _question(self, question_id: str, resolution: Resolution) -> Dict[str, Any]:
        field_key, raw = resolution.field, resolution.raw
        label = self.layer.dimension(field_key).get("label", field_key)
        if resolution.status is Status.AMBIGUOUS:
            options = [{"id": c.id, "label": c.name} for c in resolution.candidates]
            message = f"{raw!r} matches more than one {label}. Which one did you mean?"
        elif self._llm_can_fix(resolution):  # only after a user's typed answer
            options = [{"id": v, "label": v} for v in self._valid_values(field_key)[:MAX_LISTED_VALUES]]
            message = f"{raw!r} is not a known {label}. Pick one of the options."
        else:  # an entity that isn't in the index
            options = [{"id": c.id, "label": c.name} for c in resolution.candidates]
            message = f"No {label} matches {raw!r}{self._index_age(field_key)}. Type its exact name."
        return {
            "id": question_id,
            "field": field_key,
            "value": raw,
            "reason": resolution.status.value.lower(),
            "message": message,
            "options": options,
        }

    def _index_age(self, field_key: str) -> str:
        entry = self.layer.dimension(field_key)
        status = self.registry.status(entry.get("entity") or entry.get("catalog"))
        return f" (list {status.describe_age()})" if status else ""

    def _purge_expired(self) -> None:
        now = self.clock()
        for key in [k for k, p in self._pending.items() if p.expires_at <= now]:
            del self._pending[key]


def _substitute(validated: ValidatedQuery, field_key: str, old: str, new: str) -> ValidatedQuery:
    """The same query with one filter value replaced (a user's clarification answer)."""
    filters = []
    for condition in validated.ast.filters:
        if condition.field.canonical == field_key:
            if condition.value == old:
                condition = replace(condition, value=new)
            elif isinstance(condition.value, tuple) and old in condition.value:
                condition = replace(
                    condition, value=tuple(new if v == old else v for v in condition.value)
                )
        filters.append(condition)
    return replace(validated, ast=replace(validated.ast, filters=tuple(filters)))


def _error(http_status: int, stage: str, errors: Sequence[str], **extra: Any) -> Outcome:
    """stage: request | translate | scope | parse | validate | resolve | clarification | compile."""
    return Outcome(http_status, {"status": "error", "stage": stage, "errors": list(errors), **extra})
