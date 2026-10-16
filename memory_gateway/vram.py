"""Arm-isolated GPU VRAM sampling for paired release benchmarks.

Missing traces fail the scorecard. This module never treats absent hardware
as zero usage; callers must record samples or omit the summary entirely.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "orchid.paired_vram.v1"
MEASUREMENT_METHOD = "hardware_samples"


@dataclass(frozen=True)
class GpuSample:
    used_bytes: int
    capacity_bytes: int
    timestamp_s: float


def parse_nvidia_smi_csv(
    text: str,
    *,
    timestamp_s: float | None = None,
) -> tuple[GpuSample, ...]:
    """Parse `nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits`."""

    samples: list[GpuSample] = []
    observed_at = time.time() if timestamp_s is None else timestamp_s
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("memory"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            used_mib = float(parts[0])
            total_mib = float(parts[1])
        except ValueError:
            continue
        samples.append(
            GpuSample(
                used_bytes=int(used_mib * 1024 * 1024),
                capacity_bytes=int(total_mib * 1024 * 1024),
                timestamp_s=observed_at,
            )
        )
    return tuple(samples)


def summarize_device(samples: tuple[GpuSample, ...] | list[GpuSample]) -> dict[str, Any]:
    ordered = tuple(samples)
    if not ordered:
        return {
            "baseline_bytes": None,
            "peak_bytes": None,
            "byte_seconds": None,
            "capacity_bytes": None,
            "samples": 0,
        }
    baseline = ordered[0].used_bytes
    peak = max(sample.used_bytes for sample in ordered)
    capacity = max(sample.capacity_bytes for sample in ordered)
    byte_seconds = 0.0
    for previous, current in zip(ordered, ordered[1:]):
        dt = max(0.0, current.timestamp_s - previous.timestamp_s)
        byte_seconds += (previous.used_bytes + current.used_bytes) / 2.0 * dt
    return {
        "baseline_bytes": baseline,
        "peak_bytes": peak,
        "byte_seconds": byte_seconds,
        "capacity_bytes": capacity,
        "samples": len(ordered),
    }


def build_paired_summary(
    *,
    pi_main: dict[str, Any],
    orchid_main: dict[str, Any],
    orchid_selector: dict[str, Any],
    arm_isolated: bool,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "measurement_method": MEASUREMENT_METHOD,
        "arm_isolated": arm_isolated,
        "arms": {
            "PI_NATIVE": {"devices": {"main_gpu": dict(pi_main)}},
            "ORCHID": {
                "devices": {
                    "main_gpu": dict(orchid_main),
                    "selector_gpu": dict(orchid_selector),
                }
            },
        },
    }


def write_paired_summary(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def query_nvidia_smi() -> str | None:
    """Return nvidia-smi CSV or None. Failure is missing telemetry, never zero usage."""

    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if completed.returncode != 0:
        return None
    text = completed.stdout.strip()
    return text or None


def append_sample_jsonl(
    path: Path,
    samples: tuple[GpuSample, ...],
    *,
    timestamp_s: float,
    arm: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for index, sample in enumerate(samples):
            row: dict[str, Any] = {
                "timestamp_s": timestamp_s,
                "gpu_index": index,
                "used_bytes": sample.used_bytes,
                "capacity_bytes": sample.capacity_bytes,
            }
            if arm:
                row["arm"] = arm
            handle.write(json.dumps(row) + "\n")


def _normalize_arm_name(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    name = value.strip().upper().replace("-", "_")
    if name in {"PI", "PI_NATIVE", "ARM_A_PI", "ARM_A"}:
        return "PI_NATIVE"
    if name in {"ORCHID", "ARM_B_ORCHID", "ARM_B"}:
        return "ORCHID"
    if name in {"SHARED", "CONCURRENT"}:
        return None
    return name


def load_sample_jsonl(path: Path) -> dict[int, list[GpuSample]]:
    series: dict[int, list[GpuSample]] = {}
    if not path.exists():
        return series
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        gpu_index = row.get("gpu_index")
        used = row.get("used_bytes")
        capacity = row.get("capacity_bytes")
        timestamp_s = row.get("timestamp_s")
        if not isinstance(gpu_index, int) or not isinstance(used, int):
            continue
        if not isinstance(capacity, int) or not isinstance(timestamp_s, (int, float)):
            continue
        series.setdefault(gpu_index, []).append(
            GpuSample(
                used_bytes=used,
                capacity_bytes=capacity,
                timestamp_s=float(timestamp_s),
            )
        )
    return series


def load_arm_sample_jsonl(path: Path) -> dict[str, dict[int, list[GpuSample]]]:
    """Group samples by tagged arm. Untagged rows are omitted from this view."""

    arms: dict[str, dict[int, list[GpuSample]]] = {}
    if not path.exists():
        return arms
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        arm = _normalize_arm_name(row.get("arm"))
        if arm is None:
            continue
        gpu_index = row.get("gpu_index")
        used = row.get("used_bytes")
        capacity = row.get("capacity_bytes")
        timestamp_s = row.get("timestamp_s")
        if not isinstance(gpu_index, int) or not isinstance(used, int):
            continue
        if not isinstance(capacity, int) or not isinstance(timestamp_s, (int, float)):
            continue
        arms.setdefault(arm, {}).setdefault(gpu_index, []).append(
            GpuSample(
                used_bytes=used,
                capacity_bytes=capacity,
                timestamp_s=float(timestamp_s),
            )
        )
    return arms


def read_active_arm(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    try:
        return _normalize_arm_name(path.read_text(encoding="utf-8").splitlines()[0])
    except OSError:
        return None


def assemble_paired_summary_from_jsonl(
    path: Path,
    *,
    main_gpu_index: int = 0,
    selector_gpu_index: int = 1,
    arm_isolated: bool | None = None,
) -> dict[str, Any] | None:
    """Build a paired summary from live samples.

    Arm-tagged sequential traces isolate PI main from ORCHID main. Untagged
    concurrent traces on a shared GPU stay incomplete unless the caller
    explicitly claims isolation.
    """

    tagged = load_arm_sample_jsonl(path)
    if tagged:
        pi_series = tagged.get("PI_NATIVE") or {}
        orchid_series = tagged.get("ORCHID") or {}
        isolated = True if arm_isolated is None else arm_isolated
        return build_paired_summary(
            pi_main=summarize_device(tuple(pi_series.get(main_gpu_index, ()))),
            orchid_main=summarize_device(tuple(orchid_series.get(main_gpu_index, ()))),
            orchid_selector=summarize_device(
                tuple(orchid_series.get(selector_gpu_index, ()))
            ),
            arm_isolated=isolated,
        )
    series = load_sample_jsonl(path)
    if not series:
        return None
    isolated = False if arm_isolated is None else arm_isolated
    pi_main = summarize_device(series.get(main_gpu_index, ()))
    orchid_main = summarize_device(series.get(main_gpu_index, ()))
    orchid_selector = summarize_device(series.get(selector_gpu_index, ()))
    return build_paired_summary(
        pi_main=pi_main,
        orchid_main=orchid_main,
        orchid_selector=orchid_selector,
        arm_isolated=isolated,
    )


def sample_until_stopped(
    output: Path,
    *,
    interval_seconds: float = 2.0,
    query_fn: Any = query_nvidia_smi,
    should_continue: Any = None,
    arm_file: Path | None = None,
) -> int:
    """Poll GPU memory until stopped. Returns the number of successful polls."""

    polls = 0
    while True:
        if should_continue is not None and not should_continue():
            return polls
        raw = query_fn()
        if raw:
            observed_at = time.time()
            samples = parse_nvidia_smi_csv(raw, timestamp_s=observed_at)
            if samples:
                append_sample_jsonl(
                    output,
                    samples,
                    timestamp_s=observed_at,
                    arm=read_active_arm(arm_file),
                )
                polls += 1
        if should_continue is None:
            return polls
        time.sleep(max(0.05, interval_seconds))

