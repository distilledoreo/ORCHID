"""Compare a completed cached 32K SpecPrefill follow-up to its frozen control."""
import argparse
import json
from pathlib import Path


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert (args.baseline/'case.json').read_bytes() == (args.run/'case.json').read_bytes()
    meta = json.loads((args.run/'metadata.json').read_text())
    old_meta = json.loads((args.baseline/'metadata.json').read_text())
    assert meta['case_sha256'] == old_meta['case_sha256']
    assert meta['native_sha256'] == old_meta['native_sha256']
    result = {}
    for arm, root in (('full32k',args.baseline),('specprefill32k',args.run)):
        summary = json.loads((root/arm/'summary.json').read_text())
        turns = rows(root/arm/'turns.jsonl')
        calls = [r for r in rows(root/arm/'calls.jsonl') if r['turn'] >= 0]
        assert len(turns) == summary['turns'] == 22
        assert all(c['input_tokens']+c['output_tokens'] <= 32768 for c in calls)
        result[arm] = {'summary':summary,'turns':turns,'calls':calls}
    baseline, spec = result['full32k'], result['specprefill32k']
    for a,b in zip(baseline['turns'],spec['turns'],strict=True):
        assert a['question'] == b['question'] and a['expected'] == b['expected']
    ratio = spec['summary']['total_wall_s']/baseline['summary']['total_wall_s']
    lines = ['# Cached 32K SpecPrefill follow-up', '',
        f'The SpecPrefill run completed all 22 turns with '
        f'{spec["summary"]["correct_fields"]}/44 values correct and '
        f'{spec["summary"]["compactions"]} compaction'
        f'{"s" if spec["summary"]["compactions"] != 1 else ""}. Its total measured time was '
        f'{ratio:.2f}× the cached full-context control.', '',
        '| Method | Correct values | Exact turns | Late recall | Total time | Mean end-to-end TTFT | Compactions |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for arm, item in result.items():
        s = item['summary']
        lines.append(f'| {arm} | {s["correct_fields"]}/44 | {s["exact_turns"]}/22 '
                     f'| {s["late_correct_fields"]}/8 | {s["total_wall_s"]:.1f}s '
                     f'| {s["mean_e2e_ttft_s"]:.2f}s | {s["compactions"]} |')
    lines += ['', '## Setup', '',
        f'The frozen conversation has {meta["source_tokens"]:,} source tokens. Its conservative '
        f'full-context bound, including maximum-length replies, is '
        f'{meta["max_full_context_tokens_including_64_token_answers"]:,} tokens. '
        'The case and native executable hashes match the earlier control. Flash-Next runs on '
        'the same P100 with a 32,768-token context and resident KV capacity, prompt-cache=6, '
        'sequential positions, greedy answers capped at 64 tokens, and a 4 GiB VRAM reserve. '
        'The student also has a 32,768-token limit and uses the existing cached adaptive selector.', '',
        f'The student prefill block is {meta["student_prefill_block"]} tokens, with '
        '`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` to reduce fragmentation. '
        'The first attempt with 1,024-token blocks ran out of GPU memory before turn 17 '
        'after answering 31/32 values correctly. A 512-token-block attempt with the default '
        'allocator failed during lookahead before turn 18 after answering 33/34 correctly. '
        'The smaller block and allocator reduce temporary memory pressure; model weights, '
        'selection policy, keep-rate rules, target configuration, and input case are unchanged. '
        'The reported run restarted at turn 1. The failed attempt is retained separately; its '
        'partial timings are excluded. Runtime settings are recorded in `runtime.json`.', '',
        'Times include admission, student work, archive/index access, any compaction/rebuild, '
        'and session persistence. Loading and warmup are excluded. This is a native session '
        'harness, not an HTTP gateway measurement. The control is the completed earlier run, '
        'not an additional control rerun. Each arm keeps its own actual replies.', '',
        'The headroom policy triggered compaction on turn 20. The summary request itself '
        'was 31,590 tokens; adding the 1,024-token summary output reserve and admission '
        'allowances exceeded the 32,768-token budget. The preceding student prompt was '
        'only 28,653 tokens: this was summary headroom exhaustion, not exhaustion of '
        'the original conversation window. The first selection miss occurred on turn 4, '
        'before compaction.', '',
        '## Cache and prefill measurements', '',
        '| Method | Answer backend TTFT, mean | Outside native calls | Answer prefix reuse (sum) | Native prompt tokens read (sum) | Largest native input |',
        '|---|---:|---:|---:|---:|---:|']
    for arm,item in result.items():
        s, turns, calls = item['summary'],item['turns'],item['calls']
        outside = sum(r['wall_s']-r['native_calls_s'] for r in turns)
        read = sum(c['engine'].get('prompt_read', c['input_tokens']-c['engine'].get('reused',0)) for c in calls)
        lines.append(f'| {arm} | {sum(r["target_ttft_s"] for r in turns)/len(turns):.2f}s '
                     f'| {outside:.1f}s | {s["reused_answer_tokens"]:,} | {read:,.0f} '
                     f'| {max(c["input_tokens"] for c in calls):,} |')
    lines += ['', 'Outside-native time includes the student, indexing, framing, and persistence; '
              'it is not an isolated student-kernel timer.', '', '## Answers differing from the control', '']
    for a,b in zip(baseline['turns'],spec['turns'],strict=True):
        if a['text'] != b['text']:
            lines.append(f'- Turn {a["turn"]}, {a["kind"]}: expected `{a["expected"]}`; '
                         f'control `{a["text"]}`; SpecPrefill `{b["text"]}`.')
    lines += ['', 'One synthetic code/configuration conversation, one completed run per method; '
              'no order-balanced repeats or confidence intervals. Exact value recall and linked '
              'revision lookups do not establish broad coding or prose quality.', '',
              f'Raw follow-up artifacts: [{args.run}]({args.run}).', '',
              f'Case SHA256: `{meta["case_sha256"]}`.', '']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text('\n'.join(lines))
    print(json.dumps({arm:item['summary'] for arm,item in result.items()},indent=2))


if __name__ == '__main__':
    main()
