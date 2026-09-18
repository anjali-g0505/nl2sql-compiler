"""Lexer tests. The DSL strings come from the grammar.md oracle (E1-E14)."""
import pytest

from app.lexer import LexError, Token, TokenType, tokenize

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


@pytest.mark.parametrize("bad", ["@", "#", "$", "%", ";", "(", ")", '"', "-"])
def test_unclassifiable_characters_raise(bad):
    with pytest.raises(LexError):
        tokenize(f"SHOW value {bad}")
