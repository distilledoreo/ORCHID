"""Phase 6 event-count soak: real agent sessions replayed through the running gateway.

Each Codex rollout becomes one thread (every other one replayed as plain chat, which the
gateway serves lean, the rest as agent requests with tools, which it serves natively): its user messages, assistant messages, tool
calls and tool outputs, replayed as growing chat requests (tools declared) at every
point the agent answered. Threads are run in blocks so the gateway switches between
them, the gateway is restarted cleanly and killed hard (``kill -9`` mid-turn) on a
schedule, and every request that failed because of a kill is retried until it is
answered. Every turn records its latency and the GPU, RAM and disk in use.

At the end every thread gets one more turn on top of its whole history: each must
continue its own stored thread (no conflict, no new thread), which holds only when
every earlier turn's record survived the restarts and kills: zero data loss. Errors
are any failed request other than those a kill caused (retried until answered).

    python -m experiments.strata_specprefill.soak OUT --start CMD --stop CMD --pid-file PATH ...
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import random
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .agent_eval_cases import events

TOOLS = [{'type': 'function', 'function': {'name': 'exec_command', 'description': 'Run a shell command.',
          'parameters': {'type': 'object', 'properties': {'cmd': {'type': 'string'}}, 'required': ['cmd']}}}]
SYSTEM = 'You are a coding agent working in a repository. Use the exec_command tool to inspect and change files.'


def thread_requests(path, limit):
    """[messages] at each point the recorded agent answered (after a user message or a tool output)."""
    messages, out, call = [{'role': 'system', 'content': SYSTEM}], [], 0
    for kind, text, _ in events(path):
        if kind == 'user':
            messages.append({'role': 'user', 'content': text[:8000]})
            out.append(list(messages))
        elif kind == 'assistant':
            messages.append({'role': 'assistant', 'content': text[:4000]})
        elif kind == 'call':
            call += 1
            messages.append({'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': f'call_{call}', 'type': 'function',
                 'function': {'name': 'exec_command', 'arguments': json.dumps({'cmd': text[:2000]})}}]})
        elif kind == 'output' and call and messages[-1].get('tool_calls'):
            messages.append({'role': 'tool', 'tool_call_id': f'call_{call}', 'content': text[:12000]})
            out.append(list(messages))
        if len(out) >= limit:
            break
    # A rollout that starts mid-session (resumed) has tool steps before any user message; the chat
    # template (Flash-Next's own too) needs a user query, so a thread starts at its first one.
    return [m for m in out if m[-1]['role'] in ('user', 'tool') and any(x['role'] == 'user' for x in m)]


def as_chat(requests):
    """The same thread as a plain chat (no tools declared, so the gateway serves it lean): each
    command becomes the assistant saying what it ran, each output a user message carrying it."""
    out = []
    for messages in requests:
        chat = []
        for m in messages:
            if m.get('tool_calls'):
                cmd = json.loads(m['tool_calls'][0]['function']['arguments'])['cmd']
                chat.append({'role': 'assistant', 'content': f'I ran:\n{cmd}'})
            elif m['role'] == 'tool':
                chat.append({'role': 'user', 'content': f'Its output:\n{m["content"]}'})
            else:
                chat.append(dict(m))
        out.append(chat)
    return out


def gauges(state_dir, pid_file):
    def run(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:
            return ''
    vram = run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits'])
    rss = 0
    try:
        pid = int(Path(pid_file).read_text().strip())
        for p in [pid] + [int(c) for c in run(['pgrep', '-P', str(pid)]).split()]:
            for line in Path(f'/proc/{p}/status').read_text().splitlines():
                if line.startswith('VmRSS:'):
                    rss += int(line.split()[1])
    except Exception:
        pass
    disk = int(run(['du', '-sk', str(state_dir)]).split()[0] or 0) if Path(state_dir).exists() else 0
    mem = {k: int(v.split()[0]) for k, v in (l.split(':', 1) for l in Path('/proc/meminfo').read_text().splitlines())
           if k in ('MemAvailable', 'SwapFree')}
    return {'vram_mib': int(vram or 0), 'rss_mib': rss // 1024, 'disk_mib': disk // 1024,
            'ram_available_mib': mem.get('MemAvailable', 0) // 1024, 'swap_free_mib': mem.get('SwapFree', 0) // 1024}


def post(url, body, timeout=1800):
    req = urllib.request.Request(url + '/chat/completions', data=json.dumps(body).encode(),
                                 headers={'content-type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode()
    if body.get('stream'):
        chunks = [json.loads(l[6:]) for l in raw.splitlines() if l.startswith('data: ') and l != 'data: [DONE]']
        return {'stream': True, 'chunks': len(chunks), 'error': next((c for c in chunks if 'error' in c), None)}
    return json.loads(raw)


def wait_ready(url, timeout=1200):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url.rsplit('/v1', 1)[0] + '/readyz', timeout=10) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(10)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('out', type=Path)
    parser.add_argument('--logs', type=Path, nargs='+', required=True, help='Codex rollout files, one per thread')
    parser.add_argument('--url', default='http://127.0.0.1:18080/v1')
    parser.add_argument('--model', default='orchid-flash-next-specprefill')
    parser.add_argument('--start', required=True, help='command that starts the gateway')
    parser.add_argument('--stop', required=True, help='command that stops it cleanly')
    parser.add_argument('--pid-file', required=True, help='file the start command writes the gateway pid to')
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--turns', type=int, default=1500)
    parser.add_argument('--block', type=int, default=14, help='turns per thread before switching')
    parser.add_argument('--restarts', type=int, default=10)
    parser.add_argument('--kills', type=int, default=2)
    parser.add_argument('--max-tokens', type=int, default=48)
    parser.add_argument('--chat-every', type=int, default=2,
                        help='every Nth thread is replayed as plain chat (lean); 0: all agents')
    parser.add_argument('--seed', type=int, default=20261019)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    per = args.turns // len(args.logs) + 2
    threads = [thread_requests(p, per) for p in args.logs]
    threads = [t for t in threads if t]
    chat = {i for i in range(len(threads)) if args.chat_every and i % args.chat_every == 1}
    threads = [as_chat(t) if i in chat else t for i, t in enumerate(threads)]
    cursor = [0] * len(threads)
    total = min(args.turns, sum(len(t) for t in threads))
    restart_at = set(rng.sample(range(50, total - 10), args.restarts))
    kill_at = set(rng.sample(sorted(set(range(60, total - 10)) - restart_at), args.kills))
    rows_path = args.out / 'turns.jsonl'
    rows = [json.loads(l) for l in rows_path.read_text().splitlines()] if rows_path.exists() else []
    for row in rows:
        cursor[row['thread']] = max(cursor[row['thread']], row['index'] + 1)
    done, errors, switches, last = len(rows), sum(1 for r in rows if r['error']), 0, None
    if not wait_ready(args.url):
        raise SystemExit('the gateway did not become ready')
    while done < total:
        live = [i for i, t in enumerate(threads) if cursor[i] < len(t)]
        if not live:
            break
        thread = rng.choice([i for i in live if i != last] or live)
        switches += last is not None and thread != last
        last = thread
        for _ in range(args.block):
            if done >= total or cursor[thread] >= len(threads[thread]):
                break
            index = cursor[thread]
            body = {'model': args.model, 'messages': threads[thread][index],
                    'max_tokens': args.max_tokens, 'temperature': 0, 'stream': bool(index % 3 == 0)}
            if thread not in chat:
                body['tools'] = TOOLS
            event = 'kill' if done in kill_at else 'restart' if done in restart_at else None
            if event == 'kill':  # kill -9 while this turn is generating, then retry it after a restart
                pid = int(Path(args.pid_file).read_text().strip())
                killer = subprocess.Popen(['bash', '-c', f'sleep 4; kill -9 {pid}; pkill -9 -P {pid} || true'])
            started, tries, failure = time.perf_counter(), 0, None
            while True:
                tries += 1
                try:
                    result = post(args.url, body)
                    failure = (result.get('error') if result.get('stream') else result.get('error'))
                    break
                except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError,
                        json.JSONDecodeError) as error:  # A kill can cut a streamed reply part way (IncompleteRead).
                    if isinstance(error, urllib.error.HTTPError):  # An answer: the gateway refused the request.
                        failure = repr(error)
                        break
                    if event != 'kill' or tries > 5:
                        failure = repr(error)
                        break
                    killer.wait()
                    subprocess.run(args.start, shell=True, check=True)
                    wait_ready(args.url)
            wall = time.perf_counter() - started
            if failure:
                errors += 1
            orchid = result.get('orchid', {}) if not result.get('stream') else {}
            row = {'turn': done, 'thread': thread, 'chat': thread in chat, 'index': index, 'event': event, 'tries': tries,
                   'wall_s': round(wall, 2), 'error': failure, 'engine': orchid.get('engine'),
                   'reason': orchid.get('reason'), 'stream': body['stream'], **gauges(args.state_dir, args.pid_file)}
            with rows_path.open('a') as f:
                f.write(json.dumps(row) + '\n')
            print(json.dumps(row), flush=True)
            cursor[thread] += 1
            done += 1
            if event == 'restart':
                subprocess.run(args.stop, shell=True, check=True)
                subprocess.run(args.start, shell=True, check=True)
                if not wait_ready(args.url):
                    raise SystemExit('the gateway did not come back after a restart')
    # Data loss check: one more turn per thread, on its whole recorded history.
    keys, continued = [], 0
    for i, t in enumerate(threads):
        last_messages = t[min(cursor[i], len(t)) - 1] if cursor[i] else None
        if last_messages is None:
            continue
        body = {'model': args.model, 'max_tokens': 16, 'temperature': 0,
                'messages': last_messages + [{'role': 'assistant', 'content': 'Noted.'},
                                             {'role': 'user', 'content': 'Reply with the word OK.'}]}
        if i not in chat:
            body['tools'] = TOOLS
        try:
            result = post(args.url, body)
            keys.append((result.get('orchid') or {}).get('thread'))
            continued += 1
        except Exception as error:
            errors += 1
            print('final turn failed', i, repr(error), flush=True)
    with urllib.request.urlopen(args.url.rsplit('/v1', 1)[0] + '/v1/orchid/threads', timeout=60) as r:
        stored = json.load(r)['data']
    logged = [json.loads(l) for l in rows_path.read_text().splitlines()]
    switches = sum(a['thread'] != b['thread'] for a, b in zip(logged, logged[1:]))  # Across resumes too.
    summary = {'turns': done, 'threads': len(threads), 'switches': switches, 'restarts': args.restarts,
               'kills': args.kills, 'errors': errors, 'final_turns_answered': continued,
               'final_distinct_threads': len(set(keys)), 'stored_threads': len(stored),
               'max_vram_mib': max((r['vram_mib'] for r in map(json.loads, rows_path.read_text().splitlines())),
                                   default=0)}
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
