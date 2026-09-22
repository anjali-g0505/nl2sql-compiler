"""Value resolution: matches the text in WHERE conditions to values the data holds.

Runs after the validator (names are grounded) and before codegen (SQL is built). The
problem it solves is silent emptiness: `WHERE card_type = 'Credt'` is valid DSL over a
real dimension, so without this stage it compiles, runs, and returns zero rows with no
explanation. Here it becomes something actionable instead.

Every text value in an =, !=, IN or NOT IN condition ends as one of:

    SUCCESS    exact after normalization ('credit' -> 'Credit'). Silent.
    CORRECTED  one fuzzy match above threshold. Continue, surface "Interpreted X as Y".
    AMBIGUOUS  several candidates. Ask the USER; never retry the LLM on a coin flip.
    UNKNOWN    nothing close. enum/catalog -> hand back to the LLM with the valid
               values (one retry); entity -> ask the user with the nearest matches.
    SKIPPED    no index available for this dimension (e.g. masked ids, or a catalog
               index that has not been built); the value passes through untouched.

How a dimension resolves is declared in config, never hardcoded here:
    resolution: enum     -> a fixed list written in the config (card_type, card_variant, status) - index built from config
    resolution: catalog  -> a small list of distinct values from the database (mcc, response, country and location) -index supplied
    resolution: entity   -> a large id/name index with aliases (issuer, acquirer and merchant) - index supplied
    resolution: id       -> id: exact only, never matched,(card, customer, month and txn) -no fuzzy matching 

Indexes are passed in as a mapping of name -> ValueIndex, so this module needs no
database: enum indexes are built from config, and catalog/entity indexes come from
the caller (stage 3 builds them from SQL and refreshes them nightly).

All values in a query are resolved before returning — like the validator, and unlike
the parser — so one round trip can fix everything at once.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from rapidfuzz import fuzz, process

from compiler.ast import Condition, ValidatedQuery
from compiler.semantic_layer import SemanticLayer

PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
WHITESPACE = re.compile(r"\s+")
SCORERS = {  # config value_resolution.fuzzy.scorer
    "token_sort_ratio": fuzz.token_sort_ratio,
    "token_set_ratio": fuzz.token_set_ratio,
    "partial_ratio": fuzz.partial_ratio,
    "ratio": fuzz.ratio,
}


class Status(Enum):
    SUCCESS = "SUCCESS"
    CORRECTED = "CORRECTED"
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class Candidate:
    """One possible value: its id, its display name, and how well it matched."""

    id: str
    name: str
    score: float = 100.0

    def value_for(self, filter_value: str) -> str:
        """What the SQL should compare against: the id (mcc code) or the name."""
        return self.id if filter_value == "id" else self.name


@dataclass(frozen=True)
class Resolution:
    """The outcome for one text value in one condition."""

    field: str  # canonical dimension key, e.g. "card_type"
    raw: str  # exactly what the LLM/user wrote
    status: Status
    resolved: Optional[str] = None  # the value that goes into the SQL
    candidates: Tuple[Candidate, ...] = ()  # nearest matches, for CORRECTED/AMBIGUOUS/UNKNOWN

    @property
    def needs_attention(self) -> bool:
        """AMBIGUOUS and UNKNOWN stop the pipeline; the others do not."""
        return self.status in (Status.AMBIGUOUS, Status.UNKNOWN)


@dataclass(frozen=True)
class ResolvedQuery:
    """The resolver's output: the query with real values, plus what happened.

    `query` is a copy of the ValidatedQuery whose filter values have been replaced by
    resolved ones. `resolutions` keeps every raw value beside its outcome, for the
    audit panel, the `value_corrected` assumptions and any clarification the user is
    asked for. The original text also survives in `query.ast.raw_dsl`.
    """

    query: ValidatedQuery
    resolutions: Tuple[Resolution, ...] = ()

    @property
    def ok(self) -> bool:
        """True when nothing needs the user or another LLM attempt."""
        return not any(r.needs_attention for r in self.resolutions)

    @property
    def corrections(self) -> Tuple[Resolution, ...]:
        return tuple(r for r in self.resolutions if r.status is Status.CORRECTED)

    @property
    def unresolved(self) -> Tuple[Resolution, ...]:
        return tuple(r for r in self.resolutions if r.needs_attention)


class ValueIndex(Protocol):
    """What the resolver needs from an index, so it can be swapped for a DB index later.

    The index owns scoring, so build one with build_index() to pick up the scorer and
    threshold from config rather than whatever an implementation defaults to.
    """

    def lookup_exact(self, normalized: str) -> Optional[Candidate]:
        """An exact hit on a normalized id, name or alias."""

    def search_fuzzy(self, normalized: str, limit: int) -> Sequence[Candidate]:
        """The closest matches, best first, already scored."""


class StaticIndex:
    """An in-memory index over a fixed list of (id, name) pairs, plus aliases.

    Used for `enum` dimensions (built from config values) and, in stage 3, for
    catalogue and entity indexes built from the database.
    """

    def __init__(
        self,
        rows: Sequence[Tuple[str, str]],
        aliases: Optional[Mapping[str, Sequence[str]]] = None,
        scorer=fuzz.token_sort_ratio,
        threshold: int = 88,
    ):
        self.rows = tuple(Candidate(str(i), str(n)) for i, n in rows)
        self.scorer = scorer
        self.threshold = threshold
        self._exact: Dict[str, Candidate] = {}
        for candidate in self.rows:
            for key in (candidate.id, candidate.name):
                self._exact.setdefault(normalize(key), candidate)
        for entity_id, alias_list in (aliases or {}).items():
            match = next((c for c in self.rows if c.id == str(entity_id)), None)
            if match:
                for alias in alias_list:
                    self._exact.setdefault(normalize(str(alias)), match)
        # searched by name; ids are matched exactly, never fuzzily
        self._choices = {normalize(c.name): c for c in self.rows}

    @classmethod
    def from_values(cls, values: Sequence[str], **kwargs) -> "StaticIndex":
        """An index where the value is both id and name (config enum lists)."""
        return cls([(v, v) for v in values], **kwargs)

    def lookup_exact(self, normalized: str) -> Optional[Candidate]:
        return self._exact.get(normalized)

    def search_fuzzy(self, normalized: str, limit: int) -> Sequence[Candidate]:
        matches = process.extract(
            normalized,
            list(self._choices),
            scorer=self.scorer,
            limit=limit,
            score_cutoff=self.threshold,
        )
        return [replace(self._choices[key], score=score) for key, score, _ in matches]


def fuzzy_settings(layer: Optional[SemanticLayer] = None) -> Dict:
    """The value_resolution.fuzzy block, with defaults filled in."""
    fuzzy = (layer or SemanticLayer.load()).raw.get("value_resolution", {}).get("fuzzy", {})
    return {
        "scorer": SCORERS.get(fuzzy.get("scorer", ""), fuzz.token_set_ratio),
        "threshold": int(fuzzy.get("threshold", 88)),
        "min_length": int(fuzzy.get("min_length", 4)),
        "max_candidates": int(fuzzy.get("max_candidates", 5)),
    }


def build_index(
    rows: Sequence[Tuple[str, str]],
    aliases: Optional[Mapping[str, Sequence[str]]] = None,
    layer: Optional[SemanticLayer] = None,
) -> StaticIndex:
    """Build an index that scores the way config says, so every index agrees.

    Catalogue and entity indexes (stage 3) are built through here, not by calling
    StaticIndex directly, otherwise a caller's default scorer would quietly differ
    from the resolver's.
    """
    settings = fuzzy_settings(layer)
    return StaticIndex(
        rows,
        aliases=aliases,
        scorer=settings["scorer"],
        threshold=settings["threshold"],
    )


def normalize(text: str) -> str:
    """Casefold, strip accents and punctuation, collapse whitespace.

    Matching only: the resolved value written into the SQL is always a real value from
    config or the database, never this normalized form.
    """
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = PUNCTUATION.sub(" ", text.casefold())
    return WHITESPACE.sub(" ", text).strip()


def resolve(
    validated: ValidatedQuery,
    layer: Optional[SemanticLayer] = None,
    indexes: Optional[Mapping[str, ValueIndex]] = None,
) -> ResolvedQuery:
    """Resolve every text filter value in a validated query.

    `indexes` maps a catalog/entity name from config (e.g. "merchant", "mcc") to an
    index. Enum dimensions need no entry: their index is built from config.
    """
    return _Resolver(validated, layer or SemanticLayer.load(), indexes or {}).run()


class _Resolver:
    def __init__(
        self,
        validated: ValidatedQuery,
        layer: SemanticLayer,
        indexes: Mapping[str, ValueIndex],
    ):
        self.validated = validated
        self.layer = layer
        self.indexes = indexes
        self.resolutions: List[Resolution] = []
        settings = fuzzy_settings(layer)
        self.scorer = settings["scorer"]
        self.threshold = settings["threshold"]
        self.min_length = settings["min_length"]
        self.max_candidates = settings["max_candidates"]
        self._enum_cache: Dict[str, StaticIndex] = {}

    def run(self) -> ResolvedQuery: 
        ast = self.validated.ast
        filters = tuple(self._condition(c) for c in ast.filters)
        query = replace(self.validated, ast=replace(ast, filters=filters))
        return ResolvedQuery(query=query, resolutions=tuple(self.resolutions))

    # --- one condition --------------------------------------------------------

    def _condition(self, condition: Condition) -> Condition:
        if isinstance(condition.value, Decimal):  # numeric attribute, nothing to resolve
            return condition

        field = condition.field.canonical
        entry = self.layer.dimension(field)
        index = self._index_for(entry)
        filter_value = entry.get("filter_value", "name")

        if isinstance(condition.value, tuple):
            values = [self._value(field, v, entry, index, filter_value) for v in condition.value]
            return replace(condition, value=tuple(values))
        return replace(
            condition,
            value=self._value(field, condition.value, entry, index, filter_value),
        )

    def _index_for(self, entry: Mapping) -> Optional[ValueIndex]:
        """The index a dimension resolves against, or None when it can't be resolved."""
        kind = entry.get("resolution")
        if kind == "enum":
            key = ",".join(entry.get("values", []))
            if key not in self._enum_cache:
                self._enum_cache[key] = StaticIndex.from_values(
                    entry.get("values", []), scorer=self.scorer, threshold=self.threshold
                )
            return self._enum_cache[key]
        if kind in ("catalog", "entity"):
            return self.indexes.get(entry.get(kind))  # supplied by the caller
        return None  # resolution: id, or a dimension that declares nothing

    # --- one value ------------------------------------------------------------

    def _value(
        self,
        field: str,
        raw: str,
        entry: Mapping,
        index: Optional[ValueIndex],
        filter_value: str,
    ) -> str:
        """Resolve one value, record what happened, and return what the SQL should use."""
        if index is None:
            self._record(field, raw, Status.SKIPPED, resolved=raw)
            return raw

        normalized = normalize(raw)
        exact = index.lookup_exact(normalized)
        if exact:
            value = exact.value_for(filter_value)
            self._record(field, raw, Status.SUCCESS, resolved=value, candidates=(exact,))
            return value

        if len(normalized) < self.min_length:
            self._record(field, raw, Status.UNKNOWN, resolved=raw)
            return raw  # too short to guess at: 'IN' must not become 'India'

        candidates = tuple(index.search_fuzzy(normalized, self.max_candidates))
        if len(candidates) == 1:
            value = candidates[0].value_for(filter_value)
            self._record(field, raw, Status.CORRECTED, resolved=value, candidates=candidates)
            return value
        if len(candidates) > 1:
            self._record(field, raw, Status.AMBIGUOUS, resolved=raw, candidates=candidates)
            return raw
        self._record(field, raw, Status.UNKNOWN, resolved=raw, candidates=())
        return raw

    def _record(self, field, raw, status, resolved=None, candidates=()) -> None:
        self.resolutions.append(
            Resolution(
                field=field,
                raw=raw,
                status=status,
                resolved=resolved,
                candidates=tuple(candidates),
            )
        )
