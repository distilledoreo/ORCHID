import json

from memory_gateway.coding import render_message, update_ledger
from memory_gateway.compaction import Event
from memory_gateway.context import ContextAssembler
from memory_gateway.db import SQLiteStore
from memory_gateway.ingestion import append_novel_messages
from test_upgrade_layout import promote


def append(store, message, request):
    return append_novel_messages(store, project_id="p", thread_id="t", source="response",
                                  request_id=request, messages=[message]).appended_events[0]


def test_offload_preserves_original_and_range_handle(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    store.ensure_project_and_thread("p", "t")
    message = {"role": "user", "content": "start\n" + "unique detail in the middle. " * 400 + "\nend"}
    row = append(store, message, "large")
    policy = {"keep_turns": 2, "offload_tokens": 128}
    original = store.get_event(row["id"])
    result = render_message(store, row, message, None, policy)
    assert "event:" + row["id"] in result["content"]
    assert len(result["content"]) < 1000 and len(message["content"]) > 10000
    from memory_gateway.raw_recall import RawRecall
    assert RawRecall(store).read("p", "t", row["id"], 0, 4096)["excerpt"] == message["content"][:4096]
    assert store.get_event(row["id"]) == original
    assert render_message(store, row, message, None, None) == message
    with store.transaction() as connection:
        connection.execute("DELETE FROM raw_event_documents WHERE event_id=?", (row["id"],))
    assert render_message(store, row, message, None, policy) == message


def test_mask_cutoff_freezes_only_at_successful_promotion(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    store.ensure_project_and_thread("p", "t")
    store.coding_policy = {"keep_turns": 1, "offload_tokens": 99999}
    first = append(store, {"role": "user", "content": "task"}, "u")
    tool = append(store, {"role": "tool", "tool_call_id": "old", "content": "old output"}, "tool")
    append(store, {"role": "assistant", "content": "new turn"}, "a")
    promote(store, first)
    active = store.get_active_capsule("t")
    before = ContextAssembler(store, layout="stable").assemble("t", [])
    assert "OLDER TOOL OUTPUT" in json.dumps(before.messages)
    append(store, {"role": "assistant", "content": "later turn"}, "later")
    after = ContextAssembler(store, layout="stable").assemble("t", [])
    assert after.messages[:len(before.messages)] == before.messages
    with store.connect() as connection:
        rows = connection.execute("SELECT * FROM coding_prompt_views").fetchall()
    assert len(rows) == 1 and rows[0]["capsule_id"] == active["id"]
    assert store.get_event(tool["id"])["content"] == tool["content"]


def test_ledger_handles_pending_calls_failed_writes_and_patch_results(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    store.ensure_project_and_thread("p", "t")
    patch = "*** Begin Patch\n*** Update File: src/widget.py\n@@\n-x\n+y\n*** Add File: assets/icon.py\n+data\n*** End Patch"
    call = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c", "type": "function", "function": {"name": "apply_patch", "arguments": json.dumps({"patch": patch})}}]}
    row = append(store, call, "call")
    ledger, pending = update_ledger([Event.from_row(row)])
    assert pending and not ledger["src/widget.py"]["edited"]
    failed = append(store, {"role": "tool", "tool_call_id": "c", "content": '{"status":"failed","error":"no change"}'}, "failed")
    unchanged, waiting = update_ledger([Event.from_row(failed)], ledger, pending)
    assert not unchanged["src/widget.py"]["edited"] and not waiting
    success = append(store, {"role": "tool", "tool_call_id": "c", "content": "Success. Updated the following files."}, "ok")
    changed, waiting = update_ledger([Event.from_row(success)], ledger, pending)
    assert changed["src/widget.py"]["edited"][0]["result_event_id"] == success["id"]
    assert changed["assets/icon.py"]["created"] and not waiting


def test_coding_replay_probes_and_file_ledger():
    from memory_gateway.evaluation import evaluate
    report = evaluate()
    coding = report["arms"][4]
    assert coding["arm"] == "coding"
    assert all(probe["passed"] for probe in coding["probes"].values())
    assert coding["raw_recall_probe"]["passed"]
    assert coding["file_ledger"]["src/widget.py"]["read"]
    assert coding["file_ledger"]["assets/icon.png"]["created"]


def test_oversized_unique_source_promotes_with_recoverable_artifact_handle(tmp_path):
    from memory_gateway.compaction import CompactionWorker, queue_snapshot_job
    from memory_gateway.evaluation import FakeObserverClient
    from memory_gateway.observer import ObserverEngine
    import asyncio
    store = SQLiteStore(tmp_path / "test.db")
    store.ensure_project_and_thread("p", "t")
    text = "\n".join(f"unique diagnostic row {i}: value_{i * 7919}" for i in range(5000)) + "\nFACT late=one"
    row = append(store, {"role": "user", "content": text}, "large")
    engine = ObserverEngine(FakeObserverClient(), store=store, coding_agent_mode=True)
    worker = CompactionWorker(store, engine, "worker")
    queue_snapshot_job(store, "t")
    assert asyncio.run(worker.run_once()) == "PROMOTED"
    active = store.get_active_capsule("t")
    assert "event:" + row["id"] in active["content"] and "FACT late=one" in active["content"]
    from memory_gateway.raw_recall import RawRecall
    assert RawRecall(store).read("p", "t", row["id"], len(text) - 13)["excerpt"] == "FACT late=one"
    assert store.get_event(row["id"])["content"] == row["content"]
