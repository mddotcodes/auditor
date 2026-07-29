"""Multi-provider LLM client (OpenAI / Anthropic / OpenRouter).

Supports **single-model** mode and optional **dual plan/code** mode
(strong planner for the test plan, cheaper implementer for Solidity).

This is *not* dual-model consensus — one planner, one implementer.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from enum import StrEnum
from typing import Any, Literal

Role = Literal["plan", "code", "repair", "default"]


class LlmRole(StrEnum):
    PLAN = "plan"
    CODE = "code"
    REPAIR = "repair"
    DEFAULT = "default"


# Recommended OpenRouter defaults (plan = stronger reasoning, code = cheaper/faster).
DEFAULT_PLAN_MODEL = "deepseek/deepseek-v4-pro"
DEFAULT_CODE_MODEL = "deepseek/deepseek-v4-flash"
DEFAULT_SINGLE_OPENROUTER = DEFAULT_CODE_MODEL
DEFAULT_SINGLE_OPENAI = "gpt-4o-mini"
DEFAULT_SINGLE_ANTHROPIC = "claude-3-5-haiku-latest"


def _ssl_context() -> ssl.SSLContext:
    """Use certifi CA bundle when available (fixes macOS Python.org SSL failures)."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def llm_available() -> bool:
    return bool(
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
    )


def dual_mode_enabled() -> bool:
    """True when plan/implement split is on (AUDIT_LLM_DUAL)."""
    raw = os.environ.get("AUDIT_LLM_DUAL", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def model_for_role(role: Role | LlmRole = "default") -> str:
    """Resolve model id for a role under the active provider defaults."""
    r = LlmRole(role) if not isinstance(role, LlmRole) else role
    single = os.environ.get("AUDIT_LLM_MODEL", "").strip()

    if dual_mode_enabled():
        if r is LlmRole.PLAN:
            return (
                os.environ.get("AUDIT_LLM_PLAN_MODEL", "").strip() or single or DEFAULT_PLAN_MODEL
            )
        if r is LlmRole.CODE:
            return (
                os.environ.get("AUDIT_LLM_CODE_MODEL", "").strip() or single or DEFAULT_CODE_MODEL
            )
        if r is LlmRole.REPAIR:
            return (
                os.environ.get("AUDIT_LLM_REPAIR_MODEL", "").strip()
                or os.environ.get("AUDIT_LLM_CODE_MODEL", "").strip()
                or single
                or DEFAULT_CODE_MODEL
            )

    # Single-model path
    if single:
        return single
    if os.environ.get("OPENROUTER_API_KEY"):
        return DEFAULT_SINGLE_OPENROUTER
    if os.environ.get("OPENAI_API_KEY"):
        return DEFAULT_SINGLE_OPENAI
    if os.environ.get("ANTHROPIC_API_KEY"):
        return DEFAULT_SINGLE_ANTHROPIC
    return DEFAULT_SINGLE_OPENROUTER


def complete_text(
    prompt: str,
    *,
    max_tokens: int = 2000,
    role: Role | LlmRole = "default",
    system: str | None = None,
) -> str:
    """Return assistant text from the first configured provider.

    ``role`` selects model when ``AUDIT_LLM_DUAL=true``:
    plan → AUDIT_LLM_PLAN_MODEL, code/repair → code (or repair override).
    """
    model = model_for_role(role)
    system_msg = system or _default_system(role)

    if os.environ.get("OPENROUTER_API_KEY"):
        return _openai_compatible(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
            model=model,
            prompt=prompt,
            max_tokens=max_tokens,
            system=system_msg,
        )
    if os.environ.get("OPENAI_API_KEY"):
        return _openai_compatible(
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            api_key=os.environ["OPENAI_API_KEY"],
            model=model,
            prompt=prompt,
            max_tokens=max_tokens,
            system=system_msg,
        )
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _anthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            model=model,
            prompt=prompt,
            max_tokens=max_tokens,
            system=system_msg,
        )
    msg = "No LLM API key configured (OPENAI_API_KEY / ANTHROPIC_API_KEY / OPENROUTER_API_KEY)"
    raise RuntimeError(msg)


def _default_system(role: Role | LlmRole) -> str:
    r = LlmRole(role) if not isinstance(role, LlmRole) else role
    if r is LlmRole.PLAN:
        return (
            "You are a senior smart-contract security engineer. "
            "You produce structured test plans only — not full source code."
        )
    if r is LlmRole.REPAIR:
        return (
            "You are a careful Solidity engineer. "
            "You fix Foundry test compile errors with minimal changes."
        )
    return "You are a careful Solidity security engineer who writes Foundry tests."


def _openai_compatible(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    max_tokens: int,
    system: str,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    data = _post_json(url, body, headers={"Authorization": f"Bearer {api_key}"})
    return str(data["choices"][0]["message"]["content"])


def _anthropic(
    *,
    api_key: str,
    model: str,
    prompt: str,
    max_tokens: int,
    system: str,
) -> str:
    url = "https://api.anthropic.com/v1/messages"
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
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
        with urllib.request.urlopen(req, timeout=180, context=_ssl_context()) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        msg = f"LLM HTTP {exc.code}: {detail}"
        raise RuntimeError(msg) from exc
    if not isinstance(payload, dict):
        msg = "LLM response was not a JSON object"
        raise RuntimeError(msg)
    return payload
