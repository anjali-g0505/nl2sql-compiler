"""Recursive-descent parser for the DSL defined in grammar.md.

Tokens in, QueryAST out — one function per grammar rule (recursive descent), each named after it:

    SHOW value BY merchant PERIOD MTD LIMIT 25 IN CRORE
    -> QueryAST(metrics=(Name('value', 'value'),),
                dimensions=(Name('merchant', 'merchant'),),
                period=Period('MTD'), limit=25, unit='CRORE', raw_dsl=...)

SYNTAX ONLY. Like the lexer, this stage never reads config.yaml: `SHOW value BY
nonsense_dimension AS DONUT` parses happily, and the validator is what rejects the
unknown dimension and chart type. Keeping that line clean is what makes both stages
testable on their own. What the parser does reject is shape: a missing SHOW, a clause
out of order or repeated, an operator where a value belongs, an empty IN list.

Names are stored unresolved (canonical == raw, original case). Values that come from
config's closed sets — period names, units, chart types — are uppercased, since the DSL
writes them uppercase and the validator matches them that way.

Errors are ParseError, raised on the FIRST problem (no error recovery): one precise
message beats a cascade of guesses, and the caller — an LLM retry loop — regenerates
the whole query anyway.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Optional, Sequence, Tuple

from compiler.ast import Condition, MetricCondition, Name, Order, Period, QueryAST
from compiler.lexer import LexError, Token, TokenType, tokenize

T = TokenType

# DSL operator spelling for each operator token, as stored on the AST.
#these are used in _condition function later on which is a part of the query fucntion
OPERATORS = {
    T.EQUALS: "=",
    T.NEQ: "!=",
    T.GT: ">",
    T.GTE: ">=",
    T.LT: "<",
    T.LTE: "<=",
}
NUMBER_TOKENS = (T.INTEGER, T.DECIMAL)
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

# Clause order is fixed (grammar.md §1). Kept here only to explain leftover tokens:
# "duplicate WHERE clause" or "out of order" reads better than "expected end of input".
CLAUSE_NAMES = {
    T.SHOW: "SHOW",
    T.BY: "BY",
    T.WHERE: "WHERE",
    T.PERIOD: "PERIOD",
    T.HAVING: "HAVING",
    T.ORDER: "ORDER BY",
    T.LIMIT: "LIMIT",
    T.IN: "IN",
    T.AS: "AS",
}
CLAUSE_ORDER = "SHOW, BY, WHERE, PERIOD, HAVING, ORDER BY, LIMIT, IN, AS"


class ParseError(Exception):
    """Raised when the token sequence doesn't fit the grammar.

    Carries the offending token so callers (and an LLM retry loop) can point at the
    exact spot in the original DSL string.
    """

    def __init__(self, message: str, token: Token):
        self.token = token
        self.position = token.position
        super().__init__(f"{message} at position {token.position}")


def parse(dsl: str) -> QueryAST:
    """Parse a DSL string. Raises LexError or ParseError on the first problem."""
    return parse_tokens(tokenize(dsl), dsl)


def parse_tokens(tokens: Sequence[Token], raw_dsl: str = "") -> QueryAST:
    """Parse an already-tokenized DSL string (the lexer's output)."""
    return _Parser(tokens, raw_dsl)._query()


def _describe(token: Token) -> str:
    """How a token is named in an error message."""
    if token.type is T.EOF:
        return "end of input"
    if token.type is T.STRING:
        return f"string {token.value!r}"
    return f"{token.value!r}"


class _Parser:
    """One instance per parse; holds the token list and the read position."""

    def __init__(self, tokens: Sequence[Token], raw_dsl: str):
        self.tokens = list(tokens)
        self.raw_dsl = raw_dsl
        self.i = 0
        self.seen_clauses = set()  # clause heads already consumed, for error messages, set() since we don't care about order, just duplicates

    # --- token helpers --------------------------------------------------------
    #peek at the next token, optionally offset by a number of tokens. If the offset is beyond the end of the token list, return the EOF token.
    #since offset is 0 by default, peek() returns the next token to be read, eg if self.i = 2 , peek(0) gives the 3rd toekn without consuming it (doesnt move the cursor). 
    #If you want to consume the token, use advance() instead (moves the cursor)
    
    """self.i == 2, tokens[2] is BY
        self.peek()      # -> BY   (self.i is still 2)
        self.peek()      # -> BY   again: peeking changes nothing
        self.advance()   # -> BY   and now self.i == 3
        self.peek()      # -> IDENTIFIER('issuer')
    """
    def peek(self, offset: int = 0) -> Token:
        #shows the current token wihtout advancing the cursor. Return EOF if the offset is beyond the end of the token list. 

        index = min(self.i + offset, len(self.tokens) - 1)  # EOF repeats at the end
        return self.tokens[index]

    def at(self, *types: TokenType) -> bool:
        return self.peek().type in types

    def advance(self) -> Token:
        #this is used to show the current token and advance the cursor to the next token.
        token = self.peek()
        if token.type is not T.EOF:
            self.i += 1
        return token

    def match(self, type_: TokenType) -> bool:
        """Consume the next token if it has this type eg: match(by) - advance if this token is found if not found just return false; a clause head is recorded.
        This is used for optional clauses, like BY, WHERE, HAVING, ORDER BY, LIMIT, IN, AS. If the clause is found, it is recorded in self.seen_clauses so that we can check for duplicates later. If the clause is not found, we just return false and do not advance the cursor."""

        if not self.at(type_):
            return False
        self.seen_clauses.add(type_)
        self.advance()
        return True

    def expect(self, type_: TokenType, what: str) -> Token:
        """ This is used for mandatory clauses, like SHOW, BY, WHERE, HAVING, ORDER BY, LIMIT, IN, AS. If the clause is found, it is recorded in self.seen_clauses so that we can check for duplicates later. 
        If the clause is not found, we raise a ParseError with a message indicating what was expected and what was found instead."""

        if not self.at(type_):
            raise ParseError(f"Expected {what}, got {_describe(self.peek())}", self.peek())
        return self.advance()

    def expect_one_of(self, types: Sequence[TokenType], what: str) -> Token:
        """Like expect(), where more than one token is allowed here (LAST n DAYS|MONTHS)."""
        if not any(self.at(type_) for type_ in types):
            raise ParseError(f"Expected {what}, got {_describe(self.peek())}", self.peek())
        return self.advance()

    def error(self, what: str) -> ParseError:
        return ParseError(f"Expected {what}, got {_describe(self.peek())}", self.peek())

    # --- query := SHOW metric_list [BY ...] [WHERE ...] ... --------------------

    def _query(self) -> QueryAST:
        self.expect(T.SHOW, "SHOW")
        self.seen_clauses.add(T.SHOW)
        metrics = self._name_list("a metric name")
    #The type of objects: Name, Condition, MetricCondition, Order, Period, QueryAST are defined in ast.py file.
        dimensions: Tuple[Name, ...] = () #dimension=() is an empty tuple which is the defalt value, dimension is optional and it is a tuple containing Name objects.
        if self.match(T.BY):
            dimensions = self._name_list("a dimension name")

        filters: Tuple[Condition, ...] = ()
        if self.match(T.WHERE):
            filters = self._condition_list()

        period: Optional[Period] = None
        if self.match(T.PERIOD):
            period = self._period_spec()

        having: Tuple[MetricCondition, ...] = ()
        if self.match(T.HAVING):
            having = self._having_list()

        order: Optional[Order] = None
        if self.match(T.ORDER):
            order = self._order_clause()

        limit: Optional[int] = None
        if self.match(T.LIMIT):
            limit = self._limit()

        unit: Optional[str] = None
        if self.match(T.IN):
            unit = self._config_value("a unit")

        chart_type: Optional[str] = None
        if self.match(T.AS):
            chart_type = self._config_value("a chart type")

        if not self.at(T.EOF):
            raise self._trailing_error()

        return QueryAST(
            metrics=metrics,
            dimensions=dimensions,
            filters=filters,
            period=period,
            having=having,
            order=order,
            limit=limit,
            unit=unit,
            chart_type=chart_type,
            raw_dsl=self.raw_dsl,
        )

    def _trailing_error(self) -> ParseError:
        """Explain a token left over after the last clause."""
        token = self.peek()
        clause = CLAUSE_NAMES.get(token.type)
        if clause and token.type in self.seen_clauses:
            return ParseError(f"Duplicate {clause} clause", token)
        if clause:
            return ParseError(
                f"{clause} clause is out of order; clause order is {CLAUSE_ORDER}", token
            )
        return self.error("end of input")

    # --- metric_list / dimension_list := name (',' name)* ---------------------

    def _name_list(self, what: str) -> Tuple[Name, ...]:
        names = [self._name(what)] #this line calls the _name method to parse the first name in the list and adds it to the names list. The _name method expects a token of type IDENTIFIER and returns a Name object
        while self.match(T.COMMA):
            names.append(self._name(what))
        return tuple(names)

    def _name(self, what: str) -> Name:
        """A metric, dimension or attribute reference, left unresolved for the validator."""
        token = self.expect(T.IDENTIFIER, what)
        return Name(raw=str(token.value), canonical=str(token.value))

    def _config_value(self, what: str) -> str:
        """A value from a config closed set (unit, chart type, period name)."""
        token = self.expect(T.IDENTIFIER, what)
        return str(token.value).upper()

    # --- condition_list := condition (AND condition)* -------------------------

    def _condition_list(self) -> Tuple[Condition, ...]:
        conditions = [self._condition()]
        while self.match(T.AND):
            conditions.append(self._condition())
        return tuple(conditions)

    def _condition(self) -> Condition:
        #there are three conditions given below, each condition has field, (in, not in, operator), (list, number)
        """condition := field ('='|'!=') string | field [NOT] IN (list) | field op number"""
        field = self._name("a dimension or attribute name")

        negated = self.at(T.NOT)
        if negated:
            self.advance()
            self.expect(T.IN, "IN after NOT")
            return self._in_list(field, "NOT IN")
        if self.at(T.IN):
            self.advance()
            return self._in_list(field, "IN")

        op_token = self.peek()
        if op_token.type not in OPERATORS:
            raise self.error("a comparison operator, IN or NOT IN")
        self.advance()
        op = OPERATORS[op_token.type]

        value_token = self.peek()
        if value_token.type is T.STRING:
            self.advance()
            return self._build(
                lambda: Condition(field, op, str(value_token.value)), op_token
            )
        if value_token.type in NUMBER_TOKENS:
            self.advance()
            return self._build(
                lambda: Condition(field, op, _to_decimal(value_token)), op_token
            )
        raise self.error("a quoted value or a number")

    def _in_list(self, field: Name, op: str) -> Condition:
        """The '(' 'a', 'b' ')' part of an IN / NOT IN condition."""
        open_paren = self.expect(T.LPAREN, f"'(' after {op}")
        values = [str(self.expect(T.STRING, "a quoted value").value)]
        while self.match(T.COMMA):
            values.append(str(self.expect(T.STRING, "a quoted value").value))
        self.expect(T.RPAREN, "',' or ')'")
        return self._build(lambda: Condition(field, op, tuple(values)), open_paren)

    # --- having_list := having_cond (AND having_cond)* ------------------------

    def _having_list(self) -> Tuple[MetricCondition, ...]:
        conditions = [self._having_cond()]
        while self.match(T.AND):
            conditions.append(self._having_cond())
        return tuple(conditions)

    def _having_cond(self) -> MetricCondition:
        """having_cond := metric comp_op number"""
        metric = self._name("a metric name")
        op_token = self.peek()
        if op_token.type not in OPERATORS:
            raise self.error("a comparison operator")
        self.advance()
        if not self.at(*NUMBER_TOKENS):
            raise self.error("a number (HAVING compares metrics to numbers)")
        value_token = self.advance()
        return self._build(
            lambda: MetricCondition(metric, OPERATORS[op_token.type], _to_decimal(value_token)),
            op_token,
        )

    # --- period_spec := name | LAST integer (DAYS|MONTHS) | FROM date TO date --

    def _period_spec(self) -> Period:
        if self.at(T.LAST):
            last = self.advance()
            n_token = self.expect(T.INTEGER, "a whole number of days or months")
            if n_token.value < 1:
                raise ParseError("LAST needs at least 1 day or month", n_token)
            unit = self.expect_one_of((T.DAYS, T.MONTHS), "DAYS or MONTHS")
            kind = "LAST_N_DAYS" if unit.type is T.DAYS else "LAST_N_MONTHS"
            return self._build(lambda: Period(kind, n=int(n_token.value)), last)
        if self.at(T.FROM):
            from_token = self.advance()
            start = self._date_literal()
            self.expect(T.TO, "TO")
            end = self._date_literal()
            return self._build(lambda: Period("RANGE", start=start, end=end), from_token)
        return Period(self._config_value("a period spec"))

    def _date_literal(self) -> date:
        """A 'YYYY-MM-DD' string literal, converted here so the AST holds real dates."""
        token = self.expect(T.STRING, "a quoted date 'YYYY-MM-DD'")
        text = str(token.value)
        if not DATE_PATTERN.fullmatch(text):
            raise ParseError(f"Date {text!r} is not in 'YYYY-MM-DD' form", token)
        try:
            return date.fromisoformat(text)
        except ValueError:  # well-formed but not a real day, e.g. '2025-02-30'
            raise ParseError(f"Date {text!r} is not a real calendar date", token) from None

    # --- ORDER BY sort_key [ASC|DESC] / LIMIT integer -------------------------

    def _order_clause(self) -> Order:
        """ORDER BY sort_key [ASC | DESC] — the direction is optional, default DESC."""
        self.expect(T.BY, "BY after ORDER")
        sort_key = self._name("a metric or dimension name")
        if self.match(T.ASC):
            return Order(sort_key, "ASC")
        self.match(T.DESC)  # explicit DESC is allowed but changes nothing
        return Order(sort_key, "DESC")

    def _limit(self) -> int:
        token = self.expect(T.INTEGER, "a whole number")
        if token.value < 1:
            raise ParseError("LIMIT must be at least 1", token)
        return int(token.value)

    # --- AST construction -----------------------------------------------------

    def _build(self, make, token: Token):
        """Build an AST node, turning its own validation error into a ParseError.

        The AST types check themselves (e.g. a text value with '>', a RANGE whose start
        is after its end). Re-raising here attaches a position from the DSL string.
        """
        try:
            return make()
        except ValueError as exc:
            raise ParseError(str(exc), token) from None


def _to_decimal(token: Token) -> Decimal:
    """INTEGER or DECIMAL token -> exact Decimal (never float)."""
    return token.value if isinstance(token.value, Decimal) else Decimal(int(token.value))


__all__ = ["parse", "parse_tokens", "ParseError", "LexError"]
