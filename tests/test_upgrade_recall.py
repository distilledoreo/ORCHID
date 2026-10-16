import asyncio
import json
import subprocess
import sys

import httpx
import pytest

from memory_gateway.db import SQLiteStore
from memory_gateway.evaluation import RollingSummaryEngine
from memory_gateway.gateway import create_app
from memory_gateway.config import RuntimeConfig
from memory_gateway.raw_recall import RawRecall
from memory_gateway.recall_mcp import RecallMCP


def add(store, project, thread, text, privacy="normal"):
    store.ensure_project_and_thread(project, thread)
    return store.append_event(project_id=project, thread_id=thread, role="user", event_type="user_message",
                              content=json.dumps({"role": "user", "content": text}), privacy_class=privacy)


def test_raw_fts_scope_privacy_range_and_source_identity(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    row = add(store, "p", "t", "Launch deadline is Friday; exact artifact src/widget.py")
    add(store, "p", "t", "Private deadline password", "restricted")
    other = add(store, "q", "u", "Other deadline secret")
    recall = RawRecall(store)
    before = store.list_events("t")
    hits = recall.lexical("p", "t", "deadline", 5)
    assert [hit["event_id"] for hit in hits] == [row["id"]]
    assert recall.read("p", "t", other["id"]) is None
    assert recall.read("p", "t", row["id"], 7, 15)["excerpt"] == "deadline"
    assert recall.lexical("p", "t", "event:" + row["id"], 1)[0]["event_id"] == row["id"]
    assert store.list_events("t") == before
    with pytest.raises(ValueError):
        recall.lexical("q", "t", "deadline", 5)


def test_failed_index_never_loses_raw_event_and_backfill_recovers(tmp_path, monkeypatch):
    import memory_gateway.raw_recall as module
    store = SQLiteStore(tmp_path / "test.db")
    original = module.index_event
    monkeypatch.setattr(module, "index_event", lambda *_: (_ for _ in ()).throw(RuntimeError("index unavailable")))
    row = add(store, "p", "t", "recoverable evidence")
    assert store.get_event(row["id"]) == row
    monkeypatch.setattr(module, "index_event", original)
    reopened = SQLiteStore(store.path)
    assert RawRecall(reopened).lexical("p", "t", "recoverable", 1)[0]["event_id"] == row["id"]
    assert reopened.get_event(row["id"]) == row


def test_optional_embeddings_are_scoped_chunked_and_telemetried(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    target = add(store, "p", "t", "The launch happens Friday.")
    add(store, "p", "t", "Water temperatures.")
    add(store, "p", "t", "Do not send private material.", "private")
    add(store, "q", "u", "Other project material.")
    inputs = []
    def handler(request):
        body = json.loads(request.content)
        inputs.extend(body["input"])
        vectors = [[1.0, 0.0] if "Friday" in text or "deadline" in text else [0.0, 1.0]
                   for text in body["input"]]
        return httpx.Response(200, json={"data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)],
                                        "usage": {"prompt_tokens": 40}})
    recall = RawRecall(store, embeddings_url="http://embedding.local/v1", embeddings_model="test",
                       embeddings_api_key="transport-only-secret", transport=httpx.MockTransport(handler))
    async def run():
        assert (await recall.index_embeddings("p", "t"))["indexed_chunks"] == 2
        assert (await recall.index_embeddings("p", "t"))["indexed_chunks"] == 0
        result = await recall.recall("p", "t", "deadline", 5)
        assert result["embedding_status"] == "used"
        assert [hit["event_id"] for hit in result["results"]] == [target["id"]]
    asyncio.run(run())
    assert not any("private" in text or "Other project" in text for text in inputs)
    runs = store.list_model_runs()
    assert len(runs) == 2 and all(run["input_tokens"] == 40 for run in runs)
    assert "transport-only-secret" not in json.dumps(runs)


def test_embedding_failure_keeps_lexical_results(tmp_path):
    store = SQLiteStore(tmp_path / "test.db")
    row = add(store, "p", "t", "deadline Friday")
    recall = RawRecall(store, embeddings_url="http://embedding.local/v1", embeddings_model="test",
                       transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    with store.transaction() as connection:
        connection.execute("INSERT INTO raw_event_embeddings VALUES (?,0,15,?,?)", (row["id"], recall.index_id, "[1.0,0.0]"))
    result = asyncio.run(recall.recall("p", "t", "deadline", 5))
    assert result["embedding_status"] == "failed_open"
    assert result["results"][0]["event_id"] == row["id"]


def test_mcp_calls_real_scoped_http_recall_and_rejects_scope_override(tmp_path):
    config = RuntimeConfig(str(tmp_path / "test.db"), "http://unused", None, None, "", "", None,
                           "", "", None, None, None, None, 128000, .65, .85, .25, 1,
                           capsule_verification_enabled=False)
    app = create_app(config=config, compaction_engine=RollingSummaryEngine())
    row = add(app.state.store, "p", "t", "deadline Friday")
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://orchid") as client:
            assert (await client.post("/v1/memory/recall", json={"query": "deadline"})).status_code == 400
            mcp = RecallMCP(client, "p", "t")
            init = await mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            assert "tools" in init["result"]["capabilities"]
            assert await mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
            listed = await mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            assert listed["result"]["tools"][0]["name"] == "recall"
            args = {"query": "deadline", "k": 1}
            request = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "recall", "arguments": args}}
            result = await mcp.handle(request)
            assert result["result"]["structuredContent"]["results"][0]["event_id"] == row["id"]
            args["project"] = "q"
            assert (await mcp.handle(request))["error"]["code"] == -32602
    asyncio.run(run())


def test_mcp_stdio_outputs_only_protocol_json():
    messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    result = subprocess.run([sys.executable, "-m", "memory_gateway.recall_mcp", "--project", "p", "--thread", "t"],
                            input="\n".join(json.dumps(m) for m in messages) + "\n", text=True,
                            capture_output=True, timeout=10, check=True)
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert [reply["id"] for reply in replies] == [1, 2]
