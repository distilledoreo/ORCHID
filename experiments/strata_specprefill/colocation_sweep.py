"""How deep can the student go on the P100 next to a small-window Flash-Next?

For one configuration (target window, VRAM reserve, optional student prefill
window): start the private Strata engine, record its expert-cache size and the
latency of a fixed ~230-token cached target turn, then grow the student's
prefix cache on real code text and time a 2,304-token student turn (prefill +
lookahead + score capture) at each depth until it runs out of memory.

Usage: python -m experiments.strata_specprefill.colocation_sweep --config CFG --student PATH
         --target-context 8192 --reserve-mib 4096 [--prefill-window 8192] --output OUT.json
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from .agent_cases import build_agent_case
from .run import load_frontend

DEPTHS = (32768, 65536, 131072, 196608, 258048)
TURN = 2304


def corpus(tok, need):
    """Concatenated agent-case tool outputs until `need` tokens."""
    ids, seed = [], 50000
    while len(ids) < need:
        for turn in build_agent_case(tok, seed)['turns']:
            if turn['source']:
                ids += tok.encode(turn['source']['text'] + '\n')
        seed += 1
    return ids[:need]


def engine(cfg, context, reserve, log, kv_resident=None, prompt_cache=6, extra=()):
    from serve.server import StrataEngine, child_env
    args = [a for a in cfg['args'] if a not in ('--selected-prefill', '--selected-prefix-cache')]
    kv = context if kv_resident is None else kv_resident
    for flag, value in (('--max-context', context), ('--kv-resident', kv),
                        ('--vram-reserve-mib', reserve), ('--prompt-cache', prompt_cache),
                        ('--conversation-cache-mib', 0), ('--turn-token', 248045)):
        if flag in args:
            args[args.index(flag) + 1] = str(value)
        else:
            args += [flag, str(value)]
    extra = list(extra)
    for i in range(0, len(extra) - 1, 2):  # override or append flag/value pairs
        if extra[i] in args:
            args[args.index(extra[i]) + 1] = extra[i + 1]
        else:
            args += extra[i:i + 2]
    env = child_env(cfg)
    env.update(STRATA_ROUTE_TAIL_SKIP='0', STRATA_PREFILL_CPU_SHARE='0')
    return StrataEngine(cfg['exe'], args, cwd=cfg['cwd'], log=str(log), env=env)


def target_turns(target, tok):
    """Three cached ~230-token turns on a short conversation."""
    from memory_gateway.specprefill.compaction_adapters import NativeTarget
    native = NativeTarget(target)
    enc = lambda s, special=False: list(tok.encode(s, parse_special=special))  # noqa: E731
    ids = enc('<|im_start|>system\n', True) + enc('Answer from the excerpts.') + enc('<|im_end|>\n', True)
    out = []
    text = tok.decode(corpus(tok, 4000))
    for i in range(4):
        piece = text[i * 900:(i + 1) * 900]
        prompt = ids + enc('<|im_start|>user\n', True) + enc(piece + '\nWhat is the first constant?') + enc(
            '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n', True)
        reply = native(prompt, 12)
        ids = prompt + list(reply) + enc('\n')
        if i:
            out.append({'prompt_read': native.last['engine']['prompt_read'],
                        'prompt_ms': native.last['engine']['prompt_ms'],
                        'decode_ms': native.last['engine']['decode_ms']})
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--student', type=Path, required=True)
    parser.add_argument('--target-context', type=int, default=8192)
    parser.add_argument('--reserve-mib', type=int, default=4096)
    parser.add_argument('--prefill-window', type=int, default=None)
    parser.add_argument('--block', type=int, default=2048)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import torch

    from memory_gateway.specprefill.cached_student import CachedStudent
    from memory_gateway.specprefill.core import compatible_ids
    from memory_gateway.specprefill.student import Student
    cfg = json.loads(args.config.read_text())
    tok, _, vocab = load_frontend(Path(cfg['cwd']), Path(cfg['tokenizer']))
    log = args.output.with_suffix('.engine.log')
    result = {'target_context': args.target_context, 'reserve_mib': args.reserve_mib,
              'prefill_window': args.prefill_window, 'block': args.block, 'depths': []}
    eng = engine(cfg, args.target_context, args.reserve_mib, log)
    try:
        cache = re.findall(r'expert cache (?:\()?(\d+) (?:experts|slots), ([\d.]+) GiB', log.read_text())
        result['expert_cache'] = {'experts': int(cache[-1][0]), 'gib': float(cache[-1][1])} if cache else None
        result['target_turns'] = target_turns(eng, tok)
        print(json.dumps({k: result[k] for k in ('expert_cache', 'target_turns')}), flush=True)
        allowed = compatible_ids(vocab, json.loads((args.student / 'tokenizer.json').read_text()))
        student = CachedStudent(Student(args.student, allowed), block=args.block,
                                prefill_window=args.prefill_window)
        ids = corpus(tok, DEPTHS[-1] + TURN)
        for depth in DEPTHS:
            row = {'depth': depth}
            try:
                student.trace(ids[:depth], 1.0)  # grow the cached prefix (untimed)
                torch.cuda.reset_peak_memory_stats()
                started = time.perf_counter()
                trace = student.trace(ids[:depth + TURN], 1.0)
                row.update(turn_s=round(time.perf_counter() - started, 3),
                           reused=trace['reused_prefix_tokens'],
                           peak_mib=round(torch.cuda.max_memory_allocated() / 2**20),
                           allocated_mib=round(torch.cuda.memory_allocated() / 2**20))
            except torch.OutOfMemoryError as error:
                row['oom'] = str(error).split('\n')[0][:160]
                result['depths'].append(row)
                print(json.dumps(row), flush=True)
                break
            result['depths'].append(row)
            print(json.dumps(row), flush=True)
    finally:
        eng.close()
        args.output.write_text(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
