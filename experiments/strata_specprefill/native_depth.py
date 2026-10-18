"""Native cached Flash-Next turn latency at long depth (the 256K baseline).

Grows one cached conversation to each depth checkpoint (bulk turns, untimed),
then times a 2,304-token tool-output turn plus a short question on top of the
cached prefix: prefill (prompt_ms), decode and wall time. Same engine flags as
the benchmarks except context capacity and K/V residency.

Usage: python -m experiments.strata_specprefill.native_depth --config CFG --context 262144
         [--kv-resident N] [--reserve-mib 700] --output OUT.json
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from .colocation_sweep import corpus, engine
from .run import load_frontend

DEPTHS = (32768, 65536, 131072, 196608, 253952)
TURN = 2304


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--context', type=int, default=262144)
    parser.add_argument('--kv-resident', type=int, default=None)
    parser.add_argument('--reserve-mib', type=int, default=700)
    parser.add_argument('--bulk', type=int, default=16384, help='tokens per untimed growth turn')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from memory_gateway.specprefill.compaction_adapters import NativeTarget
    cfg = json.loads(args.config.read_text())
    if args.kv_resident is not None:
        cfg = {**cfg, 'args': [a for a in cfg['args']]}
        if '--kv-resident' in cfg['args']:
            i = cfg['args'].index('--kv-resident')
            del cfg['args'][i:i + 2]
    tok, _, _ = load_frontend(Path(cfg['cwd']), Path(cfg['tokenizer']))
    log = args.output.with_suffix('.engine.log')
    kv = args.kv_resident if args.kv_resident is not None else args.context
    result = {'context': args.context, 'kv_resident': kv, 'reserve_mib': args.reserve_mib, 'depths': []}
    eng = engine(cfg, args.context, args.reserve_mib, log, kv_resident=kv)
    enc = lambda s, special=False: list(tok.encode(s, parse_special=special))  # noqa: E731
    try:
        cache = re.findall(r'expert cache (?:\()?(\d+) (?:experts|slots), ([\d.]+) GiB', log.read_text())
        result['expert_cache_gib'] = float(cache[-1][1]) if cache else None
        target = NativeTarget(eng)
        body = corpus(tok, DEPTHS[-1] + TURN + 4096)
        ids = enc('<|im_start|>system\n', True) + enc('Answer from the tool outputs.') + enc('<|im_end|>\n', True)
        used = 0

        def turn(n, question, maximum):
            nonlocal ids, used
            piece = body[used:used + n]
            used += n
            prompt = (ids + enc('<|im_start|>user\n', True) + piece + enc('\n' + question)
                      + enc('<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n', True))
            started = time.perf_counter()
            reply = target(prompt, maximum)
            wall = time.perf_counter() - started
            ids = prompt + list(reply) + enc('\n')
            return wall, target.last['engine']

        for depth in DEPTHS:
            while len(ids) + args.bulk < depth:
                turn(args.bulk, 'Reply OK.', 2)
            if len(ids) < depth - 64:
                turn(depth - len(ids) - 64, 'Reply OK.', 2)
            wall, info = turn(TURN, 'What is the first constant defined above? Reply with the integer only.', 12)
            row = {'depth': len(ids), 'wall_s': round(wall, 2), 'prompt_read': info['prompt_read'],
                   'prompt_ms': info['prompt_ms'], 'decode_ms': info['decode_ms'], 'reused': info['reused']}
            result['depths'].append(row)
            print(json.dumps(row), flush=True)
    except Exception as error:  # Record how far native got (OOM, capacity).
        result['error'] = f'{type(error).__name__}: {error}'[:300]
        print(json.dumps({'error': result['error']}), flush=True)
    finally:
        eng.close()
        args.output.write_text(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
