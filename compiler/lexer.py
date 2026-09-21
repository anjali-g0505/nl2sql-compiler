#lexer doesn't read config.yaml, it only tokenizes the DSL string into a flat list of tokens for the parser to consume. 

"""Lexer for the DSL defined in grammar.md.

Turns a raw DSL string into a flat list of tokens for the parser to consume:

    SHOW value BY merchant PERIOD MTD LIMIT 25 IN CRORE
    -> [SHOW, IDENTIFIER('value'), BY, IDENTIFIER('merchant'), PERIOD,
        IDENTIFIER('MTD'), LIMIT, INTEGER(25), IN, IDENTIFIER('CRORE'), EOF]

Deliberately knows nothing about config.yaml: whether 'value' is a real metric is a
later semantic-validation concern. Every word that is not fixed DSL syntax becomes an
IDENTIFIER, valid or not. That includes the closed value sets — period specs (MTD),
units (CRORE) and chart types (PIE) — which live in config.yaml, so new ones are added
there without touching this file, and a dimension may be named 'table' or 'line'.
Only structural keywords (SHOW, BY, PERIOD, AS, LAST, DAYS, FROM, TO, ...) are fixed
syntax here; the validator checks every value against config.

Keyword matching is case-insensitive and canonicalized to uppercase;
IDENTIFIER and STRING values keep their original case, because validation matches them against config.yaml keys
and database values case-sensitively.

Numbers: digits only -> INTEGER (int); digits '.' digits -> DECIMAL (decimal.Decimal, so
the literal prints back exactly in generated SQL). Anything else starting with a digit
("90days", "5.", "1.2.3") is a LexError. Comparison operators (= != > >= < <=) are
lexed anywhere; the parser decides where they are legal. Not-equal is spelled only '!=':
'<>' is rejected with a hint, so the DSL has one spelling for each operator.
Dates are ordinary STRING tokens ('2025-05-12'); the parser converts and checks them.

IN is one token for both uses; the parser tells them apart by what follows:
`card_type IN ('Credit', 'Debit')` (a list, after a field) vs `IN CRORE` (the unit clause).
"""
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import List, Union


class TokenType(Enum): #This class defines the different types of tokens that can be recognized by the lexer. Each token type corresponds to a specific keyword, punctuation, or category of values in the DSL.
    # Fixed keywords
    SHOW = "SHOW" 
    BY = "BY"
    WHERE = "WHERE"
    AND = "AND"
    PERIOD = "PERIOD"
    HAVING = "HAVING"
    ORDER = "ORDER"
    LIMIT = "LIMIT"
    IN = "IN"
    AS = "AS"
    ASC = "ASC"
    DESC = "DESC"
    LAST = "LAST"
    DAYS = "DAYS"
    FROM = "FROM"                    # PERIOD FROM 'date' TO 'date'
    TO = "TO"
    NOT = "NOT"                      # only in NOT IN (...)

    # Open categories
    IDENTIFIER = "IDENTIFIER"        # metric / dimension / attribute names, and the config-defined values for period_spec, unit and chart_type: MTD, CRORE, PIE, ...
    STRING = "STRING"                # 'Business Decline', quotes stripped
    INTEGER = "INTEGER"              # LIMIT n, LAST n DAYS, HAVING thresholds
    DECIMAL = "DECIMAL"              # HAVING thresholds like 0.9

    # Punctuation, operators and end marker
    COMMA = "COMMA"
    LPAREN = "LPAREN"                # ( — opens an IN list
    RPAREN = "RPAREN"                # )
    EQUALS = "EQUALS"
    NEQ = "NEQ"                      # !=
    GT = "GT"                        # >
    GTE = "GTE"                      # >=
    LT = "LT"                        # <
    LTE = "LTE"                      # <=
    EOF = "EOF"


# Resolution order for a bare word: fixed keywords first, then the closed value sets,
# then IDENTIFIER as the fallback. Keys are uppercase; lookups uppercase the word.

KEYWORDS = { #again this is a dictionary that maps the uppercase representation of each keyword to its corresponding TokenType. This allows the lexer to quickly identify and categorize keywords in the DSL input.
    "SHOW": TokenType.SHOW,
    "BY": TokenType.BY,
    "WHERE": TokenType.WHERE,
    "AND": TokenType.AND,
    "PERIOD": TokenType.PERIOD,
    "HAVING": TokenType.HAVING,
    "ORDER": TokenType.ORDER,
    "LIMIT": TokenType.LIMIT,
    "IN": TokenType.IN,
    "AS": TokenType.AS,
    "ASC": TokenType.ASC,
    "DESC": TokenType.DESC,
    "LAST": TokenType.LAST,
    "DAYS": TokenType.DAYS,
    "FROM": TokenType.FROM,
    "TO": TokenType.TO,
    "NOT": TokenType.NOT,
}

QUOTE = "'"
#this is a constant that represents the single quote character, which is used to delimit string literals in the DSL. The lexer uses this constant to identify and extract string values enclosed in single quotes.
WORD_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_") #this is a set of characters that can be used to start an identifier or keyword in the DSL.
WORD_CHARS = WORD_START | set("0123456789") #this is a set of characters that can be used in the body of an identifier or keyword in the DSL.
#Identifiers and keywords can contain any uppercase or lowercase letter, any digit, or an underscore. 
#The WORD_CHARS set is used to determine the valid characters that can be part of an identifier or keyword when tokenizing the DSL input.
DIGITS = set("0123456789")
# Two-character operators are listed so the lexer can prefer ">=" over ">" then "=".
COMPARISONS = {
    ">=": TokenType.GTE,
    "<=": TokenType.LTE,
    "!=": TokenType.NEQ,
    ">": TokenType.GT,
    "<": TokenType.LT,
}
PUNCTUATION = {
    ",": TokenType.COMMA,
    "(": TokenType.LPAREN,
    ")": TokenType.RPAREN,
    "=": TokenType.EQUALS,
}

@dataclass(frozen=True) 
#this is a decorator that is used to define the Token class as an immutable data class. The frozen=True parameter makes the instances of the Token class immutable, meaning that their attributes cannot be modified after they are created.
#thus this is helpful for representing tokens, which should not change once they are created during the lexing process.
class Token:
    type: TokenType
    value: Union[str, int, Decimal] #it represents the value associated with the token. The value can be either a string (for identifiers, strings, and keywords) or an integer (for numeric values).
    position: int  # character offset of the token's first character in the input

    def __repr__(self) -> str:  # keeps pytest failure output readable
        return f"Token({self.type.name}, {self.value!r}, at {self.position})"

class LexError(Exception):
    """Raised when a character or substring cannot be tokenized."""

    def __init__(self, message: str, text: str, position: int):
        self.text = text          # the offending character or substring
        self.position = position  # its offset in the original DSL string
        super().__init__(f"{message} at position {position}")


def tokenize(dsl: str) -> List[Token]:
    """Tokenize a DSL string. Always ends with a single EOF token."""
    tokens: List[Token] = []
    i = 0
    length = len(dsl)

    while i < length:
        char = dsl[i]

        if char.isspace():
            i += 1
            continue

        if char in PUNCTUATION:
            tokens.append(Token(PUNCTUATION[char], char, i))
            i += 1
            continue

        if char in "<>!":
            if dsl[i:i + 2] == "<>":
                raise LexError("Use '!=' for not-equal, not '<>'", "<>", i)
            op = dsl[i:i + 2] if dsl[i:i + 2] in COMPARISONS else char
            if op not in COMPARISONS:  # a lone '!'
                raise LexError(f"Unexpected character {char!r}", char, i)
            tokens.append(Token(COMPARISONS[op], op, i))
            i += len(op)
            continue

        if char == QUOTE:
            end = dsl.find(QUOTE, i + 1)
            if end == -1:
                raise LexError("Unterminated string literal", dsl[i:], i)
            tokens.append(Token(TokenType.STRING, dsl[i + 1:end], i))
            i = end + 1
            continue

        if char in WORD_CHARS:
            start = i
            while i < length and dsl[i] in WORD_CHARS:
                i += 1
            word = dsl[start:i]

            if char in DIGITS:
                # Take any '.' parts too, so "1.2.3" is one bad number, not three tokens.
                while i < length and (dsl[i] == "." or dsl[i] in WORD_CHARS):
                    i += 1
                word = dsl[start:i]
                whole, dot, frac = word.partition(".")
                if not dot and word.isdigit():
                    tokens.append(Token(TokenType.INTEGER, int(word), start))
                elif dot and whole.isdigit() and frac.isdigit():
                    tokens.append(Token(TokenType.DECIMAL, Decimal(word), start))
                else:  # e.g. "90days", "5.", "1.2.3" — a typo, not several tokens
                    raise LexError(f"Invalid number {word!r}", word, start)
                continue

            upper = word.upper()
            if upper in KEYWORDS:
                tokens.append(Token(KEYWORDS[upper], upper, start))
            else:
                tokens.append(Token(TokenType.IDENTIFIER, word, start))  # original case
            continue

        raise LexError(f"Unexpected character {char!r}", char, i)

    tokens.append(Token(TokenType.EOF, "", length))
    return tokens
