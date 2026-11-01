"""LLM providers (fake HTTP transport, no network) and how their output is checked."""
import json
from datetime import date

import httpx
import pytest

from revert.agent.llm import ClaudeProvider, GeminiProvider, LLMError, OpenAIProvider, get_llm
from revert.agent.parse import Installment
from revert.agent.understand import understand

TODAY = date(2026, 10, 30)


def fake(reply_json: dict | str, status: int = 200, seen: list | None = None):
    text = reply_json if isinstance(reply_json, str) else json.dumps(reply_json)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": "quota"})
        host = request.url.host
        if "googleapis" in host:
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})
        if "anthropic" in host:
            return httpx.Response(200, json={"content": [{"type": "text", "text": text}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    return httpx.MockTransport(handler)


@pytest.mark.parametrize("cls,host,key_header", [
    (GeminiProvider, "generativelanguage.googleapis.com", "x-goog-api-key"),
    (ClaudeProvider, "api.anthropic.com", "x-api-key"),
    (OpenAIProvider, "api.openai.com", "authorization"),
])
def test_providers_send_key_in_header_and_parse_json(cls, host, key_header):
    seen = []
    llm = cls("secret-key", transport=fake({"intent": "smalltalk"}, seen=seen))
    assert llm.complete_json("sys", "hi") == {"intent": "smalltalk"}
    req = seen[0]
    assert req.url.host == host and "secret-key" in req.headers[key_header]
    assert "secret-key" not in str(req.url)  # never in the URL, where it could end up in logs


def test_llm_understanding_is_used_when_valid():
    llm = GeminiProvider("k", transport=fake({
        "intent": "promise_to_pay", "language": "hi", "upset": False,
        "installments": [{"amount_rupees": 50000, "date": "2026-10-30"},
                         {"amount_rupees": None, "date": "2026-11-15", "rest": True}],
    }))
    u = understand("abhi pachas hazaar, baaki 15 tarikh ko - 50000 abhi", TODAY, "en", llm)
    assert u.engine == "llm:gemini" and u.intent == "promise_to_pay" and u.language == "hi"
    assert u.installments == (Installment(50_000_00, TODAY), Installment(None, date(2026, 11, 15), rest=True))


def test_llm_amount_not_in_the_message_is_rejected():
    llm = GeminiProvider("k", transport=fake({
        "intent": "promise_to_pay", "language": "en",
        "installments": [{"amount_rupees": 75000, "date": "2026-11-02"}],  # dealer never wrote 75000
    }))
    u = understand("will pay 20k on monday", TODAY, "en", llm)
    assert u.installments == (Installment(20_000_00, date(2026, 11, 2)),)  # the parser's reading wins
    assert any("75000" in n for n in u.notes)


def test_llm_bad_intent_and_far_dates_are_cleaned():
    llm = GeminiProvider("k", transport=fake({
        "intent": "sue_them", "language": "fr", "invoice_refs": ["9999"],
        "installments": [{"amount_rupees": None, "date": "2030-01-01"}],
    }))
    u = understand("about INV-1043", TODAY, "kn", llm)
    assert u.intent == "unclear" and u.language == "en" and u.invoice_refs == ("1043",)


@pytest.mark.parametrize("transport", [fake({}, status=429), fake("not json at all")])
def test_llm_failure_falls_back_to_rules(transport):
    u = understand("I'll pay Friday", TODAY, "en", GeminiProvider("k", transport=transport))
    assert u.engine == "rules" and u.intent == "promise_to_pay"
    assert any("llm failed" in n for n in u.notes)


def test_unreachable_llm_raises_llmerror():
    def boom(request):
        raise httpx.ConnectError("offline")
    with pytest.raises(LLMError):
        GeminiProvider("k", transport=httpx.MockTransport(boom)).complete_json("s", "u")


def test_get_llm_without_key_uses_rules():
    assert get_llm("gemini", "") is None
    assert get_llm("rules", "anything") is None
    assert get_llm("claude", "k").name == "claude"
    with pytest.raises(ValueError):
        get_llm("grok", "k")
