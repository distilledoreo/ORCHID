"""Credential-free gateway replay. Fake semantic quality is not provider evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import threading
import time
from contextlib import contextmanager
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from .compaction import CompactionResult, compute_input_hash, queue_snapshot_job
from .config import RuntimeConfig
from .context import estimate_tokens
from .db import canonical_json
from .gateway import create_app
from .pipeline import LosslessCompactionEngine
from .pipeline_adapters import (
    OpenAICompatCanonicalizerEngine,
    OpenAICompatConsolidatorEngine,
    OpenAICompatSelectorEngine,
)

FACT = re.compile(r"FACT ([a-z_]+)=([a-z0-9_-]+)")
PROBE = re.compile(r"PROBE ([a-z_]+)")


def synthetic_thread(generations: int = 10) -> list[dict]:
    if generations < 10:
        raise ValueError("retention replay requires at least ten generations")
    messages = []
    for generation in range(1, generations + 1):
        facts = "FACT stable=bluejay FACT region=east" if generation == 1 else ""
        if generation == 6:
            facts = "FACT region=west FACT rejected_database=postgres"
        messages.append({"role": "user", "content": f"Generation {generation}. {facts}\n" +
                         ("transient diagnostic output, no durable changes. " * 120)})
    return messages


def fact_lines(text: str) -> str:
    state = dict(FACT.findall(text))
    return "\n".join(f"FACT {key}={value}" for key, value in state.items()) or "No durable facts."


class FakeStageClient:
    """Exercise production adapters with deterministic replies and estimated usage."""

    def __init__(self, stage: str):
        self.stage = stage
        self.model = "fake-" + stage
        self.prompt_version = "replay-v1"
        self.generation_settings = {"temperature": 0}
        self.last_telemetry = None
        self.response_format = None
        self.runs: list[dict] = []

    async def complete_json(self, payload: dict) -> dict:
        started = time.perf_counter()
        if self.stage == "selector":
            response = {"selected_event_ids": [event["id"] for event in payload["events"]]}
        elif self.stage == "canonicalizer":
            response = {"canonical_text": fact_lines(canonical_json(payload))}
        else:
            base = (payload.get("base_capsule") or {}).get("content", "")
            packet = payload.get("lossless_packet") or {}
            response = {"content": fact_lines(base + "\n" + canonical_json(packet)),
                        "evidence_event_ids": packet.get("selected_event_ids", [])}
        self.last_telemetry = {
            "stage": self.stage, "input_tokens": estimate_tokens(canonical_json(payload)),
            "output_tokens": estimate_tokens(canonical_json(response)),
            "wall_ms": (time.perf_counter() - started) * 1000,
            "status": "SUCCEEDED", "usage_kind": "estimate",
        }
        self.runs.append(dict(self.last_telemetry))
        return response


class RollingSummaryEngine:
    def __init__(self):
        self.runs: list[dict] = []

    async def compact(self, *, base_capsule, events, snapshot_end_event_id):
        started = time.perf_counter()
        text = (base_capsule.content if base_capsule else "") + "\n" + "\n".join(e.content for e in events)
        content = fact_lines(text)
        self.runs.append({"stage": "rolling", "input_tokens": estimate_tokens(text),
                          "output_tokens": estimate_tokens(content),
                          "wall_ms": (time.perf_counter() - started) * 1000,
                          "status": "SUCCEEDED", "usage_kind": "estimate"})
        ids = tuple(event.id for event in events)
        return CompactionResult(content=content, covered_event_ids=ids, evidence_event_ids=ids,
                                input_hash=compute_input_hash(base_capsule, events, snapshot_end_event_id),
                                output_hash=sha256(content.encode()).hexdigest(),
                                model_identity="fake-rolling", prompt_version="replay-v1",
                                generation_settings={"temperature": 0})


class FakeObserverClient(FakeStageClient):
    def __init__(self):
        super().__init__("observer")

    async def complete_json(self, payload):
        started = time.perf_counter()
        if payload["task"] == "reflection":
            response = {"decisions": [{"item_id": item["id"], "action": "dropped",
                                      "reason": "test-only budget eviction", "merge_group": None}
                                     for item in payload["items"]], "merges": []}
        else:
            prior = {FACT.search(item["claim"]).group(1): item for item in payload["current_items"]
                     if FACT.search(item["claim"])}
            operations = []
            for event in payload["events"]:
                for span in event["spans"]:
                    for match in FACT.finditer(span["text"]):
                        key, value = match.groups()
                        target = prior.get(key)
                        if target and target["claim"] == match.group():
                            continue
                        operations.append({"op": "supersede" if target else "add",
                                           "target_id": target["id"] if target else None, "reason": "new evidence",
                                           "item": {"claim": match.group(), "kind": "decision" if key.startswith("rejected_") else "fact",
                                                    "quotes": [{"event_id": event["event_id"], "text": match.group(),
                                                                "start": span["start"] + match.start(),
                                                                "end": span["start"] + match.end()}]}})
            response = {"operations": operations or [{"op": "noop", "target_id": None, "item": None,
                                                       "reason": "no durable changes"}]}
        run = {"stage": payload["task"], "input_tokens": estimate_tokens(canonical_json(payload)),
               "output_tokens": estimate_tokens(canonical_json(response)),
               "wall_ms": (time.perf_counter() - started) * 1000,
               "status": "SUCCEEDED", "usage_kind": "estimate"}
        self.last_telemetry = run
        self.runs.append(run)
        return response


@contextmanager
def fake_backend():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["content-length"])))
            # Root-task continuity carries original instructions; promoted state
            # supersedes its historical values, just as a model is instructed.
            text = canonical_json([message for message in payload["messages"]
                                   if not (message.get("metadata") or {}).get("orchid_root_task_event_id")])
            probes = PROBE.findall(text)
            answer = dict(FACT.findall(text)).get(probes[-1], "unknown") if probes else "acknowledged"
            response = {"choices": [{"message": {"role": "assistant", "content": answer},
                                     "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": estimate_tokens(text),
                                  "completion_tokens": estimate_tokens(answer)}}
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


async def replay_arm(name: str, messages: list[dict], path: Path, backend: str) -> dict:
    clients = []
    if name == "three_stage":
        clients = [FakeStageClient(stage) for stage in ("selector", "canonicalizer", "consolidator")]
        engine = LosslessCompactionEngine(OpenAICompatSelectorEngine(clients[0]),
                                         OpenAICompatCanonicalizerEngine(clients[1]),
                                         OpenAICompatConsolidatorEngine(clients[2]))
    elif name == "observer":
        from .observer import ObserverEngine
        clients = [FakeObserverClient()]
        engine = ObserverEngine(clients[0])
    else:
        engine = RollingSummaryEngine()
    # Construct explicitly: evaluation must never load provider settings/credentials from env.
    config = RuntimeConfig(str(path), backend, None, "fake-foreground", "", "", None,
                           "", "", None, None, None, None, 128_000, .65, .85, .25, 1,
                           initial_compaction_threshold_tokens=100_000,
                           foreground_target_tokens=110_000, foreground_hard_limit_tokens=120_000,
                           capsule_verification_enabled=False)
    app = create_app(config=config, compaction_engine=engine)
    if name == "observer":
        engine.store = app.state.store
    history: list[dict] = []
    promotions = 0
    started = time.perf_counter()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://replay") as client:
        async def send(message):
            history.append(message)
            response = await client.post("/v1/chat/completions", json={"model": "fake", "messages": history},
                                         headers={"x-memory-project": "replay", "x-memory-thread": "thread"})
            if response.status_code != 200:
                raise RuntimeError(f"replay gateway returned {response.status_code}")
            answer = response.json()["choices"][0]["message"]
            history.append(answer)
            return answer["content"]
        for message in messages:
            await send(message)
            if name != "full_context":
                queue_snapshot_job(app.state.store, "thread")
                status = await app.state.compaction_worker.run_once()
                if status != "PROMOTED":
                    raise RuntimeError(f"replay compaction {status}")
                promotions += 1
        expected = {"stable": "bluejay", "region": "west", "absent": "unknown",
                    "rejected_database": "postgres"}
        answers = {key: await send({"role": "user", "content": f"PROBE {key}"}) for key in expected}
    runs = [run for stage in clients for run in stage.runs] if clients else engine.runs
    source_tokens = sum(estimate_tokens(canonical_json(message)) for message in messages)
    factor = 1000 / source_tokens
    return {"arm": name, "source_tokens": source_tokens, "source_token_kind": "estimate",
            "calls": len(runs), "promotions": promotions, "stored_events": len(app.state.store.list_events("thread")),
            "input_tokens": sum(run["input_tokens"] for run in runs),
            "output_tokens": sum(run["output_tokens"] for run in runs),
            "compaction_wall_ms": sum(run["wall_ms"] for run in runs),
            "replay_wall_ms": (time.perf_counter() - started) * 1000,
            "per_1000_source_tokens": {key: sum(run[key] for run in runs) * factor
                                       for key in ("input_tokens", "output_tokens", "wall_ms")}
                                       | {"calls": len(runs) * factor},
            "probes": {key: {"expected": expected[key], "actual": answers[key],
                             "passed": answers[key] == expected[key]} for key in expected},
            "model_runs": runs}


def evaluate(messages: list[dict] | None = None) -> dict:
    messages = messages or synthetic_thread()
    with TemporaryDirectory() as tmp, fake_backend() as backend:
        arms = [asyncio.run(replay_arm(name, messages, Path(tmp) / f"{name}.db", backend))
                for name in ("full_context", "rolling_summary", "three_stage", "observer")]
    return {"format": "orchid-gateway-replay-v1", "fixture_hash": sha256(canonical_json(messages).encode()).hexdigest(),
            "mode": "deterministic-fakes", "quality_scope": "protocol/regression; not live-model recall evidence",
            "arms": arms, "legacy_to_observer_input_ratio": arms[2]["input_tokens"] / max(1, arms[3]["input_tokens"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, help="Sanitized JSON array of messages; ten or more generations")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    messages = json.loads(args.fixture.read_text()) if args.fixture else None
    report = evaluate(messages)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps([{k: arm[k] for k in ("arm", "calls", "input_tokens", "output_tokens", "probes")}
                      for arm in report["arms"]], indent=2))


if __name__ == "__main__":
    main()
