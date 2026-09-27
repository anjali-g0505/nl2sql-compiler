"""One place that talks to Groq.

Both callers — the translator writing DSL and the answerer writing prose — need the same
request shape, the same failure handling and the same rate-limit fallback, so it lives
here rather than twice.

Failures are deliberately plain: a caller gets `ModelError` with a sentence fit to show a
user, never a stack trace or a provider-specific code.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_FALLBACK_MODEL = "openai/gpt-oss-20b"  # its own rate limit; used on a 429


class ModelError(Exception):
    """The model could not be reached or refused the request. Not the user's fault."""


class RateLimited(ModelError):
    """This model is over its quota; another may still answer."""


@dataclass
class GroqChat:
    """A chat completion, with the fallback model tried once on a rate limit."""

    api_key: str
    model: str = DEFAULT_MODEL
    fallback_model: Optional[str] = DEFAULT_FALLBACK_MODEL
    client: Optional[httpx.Client] = None
    timeout: float = 30.0

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = httpx.Client(timeout=self.timeout)
        if self.fallback_model == self.model:
            self.fallback_model = None

    def complete(self, messages: Sequence[Dict[str, str]], max_tokens: int = 1024,
                 temperature: float = 0.0) -> str:
        """The assistant's reply as text, or ModelError."""
        try:
            return self._once(self.model, messages, max_tokens, temperature)
        except RateLimited:
            if not self.fallback_model:
                raise ModelError("The language model is rate limited; try again in a minute.") from None
            try:
                return self._once(self.fallback_model, messages, max_tokens, temperature)
            except RateLimited:
                raise ModelError("The language model is rate limited; try again in a minute.") from None

    def _once(self, model: str, messages: Sequence[Dict[str, str]], max_tokens: int,
              temperature: float) -> str:
        body: Dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "temperature": temperature,
            "max_completion_tokens": max_tokens,  # reasoning tokens count here too
        }
        if model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"   # these answers need little deliberation
            body["include_reasoning"] = False  # we use the reply, not the thinking
        try:
            response = self.client.post(
                GROQ_URL, json=body, headers={"Authorization": f"Bearer {self.api_key}"}
            )
        except httpx.TimeoutException:
            raise ModelError("The language model timed out.") from None
        except httpx.HTTPError as exc:
            raise ModelError(f"Could not reach the language model: {type(exc).__name__}") from None

        if response.status_code == 429:
            raise RateLimited()
        if response.status_code != 200:
            raise ModelError(
                f"The language model returned HTTP {response.status_code}: {_error_message(response)}"
            )
        try:
            return response.json()["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError):
            raise ModelError("The language model returned an unexpected response.") from None


def _error_message(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return response.text[:200]


def messages(system: str, user: str, extra: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, str]]:
    """The common shape: instructions first, the question last, so a cached prefix holds."""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}, *(extra or [])]
