"""Audit matched provenance schedules; report quality, latency, and framing overhead."""
from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter
from pathlib import Path

from . import cache_experiment as api
from .append_experiment import SYSTEM, encode, finish_reply, question_end, role
from .core import token_hash
from .omission_archive import OmissionArchive
from .provenance_experiment import BASE, STYLES, add_sources, digest, render_evidence


def require(condition, message):
    if not condition:
        raise ValueError(message)


def mean(values):
    values = list(values)
    return statistics.mean(values) if values else None


def metrics(rows):
    growing = [r for r in rows if r['turn'] > 0]
    return {'turns':len(rows), 'correct':sum(r['correct'] for r in rows),
            'stale_version_errors':sum(r['stale_version_error'] for r in rows),
            'wrong_source_errors':sum(r['wrong_source_error'] for r in rows),
            'unknown':sum(r['unknown'] for r in rows),
            'mean_target_ttft_s':mean(r['target_ttft_s'] for r in rows),
            'mean_delivery_ttft_s':mean(r['delivery_ttft_s'] for r in rows),
            'growing_target_ttft_s':mean(r['target_ttft_s'] for r in growing),
            'growing_delivery_ttft_s':mean(r['delivery_ttft_s'] for r in growing),
            'evidence_tokens':sum(r['evidence_tokens'] for r in rows),
            'extra_vs_bare_tokens':sum(r['extra_vs_bare_tokens'] for r in rows),
            'growing_zero_reuse':sum(r['target_reused']==0 for r in growing),
            'mean_final_context_tokens':mean(r['target_prompt_tokens'] for r in rows if r['turn'] == max(x['turn'] for x in rows if x['case']==r['case']))}


def generate(source, output):
    raw = (source/'cases.json').read_bytes()
    protocol = json.loads((source/'protocol.json').read_text())
    complete = json.loads((source/'COMPLETE.json').read_text())
    require(digest(raw) == protocol['cases_sha256'] == complete['cases_sha256'], 'Frozen inputs changed')
    require(complete['styles'] == list(STYLES), 'Incomplete styles')
    cases = json.loads(raw)['cases']
    rows = [json.loads(line) for line in (source/'requests.jsonl').read_text().splitlines()]
    expected = Counter((style, c['id'], i) for style in STYLES for c in cases for i in range(len(c['turns'])))
    require(Counter((r['style'], r['case'], r['turn']) for r in rows) == expected, 'Missing/duplicate trial rows')
    lookup = {(r['style'], r['case'], r['turn']):r for r in rows}
    tok, _, _ = api.load_frontend(api.STRATA, api.TOKENIZER)
    matched, spans, visible_fresh = 0, 0, 0
    source_replays = Counter()
    for case in cases:
        for i, turn in enumerate(case['turns']):
            rr = [lookup[style, case['id'], i] for style in STYLES]
            require(all(r['payload_sha256'] == turn['payload_sha256'] for r in rr), 'Payload hash differs')
            require(all(r['delivered_handles'] == turn['delivered_handles'] for r in rr), 'Delivery order differs')
            matched += 1
        for style in STYLES:
            session = source/style/case['id']
            archived = OmissionArchive.load(session/'archive.json', tok)
            saved = json.loads((session/'transcript.json').read_text())['target_ids']
            sequence = role(tok, 'system')+encode(tok, SYSTEM)+encode(tok, '<|im_end|>\n', True)
            audit = OmissionArchive(tok)
            for i, turn in enumerate(case['turns']):
                row = lookup[style, case['id'], i]
                source_replays[style] += sum(d['end'] > row['target_reused'] for d in audit.delivered.values())
                add_sources(audit, turn, i)
                block, placements = render_evidence(audit, turn['deliver'], style)
                sequence.extend(role(tok, 'user'))
                audit.commit(placements, len(sequence), block)
                sequence.extend(encode(tok, block))
                sequence.extend(question_end(tok, turn['question']))
                require(token_hash(sequence) == row['target_input_hash'], 'Exact prompt reconstruction failed')
                require(row['prefix_exact'], 'Append-only invariant failed')
                require(len(sequence) == row['target_prompt_tokens'], 'Prompt token count changed')
                require(saved[:len(sequence)] == sequence, 'Cached prefix rewritten')
                correct = re.findall(r'\d{5}', row['text']) == [turn['answer']]
                require(correct == row['correct'], 'Correctness grade mismatch')
                if case['split'] == 'fresh-controlled':
                    name = re.search(r'What does (\w+)\(\)', turn['question'])[1]
                    src = audit.sources[audit.source_id(turn['evidence']['path'], turn['evidence']['version'])]
                    value = re.search(r'^def '+re.escape(name)+r'\(\):\n    return (\d{5})', src['text'], re.M)[1]
                    require(value == turn['answer'], 'Independent source ground truth mismatch')
                    needed = re.search(r'^def '+re.escape(name)+r'\(\):\n    return \d{5}', src['text'], re.M)[0].encode()
                    prompt_bytes = b''.join(tok.token_bytes(t) for t in sequence)
                    require(needed in prompt_bytes, 'Required fresh evidence unavailable to target')
                    visible_fresh += 1
                finish_reply(tok, sequence, row['output_ids'])
            require(sequence == saved, 'Saved final transcript mismatch')
            require(audit.delivered == archived.delivered or
                    all((audit.delivered[h]['start'], audit.delivered[h]['end']) ==
                        (archived.delivered[h]['start'], archived.delivered[h]['end']) for h in audit.delivered),
                    'Delivery span mismatch')
            for handle, span in archived.delivered.items():
                payload = b''.join(tok.token_bytes(t) for t in saved[span['start']:span['end']])
                require(archived.chunks[handle].text.encode() in payload, 'Delivered source bytes mismatch')
                spans += 1
            deliveries = [h for e in archived.events if e['event']=='deliver' for h in e['handles']]
            require(len(deliveries) == len(set(deliveries)), 'Duplicate semantic delivery')
    stats = {style:{split:metrics([r for r in rows if r['style']==style and (split=='all' or r['split']==split)])
                    for split in ('all', 'existing-selector-replay', 'fresh-controlled')}
             for style in STYLES}
    critical = {style:metrics([r for r in rows if r['style']==style and r['split']=='fresh-controlled' and r['turn']>=2])
                for style in STYLES}
    per_case = {c['id']:{style:sum(lookup[style, c['id'], i]['correct'] for i in range(len(c['turns'])))
                         for style in STYLES} for c in cases}
    summary = {'protocol':protocol, 'cases':len(cases), 'rows':len(rows), 'matched_evidence_schedules':matched,
               'prompt_hashes_verified':len(rows), 'exact_source_byte_spans':spans,
               'fresh_prompts_with_full_required_function':visible_fresh,
               'previously_delivered_chunks_outside_reused_prefix':dict(source_replays), 'metrics':stats,
               'fresh_conflict_turns':critical, 'per_case_correct':per_case}
    lines = ['# Matched provenance experiment', '',
             'Bare, minimal, and detailed evidence frames receive identical archived source bytes and delivery '
             'order on every turn. Four existing selector-driven conversation schedules are replayed; four fresh '
             'controlled conversations test cross-file/version ambiguity, delayed old evidence, current-version '
             'queries, and repeated queries after a stale revision arrives. Each arm retains its own actual replies.', '',
             'Bare frames contain only a generic EVIDENCE delimiter and exact content. Minimal frames add file, '
             'version, and current/historical status. Detailed frames use our existing source ID, file, version, '
             'chunk/byte ranges, original turn, and supersession metadata. No omission placeholders, separate '
             'source-status notices, or retrieval tools are enabled in any arm. Metadata embedded in original '
             'source content is preserved in all arms.', '',
             '| Format | All correct | Existing replay | Fresh controlled | Fresh conflict turns | Stale-version errors (fresh) | UNKNOWN responses |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for style in STYLES:
        m = stats[style]
        def fraction(x):
            return f"{x['correct']}/{x['turns']}"
        lines.append(f"| {style} | {fraction(m['all'])} | {fraction(m['existing-selector-replay'])} | "
                     f"{fraction(m['fresh-controlled'])} | {fraction(critical[style])} | {m['fresh-controlled']['stale_version_errors']} | {m['all']['unknown']} |")
    lines.extend(['', '| Format | Growing target TTFT | Growing delivery TTFT | Evidence tokens | Extra tokens vs bare | Growing turns with zero reuse |',
                  '|---|---:|---:|---:|---:|---:|---:|'])
    for style in STYLES:
        m = stats[style]['all']
        lines.append(f"| {style} | {m['growing_target_ttft_s']:.3f} s | {m['growing_delivery_ttft_s']:.3f} s | "
                     f"{m['evidence_tokens']} | {m['extra_vs_bare_tokens']} | {m['growing_zero_reuse']} |")
    lines.extend(['', 'Detailed provenance was the most reliable format on this set. Minimal saved framing tokens '
                  'but did not match detailed accuracy; keep the existing detailed default for now. This compares '
                  'metadata bundles, so it does not establish which extra field (source ID, turn, ranges, or '
                  'supersession) contributed to the difference.', '',
                  'Target TTFT measures native inference through its first answer token. Delivery TTFT also '
                  'includes source archival, rendering, and tokenization. Frozen selector planning and model '
                  'startup are excluded; these numbers must not be compared directly with the earlier end-to-end '
                  'student-plus-target timings. Qwen3.5-0.8B remains resident alongside the same Flash-Next Swift '
                  'IQ3_S target on the P100 16GB; target prefix caching is enabled. Format order rotates by conversation.', '',
                  f'Audits: {matched} identical evidence schedules, {len(rows)} exact prompt reconstructions, '
                  f'{spans} exact delivered source byte spans, no duplicate deliveries. All {visible_fresh} fresh '
                  'prompts contain the complete required function definition, independently checked against the '
                  'requested source revision. Failures on those probes cannot be explained by an omitted answer span. '
                  f'Previously delivered source chunks outside the reused native prefix: {dict(source_replays)}.', '',
                  '| Conversation | Bare correct | Minimal correct | Detailed correct |', '|---|---:|---:|---:|'])
    for case, values in per_case.items():
        lines.append(f"| {case} | {values['bare']} | {values['minimal']} | {values['detailed']} |")
    lines.extend(['', 'Limitations: eight synthetic conversations, one greedy sample per format/turn, no real coding '
                  'edits or tool-use workload. Fresh controlled schedules intentionally create ambiguous revisions '
                  'without labels; they isolate provenance usefulness rather than estimate ordinary production '
                  'failure rates. Selection is held fixed, not independently reevaluated by each student. '
                  'Actual target answers can change later histories. Small latency differences need repeated '
                  'measurements before being treated as meaningful. No prompts, raw responses, or machine paths '
                  'are exported in this report.'])
    output.mkdir(parents=True, exist_ok=True)
    (output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    return summary


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--source', type=Path, default=BASE)
    ap.add_argument('--output', type=Path, default=api.ROOT/'artifacts/strata-specprefill/provenance-v1')
    a = ap.parse_args()
    summary = generate(a.source, a.output)
    print(json.dumps({'rows':summary['rows'], 'metrics':summary['metrics']}))


if __name__ == '__main__':
    main()
