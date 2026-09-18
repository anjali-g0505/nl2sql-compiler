#lexer doesn't read config.yaml, it only tokenizes the DSL string into a flat list of tokens for the parser to consume. 

"""Lexer for the DSL defined in grammar.md.

Turns a raw DSL string into a flat list of tokens for the parser to consume:

    SHOW value BY merchant PERIOD MTD LIMIT 25 IN CRORE
    -> [SHOW, IDENTIFIER('value'), BY, IDENTIFIER('merchant'), PERIOD,
        PERIOD_SPEC('MTD'), LIMIT, INTEGER(25), IN, UNIT('CRORE'), EOF]

Deliberately knows nothing about config.yaml: whether 'value' is a real metric is a
later semantic-validation concern. Every word that is not fixed DSL syntax becomes an
IDENTIFIER, valid or not.

Keyword matching is case-insensitive and canonicalized to uppercase;
IDENTIFIER and STRING values keep their original case, because validation matches them against config.yaml keys
and database values case-sensitively.
"""
from dataclasses import dataclass
from enum import Enum
from typing import List, Union


class TokenType(Enum): #This class defines the different types of tokens that can be recognized by the lexer. Each token type corresponds to a specific keyword, punctuation, or category of values in the DSL.
    # Fixed keywords
    SHOW = "SHOW" 
    BY = "BY"
    WHERE = "WHERE"
    AND = "AND"
    PERIOD = "PERIOD"
    ORDER = "ORDER"
    LIMIT = "LIMIT"
    IN = "IN"
    AS = "AS"
    ASC = "ASC"
    DESC = "DESC"
    LAST = "LAST"
    DAYS = "DAYS"

    # Closed value sets
    PERIOD_SPEC = "PERIOD_SPEC"      # FTD | WTD | MTD | QTD | YTD
    UNIT = "UNIT"                    # CRORE | LAKH
    CHART_TYPE = "CHART_TYPE"        # TABLE | KPI | BAR | LINE | PIE

    # Open categories
    IDENTIFIER = "IDENTIFIER"        # metric / dimension names, original case
    STRING = "STRING"                # 'Business Decline', quotes stripped
    INTEGER = "INTEGER"              # LIMIT n, LAST n DAYS

    # Punctuation and end marker
    COMMA = "COMMA"
    EQUALS = "EQUALS"
    EOF = "EOF"


# Resolution order for a bare word: fixed keywords first, then the closed value sets,
# then IDENTIFIER as the fallback. Keys are uppercase; lookups uppercase the word.

KEYWORDS = { #again this is a dictionary that maps the uppercase representation of each keyword to its corresponding TokenType. This allows the lexer to quickly identify and categorize keywords in the DSL input.
    "SHOW": TokenType.SHOW,
    "BY": TokenType.BY,
    "WHERE": TokenType.WHERE,
    "AND": TokenType.AND,
    "PERIOD": TokenType.PERIOD,
    "ORDER": TokenType.ORDER,
    "LIMIT": TokenType.LIMIT,
    "IN": TokenType.IN,
    "AS": TokenType.AS,
    "ASC": TokenType.ASC,
    "DESC": TokenType.DESC,
    "LAST": TokenType.LAST,
    "DAYS": TokenType.DAYS,
}
PERIOD_SPECS = frozenset({"FTD", "WTD", "MTD", "QTD", "YTD"})
UNITS = frozenset({"CRORE", "LAKH"})
CHART_TYPES = frozenset({"TABLE", "KPI", "BAR", "LINE", "PIE"})

QUOTE = "'"
#this is a constant that represents the single quote character, which is used to delimit string literals in the DSL. The lexer uses this constant to identify and extract string values enclosed in single quotes.
WORD_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_") #this is a set of characters that can be used to start an identifier or keyword in the DSL.
WORD_CHARS = WORD_START | set("0123456789") #this is a set of characters that can be used in the body of an identifier or keyword in the DSL.
#Identifiers and keywords can contain any uppercase or lowercase letter, any digit, or an underscore. 
#The WORD_CHARS set is used to determine the valid characters that can be part of an identifier or keyword when tokenizing the DSL input.
DIGITS = set("0123456789")

@dataclass(frozen=True) 
#this is a decorator that is used to define the Token class as an immutable data class. The frozen=True parameter makes the instances of the Token class immutable, meaning that their attributes cannot be modified after they are created.
#thus this is helpful for representing tokens, which should not change once they are created during the lexing process.
class Token:
    type: TokenType
    value: Union[str, int] #it represents the value associated with the token. The value can be either a string (for identifiers, strings, and keywords) or an integer (for numeric values).
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

        if char == ",":
            tokens.append(Token(TokenType.COMMA, ",", i))
            i += 1
            continue

        if char == "=":
            tokens.append(Token(TokenType.EQUALS, "=", i))
            i += 1
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
                if not word.isdigit():  # e.g. "90days" — a typo, not two tokens
                    raise LexError(f"Invalid number {word!r}", word, start)
                tokens.append(Token(TokenType.INTEGER, int(word), start))
                continue

            upper = word.upper()
            if upper in KEYWORDS:
                tokens.append(Token(KEYWORDS[upper], upper, start))
            elif upper in PERIOD_SPECS:
                tokens.append(Token(TokenType.PERIOD_SPEC, upper, start))
            elif upper in UNITS:
                tokens.append(Token(TokenType.UNIT, upper, start))
            elif upper in CHART_TYPES:
                tokens.append(Token(TokenType.CHART_TYPE, upper, start))
            else:
                tokens.append(Token(TokenType.IDENTIFIER, word, start))  # original case
            continue

        raise LexError(f"Unexpected character {char!r}", char, i)

    tokens.append(Token(TokenType.EOF, "", length))
    return tokens
