"""EWMA compaction ingestion forecasting for predictive scheduling."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

DEFAULT_SERVICE_TIME_SECONDS = 240.0
MIN_SERVICE_SAMPLES = 3
SERVICE_SAMPLE_WINDOW = 10
BURST_MARGIN = 0.20
PROJECTED_URGENT_FRACTION = 0.80


@dataclass(frozen=True)
class CompactionForecast:
    """Tracks per-thread uncompacted growth with an EWMA."""

    rate_ewma: float = 0.0
    last_uncompacted_tokens: int = 0

    def observe(self, uncompacted_tokens: int, *, alpha: float = 0.2) -> CompactionForecast:
        delta = max(0, uncompacted_tokens - self.last_uncompacted_tokens)
        if self.rate_ewma <= 0:
            next_rate = float(delta)
        else:
            next_rate = (alpha * delta) + ((1.0 - alpha) * self.rate_ewma)
        return CompactionForecast(
            rate_ewma=max(0.0, next_rate),
            last_uncompacted_tokens=max(0, uncompacted_tokens),
        )

    def forecast_tokens(self, *, turns_ahead: float = 1.0) -> float:
        if turns_ahead <= 0:
            return float(self.last_uncompacted_tokens)
        return float(self.last_uncompacted_tokens) + (self.rate_ewma * turns_ahead)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _parse_timestamp(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def job_duration_seconds(job: dict[str, Any]) -> float | None:
    started = _parse_timestamp(job.get("started_at"))
    finished = _parse_timestamp(job.get("finished_at"))
    if started is None or finished is None:
        return None
    duration = (finished - started).total_seconds()
    if duration <= 0:
        return None
    return duration


def service_time_seconds(
    durations: list[float],
    *,
    default: float = DEFAULT_SERVICE_TIME_SECONDS,
    min_samples: int = MIN_SERVICE_SAMPLES,
) -> float:
    """p95 service time, or the conservative default until enough samples exist."""

    valid = [value for value in durations if value > 0]
    if len(valid) < min_samples:
        return default
    return _percentile(valid, 0.95)


def median_turn_interval_seconds(events: list[dict[str, Any]]) -> float | None:
    timestamps: list[datetime] = []
    for event in events:
        if event.get("role") != "user":
            continue
        parsed = _parse_timestamp(event.get("created_at"))
        if parsed is not None:
            timestamps.append(parsed)
    if len(timestamps) < 3:
        return None
    intervals = [
        (timestamps[index] - timestamps[index - 1]).total_seconds()
        for index in range(1, len(timestamps))
        if timestamps[index] > timestamps[index - 1]
    ]
    if len(intervals) < 2:
        return None
    return _percentile(intervals, 0.50)


def projected_tokens_at_completion(
    forecast: CompactionForecast,
    *,
    service_time: float,
    median_turn_interval: float | None,
    lookahead_turns: float = 3.0,
    burst_margin: float = BURST_MARGIN,
) -> float:
    """Project uncompacted tokens when the in-flight job would finish."""

    turns_ahead = max(0.0, lookahead_turns)
    if median_turn_interval is not None and median_turn_interval > 0 and service_time > 0:
        turns_ahead = max(turns_ahead, service_time / median_turn_interval)
    arriving = max(0.0, forecast.rate_ewma) * turns_ahead
    return float(forecast.last_uncompacted_tokens) + arriving * (1.0 + max(0.0, burst_margin))


def should_schedule_background(
    *,
    current_tokens: int,
    forecast: CompactionForecast,
    background_threshold_tokens: int,
    lookahead_turns: float = 3.0,
    projected_tokens: float | None = None,
) -> bool:
    if current_tokens >= background_threshold_tokens:
        return True
    if projected_tokens is not None:
        return projected_tokens >= background_threshold_tokens
    if forecast.rate_ewma <= 0:
        return False
    projected = forecast.forecast_tokens(turns_ahead=lookahead_turns)
    return projected >= background_threshold_tokens
