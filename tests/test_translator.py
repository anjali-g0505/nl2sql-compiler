"""Groq translator tests. The HTTP call is mocked: no API key or network needed."""
import json
from datetime import date

import httpx
import pytest

from app.pipeline import Feedback, TranslatorError, Unanswerable
from app.translator import GroqTranslator, build_system_prompt, clean_reply
from compiler.semantic_layer import SemanticLayer


def reply(content, status=200):
    if status != 200:
        return httpx.Response(status, json={"error": {"message": content, "type": "x"}})
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


class FakeGroq:
    """Answers each request with the next scripted response; records the bodies."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(json.loads(request.content))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def translator(fake, **kwargs):
    client = httpx.Client(transport=httpx.MockTransport(fake))
    return GroqTranslator(api_key="test-key", client=client, **kwargs)


# --- the prompt ---------------------------------------------------------------

def test_prompt_is_built_from_config_and_grammar():
    prompt = build_system_prompt(year=2025)
    layer = SemanticLayer.load()
    for key in list(layer.metrics) + list(layer.dimensions) + list(layer.attributes):
        assert key in prompt                                  # all the vocabulary
    assert "card_type: Card type; values: 'Credit', 'Debit', 'Prepaid'" in prompt
    assert "query      := SHOW metric_list" in prompt           # the grammar
    assert "A date with\n   no year uses 2025." in prompt
    assert 'Q: "Total value by issuer."\nDSL: SHOW value BY issuer' in prompt
    assert prompt.count("\nDSL: ") == 21                        # every oracle example


def test_prompt_says_rates_are_fractions():
    prompt = build_system_prompt(year=2025)
    assert ("active_card_rate, success_rate, business_decline_rate, technical_decline_rate "
            "are fractions between 0 and 1") in prompt
    assert '"under 10%" -> < 0.1' in prompt


def test_prompt_stays_small():
    # the free tier allows 8K tokens a minute; ~4 characters per token
    assert len(build_system_prompt(year=2025)) / 4 < 2500


@pytest.mark.parametrize("raw, expected", [
    ("SHOW value", "SHOW value"),
    ("```\nSHOW value BY issuer\n```", "SHOW value BY issuer"),
    ("```sql\nSHOW value\n```", "SHOW value"),
    ("DSL: SHOW value", "SHOW value"),
    ("SHOW value\n  BY issuer", "SHOW value BY issuer"),
])
def test_clean_reply(raw, expected):
    assert clean_reply(raw) == expected


# --- requests -----------------------------------------------------------------

def test_request_uses_the_model_settings():
    fake = FakeGroq(reply("SHOW value"))
    assert translator(fake).translate("total value?") == "SHOW value"

    body = fake.requests[0]
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["temperature"] == 0
    assert body["reasoning_effort"] == "low"
    assert body["include_reasoning"] is False
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1] == {"role": "user", "content": "total value?"}


def test_non_reasoning_models_get_no_reasoning_parameters():
    fake = FakeGroq(reply("SHOW value"))
    translator(fake, model="llama-3.3-70b-versatile").translate("q")
    assert "reasoning_effort" not in fake.requests[0]


def test_the_reference_year_goes_into_the_prompt():
    fake = FakeGroq(reply("SHOW value"))
    translator(fake, reference_date=lambda: date(2024, 3, 1)).translate("q")
    assert "no year uses 2024." in fake.requests[0]["messages"][0]["content"]


def test_a_retry_shows_the_model_its_answer_and_the_errors():
    fake = FakeGroq(reply("SHOW value"))
    translator(fake).translate(
        "revenue?", Feedback(dsl="SHOW revenue", errors=("unknown metric 'revenue'",))
    )
    messages = fake.requests[0]["messages"]
    assert messages[2] == {"role": "assistant", "content": "SHOW revenue"}
    assert "- unknown metric 'revenue'" in messages[3]["content"]


# --- outcomes -----------------------------------------------------------------

def test_cannot_means_out_of_scope():
    fake = FakeGroq(reply("CANNOT: weather is not in this data"))
    with pytest.raises(Unanswerable) as exc:
        translator(fake).translate("weather?")
    assert str(exc.value) == "weather is not in this data"


def test_rate_limit_falls_back_to_the_second_model():
    fake = FakeGroq(reply("slow down", 429), reply("SHOW value"))
    assert translator(fake).translate("q") == "SHOW value"
    assert [r["model"] for r in fake.requests] == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]


def test_rate_limit_on_both_models_is_an_error():
    fake = FakeGroq(reply("slow down", 429), reply("slow down", 429))
    with pytest.raises(TranslatorError, match="rate limited"):
        translator(fake).translate("q")


def test_rate_limit_without_a_fallback_is_an_error():
    fake = FakeGroq(reply("slow down", 429))
    with pytest.raises(TranslatorError, match="rate limited"):
        translator(fake, fallback_model=None).translate("q")


@pytest.mark.parametrize("response, message", [
    (reply("bad key", 401), "HTTP 401: bad key"),
    (reply(""), "empty answer"),
    (httpx.Response(200, json={"unexpected": True}), "unexpected response"),
    (httpx.ReadTimeout("slow"), "timed out"),
    (httpx.ConnectError("down"), "Could not reach"),
])
def test_failures_become_translator_errors(response, message):
    with pytest.raises(TranslatorError, match=message):
        translator(FakeGroq(response)).translate("q")
