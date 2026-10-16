"""OpenRouter provider-routing helpers for the sealed viability experiment."""

from __future__ import annotations

import os
from typing import Any

# OpenRouter slug for NovitaAI (matches /api/v1/models/.../endpoints provider_name).
DEFAULT_OPENROUTER_PROVIDER_SLUG = "Novita"


def openrouter_provider_routing_from_env() -> dict[str, Any] | None:
    """Return OpenRouter `provider` body prefs when explicitly configured.

    Set BENCHMARK_OPENROUTER_PROVIDER=Novita (or a comma-separated only-list)
    to pin routing. Empty / unset leaves routing to OpenRouter defaults.
    """
    raw = (os.environ.get("BENCHMARK_OPENROUTER_PROVIDER") or "").strip()
    if not raw:
        return None
    only = [part.strip() for part in raw.split(",") if part.strip()]
    if not only:
        return None
    allow_fallbacks = os.environ.get("BENCHMARK_OPENROUTER_ALLOW_FALLBACKS", "0") == "1"
    return {
        "only": only,
        "order": only,
        "allow_fallbacks": allow_fallbacks,
    }


def apply_openrouter_provider_routing(body: dict[str, Any]) -> dict[str, Any]:
    """Copy body and inject sealed OpenRouter provider routing when configured."""
    routing = openrouter_provider_routing_from_env()
    if routing is None:
        return body
    outbound = dict(body)
    outbound["provider"] = routing
    return outbound
