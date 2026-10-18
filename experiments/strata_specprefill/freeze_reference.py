"""Freeze the validated 8k reference bundle (P100, Swift + Qwen3.5-0.8B).

Copies configs, offline scores/signals/positions, live quality+timings, and
environment pins into artifacts/strata-specprefill/reference-8k-v1/ with a
sha256 manifest. Raw prompt text stays in the ignored data dir; the bundle
holds selected token IDs + positions + hashes needed to reproduce/verify.

Usage: .../.venv/bin/python -m experiments.strata_specprefill.freeze_reference
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/strata-specprefill/reference-8k-v1"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, str] = {}

    def add_file(src: Path, name: str) -> None:
        data = src.read_bytes()
        (OUT / name).write_bytes(data)
        manifest[name] = sha(data)

    # 1. Model + engine configs.
    for n in ("alone-config.json", "colocated-config.json", "longctx-8k-alone.json",
              "longctx-16kctx-alone.json"):
        add_file(ROOT / f"data/strata_specprefill/{n}", f"config-{n}")
    # 2. Offline student results (scores hashes, signals, per-rate positions).
    for n in ("longctx-offline-8k-dev-v1.json", "longctx-offline-8k-held-v1.json"):
        add_file(ROOT / f"data/strata_specprefill/{n}", n)
    # 3. Live quality + timing results.
    for n in ("requests.jsonl", "COMPLETE.json"):
        for split in ("dev", "held"):
            src = ROOT / f"data/strata_specprefill/longctx-live-8k-{split}-v1/{n}"
            add_file(src, f"live-8k-{split}-{n.replace('.json', '.json').replace('requests.jsonl', 'requests.jsonl')}")
    # 4. Short-context reference pointers (prior validated results, not duplicated).
    for n in ("smoke-summary.json", "adaptive-retention/summary.json",
              "keep-rates/summary.json"):
        src = ROOT / f"artifacts/strata-specprefill/{n}"
        if src.exists():
            add_file(src, "prior-" + n.replace("/", "-"))

    env = {
        "target": "Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S (IQ3_S GGUF)",
        "student": "Qwen3.5-0.8B FP16 (/media/distilledoreo/Storage/AI/state-projection/models/Qwen3.5-0.8B)",
        "hardware": "Tesla P100-PCIE-16GB, single GPU, sequential student-then-target",
        "engine": "Strata 0.1.43 / CUDA 12.9, int8 KV, MTP=4 (spec 4)",
        "student_runtime": "torch 2.7.1+cu126, transformers 5.19.0 (reference fallbacks)",
        "contexts": {"validation_ctx": 16384, "validation_prompt_tokens": "~8170-8290",
                     "short_ctx": 4096},
        "controls": {"route_tail_skip": 0, "cpu_prefill_share": 0, "pcie_frac": 1,
                     "prompt_cache": 0, "temperature": 0},
        "timing": "server_ttft_s live; pipeline_seq_ttft_s = offline student_s + server_ttft_s (sequential estimate)",
    }
    try:
        env["strata_commit"] = subprocess.check_output(
            ["git", "-C", "/home/distilledoreo/AI/Strata", "rev-parse", "HEAD"],
            text=True).strip()
    except Exception:
        env["strata_commit"] = "unknown"
    env["orchid_commit"] = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    (OUT / "environment.json").write_text(json.dumps(env, indent=2) + "\n")
    manifest["environment.json"] = sha((OUT / "environment.json").read_bytes())

    # 5. Quality rollup (recomputed, not copied).
    rollup: dict = {"splits": {}}
    for split in ("dev", "held"):
        rows = [json.loads(line) for line in
                (OUT / f"live-8k-{split}-requests.jsonl").read_text().splitlines()]
        arms: dict = {}
        for r in rows:
            arms.setdefault(r["arm"], []).append(r)
        rollup["splits"][split] = {
            arm: {"correct": sum(x["correct"] for x in v), "n": len(v),
                  "mean_server_ttft_s": sum(x["server_ttft_s"] for x in v) / len(v),
                  "mean_pipeline_seq_ttft_s": sum(x["pipeline_seq_ttft_s"] for x in v) / len(v),
                  "mean_retained": sum(x["retained_tokens"] for x in v) / len(v)}
            for arm, v in sorted(arms.items())
        }
    (OUT / "quality.json").write_text(json.dumps(rollup, indent=2) + "\n")
    manifest["quality.json"] = sha((OUT / "quality.json").read_bytes())

    (OUT / "MANIFEST.json").write_text(json.dumps({"files": manifest}, indent=2) + "\n")
    print(f"frozen {OUT} files={len(manifest)}")
    print(json.dumps(rollup, indent=1))


if __name__ == "__main__":
    main()
