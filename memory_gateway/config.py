from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value else default


def _int_env(name: str, default: int) -> int:
    value = _env(name)
    if value is None:
        return default
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def _float_env(name: str, default: float) -> float:
    value = _env(name)
    if value is None:
        return default
    parsed = float(value)
    if not 0 < parsed < 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return parsed


def _bool_env(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True)
class RuntimeConfig:
    db_path: str
    backend_url: str
    backend_api_key: str | None
    backend_model: str | None
    selector_url: str
    selector_model: str
    selector_api_key: str | None
    canonicalizer_url: str
    canonicalizer_model: str
    canonicalizer_api_key: str | None
    consolidator_url: str | None
    consolidator_model: str | None
    consolidator_api_key: str | None
    context_tokens: int
    background_fraction: float
    urgent_fraction: float
    worker_poll_seconds: float
    model_timeout_seconds: float
    selector_context_tokens: int = 32_768
    canonicalizer_input_tokens: int = 8_192
    lease_seconds: int = 900
    lease_renewal_seconds: float = 30.0
    recover_expired_jobs: bool = False
    cold_memory_mode: str = "off"
    cold_memory_candidate_limit: int = 20
    cold_memory_timeout_ms: int = 50
    cold_memory_token_budget: int = 512
    cold_memory_max_injected: int = 3
    cold_memory_telemetry_queue_size: int = 256
    cold_memory_telemetry_batch_size: int = 32
    cold_memory_telemetry_flush_ms: int = 5

    @classmethod
    def from_env(
        cls,
        *,
        db_path: str | None = None,
        backend_url: str | None = None,
    ) -> "RuntimeConfig":
        lease_seconds = _int_env("ORCHID_LEASE_SECONDS", 900)
        lease_renewal_seconds = float(_env("ORCHID_LEASE_RENEWAL_SECONDS", "30"))
        if lease_renewal_seconds <= 0 or lease_renewal_seconds >= lease_seconds:
            raise ValueError(
                "ORCHID_LEASE_RENEWAL_SECONDS must be positive and shorter "
                "than ORCHID_LEASE_SECONDS"
            )
        cold_memory_mode = (_env("ORCHID_COLD_MEMORY_MODE", "off") or "off").lower()
        if cold_memory_mode not in {"off", "shadow", "inject"}:
            raise ValueError("ORCHID_COLD_MEMORY_MODE must be off, shadow, or inject")
        return cls(
            db_path=db_path or _env("ORCHID_DB", "./data/memory.db"),
            backend_url=(backend_url or _env("ORCHID_BACKEND_URL", "http://127.0.0.1:1234")).rstrip("/"),
            backend_api_key=_env("ORCHID_BACKEND_API_KEY"),
            backend_model=_env("ORCHID_BACKEND_MODEL"),
            selector_url=_env("ORCHID_SELECTOR_URL", "http://127.0.0.1:1234/v1").rstrip("/"),
            selector_model=_env("ORCHID_SELECTOR_MODEL", "qwen3.5-4b@q6_k"),
            selector_api_key=_env("ORCHID_SELECTOR_API_KEY"),
            canonicalizer_url=_env("ORCHID_CANONICALIZER_URL", "http://127.0.0.1:1234/v1").rstrip("/"),
            canonicalizer_model=_env("ORCHID_CANONICALIZER_MODEL", "qwen3.5-4b@q6_k"),
            canonicalizer_api_key=_env("ORCHID_CANONICALIZER_API_KEY"),
            consolidator_url=_env("ORCHID_CONSOLIDATOR_URL"),
            consolidator_model=_env("ORCHID_CONSOLIDATOR_MODEL"),
            consolidator_api_key=_env("ORCHID_CONSOLIDATOR_API_KEY"),
            context_tokens=_int_env("ORCHID_CONTEXT_TOKENS", 32_768),
            background_fraction=_float_env("ORCHID_BACKGROUND_FRACTION", 0.65),
            urgent_fraction=_float_env("ORCHID_URGENT_FRACTION", 0.85),
            worker_poll_seconds=float(_env("ORCHID_WORKER_POLL_SECONDS", "0.25")),
            model_timeout_seconds=float(_env("ORCHID_MODEL_TIMEOUT_SECONDS", "120")),
            selector_context_tokens=_int_env("ORCHID_SELECTOR_CONTEXT_TOKENS", 32_768),
            canonicalizer_input_tokens=_int_env(
                "ORCHID_CANONICALIZER_INPUT_TOKENS",
                8_192,
            ),
            lease_seconds=lease_seconds,
            lease_renewal_seconds=lease_renewal_seconds,
            recover_expired_jobs=_bool_env("ORCHID_RECOVER_EXPIRED_JOBS", False),
            cold_memory_mode=cold_memory_mode,
            cold_memory_candidate_limit=_int_env(
                "ORCHID_COLD_MEMORY_CANDIDATE_LIMIT", 20
            ),
            cold_memory_timeout_ms=_int_env("ORCHID_COLD_MEMORY_TIMEOUT_MS", 50),
            cold_memory_token_budget=_int_env("ORCHID_COLD_MEMORY_TOKEN_BUDGET", 512),
            cold_memory_max_injected=_int_env("ORCHID_COLD_MEMORY_MAX_INJECTED", 3),
            cold_memory_telemetry_queue_size=_int_env(
                "ORCHID_COLD_MEMORY_TELEMETRY_QUEUE_SIZE", 256
            ),
            cold_memory_telemetry_batch_size=_int_env(
                "ORCHID_COLD_MEMORY_TELEMETRY_BATCH_SIZE", 32
            ),
            cold_memory_telemetry_flush_ms=_int_env(
                "ORCHID_COLD_MEMORY_TELEMETRY_FLUSH_MS", 5
            ),
        )

    @property
    def compaction_configured(self) -> bool:
        return bool(
            self.selector_url
            and self.selector_model
            and self.canonicalizer_url
            and self.canonicalizer_model
            and self.consolidator_url
            and self.consolidator_model
        )

    @property
    def raw_tail_target_tokens(self) -> int:
        return max(1_000, min(16_000, self.context_tokens // 2))

    @property
    def minimum_raw_tail_tokens(self) -> int:
        return min(12_000, self.raw_tail_target_tokens)
