"""Is native's long-context failure the model or the prompt cache?

Rebuilds a native arm's exact prompt at chosen turns (case sources and
questions plus that arm's recorded replies) and sends each as one cold request
to a fresh engine with the prompt cache disabled. Correct answers here, where
the cached run returned nothing, point at the cache path rather than the model.

Usage: python -m experiments.strata_specprefill.cold_check RUN_DIR --arm native256k --turns 61 70
         --config CFG [--context 262144]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .colocation_sweep import engine
from .run import load_frontend
from .three_way_compaction import PlainSession, score


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--arm', default='native256k')
    parser.add_argument('--turns', type=int, nargs='+', required=True, help='1-based turn numbers')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--context', type=int, default=262144)
    parser.add_argument('--prompt-cache', type=int, default=0)
    parser.add_argument('--flags', default='', help='engine flag/value pairs, e.g. "--kv fp16"')
    parser.add_argument('--tag', default='')
    parser.add_argument('--oracle-replies', action='store_true',
                        help='history replies are the correct answers, not the arm\'s recorded ones')
    args = parser.parse_args()
    from memory_gateway.specprefill.compaction_adapters import NativeTarget
    cfg = json.loads(args.config.read_text())
    tok, _, _ = load_frontend(Path(cfg['cwd']), Path(cfg['tokenizer']))
    case = json.loads((args.run/'case.json').read_text())
    recorded = [json.loads(line) for line in (args.run/args.arm/'turns.jsonl').read_text().splitlines()]
    plain = PlainSession(tok, None, args.context, case['system'], compacting=False)
    prompts = {}
    for i, turn in enumerate(case['turns'][:max(args.turns)]):
        incoming = plain.frame('user', turn['source']['text']) if turn['source'] else []
        prompt = plain.ids + incoming + plain.query(turn['question'])
        if i + 1 in args.turns:
            prompts[i + 1] = prompt
        said = str(turn['expected'][0]) if args.oracle_replies else recorded[i]['text']
        reply = plain.enc(said, False) + plain.enc('<|im_end|>', True) + plain.enc('\n')
        plain.ids = prompt + reply
    log = args.run/f'cold-check-{args.arm}{args.tag}.engine.log'
    eng = engine(cfg, args.context, 700, log, prompt_cache=args.prompt_cache, extra=args.flags.split())
    try:
        target = NativeTarget(eng)
        for number, prompt in prompts.items():
            turn = case['turns'][number - 1]
            output = target(prompt, 64)
            text = tok.decode(output)
            result = score(text, turn['expected'], turn.get('score'))
            print(json.dumps({'turn': number, 'prompt_tokens': len(prompt), 'cold_text': text[:120],
                              'cached_text': recorded[number - 1]['text'][:120], **result,
                              'prompt_read': target.last['engine']['prompt_read']}), flush=True)
    finally:
        eng.close()


if __name__ == '__main__':
    main()
