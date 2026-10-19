"""Blind side-by-side review of two arms' answers to the agent-eval edit and long-output probes.

  packet  write one markdown file per probe: the user's latest request, the
          recent history before the probe, the recorded patch (one example
          solution, not the answer key) and the two answers as A and B in a
          random order. The A/B key goes to key.json, kept apart for unblinding.
  apply   for every answer that is a patch, check that its removed ('-') and
          context (' ') lines exist in the history before the probe: a patch
          can only apply to code the agent could have seen.
  score   join the judgments (judgments.json: {probe: {"A": verdict, "B":
          verdict, "better": "A"|"B"|"equal"}}, verdict correct|partial|wrong)
          with the key, per arm.

Usage: python -m experiments.strata_specprefill.blind_review packet RUN_DIR OUT_DIR [--arms lean256k native256k]
       python -m experiments.strata_specprefill.blind_review apply RUN_DIR [--arms ...]
       python -m experiments.strata_specprefill.blind_review score OUT_DIR
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

CONTEXT_CHARS = 14000


def probes(run, arms):
    case = json.loads((run / 'case.json').read_text())
    rows = {arm: [json.loads(l) for l in (run / arm / 'turns.jsonl').read_text().splitlines()] for arm in arms}
    history = ''
    for i, turn in enumerate(case['turns']):
        if turn['source']:
            history += turn['source']['text']
        if turn['kind'] in ('edit', 'long'):
            yield i, turn, history, {arm: rows[arm][i]['text'] for arm in arms}


def latest_request(history):
    at = history.rfind('USER:\n')
    return history[at + 6:].split('\nASSISTANT:\n')[0].split('\n$ ')[0].strip()[:3000] if at >= 0 else ''


def packet(run, out, arms, seed=20261018):
    from .agent_eval_report import references
    case = json.loads((run / 'case.json').read_text())
    refs = references(case)
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    key = {}
    for i, turn, history, answers in probes(run, arms):
        order = list(arms)
        rng.shuffle(order)
        name = f'probe-{i:03d}'
        key[name] = {'A': order[0], 'B': order[1], 'kind': turn['kind']}
        (out / f'{name}.md').write_text(
            f'# {name} ({turn["kind"]})\n\n## Question asked\n{turn["question"]}\n\n'
            f'## The user\'s latest request in the session\n{latest_request(history)}\n\n'
            f'## Recent history before the probe (last {CONTEXT_CHARS} characters)\n```\n{history[-CONTEXT_CHARS:]}\n```\n\n'
            f'## One example solution (what the recorded agent added; not the answer key)\n```\n{refs.get(i, "")[:6000]}\n```\n\n'
            f'## Answer A\n```\n{answers[order[0]]}\n```\n\n## Answer B\n```\n{answers[order[1]]}\n```\n')
    (out / 'key.json').write_text(json.dumps(key, indent=1))
    return len(key)


def applies(answer, history):
    """(lines checked, lines found): a patch's '-' and ' ' lines that exist in the earlier history."""
    seen = {re.sub(r'\s+', ' ', line.strip()) for line in history.splitlines() if line.strip()}
    checked = found = 0
    for line in answer.splitlines():
        if line[:1] in ('-', ' ') and not line.startswith(('---', '-***')) and line[1:].strip():
            checked += 1
            found += re.sub(r'\s+', ' ', line[1:].strip()) in seen
    return checked, found


def apply_check(run, arms):
    totals = {arm: [0, 0, 0] for arm in arms}  # patches with checked lines, lines checked, lines found
    for _, turn, history, answers in probes(run, arms):
        if turn['kind'] != 'edit':
            continue
        for arm, answer in answers.items():
            checked, found = applies(answer, history)
            if checked:
                totals[arm][0] += 1
            totals[arm][1] += checked
            totals[arm][2] += found
    return {arm: {'patches_with_context': t[0], 'context_lines': t[1], 'found_in_history': t[2],
                  'found_pct': round(100 * t[2] / max(1, t[1]), 1)} for arm, t in totals.items()}


def score(out):
    key = json.loads((out / 'key.json').read_text())
    judged = json.loads((out / 'judgments.json').read_text())
    per = {}
    for name, verdict in judged.items():
        for slot in ('A', 'B'):
            arm = key[name][slot]
            entry = per.setdefault(arm, {'correct': 0, 'partial': 0, 'wrong': 0, 'better': 0, 'equal': 0, 'probes': 0})
            entry[verdict[slot]] += 1
            entry['probes'] += 1
            if verdict['better'] == slot:
                entry['better'] += 1
            elif verdict['better'] == 'equal':
                entry['equal'] += 1
    for entry in per.values():
        entry['acceptable_pct'] = round(100 * (entry['correct'] + 0.5 * entry['partial']) / max(1, entry['probes']), 1)
    return per


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('mode', choices=('packet', 'apply', 'score'))
    parser.add_argument('path', type=Path)
    parser.add_argument('out', type=Path, nargs='?')
    parser.add_argument('--arms', nargs=2, default=['lean256k', 'native256k'])
    args = parser.parse_args()
    if args.mode == 'packet':
        print(packet(args.path, args.out, args.arms), 'probes written')
    elif args.mode == 'apply':
        print(json.dumps(apply_check(args.path, args.arms), indent=1))
    else:
        print(json.dumps(score(args.path), indent=1))


if __name__ == '__main__':
    main()
