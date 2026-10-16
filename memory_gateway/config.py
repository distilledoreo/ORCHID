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


def _positive_float_env(name: str, default: float) -> float:
    value = _env(name)
    if value is None:
        return default
    parsed = float(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
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
    backend_context_limit_tokens: int = 128_000
    foreground_target_tokens: int = 32_000
    initial_compaction_threshold_tokens: int = 8_000
    foreground_hard_limit_tokens: int = 48_000
    recent_tail_target_tokens: int = 8_000
    capsule_max_tokens: int = 2_048
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
    # Frozen semantic evidence currently requires the canonicalizer to remain
    # enabled. Experimental bypasses are outside the sealed core-goal path.
    canonicalizer_enabled: bool = True
    compaction_context_tokens: int = 32_768
    compaction_reserved_output_tokens: int = 8_192
    compaction_fixed_overhead_tokens: int = 4_096
    compaction_safety_margin: float = 0.7
    compaction_request_amplification: float = 3.0
    memory_format: str = "v1"
    capsule_verification_enabled: bool = True
    capsule_verification_input_tokens: int = 4_096
    capsule_verification_output_tokens: int = 512
    publication_min_savings_ratio: float = 1.25
    publication_economics_enabled: bool = False
    publication_horizon_turns: int = 8
    compaction_forecast_alpha: float = 0.2
    compaction_forecast_lookahead_turns: float = 3.0
    coding_agent_mode: bool = False
    coding_keep_turns: int = 4
    coding_offload_tokens: int = 8_000
    embeddings_url: str | None = None
    embeddings_model: str | None = None
    embeddings_api_key: str | None = None
    embeddings_timeout_seconds: float = 15.0
    memory_engine: str = "auto"
    observer_url: str | None = None
    observer_model: str | None = None
    observer_api_key: str | None = None
    observer_input_tokens: int = 32_768
    observer_output_tokens: int = 2_048
    observer_timeout_seconds: float = 120.0
    background_idle_only: bool = False
    prompt_layout: str = "stable"
    foreground_reserved_output_tokens: int = 2_048
    consolidator_timeout_seconds: float | None = None

    def effective_consolidator_timeout_seconds(self) -> float:
        if self.consolidator_timeout_seconds is None:
            return self.model_timeout_seconds
        return self.consolidator_timeout_seconds

    @classmethod
    def from_env(
        cls,
        *,
        db_path: str | None = None,
        backend_url: str | None = None,
    ) -> RuntimeConfig:
        memory_engine = _env("ORCHID_MEMORY_ENGINE", "auto")
        if memory_engine not in {"auto", "observer", "legacy"}:
            raise ValueError("ORCHID_MEMORY_ENGINE must be auto, observer, or legacy")
        prompt_layout = _env("ORCHID_PROMPT_LAYOUT", "stable")
        if prompt_layout not in {"stable", "legacy"}:
            raise ValueError("ORCHID_PROMPT_LAYOUT must be stable or legacy")
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
        legacy_context = _env("ORCHID_CONTEXT_TOKENS")
        backend_context = _env("ORCHID_BACKEND_CONTEXT_LIMIT_TOKENS")
        if legacy_context and backend_context and int(legacy_context) != int(backend_context):
            raise ValueError(
                "ORCHID_CONTEXT_TOKENS and ORCHID_BACKEND_CONTEXT_LIMIT_TOKENS conflict"
            )
        backend_context_limit_tokens = _int_env(
            "ORCHID_BACKEND_CONTEXT_LIMIT_TOKENS",
            int(legacy_context) if legacy_context else 128_000,
        )
        foreground_target_tokens = _int_env("ORCHID_FOREGROUND_TARGET_TOKENS", 32_000)
        initial_compaction_threshold_tokens = _int_env(
            "ORCHID_INITIAL_COMPACTION_THRESHOLD_TOKENS", 8_000
        )
        foreground_hard_limit_tokens = _int_env("ORCHID_FOREGROUND_HARD_LIMIT_TOKENS", 48_000)
        recent_tail_target_tokens = _int_env("ORCHID_RECENT_TAIL_TARGET_TOKENS", 8_000)
        capsule_max_tokens = _int_env("ORCHID_CAPSULE_MAX_TOKENS", 2_048)
        capsule_verification_enabled = _bool_env("ORCHID_CAPSULE_VERIFICATION_ENABLED", True)
        capsule_verification_input_tokens = _int_env(
            "ORCHID_CAPSULE_VERIFICATION_INPUT_TOKENS", 4_096
        )
        capsule_verification_output_tokens = _int_env(
            "ORCHID_CAPSULE_VERIFICATION_OUTPUT_TOKENS", 512
        )
        if foreground_target_tokens > foreground_hard_limit_tokens:
            raise ValueError(
                "ORCHID_FOREGROUND_TARGET_TOKENS must not exceed ORCHID_FOREGROUND_HARD_LIMIT_TOKENS"
            )
        if not 0 < initial_compaction_threshold_tokens <= foreground_target_tokens:
            raise ValueError(
                "ORCHID_INITIAL_COMPACTION_THRESHOLD_TOKENS must be positive and "
                "must not exceed ORCHID_FOREGROUND_TARGET_TOKENS"
            )
        if foreground_hard_limit_tokens > backend_context_limit_tokens:
            raise ValueError(
                "ORCHID_FOREGROUND_HARD_LIMIT_TOKENS must not exceed backend context limit"
            )
        memory_format = (_env("ORCHID_MEMORY_FORMAT", "v1") or "v1").lower()
        if memory_format not in {"v1", "v2_shadow", "v2"}:
            raise ValueError("ORCHID_MEMORY_FORMAT must be v1, v2_shadow, or v2")
        publication_min_savings_ratio = float(
            _env("ORCHID_PUBLICATION_MIN_SAVINGS_RATIO", "1.25") or "1.25"
        )
        if publication_min_savings_ratio <= 0:
            raise ValueError("ORCHID_PUBLICATION_MIN_SAVINGS_RATIO must be positive")
        publication_economics_enabled = _bool_env(
            "ORCHID_PUBLICATION_ECONOMICS_ENABLED", False
        )
        publication_horizon_turns = _int_env("ORCHID_PUBLICATION_HORIZON_TURNS", 8)
        compaction_forecast_alpha = float(
            _env("ORCHID_COMPACTION_FORECAST_ALPHA", "0.2") or "0.2"
        )
        if not 0 < compaction_forecast_alpha <= 1:
            raise ValueError("ORCHID_COMPACTION_FORECAST_ALPHA must be between 0 and 1")
        compaction_forecast_lookahead_turns = float(
            _env("ORCHID_COMPACTION_FORECAST_LOOKAHEAD_TURNS", "3") or "3"
        )
        if compaction_forecast_lookahead_turns <= 0:
            raise ValueError("ORCHID_COMPACTION_FORECAST_LOOKAHEAD_TURNS must be positive")
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
            context_tokens=backend_context_limit_tokens,
            backend_context_limit_tokens=backend_context_limit_tokens,
            foreground_target_tokens=foreground_target_tokens,
            initial_compaction_threshold_tokens=initial_compaction_threshold_tokens,
            foreground_hard_limit_tokens=foreground_hard_limit_tokens,
            recent_tail_target_tokens=recent_tail_target_tokens,
            capsule_max_tokens=capsule_max_tokens,
            background_fraction=_float_env("ORCHID_BACKGROUND_FRACTION", 0.65),
            urgent_fraction=_float_env("ORCHID_URGENT_FRACTION", 0.85),
            worker_poll_seconds=float(_env("ORCHID_WORKER_POLL_SECONDS", "0.25")),
            model_timeout_seconds=float(_env("ORCHID_MODEL_TIMEOUT_SECONDS", "120")),
            consolidator_timeout_seconds=(
                _positive_float_env(
                    "ORCHID_CONSOLIDATOR_TIMEOUT_SECONDS",
                    float(_env("ORCHID_MODEL_TIMEOUT_SECONDS", "120") or "120"),
                )
                if _env("ORCHID_CONSOLIDATOR_TIMEOUT_SECONDS")
                else None
            ),
            selector_context_tokens=_int_env("ORCHID_SELECTOR_CONTEXT_TOKENS", 32_768),
            canonicalizer_input_tokens=_int_env(
                "ORCHID_CANONICALIZER_INPUT_TOKENS",
                8_192,
            ),
            lease_seconds=lease_seconds,
            lease_renewal_seconds=lease_renewal_seconds,
            coding_agent_mode=_bool_env("ORCHID_CODING_AGENT_MODE", False),
            coding_keep_turns=_int_env("ORCHID_CODING_KEEP_TURNS", 4),
            coding_offload_tokens=_int_env("ORCHID_CODING_OFFLOAD_TOKENS", 8_000),
            embeddings_url=_env("ORCHID_EMBEDDINGS_URL"), embeddings_model=_env("ORCHID_EMBEDDINGS_MODEL"),
            embeddings_api_key=_env("ORCHID_EMBEDDINGS_API_KEY"),
            embeddings_timeout_seconds=_positive_float_env("ORCHID_EMBEDDINGS_TIMEOUT_SECONDS", 15.0),
            memory_engine=memory_engine,
            observer_url=_env("ORCHID_OBSERVER_URL"), observer_model=_env("ORCHID_OBSERVER_MODEL"),
            observer_api_key=_env("ORCHID_OBSERVER_API_KEY"),
            observer_input_tokens=_int_env("ORCHID_OBSERVER_INPUT_TOKENS", 32_768),
            observer_output_tokens=_int_env("ORCHID_OBSERVER_OUTPUT_TOKENS", 2_048),
            observer_timeout_seconds=_positive_float_env("ORCHID_OBSERVER_TIMEOUT_SECONDS", 120.0),
            background_idle_only=_bool_env("ORCHID_BACKGROUND_IDLE_ONLY", False),
            prompt_layout=prompt_layout,
            foreground_reserved_output_tokens=_int_env("ORCHID_FOREGROUND_RESERVED_OUTPUT_TOKENS", 2_048),
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
            canonicalizer_enabled=_bool_env("ORCHID_CANONICALIZER_ENABLED", True),
            compaction_context_tokens=_int_env(
                "ORCHID_COMPACTION_CONTEXT_TOKENS", 32_768
            ),
            compaction_reserved_output_tokens=_int_env(
                "ORCHID_COMPACTION_RESERVED_OUTPUT_TOKENS", 8_192
            ),
            compaction_fixed_overhead_tokens=_int_env(
                "ORCHID_COMPACTION_FIXED_OVERHEAD_TOKENS", 4_096
            ),
            compaction_safety_margin=_float_env(
                "ORCHID_COMPACTION_SAFETY_MARGIN", 0.7
            ),
            compaction_request_amplification=_positive_float_env(
                "ORCHID_COMPACTION_REQUEST_AMPLIFICATION", 3.0
            ),
            memory_format=memory_format,
            capsule_verification_enabled=capsule_verification_enabled,
            capsule_verification_input_tokens=capsule_verification_input_tokens,
            capsule_verification_output_tokens=capsule_verification_output_tokens,
            publication_min_savings_ratio=publication_min_savings_ratio,
            publication_economics_enabled=publication_economics_enabled,
            publication_horizon_turns=publication_horizon_turns,
            compaction_forecast_alpha=compaction_forecast_alpha,
            compaction_forecast_lookahead_turns=compaction_forecast_lookahead_turns,
        )

    @property
    def compaction_configured(self) -> bool:
        return bool(
            self.selector_url
            and self.selector_model
            and self.consolidator_url
            and self.consolidator_model
            and (
                not self.canonicalizer_enabled
                or (self.canonicalizer_url and self.canonicalizer_model)
            )
        )

    @property
    def raw_tail_target_tokens(self) -> int:
        return self.recent_tail_target_tokens

    @property
    def minimum_raw_tail_tokens(self) -> int:
        return min(12_000, self.recent_tail_target_tokens)

    @property
    def compaction_source_window_tokens(self) -> int:
        """Safe source-item budget for one semantic compaction generation.

        Keep provider context, output headroom, fixed prompt overhead, and
        estimator uncertainty separate from the source window so a large
        backlog is retired over multiple jobs.
        """

        available = (
            self.compaction_context_tokens
            - self.compaction_reserved_output_tokens
            - self.compaction_fixed_overhead_tokens
        )
        return max(
            1_000,
            int(
                max(1, available)
                * self.compaction_safety_margin
                / self.compaction_request_amplification
            ),
        )
