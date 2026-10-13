import asyncio
import json

import pytest

from memory_gateway.activity import ActivityGate, ForegroundActivityMiddleware
from memory_gateway.compaction import CompactionWorker, Event, queue_snapshot_job
from memory_gateway.db import SQLiteStore
from memory_gateway.evaluation import FakeObserverClient
from memory_gateway.ingestion import append_novel_messages
from memory_gateway.observer import ObserverEngine, Operation, apply_operations, source_spans


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


def test_reflection_that_frees_too_little_falls_back_without_losing_events_or_items(tmp_path):
    class Broken(FakeObserverClient):
        async def complete_json(self, payload):
            if payload["task"] == "reflection":
                return {"decisions": [], "merges": []}
            return await super().complete_json(payload)
    store, _, worker = setup(tmp_path, budget=35, client=Broken())
    ingest(store, "FACT one=alpha FACT two=beta FACT three=gamma")
    before = store.list_events("t")
    # Reflection is an optimisation: when the model's answer does not fit, software hides items by priority
    # and the observation is still promoted. Nothing is deleted from the ledger or the event log.
    assert compact(store, worker) == "PROMOTED"
    state = json.loads(store.get_active_capsule("t")["model_metadata_json"])["observer_state"]
    assert len(state["items"]) == 3 and not any(item["hot"] for item in state["items"])
    assert "did not fit" in state["reflection_error"]
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
        waiting = Event.from_row({"id": "evt_wait", "sequence": 1, "content": "FACT region=east", "content_hash": "h",
                                  "event_type": "user_message", "role": "user"})
        task = asyncio.create_task(engine.compact(base_capsule=None, events=[waiting], snapshot_end_event_id="evt_wait"))
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


def test_transport_error_does_not_expose_credential_in_job_or_telemetry(tmp_path, monkeypatch):
    import httpx
    from memory_gateway.structured_client import OpenAICompatStructuredClient
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _: httpx.Response(503, text="Bearer transport-only-secret"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=transport, **kw))
    store, _, _ = setup(tmp_path)
    client = OpenAICompatStructuredClient(endpoint="http://observer.local/v1", model="test",
                                          prompt_version="observer-v1", system_prompt="observer",
                                          api_key="transport-only-secret", telemetry_recorder=store)
    worker = CompactionWorker(store, ObserverEngine(client, store=store), "worker")
    ingest(store, "FACT region=east")
    assert compact(store, worker) == "FAILED"
    assert "transport-only-secret" not in json.dumps(store.list_jobs())
    assert "transport-only-secret" not in json.dumps(store.list_model_runs())


def _add(text, start, end, event="e"):
    return Operation.model_validate({"op": "add", "target_id": None, "reason": "evidence",
                                     "item": {"claim": text, "kind": "fact",
                                              "quotes": [{"event_id": event, "start": start, "end": end, "text": text}]}})


def test_software_locates_verbatim_quotes_when_model_offsets_are_wrong():
    source = "intro. region east. later the region west applies. region east again."
    items, rejected = apply_operations([], [_add("region west", 0, 5)], {"e": (source, 7)})
    assert not rejected
    quote = items[0]["quotes"][0]
    assert source[quote["start"]:quote["end"]] == "region west"
    # Offsets past the end of the source are a counting error, not a different quote.
    items, rejected = apply_operations([], [_add("region west", 900, 911)], {"e": (source, 7)})
    assert not rejected and source[items[0]["quotes"][0]["start"]:items[0]["quotes"][0]["end"]] == "region west"


def test_repeated_text_resolves_to_the_occurrence_nearest_the_models_estimate():
    source = "intro. region east. later the region west applies. region east again."
    first, last = source.index("region east"), source.rindex("region east")
    near_first, _ = apply_operations([], [_add("region east", first + 3, first + 14)], {"e": (source, 7)})
    near_last, _ = apply_operations([], [_add("region east", last - 4, last + 7)], {"e": (source, 7)})
    assert near_first[0]["quotes"][0]["start"] == first
    assert near_last[0]["quotes"][0]["start"] == last


def test_paraphrased_or_unknown_source_quotes_are_still_rejected():
    source = "region east"
    _, rejected = apply_operations([], [_add("the eastern region", 0, 11)], {"e": (source, 4)})
    assert rejected and "exact substring" in rejected[0]["reason"]
    _, rejected = apply_operations([], [_add("region east", 0, 11, event="missing")], {"e": (source, 4)})
    assert rejected and "unknown source" in rejected[0]["reason"]


def test_a_batch_of_only_invalid_operations_does_not_advance_coverage(tmp_path):
    class Paraphrasing(FakeObserverClient):
        async def complete_json(self, payload):
            event = payload["events"][0]["event_id"]
            return {"operations": [{"op": "add", "target_id": None, "reason": "evidence",
                                    "item": {"claim": "region is east", "kind": "fact",
                                             "quotes": [{"event_id": event, "start": 0, "end": 9, "text": "not in the source"}]}}]}
    store, _, worker = setup(tmp_path, client=Paraphrasing())
    ingest(store, "FACT region=east")
    assert compact(store, worker) == "FAILED"
    assert store.get_active_capsule("t") is None


def _ingest_as(store, role, content, request):
    return append_novel_messages(store, project_id="p", thread_id="t", messages=[{"role": role, "content": content}],
                                 request_id=request, source="response").appended_events[0]


class _Recording(FakeObserverClient):
    def __init__(self):
        super().__init__()
        self.payloads = []

    async def complete_json(self, payload):
        self.payloads.append(payload)
        return await super().complete_json(payload)


def test_instructions_are_covered_but_never_sent_to_the_observer(tmp_path):
    client = _Recording()
    store, _, worker = setup(tmp_path, client=client)
    _ingest_as(store, "developer", "FACT harness=pi You are a coding assistant.", "a")
    assert compact(store, worker) == "PROMOTED"
    assert client.payloads == []
    _ingest_as(store, "developer", "FACT harness=pi Again.", "b")
    ingest(store, "FACT region=east", "c")
    assert compact(store, worker) == "PROMOTED"
    sent = [event["role"] for payload in client.payloads for event in payload["events"]]
    assert sent == ["user"]
    state = json.loads(store.get_active_capsule("t")["model_metadata_json"])["observer_state"]
    assert [item["claim"] for item in state["items"]] == ["FACT region=east"]


def test_one_malformed_operation_does_not_discard_the_reply(tmp_path):
    class Sloppy(FakeObserverClient):
        async def complete_json(self, payload):
            response = await super().complete_json(payload)
            response["operations"].append({"op": "add", "claim": "missing the item envelope"})
            return response
    store, _, worker = setup(tmp_path, client=Sloppy())
    ingest(store, "FACT region=east")
    assert compact(store, worker) == "PROMOTED"
    state = json.loads(store.get_active_capsule("t")["model_metadata_json"])["observer_state"]
    assert [item["claim"] for item in state["items"]] == ["FACT region=east"]
    assert [entry["reason"] for entry in state["rejected_operations"]] == ["malformed operation"]


def test_failed_reply_is_retried_with_a_tighter_limit_then_on_half_the_input(tmp_path):
    class Overflowing(_Recording):
        """Fails like a reply cut off at the output cap whenever it is asked about more than one event."""
        async def complete_json(self, payload):
            if payload["task"] == "observe" and len(payload["events"]) > 1:
                self.payloads.append(payload)
                raise RuntimeError("truncated")
            return await super().complete_json(payload)
    client = Overflowing()
    store, _, worker = setup(tmp_path, client=client)
    ingest(store, "FACT one=alpha", "a")
    ingest(store, "FACT two=beta", "b")
    assert compact(store, worker) == "PROMOTED"
    attempts = [(payload["max_operations"], len(payload["events"])) for payload in client.payloads]
    assert attempts == [(8, 2), (3, 2), (8, 1), (8, 1)]
    state = json.loads(store.get_active_capsule("t")["model_metadata_json"])["observer_state"]
    assert sorted(item["claim"] for item in state["items"]) == ["FACT one=alpha", "FACT two=beta"]


def test_split_keeps_offsets_in_the_original_source_view():
    from memory_gateway.observer import split_events
    text = ("line of source text\n" * 200)
    event = {"event_id": "e", "sequence": 1, "role": "tool", "spans": [{"start": 0, "end": len(text), "text": text}], "offloaded": False}
    first, second = split_events([event])
    a, b = first[0]["spans"][0], second[0]["spans"][0]
    assert a["text"] + b["text"] == text and a["end"] == b["start"] and a["text"].endswith("\n")
    assert text[b["start"]:b["end"]] == b["text"]
    small = {**event, "spans": [{"start": 0, "end": 5, "text": "short"}]}
    assert split_events([small]) is None


def test_large_snapshot_is_observed_in_several_calls(tmp_path):
    client = _Recording()
    store = SQLiteStore(tmp_path / "test.db")
    store.ensure_project_and_thread("p", "t")
    worker = CompactionWorker(store, ObserverEngine(client, store=store, call_source_tokens=60), "test")
    for index, key in enumerate(("alpha", "beta", "gamma", "delta")):
        ingest(store, f"FACT {key}=value{index} " + "padding words " * 12, f"r{index}")
    queue_snapshot_job(store, "t", raw_events=True)
    assert asyncio.run(worker.run_once()) == "PROMOTED"
    assert len(client.payloads) > 1
    assert sum(len(payload["events"]) for payload in client.payloads) == 4
    state = json.loads(store.get_active_capsule("t")["model_metadata_json"])["observer_state"]
    assert len(state["items"]) == 4


def test_raw_event_snapshots_are_not_bounded_by_the_selector_chunk_rule(tmp_path):
    from memory_gateway.compaction import freeze_snapshot
    store = SQLiteStore(tmp_path / "test.db")
    store.ensure_project_and_thread("p", "t")
    for index in range(6):
        ingest(store, f"FACT key{index}=value " + "padding words " * 400, f"r{index}")
    legacy = freeze_snapshot(store, "t", source_window_tokens=8000)
    raw = freeze_snapshot(store, "t", source_window_tokens=8000, raw_events=True)
    assert len(raw.events) > len(legacy.events)


def test_a_long_user_message_is_read_in_small_pieces(tmp_path):
    from memory_gateway.observer import USER_CALL_TOKENS, pieces
    client = _Recording()
    store, _, worker = setup(tmp_path, client=client)
    keys = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta")
    goal = "".join(f"FACT {key}=required by the person.\n"
                   + "".join(f"{key} requirement detail number {n} of the plan\n" for n in range(150)) for key in keys)
    event = ingest(store, goal)
    queue_snapshot_job(store, "t", raw_events=True)
    assert asyncio.run(worker.run_once()) == "PROMOTED"
    assert len(client.payloads) > 2
    assert all(len(payload["events"]) == 1 and payload["events"][0]["event_id"] == event["id"] for payload in client.payloads)
    spans = [span for payload in client.payloads for span in payload["events"][0]["spans"]]
    assert all(left["end"] <= right["start"] for left, right in zip(spans, spans[1:]))
    state = json.loads(store.get_active_capsule("t")["model_metadata_json"])["observer_state"]
    assert sorted(item["claim"] for item in state["items"]) == sorted(f"FACT {key}=required" for key in keys)
    small = {"event_id": "e", "role": "user", "spans": [{"start": 0, "end": 5, "text": "short"}], "offloaded": False}
    assert pieces(small, USER_CALL_TOKENS) == [small]


def test_an_unlisted_kind_is_relabelled_instead_of_costing_the_claim(tmp_path):
    class Inventive(FakeObserverClient):
        async def complete_json(self, payload):
            response = await super().complete_json(payload)
            for operation in response["operations"]:
                if operation.get("item"):
                    operation["item"]["kind"] = "requirement"
            return response
    store, _, worker = setup(tmp_path, client=Inventive())
    ingest(store, "FACT region=east")
    assert compact(store, worker) == "PROMOTED"
    state = json.loads(store.get_active_capsule("t")["model_metadata_json"])["observer_state"]
    assert [(item["claim"], item["kind"]) for item in state["items"]] == [("FACT region=east", "fact")]
    assert state["rejected_operations"] == []


def test_reflection_lists_only_changes_and_unlisted_items_are_kept(tmp_path):
    class Selective(_Recording):
        async def complete_json(self, payload):
            if payload["task"] == "reflection":
                self.payloads.append(payload)
                first = payload["items"][0]["id"]
                return {"decisions": [{"item_id": first, "action": "dropped", "reason": "least needed", "merge_group": None}],
                        "merges": []}
            return await super().complete_json(payload)
    from memory_gateway.context import estimate_tokens
    (tmp_path / "probe").mkdir()
    probe, _, probe_worker = setup(tmp_path / "probe")
    ingest(probe, "FACT one=alpha FACT two=beta FACT three=gamma")
    assert compact(probe, probe_worker) == "PROMOTED"
    unreflected = estimate_tokens(probe.get_active_capsule("t")["content"])
    client = Selective()
    store = SQLiteStore(tmp_path / "test.db")
    store.ensure_project_and_thread("p", "t")
    # One token under the unreflected size: dropping a single item is enough to fit.
    worker = CompactionWorker(store, ObserverEngine(client, store=store, memory_budget_tokens=unreflected - 1), "test")
    ingest(store, "FACT one=alpha FACT two=beta FACT three=gamma")
    assert compact(store, worker) == "PROMOTED"
    state = json.loads(store.get_active_capsule("t")["model_metadata_json"])["observer_state"]
    assert [item["hot"] for item in state["items"]] == [False, True, True]
    assert state["reflection_error"] is None
    sent = [payload for payload in client.payloads if payload["task"] == "reflection"][0]
    assert set(sent["items"][0]) == {"id", "kind", "valid_from", "claim", "quotes"}


def test_failed_reflection_falls_back_to_priority_eviction(tmp_path):
    from memory_gateway.context import estimate_tokens

    class NoReflection(FakeObserverClient):
        async def complete_json(self, payload):
            if payload["task"] == "reflection":
                raise RuntimeError("cut off")
            return await super().complete_json(payload)
    (tmp_path / "probe").mkdir()
    probe, _, probe_worker = setup(tmp_path / "probe")
    ingest(probe, "FACT one=alpha FACT rejected_two=beta FACT three=gamma")
    assert compact(probe, probe_worker) == "PROMOTED"
    unreflected = estimate_tokens(probe.get_active_capsule("t")["content"])
    store = SQLiteStore(tmp_path / "test.db")
    store.ensure_project_and_thread("p", "t")
    worker = CompactionWorker(store, ObserverEngine(NoReflection(), store=store, memory_budget_tokens=unreflected - 1), "test")
    ingest(store, "FACT one=alpha FACT rejected_two=beta FACT three=gamma")
    assert compact(store, worker) == "PROMOTED"
    active = store.get_active_capsule("t")
    state = json.loads(active["model_metadata_json"])["observer_state"]
    assert estimate_tokens(active["content"]) <= unreflected - 1
    # The oldest plain fact is hidden first; the decision outranks it and stays visible.
    assert [(item["kind"], item["hot"]) for item in state["items"]] == [("fact", False), ("decision", True), ("fact", True)]
    assert len(state["items"]) == 3 and "reflection" in state["reflection_error"]
    assert [entry["action"] for entry in state["reflection"]] == ["evicted"]


def test_quotes_may_differ_from_the_source_only_in_whitespace():
    source = "# Architecture\r\n\r\nPixel layers own   immutable\r\nsparse tiles."
    items, rejected = apply_operations([], [_add("Pixel layers own immutable sparse tiles.", 0, 10)], {"e": (source, 3)})
    assert not rejected
    quote = items[0]["quotes"][0]
    assert quote["text"] == source[quote["start"]:quote["end"]] == "Pixel layers own   immutable\r\nsparse tiles."
    _, rejected = apply_operations([], [_add("Pixel layers own mutable sparse tiles.", 0, 10)], {"e": (source, 3)})
    assert rejected


def test_long_tool_output_is_read_in_parts(tmp_path):
    client = _Recording()
    store, _, worker = setup(tmp_path, client=client)
    document = "".join(f"design rule number {n} for the document model and its tiles\n" for n in range(900))
    _ingest_as(store, "tool", "FACT architecture=tiled\n" + document + "FACT history=immutable\n", "doc")
    queue_snapshot_job(store, "t", raw_events=True)
    assert asyncio.run(worker.run_once()) == "PROMOTED"
    assert len(client.payloads) > 2
    state = json.loads(store.get_active_capsule("t")["model_metadata_json"])["observer_state"]
    assert sorted(item["claim"] for item in state["items"]) == ["FACT architecture=tiled", "FACT history=immutable"]
