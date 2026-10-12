from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .compaction import queue_snapshot_job
from .compaction_forecast import (
    BURST_MARGIN,
    PROJECTED_URGENT_FRACTION,
    CompactionForecast,
    job_duration_seconds,
    median_turn_interval_seconds,
    projected_tokens_at_completion,
    service_time_seconds,
    should_schedule_background,
)
from .context import estimate_tokens
from .db import SQLiteStore
from .publication_economics import (
    DEFAULT_HORIZON_TURNS,
    PublicationEconomics,
    cached_prefix_tokens_from_report,
    estimate_publication_economics,
)


@dataclass(frozen=True)
class ThresholdPolicy:
    usable_context_tokens: int
    background_fraction: float = 0.65
    urgent_fraction: float = 0.85
    background_threshold_tokens: int | None = None
    bootstrap_threshold_tokens: int | None = None
    urgent_threshold_tokens: int | None = None
    predictive_lookahead_turns: float = 3.0
    projected_urgent_fraction: float = PROJECTED_URGENT_FRACTION

    def _urgent_threshold(self) -> int:
        if self.urgent_threshold_tokens is not None:
            return self.urgent_threshold_tokens
        return int(self.usable_context_tokens * self.urgent_fraction)

    def _background_threshold(self) -> int:
        if self.background_threshold_tokens is not None:
            return self.background_threshold_tokens
        return int(self.usable_context_tokens * self.background_fraction)

    def _projected_urgent_threshold(self) -> int:
        return int(self._urgent_threshold() * self.projected_urgent_fraction)

    def priority_for(
        self,
        estimated_tokens: int,
        *,
        forecast_rate: float = 0.0,
        bootstrap: bool = False,
        projected_tokens: float | None = None,
    ) -> int | None:
        urgent_at = self._urgent_threshold()
        projected_urgent_at = self._projected_urgent_threshold()
        background_at = (
            self.bootstrap_threshold_tokens
            if bootstrap and self.bootstrap_threshold_tokens is not None
            else self._background_threshold()
        )
        if estimated_tokens >= urgent_at:
            return 100
        if projected_tokens is not None and projected_tokens >= projected_urgent_at:
            return 100
        if estimated_tokens >= background_at:
            return 50
        if projected_tokens is not None and projected_tokens >= background_at:
            return 50
        if forecast_rate > 0:
            forecast = CompactionForecast(
                rate_ewma=forecast_rate,
                last_uncompacted_tokens=estimated_tokens,
            )
            if should_schedule_background(
                current_tokens=estimated_tokens,
                forecast=forecast,
                background_threshold_tokens=background_at,
                lookahead_turns=self.predictive_lookahead_turns,
                projected_tokens=projected_tokens,
            ):
                return 50
        return None


class CompactionScheduler:
    """Creates immutable-watermark jobs without running model inference."""

    def __init__(
        self,
        store: SQLiteStore,
        policy: ThresholdPolicy,
        *,
        source_window_tokens: int | None = None,
        forecast_alpha: float = 0.2,
        publication_min_savings_ratio: float = 1.25,
        publication_economics_enabled: bool = True,
        publication_horizon_turns: int = DEFAULT_HORIZON_TURNS,
    ):
        self.store = store
        self.policy = policy
        self.source_window_tokens = source_window_tokens
        # Set by the gateway when the engine reads raw events (the observer): its snapshots are bounded
        # by tokens alone, not by the legacy selector's chunk count.
        self.raw_events = False
        self.forecast_alpha = forecast_alpha
        self.publication_min_savings_ratio = publication_min_savings_ratio
        self.publication_economics_enabled = publication_economics_enabled
        self.publication_horizon_turns = publication_horizon_turns

    def uncompacted_tokens(self, thread_id: str) -> int:
        active = self.store.get_active_capsule(thread_id)
        start_sequence = None
        if active and active["covered_end_event_id"]:
            covered = self.store.get_event(active["covered_end_event_id"])
            if covered is None:
                raise ValueError("active capsule covered_end_event_id does not exist")
            if covered["thread_id"] != thread_id:
                raise ValueError("active capsule coverage belongs to another thread")
            start_sequence = covered["sequence"] + 1

        events = self.store.list_events(thread_id, start_sequence=start_sequence)
        return sum(
            event["token_count"]
            if event["token_count"] is not None
            else estimate_tokens(event["content"])
            for event in events
        )

    def _record_forecast(self, thread_id: str, uncompacted_tokens: int) -> float:
        rate_ewma, _ = self.store.update_thread_compaction_forecast(
            thread_id,
            uncompacted_tokens,
            alpha=self.forecast_alpha,
        )
        return rate_ewma

    def _projected_tokens(self, thread_id: str, uncompacted: int, forecast_rate: float) -> float:
        forecast = CompactionForecast(
            rate_ewma=forecast_rate,
            last_uncompacted_tokens=uncompacted,
        )
        jobs = self.store.list_jobs(thread_id=thread_id, limit=10)
        durations = [
            duration
            for job in jobs
            if (duration := job_duration_seconds(job)) is not None
        ]
        return projected_tokens_at_completion(
            forecast,
            service_time=service_time_seconds(durations),
            median_turn_interval=median_turn_interval_seconds(
                self.store.list_events(thread_id)
            ),
            lookahead_turns=self.policy.predictive_lookahead_turns,
            burst_margin=BURST_MARGIN,
        )

    def try_promote_ready_candidate(self, thread_id: str) -> bool:
        if not self.publication_economics_enabled:
            return False
        candidate = self.store.get_latest_ready_capsule(thread_id)
        if candidate is None:
            return False
        economics = self._live_economics(thread_id, candidate.get("model_metadata_json"))
        if economics is None or not economics.should_publish:
            return False
        promoted = self.store.promote_capsule_cas(thread_id, candidate["id"])
        if promoted:
            self.store.stale_ready_capsules(thread_id)
        return promoted

    def _live_economics(self, thread_id: str, raw: Any) -> PublicationEconomics | None:
        if not raw:
            return None
        try:
            metadata = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        payload = metadata.get("publication_economics")
        if not isinstance(payload, dict):
            return None
        source_tokens = payload.get("compacted_source_tokens")
        capsule_tokens = payload.get("promoted_capsule_tokens")
        if not isinstance(source_tokens, (int, float)) or not isinstance(
            capsule_tokens, (int, float)
        ):
            return PublicationEconomics(
                future_savings_tokens=int(payload.get("future_savings_tokens") or 0),
                reset_cost_tokens=int(payload.get("reset_cost_tokens") or 0),
                min_savings_ratio=float(
                    payload.get("min_savings_ratio") or self.publication_min_savings_ratio
                ),
                reason=(
                    "publish:stored"
                    if int(payload.get("future_savings_tokens") or 0)
                    >= int(
                        int(payload.get("reset_cost_tokens") or 0)
                        * float(
                            payload.get("min_savings_ratio")
                            or self.publication_min_savings_ratio
                        )
                    )
                    else "defer:stored"
                ),
            )
        cache = self.store.get_thread_prompt_cache(thread_id)
        current_prompt = None
        if cache and isinstance(cache.get("current_prompt_tokens"), (int, float)):
            current_prompt = int(cache["current_prompt_tokens"])
        uncompacted = self.uncompacted_tokens(thread_id)
        return estimate_publication_economics(
            compacted_source_tokens=int(source_tokens),
            promoted_capsule_tokens=int(capsule_tokens),
            min_savings_ratio=float(
                payload.get("min_savings_ratio") or self.publication_min_savings_ratio
            ),
            current_prompt_tokens=current_prompt,
            recently_cached_prefix_tokens=cached_prefix_tokens_from_report(cache),
            horizon_turns=int(payload.get("horizon_turns") or self.publication_horizon_turns),
            urgent=uncompacted >= self.policy._urgent_threshold(),
        )

    def maybe_enqueue(self, thread_id: str) -> str | None:
        self.try_promote_ready_candidate(thread_id)
        bootstrap = self.store.get_active_capsule(thread_id) is None
        uncompacted = self.uncompacted_tokens(thread_id)
        forecast_rate = self._record_forecast(thread_id, uncompacted)
        projected = self._projected_tokens(thread_id, uncompacted, forecast_rate)
        priority = self.policy.priority_for(
            uncompacted,
            forecast_rate=forecast_rate,
            bootstrap=bootstrap,
            projected_tokens=projected,
        )
        if priority is None:
            self.store.clear_compaction_dirty_if_idle(thread_id)
            return None
        return queue_snapshot_job(
            self.store,
            thread_id,
            priority=priority,
            source_window_tokens=self.source_window_tokens,
            raw_events=self.raw_events,
        )

    def enqueue_urgent(self, thread_id: str) -> str | None:
        uncompacted = self.uncompacted_tokens(thread_id)
        self._record_forecast(thread_id, uncompacted)
        return queue_snapshot_job(
            self.store,
            thread_id,
            priority=100,
            source_window_tokens=self.source_window_tokens,
            raw_events=self.raw_events,
        )

    def reconcile_after_job(self, thread_id: str) -> str | None:
        return self.maybe_enqueue(thread_id)
