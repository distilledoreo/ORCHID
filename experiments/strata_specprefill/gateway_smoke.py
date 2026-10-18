"""Live end-to-end check of the lean SpecPrefill gateway route on the real models.

Drives /v1/chat/completions through the FastAPI app with the lean engine:
a long agent-style tool output plus question, follow-up recall of an omitted
detail, a service restart from checkpoint mid-conversation, a streamed turn,
and a tool-call round trip. Prints per-turn correctness, latency and the
target tokens the turn cost.

Usage: python -m experiments.strata_specprefill.gateway_smoke --config CFG --student PATH --state DIR --seed N
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from fastapi.testclient import TestClient

from memory_gateway.config import RuntimeConfig
from memory_gateway.gateway import create_app
from memory_gateway.specprefill.service import GatewayOptions, SpecPrefillService

from .agent_cases import build_agent_case
from .run import load_frontend

TOOLS = [{'type': 'function', 'function': {
    'name': 'read_file', 'description': 'Read a repository file.',
    'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}}}]


def recall_question(turn):
    """An un-asked constant and function return from a file the session ingested."""
    path, text = turn['source']['path'], turn['source']['text']
    asked = set(re.findall(r'[A-Z][A-Z_]+|\w+(?=\(\))', turn['question']))
    const = next((n, v) for n, v in re.findall(r'^([A-Z][A-Z_]+) = (\d+)$', text, re.M) if n not in asked)
    fn, ret = next((n, v) for n, v in re.findall(r'^def (\w+)\(.*?\n(?:    .*\n)*?    return (\d+)$', text, re.M)
                   if n not in asked)
    return {'question': f'Return the value of {const[0]} in {path}, then the integer returned by {fn}() in {path}.',
            'expected': [int(const[1]), int(ret)], 'kind': 'late'}


def app(args):
    options = GatewayOptions(str(args.state), str(args.config), str(args.student), engine='lean',
                             context_tokens=8192, answer_tokens=128)
    service = SpecPrefillService(options)
    return create_app(config=RuntimeConfig.from_env(db_path=str(args.state / 'gateway.db')),
                      specprefill_service=service), service


def post(client, messages, rid, **extra):
    headers = {'x-memory-project': 'smoke', 'x-memory-thread': 't1', 'x-request-id': rid}
    started = time.perf_counter()
    response = client.post('/v1/chat/completions', json={'model': 'orchid-flash-next-specprefill',
                           'messages': messages, 'max_tokens': 96, **extra}, headers=headers)
    elapsed = time.perf_counter() - started
    if extra.get('stream'):
        chunks = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: {')]
        text = ''.join(c['choices'][0]['delta'].get('content') or '' for c in chunks if 'choices' in c)
        return {'role': 'assistant', 'content': text}, None, elapsed, response.status_code
    body = response.json()
    if response.status_code != 200:
        raise RuntimeError(body)
    return body['choices'][0]['message'], body, elapsed, 200


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--student', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=4242)
    parser.add_argument('--tools-only', action='store_true', help='run only the tool-loop step')
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text())
    tok, _, _ = load_frontend(Path(cfg['cwd']), Path(cfg['tokenizer']))
    case = build_agent_case(tok, args.seed)
    system = {'role': 'system', 'content': case['system']}
    source_turns = [t for t in case['turns'] if t['source']]
    late = [recall_question(source_turns[0]), recall_question(source_turns[2])]
    results, messages = [], [system]

    def record(name, turn, message, body, elapsed):
        text = message.get('content') or ''
        fields = [f.strip() for f in text.replace('\n', ' ').split(',')]
        ok = [i < len(fields) and fields[i] == str(v) for i, v in enumerate(turn['expected'])]
        orchid = (body or {}).get('orchid', {})
        results.append({'step': name, 'correct': f'{sum(ok)}/{len(ok)}', 'seconds': round(elapsed, 2),
                        'new_tokens': orchid.get('new_tokens'), 'evidence_tokens': orchid.get('evidence_tokens'),
                        'answer': text[:60]})
        print(json.dumps(results[-1]), flush=True)

    if not args.tools_only:
        conversation(args, source_turns, late, messages, record, results)
    tool_loop(args, case, source_turns, system, record, results)
    (args.state.parent / 'smoke-results.json').write_text(json.dumps(results, indent=2))


def conversation(args, source_turns, late, messages, record, results):
    application, service = app(args)
    with TestClient(application) as client:
        for i, turn in enumerate(source_turns[:4]):
            messages.append({'role': 'user', 'content': turn['source']['text'] + '\n\n' + turn['question']})
            message, body, elapsed, _ = post(client, messages, f'r{i}')
            messages.append(message)
            record(f'source-{i}', turn, message, body, elapsed)
    service.close()
    application, service = app(args)  # Restart: sessions restore from checkpoints.
    with TestClient(application) as client:
        messages.append({'role': 'user', 'content': late[0]['question']})
        message, body, elapsed, _ = post(client, messages, 'late0')
        messages.append(message)
        record('late-after-restart', late[0], message, body, elapsed)
        messages.append({'role': 'user', 'content': late[1]['question']})
        message, _, elapsed, status = post(client, messages, 'late1', stream=True)
        messages.append(message)
        record('late-streamed', late[1], message, None, elapsed)
        # Idempotent retry returns the stored completion without inference.
        _, body, elapsed, _ = post(client, messages[:-1], 'late1')
        results.append({'step': 'retry-replay', 'seconds': round(elapsed, 2)})
        print(json.dumps(results[-1]), flush=True)
    service.close()


def tool_loop(args, case, source_turns, system, record, results):
    # Tool round trip on a fresh thread state directory.
    args.state = args.state.with_name(args.state.name + '-tools')
    application, service = app(args)
    with TestClient(application) as client:
        turn = source_turns[4]
        path = turn['source']['path']
        tmsgs = [system, {'role': 'user', 'content': f'Use read_file to open {path}. {turn["question"]}'}]
        message, body, elapsed, _ = post(client, tmsgs, 't0', tools=TOOLS)
        calls = message.get('tool_calls') or []
        results.append({'step': 'tool-call', 'called': [c['function']['name'] for c in calls],
                        'seconds': round(elapsed, 2), 'finish': body['choices'][0]['finish_reason']})
        print(json.dumps(results[-1]), flush=True)
        # A real agent loop: serve read_file for any case file, up to four calls.
        files = {}
        for t in case['turns']:
            if t['source']:
                files[t['source']['path']] = t['source']['text']
        served = 0
        while calls and served < 4:
            tmsgs.append(message)
            for call in calls:
                path_arg = json.loads(call['function']['arguments']).get('path', '')
                tmsgs.append({'role': 'tool', 'tool_call_id': call['id'],
                              'content': files.get(path_arg, f'error: {path_arg} not found')})
                served += 1
            message, body, elapsed, _ = post(client, tmsgs, f't{served}', tools=TOOLS)
            calls = message.get('tool_calls') or []
            results.append({'step': f'tool-turn-{served}', 'seconds': round(elapsed, 2),
                            'finish': body['choices'][0]['finish_reason'],
                            'called': [json.loads(c['function']['arguments']).get('path') for c in calls]})
            print(json.dumps(results[-1]), flush=True)
        record('tool-loop-answer', turn, message, body, elapsed)
    service.close()


if __name__ == '__main__':
    main()
