"""Which of the three things is this message asking for?

    DATA       run a query and show rows        "value by issuer this month"
    EXPLAIN    interpret the result on screen   "why is that so high?"
    KNOWLEDGE  answer from the documentation    "what does do not honor mean"

The routing is cheap on purpose. Two rules run first because they are unambiguous and
save a 2,000-token call; everything else goes to the data path, and **the compiler is
the classifier**: if the translator says the question cannot be expressed in the data
vocabulary, it was not a data question, and the knowledge path takes it.

That last part matters. There is no separate intent model to train, tune or keep in step
with the vocabulary — the thing that knows what the data can answer is the thing that
decides.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class Route(str, Enum):
    DATA = "data"
    EXPLAIN = "explain"
    KNOWLEDGE = "knowledge"


# "this", "that", "these" and friends only make sense about something already on screen —
# except in a date phrase, where "this month" points at a period, not at the last answer
REFERS_BACK = re.compile(
    r"\b(?:(this|that|these|those|it|above)\b(?!\s+(?:month|week|year|quarter|day|period|time))"
    r"|the (?:result|table|chart|number|figure|answer))\b",
    re.I,
)
ASKS_TO_EXPLAIN = re.compile(
    r"\b(explain|interpret|what does (this|that|it) mean|why is (this|that|it)|what am i "
    r"looking at|break (this|that|it) down|walk me through)\b",
    re.I,
)
# definitional phrasing that never needs a query
DEFINITIONAL = re.compile(
    r"^\s*(what (is|are|does|do)\b(?!.*\b(by|per|for each|this month|last|top|highest|lowest)\b)"
    r"|what'?s the (difference|meaning)"
    r"|how (is|are) \w+ (calculated|computed|defined)"
    r"|how does (it|the system|intentql)"
    r"|can (it|the system|intentql|you)\b"
    r"|why (does|do|doesn'?t|don'?t) (it|the system|intentql|the model|the compiler)"
    r"|tell me about\b"
    r"|define\b)",
    re.I,
)


def route(message: str, has_result: bool) -> Route:
    """Where to send a message. `has_result` is whether a previous result is in hand."""
    text = message.strip()
    # an explicit "explain this" is an explanation even with nothing on screen: the
    # honest reply is "there is no result yet", not a query invented from the words
    if ASKS_TO_EXPLAIN.search(text) or (has_result and _is_bare_follow_up(text)):
        return Route.EXPLAIN
    if DEFINITIONAL.search(text):
        return Route.KNOWLEDGE
    return Route.DATA


def _is_bare_follow_up(text: str) -> bool:
    """A short question that points at something already shown, e.g. "why is that high?".

    Length matters: "why is that high" is about the result, while "why is the decline
    rate for prepaid cards higher than credit in the last quarter" is a fresh question
    that happens to contain "that".
    """
    return bool(REFERS_BACK.search(text)) and len(text.split()) <= 12


def fallback_route(current: Route) -> Optional[Route]:
    """Where to go when a route comes back empty-handed.

    A data question the compiler cannot express is usually a knowledge question; a
    knowledge question with nothing retrieved is simply unanswered, not a data question,
    because inventing a query for it would answer something the user did not ask.
    """
    return Route.KNOWLEDGE if current is Route.DATA else None
