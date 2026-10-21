"""Phase 4 live agent eval: a real coding agent fixes planted bugs through one server.

Codex CLI (``codex exec``, then ``codex exec resume --last``) works through the
tasks of live_tasks.py one after another in a single thread, so its context
grows with every task, against an OpenAI-compatible server: the SpecPrefill
gateway, or native Strata Flash-Next. After each task the harness runs that
task's test file (and every earlier task's); a task passes when its tests pass
and no test file was edited. Wall time per task is measured here; the thread's depth per
task comes from Codex's session log (input tokens of each model call).

  run     ARM --base-url URL --model NAME --tasks DIR --out DIR
  report  OUT...   tasks passed and wall time per arm, by thread depth
  calls   OUT...   seconds per model call by thread depth (agent paths differ; this doesn't)

Usage: python -m experiments.strata_specprefill.live_agent_eval run lean --base-url http://127.0.0.1:18080/v1 ...
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from .live_tasks import PYTHON, check, plant

TEST = f'{PYTHON} -m pytest {{test}} -q -p no:cacheprovider -o addopts='
FIRST = ('You will get several tasks in this repository, one at a time, in this session.\n\n')
TASK = ('The test file {test} is failing. Find the bug in the code (not in the tests; do not edit any file under '
        'tests/) and fix it. Check your fix with:\n{command}\nWhen it passes, reply with one line saying what you '
        'fixed.')
# Deep variant: before the tasks, the agent reads these files (about 150K tokens), so the
# tasks run with the thread at 128K+ tokens, where native Flash-Next is slowest.
WARMUP_FILES = ['memory_gateway/db.py', 'memory_gateway/pipeline_adapters.py', 'memory_gateway/compaction.py',
                'memory_gateway/context.py', 'memory_gateway/gateway.py', 'memory_gateway/pipeline.py',
                'memory_gateway/specprefill/service.py', 'memory_gateway/specprefill/compacting_session.py',
                'memory_gateway/specprefill/archive_search.py', 'memory_gateway/evaluation.py',
                'memory_gateway/observer.py', 'memory_gateway/config.py']  # None holds a task's bug.
WARMUP = ('Before the tasks, get to know this code base: read each of these files in full, in pieces of 300 lines '
          '(sed -n \'1,300p\' FILE, then 301,600 and so on to the end), one piece per command, without '
          'summarizing between pieces:\n{files}\nWhen you have read them all, reply with the word READY.')
BINS = ((0, 64000), (64000, 128000), (128000, 192000), (192000, 10**9))


def codex_args(base_url, model, window, compact_at=None):
    args = ['-c', f'model={model}', '-c', 'model_provider=eval',
            '-c', f"model_providers.eval={{name='eval', base_url='{base_url}', wire_api='responses'}}",
            '-c', f'model_context_window={window}', '-c', 'model_max_output_tokens=4096']
    # Codex compacts the thread early for a model it has no metadata for (at about 45K),
    # summarizing away detail. Native needs it near its 256K window; the gateway keeps the
    # whole history losslessly, so with it Codex never compacts (compact_at=None means off).
    return args + ['-c', f'model_auto_compact_token_limit={compact_at or 10**12}']


def usage(events):
    """Input tokens of the last model call and the total over the task, from codex --json events."""
    last = total = 0
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        info = event.get('usage') or (event.get('msg') or {}).get('info') or {}
        tokens = info.get('input_tokens') or (info.get('last_token_usage') or {}).get('input_tokens')
        if tokens:
            last = tokens
            total += tokens
    return last, total


def codex(command, cwd, env, timeout):
    """(return code or 'timeout', stdout, stderr). Codex runs in its own process group,
    so a timeout ends its native child too (a survivor keeps the thread locked)."""
    proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        return 'timeout', out, err


def run(arm, base_url, model, tasks_dir, out, window=262144, timeout=2400, warmup=False, compact_at=None):
    out.mkdir(parents=True, exist_ok=True)
    repo = out / 'repo'
    tasks = plant(tasks_dir, repo)
    env = {**os.environ, 'CODEX_HOME': str(out / 'codex-home')}
    Path(env['CODEX_HOME']).mkdir(exist_ok=True)
    rows_path = out / 'tasks.jsonl'
    done = [json.loads(l) for l in rows_path.read_text().splitlines()] if rows_path.exists() else []
    base = ['codex', 'exec', '--json', '--skip-git-repo-check', '--ignore-user-config', '-s', 'workspace-write',
            *codex_args(base_url, model, window, compact_at=compact_at)]
    if warmup and not (out / 'warmup.json').exists():
        started = time.perf_counter()
        rc, events, err = codex(base + [WARMUP.format(files='\n'.join(WARMUP_FILES))], repo, env, 4 * timeout)
        (out / 'warmup.jsonl').write_text(events)
        (out / 'warmup.json').write_text(json.dumps({'rc': rc, 'wall_s': round(time.perf_counter() - started, 1),
                                                     'stderr': err[-2000:]}))
    for i, task in enumerate(tasks):
        if i < len(done):
            continue
        first = i == 0 and not warmup
        prompt = (FIRST if first else '') + TASK.format(test=task['test'], command=TEST.format(test=task['test']))
        command = base + ([prompt] if first else ['resume', '--last', prompt])
        started = time.perf_counter()
        rc, events, err = codex(command, repo, env, timeout)
        if rc not in (0, 'timeout') and time.perf_counter() - started < 10:
            time.sleep(15)  # Codex refused at once (e.g. the thread still locked): try once more.
            rc, events, err = codex(command, repo, env, timeout)
        wall = time.perf_counter() - started
        (out / f'task-{i:02d}.jsonl').write_text(events)
        (out / f'task-{i:02d}.err').write_text(err)
        edited = subprocess.run(['git', 'diff', '--name-only', 'HEAD', '--', 'tests'], cwd=repo,
                                capture_output=True, text=True).stdout.split()
        results = check(repo, [t['test'] for t in tasks[:i + 1]])
        failed, passed = results[task['test']]
        last, total = usage(events)
        row = {'arm': arm, 'task': i, 'module': task['module'], 'test': task['test'], 'rc': rc,
               'wall_s': round(wall, 1), 'passed': failed == 0 and passed > 0 and not edited,
               'edited_tests': edited, 'earlier_broken': [t for t, (f, _) in results.items()
                                                         if t != task['test'] and f != 0],
               'context_tokens': last, 'input_tokens': total}
        done.append(row)
        with rows_path.open('a') as f:
            f.write(json.dumps(row) + '\n')
        print(json.dumps(row), flush=True)
    return done


def depths(path):
    """Per task, the thread's input tokens at its first and last model call (Codex's session log)."""
    out, current = [], None
    for session in sorted((path / 'codex-home' / 'sessions').rglob('*.jsonl')):
        for line in session.read_text().splitlines():
            payload = json.loads(line).get('payload') or {}
            if payload.get('type') == 'task_started':
                current = [None, None]
                out.append(current)
            elif payload.get('type') == 'token_count' and current is not None and payload.get('info'):
                tokens = payload['info']['last_token_usage']['input_tokens']
                current[0] = tokens if current[0] is None else current[0]
                current[1] = tokens
    return out


def _item_text(payload):
    """The text a conversation item adds to the model's input."""
    kind = payload.get('type')
    if kind == 'message':
        return ''.join(part.get('text', '') for part in payload.get('content') or () if isinstance(part, dict))
    if kind in ('function_call', 'custom_tool_call'):
        return payload.get('arguments') or payload.get('input') or ''
    if kind in ('function_call_output', 'custom_tool_call_output'):
        out = payload.get('output')
        return out if isinstance(out, str) else json.dumps(out)
    if kind == 'reasoning':
        return ''.join(part.get('text', '') for part in payload.get('content') or () if isinstance(part, dict))
    return ''


def calls(path):
    """Per model call: (reported input tokens, conversation characters before it, output tokens,
    seconds from its input being ready to its response), from Codex's session log. Tool run time
    is excluded. Conversation characters measure depth the same way for every server."""
    from datetime import datetime
    out, ready, chars, pending = [], None, 0, None
    for session in sorted((path / 'codex-home' / 'sessions').rglob('*.jsonl')):
        for line in session.read_text().splitlines():
            item = json.loads(line)
            payload = item.get('payload') or {}
            ts = datetime.fromisoformat(item['timestamp'].replace('Z', '+00:00')).timestamp()
            kind = payload.get('type')
            if item.get('type') == 'response_item':
                if kind in ('reasoning', 'message', 'function_call', 'custom_tool_call') and \
                        payload.get('role') != 'user' and ready is not None:
                    pending = pending or [ready, chars]
                    pending.append(ts)
                chars += len(_item_text(payload))
            if kind in ('task_started', 'function_call_output', 'custom_tool_call_output', 'task_complete'):
                if pending:
                    start, depth, *stamps = pending
                    out.append([None, depth, None, stamps[-1] - start])
                    pending = None
                ready = ts
            elif kind == 'token_count' and payload.get('info') and out and out[-1][0] is None:
                usage = payload['info']['last_token_usage']
                out[-1][0], out[-1][2] = usage['input_tokens'], usage['output_tokens']
    return [tuple(r) for r in out if r[0] is not None]


def calibrate(paths):
    """tokens = a + b * characters, least squares over calls whose reported tokens are the
    whole conversation (native servers)."""
    pts = [(c, t) for p in paths for t, c, _, _ in calls(p)]
    n = len(pts)
    mc, mt = sum(c for c, _ in pts) / n, sum(t for _, t in pts) / n
    b = sum((c - mc) * (t - mt) for c, t in pts) / sum((c - mc) ** 2 for c, _ in pts)
    return mt - b * mc, b


def call_report(outs, native=()):
    """Mean seconds per model call and per output token, by conversation depth, per arm."""
    a, b = calibrate(native or [p for p in outs if 'native' in p.name])
    table = {'calibration': {'tokens_at_0_chars': round(a), 'tokens_per_char': round(b, 4)}}
    for path in outs:
        rows = [(a + b * chars, out_tokens, secs) for _, chars, out_tokens, secs in calls(path)]
        bins = []
        for lo, hi in BINS:
            sel = [r for r in rows if lo <= r[0] < hi]
            if sel:
                bins.append({'depth': f'{lo // 1000}K-' + (f'{hi // 1000}K' if hi < 10**9 else ''),
                             'calls': len(sel), 'mean_s': round(sum(r[2] for r in sel) / len(sel), 1),
                             'output_tokens': sum(r[1] for r in sel), 'total_s': round(sum(r[2] for r in sel), 1),
                             's_per_output_token': round(sum(r[2] for r in sel) / max(1, sum(r[1] for r in sel)), 3)})
        table[path.name] = bins
    return table


def report(outs):
    out = {}
    for path in outs:
        rows = [json.loads(l) for l in (path / 'tasks.jsonl').read_text().splitlines()]
        found = depths(path)[1 if (path / 'warmup.json').exists() else 0:]
        for row, (first, last) in zip(rows, found):
            row['context_tokens'], row['context_end_tokens'] = first or 0, last or 0
        arm = rows[0]['arm'] if rows else path.name
        bins = []
        for lo, hi in BINS:
            sel = [r for r in rows if lo <= r['context_tokens'] < hi]
            if sel:
                bins.append({'depth': f'{lo // 1000}K-' + (f'{hi // 1000}K' if hi < 10**9 else ''),
                             'tasks': len(sel), 'passed': sum(r['passed'] for r in sel),
                             'wall_s': round(sum(r['wall_s'] for r in sel), 1)})
        out[arm] = {'tasks': len(rows), 'passed': sum(r['passed'] for r in rows),
                    'wall_s': round(sum(r['wall_s'] for r in rows), 1),
                    'max_context': max((r['context_tokens'] for r in rows), default=0), 'bins': bins}
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('mode', choices=('run', 'report', 'calls'))
    parser.add_argument('args', nargs='+')
    parser.add_argument('--base-url')
    parser.add_argument('--model')
    parser.add_argument('--tasks', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--timeout', type=int, default=2400)
    parser.add_argument('--warmup', action='store_true', help='deep variant: read WARMUP_FILES first')
    parser.add_argument('--window', type=int, default=262144, help="the context window Codex is told")
    parser.add_argument('--compact-at', type=int, default=None, help='Codex compaction limit (default: off)')
    args = parser.parse_args()
    if args.mode == 'run':
        run(args.args[0], args.base_url, args.model, args.tasks, args.out, timeout=args.timeout, warmup=args.warmup,
            window=args.window, compact_at=args.compact_at)
    elif args.mode == 'calls':
        print(json.dumps(call_report([Path(p) for p in args.args]), indent=1))
    else:
        print(json.dumps(report([Path(p) for p in args.args]), indent=1))


if __name__ == '__main__':
    main()
