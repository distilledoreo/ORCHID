from __future__ import annotations

import json
from pathlib import Path

from tools.finalize_freetoshop_corrected_all_solar import rpc_summary


def test_rpc_summary_counts_only_completed_assistant_usage(tmp_path: Path) -> None:
    telemetry = tmp_path / "telemetry.jsonl"
    rows = [
        {
            "type": "message_update",
            "usage": {"input": 9_999, "output": 9_999, "totalTokens": 19_998},
        },
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "done"}],
                "timestamp": 123,
                "usage": {
                    "input": 100,
                    "output": 20,
                    "totalTokens": 120,
                    "cacheRead": 7,
                    "cost": {"total": 0.5},
                },
            },
        },
        {"type": "tool_execution_start", "toolName": "bash", "args": {"command": "npm test"}},
        {"type": "tool_execution_end", "isError": False},
    ]
    telemetry.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    result = rpc_summary(telemetry)

    assert result["usage_responses"] == 1
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 20
    assert result["cache_read_tokens"] == 7
    assert result["reported_cost_usd"] == 0.5
    assert result["largest_context_tokens"] == 100
    assert result["test_commands"] == ["npm test"]
    assert result["assistant_timeline"][0]["text"] == "done"
