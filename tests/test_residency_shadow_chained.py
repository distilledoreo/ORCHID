"""Tests for chained residency shadow replay mechanics (no Gemini calls)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from residency_shadow_chained import (  # noqa: E402
    EXPECTED_BUNDLE_HASH,
    FORK_GENERATION,
    conn_for_generation,
    make_shadow_capsule,
    open_connections,
    source_db_path,
    verify_frozen_contract,
)
from residency_shadow_sample import reconstruct_input  # noqa: E402


def test_prompt_bundle_hash_matches_frozen_contract():
    assert verify_frozen_contract() == EXPECTED_BUNDLE_HASH


def test_source_db_fork_at_generation_63():
    assert source_db_path(62) != source_db_path(63)
    assert "degradation" in str(source_db_path(1))
    assert "protocol_hardened" in str(source_db_path(63))


def test_shadow_capsule_chain_ids_are_deterministic():
    cap1 = make_shadow_capsule(1, "hello world", None, "evt_abc")
    cap2 = make_shadow_capsule(2, "hello world updated", cap1.id, "evt_def")
    assert cap1.id.startswith("shadow_cap_0001_")
    assert cap2.id.startswith("shadow_cap_0002_")
    assert cap1.capsule_hash != cap2.capsule_hash


def test_reconstruct_generation_1_and_63():
    conns = open_connections()
    for generation in (1, FORK_GENERATION):
        rebuilt = reconstruct_input(conn_for_generation(conns, generation), generation)
        assert not isinstance(rebuilt, dict), f"gen {generation} unavailable"
        assert rebuilt.selected_events
        assert rebuilt.output_capsule_content
