from __future__ import annotations

import json
from pathlib import Path

from memory_gateway.benchmark_scorecard import summarize_paired_vram
from memory_gateway.vram import (
    GpuSample,
    assemble_paired_summary_from_jsonl,
    build_paired_summary,
    parse_nvidia_smi_csv,
    sample_until_stopped,
    summarize_device,
    write_paired_summary,
)


def test_parse_nvidia_smi_csv_converts_mib_to_bytes() -> None:
    samples = parse_nvidia_smi_csv("1024, 8192\n512, 8192\n")
    assert len(samples) == 2
    assert samples[0].used_bytes == 1024 * 1024 * 1024
    assert samples[0].capacity_bytes == 8192 * 1024 * 1024


def test_summarize_device_integrates_trapezoid_byte_seconds() -> None:
    samples = (
        GpuSample(used_bytes=100, capacity_bytes=1000, timestamp_s=0.0),
        GpuSample(used_bytes=300, capacity_bytes=1000, timestamp_s=2.0),
    )
    summary = summarize_device(samples)
    assert summary["baseline_bytes"] == 100
    assert summary["peak_bytes"] == 300
    assert summary["byte_seconds"] == 400.0
    assert summary["samples"] == 2


def test_empty_device_summary_is_missing_not_zero() -> None:
    summary = summarize_device(())
    assert summary["baseline_bytes"] is None
    assert summary["peak_bytes"] is None
    assert summary["byte_seconds"] is None
    assert summary["samples"] == 0


def test_write_paired_summary_is_complete_when_arm_isolated(tmp_path: Path) -> None:
    device = summarize_device(
        (
            GpuSample(used_bytes=100, capacity_bytes=1000, timestamp_s=0.0),
            GpuSample(used_bytes=200, capacity_bytes=1000, timestamp_s=1.0),
        )
    )
    selector = {**device, "peak_bytes": 300}
    payload = build_paired_summary(
        pi_main=device,
        orchid_main=device,
        orchid_selector=selector,
        arm_isolated=True,
    )
    write_paired_summary(tmp_path / "hardware" / "paired_vram_summary.json", payload)
    summary = summarize_paired_vram(tmp_path)
    assert summary["complete"] is True
    assert summary["orchid"]["selector_peak_fraction"] == 0.3


def test_shared_gpu_sampling_is_incomplete(tmp_path: Path) -> None:
    device = summarize_device(
        (
            GpuSample(used_bytes=100, capacity_bytes=1000, timestamp_s=0.0),
            GpuSample(used_bytes=200, capacity_bytes=1000, timestamp_s=1.0),
        )
    )
    payload = build_paired_summary(
        pi_main=device,
        orchid_main=device,
        orchid_selector=device,
        arm_isolated=False,
    )
    write_paired_summary(tmp_path / "hardware" / "paired_vram_summary.json", payload)
    summary = summarize_paired_vram(tmp_path)
    assert summary["complete"] is False
    assert json.loads(
        (tmp_path / "hardware" / "paired_vram_summary.json").read_text(encoding="utf-8")
    )["arm_isolated"] is False


def test_arm_tagged_sequential_samples_are_isolated(tmp_path: Path) -> None:
    path = tmp_path / "hardware" / "vram_samples.jsonl"
    arm_file = tmp_path / "hardware" / "active_arm.txt"
    arm_file.parent.mkdir(parents=True)
    arm_file.write_text("ORCHID\n", encoding="utf-8")
    sample_until_stopped(
        path,
        interval_seconds=0.01,
        query_fn=lambda: "200, 1000\n80, 800\n",
        arm_file=arm_file,
    )
    arm_file.write_text("PI_NATIVE\n", encoding="utf-8")
    sample_until_stopped(
        path,
        interval_seconds=0.01,
        query_fn=lambda: "150, 1000\n10, 800\n",
        arm_file=arm_file,
    )
    payload = assemble_paired_summary_from_jsonl(path)
    assert payload is not None
    assert payload["arm_isolated"] is True
    assert payload["arms"]["PI_NATIVE"]["devices"]["main_gpu"]["peak_bytes"] == 150 * 1024 * 1024
    assert payload["arms"]["ORCHID"]["devices"]["main_gpu"]["peak_bytes"] == 200 * 1024 * 1024
    assert payload["arms"]["ORCHID"]["devices"]["selector_gpu"]["peak_bytes"] == 80 * 1024 * 1024
    write_paired_summary(tmp_path / "hardware" / "paired_vram_summary.json", payload)
    summary = summarize_paired_vram(tmp_path)
    assert summary["complete"] is True


def test_jsonl_samples_assemble_without_inventing_zeros(tmp_path: Path) -> None:
    path = tmp_path / "hardware" / "vram_samples.jsonl"
    polls = sample_until_stopped(
        path,
        interval_seconds=0.01,
        query_fn=lambda: "100, 1000\n50, 800\n",
    )
    assert polls == 1
    payload = assemble_paired_summary_from_jsonl(
        path,
        main_gpu_index=0,
        selector_gpu_index=1,
        arm_isolated=False,
    )
    assert payload is not None
    assert payload["arm_isolated"] is False
    assert payload["arms"]["ORCHID"]["devices"]["selector_gpu"]["samples"] == 1
    assert assemble_paired_summary_from_jsonl(tmp_path / "missing.jsonl") is None
