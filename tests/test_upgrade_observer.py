import asyncio
import json

import pytest

from memory_gateway.activity import ActivityGate, ForegroundActivityMiddleware
from memory_gateway.compaction import CompactionWorker, queue_snapshot_job
from memory_gateway.db import SQLiteStore
from memory_gateway.evaluation import FakeObserverClient
from memory_gateway.ingestion import append_novel_messages
from memory_gateway.observer import ObserverEngine, Operation, apply_operations, render_items, source_spans


def setup(tmp_path, budget=2048, client=None):
    store = SQLiteStore(tmp_path / "test.db")
    store.ensure_project_and_thread("p", "t")
    client = client or FakeObserverClient()
    engine = ObserverEngine(client, store=store, memory_budget_tokens=budget)
    return store, client, CompactionWorker(store, engine, "test")


def ingest(store, content, request="r"):
    return append_novel_messages(store, project_id="p", thread_id="t", messages=[{"role": "user", "content": content}],
                                 request_id=request, source="response").appended_events[0]


def compact(store, worker):
    queue_snapshot_job(store, "t")
    return asyncio.run(worker.run_once())


def test_quote_ranges_are_exact_and_valid_operations_survive_rejection():
    def operation(text, end):
        return Operation.model_validate({"op": "add", "target_id": None, "reason": "evidence",
                                         "item": {"claim": "region east", "kind": "fact",
                                                  "quotes": [{"event_id": "e", "start": 0, "end": end, "text": text}]}})
    source = "region east"
    items, rejected = apply_operations([], [operation(source + " invented", 20), operation(source, len(source))],
                                       {"e": (source, 4)})
    assert len(items) == len(rejected) == 1
    assert items[0]["valid_from"] == 4
    assert items[0]["source_event_ids"] == ["e"]
    _, rejected = apply_operations([], [operation("REGION EAST", len(source))], {"e": (source, 4)})
    assert rejected


def test_replacements_append_lines_and_preserve_superseded_history(tmp_path):
    store, client, worker = setup(tmp_path)
    first = ingest(store, "FACT stable=bluejay FACT region=east")
    assert compact(store, worker) == "PROMOTED"
    old = store.get_active_capsule("t")
    ingest(store, "FACT region=west", "new")
    assert compact(store, worker) == "PROMOTED"
    active = store.get_active_capsule("t")
    assert active["content"].startswith(old["content"] + "\n")
    state = json.loads(active["model_metadata_json"])["observer_state"]
    assert [item["status"] for item in state["items"]] == ["current", "superseded", "current"]
    assert state["items"][1]["superseded_by"] == state["items"][2]["id"]
    assert state["items"][0]["source_event_ids"] == [first["id"]]
    assert len(client.runs) == 2


def test_reflection_accounts_for_all_items_and_preserves_cold_history(tmp_path):
    store, client, worker = setup(tmp_path, budget=35)
    ingest(store, "FACT one=alpha FACT two=beta FACT three=gamma")
    assert compact(store, worker) == "PROMOTED"
    state = json.loads(store.get_active_capsule("t")["model_metadata_json"])["observer_state"]
    assert len(state["items"]) == len(state["reflection"]) == 3
    assert all(not item["hot"] for item in state["items"])
    assert [run["stage"] for run in client.runs] == ["observe", "reflection"]


def test_incomplete_reflection_and_protocol_failure_leave_active_unchanged(tmp_path):
    class Broken(FakeObserverClient):
        async def complete_json(self, payload):
            if payload["task"] == "reflection":
                return {"decisions": [], "merges": []}
            return await super().complete_json(payload)
    store, _, worker = setup(tmp_path, budget=35, client=Broken())
    ingest(store, "FACT one=alpha FACT two=beta FACT three=gamma")
    before = store.list_events("t")
    assert compact(store, worker) == "FAILED"
    assert store.get_active_capsule("t") is None
    assert store.list_events("t") == before


def test_literal_prefilter_preserves_unique_text_and_original_ranges():
    text = "FACT early=one\n" + "the exact same diagnostic sentence. " * 100 + "\nFACT late=two"
    spans = source_spans(text)
    assert sum(len(span["text"]) for span in spans) < len(text) / 10
    assert all(text[span["start"]:span["end"]] == span["text"] for span in spans)
    assert "FACT early=one" in spans[0]["text"]
    assert "FACT late=two" in spans[-1]["text"]


def test_idle_gate_and_cancellation_release_stream_activity():
    async def run():
        gate = ActivityGate()
        async def cancelled_app(scope, receive, send):
            assert gate.active == 1
            raise asyncio.CancelledError
        app = ForegroundActivityMiddleware(cancelled_app, gate)
        with pytest.raises(asyncio.CancelledError):
            await app({"type": "http", "path": "/v1/chat/completions"}, None, None)
        assert gate.active == 0 and gate.idle.is_set()
        gate.begin()
        engine = ObserverEngine(FakeObserverClient(), idle_gate=gate)
        task = asyncio.create_task(engine.compact(base_capsule=None, events=[], snapshot_end_event_id=None))
        await asyncio.sleep(0)
        assert not task.done() and not engine.client.runs
        gate.end()
        await task
        assert len(engine.client.runs) == 1
    asyncio.run(run())


def test_real_observer_transport_persists_usage_and_job_telemetry(tmp_path, monkeypatch):
    import httpx
    from memory_gateway.structured_client import OpenAICompatStructuredClient
    original = httpx.AsyncClient
    fake = FakeObserverClient()
    async def handler(request):
        body = json.loads(request.content)
        assert body["response_format"]["json_schema"]["strict"]
        text = body["messages"][1]["content"].split("--- BEGIN INPUT ---\n", 1)[1].split("--- END INPUT ---", 1)[0]
        response = await fake.complete_json(json.loads(text))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response)}, "finish_reason": "stop"}],
                                        "usage": {"prompt_tokens": 78, "completion_tokens": 9}})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    store, _, _ = setup(tmp_path)
    client = OpenAICompatStructuredClient(endpoint="http://observer.local/v1", model="observer-test",
                                          prompt_version="observer-v1", system_prompt="structured observer",
                                          api_key="transport-only-sentinel", telemetry_recorder=store)
    worker = CompactionWorker(store, ObserverEngine(client, store=store), "worker")
    ingest(store, "FACT region=east")
    assert compact(store, worker) == "PROMOTED"
    runs = [run for run in store.list_model_runs() if run["thread_id"] == "t"]
    assert len(runs) == 1 and runs[0]["stage"] == "observer"
    assert runs[0]["input_tokens"] == 78 and runs[0]["output_tokens"] == 9
    assert runs[0]["job_id"] and runs[0]["generation"] == 1
    assert "transport-only-sentinel" not in json.dumps(runs)
    assert "transport-only-sentinel" not in json.dumps(store.get_active_capsule("t"))


def test_observer_loses_cas_without_replacing_other_promotion(tmp_path):
    from test_upgrade_layout import promote
    class Racing(FakeObserverClient):
        async def complete_json(self, payload):
            result = await super().complete_json(payload)
            promote(store, store.latest_event("t"))
            return result
    store, _, worker = setup(tmp_path, client=Racing())
    ingest(store, "FACT region=east")
    before = store.list_events("t")
    assert compact(store, worker) == "STALE"
    assert store.get_active_capsule("t")["content"] == "Memory through sequence 1"
    assert store.list_events("t") == before
