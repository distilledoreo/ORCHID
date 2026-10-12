"""Recorded-experiment checks are optional; ordinary tests use local fakes."""

import sqlite3
from pathlib import Path

import pytest


def pytest_collection_modifyitems(items):
    recorded = {
        "test_full_raw_metrics_monotonic_through_200",
        "test_tail_policy_matches_endurance_harness_budget",
        "test_reconstruct_generation_1_and_63",
    }
    root = Path(__file__).resolve().parents[1]
    paths = [root / "data" / name for name in
             ("live_endurance_degradation.db", "live_endurance_protocol_hardened.db")]
    available = True
    for path in paths:
        if not path.exists():
            available = False
            break
        try:
            with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
                connection.execute("SELECT id FROM compaction_jobs LIMIT 1")
        except sqlite3.Error:
            available = False
            break
    if not available:
        marker = pytest.mark.skip(reason="optional recorded endurance databases are not available")
        for item in items:
            if item.name in recorded:
                item.add_marker(marker)
