"""Goal-gate table for matched multi-turn runs (docs/specprefill-goal.md).

Per run directory (one case/seed), compares:
  native32k  native cached Flash-Next alone (700 MiB reserve; production baseline)
  full32k    native cached, 4096 MiB reserve (co-located reference)
  summary8k  ordinary compaction at an 8K window
  lean8k     SpecPrefill, 8K target window, student co-located on the P100
  lean8ksep  SpecPrefill with the student off the target GPU (CPU here); the
             projection adds the co-located run's measured GPU student seconds
             to this run's target-side time. It is a projection, not a
             matched end-to-end measurement.

Usage: python -m experiments.strata_specprefill.goal_report RUN_DIR [RUN_DIR ...] [--json OUT]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .ledger_report import ledger


def load(directory, arm):
    path = Path(directory)/arm
    if not (path/'summary.json').exists():
        return None
    summary = json.loads((path/'summary.json').read_text())
    turns = [json.loads(line) for line in (path/'turns.jsonl').read_text().splitlines()]
    return summary, turns


def report(directory):
    out = {'run': str(directory)}
    arms = {a: load(directory, a) for a in ('native32k', 'full32k', 'summary8k', 'lean8k', 'lean8ksep')}
    for arm, data in arms.items():
        if data:
            s, _ = data
            out[arm] = {'correct': f"{s['correct_fields']}/{s['fields']}",
                        'late': f"{s['late_correct_fields']}/{s['late_fields']}",
                        'wall_s': round(s['total_wall_s'], 1), 'compactions': s['compactions']}
    base = 'native32k' if arms['native32k'] else 'full32k'
    for arm in ('lean8k', 'lean8ksep', 'summary8k'):
        if arms[arm] and arms[base]:
            out[arm]['retention_vs_native'] = round(ledger(Path(directory)/base, Path(directory)/arm)['retention'], 3)
    if arms['lean8ksep'] and arms['lean8k']:
        _, sep = arms['lean8ksep']
        _, gpu = arms['lean8k']
        target_side = sum(t['wall_s'] - t['ledger']['student_s'] for t in sep)
        student_gpu = sum(t['ledger']['student_s'] for t in gpu)
        out['lean8ksep']['cpu_student_s'] = round(sum(t['ledger']['student_s'] for t in sep), 1)
        out['lean8ksep']['target_side_s'] = round(target_side, 1)
        out['lean8ksep']['projected_wall_s'] = round(target_side + student_gpu, 1)
    def correct(arm):
        return int(out[arm]['correct'].split('/')[0])

    def ratio(a, b):
        return round(a / b, 2) if a and b else None
    gates = {}
    for arm, key in (('lean8k', 'wall_s'), ('lean8ksep', 'projected_wall_s')):
        if arm in out and base in out and 'summary8k' in out and key in out[arm]:
            spec, native, comp = out[arm][key], out[base]['wall_s'], out['summary8k']['wall_s']
            gates[arm] = {'speedup_vs_native': ratio(native, spec), 'speedup_vs_compaction': ratio(comp, spec),
                          'quality_vs_native': f"{out[arm]['correct']} vs {out[base]['correct']}",
                          'quality_vs_compaction': f"{out[arm]['correct']} vs {out['summary8k']['correct']}",
                          'equal_to_native': correct(arm) >= correct(base) - 1,
                          'better_than_compaction': correct(arm) > correct('summary8k'),
                          'twice_native_speed': native / spec >= 2,
                          'twice_compaction_speed': comp / spec >= 2}
    out['baseline'] = base
    out['gates'] = gates
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', type=Path, nargs='+')
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    results = [report(r) for r in args.runs]
    for r in results:
        print(json.dumps(r, indent=1))
    if args.json:
        args.json.write_text(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
