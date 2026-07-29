"""Minimal multi-provider LLM client (OpenAI / Anthropic / OpenRouter)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


def llm_available() -> bool:
    return bool(
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
    )


def complete_text(prompt: str, *, max_tokens: int = 2000) -> str:
    """Return assistant text from the first configured provider."""
    if os.environ.get("OPENROUTER_API_KEY"):
        return _openai_compatible(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
            model=os.environ.get("AUDIT_LLM_MODEL", "openai/gpt-4o-mini"),
            prompt=prompt,
            max_tokens=max_tokens,
        )
    if os.environ.get("OPENAI_API_KEY"):
        return _openai_compatible(
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            api_key=os.environ["OPENAI_API_KEY"],
            model=os.environ.get("AUDIT_LLM_MODEL", "gpt-4o-mini"),
            prompt=prompt,
            max_tokens=max_tokens,
        )
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _anthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            model=os.environ.get("AUDIT_LLM_MODEL", "claude-3-5-haiku-latest"),
            prompt=prompt,
            max_tokens=max_tokens,
        )
    msg = "No LLM API key configured (OPENAI_API_KEY / ANTHROPIC_API_KEY / OPENROUTER_API_KEY)"
    raise RuntimeError(msg)


def _openai_compatible(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    max_tokens: int,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a careful Solidity security engineer."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    data = _post_json(url, body, headers={"Authorization": f"Bearer {api_key}"})
    return str(data["choices"][0]["message"]["content"])


def _anthropic(*, api_key: str, model: str, prompt: str, max_tokens: int) -> str:
    url = "https://api.anthropic.com/v1/messages"
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    data = _post_json(
        url,
        body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    parts = data.get("content") or []
    texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
    return "\n".join(texts)


def _post_json(url: str, body: dict[str, Any], *, headers: dict[str, str]) -> dict[str, Any]:
    raw = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=raw,
        headers={"content-type": "application/json", "user-agent": "auditor-engine", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        msg = f"LLM HTTP {exc.code}: {detail}"
        raise RuntimeError(msg) from exc
    if not isinstance(payload, dict):
        msg = "LLM response was not a JSON object"
        raise RuntimeError(msg)
    return payload
