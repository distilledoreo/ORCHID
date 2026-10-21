"""Frozen-history counterfactuals for explicit source and revision retrieval.

Reconstructs the saved student/target histories using logged chunk decisions,
not new student predictions. Only the three requested target answers are live.
This is an experimental delivery test, not a production selector change.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from memory_gateway.specprefill.compacting_session import CompactingSession, Limits, SummaryEntry
from memory_gateway.specprefill.compaction_adapters import NativeSummarizer
from memory_gateway.specprefill.core import token_hash
from memory_gateway.specprefill.exact_lookup import exact_block, references  # noqa: F401

from .run import load_frontend
from .three_way_compaction import MeasuredTarget, score


def rows(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def prepare(run, output, tok, *, snapshots=False):
    """Replay exact logged chunk choices; verify every target length and final IDs."""
    arm = run / 'specprefill32k'
    case = json.loads((run / 'case.json').read_text())
    assert hashlib.sha256((run / 'case.json').read_bytes()).hexdigest() == json.loads(
        (run / 'metadata.json').read_text())['case_sha256']
    with sqlite3.connect(f'file:{arm}/session.archive.sqlite3?mode=ro', uri=True) as db:
        events = [json.loads(x[0]) for x in db.execute(
            "SELECT value FROM events WHERE kind='session' ORDER BY seq")]
        state = json.loads(db.execute('SELECT value FROM session_state').fetchone()[0])
        bodies = {p: b.decode() for p, b in db.execute('SELECT path,body FROM sources')}
    answers = [e for e in events if e['kind'] == 'answer']
    compacts = iter(e for e in events if e['kind'] == 'compact')
    turns = rows(arm / 'turns.jsonl')
    calls = [c for c in rows(arm / 'calls.jsonl') if c['turn'] >= 0]
    summaries, probes = {}, {}
    for r in rows(arm / 'summaries.jsonl'):
        text = re.sub(r'<\|[^>]+\|>', '', r['text']).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            summaries[r['turn']] = data
        else:
            probes[r['turn']] = list(tok.encode(r['text'], parse_special=True))
    live_calls = iter(c for c in calls if not (c['phase'] == 'compact' and c['output_tokens'] > 1))
    captured = []
    phase, turn, bound_index, compact_event = 'answer', 0, 0, None

    def frozen_target(ids, maximum):
        c = next(live_calls)
        assert c['turn'] == turn and c['input_tokens'] == len(ids)
        if phase == 'compact':
            result = probes[turn]
        else:
            result = list(tok.encode(bodies[f'conversation/{turn}/assistant'], parse_special=True))
            if turn + 1 in (4, 19, 21):
                q = case['turns'][turn]['question']
                sources = {(s['path'], s['version']): dict(s) for s in
                           (session.archive.sources[sid] for sid in session.archive.sources)
                           if s['path'].startswith('config/')}
                block, units = exact_block(q, sources, session._encode)
                query = session._query(q)
                assert list(ids[-len(query):]) == query
                extra = session._frame('user', block)
                enhanced = list(ids[:-len(query)]) + extra + query
                assert len(extra) <= session.limits.expansion_tokens
                assert len(enhanced) + 64 <= session.limits.target_context
                captured.append({'turn': turn + 1, 'question': q, 'expected': turns[turn]['expected'],
                                 'previous_text': turns[turn]['text'], 'baseline_ids': list(ids),
                                 'enhanced_ids': enhanced, 'units': units, 'evidence_tokens': len(extra),
                                 'baseline_hash': token_hash(ids), 'enhanced_hash': token_hash(enhanced)})
        assert len(result) == c['output_tokens']
        return result

    summarizer = NativeSummarizer(frozen_target, tok, context_limit=32768, output_tokens=1024)

    def frozen_summary(request):
        c = next(c for c in calls if c['turn'] == turn and c['phase'] == 'compact'
                 and c['output_tokens'] > 1)
        assert len(summarizer.input_ids(request)) == c['input_tokens']
        return [SummaryEntry(x['text'], tuple(x['references'])) for x in summaries[turn]]

    session = CompactingSession(tok, lambda ids: {'positions': [], 'scores': [0.] * len(ids)},
                                frozen_target, frozen_summary, system=case['system'],
                                limits=Limits(**state['limits']), checkpoint=output / 'replay.json',
                                exact_lookup=False)

    def frozen_bound(handles, **kwargs):
        nonlocal bound_index
        if phase == 'compact':
            return list(compact_event['exact'])
        field = ('expanded', 'delivered')[bound_index]
        bound_index += 1
        return list(answers[turn][field])

    session._bounded = frozen_bound
    try:
        for turn, item in enumerate(case['turns']):
            if turns[turn]['compacted_this_turn']:
                phase, compact_event = 'compact', next(compacts)
                assert session.compact(item['question']) == compact_event
            phase, bound_index = 'answer', 0
            if item['source']:
                s = item['source']
                session.add_source(s['path'], s['version'], s['text'],
                                   cue='Configuration code, exact revision fields and ROUTE_KEY linkage.')
            if snapshots and turn + 1 in (4, 19, 21):
                directory = output / f'turn-{turn + 1}'
                directory.mkdir()
                session._save()
                for source_db, name in ((session.archive.db, 'replay.archive.sqlite3'),
                                        (session.search.db, 'replay.search.sqlite3')):
                    with sqlite3.connect(directory / name) as copy:
                        source_db.backup(copy)
                (directory / 'replay.json').write_bytes(session.checkpoint.read_bytes())
            session.answer(item['question'], maximum=64)
            assert bound_index == 2
            assert session.events[-1]['student_tokens'] == answers[turn]['student_tokens']
            assert session.events[-1]['target_tokens'] == answers[turn]['target_tokens']
        assert session.student_ids == state['student_ids']
        assert session.target_ids == state['target_ids']
    finally:
        session.close()
    return captured


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--prepare-only', action='store_true')
    args = p.parse_args()
    cfg = json.loads((args.run / 'specprefill32k/engine-config.json').read_text())
    tok, _, _ = load_frontend(Path(cfg['cwd']), Path(cfg['tokenizer']))
    prepared = args.output / 'prompts.json.gz'
    if not prepared.exists():
        args.output.mkdir(parents=True, exist_ok=False)
        prompts = prepare(args.run, args.output, tok)
        with gzip.open(prepared, 'wt') as f:
            json.dump(prompts, f)
        (args.output / 'validation.json').write_text(json.dumps({
            'source_run': str(args.run), 'turns_replayed': 22, 'all_target_lengths_exact': True,
            'final_student_ids_exact': True, 'final_target_ids_exact': True,
            'student_decisions': 'Frozen logged expanded/delivered handles; no student inference',
            'prompt_file_sha256': hashlib.sha256(prepared.read_bytes()).hexdigest()}, indent=2))
        print('Reconstructed all 22 turns; final student and target IDs exactly match.', flush=True)
    else:
        validation = json.loads((args.output / 'validation.json').read_text())
        if validation['source_run'] != str(args.run):
            raise ValueError('Prepared prompts belong to another source run')
        if validation['prompt_file_sha256'] != hashlib.sha256(prepared.read_bytes()).hexdigest():
            raise ValueError('Prepared prompt checksum mismatch')
        with gzip.open(prepared, 'rt') as f:
            prompts = json.load(f)
    if args.prepare_only:
        return
    if (args.output / 'results.jsonl').exists():
        raise FileExistsError('Retain previous results and use a fresh output directory')
    from serve.server import StrataEngine, child_env
    env = child_env(cfg)
    env.update(STRATA_ROUTE_TAIL_SKIP='0', STRATA_PREFILL_CPU_SHARE='0')
    engine = StrataEngine(cfg['exe'], cfg['args'], cwd=cfg['cwd'],
                         log=str(args.output / 'engine.log'), env=env)
    results = []
    try:
        target = MeasuredTarget(engine, tok, 32768, args.output)
        import time
        for prompt in prompts:
            for variant in ('baseline', 'enhanced'):
                target.turn, target.phase = prompt['turn'] - 1, variant
                target.first, target.turn_started = None, time.perf_counter()
                ids = prompt[variant + '_ids']
                result = target(ids, 64)
                text = re.sub(r'<\|[^>]+\|>', '', tok.decode(result)).strip()
                row = {'turn': prompt['turn'], 'variant': variant, 'text': text,
                       'expected': prompt['expected'], **score(text, prompt['expected']),
                       'prompt_tokens': len(ids), 'prompt_hash': token_hash(ids),
                       'evidence_tokens': prompt['evidence_tokens'] if variant == 'enhanced' else 0,
                       'wall_s': time.perf_counter() - target.turn_started,
                       'reproduced_previous': text == prompt['previous_text'] if variant == 'baseline' else None}
                results.append(row)
                with (args.output / 'results.jsonl').open('a') as f:
                    f.write(json.dumps(row) + '\n')
                print(json.dumps(row), flush=True)
        (args.output / 'summary.json').write_text(json.dumps(results, indent=2))
    finally:
        engine.close()


if __name__ == '__main__':
    main()
