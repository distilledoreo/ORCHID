"""Bounded multi-generation compaction endurance harness.

This is a diagnostic, not a claim of infinite recall. It runs a synthetic
thread through CompactionWorker, then scores capsule-only state against an
authoritative replay of the same event history.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Iterable

from memory_gateway.compaction import (
    CompactionWorker,
    Event,
    compute_source_event_hash,
    queue_snapshot_job,
)
from memory_gateway.context import ContextAssembler, estimate_tokens
from memory_gateway.db import SQLiteStore
from memory_gateway.fake_engines import PerfectCompactionEngine
from memory_gateway.pipeline import (
    CanonicalizationBatchResult,
    ConsolidationResult,
    LosslessCompactionEngine,
    SelectionResult,
    build_canonicalization_result,
)

FACT_RE = re.compile(
    r"\[FACT id=(?P<id>[^\s\]]+) kind=(?P<kind>[^\s\]]+)\] (?P<value>.+)$"
)
CAPSULE_FORMAT = "orchid_endurance_capsule_v1"
CHECKPOINT_GENERATIONS = (1, 5, 10, 25, 50)
DEFAULT_GENERATIONS = 50
THREAD_ID = "endurance"
PROJECT_ID = "endurance"


class FactKind:
    CURRENT = "current"
    REJECTED = "rejected"
    CONDITIONAL = "conditional"
    DECISION = "decision"
    LOW = "low"


@dataclass
class WorldState:
    current: dict[str, str] = field(default_factory=dict)
    rejected: dict[str, str] = field(default_factory=dict)
    superseded: dict[str, list[str]] = field(default_factory=dict)
    conditionals: dict[str, str] = field(default_factory=dict)
    decisions: dict[str, str] = field(default_factory=dict)
    low: dict[str, str] = field(default_factory=dict)

    def copy(self) -> "WorldState":
        return WorldState(
            current=dict(self.current),
            rejected=dict(self.rejected),
            superseded={key: list(values) for key, values in self.superseded.items()},
            conditionals=dict(self.conditionals),
            decisions=dict(self.decisions),
            low=dict(self.low),
        )

    def apply_fact(self, fact_id: str, kind: str, value: str) -> None:
        if kind == FactKind.CURRENT:
            previous = self.current.get(fact_id)
            if previous is not None and previous != value:
                self.superseded.setdefault(fact_id, []).append(previous)
            self.current[fact_id] = value
            return
        if kind == FactKind.REJECTED:
            self.rejected[fact_id] = value
            if self.current.get(fact_id) == value:
                del self.current[fact_id]
            return
        if kind == FactKind.CONDITIONAL:
            self.conditionals[fact_id] = value
            return
        if kind == FactKind.DECISION:
            self.decisions[fact_id] = value
            return
        if kind == FactKind.LOW:
            self.low[fact_id] = value
            return
        raise ValueError(f"unknown fact kind: {kind}")

    def to_capsule_dict(self, include_low: bool = True) -> dict[str, Any]:
        payload = {
            "format": CAPSULE_FORMAT,
            "current": dict(self.current),
            "rejected": dict(self.rejected),
            "superseded": {key: list(values) for key, values in self.superseded.items()},
            "conditionals": dict(self.conditionals),
            "decisions": dict(self.decisions),
        }
        if include_low:
            payload["low_salience"] = dict(self.low)
        else:
            payload["low_salience"] = {}
        return payload

    def render(self, include_low: bool = True) -> str:
        return json.dumps(self.to_capsule_dict(include_low=include_low), ensure_ascii=False, sort_keys=True)


def parse_facts(text: str) -> list[tuple[str, str, str]]:
    facts: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        match = FACT_RE.search(line.strip())
        if match:
            facts.append((match.group("id"), match.group("kind"), match.group("value")))
    return facts


def replay_history(contents: Iterable[str], *, include_low: bool = True) -> WorldState:
    state = WorldState()
    for content in contents:
        for fact_id, kind, value in parse_facts(content):
            if kind == FactKind.LOW and not include_low:
                continue
            state.apply_fact(fact_id, kind, value)
    return state


def parse_capsule_state(content: str) -> WorldState | None:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if payload.get("format") != CAPSULE_FORMAT:
        return None
    superseded = {
        key: list(values) for key, values in (payload.get("superseded") or {}).items()
    }
    return WorldState(
        current=dict(payload.get("current") or {}),
        rejected=dict(payload.get("rejected") or {}),
        superseded=superseded,
        conditionals=dict(payload.get("conditionals") or {}),
        decisions=dict(payload.get("decisions") or {}),
        low=dict(payload.get("low_salience") or {}),
    )


def fact_line(fact_id: str, kind: str, value: str) -> str:
    return f"[FACT id={fact_id} kind={kind}] {value}"


def filler_line(generation: int, index: int) -> str:
    return (
        f"Filler chatter for compaction generation {generation}, note {index}. "
        "A coworker mentioned Redis caching. No decision was made. "
        "This turn is low-salience operational noise."
    )


def script_for_generation(generation: int) -> list[str]:
    """Return the new raw events that should enter this generation's snapshot."""

    lines: list[str] = [fact_line("compaction_generation", FactKind.CURRENT, str(generation))]
    if generation == 1:
        lines.extend(
            [
                fact_line("project_name", FactKind.CURRENT, "Orchid Memory Gateway"),
                fact_line("owner", FactKind.CURRENT, "Disti"),
                fact_line("timezone", FactKind.CURRENT, "America/New_York"),
                fact_line("lease_seconds", FactKind.CURRENT, "30"),
                fact_line("database", FactKind.CURRENT, "undecided"),
                fact_line(
                    "recover_expired_jobs",
                    FactKind.CONDITIONAL,
                    "expired RUNNING jobs restart only if recover_expired_jobs is true",
                ),
                fact_line(
                    "event_model",
                    FactKind.DECISION,
                    "events are append-only; corrections add a new event",
                ),
                fact_line("coffee_order", FactKind.LOW, "oat latte"),
                fact_line("favorite_color", FactKind.LOW, "teal"),
            ]
        )
    elif generation == 5:
        lines.append(fact_line("lease_seconds", FactKind.CURRENT, "900"))
    elif generation == 8:
        lines.extend(
            [
                fact_line("database", FactKind.REJECTED, "Postgres"),
                fact_line("database", FactKind.CURRENT, "SQLite"),
            ]
        )
    elif generation == 12:
        lines.append(
            fact_line(
                "promotion",
                FactKind.DECISION,
                "promote READY descendants with compare-and-swap against the active capsule",
            )
        )
    elif generation == 20:
        lines.append(fact_line("lab_mascot", FactKind.CURRENT, "red panda"))
    elif generation == 28:
        lines.append(fact_line("lab_mascot", FactKind.CURRENT, "okapi"))
    elif generation == 40:
        lines.append(fact_line("coffee_order", FactKind.LOW, "black coffee"))
    for index in range(4):
        lines.append(filler_line(generation, index))
    return lines


def expected_state_after(generation: int, *, include_low: bool = True) -> WorldState:
    contents: list[str] = []
    for current in range(1, generation + 1):
        contents.extend(script_for_generation(current))
    return replay_history(contents, include_low=include_low)


class TaggedFactSelector:
    def __init__(self, *, include_low: bool = True) -> None:
        self.include_low = include_low
        self.model_identity = "endurance-selector"
        self.prompt_version = "endurance-selector-v1"

    async def select(self, *, events: list[Event]) -> SelectionResult:
        selected: list[str] = []
        for event in events:
            facts = parse_facts(event.content)
            if not facts:
                continue
            if not self.include_low and all(kind == FactKind.LOW for _, kind, _ in facts):
                continue
            if not self.include_low:
                kept = [(fact_id, kind, value) for fact_id, kind, value in facts if kind != FactKind.LOW]
                if not kept:
                    continue
            selected.append(event.id)
        payload = {"selected_event_ids": selected}
        digest = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return SelectionResult(
            selected_event_ids=tuple(selected),
            input_hash=digest,
            output_hash=digest,
            model_identity=self.model_identity,
            prompt_version=self.prompt_version,
            generation_settings={"temperature": 0},
        )


class IdentityCanonicalizer:
    model_identity = "endurance-canonicalizer"
    prompt_version = "endurance-canonicalizer-v1"

    async def canonicalize(self, *, events):
        canonical_text = "\n".join(item.content for item in events)
        selected_ids = tuple(item.id for item in events)
        return build_canonicalization_result(
            batches=(
                CanonicalizationBatchResult(
                    batch_index=0,
                    covered_source_refs=selected_ids,
                    canonical_text=canonical_text,
                    input_hash=compute_source_event_hash(None, events) if events else "empty",
                    output_hash=sha256(canonical_text.encode()).hexdigest(),
                    cited_source_refs=selected_ids,
                ),
            )
            if events
            else (),
            selected_events=list(events),
            model_identity=self.model_identity,
            prompt_version=self.prompt_version,
            generation_settings={"temperature": 0},
        )


class SemanticConsolidator:
    def __init__(self, *, include_low: bool = True) -> None:
        self.include_low = include_low
        self.model_identity = "endurance-consolidator"
        self.prompt_version = "endurance-consolidator-v1"

    async def consolidate(self, *, base_capsule, events, snapshot_end_event_id, packet):
        state = WorldState()
        if base_capsule is not None:
            parsed = parse_capsule_state(base_capsule.content)
            if parsed is not None:
                state = parsed
            else:
                state = replay_history([base_capsule.content], include_low=self.include_low)
        for item in packet.authoritative_events:
            for fact_id, kind, value in parse_facts(item["content"]):
                if kind == FactKind.LOW and not self.include_low:
                    continue
                state.apply_fact(fact_id, kind, value)
        content = state.render(include_low=self.include_low)
        evidence_ids = tuple(item.id for item in events)
        return ConsolidationResult(
            content=content,
            evidence_event_ids=evidence_ids,
            model_identity=self.model_identity,
            prompt_version=self.prompt_version,
            generation_settings={"temperature": 0, "seed": 0},
        )


def semantic_engine(*, include_low: bool = True) -> LosslessCompactionEngine:
    return LosslessCompactionEngine(
        TaggedFactSelector(include_low=include_low),
        IdentityCanonicalizer(),
        SemanticConsolidator(include_low=include_low),
    )


@dataclass
class ProbeResult:
    name: str
    passed: bool
    detail: str


@dataclass
class CheckpointReport:
    generation: int
    capsule_id: str
    capsule_chars: int
    capsule_tokens: int
    compaction_ms: float
    lineage_depth: int
    events_in_history: int
    fact_events_in_tail: int
    probes: list[ProbeResult]
    expected: WorldState
    observed: WorldState | None
    retained: int = 0
    retained_possible: int = 0
    superseded_ok: bool = False
    resurrection: list[str] = field(default_factory=list)
    hallucinations: list[str] = field(default_factory=list)
    stable_drift: float = 0.0

    @property
    def passed(self) -> bool:
        return all(probe.passed for probe in self.probes)


@dataclass
class EnduranceReport:
    mode: str
    generations: int
    checkpoints: list[CheckpointReport]
    latencies_ms: list[float]
    capsule_growth: list[tuple[int, int]]

    @property
    def passed(self) -> bool:
        return all(checkpoint.passed for checkpoint in self.checkpoints)


def _lineage_depth(store: SQLiteStore, capsule_id: str) -> int:
    depth = 0
    current = capsule_id
    seen: set[str] = set()
    while current:
        if current in seen:
            raise RuntimeError("capsule lineage contains a cycle")
        seen.add(current)
        depth += 1
        with store.connect() as connection:
            row = connection.execute(
                "SELECT base_capsule_id, state FROM capsules WHERE id = ?",
                (current,),
            ).fetchone()
        if row is None:
            raise RuntimeError("capsule lineage is missing a parent")
        current = row["base_capsule_id"]
    return depth


def _append(store: SQLiteStore, content: str, *, role: str = "user") -> dict[str, Any]:
    return store.append_event(
        project_id=PROJECT_ID,
        thread_id=THREAD_ID,
        event_type="user_message",
        role=role,
        content=content,
    )


def _pad_tail(store: SQLiteStore, generation: int) -> None:
    for index in range(8):
        _append(store, f"Protected-tail padding after generation {generation}, pad {index}.")


def score_checkpoint(
    store: SQLiteStore,
    *,
    generation: int,
    compaction_ms: float,
    include_low: bool,
    require_low: bool,
    assembler: ContextAssembler,
) -> CheckpointReport:
    active = store.get_active_capsule(THREAD_ID)
    if active is None:
        raise RuntimeError("checkpoint requires an active capsule")
    events = store.list_events(THREAD_ID)
    expected = replay_history((event["content"] for event in events), include_low=include_low)
    observed = parse_capsule_state(active["content"])
    context = assembler.assemble(
        THREAD_ID,
        [{"role": "user", "content": "capsule-only recall probe"}],
    )
    tail_contents = [
        event["content"]
        for event in events
        if event["id"] in context.raw_tail_event_ids
    ]
    fact_events_in_tail = sum(1 for content in tail_contents if parse_facts(content))
    probes: list[ProbeResult] = []

    probes.append(
        ProbeResult(
            "lineage_depth",
            _lineage_depth(store, active["id"]) == generation,
            f"depth={_lineage_depth(store, active['id'])} expected={generation}",
        )
    )
    probes.append(
        ProbeResult(
            "covered_events_left_fact_tail",
            fact_events_in_tail == 0,
            f"fact_events_in_tail={fact_events_in_tail}",
        )
    )
    probes.append(
        ProbeResult(
            "raw_history_intact",
            len(events) >= generation,
            f"events={len(events)}",
        )
    )

    resurrection: list[str] = []
    hallucinations: list[str] = []
    retained = 0
    retained_possible = len(expected.current)
    superseded_ok = False
    stable_drift = 1.0

    if observed is None:
        content = active["content"]
        retained = sum(1 for value in expected.current.values() if value in content)
        for fact_id, value in expected.rejected.items():
            if value in content and expected.current.get(fact_id) != value:
                resurrection.append(f"{fact_id}={value}")
        for fact_id, values in expected.superseded.items():
            for value in values:
                if value in content and expected.current.get(fact_id) != value:
                    resurrection.append(f"{fact_id}:{value}")
        if "Redis is the cache" in content or "Postgres is the database" in content:
            hallucinations.append("chosen-store-claim")
        probes.append(
            ProbeResult(
                "structured_capsule",
                False,
                "capsule is not structured endurance JSON; scored with substring probes",
            )
        )
        probes.append(
            ProbeResult(
                "retention",
                retained == retained_possible,
                f"{retained}/{retained_possible} current values present in capsule text",
            )
        )
        probes.append(
            ProbeResult(
                "no_resurrection",
                not resurrection,
                ", ".join(resurrection) or "none",
            )
        )
    else:
        missing = [
            f"{fact_id}={value}"
            for fact_id, value in expected.current.items()
            if observed.current.get(fact_id) != value
        ]
        retained = retained_possible - len(missing)
        probes.append(
            ProbeResult(
                "retention",
                not missing,
                ", ".join(missing) or "all current facts retained",
            )
        )
        superseded_failures = []
        for fact_id, values in expected.superseded.items():
            if observed.current.get(fact_id) in values:
                superseded_failures.append(fact_id)
            for value in values:
                if value not in observed.superseded.get(fact_id, []):
                    superseded_failures.append(f"{fact_id}:{value} not recorded as superseded")
        superseded_ok = not superseded_failures
        probes.append(
            ProbeResult(
                "supersession",
                superseded_ok,
                ", ".join(superseded_failures) or "changed facts superseded",
            )
        )
        for fact_id, value in expected.rejected.items():
            if observed.current.get(fact_id) == value:
                resurrection.append(f"rejected current {fact_id}={value}")
            if observed.rejected.get(fact_id) != value:
                resurrection.append(f"rejected missing {fact_id}={value}")
        for fact_id, values in expected.superseded.items():
            if observed.current.get(fact_id) in values:
                resurrection.append(f"superseded current {fact_id}")
        probes.append(
            ProbeResult(
                "no_resurrection",
                not resurrection,
                ", ".join(resurrection) or "none",
            )
        )
        for fact_id, value in expected.conditionals.items():
            if observed.current.get(fact_id) == value:
                hallucinations.append(f"conditional promoted {fact_id}")
            if observed.conditionals.get(fact_id) != value:
                hallucinations.append(f"conditional lost {fact_id}")
        if observed.current.get("database") == "Postgres":
            hallucinations.append("rejected Postgres became current")
        if "Redis" in observed.current.values() or observed.current.get("cache") == "Redis":
            hallucinations.append("Redis decision hallucinated")
        extra_current = [
            f"{fact_id}={value}"
            for fact_id, value in observed.current.items()
            if expected.current.get(fact_id) != value and fact_id not in expected.current
        ]
        hallucinations.extend(extra_current)
        probes.append(
            ProbeResult(
                "no_hallucinated_state",
                not hallucinations,
                ", ".join(hallucinations) or "none",
            )
        )
        probes.append(
            ProbeResult(
                "conditionals_remain_conditional",
                observed.conditionals == expected.conditionals
                and all(fact_id not in observed.current for fact_id in expected.conditionals),
                json.dumps(observed.conditionals, sort_keys=True),
            )
        )
        probes.append(
            ProbeResult(
                "decisions_retained",
                observed.decisions == expected.decisions,
                json.dumps(observed.decisions, sort_keys=True),
            )
        )
        if require_low:
            probes.append(
                ProbeResult(
                    "low_salience_retained",
                    observed.low == expected.low,
                    json.dumps(observed.low, sort_keys=True),
                )
            )
        else:
            probes.append(
                ProbeResult(
                    "low_salience_dropped_by_selector",
                    observed.low == {},
                    json.dumps(observed.low, sort_keys=True),
                )
            )
        stable_keys = [
            key
            for key in expected.current
            if key not in expected.superseded and key != "compaction_generation"
        ]
        if stable_keys:
            mismatches = sum(
                1 for key in stable_keys if observed.current.get(key) != expected.current[key]
            )
            stable_drift = mismatches / len(stable_keys)
        else:
            stable_drift = 0.0
        probes.append(
            ProbeResult(
                "stable_fact_drift",
                stable_drift == 0.0,
                f"drift={stable_drift:.3f}",
            )
        )

    return CheckpointReport(
        generation=generation,
        capsule_id=active["id"],
        capsule_chars=len(active["content"]),
        capsule_tokens=estimate_tokens(active["content"]),
        compaction_ms=compaction_ms,
        lineage_depth=_lineage_depth(store, active["id"]),
        events_in_history=len(events),
        fact_events_in_tail=fact_events_in_tail,
        probes=probes,
        expected=expected,
        observed=observed,
        retained=retained,
        retained_possible=retained_possible,
        superseded_ok=superseded_ok,
        resurrection=resurrection,
        hallucinations=hallucinations,
        stable_drift=stable_drift,
    )


def run_endurance(
    store: SQLiteStore,
    engine,
    *,
    generations: int = DEFAULT_GENERATIONS,
    checkpoints: tuple[int, ...] = CHECKPOINT_GENERATIONS,
    include_low: bool = True,
    require_low: bool = True,
    mode: str = "semantic",
) -> EnduranceReport:
    store.create_project(PROJECT_ID)
    store.create_thread(THREAD_ID, PROJECT_ID)
    worker = CompactionWorker(store, engine, "endurance-worker")
    assembler = ContextAssembler(
        store,
        raw_tail_target_tokens=80,
        minimum_raw_tail_tokens=1,
    )
    reports: list[CheckpointReport] = []
    latencies: list[float] = []
    growth: list[tuple[int, int]] = []
    previous_active: str | None = None

    for generation in range(1, generations + 1):
        for content in script_for_generation(generation):
            _append(store, content)
        queue_snapshot_job(store, THREAD_ID)
        started = time.perf_counter()
        result = _run_worker(worker)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if result != "PROMOTED":
            raise RuntimeError(f"generation {generation} compaction returned {result}")
        latencies.append(elapsed_ms)
        active = store.get_active_capsule(THREAD_ID)
        if active is None:
            raise RuntimeError("compaction promoted without an active capsule")
        if previous_active is not None and active["base_capsule_id"] != previous_active:
            raise RuntimeError("descendant did not use the previous active capsule as its base")
        if previous_active is not None:
            with store.connect() as connection:
                prior = connection.execute(
                    "SELECT state FROM capsules WHERE id = ?",
                    (previous_active,),
                ).fetchone()
            if prior["state"] != "SUPERSEDED":
                raise RuntimeError("prior capsule was not marked SUPERSEDED")
        previous_active = active["id"]
        growth.append((generation, len(active["content"])))
        _pad_tail(store, generation)
        if generation in checkpoints:
            reports.append(
                score_checkpoint(
                    store,
                    generation=generation,
                    compaction_ms=elapsed_ms,
                    include_low=include_low,
                    require_low=require_low,
                    assembler=assembler,
                )
            )
    return EnduranceReport(
        mode=mode,
        generations=generations,
        checkpoints=reports,
        latencies_ms=latencies,
        capsule_growth=growth,
    )


def _run_worker(worker: CompactionWorker) -> str:
    import asyncio

    return asyncio.run(worker.run_once()) or "NONE"


def format_report(report: EnduranceReport) -> str:
    lines = [
        f"ORCHID endurance {report.mode}: {report.generations} generations",
        f"compaction latency ms: min={min(report.latencies_ms):.1f} "
        f"median={sorted(report.latencies_ms)[len(report.latencies_ms)//2]:.1f} "
        f"max={max(report.latencies_ms):.1f}",
        "capsule growth (generation, chars): "
        + ", ".join(
            f"{generation}:{chars}"
            for generation, chars in report.capsule_growth
            if generation in {checkpoint.generation for checkpoint in report.checkpoints}
        ),
    ]
    for checkpoint in report.checkpoints:
        status = "PASS" if checkpoint.passed else "FAIL"
        lines.append(
            f"checkpoint gen {checkpoint.generation} {status} "
            f"chars={checkpoint.capsule_chars} tokens~{checkpoint.capsule_tokens} "
            f"compact_ms={checkpoint.compaction_ms:.1f} lineage={checkpoint.lineage_depth}"
        )
        for probe in checkpoint.probes:
            mark = "ok" if probe.passed else "FAIL"
            lines.append(f"  {mark} {probe.name}: {probe.detail}")
    return "\n".join(lines)


def naive_engine() -> PerfectCompactionEngine:
    return PerfectCompactionEngine(model_identity="endurance-naive")
