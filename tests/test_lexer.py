"""Lexer tests. The DSL strings come from the grammar.md oracle (E1-E16)."""
from decimal import Decimal

import pytest

from compiler.lexer import LexError, Token, TokenType, tokenize

T = TokenType


def types(dsl: str):
    return [token.type for token in tokenize(dsl)]


def values(dsl: str):
    return [token.value for token in tokenize(dsl)]


# --- E1-E14 oracle strings: exact token type sequences -----------------------

def test_e1_scalar():
    assert types("SHOW value") == [T.SHOW, T.IDENTIFIER, T.EOF]


def test_e2_group_by():
    assert types("SHOW value BY issuer") == [T.SHOW, T.IDENTIFIER, T.BY, T.IDENTIFIER, T.EOF]


def test_e4_filter():
    dsl = "SHOW volume BY month WHERE card_type = 'Credit'"
    assert types(dsl) == [
        T.SHOW, T.IDENTIFIER, T.BY, T.IDENTIFIER,
        T.WHERE, T.IDENTIFIER, T.EQUALS, T.STRING, T.EOF,
    ]
    assert values(dsl) == ["SHOW", "volume", "BY", "month", "WHERE", "card_type", "=", "Credit", ""]


def test_e5_top_n():
    dsl = "SHOW volume BY customer WHERE status = 'Business Decline' ORDER BY volume DESC LIMIT 1"
    assert types(dsl) == [
        T.SHOW, T.IDENTIFIER, T.BY, T.IDENTIFIER,
        T.WHERE, T.IDENTIFIER, T.EQUALS, T.STRING,
        T.ORDER, T.BY, T.IDENTIFIER, T.DESC,
        T.LIMIT, T.INTEGER, T.EOF,
    ]
    tokens = tokenize(dsl)
    assert tokens[7] == Token(T.STRING, "Business Decline", dsl.index("'"))  # spacing/case kept
    assert tokens[13].value == 1 and isinstance(tokens[13].value, int)


def test_e6_two_dimensions():
    dsl = "SHOW value BY issuer, acquirer"
    assert types(dsl) == [
        T.SHOW, T.IDENTIFIER, T.BY, T.IDENTIFIER, T.COMMA, T.IDENTIFIER, T.EOF,
    ]


def test_e7_chart_override():
    dsl = "SHOW value BY issuer AS PIE"
    assert types(dsl) == [
        T.SHOW, T.IDENTIFIER, T.BY, T.IDENTIFIER, T.AS, T.CHART_TYPE, T.EOF,
    ]
    assert tokenize(dsl)[5].value == "PIE"


def test_e8_period_and_unit():
    dsl = "SHOW value BY card_type PERIOD MTD IN CRORE"
    assert types(dsl) == [
        T.SHOW, T.IDENTIFIER, T.BY, T.IDENTIFIER,
        T.PERIOD, T.PERIOD_SPEC, T.IN, T.UNIT, T.EOF,
    ]
    assert values(dsl)[5] == "MTD"
    assert values(dsl)[7] == "CRORE"


def test_e10_rolling_window():
    dsl = "SHOW active_card_rate PERIOD LAST 90 DAYS"
    assert types(dsl) == [
        T.SHOW, T.IDENTIFIER, T.PERIOD, T.LAST, T.INTEGER, T.DAYS, T.EOF,
    ]
    assert tokenize(dsl)[4].value == 90


def test_e14_multi_metric():
    dsl = "SHOW volume, value, ats BY country"
    assert types(dsl) == [
        T.SHOW, T.IDENTIFIER, T.COMMA, T.IDENTIFIER, T.COMMA, T.IDENTIFIER,
        T.BY, T.IDENTIFIER, T.EOF,
    ]


def test_e15_having_threshold():
    dsl = "SHOW value BY customer HAVING value > 30000 ORDER BY value DESC"
    assert types(dsl) == [
        T.SHOW, T.IDENTIFIER, T.BY, T.IDENTIFIER,
        T.HAVING, T.IDENTIFIER, T.GT, T.INTEGER,
        T.ORDER, T.BY, T.IDENTIFIER, T.DESC, T.EOF,
    ]
    assert tokenize(dsl)[7].value == 30000


def test_e16_having_with_period_and_unit():
    dsl = "SHOW value BY merchant PERIOD MTD HAVING value > 3 IN CRORE"
    assert types(dsl) == [
        T.SHOW, T.IDENTIFIER, T.BY, T.IDENTIFIER, T.PERIOD, T.PERIOD_SPEC,
        T.HAVING, T.IDENTIFIER, T.GT, T.INTEGER, T.IN, T.UNIT, T.EOF,
    ]


def test_e17_explicit_date_range():
    dsl = "SHOW value PERIOD FROM '2025-05-12' TO '2025-06-14'"
    assert types(dsl) == [
        T.SHOW, T.IDENTIFIER, T.PERIOD, T.FROM, T.STRING, T.TO, T.STRING, T.EOF,
    ]
    # dates stay plain strings here; the parser converts and checks them
    assert values(dsl)[4] == "2025-05-12"
    assert values(dsl)[6] == "2025-06-14"


def test_e18_numeric_where():
    dsl = "SHOW value BY txn WHERE amount > 100000000 AS TABLE"
    assert types(dsl) == [
        T.SHOW, T.IDENTIFIER, T.BY, T.IDENTIFIER,
        T.WHERE, T.IDENTIFIER, T.GT, T.INTEGER,
        T.AS, T.CHART_TYPE, T.EOF,
    ]
    assert values(dsl)[7] == 100000000


def test_from_to_are_case_insensitive_keywords():
    assert types("from To") == [T.FROM, T.TO, T.EOF]


def test_unquoted_date_raises():
    # without quotes, '-' is not a DSL character
    with pytest.raises(LexError) as exc:
        tokenize("PERIOD FROM 2025-05-12 TO 2025-06-14")
    assert exc.value.text == "-"


# --- HAVING operators and numbers ----------------------------------------------

@pytest.mark.parametrize("op, expected", [
    (">", T.GT), (">=", T.GTE), ("<", T.LT), ("<=", T.LTE), ("=", T.EQUALS), ("!=", T.NEQ),
])
def test_comparison_operators(op, expected):
    token = tokenize(f"HAVING value {op} 5")[2]
    assert token.type is expected
    assert token.value == op


def test_two_char_operator_is_one_token_but_spaced_is_two():
    assert types("value>=5") == [T.IDENTIFIER, T.GTE, T.INTEGER, T.EOF]
    assert types("value > = 5") == [T.IDENTIFIER, T.GT, T.EQUALS, T.INTEGER, T.EOF]


def test_e19_in_list_and_not_equal():
    dsl = "SHOW volume BY issuer WHERE card_type IN ('Credit', 'Debit') AND status != 'Success'"
    assert types(dsl) == [
        T.SHOW, T.IDENTIFIER, T.BY, T.IDENTIFIER,
        T.WHERE, T.IDENTIFIER, T.IN, T.LPAREN, T.STRING, T.COMMA, T.STRING, T.RPAREN,
        T.AND, T.IDENTIFIER, T.NEQ, T.STRING, T.EOF,
    ]
    assert values(dsl)[8] == "Credit"
    assert values(dsl)[10] == "Debit"


def test_not_in_list():
    assert types("card_type NOT IN ('Prepaid')") == [
        T.IDENTIFIER, T.NOT, T.IN, T.LPAREN, T.STRING, T.RPAREN, T.EOF,
    ]


def test_list_in_and_unit_in_are_the_same_token():
    # the parser tells them apart by position and by the next token
    tokens = tokenize("WHERE card_type IN ('Credit') IN CRORE")
    assert [t.type for t in tokens if t.value == "IN"] == [T.IN, T.IN]
    assert tokens[3].type is T.LPAREN
    assert tokens[7].type is T.UNIT


def test_parens_need_no_surrounding_space():
    assert types("IN('a','b')") == [
        T.IN, T.LPAREN, T.STRING, T.COMMA, T.STRING, T.RPAREN, T.EOF,
    ]


def test_not_equal_spelled_sql_style_raises_with_hint():
    with pytest.raises(LexError) as exc:
        tokenize("WHERE status <> 'Success'")
    assert exc.value.text == "<>"
    assert exc.value.position == 13
    assert "!=" in str(exc.value)


def test_lone_bang_raises():
    with pytest.raises(LexError) as exc:
        tokenize("WHERE status ! 'Success'")
    assert exc.value.text == "!"
    assert exc.value.position == 13


def test_having_keyword_is_case_insensitive():
    assert tokenize("having")[0] == Token(T.HAVING, "HAVING", 0)


def test_decimal_is_exact():
    token = tokenize("HAVING success_rate < 0.9")[3]
    assert token == Token(T.DECIMAL, Decimal("0.9"), 22)
    assert str(token.value) == "0.9"  # prints back exactly, unlike a float


def test_integer_stays_integer():
    token = tokenize("LIMIT 25")[1]
    assert token.type is T.INTEGER
    assert token.value == 25 and isinstance(token.value, int)


# --- Lexing rules ------------------------------------------------------------

def test_keywords_are_case_insensitive_and_canonicalized():
    tokens = tokenize("show value by issuer period mtd in crore as pie")
    assert [t.type for t in tokens] == [
        T.SHOW, T.IDENTIFIER, T.BY, T.IDENTIFIER,
        T.PERIOD, T.PERIOD_SPEC, T.IN, T.UNIT, T.AS, T.CHART_TYPE, T.EOF,
    ]
    assert [t.value for t in tokens] == [
        "SHOW", "value", "BY", "issuer", "PERIOD", "MTD", "IN", "CRORE", "AS", "PIE", "",
    ]


def test_identifier_case_is_preserved():
    assert tokenize("SHOW Spend_Per_Card")[1].value == "Spend_Per_Card"


def test_punctuation_needs_no_surrounding_space():
    dsl = "SHOW value BY issuer,acquirer WHERE card_type='Credit'"
    assert types(dsl) == [
        T.SHOW, T.IDENTIFIER, T.BY, T.IDENTIFIER, T.COMMA, T.IDENTIFIER,
        T.WHERE, T.IDENTIFIER, T.EQUALS, T.STRING, T.EOF,
    ]


def test_positions_point_at_the_original_string():
    dsl = "SHOW value BY issuer"
    tokens = tokenize(dsl)
    assert [t.position for t in tokens] == [0, 5, 11, 14, len(dsl)]
    for token in tokens[:-1]:
        assert dsl[token.position:].upper().startswith(str(token.value).upper())


def test_extra_whitespace_produces_no_tokens():
    assert types("  SHOW\tvalue\n BY   issuer  ") == [
        T.SHOW, T.IDENTIFIER, T.BY, T.IDENTIFIER, T.EOF,
    ]


def test_empty_input_is_just_eof():
    assert tokenize("") == [Token(T.EOF, "", 0)]


def test_string_keeps_inner_case_spacing_and_keywords():
    # 'Show BY' inside quotes is data, not syntax.
    assert tokenize("WHERE x = '  Show BY  '")[3].value == "  Show BY  "


def test_empty_string_literal():
    assert tokenize("WHERE x = ''")[3] == Token(T.STRING, "", 10)


def test_keyword_words_are_not_identifiers():
    # A future metric literally named "table" would lex as CHART_TYPE, by design.
    assert tokenize("SHOW table")[1].type is T.CHART_TYPE


# --- Errors ------------------------------------------------------------------

def test_stray_symbol_raises():
    with pytest.raises(LexError) as exc:
        tokenize("SHOW value # BY issuer")
    assert exc.value.position == 11
    assert exc.value.text == "#"
    assert "position 11" in str(exc.value)


def test_unterminated_string_raises():
    dsl = "SHOW volume WHERE card_type = 'Credit"
    with pytest.raises(LexError) as exc:
        tokenize(dsl)
    assert exc.value.position == dsl.index("'")
    assert exc.value.text == "'Credit"
    assert "Unterminated string literal" in str(exc.value)


def test_number_glued_to_word_raises():
    with pytest.raises(LexError) as exc:
        tokenize("SHOW volume PERIOD LAST 90days")
    assert exc.value.text == "90days"
    assert exc.value.position == 24


@pytest.mark.parametrize("bad", ["5.", "1.2.3", "0.9x", "3.x"])
def test_malformed_decimal_raises(bad):
    with pytest.raises(LexError) as exc:
        tokenize(f"HAVING value > {bad}")
    assert exc.value.text == bad
    assert exc.value.position == 15


def test_decimal_must_start_with_a_digit():
    with pytest.raises(LexError) as exc:
        tokenize("HAVING value > .5")
    assert exc.value.text == "."


@pytest.mark.parametrize("bad", ["@", "#", "$", "%", ";", "[", "]", '"', "-", "!"])
def test_unclassifiable_characters_raise(bad):
    with pytest.raises(LexError):
        tokenize(f"SHOW value {bad}")
