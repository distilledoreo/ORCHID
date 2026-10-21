"""Phase 4 report: SpecPrefill vs native on the agent-eval case (edits, long outputs, whole history).

Per probe kind and arm:
- line recall: expected lines (or paths) found in the reply, as scored in the run;
- chrF: character n-gram F-score (n = 1..6, beta = 2) between the reply and the
  full reference (the real patch's added lines, the real new file, the list of
  edited paths). Unlike exact line recall, it credits an answer that is close
  to what the agent actually wrote without matching it line for line.
Plus wall time per arm, and per depth bin from depth_report.

Usage: python -m experiments.strata_specprefill.agent_eval_report RUN_DIR [--json OUT]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .agent_eval_cases import added_lines
from .depth_report import report


def chrf(hypothesis, reference, n=6, beta=2.0):
    hypothesis, reference = ' '.join(hypothesis.split()), ' '.join(reference.split())
    precisions, recalls = [], []
    for k in range(1, n + 1):
        h = Counter(hypothesis[i:i + k] for i in range(len(hypothesis) - k + 1))
        r = Counter(reference[i:i + k] for i in range(len(reference) - k + 1))
        if not h or not r:
            continue
        overlap = sum((h & r).values())
        precisions.append(overlap / sum(h.values()))
        recalls.append(overlap / sum(r.values()))
    if not precisions:
        return 0.0
    p, r = sum(precisions) / len(precisions), sum(recalls) / len(recalls)
    return 0.0 if p + r == 0 else (1 + beta**2) * p * r / (beta**2 * p + r)


def references(case):
    """Full reference text per probe turn, rebuilt from the session the case came from."""
    from .agent_eval_cases import events
    patches = [text for kind, text, _ in events(Path(case['log'])) if kind == 'patch']
    refs, used = {}, 0
    for i, turn in enumerate(case['turns']):
        if turn['kind'] in ('edit', 'long'):
            # The probe precedes the first not-yet-used patch whose added lines it samples.
            for j in range(used, len(patches)):
                body = '\n'.join(added_lines(patches[j]))
                if all(' '.join(e.split()) in ' '.join(body.split()) for e in turn['expected'][:3]):
                    refs[i], used = body, j + 1
                    break
        elif turn['kind'] == 'whole':
            refs[i] = '\n'.join(turn['expected'])
    return refs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--arms', nargs='+', default=['lean256k', 'native256k'])
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    case = json.loads((args.run / 'case.json').read_text())
    refs = references(case)
    out = {'probes': {}, 'arms': {}}
    for arm in args.arms:
        rows = [json.loads(l) for l in (args.run / arm / 'turns.jsonl').read_text().splitlines()]
        by_kind = {}
        for i, row in enumerate(rows):
            if row['kind'] == 'filler':
                continue
            entry = by_kind.setdefault(row['kind'], {'probes': 0, 'correct': 0, 'fields': 0, 'chrf': []})
            entry['probes'] += 1
            entry['correct'] += row['correct_fields']
            entry['fields'] += row['fields']
            if i in refs:
                entry['chrf'].append(chrf(row['text'], refs[i]))
        out['arms'][arm] = {
            'wall_s': round(sum(r['wall_s'] for r in rows), 1),
            **{kind: {'probes': e['probes'], 'line_recall': f"{e['correct']}/{e['fields']}",
                      'line_recall_pct': round(100 * e['correct'] / max(1, e['fields']), 1),
                      'chrf': round(100 * sum(e['chrf']) / max(1, len(e['chrf'])), 1)}
               for kind, e in sorted(by_kind.items())}}
    out['depth'] = report(args.run, args.arms[-1], args.arms[:-1])['bins']
    print(json.dumps(out, indent=1))
    if args.json:
        args.json.write_text(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
