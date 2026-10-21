"""Per-turn token ledger: how much the target actually reads versus native.

Compares a SpecPrefill (or compaction) arm's native calls with a native
cached control on the same case. Retention is target prompt tokens read
divided by the control's prompt tokens read for that turn; resident ratio is
the target's answer prompt length divided by the control's. Compaction calls
are charged to the turn that triggered them.

Usage: python -m experiments.strata_specprefill.ledger_report CONTROL_DIR ARM_DIR [...]
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def load_calls(directory):
    calls = [json.loads(line) for line in (Path(directory)/'calls.jsonl').read_text().splitlines()]
    turns = defaultdict(lambda: {'answer_read': 0, 'answer_input': 0, 'other_read': 0,
                                 'prefill_s': 0.0, 'decode_s': 0.0, 'other_s': 0.0, 'calls': 0})
    for call in calls:
        if call['turn'] < 0:
            continue
        row, engine = turns[call['turn']], call['engine']
        row['calls'] += 1
        if call['phase'] == 'answer':
            row['answer_read'] += engine['prompt_read']
            row['answer_input'] = call['input_tokens']
            row['prefill_s'] += engine['prompt_ms'] / 1000
            row['decode_s'] += engine['decode_ms'] / 1000
        else:
            row['other_read'] += engine['prompt_read']
            row['other_s'] += call['wall_s']
    return dict(sorted(turns.items()))


def load_wall(directory):
    path = Path(directory)/'turns.jsonl'
    if not path.exists():
        return {}
    return {json.loads(line)['turn'] - 1: json.loads(line) for line in path.read_text().splitlines()}


def ledger(control_dir, arm_dir):
    control, arm = load_calls(control_dir), load_calls(arm_dir)
    control_wall, arm_wall = load_wall(control_dir), load_wall(arm_dir)
    rows = []
    for turn in sorted(set(control) & set(arm)):
        c, a = control[turn], arm[turn]
        read = a['answer_read'] + a['other_read']
        rows.append({'turn': turn + 1, 'control_read': c['answer_read'], 'arm_read': read,
                     'arm_answer_read': a['answer_read'], 'arm_other_read': a['other_read'],
                     'retention': read / max(1, c['answer_read']),
                     'resident_ratio': a['answer_input'] / max(1, c['answer_input']),
                     'control_wall_s': control_wall.get(turn, {}).get('wall_s'),
                     'arm_wall_s': arm_wall.get(turn, {}).get('wall_s'),
                     'arm_prefill_s': a['prefill_s'], 'arm_decode_s': a['decode_s'],
                     'arm_other_native_s': a['other_s'],
                     'arm_outside_native_s': (arm_wall[turn]['wall_s'] - arm_wall[turn]['native_calls_s']
                                              if turn in arm_wall and 'native_calls_s' in arm_wall[turn] else None)})
    control_read = sum(r['control_read'] for r in rows)
    arm_read = sum(r['arm_read'] for r in rows)
    def total(key):
        values = [r[key] for r in rows if r[key] is not None]
        return sum(values) if values else None
    return {'turns': len(rows), 'control_read': control_read, 'arm_read': arm_read,
            'retention': arm_read / max(1, control_read),
            'final_resident_ratio': rows[-1]['resident_ratio'] if rows else None,
            'control_wall_s': total('control_wall_s'), 'arm_wall_s': total('arm_wall_s'),
            'arm_prefill_s': total('arm_prefill_s'), 'arm_decode_s': total('arm_decode_s'),
            'arm_other_native_s': total('arm_other_native_s'),
            'arm_outside_native_s': total('arm_outside_native_s'), 'rows': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('control', type=Path)
    parser.add_argument('arms', type=Path, nargs='+')
    parser.add_argument('--rows', action='store_true')
    args = parser.parse_args()
    for arm in args.arms:
        result = ledger(args.control, arm)
        if args.rows:
            for row in result['rows']:
                print(json.dumps(row))
        print(json.dumps({'arm': str(arm), **{k: v for k, v in result.items() if k != 'rows'}}))


if __name__ == '__main__':
    main()
