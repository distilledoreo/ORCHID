"""Export bounded cache experiment statistics without prompts or provider responses."""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from .core import token_hash


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def summarize(records, latency):
    return {arm: {
        'requests': len(rr := [r for r in records if r['arm'] == arm]),
        'correct': sum(r['correct'] for r in rr),
        'mean_ttft_s': statistics.mean(r[latency] for r in rr),
        'growing_turn_mean_ttft_s': statistics.mean(r[latency] for r in rr if r['turn'] > 0),
        'per_turn_ttft_s': [statistics.mean(r[latency] for r in rr if r['turn'] == t) for t in range(4)],
        'mean_target_reused_tokens': statistics.mean(r['engine'].get('reused', 0) for r in rr),
        'retained_fraction': sum(r.get('retained_tokens', r['input_tokens']) for r in rr) / sum(r['input_tokens'] for r in rr),
    } for arm in sorted({r['arm'] for r in records})}


def validate_records(records, arms, frozen):
    expected = Counter((sid, t, arm) for sid, t in frozen for arm in arms)
    actual = Counter((r['session'], r['turn'], r['arm']) for r in records)
    if actual != expected:
        raise ValueError('Missing, duplicate, or unexpected benchmark rows')
    for r in records:
        if r['input_hash'] != token_hash(frozen[(r['session'], r['turn'])]['input_ids']):
            raise ValueError('Benchmark row is not bound to the frozen prompt')


def generate(source, output):
    sets = json.loads((source / 'sets.json').read_text())
    frozen = {(s['id'], t['turn']): t for s in sets for t in s['turns']}
    control = rows(source / 'control.jsonl')
    isolation = rows(source / 'student-cache.jsonl')
    both = rows(source / 'both-cache.jsonl')
    student = rows(source / 'student.jsonl')
    validate_records(control, ['control-off', 'control-cache'], frozen)
    validate_records(isolation, ['student-off', 'student-on'], frozen)
    validate_records(both, ['full-colocated-cache', 'stable-both-cache'], frozen)
    if Counter((r['session'], r['turn']) for r in student) != Counter(frozen.keys()):
        raise ValueError('Incomplete student comparisons')
    stable = [r for r in both if r['arm'] == 'stable-both-cache']
    if not all(r['cold_output_equal'] for r in stable):
        raise ValueError('Cached target output differs from exact cold replay')
    if any(r['engine'].get('reused', 0) for r in isolation):
        raise ValueError('Student-only experiment unexpectedly reused target state')
    probes = json.loads((source / 'position-binding.json').read_text())
    if (probes['append_reused'] <= 0 or probes['changed_position_reused'] > 10
            or probes['sparse_to_implicit_reused'] != 0 or not probes['changed_position_cold_output_equal']):
        raise ValueError('Target cache binding checks failed')
    summary = {
        'sessions': len(sets), 'turns_per_session': 4,
        'prompt_lengths': [len(t['input_ids']) for t in sets[0]['turns']],
        'growth_tokens': [t['growth_tokens'] for t in sets[0]['turns']],
        'control': summarize(control, 'target_ttft_s'),
        'student_isolation': summarize(isolation, 'pipeline_ttft_s'),
        'both_caches': summarize(both, 'pipeline_ttft_s'),
        'equivalence': {
            'comparisons': len(student),
            'cached_vs_chunked_positions_equal': sum(r['comparison']['selected_positions_equal'] for r in student),
            'cached_vs_full_positions_equal': sum(r['comparison']['reference_positions_equal'] for r in student),
            'drafts_equal': sum(r['comparison']['draft_ids_equal'] for r in student),
            'max_score_abs_diff': max(r['comparison']['max_score_abs_diff'] for r in student),
            'min_selection_jaccard': min(r['comparison']['selection_jaccard'] for r in student),
            'target_cached_cold_outputs_equal': len(stable),
        },
        'position_binding': probes,
        'stable_backfill_turns': sum(r['backfilled_tokens'] > 0 for r in stable),
        'stable_target_reused_per_turn': [statistics.mean(r['engine'].get('reused', 0) for r in stable if r['turn'] == t) for t in range(4)],
        'stable_retained_per_turn': [statistics.mean(r['retained_tokens']/r['input_tokens'] for r in stable if r['turn'] == t) for t in range(4)],
        'provenance': json.loads((source / 'provenance.json').read_text()),
    }
    reference = rows(source / 'reference-quality.jsonl')
    expected_reference = Counter((r['session'], r['turn']) for r in student if not r['comparison']['reference_positions_equal'])
    if Counter((r['session'], r['turn']) for r in reference) != expected_reference:
        raise ValueError('Incomplete original full-prefill quality checks')
    live_cached = {(r['session'], r['turn']): r['positions'] for r in isolation if r['arm'] == 'student-on'}
    for r in student:
        key = (r['session'], r['turn'])
        if r['input_hash'] != token_hash(frozen[key]['input_ids']):
            raise ValueError('Student comparison prompt drift')
        if live_cached[key] != r['cached']['positions']:
            raise ValueError('Live/standalone selections differ; reference quality alias requires fresh checks')
    summary['reference_distinct_selection_checks'] = {'requests': len(reference), 'correct': sum(r['correct'] for r in reference)}
    standalone = summary['control']['control-cache']['growing_turn_mean_ttft_s']
    spec = summary['both_caches']['stable-both-cache']['growing_turn_mean_ttft_s']
    summary['spec_reduction_vs_production_control'] = 1-spec/standalone
    comparison_text = f'{abs(1-spec/standalone):.1%} ' + ('faster' if spec < standalone else 'slower')
    control_pairs = {(r['session'], r['turn'], r['arm']): r for r in control}
    control_equal = sum(control_pairs[(sid,t,'control-off')]['output_ids'] == control_pairs[(sid,t,'control-cache')]['output_ids'] for sid,t in frozen)
    iso_off = summary['student_isolation']['student-off']
    iso_on = summary['student_isolation']['student-on']
    lines = [
        '# Multi-turn prefix caching experiment', '',
        'Same Swift IQ3_S Flash-Next target, Qwen3.5-0.8B student, and P100 16GB. '
        'Two frozen scripted recall conversations, four probes each. Greedy decoding, '
        'int8 target KV, MTP 4, 16k context, six prefix checkpoints, no parked conversations. '
        'These are synthetic fixed histories, not repository edits or a live agent loop.', '',
        '## Step 1: ordinary target control', '',
        '| Prompt tokens | Added tokens | Cache off TTFT | Cache on TTFT | Reduction |',
        '|---:|---:|---:|---:|---:|',
    ]
    off = summary['control']['control-off']['per_turn_ttft_s']
    on = summary['control']['control-cache']['per_turn_ttft_s']
    for t, n in enumerate(summary['prompt_lengths']):
        lines.append(f"| {n} | {summary['growth_tokens'][t]} | {off[t]:.3f}s | {on[t]:.3f}s | {1-on[t]/off[t]:.1%} |")
    lines += ['', f"Cache off: {summary['control']['control-off']['correct']}/8 correct; cache on: {summary['control']['control-cache']['correct']}/8. Output IDs match {control_equal}/8. "
              'Warmup used a 256-token prefix; initial probes are therefore excluded from the primary growing-turn comparison.', '',
              '## Steps 2–3: student caching and equivalence', '',
              '| Turn | Uncached chunked student | Cached student | Reused prefix |',
              '|---:|---:|---:|---:|']
    for t in range(4):
        rr = [r for r in student if r['turn'] == t]
        lines.append(f"| {t} | {statistics.mean(r['uncached']['student_selection_s'] for r in rr):.3f}s | "
                     f"{statistics.mean(r['cached']['student_selection_s'] for r in rr):.3f}s | "
                     f"{statistics.mean(r['cached']['reused_prefix_tokens'] for r in rr):.0f} |")
    e = summary['equivalence']
    lines += ['', f"Draft IDs match {e['drafts_equal']}/8. Exact selected positions match "
              f"{e['cached_vs_chunked_positions_equal']}/8 versus uncached chunking and "
              f"{e['cached_vs_full_positions_equal']}/8 versus original full prefill. "
              f"Maximum absolute score difference is {e['max_score_abs_diff']:.8f}; "
              f"minimum selection Jaccard is {e['min_selection_jaccard']:.4f}. "
              'This is approximate numerical equivalence, not bitwise equivalence. '
              'The prompt cache is cloned before lookahead; only prompt state survives between turns.', '',
              'Target caching was disabled throughout the student isolation arm. Both selector variants '
              f"answered {iso_off['correct']}/8 (uncached) and {iso_on['correct']}/8 (cached) correctly. "
              f"Growing-turn pipeline means: {iso_off['growing_turn_mean_ttft_s']:.3f}s uncached versus "
              f"{iso_on['growing_turn_mean_ttft_s']:.3f}s cached. These include live student inference.", '',
              f"The original full-prefill selections were evaluated directly on the {len(reference)} "
              f"prompts where they differed from the cached student: {sum(r['correct'] for r in reference)}/{len(reference)} correct. "
              f'The other {len(student)-len(reference)} selections are identical to cached-student positions already evaluated live.', '',
              '## Step 4: both caches enabled', '',
              'Stable selection unions previous retained positions with newly selected positions. '
              'Earlier backfill invalidates the affected cached suffix; no old chunk is appended out of order. '
              'Token IDs and rotary coordinates must match for every reusable checkpoint. '
              'Retention is allowed to grow beyond 30%.', '',
              '| Prompt tokens | Ordinary cached target, alone | Ordinary cached target, colocated | SpecPrefill, both caches | Spec retention | Target tokens reused |',
              '|---:|---:|---:|---:|---:|---:|']
    col = summary['both_caches']['full-colocated-cache']['per_turn_ttft_s']
    sp = summary['both_caches']['stable-both-cache']['per_turn_ttft_s']
    for t, n in enumerate(summary['prompt_lengths']):
        lines.append(f"| {n} | {on[t]:.3f}s | {col[t]:.3f}s | {sp[t]:.3f}s | "
                     f"{summary['stable_retained_per_turn'][t]:.1%} | {summary['stable_target_reused_per_turn'][t]:.0f} |")
    lines += ['', f"Growing-turn mean TTFT: production-relevant ordinary cached target **{standalone:.3f}s**, "
              f"both-cache SpecPrefill **{spec:.3f}s** ({comparison_text}). "
              'The colocated ordinary control separates selection effects from GPU residency pressure.', '',
              f"Ordinary colocated target: {summary['both_caches']['full-colocated-cache']['correct']}/8 correct; "
              f"both-cache SpecPrefill: {summary['both_caches']['stable-both-cache']['correct']}/8 correct. "
              f"Selected cached responses match exact cold replay **{len(stable)}/8**. "
              f"Backfill occurred on {summary['stable_backfill_turns']}/8 probes. "
              f"The coordinate-change probe reused {probes['changed_position_reused']} tokens, "
              f"the append probe reused {probes['append_reused']}, and sparse-to-implicit reused zero.", '',
              '## Measurement limits', '',
              '- Timers use the same native Strata transport. Pipeline TTFT runs from before student work '
              'to the first target token. No sum of separately measured stages is substituted.',
              '- Models are loaded and warmed before measurements. Loading, HTTP, and chat rendering are excluded. '
              'The two pipeline models remain resident throughout each epoch.',
              '- Ordinary standalone control uses a 700 MiB reserve; colocated arms use 4096 MiB. '
              'The production baseline benefits from the larger expert cache. Same model weights/hardware/decode controls.',
              '- Only two samples per prompt length, one run per arm, fixed arm ordering, short outputs. '
              'This is a bounded feasibility test, not a statistically established general speedup.',
              '- Cached student resets on nonextending or changed prefixes. No partial-prefix/branch cache yet. '
              'Lookahead still scores the full prompt; cache copying remains part of measured student time.',
              '- CUDA build and native tests were run. HIP/SYCL compilers were unavailable. '
              'Installed production executable and service were not replaced.', '',
              'Raw prompts, selected IDs, provider outputs, and machine paths remain in ignored '
              '`data/strata_specprefill/cache-v1/`. Public summary contains aggregate statistics and hashes only.', '']
    output.mkdir(parents=True, exist_ok=True)
    (output / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    (output / 'REPORT.md').write_text('\n'.join(lines))
    print(json.dumps({'summary': str(output/'summary.json'), 'growing_turn_reduction': 1-spec/standalone}))


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--input', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    generate(a.input, a.output)


if __name__ == '__main__':
    main()
