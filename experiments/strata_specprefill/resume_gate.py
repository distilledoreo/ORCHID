"""Phase 2 gate: thread switches and restarts on two ~250K-token threads, over HTTP.

Replays the user's real Codex session (log_cases, as gateway_long.py does) into a
running gateway with no ORCHID headers: thread A, then thread B (the same
session under a different system prompt, so a separate thread). The last few
turns of each then alternate A, B, A, B..., so every one is a thread switch.

  build    replay a thread's first turns (all but the last --tail)
  switch   alternate the last --tail turns of A and B, except the final one
  probe    the final turn on one thread (after a restart: the first answer)
  alone    the last --tail turns of A only (the no-switch reference)
  extra    one new question (--question) on a thread that has answered every turn
  pingpong --rounds more alternating A, B questions after every turn (switch timing)

Each turn is written to --out as JSON lines (thread, turn, seconds, answer).

Usage: python -m experiments.strata_specprefill.resume_gate MODE --config CFG --log ROLLOUT
         --out FILE [--url http://127.0.0.1:18080] [--tail 6] [--thread A|B]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

from .log_cases import build_log_case
from .run import load_frontend

MODEL = 'orchid-flash-next-specprefill'
TOOLS = [{'type': 'function', 'function': {
    'name': 'read_session_part', 'description': 'Read the next part of the recorded session.',
    'parameters': {'type': 'object', 'properties': {'part': {'type': 'integer'}}, 'required': ['part']}}}]


def history(case, name, turns, answers):
    """Messages for `turns` of thread `name`, with the recorded answers so far."""
    messages = [{'role': 'system', 'content': f'{case["system"]}\n\n(Session {name}.)'}]
    for i, turn in enumerate(turns):
        if turn['source']:
            call = {'id': f'call_part_{i}', 'type': 'function',
                    'function': {'name': 'read_session_part', 'arguments': json.dumps({'part': i})}}
            messages += [{'role': 'assistant', 'content': None, 'tool_calls': [call]},
                         {'role': 'tool', 'tool_call_id': call['id'], 'content': turn['source']['text']}]
        messages.append({'role': 'user', 'content': turn['question']})
        if i < len(answers):
            messages.append({'role': 'assistant', 'content': answers[i]})
    return messages


def ask(client, url, messages):
    started = time.perf_counter()
    response = client.post(url + '/v1/chat/completions',
                           json={'model': MODEL, 'messages': messages, 'max_tokens': 64, 'tools': TOOLS})
    elapsed = time.perf_counter() - started
    if response.status_code != 200:
        raise RuntimeError(f'{response.status_code}: {response.text[:300]}')
    data = response.json()
    return data['choices'][0]['message'].get('content') or '', elapsed, data.get('orchid') or {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('build', 'switch', 'probe', 'alone', 'extra', 'pingpong'))
    parser.add_argument('--rounds', type=int, default=4)
    parser.add_argument('--question', default='Reply with the word OK.')
    parser.add_argument('--expected', default='OK')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--url', default='http://127.0.0.1:18080')
    parser.add_argument('--tail', type=int, default=6)
    parser.add_argument('--thread', default='A')
    parser.add_argument('--seed', type=int, default=31)
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text())
    tok, _, _ = load_frontend(Path(cfg['cwd']), Path(cfg['tokenizer']))
    case = build_log_case(tok, args.log, args.seed)
    turns = case['turns']
    head = turns[:-args.tail]
    answers_file = args.out.with_suffix('.answers.json')
    answers = json.loads(answers_file.read_text()) if answers_file.exists() else {}
    client = httpx.Client(timeout=1800)

    def turn(name, index):
        known = answers.setdefault(name, [])
        reply, seconds, orchid = ask(client, args.url, history(case, name, turns[:index + 1], known))
        known.append(reply)
        answers_file.write_text(json.dumps(answers))
        row = {'mode': args.mode, 'thread': name, 'turn': index + 1, 'seconds': round(seconds, 2),
               'engine': orchid.get('engine'), 'evidence_tokens': orchid.get('evidence_tokens'),
               'resume': orchid.get('resume'),
               'answer': reply[:120], 'expected': turns[index]['expected'][0][:120]}
        with args.out.open('a') as f:
            f.write(json.dumps(row) + '\n')
        print(json.dumps(row), flush=True)

    if args.mode == 'build':
        for i in range(len(answers.get(args.thread, [])), len(head)):
            turn(args.thread, i)
    elif args.mode == 'switch':
        for i in range(len(head), len(turns) - 1):
            for name in ('A', 'B'):
                if len(answers.get(name, [])) <= i:  # Resumable: skip turns already answered.
                    turn(name, i)
    elif args.mode == 'alone':
        for i in range(len(head), len(turns)):
            turn('A', i)
    elif args.mode == 'probe':
        turn(args.thread, len(turns) - 1)
    elif args.mode == 'extra':
        extras = answers.setdefault('extras', {'A': [], 'B': []})
        extras[args.thread].append(args.question)
        turns.extend({'source': None, 'question': q, 'expected': [args.expected]} for q in extras[args.thread])
        turn(args.thread, len(turns) - 1)
    else:
        # Each thread's own extra questions follow the case turns in its history.
        extras = answers.setdefault('extras', {'A': [], 'B': []})
        base = list(turns)
        for r in range(args.rounds):
            for name in ('A', 'B'):
                question = f'Ping {len(extras[name]) + 1}: reply with the word OK.'
                extras[name].append(question)
                turns[:] = base + [{'source': None, 'question': q, 'expected': ['OK']} for q in extras[name]]
                turn(name, len(turns) - 1)


if __name__ == '__main__':
    main()
