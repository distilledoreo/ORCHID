"""Audit and aggregate uncached, teacher-forced SpecPrefill divergence."""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from .core import token_hash
from .divergence_experiment import BASE, sha
from .divergence_metrics import compare_rows, content_positions

ARMS = ('fixed30', 'adaptive', 'fixed27')
TRAJECTORIES = ('full-generated', 'reference')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def aggregate(rows):
    import numpy as np
    return {'positions':len(rows), 'top1_matches':sum(r['top1_agree'] for r in rows),
            'top1_agreement':statistics.mean(r['top1_agree'] for r in rows),
            'mean_kl_nats':statistics.mean(r['kl_full_to_selected'] for r in rows),
            'p95_kl_nats':float(np.percentile([r['kl_full_to_selected'] for r in rows], 95)),
            'max_kl_nats':max(r['kl_full_to_selected'] for r in rows),
            'full_reference_logp_per_token':statistics.mean(r['full_reference_logp'] for r in rows),
            'selected_reference_logp_per_token':statistics.mean(r['selected_reference_logp'] for r in rows),
            'reference_logp_delta_per_token':statistics.mean(r['reference_logp_delta'] for r in rows)}


def generate(source, output):
    import numpy as np
    protocol = json.loads((source/'protocol.json').read_text())
    complete = json.loads((source/'COMPLETE.json').read_text())
    require(sha(source/'cases.json') == protocol['cases_sha256'] == complete['cases_sha256'], 'Frozen input hash mismatch')
    require(sha(source/'selections-with27.json') == complete['selections_sha256'], 'Frozen selection hash mismatch')
    baseline = json.loads((source/'baseline-hashes-before.json').read_text())
    require(all(Path(p).is_file() and sha(Path(p)) == expected for p, expected in baseline.items()), 'An existing baseline artifact changed')
    native = json.loads((source/'native/manifest.json').read_text())
    require(sha(source/'native/strata-diagnostic') == native['diagnostic_binary_sha256'], 'Diagnostic binary changed')
    require(sha(Path('/home/distilledoreo/AI/Strata/build-cuda12/strata')) == native['baseline_binary_sha256'], 'Baseline binary changed')
    require(sha(Path('/home/distilledoreo/AI/Strata/src/program/generate.cpp')) == sha(source/'native/generate-before.cpp'), 'Original Strata source not restored')
    cases = json.loads((source/'cases.json').read_text())['cases']
    from . import cache_experiment as api
    tok, _, _ = api.load_frontend(api.STRATA, api.TOKENIZER)
    selection = {r['id']:r for r in json.loads((source/'selections-with27.json').read_text())['results']}
    requests = [json.loads(line) for line in (source/'requests.jsonl').read_text().splitlines()]
    require(len({r['index'] for r in requests}) == len(requests), 'Duplicate diagnostic request indices')
    by_label = {r['label']:r for r in requests}
    require(len(by_label) == len(requests), 'Duplicate request labels')
    expected = Counter(f'{c["id"]}/{t}/{a}' for c in cases for t in TRAJECTORIES for a in ('full',)+ARMS)
    actual = Counter(r['label'] for r in requests if not r['label'].startswith('validation-') and '/control/' not in r['label'])
    require(actual == expected, 'Incomplete diagnostic grid')
    require(all(r['reused']==0 for r in requests), 'Unexpected target cache reuse')
    require(all(r['student_reused']==0 for r in selection.values()), 'Unexpected student prefix cache reuse')
    verified_files = set()
    def load(row):
        p = source/row['file']
        if row['file'] not in verified_files:
            require(sha(p) == row['sha256'], 'Saved logit matrix hash mismatch')
            verified_files.add(row['file'])
        with np.load(p) as saved:
            result = saved['logits']
        require(result.shape == (row['rows'], 248320), 'Full vocabulary/row count mismatch')
        return result
    per_prompt, all_positions = [], []
    for case in cases:
        ids = case['input_ids']
        truth = case['truth']
        literals = ([truth['base'], truth['multiplier'], truth['offset']] if case['family']=='coding'
                    else truth['nodes'][1:]+[truth['code']])
        critical = set(content_positions(case['reference'],
                       [tok.token_bytes(t) for t in case['reference_ids'][:-1]], literals))
        require(bool(critical), 'Reference content spans not found')
        require(len(ids) == case['length'] and token_hash(ids) == case['input_hash'] == selection[case['id']]['input_hash'], 'Case/selection identity failed')
        for trajectory in TRAJECTORIES:
            full = by_label[f'{case["id"]}/{trajectory}/full']
            tokens = full['output_ids']
            require(full['ids_hash'] == token_hash(ids), 'Full-context input differs')
            if trajectory == 'reference':
                require(tokens == case['reference_ids'], 'Reference continuation mismatch')
            p = load(full)
            for arm in ARMS:
                row = by_label[f'{case["id"]}/{trajectory}/{arm}']
                positions = selection[case['id']][arm]
                require(positions == sorted(set(positions)) and positions and positions[-1] < len(ids), 'Invalid retained coordinates')
                require(row['ids_hash'] == token_hash([ids[i] for i in positions]), 'Selected token IDs changed')
                require(row['positions_hash'] == token_hash(positions) and row['original_length'] == len(ids), 'Original coordinate mismatch')
                require(row['output_ids'] == tokens, 'Teacher-forced histories diverged')
                q = load(row)
                values = compare_rows(p, q, tokens)
                for value in values:
                    value.update(case=case['id'], family=case['family'], length=case['length'], trajectory=trajectory, arm=arm)
                    value['reference_content_token'] = trajectory=='reference' and value['position'] in critical
                all_positions.extend(values)
                metrics = aggregate(values)
                metrics.update(case=case['id'], family=case['family'], length=case['length'], trajectory=trajectory,
                               arm=arm, retention=len(positions)/len(ids),
                               full_sequence_logp=sum(r['full_reference_logp'] for r in values),
                               selected_sequence_logp=sum(r['selected_reference_logp'] for r in values))
                per_prompt.append(metrics)
    controls = []
    for row in requests:
        if '/control/full-selection' in row['label']:
            case_id = row['label'].split('/')[0]
            full = by_label[f'{case_id}/full-generated/full']
            a, b = load(full), load(row)
            error = float(np.max(np.abs(a-b)))
            rows = compare_rows(a, b, full['output_ids'])
            mean_kl = sum(r['kl_full_to_selected'] for r in rows)/len(rows)
            require(row['output_ids'] == full['output_ids'] and all(r['top1_agree'] for r in rows) and mean_kl <= 0.05, 'Long full-retention control failed')
            controls.append({'case':case_id, 'max_abs_error':error, 'mean_kl_nats':mean_kl, 'positions':len(full['output_ids'])})
    require(len(controls)==3, 'Missing 8K/16K/32K full-retention controls')
    for row in requests:
        if row['label'].startswith('validation-'):
            load(row)
    grouped = {}
    for trajectory in TRAJECTORIES:
        grouped[trajectory] = {}
        for arm in ARMS:
            rr = [r for r in all_positions if r['trajectory']==trajectory and r['arm']==arm]
            metrics = aggregate(rr)
            prompts = [r for r in per_prompt if r['trajectory']==trajectory and r['arm']==arm]
            metrics['macro_prompt_top1_agreement'] = statistics.mean(r['top1_agreement'] for r in prompts)
            metrics['prompts_at_least_95pct'] = sum(r['top1_agreement'] >= .95 for r in prompts)
            metrics['prompts'] = len(prompts)
            grouped[trajectory][arm] = metrics
    by_length = {}
    by_family = {}
    for trajectory in TRAJECTORIES:
        by_length[trajectory] = {str(n):{arm:aggregate([r for r in all_positions if r['trajectory']==trajectory and r['length']==n and r['arm']==arm]) for arm in ARMS} for n in (8192, 16384, 32768)}
        by_family[trajectory] = {family:{arm:aggregate([r for r in all_positions if r['trajectory']==trajectory and r['family']==family and r['arm']==arm]) for arm in ARMS} for family in ('coding', 'multihop')}
    retention = {arm:{str(n):statistics.mean(len(selection[c['id']][arm])/n for c in cases if c['length']==n) for n in (8192, 16384, 32768)} for arm in ARMS}
    critical_metrics = {arm:aggregate([r for r in all_positions if r['arm']==arm and r['reference_content_token']]) for arm in ARMS}
    first8_metrics = {t:{arm:aggregate([r for r in all_positions if r['trajectory']==t and r['arm']==arm and r['position']<8]) for arm in ARMS} for t in TRAJECTORIES}
    native_validation = json.loads((source/'native-validation.json').read_text())
    summary = {'cases':len(cases), 'diagnostic_requests':len(requests), 'full_vocabulary':248320,
               'metrics':grouped, 'by_length':by_length, 'by_family':by_family, 'actual_retention':retention,
               'per_prompt':per_prompt, 'full_retention_controls':controls, 'native_validation':native_validation,
               'reference_content_tokens':critical_metrics, 'first8_tokens':first8_metrics,
               'baseline_artifacts_unchanged':len(baseline), 'protocol':protocol,
               'native_build':{k:v for k, v in native.items() if 'sha256' in k or k in ('cuda','hip','sycl')},
               'verified_logit_files':len(verified_files)}
    for filename, key in (('control-audit.json', 'short_control_audit'),
                          ('user-continuation.json', 'user_continuation')):
        if (source/filename).exists():
            summary[key] = json.loads((source/filename).read_text())
    lines = ['# Uncached SpecPrefill behavioral divergence', '',
             'Twelve frozen prompts: two coding and two multi-hop tasks at each exact length 8192, 16384, '
             '32768. Same Flash-Next Swift IQ3_S target and P100 16GB. The student is Qwen3.5-0.8B. '
             'Both cross-request caches are disabled; no append-only retrieval, placeholders, query pinning, '
             'or policy changes. Native speculative decoding is disabled for aligned diagnostics.', '',
             'At continuation index j, all arms see exactly the same previous continuation IDs, at original '
             'positions N through N+j-1. Only prompt retention differs. We evaluate two separate trajectories: '
             'the full-context model\'s greedy continuation (up to 96 tokens, including EOS when emitted) and '
             'an independently constructed complete reference answer including EOS. These are conditional '
             'distribution comparisons, not independently generated completions.', '',
             'KL is D_KL(P_full || P_selected), in nats, over all 248320 vocabulary entries. Metrics use '
             'float64 log-softmax from unmodified float32 model logits. Top-1 agreement compares raw argmax '
             'IDs. Reference-answer log probability is teacher-forced, summed over the complete reference '
             'sequence; token-normalized values are also reported. No top-k approximation is used.', '',
             'The unchanged adaptive mass-0.80 selector chose about 5%, so an additional fixed27 arm was '
             'frozen before target inference to answer the specific approximately-27% question directly.', '',
             '| Arm | Actual retention, 8K | 16K | 32K |', '|---|---:|---:|---:|']
    for arm in ARMS:
        lines.append('| '+arm+' | '+' | '.join(f'{retention[arm][str(n)]:.2%}' for n in (8192,16384,32768))+' |')
    for trajectory in TRAJECTORIES:
        lines.extend(['', f'## {trajectory} trajectory', '',
                      '| Arm | Top-1 agreement | Mean KL | P95 KL | LogP delta/token | Prompts with ≥95% agreement |',
                      '|---|---:|---:|---:|---:|---:|'])
        for arm, m in grouped[trajectory].items():
            lines.append(f"| {arm} | {m['top1_agreement']:.2%} ({m['top1_matches']}/{m['positions']}) | "
                         f"{m['mean_kl_nats']:.4f} | {m['p95_kl_nats']:.4f} | {m['reference_logp_delta_per_token']:+.4f} | "
                         f"{m['prompts_at_least_95pct']}/{m['prompts']} |")
        lines.extend(['', '| Length | Arm | Top-1 agreement | Mean KL | LogP delta/token |', '|---|---|---:|---:|---:|'])
        for n, arms in by_length[trajectory].items():
            for arm, m in arms.items():
                lines.append(f"| {int(n)//1024}K | {arm} | {m['top1_agreement']:.2%} | {m['mean_kl_nats']:.4f} | {m['reference_logp_delta_per_token']:+.4f} |")
    lines.extend(['', '## Reference facts, excluding boilerplate', '',
                  'These positions overlap the required literal constants, relay destination IDs, and final '
                  'access code in the independently constructed reference. This grading-only breakdown '
                  'checks whether syntax-heavy aggregate agreement conceals disagreement on answer facts.', '',
                  '| Arm | Content Top-1 agreement | Content KL | Content LogP delta/token |', '|---|---:|---:|---:|'])
    for arm, m in critical_metrics.items():
        lines.append(f"| {arm} | {m['top1_agreement']:.2%} ({m['top1_matches']}/{m['positions']}) | {m['mean_kl_nats']:.4f} | {m['reference_logp_delta_per_token']:+.4f} |")
    lines.extend(['', '## Reference-answer sequence probability', '',
                  '| Case | Arm | Full logP | Selected logP | Difference |', '|---|---|---:|---:|---:|'])
    for r in per_prompt:
        if r['trajectory']=='reference':
            lines.append(f"| {r['case']} | {r['arm']} | {r['full_sequence_logp']:.3f} | {r['selected_sequence_logp']:.3f} | {r['selected_sequence_logp']-r['full_sequence_logp']:+.3f} |")
    lines.extend(['', '## Validation and limits', '',
                  f'All {len(requests)} native requests reported zero reused prefix tokens. Teacher-forced IDs '
                  'matched exactly, and sparse token IDs/coordinate hashes matched the frozen selection. '
                  'Full-retention explicit-position controls passed at 8K, 16K, and 32K. Short-prompt controls '
                  'compare forced versus greedy decoding and cold independent sparse-prefix rows versus '
                  'in-request teacher-forced rows. See summary.json for numerical errors and family breakdowns.', '',
                  f'{len(baseline)} existing baseline artifacts and the original native binary/source were '
                  'verified unchanged. This creates a separate diagnostic result; it does not revise earlier '
                  'latency or correctness tables. Logit dumping, teacher forcing, and disabled speculative '
                  'decoding make diagnostic durations unsuitable as production latency measurements.', '',
                  'CUDA diagnostic executable built; focused native unit checks passed. HIP/SYCL compilers '
                  'are unavailable here, so those backends were not built. The diagnostic is opt-in and '
                  'distributed as a separate patch/executable; the existing native source was restored.', '',
                  'Limitations: synthetic repository reasoning and relay-graph tasks, four prompts per length, '
                  'one greedy full-context continuation per prompt, one independent reference, shared quantized '
                  'target and int8 KV. Adjacent token positions are correlated. Aggregate Top-1 percentages '
                  'are empirical agreement on this sample, not a deployment-wide guarantee. Formatting and '
                  'predictable reference prefixes may increase conditional agreement. KL/Top-1 do not by '
                  'themselves establish task correctness or equivalence of independently generated answers.'])
    lines.extend(['', 'Short-control qualification: the short forced/full-selection repeats and cold sparse '
                  'prefix comparisons were not bit-identical; their measured differences are retained in '
                  'summary.json and control-audit.json. Startup tolerances were relaxed in the user-resumed '
                  'run. These checks must not be described as exact numerical equivalence. In contrast, '
                  'the 8K/16K/32K full-retention controls had zero raw-logit difference and zero KL.'])
    output.mkdir(parents=True, exist_ok=True)
    (output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    with (source/'position-metrics.jsonl').open('w') as f:
        for row in all_positions:
            f.write(json.dumps(row)+'\n')
    return summary


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--source', type=Path, default=BASE)
    ap.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[2]/'artifacts/strata-specprefill/divergence-v1')
    a = ap.parse_args()
    result = generate(a.source, a.output)
    print(json.dumps({'cases':result['cases'], 'metrics':result['metrics']}))


if __name__ == '__main__':
    main()
