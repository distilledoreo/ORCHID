"""Exercise normal session.answer with real student/target on frozen histories."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import time
from pathlib import Path

from memory_gateway.specprefill.cached_student import CachedStudent
from memory_gateway.specprefill.compacting_session import CompactingSession
from memory_gateway.specprefill.compaction_adapters import AdaptiveSelector
from memory_gateway.specprefill.core import compatible_ids, token_hash
from memory_gateway.specprefill.student import Student

from .run import load_frontend
from .targeted_recall import prepare
from .three_way_compaction import MeasuredTarget, clean, score


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--student', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    cfg = json.loads((args.run / 'specprefill32k/engine-config.json').read_text())
    tok, _, vocab = load_frontend(Path(cfg['cwd']), Path(cfg['tokenizer']))
    ready = args.output / 'prepared.json'
    if not ready.exists():
        args.output.mkdir(parents=True, exist_ok=False)
        frozen = args.output / 'frozen'
        frozen.mkdir()
        prompts = prepare(args.run, frozen, tok, snapshots=True)
        ready.write_text(json.dumps({'run': str(args.run), 'turns': [p['turn'] for p in prompts],
            'reconstruction': '22 turns: exact target lengths, compact event, final student/target IDs',
            'native_sha256': hashlib.sha256(Path(cfg['exe']).read_bytes()).hexdigest()}, indent=2))
        print('Frozen pre-answer snapshots prepared and final replay IDs verified.', flush=True)
    case = json.loads((args.run / 'case.json').read_text())
    if args.prepare_only:
        return
    if (args.output / 'results.jsonl').exists():
        raise FileExistsError('Retain results and use a fresh output directory')
    assert json.loads(ready.read_text())['run'] == str(args.run)
    from serve.server import StrataEngine, child_env
    env = child_env(cfg)
    env.update(STRATA_ROUTE_TAIL_SKIP='0', STRATA_PREFILL_CPU_SHARE='0')
    engine = StrataEngine(cfg['exe'], cfg['args'], cwd=cfg['cwd'],
                         log=str(args.output / 'engine.log'), env=env)
    selector = None
    results = []
    try:
        allowed = compatible_ids(vocab, json.loads((args.student / 'tokenizer.json').read_text()))
        selector = AdaptiveSelector(CachedStudent(Student(args.student, allowed), block=512))
        target = MeasuredTarget(engine, tok, 32768, args.output)
        for number in (4, 19, 21):
            original = args.output / 'frozen' / f'turn-{number}'
            working = args.output / f'live-turn-{number}'
            shutil.copytree(original, working)
            def no_compaction(request):
                raise AssertionError('Targeted answer must not change the frozen compaction history')
            session = CompactingSession.restore(working / 'replay.json', tok, selector, target, no_compaction)
            session.exact_lookup_enabled = True
            question = case['turns'][number - 1]['question']
            traces, target_prompts = [], []
            actual_trace = session._trace
            def trace(ids):
                result = actual_trace(ids)
                traces.append({'input_ids': list(ids), 'input_hash': token_hash(ids), **result})
                return result
            session._trace = trace
            def capture_target(ids, maximum):
                target_prompts.append(list(ids))
                return target(ids, maximum)
            capture_target.stop_ids = target.stop_ids
            session.target = capture_target
            before_student, before_target = list(session.student_ids), list(session.target_ids)
            target.turn, target.phase = number - 1, 'answer'
            target.first, target.turn_started = None, time.perf_counter()
            try:
                output = session.answer(question, maximum=64)
                elapsed = time.perf_counter() - target.turn_started
                assert session.student_ids[:len(before_student)] == before_student
                assert session.target_ids[:len(before_target)] == before_target
                event = session.events[-1]
                assert event['exact_lookup']['status'] == 'delivered'
                assert all(len(t['input_ids']) + 64 <= 32768 for t in traces)
                assert len(target_prompts) == 1 and len(target_prompts[0]) + 64 <= 32768
                text = clean(tok.decode(output))
                result = {'turn': number, 'question': question, 'text': text,
                          'expected': case['turns'][number - 1]['expected'],
                          **score(text, case['turns'][number - 1]['expected']),
                          'wall_s': elapsed, 'student_passes': len(traces),
                          'append_only_prefixes_verified': True, 'event': event}
                with gzip.open(working / 'traces.json.gz', 'wt') as f:
                    json.dump({'student': traces, 'target_input_ids': target_prompts[0]}, f)
                results.append(result)
                with (args.output / 'results.jsonl').open('a') as f:
                    f.write(json.dumps(result) + '\n')
                print(json.dumps({k: v for k, v in result.items() if k != 'event'}), flush=True)
            finally:
                session.close()
                selector.reset()
        (args.output / 'summary.json').write_text(json.dumps(results, indent=2))
    finally:
        if selector is not None:
            selector.reset()
        engine.close()


if __name__ == '__main__':
    main()
