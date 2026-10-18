"""Summarize a completed, paired three-way compaction smoke comparison."""
import argparse
import json
import statistics
from pathlib import Path

from .three_way_compaction import ARMS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    meta = json.loads((args.run/'metadata.json').read_text())
    case = json.loads((args.run/'case.json').read_text())
    summaries, records = {}, {}
    for arm in ARMS:
        path = args.run/arm/'summary.json'
        summaries[arm] = json.loads((path if path.exists() else args.run/arm/'failure.json').read_text())
        records[arm] = [json.loads(line) for line in (args.run/arm/'turns.jsonl').read_text().splitlines()]
        assert len(records[arm]) == summaries[arm]['turns']
        if summaries[arm].get('status') != 'failed':
            assert len(records[arm]) == len(case['turns'])
        for i, record in enumerate(records[arm]):
            assert record['expected'] == case['turns'][i]['expected']
            assert record['question'] == case['turns'][i]['question']
    lines = ['# Cached Flash-Next: full context vs normal vs SpecPrefill compaction', '',
        'Cached 32K full context was fastest and answered every scored value correctly. '
        'Normal 8K compaction completed with lower recall and 2.38× the total time. '
        'The current SpecPrefill path failed before turn 9 and also trailed both controls '
        'on the matched first eight turns; it has no completed-session score.', '',
        f'One deterministic {meta["turns"]}-turn conversation, {meta["source_tokens"]:,} original source tokens. '
        f'The conservative full-context bound, including capped replies, is '
        f'{meta["max_full_context_tokens_including_64_token_answers"]:,} tokens. All arms use the same '
        'Flash-Next executable/model, greedy answers capped at 64 tokens, sequential positions, and prefix caching. '
        'SpecPrefill additionally uses the existing 0.8B student and paged archive.', '',
        '| Method | Outcome | Values correct | Late recall | Total turn time | Mean end-to-end TTFT | Compactions |',
        '|---|---|---:|---:|---:|---:|---:|']
    for arm in ARMS:
        s = summaries[arm]
        failed = s.get('status') == 'failed'
        outcome = f'Failed before turn {s["failure_turn"]}' if failed else 'Completed 22 turns'
        late = 'Not reached' if failed else f'{s["late_correct_fields"]}/{s["late_fields"]}'
        lines.append(f'| {arm} | {outcome} | {s["correct_fields"]}/{s["fields"]}'
                     f'{" (partial)" if failed else ""} | {late} '
                     f'| {"≥" if failed else ""}{s["total_wall_s"]:.1f}s '
                     f'| {s["mean_e2e_ttft_s"]:.2f}s{" (partial)" if failed else ""} | {s["compactions"]} |')
    lines += ['', '**The failed arm has no completed 22-turn accuracy or latency score.** Its time is a '
              'lower bound including completed turns and the failed summary call; its TTFT average '
              'covers completed answers only. The failure was `Compaction would orphan archive references`. '
              'The generated summary returned archive IDs instead of the requested M IDs and omitted '
              'required coverage. The runtime rejected it without advancing the committed generation.', '',
              '## Matched prefix before the failure', '',
              '| Method | First 8 turns: correct values | First 8 turns: total time | Compactions |',
              '|---|---:|---:|---:|']
    for arm in ARMS:
        rows = records[arm][:8]
        lines.append(f'| {arm} | {sum(r["correct_fields"] for r in rows)}/16 '
                     f'| {sum(r["wall_s"] for r in rows):.1f}s | {rows[-1]["compactions"]} |')
    lines += ['', 'Timing starts before turn admission/compaction and ends after the final answer and session '
              'persistence. End-to-end TTFT includes preceding compaction, archive indexing/search, '
              'and student work. Engine/model load and identical short warmups are excluded. '
              'The normal summary and SpecPrefill summary budgets are both 1,024 tokens; SpecPrefill '
              'uses the existing 3,072-token total compacted seed and 2,048-token expansion budgets. '
              'Compaction is scheduled by actual headroom and can occur on different turns. '
              'This is a native session harness, not an HTTP gateway benchmark: full/normal histories '
              'remain in memory, while SpecPrefill includes its paged archive and checkpoint costs. '
              'HTTP/network and gateway request-ledger time are excluded. All native engines use '
              'the same 4 GiB VRAM reserve and run sequentially on the P100.', '',
              '| Method | Median end-to-end TTFT | Mean answer backend TTFT | Target maintenance | Reused answer prefix tokens (sum) | Largest native input |',
              '|---|---:|---:|---:|---:|---:|']
    for arm in ARMS:
        s, rows = summaries[arm], records[arm]
        calls = [json.loads(line) for line in (args.run/arm/'calls.jsonl').read_text().splitlines()]
        lines.append(f'| {arm} | {s["median_e2e_ttft_s"]:.2f}s '
                     f'| {statistics.mean(r["target_ttft_s"] for r in rows):.2f}s '
                     f'| {s["maintenance_s"]:.1f}s | {s["reused_answer_tokens"]:,} '
                     f'| {max(c["input_tokens"] for c in calls):,} |')
    lines += ['', '## Quality by task', '',
              'SpecPrefill counts cover its completed prefix only; differing denominators are not a full-session ranking.', '',
              '| Task | full32k | summary8k | specprefill8k (partial) |', '|---|---:|---:|---:|']
    for kind in dict.fromkeys(t['kind'] for t in case['turns']):
        parts = []
        for arm in ARMS:
            rows = [r for r in records[arm] if r['kind'] == kind]
            parts.append(f'{sum(r["correct_fields"] for r in rows)}/{sum(r["fields"] for r in rows)}'
                         if rows else 'Not reached')
        lines.append(f'| {kind} | '+ ' | '.join(parts)+' |')
    lines += ['', '## Differences from the full-context control', '']
    for i, full in enumerate(records['full32k']):
        if any(i >= len(records[arm]) or records[arm][i]['text'] != full['text'] for arm in ARMS[1:]):
            lines.append(f'- Turn {i+1}, {full["kind"]}: expected `{full["expected"]}`; '
                         + '; '.join(f'{arm}: `{records[arm][i]["text"]}`' if i < len(records[arm])
                                     else f'{arm}: not reached' for arm in ARMS)+'.')
    lines += ['', '## Interpretation limits', '',
              'This is one synthetic code/configuration conversation with 44 scored fields, run once per method '
              'in the stated order. It measures exact revision lookup and simple linked-field reasoning, '
              'not broad coding ability or prose quality. Each arm retains its own actual answers. '
              'There are no confidence intervals or order-balanced repetitions. Summary generation and '
              'archive selection can lose or fail to recover facts; on-disk lossless storage does not imply '
              'lossless model answers. No earlier accuracy/latency baseline was changed.', '',
              'The initial SpecPrefill attempt stopped before turn 8 because summary text plus '
              '13 provenance addresses exceeded the rendered 1,024-token budget. The controller '
              'was fixed to coalesce adjacent summary items, retaining all text and references '
              'without another model call. SpecPrefill was then rerun from turn 1. The two '
              'completed controls were copied unchanged. The failed attempt is retained in '
              '`../run-v2/specprefill8k`; its partial timings are excluded from this comparison. '
              'The student uses the existing reference PyTorch recurrent kernels; no runtime '
              'packages or kernels were changed for this test.', '',
              f'Raw case, configuration, summaries, native-call metrics, answers, and engine logs: '
              f'[{args.run}]({args.run}).', '', f'Case SHA256: `{meta["case_sha256"]}`.', '']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text('\n'.join(lines))
    print(json.dumps(summaries, indent=2))


if __name__ == '__main__':
    main()
