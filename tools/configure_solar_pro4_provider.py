#!/usr/bin/env python3
"""Write the sealed Pareto reference provider profile to benchmark.env."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from memory_gateway.benchmark_manifest import (  # noqa: E402
    BACKEND_CONTEXT_LIMIT_TOKENS,
    OPENROUTER_PROVIDER_BASE_URL,
    SOLAR_PRO4_MODEL,
    SOLAR_PRO4_RATE_CARD,
)


def default_key_file() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return Path(os.environ.get("ORCHID_BENCHMARK_KEY_FILE", config_home / "orchid/benchmark.env"))


def read_api_key_file(path: Path) -> str:
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise SystemExit(f"API key file is empty: {path}")
    return content.splitlines()[0].strip()


def resolve_api_key(explicit: str | None = None, *, key_file: Path | None = None) -> str:
    for candidate in (
        explicit,
        read_api_key_file(key_file) if key_file else None,
        os.environ.get("OPENROUTER_API_KEY"),
        os.environ.get("BENCHMARK_PROVIDER_API_KEY"),
    ):
        if candidate:
            validate_openrouter_api_key(candidate)
            return candidate
    raise SystemExit("Set OPENROUTER_API_KEY or pass --api-key / --api-key-file")


def validate_openrouter_api_key(api_key: str) -> None:
    if api_key.startswith("sk-or-"):
        return
    preview = api_key[:7] + "..." if len(api_key) > 7 else "(empty)"
    raise SystemExit(
        "OpenRouter API keys must start with 'sk-or-'. "
        f"The configured key looks like {preview} and will not authenticate "
        "OpenRouter chat/completions. Create a key at https://openrouter.ai/keys "
        "and rerun: OPENROUTER_API_KEY='sk-or-...' tools/start_freetoshop_corrected_all_solar.sh setup-solar-pro4"
    )


def render_env(
    *,
    api_key: str,
    base_url: str = OPENROUTER_PROVIDER_BASE_URL,
    model_id: str = SOLAR_PRO4_MODEL,
    context_tokens: int = BACKEND_CONTEXT_LIMIT_TOKENS,
) -> str:
    pricing = SOLAR_PRO4_RATE_CARD["pricing_usd_per_million_tokens"]
    lines = [
        f"export BENCHMARK_PROVIDER_API_KEY={_shell_quote(api_key)}",
        f"export BENCHMARK_PROVIDER_BASE_URL={_shell_quote(base_url)}",
        f"export BENCHMARK_MODEL_ID={_shell_quote(model_id)}",
        f"export BENCHMARK_CONTEXT_TOKENS={context_tokens}",
        f"export BENCHMARK_INPUT_USD_PER_MILLION={pricing['input']}",
        "export BENCHMARK_CACHE_READ_USD_PER_MILLION=0.0",
        "export BENCHMARK_CACHE_WRITE_USD_PER_MILLION=0.0",
        f"export BENCHMARK_OUTPUT_USD_PER_MILLION={pricing['output']}",
        "",
    ]
    return "\n".join(lines)


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def write_env(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=default_key_file(),
        help="benchmark.env destination (default: ~/.config/orchid/benchmark.env)",
    )
    parser.add_argument("--api-key", help="OpenRouter API key (default: OPENROUTER_API_KEY)")
    parser.add_argument(
        "--api-key-file",
        type=Path,
        help="File containing an OpenRouter API key on the first line",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print env exports without writing the file",
    )
    args = parser.parse_args()
    api_key = resolve_api_key(args.api_key, key_file=args.api_key_file)
    content = render_env(api_key=api_key)
    if args.dry_run:
        print(content, end="")
        return 0
    write_env(args.output, content)
    print(f"Wrote Pareto reference provider profile to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
