"""LLM client. Groq, Anthropic, and OpenAI share one function. Mock is the default."""

from __future__ import annotations

import json
import re

import httpx

from caretrace.config import get_settings

INTENT_RULES = [
    ("audit", ("audit", "billed", "bill", "eob", "charged")),
    ("coverage", ("prior auth", "prior authorization", "authorization", "documentation")),
    ("network", ("in network", "in-network", "out of network", "npi", "provider")),
    (
        "estimate",
        (
            "cost",
            "price",
            "how much",
            "mri",
            "estimate",
            "out of pocket",
            "i need",
            "negotiated",
            "rate",
        ),
    ),
]


def complete(prompt: str, system: str = "") -> str:
    """Send one completion. Callers must not instantiate provider SDKs themselves."""
    settings = get_settings()
    provider = settings.llm_provider
    if provider == "mock":
        return mock_complete(prompt, system)
    if provider == "groq":
        return _groq(prompt, system, settings.llm_model, settings.groq_api_key)
    if provider == "anthropic":
        return _anthropic(prompt, system, settings.llm_model, settings.anthropic_api_key)
    if provider == "openai":
        return _openai(prompt, system, settings.llm_model, settings.openai_api_key)
    raise ValueError(f"unknown LLM provider {provider}")


def mock_complete(prompt: str, system: str = "") -> str:
    """Deterministic stand-in used by tests and the offline demo."""
    if "classify_intent" in system:
        return json.dumps(classify_intent(prompt))
    if "narrative" in system:
        return "Please review the cited discrepancies and reprocess the claim."
    if "extract_eob" in system:
        return "[]"
    return (
        "I can help with prices, network status, prior authorization, and bill audits. "
        "I only report figures that come back from cited tools."
    )


def classify_intent(text: str) -> dict[str, object]:
    """Keyword router shared by the mock model and the intent tests."""
    lowered = text.lower()
    intent = "general"
    for name, words in INTENT_RULES:
        if any(word in lowered for word in words):
            intent = name
            break
    full = intent == "estimate" and (
        "i need" in lowered or "and prior" in lowered or "in network" in lowered
    )
    return {"intent": intent, "full_journey": full}


def _groq(prompt: str, system: str, model: str, api_key: str) -> str:
    from groq import Groq

    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=0,
    )
    return str(response.choices[0].message.content or "")


def _anthropic(prompt: str, system: str, model: str, api_key: str) -> str:
    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        json={
            "model": model,
            "max_tokens": 800,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    return str(data["content"][0]["text"])


def _openai(prompt: str, system: str, model: str, api_key: str) -> str:
    response = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=60,
    )
    response.raise_for_status()
    return str(response.json()["choices"][0]["message"]["content"])


def numbers_in(text: str) -> list[str]:
    """Digit sequences that the verifier treats as claims."""
    return re.findall(r"\d[\d,]*(?:\.\d+)?", text)
