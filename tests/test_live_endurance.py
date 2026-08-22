from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from endurance_harness import replay_history, script_for_generation, semantic_engine
from live_endurance import (
    CapsuleParseRecallClient,
    RecordingSelector,
    TransportRetryingStructuredClient,
    _claimed_as_current_for_fact,
    _claimed_as_current_naive,
    format_live_report,
    run_live_endurance,
    score_capsule_layer,
)
from memory_gateway.compaction import Event
from memory_gateway.db import SQLiteStore
from memory_gateway.openai_adapter import ModelProtocolError, ModelTransportError
from memory_gateway.pipeline import SelectionResult


def test_capsule_layer_separates_residue_from_resurrection() -> None:
    contents: list[str] = []
    for generation in range(1, 9):
        contents.extend(script_for_generation(generation))
    expected = replay_history(contents)
    residue_ok = (
        expected.render()
        + " Postgres was rejected. The previous lease of 30 was superseded."
    )
    probes = {probe.name: probe for probe in score_capsule_layer(expected, residue_ok)}
    assert probes["current_facts_present"].passed
    assert probes["no_resurrection"].passed
    assert "rejected:database=Postgres" in probes["superseded_residue"].detail

    resurrected = "The database is Postgres and lease_seconds is 30."
    probes = {probe.name: probe for probe in score_capsule_layer(expected, resurrected)}
    assert not probes["no_resurrection"].passed
    assert probes["no_resurrection"].fatal


def test_fact_key_scoped_scorer_ignores_padding_substring_collisions() -> None:
    contents: list[str] = []
    for generation in range(1, 51):
        contents.extend(script_for_generation(generation))
    expected = replay_history(contents)
    gen50_capsule = (
        "Project: Orchid Memory Gateway. Owner: Disti. Timezone: America/New_York. "
        "Lease Duration: 900 seconds. Database Status: SQLite selected (Postgres rejected). "
        "Compaction Generation: Current (50). Promotion Policy: Promote READY descendants "
        "with compare-and-swap against the active capsule. Job Recovery Policy: Expired "
        "RUNNING jobs restart only if recover_expired_jobs is true. Event Model: Events "
        "are append-only; corrections add a new event. Preferences: Coffee order is black "
        "coffee; favorite color is teal. Lab Mascot: Okapi. Caching: A coworker mentioned "
        "Redis caching, but no decision was made. Additional Log: Protected-tail padding "
        "after generation 30 (pads 0 through 7), after generation 35 (pads 0 through 7), "
        "and after generation 40 (pads 0 through 7) recorded."
    )
    probes = {probe.name: probe for probe in score_capsule_layer(expected, gen50_capsule)}
    assert probes["current_facts_present"].passed
    assert probes["no_resurrection"].passed
    assert _claimed_as_current_naive(gen50_capsule, "30")
    assert not _claimed_as_current_for_fact(gen50_capsule, "lease_seconds", "30")
    assert "superseded:lease_seconds=30" in probes["superseded_residue"].detail


def test_numeric_values_do_not_match_inside_longer_numbers() -> None:
    contents: list[str] = []
    for generation in range(1, 5):
        contents.extend(script_for_generation(generation))
    expected = replay_history(contents)
    early_capsule = (
        "Project: Orchid Memory Gateway. Owner: Disti. Timezone: America/New_York. "
        "Lease Duration: 30 seconds. Database Status: Undecided. "
        "Compaction Generation: Current (4)."
    )
    probes = {probe.name: probe for probe in score_capsule_layer(expected, early_capsule)}
    assert probes["no_resurrection"].passed
    assert not _claimed_as_current_for_fact(early_capsule, "compaction_generation", "3")


def test_fact_key_scoped_scorer_detects_genuine_lease_resurrection() -> None:
    contents: list[str] = []
    for generation in range(1, 9):
        contents.extend(script_for_generation(generation))
    expected = replay_history(contents)
    genuine = "Lease Duration: 30 seconds."
    assert _claimed_as_current_for_fact(genuine, "lease_seconds", "30")
    probes = {probe.name: probe for probe in score_capsule_layer(expected, genuine)}
    assert not probes["no_resurrection"].passed
    assert "superseded current lease_seconds=30" in probes["no_resurrection"].detail


def test_three_layer_harness_passes_on_scripted_pipeline(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "live-shape.db")
    engine = semantic_engine(include_low=True)
    engine.selector = RecordingSelector(engine.selector)
    report = asyncio.run(
        run_live_endurance(
            store,
            engine,
            CapsuleParseRecallClient(),
            stages=(5,),
            checkpoints=(1, 5),
            worker_id="layer-test-worker",
        )
    )
    assert report.passed, format_live_report(report)
    assert report.stages_completed == [5]
    layers = {probe.layer for checkpoint in report.checkpoints for probe in checkpoint.probes}
    assert {"selector", "capsule", "recall", "provenance"} <= layers


class DropLowSelector:
    async def select(self, *, events: list[Event]) -> SelectionResult:
        selected = [
            event.id
            for event in events
            if "[FACT" in event.content and "kind=low" not in event.content
        ]
        return SelectionResult(
            selected_event_ids=tuple(selected),
            input_hash="drop-low",
            output_hash="drop-low",
            model_identity="drop-low",
            prompt_version="v1",
            generation_settings={"temperature": 0},
        )


def test_degradation_mode_records_selector_miss_and_continues(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "degrade.db")
    engine = semantic_engine(include_low=True)
    engine.selector = RecordingSelector(DropLowSelector())
    report = asyncio.run(
        run_live_endurance(
            store,
            engine,
            CapsuleParseRecallClient(),
            stages=(1,),
            checkpoints=(1,),
            worker_id="degrade-selector-worker",
            continue_on_semantic=True,
        )
    )
    assert report.stopped is None
    assert report.generations_run == 1
    checkpoint = report.checkpoints[0]
    selector = next(probe for probe in checkpoint.probes if probe.name == "selector_retention")
    assert not selector.passed


def test_protocol_retry_recovers_out_of_chunk_then_succeeds() -> None:
    from live_endurance import ProtocolRetryingSelector
    from memory_gateway.openai_adapter import ModelProtocolError
    from memory_gateway.pipeline import SelectionResult

    class FlakySelector:
        def __init__(self) -> None:
            self.calls = 0

        async def select(self, *, events):
            self.calls += 1
            if self.calls < 3:
                raise ModelProtocolError("selector returned ID outside current chunk")
            return SelectionResult(
                selected_event_ids=tuple(event.id for event in events),
                input_hash="h",
                output_hash="h",
                model_identity="x",
                prompt_version="v1",
                generation_settings={},
            )

    inner = FlakySelector()
    sleeps: list[float] = []
    wrapper = ProtocolRetryingSelector(
        inner, max_attempts=3, backoff_seconds=0.0, sleep=lambda delay: sleeps.append(delay) or asyncio.sleep(0)
    )
    result = asyncio.run(wrapper.select(events=[]))
    assert inner.calls == 3
    assert result.selected_event_ids == ()


def test_hard_stop_still_fires_for_provenance_in_degradation_mode() -> None:
    from live_endurance import LayerProbe, hard_stop_from_probes

    error = hard_stop_from_probes(
        51,
        [
            LayerProbe(
                layer="provenance",
                name="lineage_depth",
                passed=False,
                fatal=True,
                detail="depth=50 expected=51",
            )
        ],
        continue_on_semantic=True,
    )
    assert error is not None
    assert error.layer == "provenance"
    semantic = hard_stop_from_probes(
        50,
        [
            LayerProbe(
                layer="capsule",
                name="no_resurrection",
                passed=False,
                fatal=True,
                detail="superseded current lease_seconds=30",
            )
        ],
        continue_on_semantic=True,
    )
    assert semantic is None


def test_selector_miss_is_attributed_before_stop(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "selector-miss.db")
    engine = semantic_engine(include_low=True)
    engine.selector = RecordingSelector(DropLowSelector())
    report = asyncio.run(
        run_live_endurance(
            store,
            engine,
            CapsuleParseRecallClient(),
            stages=(1,),
            checkpoints=(1,),
            worker_id="selector-miss-worker",
        )
    )
    assert not report.passed
    assert report.stopped is not None
    assert report.stopped.layer == "selector"
    checkpoint = report.checkpoints[0]
    selector = next(probe for probe in checkpoint.probes if probe.name == "selector_retention")
    recall = next(probe for probe in checkpoint.probes if probe.name == "awake_recall")
    assert not selector.passed
    assert "coffee_order" in selector.detail or "favorite_color" in selector.detail
    assert not recall.passed


class _FlakyTransportClient:
    def __init__(self, errors: list[Exception], result: dict | None = None) -> None:
        self.errors = list(errors)
        self.result = result or {"ok": True}
        self.calls = 0
        self.attempts: list[int] = []
        self.response_format = None

    async def complete_json(self, input_payload: dict) -> dict:
        self.calls += 1
        self.attempts.append(getattr(self, "_transport_attempt", None))
        if self.errors:
            raise self.errors.pop(0)
        return self.result


def test_transport_retry_recovers_from_503_then_succeeds() -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    inner = _FlakyTransportClient(
        [ModelTransportError("HTTP 503: UNAVAILABLE"), ModelTransportError("HTTP 503: UNAVAILABLE")],
        {"selected_event_ids": []},
    )
    client = TransportRetryingStructuredClient(
        inner,
        max_attempts=3,
        backoff_seconds=(30.0, 60.0, 120.0),
        sleep=fake_sleep,
    )
    result = asyncio.run(client.complete_json({"events": []}))
    assert result == {"selected_event_ids": []}
    assert inner.calls == 3
    assert inner.attempts == [1, 2, 3]
    assert sleeps == [30.0, 60.0]


def test_transport_retry_does_not_retry_protocol_or_semantic_errors() -> None:
    inner = _FlakyTransportClient([ModelProtocolError("bad json")])
    client = TransportRetryingStructuredClient(inner, sleep=lambda _delay: asyncio.sleep(0))
    with pytest.raises(ModelProtocolError):
        asyncio.run(client.complete_json({}))
    assert inner.calls == 1


def test_transport_retrying_client_forwards_response_format_mutations() -> None:
    inner = _FlakyTransportClient([], {"selected_event_ids": []})
    client = TransportRetryingStructuredClient(inner, sleep=lambda _delay: asyncio.sleep(0))
    schema = {"type": "json_schema", "json_schema": {"name": "selector_response_v1"}}
    client.response_format = schema
    assert inner.response_format == schema


def test_transport_retry_exhaustion_stays_provider_error() -> None:
    inner = _FlakyTransportClient(
        [
            ModelTransportError("HTTP 503: UNAVAILABLE"),
            ModelTransportError("HTTP 503: UNAVAILABLE"),
            ModelTransportError("HTTP 503: UNAVAILABLE"),
        ]
    )
    client = TransportRetryingStructuredClient(inner, sleep=lambda _delay: asyncio.sleep(0))
    with pytest.raises(ModelTransportError, match="HTTP 503"):
        asyncio.run(client.complete_json({}))
    assert inner.calls == 3


def test_absent_identity_probe_classifies_solar_only_as_leakage() -> None:
    from live_endurance import classify_absent_identity_probe

    leakage = classify_absent_identity_probe(
        {
            "backend_chat_model": "Solar Pro4",
            "favorite_animal": None,
            "deployment_region": None,
            "cache_provider": None,
            "preferred_editor": None,
        }
    )
    assert leakage["verdict"] == "identity_leakage"
    general = classify_absent_identity_probe(
        {
            "backend_chat_model": "Solar Pro4",
            "favorite_animal": "okapi",
            "deployment_region": None,
            "cache_provider": None,
            "preferred_editor": None,
        }
    )
    assert general["verdict"] == "general_hallucination"


def test_q10_is_lab_mascot_not_running_model() -> None:
    from live_endurance import expected_recall, RECALL_QUESTIONS
    from endurance_harness import expected_state_after

    assert RECALL_QUESTIONS[9][0] == "q10"
    assert "mascot" in RECALL_QUESTIONS[9][1].lower()
    assert "model" not in RECALL_QUESTIONS[9][1].lower()
    assert expected_recall(expected_state_after(5))["q10"] is None
    assert expected_recall(expected_state_after(20))["q10"] == "red panda"
    assert expected_recall(expected_state_after(28))["q10"] == "okapi"


@pytest.mark.skipif(
    os.environ.get("ORCHID_LIVE_ENDURANCE") != "1",
    reason="provider live endurance is opt-in",
)
def test_live_endurance_staged_run(tmp_path: Path) -> None:
    from live_endurance import SolarRecallClient, live_engine
    from memory_gateway.config import RuntimeConfig

    config = RuntimeConfig.from_env(db_path=str(tmp_path / "live_endurance.db"))
    if not (config.compaction_configured and config.backend_model):
        pytest.fail("ORCHID_LIVE_ENDURANCE=1 but pipeline/backend is not configured")
    store = SQLiteStore(config.db_path)
    engine = live_engine(config, store)
    report = asyncio.run(
        run_live_endurance(
            store,
            engine,
            SolarRecallClient(config),
            stages=(10, 25, 50),
            checkpoints=(1, 5, 10, 25, 50),
        )
    )
    print(format_live_report(report))
    assert report.passed, format_live_report(report)
    assert report.stages_completed == [10, 25, 50]
