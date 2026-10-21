"""Audit the permanent-omission negative control and export sanitized paired results."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from . import cache_experiment as engine_api
from .append_report import summarize
from .omission_archive import OmissionArchive

ARMS = ('append-none', 'append-no-recovery')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def generate(source, output):
    digest = hashlib.sha256((source/'cases.json').read_bytes()).hexdigest()
    protocol = {}
    if (source/'protocol.json').exists():
        protocol = json.loads((source/'protocol.json').read_text())
        require(digest == protocol['cases_sha256'], 'Frozen case hash mismatch')
    complete = json.loads((source/'COMPLETE-append-none.json').read_text())
    require(complete['frozen_sha256'] == digest and complete['arms'] == list(ARMS), 'Run incomplete')
    cases = json.loads((source/'cases.json').read_text())['cases']
    rows = [json.loads(line) for line in (source/'requests.jsonl').read_text().splitlines()]
    expected = Counter((arm, c['id'], i) for arm in ARMS for c in cases for i in range(len(c['turns'])))
    require(Counter((r['arm'], r['case'], r['turn']) for r in rows) == expected, 'Incomplete/duplicate rows')
    by_key = {(r['arm'], r['case'], r['turn']): r for r in rows}
    tok, _, _ = engine_api.load_frontend(engine_api.STRATA, engine_api.TOKENIZER)
    byte_spans, paired_initial = 0, 0
    answer_visible = {}
    for case in cases:
        initial = [by_key[(arm, case['id'], 0)] for arm in ARMS]
        require(initial[0]['selection_hash'] == initial[1]['selection_hash'], 'Initial selector mismatch')
        require(initial[0]['target_input_hash'] == initial[1]['target_input_hash'], 'Initial target prompt mismatch')
        paired_initial += 1
        for arm in ARMS:
            path = source/arm/case['id']
            archive = OmissionArchive.load(path/'archive.json', tok)
            transcript = json.loads((path/'transcript.json').read_text())
            ids = transcript['target_ids']
            deliveries = [h for e in archive.events if e['event'] == 'deliver' for h in e['handles']]
            require(len(deliveries) == len(set(deliveries)), 'Duplicate evidence delivery')
            for handle, span in archive.delivered.items():
                payload = b''.join(tok.token_bytes(t) for t in ids[span['start']:span['end']])
                require(archive.chunks[handle].text.encode() in payload, 'Source byte mismatch')
                byte_spans += 1
            blocked = set()
            for i, turn in enumerate(case['turns']):
                row = by_key[(arm, case['id'], i)]
                prompt = ids[:row['target_prompt_tokens']]
                require(engine_api.token_hash(prompt) == row['target_input_hash'], 'Append-only prompt mismatch')
                require(row['prefix_exact'] and row['position_mode'] == 'new_sequential', 'Prefix invariant failed')
                raw = b''.join(tok.token_bytes(t) for t in prompt)
                require(turn['question'].encode() in raw, 'Current question omitted')
                answer_visible[(arm, case['id'], i)] = turn['evidence']['needle'].encode() in raw
                now = set(row['permanently_omitted_handles'])
                require(blocked <= now, 'Permanent omission was reversed')
                blocked = now
                if arm == 'append-no-recovery':
                    require(not (now & set(archive.delivered)), 'Blocked evidence delivered later')
                    require(row['historical_recovery_chunks'] == 0 and not row['fetches'], 'Recovery in negative control')
                    # Derive blocked handles independently from first-arrival selections.
                    expected_blocked = {
                        h for sid, src in archive.sources.items() if src['turn'] <= i
                        for h in src['chunks']
                        if h not in by_key[(arm, case['id'], src['turn'])]['selected_source_handles']
                    }
                    require(now == expected_blocked, 'First-arrival omission ledger mismatch')
    stats = {arm: summarize([r for r in rows if r['arm'] == arm]) for arm in ARMS}
    # Fixed paired subset: first needs that required recovery in the recovery-enabled arm.
    keys = [(r['case'], r['turn']) for r in rows
            if r['arm'] == 'append-none' and r['previously_omitted_needed']]
    for arm in ARMS:
        subset = [by_key[(arm, c, t)] for c, t in keys]
        stats[arm]['paired_recovery_needed_turns'] = len(subset)
        stats[arm]['paired_recovery_needed_correct'] = sum(r['correct'] for r in subset)
        stats[arm]['paired_recovery_needed_answer_visible'] = sum(answer_visible[(arm, c, t)] for c, t in keys)
    control = [r for r in rows if r['arm'] == 'append-no-recovery']
    permanently_needed = [r for r in control if r['needed_permanently_omitted']]
    details = [{'case':r['case'], 'turn':r['turn'], 'correct':r['correct'],
                'answer_visible_before_inference':answer_visible[(r['arm'], r['case'], r['turn'])],
                'blocked_selected_chunks':len(r['blocked_selected_handles'])} for r in permanently_needed]
    summary = {'setup':protocol.get('setup'), 'source_sha256':protocol.get('source_sha256'),
               'native_binary_sha256':protocol.get('native_binary_sha256'), 'case_sha256':digest, 'rows':len(rows), 'identical_initial_prompts_and_selections':paired_initial,
               'verified_source_byte_spans':byte_spans, 'arms':stats,
               'control_permanently_missing_needed':details,
               'control_correct_with_permanently_missing_needed':sum(r['correct'] for r in permanently_needed),
               'control_blocked_selection_count':sum(len(r['blocked_selected_handles']) for r in control),
               'recovery_needed_paired_turns':[{'case':c, 'turn':t} for c, t in keys]}
    setup = protocol.get('setup', {})
    setup_text = (f"Measured setup: {setup.get('student', 'unrecorded student')} and "
                  f"{setup.get('target', 'unrecorded target')} on {setup.get('gpu', 'unrecorded hardware')}.")
    lines = ['# Permanent-omission control', '',
             'Same frozen incoming conversations, models, frozen adaptive policy, and enabled student/target caches. '
             'Both arms retain the full current question. The control permanently denies source chunks omitted on '
             'first arrival; no placeholders or retrieval tools are enabled. Each arm carries its own actual replies forward.', '',
             setup_text, '',
             'Latency includes archive, student selection, delivery, and target prefill '
             'through the first answer token, excluding model startup. The measured run keeps both models resident.', '',
             '| Arm | Correct | Correct on paired recovery-needed turns | Growing-turn mean TTFT |',
             '|---|---:|---:|---:|']
    for arm, s in stats.items():
        lines.append(f"| {arm} | {s['correct']}/{s['requests']} | "
                     f"{s['paired_recovery_needed_correct']}/{s['paired_recovery_needed_turns']} | "
                     f"{s['growing_mean_latency_s']:.3f} s |")
    lines.extend(['', f'Initial target prompts and selector outputs matched exactly in {paired_initial}/{len(cases)} conversations. '
                  f'All {len(rows)} target prompt hashes reconstructed from append-only transcripts; current questions survived. '
                  f'{byte_spans} delivered source byte spans verified; no blocked chunk was ever delivered. '
                  f"Growing turns with zero target reuse: {stats['append-none']['growing_zero_reuse']} with recovery, "
                  f"{stats['append-no-recovery']['growing_zero_reuse']} without recovery.", '',
                  '| Control turn (zero-based) | Correct | Expected answer already in prompt | Blocked selected chunks |',
                  '|---|---:|---:|---:|'])
    for d in details:
        lines.append(f"| {d['case']} / {d['turn']} | {d['correct']} | "
                     f"{d['answer_visible_before_inference']} | {d['blocked_selected_chunks']} |")
    lines.extend(['', 'The paired recovery-needed subset is defined by the recovery-enabled arm, before comparing '
                  'control correctness. The additional control-only repeated question is shown separately above. '
                  'Answer visibility is a literal byte check performed after inference for auditing, never a selector input.', '',
                  'This tests whether recovery matters on these existing cases. It is not fresh held-out validation: '
                  'four synthetic lookup/version conversations, one sample per arm/turn, fixed arm order. Later selectors '
                  'can differ because each arm retains its actual replies. Faster incomplete answers are not an accuracy-matched speedup.'])
    output.mkdir(parents=True, exist_ok=True)
    (output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    return summary


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--source', type=Path, default=engine_api.ROOT/'data/strata_specprefill/no-recovery-v1')
    ap.add_argument('--output', type=Path, default=engine_api.ROOT/'artifacts/strata-specprefill/no-recovery-v1')
    a = ap.parse_args()
    result = generate(a.source, a.output)
    print(json.dumps({'rows':result['rows'], 'arms':result['arms']}))


if __name__ == '__main__':
    main()
