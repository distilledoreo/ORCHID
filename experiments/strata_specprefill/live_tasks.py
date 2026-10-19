"""Phase 4 live agent eval, tasks: bugs planted in a copy of this repository.

Each task is one line of a module changed (a flipped comparison, an off-by-one,
a swapped min/max ...) so that 1-3 tests of that module's own test file fail.
An agent then fixes the tasks one after another in one thread; a task passes
when its test file passes (and every earlier task's still does).

  find   OUT    copy HEAD (git archive) to OUT/base and search for one bug per
                module in MODULES; writes OUT/tasks.json
  plant  OUT DIR  a fresh copy of the base with every task's bug, as a git repo
  check  DIR TASK...  run the given tasks' test files in DIR, report pass/fail

Usage: python -m experiments.strata_specprefill.live_tasks find OUT
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PYTHON = str(REPO / '.venv' / 'bin' / 'python')
# (module, its test file): modules with fast, focused tests that need no GPU packages.
MODULES = [
    ('memory_gateway/scheduler.py', 'tests/test_scheduler.py'),
    ('memory_gateway/core_goal.py', 'tests/test_core_goal.py'),
    ('memory_gateway/source_projection.py', 'tests/test_source_projection.py'),
    ('memory_gateway/cold_memory.py', 'tests/test_cold_memory.py'),
    ('memory_gateway/benchmark_scorecard.py', 'tests/test_benchmark_scorecard.py'),
    ('memory_gateway/pareto_release.py', 'tests/test_pareto_release.py'),
    ('memory_gateway/frozen_trace_economics.py', 'tests/test_frozen_trace_economics.py'),
    ('memory_gateway/dense_reranker.py', 'tests/test_dense_reranker.py'),
    ('memory_gateway/specprefill/session_store.py', 'tests/test_specprefill_session_store.py'),
    ('memory_gateway/specprefill/requests.py', 'tests/test_specprefill_gateway_auto.py'),
    ('memory_gateway/specprefill/controls.py', 'tests/test_specprefill_controls.py'),
    ('memory_gateway/specprefill/spec_session.py', 'tests/test_spec_session.py'),
    ('memory_gateway/compaction.py', 'tests/test_compaction_forecast.py'),
    ('memory_gateway/model_telemetry.py', 'tests/test_model_telemetry.py'),
    ('memory_gateway/core_goal_status.py', 'tests/test_core_goal_status.py'),
    ('memory_gateway/canonicalizer_gate.py', 'tests/test_canonicalizer_gate.py'),
]
# Mutation operators: (pattern, replacement), applied to one occurrence in one line.
OPERATORS = [(r' <= ', ' < '), (r' >= ', ' > '), (r' < ', ' <= '), (r' > ', ' >= '), (r' == ', ' != '),
             (r' != ', ' == '), (r' \+ 1\b', ' - 1'), (r' - 1\b', ' + 1'), (r'\bmin\(', 'max('),
             (r'\bmax\(', 'min('), (r' and ', ' or '), (r' or ', ' and '), (r'\bTrue\b', 'False'),
             (r'\bFalse\b', 'True'), (r'\[1:\]', '[2:]'), (r'\[:-1\]', '[:-2]'), (r'\bnot ', '')]


def run_tests(root, test, timeout=300):
    """(failed test count, passed count) for one test file; failed=-1 on a collection error."""
    proc = subprocess.run([PYTHON, '-m', 'pytest', test, '-q', '-p', 'no:cacheprovider', '-o', 'addopts=',
                           '--maxfail=50'], cwd=root, capture_output=True, text=True,
                          timeout=timeout)
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ''
    failed = int(m.group(1)) if (m := re.search(r'(\d+) failed', tail)) else 0
    passed = int(m.group(1)) if (m := re.search(r'(\d+) passed', tail)) else 0
    if 'error' in tail and not failed:
        return -1, passed
    return failed, passed


def copy_head(out):
    out.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(['git', 'archive', 'HEAD'], cwd=REPO, capture_output=True, check=True).stdout
    subprocess.run(['tar', '-x', '-C', str(out)], input=archive, check=True)


def candidates(text, rng):
    lines = text.splitlines(keepends=True)
    out = []
    for n, line in enumerate(lines):
        code = line.split('#', 1)[0]
        if not code.strip() or code.lstrip().startswith(('"""', "'", '"', 'import ', 'from ', '@', 'def ', 'class ')):
            continue
        for pattern, replacement in OPERATORS:
            for match in re.finditer(pattern, code):
                out.append((n, match.start(), match.end(), replacement))
    rng.shuffle(out)
    return lines, out


def find(out, seed=20261019, tries=40):
    base = out / 'base'
    if not base.exists():
        copy_head(base)
    rng = random.Random(seed)
    tasks = []
    for module, test in MODULES:
        path = base / module
        if not path.exists() or not (base / test).exists():
            continue
        clean_failed, clean_passed = run_tests(base, test)
        if clean_failed or not clean_passed:
            print(f'skip {module}: its tests do not pass cleanly', file=sys.stderr)
            continue
        original = path.read_text()
        lines, options = candidates(original, rng)
        found = None
        for n, a, b, replacement in options[:tries]:
            line = lines[n]
            mutated = line[:a] + replacement + line[b:]
            path.write_text(''.join(lines[:n] + [mutated] + lines[n + 1:]))
            try:
                failed, _ = run_tests(base, test)
            finally:
                path.write_text(original)
            if 1 <= failed <= 3:
                found = {'module': module, 'test': test, 'line': n + 1, 'before': line, 'after': mutated,
                         'failed_tests': failed}
                break
        if found:
            tasks.append(found)
            print(f'{module}:{found["line"]}: {line.strip()!r} -> {mutated.strip()!r} ({failed} failing)')
        else:
            print(f'no bug found for {module}', file=sys.stderr)
    (out / 'tasks.json').write_text(json.dumps(tasks, indent=1))
    return tasks


def plant(out, directory):
    tasks = json.loads((out / 'tasks.json').read_text())
    if directory.exists():
        raise FileExistsError(directory)
    shutil.copytree(out / 'base', directory)
    for task in tasks:
        path = directory / task['module']
        lines = path.read_text().splitlines(keepends=True)
        if lines[task['line'] - 1] != task['before']:
            raise ValueError(f'{task["module"]} changed')
        lines[task['line'] - 1] = task['after']
        path.write_text(''.join(lines))
    for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=eval', '-c', 'user.email=eval@localhost',
                                                  'commit', '-qm', 'Task baseline']):
        subprocess.run(['git', *args], cwd=directory, check=True)
    return tasks


def check(directory, tests):
    return {test: run_tests(directory, test) for test in tests}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('mode', choices=('find', 'plant', 'check'))
    parser.add_argument('out', type=Path)
    parser.add_argument('rest', nargs='*')
    args = parser.parse_args()
    if args.mode == 'find':
        find(args.out)
    elif args.mode == 'plant':
        plant(args.out, Path(args.rest[0]))
    else:
        print(json.dumps(check(args.out, args.rest), indent=1))


if __name__ == '__main__':
    main()
