"""LLM providers behind one tiny interface: `complete_json(system, user) -> dict`.

Plain HTTPS calls with httpx (already a dependency), so no vendor SDKs. Pick with LLM_PROVIDER:
- gemini (default; free tier with a key from Google AI Studio): GEMINI_API_KEY
- claude: ANTHROPIC_API_KEY
- openai: OPENAI_API_KEY
- rules: no LLM at all; the offline keyword classifier

With no key for the chosen provider, `get_llm` returns None and the agent runs on rules.
The LLM is only asked to *understand* a message. It never writes the reply.
"""
from __future__ import annotations

import json
import logging
import re

import httpx

log = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "claude": "claude-haiku-5-5",
    "openai": "gpt-4.1-mini",
}


class LLMError(Exception):
    pass


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise LLMError(f"no JSON object in LLM reply: {text[:120]!r}")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise LLMError("LLM reply is not a JSON object")
    return data


class LLMProvider:
    name = "base"

    def __init__(self, api_key: str, model: str = "", timeout_s: float = 20,
                 transport: httpx.BaseTransport | None = None):
        self.api_key = api_key
        self.model = model or DEFAULT_MODELS[self.name]
        self._client = httpx.Client(timeout=timeout_s, transport=transport)

    def complete_json(self, system: str, user: str) -> dict:
        try:
            return _parse_json(self._call(system, user))
        except httpx.HTTPStatusError as e:
            raise LLMError(f"{self.name} HTTP {e.response.status_code}: {e.response.text[:200]}") from e
        except httpx.HTTPError as e:
            raise LLMError(f"{self.name} unreachable: {e}") from e
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            raise LLMError(f"{self.name} returned an unexpected response: {e}") from e

    def _call(self, system: str, user: str) -> str:
        raise NotImplementedError


class GeminiProvider(LLMProvider):
    name = "gemini"

    def _call(self, system: str, user: str) -> str:
        r = self._client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self.api_key},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
            },
        )
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]


class ClaudeProvider(LLMProvider):
    name = "claude"

    def _call(self, system: str, user: str) -> str:
        r = self._client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            json={"model": self.model, "max_tokens": 600, "system": system,
                  "messages": [{"role": "user", "content": user}]},
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")


class OpenAIProvider(LLMProvider):
    name = "openai"

    def _call(self, system: str, user: str) -> str:
        r = self._client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


PROVIDERS = {"gemini": GeminiProvider, "claude": ClaudeProvider, "openai": OpenAIProvider}


def get_llm(provider: str, api_key: str, model: str = "", timeout_s: float = 20) -> LLMProvider | None:
    provider = (provider or "rules").lower()
    if provider == "rules":
        return None
    if provider not in PROVIDERS:
        raise ValueError(f"LLM_PROVIDER must be one of gemini, claude, openai, rules (got {provider!r})")
    if not api_key:
        log.warning("LLM_PROVIDER=%s but no API key set; using the offline rules classifier", provider)
        return None
    return PROVIDERS[provider](api_key, model, timeout_s)
