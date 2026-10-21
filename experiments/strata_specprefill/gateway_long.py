"""256K-context check of the lean gateway over HTTP, on the user's real Codex session.

Replays the --case log conversation through /v1/chat/completions as an agent
loop: each session chunk arrives as an assistant tool call
(`read_session_part`) plus its tool result, each probe as a user message. The
service restarts from its checkpoint halfway through, every 10th probe streams,
and one completed request is retried to check idempotent replay. Uses the 256K
configuration: Flash-Next window 48K, student window 256K, 8 GiB reserve
(plus --sparse-keys 16384 --kv fp16 for the measured setup).

Usage: python -m experiments.strata_specprefill.gateway_long --config CFG --student PATH
         --log ROLLOUT --state DIR [--seed 31] [--budget 241000]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from memory_gateway.config import RuntimeConfig
from memory_gateway.gateway import create_app
from memory_gateway.specprefill.service import GatewayOptions, SpecPrefillService

from .log_cases import build_log_case
from .run import load_frontend
from .three_way_compaction import score

TOOLS = [{'type': 'function', 'function': {
    'name': 'read_session_part', 'description': 'Read the next part of the recorded session.',
    'parameters': {'type': 'object', 'properties': {'part': {'type': 'integer'}}, 'required': ['part']}}}]


def service(args):
    options = GatewayOptions(str(args.state), str(args.config), str(args.student), engine='lean',
                             context_tokens=49152, student_context_tokens=262144, answer_tokens=64,
                             vram_reserve_mib=8192, student_sparse_keys=args.sparse_keys,
                             student_sparse_rows=args.sparse_rows, target_kv=args.kv)
    native = SpecPrefillService(options)
    app = create_app(config=RuntimeConfig.from_env(db_path=str(args.state / 'gateway.db')),
                     specprefill_service=native)
    return app, native


def post(client, messages, rid, stream=False):
    headers = {'x-memory-project': 'long', 'x-memory-thread': 't1', 'x-request-id': rid}
    body = {'model': 'orchid-flash-next-specprefill', 'messages': messages, 'max_tokens': 64,
            'tools': TOOLS, 'stream': stream}
    started = time.perf_counter()
    response = client.post('/v1/chat/completions', json=body, headers=headers)
    elapsed = time.perf_counter() - started
    if response.status_code != 200:
        raise RuntimeError(f'{response.status_code}: {response.text[:300]}')
    if stream:
        chunks = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: {')]
        text = ''.join(c['choices'][0]['delta'].get('content') or '' for c in chunks if 'choices' in c)
        return {'role': 'assistant', 'content': text}, None, elapsed
    data = response.json()
    message = data['choices'][0]['message']
    return {k: v for k, v in message.items() if v is not None or k == 'content'}, data, elapsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--student', type=Path, required=True)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=31)
    parser.add_argument('--budget', type=int, default=241000)
    parser.add_argument('--sparse-keys', type=int, default=None)
    parser.add_argument('--sparse-rows', type=int, default=None)
    parser.add_argument('--restart-at', type=int, help='0-based turn of the restart (default: half)')
    parser.add_argument('--turns', type=int, help='stop after this many turns (pilot)')
    parser.add_argument('--kv', choices=('int8', 'fp16'), default=None)
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text())
    tok, _, _ = load_frontend(Path(cfg['cwd']), Path(cfg['tokenizer']))
    case = build_log_case(tok, args.log, args.seed, budget=args.budget)
    turns = case['turns'][:args.turns] if args.turns else case['turns']
    messages = [{'role': 'system', 'content': case['system']}]
    out = args.state.parent / 'gateway-long.jsonl'
    out.write_text('')
    half = args.restart_at if args.restart_at is not None else len(case['turns']) // 2
    app, native = service(args)
    client = TestClient(app).__enter__()
    conversation = 0
    try:
        for i, turn in enumerate(turns):
            if i == half:  # Restart: the session must restore from its checkpoint.
                client.__exit__(None, None, None)
                native.close()
                del app, native, client
                app, native = service(args)
                client = TestClient(app).__enter__()
            if turn['source']:
                call = {'id': f'call_part_{i}', 'type': 'function',
                        'function': {'name': 'read_session_part', 'arguments': json.dumps({'part': i})}}
                messages.append({'role': 'assistant', 'content': None, 'tool_calls': [call]})
                messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': turn['source']['text']})
                conversation += len(tok.encode(turn['source']['text']))
            messages.append({'role': 'user', 'content': turn['question']})
            stream = i % 10 == 9
            reply, data, elapsed = post(client, messages, f'r{i}', stream=stream)
            messages.append(reply)
            text = reply.get('content') or ''
            result = score(text, turn['expected'], turn.get('score'))
            row = {'turn': i + 1, 'kind': turn['kind'], 'conversation_tokens': conversation, 'stream': stream,
                   'seconds': round(elapsed, 2), **result, 'text': text[:80], 'expected': turn['expected'],
                   'evidence_tokens': ((data or {}).get('orchid') or {}).get('evidence_tokens'),
                   'restart_turn': i == half}
            with out.open('a') as f:
                f.write(json.dumps(row) + '\n')
            print(json.dumps({k: row[k] for k in ('turn', 'kind', 'conversation_tokens', 'seconds',
                                                  'correct_fields', 'stream')}), flush=True)
        # Idempotent replay of the last completed request.
        started = time.perf_counter()
        post(client, messages[:-1], f'r{len(turns) - 1}', stream=False)
        print(json.dumps({'retry_replay_s': round(time.perf_counter() - started, 3)}), flush=True)
    finally:
        client.__exit__(None, None, None)
        native.close()


if __name__ == '__main__':
    main()
