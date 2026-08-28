"""Live and offline consolidator items[] batch gates for CapsuleEnvelopeV2."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..compaction import Event
from ..config import RuntimeConfig
from ..pipeline_adapters import build_lossless_engine
from ..selector_contract import load_golden_request_hashes
from .admit import DeterministicSemanticVerifier, SemanticVerifier
from .offline_gate import (
    build_generation_document,
    event_from_selector_payload,
    process_v2_document,
    summarize_reports,
)


def load_offline_document(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("offline document must be a JSON object")
    if "generations" not in payload:
        raise ValueError("offline document must contain generations[]")
    return payload


def generation_from_compaction(
    *,
    generation: int,
    events: list[Event],
    content: str,
    memory_items: tuple[dict[str, Any], ...],
    evidence_event_ids: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    evidence = evidence_event_ids or tuple(event.id for event in events)
    return {
        "generation": generation,
        "content": content,
        "events": [
            {
                "id": event.id,
                "sequence": event.sequence,
                "content": event.content,
                "content_hash": event.content_hash,
                "event_type": event.event_type,
                "role": event.role,
            }
            for event in events
        ],
        "items": [dict(item) for item in memory_items],
        "evidence_event_ids": list(evidence),
    }


def build_document_from_chunks(
    chunks: list[dict[str, Any]],
    *,
    generations: list[dict[str, Any]],
    capsule_max_tokens: int = 2_048,
    shadow_only: bool = True,
    require_items: bool = True,
) -> dict[str, Any]:
    return {
        "schema_version": "orchid.v2_offline_gate.consolidator_chunks.v1",
        "capsule_max_tokens": capsule_max_tokens,
        "shadow_only": shadow_only,
        "require_items": require_items,
        "generations": generations,
        "chunk_count": len(chunks),
    }


async def consolidate_selector_chunk(
    chunk: dict[str, Any],
    *,
    engine: Any,
) -> dict[str, Any]:
    events = [
        event_from_selector_payload(item)
        for item in chunk.get("events") or ()
        if isinstance(item, dict)
    ]
    if not events:
        raise ValueError("selector chunk has no events")
    result = await engine.compact(
        base_capsule=None,
        events=events,
        snapshot_end_event_id=events[-1].id,
    )
    return generation_from_compaction(
        generation=int(chunk.get("chunk_index", 0)) + 1,
        events=events,
        content=result.content,
        memory_items=result.memory_items,
        evidence_event_ids=result.evidence_event_ids,
    )


async def consolidate_chunk_live(
    chunk: dict[str, Any],
    *,
    config: RuntimeConfig,
) -> dict[str, Any]:
    live_config = RuntimeConfig(
        **{
            **config.__dict__,
            "memory_format": "v2_shadow",
            "canonicalizer_enabled": False,
        }
    )
    engine = build_lossless_engine(live_config)
    if engine is None:
        raise RuntimeError("compaction is not configured (selector/consolidator URLs required)")
    return await consolidate_selector_chunk(chunk, engine=engine)


def _consolidator_subprocess_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Ensure subprocess children inherit consolidator credentials from benchmark env."""

    env = dict(base or os.environ)
    if not env.get("ORCHID_CONSOLIDATOR_API_KEY"):
        for fallback in ("BENCHMARK_PROVIDER_API_KEY", "OPENROUTER_API_KEY"):
            if env.get(fallback):
                env["ORCHID_CONSOLIDATOR_API_KEY"] = env[fallback]
                break
    if not env.get("ORCHID_CONSOLIDATOR_URL") and env.get("BENCHMARK_PROVIDER_BASE_URL"):
        env["ORCHID_CONSOLIDATOR_URL"] = env["BENCHMARK_PROVIDER_BASE_URL"]
    if not env.get("ORCHID_CONSOLIDATOR_MODEL") and env.get("BENCHMARK_MODEL_ID"):
        env["ORCHID_CONSOLIDATOR_MODEL"] = env["BENCHMARK_MODEL_ID"]
    root = Path(__file__).resolve().parents[2]
    pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{root}{os.pathsep}{pythonpath}" if pythonpath else str(root)
    return env


def _consolidate_chunk_subprocess(chunk: dict[str, Any]) -> dict[str, Any]:
    """Run one live consolidator call in a fresh interpreter (defeats provider caching)."""

    root = Path(__file__).resolve().parents[2]
    env = _consolidator_subprocess_env()
    proc = subprocess.run(
        [sys.executable, "-m", "memory_gateway.admission.consolidator_batch"],
        input=json.dumps(chunk, ensure_ascii=False),
        capture_output=True,
        text=True,
        cwd=str(root),
        env=env,
        check=False,
        timeout=float(os.environ.get("ORCHID_CONSOLIDATOR_CHUNK_TIMEOUT_SECONDS", "300")),
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"subprocess consolidator chunk failed: {detail}")
    return json.loads(proc.stdout)


def _env_flag(name: str, *, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


async def run_live_consolidator_on_chunks(
    chunks: list[dict[str, Any]],
    *,
    config: RuntimeConfig,
    checkpoint_dir: Path | None = None,
) -> list[dict[str, Any]]:
    isolate_chunks = _env_flag("ORCHID_CONSOLIDATOR_ISOLATE_CHUNKS", default=True)
    use_subprocess = _env_flag("ORCHID_CONSOLIDATOR_SUBPROCESS_CHUNKS", default=True)
    shared_engine = None
    if not use_subprocess and not isolate_chunks:
        shared_engine = build_lossless_engine(config)
        if shared_engine is None:
            raise RuntimeError("compaction is not configured (selector/consolidator URLs required)")
    if checkpoint_dir is not None:
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
    generations: list[dict[str, Any]] = []
    inter_chunk_delay = float(os.environ.get("ORCHID_CONSOLIDATOR_INTER_CHUNK_DELAY_SECONDS", "1"))
    for index, chunk in enumerate(chunks):
        chunk_index = int(chunk.get("chunk_index", index))
        checkpoint_path = (
            checkpoint_dir / f"chunk_{chunk_index:04d}.generation.json"
            if checkpoint_dir is not None
            else None
        )
        if checkpoint_path is not None and checkpoint_path.exists():
            generations.append(json.loads(checkpoint_path.read_text(encoding="utf-8")))
            continue
        if index and inter_chunk_delay > 0:
            await asyncio.sleep(inter_chunk_delay)
        if use_subprocess:
            generation = await asyncio.to_thread(_consolidate_chunk_subprocess, chunk)
        elif isolate_chunks:
            engine = build_lossless_engine(config)
            if engine is None:
                raise RuntimeError("compaction is not configured (selector/consolidator URLs required)")
            generation = await consolidate_selector_chunk(chunk, engine=engine)
        else:
            generation = await consolidate_selector_chunk(chunk, engine=shared_engine)
        if checkpoint_path is not None:
            checkpoint_path.write_text(
                json.dumps(generation, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        generations.append(generation)
    return generations


async def run_consolidator_document_gate(
    document: dict[str, Any],
    *,
    verifier: SemanticVerifier | None = None,
) -> dict[str, Any]:
    verifier = verifier or DeterministicSemanticVerifier()
    reports = await process_v2_document(document, verifier=verifier)
    summary = summarize_reports(reports)
    rejected = [report for report in reports if not report["ok"]]
    return {
        "schema_version": "orchid.v2_offline_gate.consolidator_report.v1",
        **summary,
        "shadow_only": bool(document.get("shadow_only", True)),
        "require_items": bool(document.get("require_items", True)),
        "reports": reports,
        "rejections": [
            {
                "generation": report["generation"],
                "error": report["error"],
                "deterministic_violations": report["deterministic_violations"],
                "semantic_violations": report["semantic_violations"],
            }
            for report in rejected
        ],
    }


def assemble_offline_document_from_checkpoints(
    checkpoint_dir: Path,
    *,
    chunks: list[dict[str, Any]] | None = None,
    capsule_max_tokens: int = 2_048,
    shadow_only: bool = True,
    require_items: bool = True,
    require_all: bool = True,
) -> dict[str, Any]:
    if chunks is None:
        chunks = load_golden_request_hashes()["chunks"]
    selected_chunks: list[dict[str, Any]] = []
    generations: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        chunk_index = int(chunk.get("chunk_index", index))
        path = checkpoint_dir / f"chunk_{chunk_index:04d}.generation.json"
        if not path.exists():
            if require_all:
                raise FileNotFoundError(f"missing consolidator checkpoint: {path}")
            continue
        selected_chunks.append(chunk)
        generations.append(json.loads(path.read_text(encoding="utf-8")))
    if not selected_chunks:
        raise FileNotFoundError(f"no consolidator checkpoints found in {checkpoint_dir}")
    return build_document_from_chunks(
        selected_chunks,
        generations=generations,
        capsule_max_tokens=capsule_max_tokens,
        shadow_only=shadow_only,
        require_items=require_items,
    )


async def run_consolidator_chunk_gate(
    *,
    chunks: list[dict[str, Any]] | None = None,
    document: dict[str, Any] | None = None,
    config: RuntimeConfig | None = None,
    verifier: SemanticVerifier | None = None,
    shadow_only: bool = True,
    require_items: bool = True,
    capsule_max_tokens: int = 2_048,
    checkpoint_dir: Path | None = None,
) -> dict[str, Any]:
    if document is None:
        if chunks is None:
            raise ValueError("chunks or document is required")
        if config is None:
            raise ValueError("config is required for live consolidator chunk gate")
        live_config = RuntimeConfig(
            **{
                **config.__dict__,
                "memory_format": "v2_shadow",
                "canonicalizer_enabled": False,
            }
        )
        generations = await run_live_consolidator_on_chunks(
            chunks,
            config=live_config,
            checkpoint_dir=checkpoint_dir,
        )
        document = build_document_from_chunks(
            chunks,
            generations=generations,
            capsule_max_tokens=capsule_max_tokens,
            shadow_only=shadow_only,
            require_items=require_items,
        )
    document = {
        **document,
        "shadow_only": shadow_only,
        "require_items": require_items,
        "capsule_max_tokens": int(document.get("capsule_max_tokens") or capsule_max_tokens),
    }
    report = await run_consolidator_document_gate(document, verifier=verifier)
    report["chunk_count"] = document.get("chunk_count", len(document.get("generations") or []))
    report["mode"] = "live" if config is not None and chunks is not None else "offline"
    if config is not None:
        report["consolidator_model"] = config.consolidator_model
        report["consolidator_url"] = config.consolidator_url
        report["isolate_chunks"] = _env_flag("ORCHID_CONSOLIDATOR_ISOLATE_CHUNKS", default=True)
        report["subprocess_chunks"] = _env_flag("ORCHID_CONSOLIDATOR_SUBPROCESS_CHUNKS", default=True)
    if checkpoint_dir is not None:
        report["checkpoint_dir"] = str(checkpoint_dir)
    return report


def offline_document_from_synthesis(
    chunks: list[dict[str, Any]],
    *,
    synthesis: str = "per_event",
) -> dict[str, Any]:
    """Build an offline document using synthesis bridge (not real consolidator items)."""

    generations: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        events = [
            event_from_selector_payload(item)
            for item in chunk.get("events") or ()
            if isinstance(item, dict)
        ]
        if not events:
            continue
        generations.append(
            build_generation_document(
                generation=int(chunk.get("chunk_index", index - 1)) + 1,
                events=events,
                synthesis=synthesis,
            )
        )
    return build_document_from_chunks(
        chunks,
        generations=generations,
        require_items=False,
    )


def _main_consolidate_chunk() -> int:
    chunk = json.loads(sys.stdin.read())
    config = RuntimeConfig.from_env()
    generation = asyncio.run(consolidate_chunk_live(chunk, config=config))
    sys.stdout.write(json.dumps(generation, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main_consolidate_chunk())
