#!/usr/bin/env python3
"""Poll nvidia-smi into a JSONL trace. Missing hardware writes nothing, never zeros."""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_gateway.vram import sample_until_stopped  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument("--stop-file", default="")
    parser.add_argument("--arm-file", default="")
    arguments = parser.parse_args()
    output = Path(arguments.output)
    stop_file = Path(arguments.stop_file) if arguments.stop_file else None
    arm_file = Path(arguments.arm_file) if arguments.arm_file else None
    running = True

    def stop(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def should_continue() -> bool:
        if not running:
            return False
        if stop_file is not None and stop_file.exists():
            return False
        return True

    sample_until_stopped(
        output,
        interval_seconds=arguments.interval_seconds,
        should_continue=should_continue,
        arm_file=arm_file,
    )


if __name__ == "__main__":
    main()
