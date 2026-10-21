"""Does a Flash-Next session save/restore reproduce the live answer exactly?

For several real-session prompts: answer a first question, then a follow-up,
three ways:
  live     the engine keeps the conversation (the no-switch path);
  restored save the session, serve another conversation, restore, ask;
  cold     serve another conversation, then ask (the prompt is read again).
Compares the follow-up's tokens. The engine runs with the gateway's 256K
settings through NativeResources (the gateway's own save/restore code).

Usage: python -m experiments.strata_specprefill.session_exactness --config CFG --student PATH
         --log ROLLOUT --out FILE [--trials 5] [--tokens 8000]
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from memory_gateway.specprefill.native import NativeResources
from memory_gateway.specprefill.spec_session import SpecLimits

from .log_cases import build_log_case


def frame(tok, role, text):
    return (tok.encode(f'<|im_start|>{role}\n', parse_special=True) + tok.encode(text)
            + tok.encode('<|im_end|>\n', parse_special=True))


def assistant(tok):
    return tok.encode('<|im_start|>assistant\n<think>\n\n</think>\n\n', parse_special=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--student', type=Path, required=True)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--trials', type=int, default=5)
    parser.add_argument('--tokens', type=int, default=8000)
    args = parser.parse_args()
    limits = SpecLimits(student_context=32768, target_context=49152)
    work = Path(tempfile.mkdtemp(prefix='orchid-exactness-'))
    resources = NativeResources(args.config, args.student, limits, work / 'engine.log', lean=True,
                                vram_reserve_mib=8192, kv='fp16')
    tok, target = resources.tok, resources.target
    case = build_log_case(tok, args.log, 31)
    texts = [t['source']['text'] for t in case['turns'] if t['source']]
    system = frame(tok, 'system', 'Answer briefly from the conversation.')
    rows = []
    try:
        for trial in range(args.trials):
            body, used = [], 0
            for text in texts[trial * 7:]:
                body.append(text)
                used += len(tok.encode(text))
                if used >= args.tokens:
                    break
            first = system + frame(tok, 'user', '\n\n'.join(body) + '\n\nWhich commands ran here? List three.')
            first += assistant(tok)
            other = system + frame(tok, 'user', '\n\n'.join(texts[-12:-9]) + '\n\nSummarize in one line.')
            other += assistant(tok)
            answer = target(first, 48)
            resources.save_target(work / f'{trial}.bin')            # The state after the first answer.
            follow = first + answer + tok.encode('<|im_end|>\n', parse_special=True)
            follow += frame(tok, 'user', 'Quote the first command you listed, exactly.') + assistant(tok)
            live = target(follow, 48)                               # The engine kept the conversation.
            target(other, 16)                                       # Another conversation takes the engine.
            restored_ok = bool(resources.restore_target(work / f'{trial}.bin'))
            restored = target(follow, 48)
            target(other, 16)
            cold = target(follow, 48)                               # Read again from the shared prefix.
            row = {'trial': trial, 'prompt_tokens': len(follow), 'restore_ok': restored_ok,
                   'restored_matches_live': restored == live, 'cold_matches_live': cold == live,
                   'live': tok.decode(live)[:80], 'restored': tok.decode(restored)[:80],
                   'cold': tok.decode(cold)[:80]}
            rows.append(row)
            print(json.dumps(row), flush=True)
    finally:
        resources.close()
    args.out.write_text(json.dumps(rows, indent=1))


if __name__ == '__main__':
    main()
