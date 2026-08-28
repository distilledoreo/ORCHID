from __future__ import annotations

from memory_gateway.compaction_forecast import (
    CompactionForecast,
    projected_tokens_at_completion,
    service_time_seconds,
    should_schedule_background,
)


def test_ewma_observes_growth() -> None:
    forecast = CompactionForecast()
    forecast = forecast.observe(100, alpha=0.2)
    forecast = forecast.observe(150, alpha=0.2)
    assert forecast.rate_ewma > 0
    assert forecast.last_uncompacted_tokens == 150


def test_predictive_background_schedules_before_threshold() -> None:
    forecast = CompactionForecast(rate_ewma=8_000, last_uncompacted_tokens=2_000)
    assert should_schedule_background(
        current_tokens=2_000,
        forecast=forecast,
        background_threshold_tokens=22_000,
        lookahead_turns=3.0,
    )


def test_forecast_tokens_returns_current_when_turns_ahead_non_positive() -> None:
    forecast = CompactionForecast(rate_ewma=1_000, last_uncompacted_tokens=5_000)
    assert forecast.forecast_tokens(turns_ahead=0.0) == 5_000
    assert forecast.forecast_tokens(turns_ahead=-1.0) == 5_000


def test_should_schedule_background_when_current_tokens_at_threshold() -> None:
    forecast = CompactionForecast(rate_ewma=0.0, last_uncompacted_tokens=1_000)
    assert should_schedule_background(
        current_tokens=10_000,
        forecast=forecast,
        background_threshold_tokens=10_000,
    )


def test_should_not_schedule_background_without_growth_signal() -> None:
    forecast = CompactionForecast(rate_ewma=0.0, last_uncompacted_tokens=1_000)
    assert not should_schedule_background(
        current_tokens=1_000,
        forecast=forecast,
        background_threshold_tokens=10_000,
        lookahead_turns=3.0,
    )


def test_service_time_defaults_until_three_samples() -> None:
    assert service_time_seconds([12.0, 18.0]) == 240.0
    assert service_time_seconds([10.0, 20.0, 30.0, 40.0]) >= 30.0


def test_projected_tokens_apply_burst_to_arrivals_only() -> None:
    forecast = CompactionForecast(rate_ewma=1_000, last_uncompacted_tokens=10_000)
    projected = projected_tokens_at_completion(
        forecast,
        service_time=240.0,
        median_turn_interval=None,
        lookahead_turns=3.0,
        burst_margin=0.20,
    )
    assert projected == 10_000 + 1_000 * 3.0 * 1.20


def test_projected_tokens_use_service_time_when_turns_are_slow() -> None:
    forecast = CompactionForecast(rate_ewma=500, last_uncompacted_tokens=4_000)
    projected = projected_tokens_at_completion(
        forecast,
        service_time=240.0,
        median_turn_interval=30.0,
        lookahead_turns=3.0,
        burst_margin=0.20,
    )
    assert projected == 4_000 + 500 * 8.0 * 1.20


def test_explicit_projection_schedules_background() -> None:
    forecast = CompactionForecast(rate_ewma=0.0, last_uncompacted_tokens=1_000)
    assert should_schedule_background(
        current_tokens=1_000,
        forecast=forecast,
        background_threshold_tokens=10_000,
        projected_tokens=10_000,
    )


def test_job_duration_and_median_turn_interval() -> None:
    from memory_gateway.compaction_forecast import (
        job_duration_seconds,
        median_turn_interval_seconds,
    )

    assert job_duration_seconds({}) is None
    assert (
        job_duration_seconds(
            {
                "started_at": "2026-08-28T00:00:00+00:00",
                "finished_at": "2026-08-28T00:02:00+00:00",
            }
        )
        == 120.0
    )
    assert median_turn_interval_seconds([]) is None
    events = [
        {"role": "user", "created_at": "2026-08-28T00:00:00+00:00"},
        {"role": "assistant", "created_at": "2026-08-28T00:00:10+00:00"},
        {"role": "user", "created_at": "2026-08-28T00:00:30+00:00"},
        {"role": "user", "created_at": "2026-08-28T00:01:00+00:00"},
    ]
    assert median_turn_interval_seconds(events) == 30.0
