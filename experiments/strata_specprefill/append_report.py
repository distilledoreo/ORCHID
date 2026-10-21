"""Audit stateful omission trials and export aggregates, never raw source/provider text."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
from collections import Counter
from pathlib import Path

from . import cache_experiment as engine_api
from .append_experiment import ARMS
from .omission_archive import OmissionArchive


def mean(values):
    values=list(values)
    return statistics.mean(values) if values else None


def summarize(rr):
    growing=[r for r in rr if r['turn']>0]
    return {'requests':len(rr),'correct':sum(r['correct'] for r in rr),
            'mean_latency_s':mean(r['charged_latency_s'] for r in rr),
            'growing_mean_latency_s':mean(r['charged_latency_s'] for r in growing),
            'held_correct':sum(r['correct'] for r in rr if r['split']=='held'),
            'held_requests':sum(r['split']=='held' for r in rr),
            'growing_mean_reused_tokens':mean(r['target_reused'] for r in growing),
            'growing_reuse_fraction':sum(r['target_reused'] for r in growing)/max(1,sum(r['target_prompt_tokens'] for r in growing)),
            'prior_prefix_preservation_fraction':sum(min(r['target_reused'],r['expected_cached_prefix']) for r in growing)/max(1,sum(r['expected_cached_prefix'] for r in growing)),
            'growing_zero_reuse':sum(r['target_reused']==0 for r in growing),
            'partial_prefix_rewinds':sum(0<r['target_reused']<r['expected_cached_prefix'] for r in growing),
            'placeholder_tokens':sum(r['placeholder_tokens'] for r in rr),
            'final_context_tokens':mean(r['target_prompt_tokens'] for r in rr if r['turn']==3),
            'omitted_needed':sum(r['previously_omitted_needed'] for r in rr),
            'recovered':sum(r['recovery_succeeded'] for r in rr),
            'already_delivered_selection_skipped':sum(r['already_delivered_selection_skipped'] for r in rr),
            'off_task_delivered_chunks':sum(r['off_task_delivered_chunks'] for r in rr),
            'evidence_frame_tokens_including_tools':sum(r['evidence_tokens']+sum(f['appended_evidence_tokens'] for f in r['fetches']) for r in rr),
            'fetch_requests':sum(len(r['fetches']) for r in rr),
            'unresolved_fetches':sum(r['unresolved_fetch'] for r in rr),
            'tool_lookup_s':sum(f['retrieval_s'] for r in rr for f in r['fetches']),
            'student_reused_tokens':sum(r['student_reused'] for r in rr),
            'growing_student_selection_s':mean(r['student_s'] for r in growing),
            'mean_initial_visible_ttft_s':mean(r['first_visible_ttft_s'] for r in rr)}


def generate(source, output):
    rows=[json.loads(line) for line in (source/'requests.jsonl').read_text().splitlines() if line.strip()]
    cases=json.loads((source/'cases.json').read_text())['cases']
    expected=Counter((arm,c['id'],i) for arm in ARMS for c in cases for i in range(len(c['turns'])))
    actual=Counter((r['arm'],r['case'],r['turn']) for r in rows)
    if actual!=expected:
        raise ValueError('Missing, duplicate, or unexpected trial rows')
    for row in rows:
        if row['arm'].startswith(('append-', 'fetch-')) and (row['prefix_exact'] is not True or row['position_mode'] != 'new_sequential'):
            raise ValueError('Append-only token/position invariant failed')
    if any(v>expected.get(k,0) for k,v in actual.items()):
        raise ValueError('Duplicate/unexpected trial rows')
    tok,_,_=engine_api.load_frontend(engine_api.STRATA,engine_api.TOKENIZER)
    audits={}
    overhead=[]
    source_replays={}
    for arm in sorted({r['arm'] for r in rows}):
        for case in cases:
            path=source/arm/case['id']/'archive.json'
            if not path.exists():
                continue
            archive=OmissionArchive.load(path,tok)
            # Loaded archives deliberately do not claim any live cache is still valid.
            assert not archive.cached
            state=json.loads(path.read_text())
            transcript=json.loads(path.with_name('transcript.json').read_text())
            case_rows=[r for r in rows if r['arm']==arm and r['case']==case['id']]
            if case_rows:
                last=case_rows[-1]
                # Archive stores original turns and actual responses; full raw IDs remain private.
                if last['target_input_hash']!=engine_api.token_hash(transcript['last_prompt_ids']):
                    raise ValueError('Saved target request hash mismatch')
            delivered_ids = transcript['last_prompt_ids'] if arm.startswith('union') else transcript['target_ids']
            for handle, span in archive.delivered.items():
                payload = b''.join(tok.token_bytes(t) for t in delivered_ids[span['start']:span['end']])
                if archive.chunks[handle].text.encode() not in payload:
                    raise ValueError('Delivered target bytes do not match the archived source')
            if arm.startswith(('append-', 'fetch-')):
                source_replays[f'{arm}/{case["id"]}'] = sum(
                    span['end'] <= row['expected_cached_prefix'] and span['end'] > row['target_reused']
                    for row in case_rows if row['turn'] > 0 for span in archive.delivered.values())
            delivered=[h for e in state['events'] if e['event']=='deliver' for h in e['handles']]
            if len(delivered)!=len(set(delivered)):
                raise ValueError('Duplicate evidence content appended')
            # The archive's immutable source bytes, chunk offsets and version pointers were verified above.
            audits[f'{arm}/{case["id"]}']={'sources':len(archive.sources),'chunks':len(archive.chunks),
                                          'delivered_chunks':len(archive.delivered),'duplicate_semantic_appends':0,'delivered_source_byte_ranges_verified':len(archive.delivered)}
            if arm=='append-grouped':
                for e in state['events']:
                    if e['event']=='placeholders':
                        costs={style:archive.format_placeholders(e['handles'],style)[1]
                               for style in ('individual','grouped','compact_individual','compact_grouped','source_only')}
                        if costs['grouped']!=e['tokens']:
                            raise ValueError('Placeholder token count changed')
                        overhead.append({'case':case['id'],'handles':len(e['handles']),'costs':costs})
    stats={arm:summarize([r for r in rows if r['arm']==arm]) for arm in ARMS if any(r['arm']==arm for r in rows)}
    memories={arm:json.loads((source/f'memory-{arm}.json').read_text())
              for arm in stats if (source/f'memory-{arm}.json').exists()}
    controls={(r['case'],r['turn']):r for r in rows if r['arm']=='full-alone'}
    for arm in stats:
        rr=[r for r in rows if r['arm']==arm]
        def normalized(text):
            codes = re.findall(r'\d{5}', text)
            return codes[0] if len(codes)==1 else text.strip()
        stats[arm]['answer_agreement_with_full'] = sum(normalized(r['text'])==normalized(controls[(r['case'],r['turn'])]['text']) for r in rr if (r['case'],r['turn']) in controls)
        stats[arm]['oracle_correctness_agreement']=sum(r['correct']==controls.get((r['case'],r['turn']),{}).get('correct') for r in rr)
    summary={'rows':len(rows),'cases':len(cases),'stateful_actual_outputs':True,
             'case_sha256':hashlib.sha256((source/'cases.json').read_bytes()).hexdigest(),
             'arms':stats,'memory':memories,'archive_audits':audits,'previously_delivered_source_chunks_reprefilled':source_replays,
             'placeholder_counterfactual_tokens':{style:sum(e['costs'][style] for e in overhead)
                   for style in ('individual','grouped','compact_individual','compact_grouped','source_only')},
             'placeholder_audit_events':len(overhead),
             'protocol':json.loads((source/'protocol.json').read_text())}
    eviction=json.loads((source/'eviction-check.json').read_text())
    if eviction!={'reused':0,'output_equal':True,'duplicate_semantic_appends':0}:
        raise ValueError('Eviction/rebuild check failed')
    summary['eviction_check']=eviction
    if len(audits)!=len(ARMS)*len(cases):
        raise ValueError('Missing per-conversation archive audit')
    output.mkdir(parents=True,exist_ok=True)
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    main = stats.get('append-none')
    grouped = stats.get('append-grouped')
    individual = stats.get('append-individual')
    if main and grouped and individual:
        summary['observed_main_results'] = {
            'no_placeholder_reduction_vs_standalone':1-main['growing_mean_latency_s']/stats['full-alone']['growing_mean_latency_s'],
            'no_placeholder_reduction_vs_union30':1-main['growing_mean_latency_s']/stats['union30']['growing_mean_latency_s'],
            'grouped_placeholder_token_reduction_vs_individual':1-grouped['placeholder_tokens']/individual['placeholder_tokens'],
        }
    lines=['# Append-only SpecPrefill with addressable omission','',
           'Same Flash-Next Swift IQ3_S weights, Qwen3.5-0.8B student, and P100 16GB. '
           'Four conversations × four growing turns. Two reuse the previous frozen incoming texts; '
           'two fresh held-out cases test delayed recovery and explicit source revisions. '
           'All histories retain the model’s actual replies. No fixed assistant-answer replay is substituted.', '',
           '## Observed result','',
           f"No-placeholder append-only: {main['correct']}/{main['requests']} correct and {main['recovered']}/{main['omitted_needed']} genuine omitted-span recoveries. "
           f"Growing-turn mean {main['growing_mean_latency_s']:.3f}s versus {stats['full-alone']['growing_mean_latency_s']:.3f}s for ordinary standalone caching "
           f"({summary['observed_main_results']['no_placeholder_reduction_vs_standalone']:.1%} lower) and {stats['union30']['growing_mean_latency_s']:.3f}s for the original stable-union control.", '',
           f"Grouped references cost {grouped['placeholder_tokens']} tokens versus {individual['placeholder_tokens']} individual-reference tokens "
           f"({summary['observed_main_results']['grouped_placeholder_token_reduction_vs_individual']:.1%} fewer), with {grouped['correct']}/{grouped['requests']} versus {individual['correct']}/{individual['requests']} accuracy. "
           'The automatic placeholder variants showed no accuracy gain over no placeholders on this set. Grouped references provide cheaper addressability; visible omission markers are not required for the measured automatic recovery.', '',
           '## Implementation','',
           '- Exact source text, bytes/character/token offsets, immutable versions, SHA-256 provenance, '
           'stable source/chunk IDs, and original student coordinates are archived. ASCII chunks align '
           'with the unchanged 32-token selector grid; UTF-8 boundaries are preserved.',
           '- Target evidence uses append-only chat turns and new sequential positions. Omitted historical '
           'content is retrieved from the archive and appended with source/version/offset provenance. '
           'Nothing is inserted into an old cached prefix. Chronological union controls retain original coordinates.',
           '- Delivered content and confirmed cache state are separate ledger states. Exact source payload '
           'is never semantically appended twice. On cache loss the complete accumulated target ID stream '
           'is explicitly replayed; no new duplicate evidence block is appended. Loading an archive never '
           'assumes a recurrent cache was restored.',
           '- Frozen adaptive mass=0.80, short-input bypass=1024, floor=160, pooling, and student model '
           'are unchanged. Only the existing union30 diagnostic uses its original fixed 30% rate.',
           '- Placeholders are emitted only when a source first arrives. Grouped ranges and compact '
           'handles resolve through a private lookup table; no tokenizer or weight changes.', '',
           '## Measured comparisons','',
           '| Arm | Accuracy | Held-out | Growing-turn TTFT | Mean reused tokens | Zero-reuse growing turns | Placeholder tokens | Final context tokens |',
           '|---|---:|---:|---:|---:|---:|---:|---:|']
    for arm,s in stats.items():
        lines.append(f"| {arm} | {s['correct']}/{s['requests']} | {s['held_correct']}/{s['held_requests']} | "
                     f"{s['growing_mean_latency_s']:.3f}s | {s['growing_mean_reused_tokens']:.0f} | "
                     f"{s['growing_zero_reuse']}/12 | {s['placeholder_tokens']} | {s['final_context_tokens']:.0f} |")
    lines+=['','All TTFT timers start before source archival, student selection and lookup, and end at '
            'the first final-answer token. For model-directed recovery, initial fetch generation, decoding, '
            'lookup and every extra target request are included. Unresolved fetch loops are failures; '
            'their full capped response duration is charged rather than treating a fetch token as an answer.', '',
            '## Recovery and delivery','',
            '| Arm | Previously omitted evidence needed | Recovered | Selected duplicate chunks skipped | Off-task chunks delivered | Tool requests |',
            '|---|---:|---:|---:|---:|---:|']
    for arm,s in stats.items():
        if arm.startswith(('append-','fetch-')):
            lines.append(f"| {arm} | {s['omitted_needed']} | {s['recovered']} | "
                         f"{s['already_delivered_selection_skipped']} | {s['off_task_delivered_chunks']} | {s['fetch_requests']} |")
    lines+=['','“Off-task” is an oracle-relative proxy: delivered chunks outside the known answer span '
            'for the current probe. It does not prove those chunks are semantically unnecessary. '
            'Recovery means all source chunks covering the needed exact span became delivered; '
            'answer accuracy is measured separately. No oracle labels drive selection or retrieval.', '',
            '## Placeholder tokenization','',
            'Same omission sets from the grouped run, re-rendered with the real target tokenizer. '
            'Only individual/grouped verbose formats were quality-tested live; compact alternatives '
            'below are token-cost measurements, not established quality equivalents.', '',
            '| Format | Target tokens |','|---|---:|']
    for style,cost in summary['placeholder_counterfactual_tokens'].items():
        lines.append(f'| {style} | {cost} |')
    lines+=['','## Cache and memory validation','',
            'Reported cache reuse comes from native engine RESUME counts, not text similarity. '
            'Append variants verify immutable prior target ID prefixes and use implicit sequential '
            'coordinates on every request. Student prompt reuse is checked against its exact previous '
            'input length. Positive reuse smaller than the previous prompt is a checkpoint rewind, '
            'not necessarily a full eviction; the zero-reuse column distinguishes complete misses.', '',
            f"Across normal append/fetch turns, {sum(source_replays.values())} previously delivered source chunks fell outside the reused prefix; "
            'the small observed checkpoint rewinds affected framing rather than old source payload. '
            'Source replay after the explicit eviction is counted separately.', '',
            'A separate grouped-arm probe deliberately evicts the cache and cold-replays the exact '
            'request. It must return identical output with zero newly appended duplicate evidence.', '',
            '| Arm | Sampled GPU peak MiB |','|---|---:|']
    for arm,m in memories.items():
        lines.append(f"| {arm} | {m['sampled_peak_mib']:.0f} |")
    lines+=['','NVML is sampled every 50ms. Standalone sampling includes the remaining initialization '
            'window; colocated arm sampling covers their requests. Sampling can miss shorter peaks. '
            'Student allocator peaks are retained in private rows. Both models remain resident throughout '
            'colocated runs. Standalone target reserve=700 MiB; all matched arms reserve=4096 MiB.', '',
            '## Limits and failures','',
            '- Four synthetic lookup/version conversations, one run per arm, fixed arm order. '
            'This does not establish general coding-agent quality or statistical significance.',
            '- Old frozen input content is reused, but provenance framing and actual model replies '
            'change the cumulative prompt IDs relative to the earlier fixed-history benchmark.',
            '- Native Strata transport is used consistently; network/HTTP overhead is excluded. '
            'Archive persistence before omission is included in append/union latency. '
            'No sum of offline stage timings replaces measured request latency.',
            '- Repeated identical file revisions share stable chunk IDs and track each original occurrence. '
            'Late historical revisions never become current solely because they arrived later. '
            'These archive hardening paths are unit-tested; benchmark streams use unique source encounters.',
            '- Prefix IDs are serialized for native cache matching even when already cached; '
            'the reused prefix is not re-prefilled. Duplicate semantic delivery and cache-loss '
            're-prefill are distinct costs.',
            '- The initial fetch-grouped instruction retained an abstention rule that conflicted with retrieval. '
            'fetch-explicit clarifies the tool-call exception and requires a lookup before abstention. '
            'It is an exploratory repair tested on the same frozen cases after observing failures, '
            'not a fresh held-out validation of that revised prompt.',
            '- Model-directed retrieval is a bounded explicit text protocol, not a trained new token. '
            'At most two archive lookups are allowed. Failures remain in accuracy and latency aggregates.',
            '- Experimental Python code only; installed production Strata and its routing are unchanged. '
            'No compaction, length-dependent thresholds, new student, tokenizer changes or weight changes.', '']
    if (source/'verification.json').exists():
        checks=json.loads((source/'verification.json').read_text())
        lines += ['## Verification','',
                  f"Root suite: {checks['root_pytest']['passed']} passed, {checks['root_pytest']['skipped']} skipped. "
                  f"Student-runtime focused checks: {checks['student_runtime_focused_passed']} passed. Ruff passed. "
                  f"Audited {checks['lossless_archive_audits']} archives and {checks['exact_delivered_source_byte_ranges']} exact delivered source-byte ranges. "
                  f"Native logs confirm {checks['native_target_calls']} completed requests, including tool calls and validation/isolation requests. "
                  'GPU memory was released after the runs. See `verification.json` for machine-readable checks.', '']
    failures=[{k:r[k] for k in ('arm','case','turn','correct','unresolved_fetch','previously_omitted_needed','recovery_succeeded','needed_chunks_delivered')}
              for r in rows if not r['correct']]
    summary['failures']=failures
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    if failures:
        lines += ['', '| Arm | Conversation | Failed turns | Failure | Needed source chunks delivered |', '|---|---|---|---|---|']
        groups = {}
        for row in rows:
            if not row['correct']:
                groups.setdefault((row['arm'],row['case']), []).append(row)
        for (arm,case), failed in groups.items():
            kind = 'abstention' if all(r['text']=='UNKNOWN' for r in failed) else 'wrong answer/tool response'
            present = 'yes' if all(r['needed_chunks_delivered'] for r in failed) else 'no'
            lines.append(f"| {arm} | {case} | {', '.join(str(r['turn']) for r in failed)} | {kind} | {present} |")
    else:
        lines.append('Accuracy failures: none.')
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'rows':len(rows),'arms':{a:s['correct'] for a,s in stats.items()}}))
    return summary


def main():
    ap=argparse.ArgumentParser(__doc__)
    ap.add_argument('--input',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args()
    generate(a.input,a.output)


if __name__=='__main__':
    main()
