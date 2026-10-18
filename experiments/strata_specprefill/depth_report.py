"""Per-depth speed and accuracy for a long-session run (the 256K goal gates).

Bins each turn by conversation depth (cumulative source tokens) and reports
per arm: wall seconds, speedup versus the native arm, and accuracy. Late and
recall probes are also split by the depth of the fact they ask about (the
first 50K tokens, the middle, the last third).

Usage: python -m experiments.strata_specprefill.depth_report RUN_DIR [--native native256k]
         [--arms lean256k summary48k] [--json OUT]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

BINS = ((0, 65536), (65536, 131072), (131072, 196608), (196608, 10**9))


def rows(run, arm):
    path = Path(run)/arm/'turns.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else None


def label(lo, hi):
    return f'{lo // 1024}K–{hi // 1024}K' if hi < 10**9 else f'{lo // 1024}K+'


def report(run, native, arms):
    base = rows(run, native)
    out = {'run': str(run), 'native': native, 'bins': [], 'arms': {}}
    data = {arm: rows(run, arm) for arm in [native, *arms]}
    total_source = max(r['conversation_tokens'] for r in base) if base else None
    for lo, hi in BINS:
        entry = {'depth': label(lo, hi)}
        for arm, turns in data.items():
            if not turns:
                continue
            sel = [r for r in turns if lo <= r['conversation_tokens'] < hi]
            if not sel:
                continue
            entry[arm] = {'turns': len(sel), 'wall_s': round(sum(r['wall_s'] for r in sel), 1),
                          'correct': f"{sum(r['correct_fields'] for r in sel)}/{sum(r['fields'] for r in sel)}"}
        if native in entry:
            for arm in arms:
                if arm in entry:
                    entry[arm]['speedup_vs_native'] = round(entry[native]['wall_s'] / entry[arm]['wall_s'], 2)
        out['bins'].append(entry)
    for arm, turns in data.items():
        if not turns:
            continue
        summary = {'turns': len(turns), 'wall_s': round(sum(r['wall_s'] for r in turns), 1),
                   'correct': f"{sum(r['correct_fields'] for r in turns)}/{sum(r['fields'] for r in turns)}"}
        for kind in ('current', 'recall', 'late'):
            sel = [r for r in turns if r['kind'] == kind]
            if sel:
                summary[kind] = f"{sum(r['correct_fields'] for r in sel)}/{sum(r['fields'] for r in sel)}"
        far = [r for r in turns if r['kind'] in ('late', 'recall') and r.get('fact_depth') is not None]
        if far and total_source:
            summary['recall_by_fact_depth'] = {
                name: f"{sum(r['correct_fields'] for r in sel)}/{len(sel)}"
                for name, sel in (
                    ('first_50K', [r for r in far if r['fact_depth'] < 50000]),
                    ('middle', [r for r in far if 50000 <= r['fact_depth'] < total_source * 2 / 3]),
                    ('last_third', [r for r in far if r['fact_depth'] >= total_source * 2 / 3]))
                if sel}
        out['arms'][arm] = summary
    if base:
        for arm in arms:
            if arm in out['arms']:
                out['arms'][arm]['speedup_vs_native'] = round(out['arms'][native]['wall_s'] / out['arms'][arm]['wall_s'], 2)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--native', default='native256k')
    parser.add_argument('--arms', nargs='+', default=['lean256k', 'summary48k'])
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    result = report(args.run, args.native, args.arms)
    print(json.dumps(result, indent=1))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
