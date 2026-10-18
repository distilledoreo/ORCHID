"""Explicit, isolated multi-turn cache experiment; private prompts/results stay in data/."""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from pathlib import Path

from .core import token_hash
from .run import load_frontend, prepare

ROOT = Path(__file__).resolve().parents[2]
STRATA = Path.home() / 'AI/Strata'
TOKENIZER = STRATA.parent / 'Strata-data/packs/swift-iq3_s/tokenizer'
STUDENT = None
CONFIG = ROOT / 'data/strata_specprefill/longctx-16kctx-alone.json'
BASE = ROOT / 'data/strata_specprefill/cache-v1'


def prepare_sets(out):
    tok, template, _ = load_frontend(STRATA, TOKENIZER)
    sessions = []
    for sid in range(2):
        answers = [str(61327 + sid * 123 + i * 237) for i in range(4)]
        facts = '\n'.join(f'Record KEY_{sid}_{i} has access code {a}.' for i, a in enumerate(answers))
        messages = [{'role': 'system', 'content': 'Read the records. Answer the requested access code with only its five digits.'}]
        turns = []
        for turn, growth in enumerate([1500, 500, 1000, 2000]):
            content = facts + '\n' if turn == 0 else ''
            before = len(prepare({'messages': messages}, tok, template)) if turn else 0
            n = 0
            while True:
                question = f'\nWhat is the access code for record KEY_{sid}_{turn}? Reply with only its code.'
                trial = messages + [{'role': 'user', 'content': content + question}]
                ids = prepare({'messages': trial}, tok, template)
                if len(ids) - before >= growth:
                    break
                content += f'Unrelated log {sid}-{turn}-{n}: green triangle, blue square, routine operation completed.\n'
                n += 1
            messages = trial
            turns.append({'turn': turn, 'input_ids': ids, 'input_hash': token_hash(ids), 'answer': answers[turn],
                          'growth_tokens': len(ids) - len(turns[-1]['input_ids']) if turns else len(ids)})
            messages = messages + [{'role': 'assistant', 'content': answers[turn]}]
        sessions.append({'id': f'cache-session-{sid}', 'turns': turns})
    out.mkdir(parents=True, exist_ok=False)
    (out / 'sets.json').write_text(json.dumps(sessions))
    print(json.dumps({'sessions': len(sessions), 'lengths': [[len(t['input_ids']) for t in s['turns']] for s in sessions]}), flush=True)


def engine_config(cache, reserve, selected=False):
    cfg = json.loads(CONFIG.read_text())
    args = cfg['args']
    args[:] = [a for a in args if a not in ('--selected-prefill', '--selected-prefix-cache')]
    args[args.index('--prompt-cache') + 1] = str(6 if cache else 0)
    args[args.index('--vram-reserve-mib') + 1] = str(reserve)
    # Same model and decode controls in every arm; no parked conversations needed.
    args += ['--turn-token', '248045', '--conversation-cache-mib', '0']
    if selected:
        args += ['--selected-prefill', '--selected-prefix-cache']
    return cfg, args


def start_engine(cache, reserve, log, selected=False):
    sys.path[:0] = [str(STRATA), str(STRATA / 'tools')]
    from serve.server import StrataEngine, child_env
    cfg, args = engine_config(cache, reserve, selected)
    env = child_env(cfg)
    env.update(STRATA_ROUTE_TAIL_SKIP='0', STRATA_PREFILL_CPU_SHARE='0')
    return StrataEngine(cfg['exe'], args, cwd=cfg['cwd'], log=str(log), env=env)


def target_request(engine, ids, maximum=8, positions=None, original=None):
    sampling = {'temperature': 0}
    if positions is not None:
        sampling['_selected_prefill'] = {'position_ids': positions, 'original_prompt_length': original}
    started = time.perf_counter()
    first = None
    output = []
    for token in engine.generate(ids, maximum, sampling, threading.Event()):
        if token is not None:
            if first is None:
                first = time.perf_counter()
            output.append(token)
    if first is None:
        raise RuntimeError('Target produced no first token')
    if positions is not None:
        expected = {'position_ids': positions, 'original_prompt_length': original, 'next_position': original}
        if engine.position_audit != expected:
            raise RuntimeError('Target did not confirm original device coordinates')
    return {'output_ids': output, 'target_ttft_s': first-started,
            'first_token_monotonic_s': first, 'engine': dict(engine.last)}


def run_control(out):
    sessions = json.loads((out / 'sets.json').read_text())
    tok, _, _ = load_frontend(STRATA, TOKENIZER)
    path = out / 'control.jsonl'
    if path.exists():
        raise ValueError('Refusing to overwrite an existing run')
    for cache in (False, True):
        engine = start_engine(cache, 700, out / f'control-{cache}.log')
        try:
            target_request(engine, sessions[0]['turns'][0]['input_ids'][:256], 2)
            for ses in sessions:
                for t in ses['turns']:
                    r = target_request(engine, t['input_ids'])
                    r.update(session=ses['id'], turn=t['turn'], arm='control-cache' if cache else 'control-off',
                             input_hash=token_hash(t['input_ids']), input_tokens=len(t['input_ids']), growth_tokens=t['growth_tokens'])
                    r['correct'] = re.findall(r'\d{5}', tok.decode(r['output_ids'])) == [t['answer']]
                    if not cache and r['engine'].get('reused', 0):
                        raise RuntimeError('Cache-off control reused tokens')
                    with path.open('a') as f:
                        f.write(json.dumps(r)+'\n')
                    print(json.dumps({k:r[k] for k in ('session','turn','arm','input_tokens','target_ttft_s','correct')} | {'reused':r['engine'].get('reused')}), flush=True)
        finally:
            engine.close()



def run_student(out):
    import gc

    import torch

    from .cached_student import CachedStudent
    from .chunked_student import trace_chunked
    from .core import compatible_ids
    from .student import Student
    sessions = json.loads((out / 'sets.json').read_text())
    _, _, vocab = load_frontend(STRATA, TOKENIZER)
    allowed = compatible_ids(vocab, json.loads((STUDENT / 'tokenizer.json').read_text()))
    student = Student(STUDENT, allowed)
    cached = CachedStudent(student, block=1024)
    trace_chunked(student.model, torch, student.stop_ids, sessions[0]['turns'][0]['input_ids'][:128],
                  .3, prefill_block=1024)  # excluded cold runtime warmup
    path = out / 'student.jsonl'
    if path.exists():
        raise ValueError('Refusing to overwrite existing traces')
    for ses in sessions:
        cached.reset()
        for turn in ses['turns']:
            ids = turn['input_ids']
            # Alternate execution order to reduce systematic thermal/warmup bias.
            traces = {}
            for arm in (['uncached', 'cached'] if turn['turn'] % 2 == 0 else ['cached', 'uncached']):
                if arm == 'cached':
                    tr = cached.trace(ids, .3)
                else:
                    tr = trace_chunked(student.model, torch, student.stop_ids, ids, .3, prefill_block=1024)
                traces[arm] = tr
            traces['reference_full'] = student.trace(ids, .3, return_scores=True)
            a, b = traces['uncached'], traces['cached']
            aa, bb = torch.tensor(a['scores']), torch.tensor(b['scores'])
            aset, bset = set(a['positions']), set(b['positions'])
            delta = (aa-bb).abs()
            comparison = {'max_score_abs_diff': delta.max().item(), 'mean_score_abs_diff': delta.mean().item(),
                          'selected_positions_equal': a['positions'] == b['positions'],
                          'selection_jaccard': len(aset & bset)/len(aset | bset),
                          'draft_ids_equal': a['draft_sampled_ids'] == b['draft_sampled_ids'],
                          'layers_equal': a['attention_layer_ids'] == b['attention_layer_ids'],
                          'query_rows_equal': a['query_rows'] == b['query_rows']}
            if not comparison['layers_equal'] or not comparison['query_rows_equal']:
                raise RuntimeError('Cached trace shape is not equivalent')
            ref = traces['reference_full']
            comparison['reference_positions_equal'] = ref['positions'] == b['positions']
            comparison['reference_draft_ids_equal'] = ref['draft_sampled_ids'] == b['draft_sampled_ids']
            comparison['reference_max_score_abs_diff'] = (torch.tensor(ref['scores'])-bb).abs().max().item()
            row = {'session': ses['id'], 'turn': turn['turn'], 'input_hash': turn['input_hash'],
                   'uncached': a, 'cached': b, 'reference_full': ref, 'comparison': comparison}
            with path.open('a') as f:
                f.write(json.dumps(row)+'\n')
            print(json.dumps({'session':ses['id'], 'turn':turn['turn'], 'tokens':len(ids),
                              'uncached_s':a['student_selection_s'], 'cached_s':b['student_selection_s'],
                              'reused':b['reused_prefix_tokens'], **comparison}), flush=True)
            gc.collect()
    cached.reset()




def check_position_binding(engine, ids, out):
    base = ids[:512]
    sparse = list(range(0, 1024, 2))
    target_request(engine, [19, 20, 21], 1)
    target_request(engine, base, 1, sparse, 2048)
    extended = ids[:544]
    positions = sparse + list(range(1024, 1056))
    appended = target_request(engine, extended, 1, positions, 2048)
    changed = list(positions)
    changed[10] += 1  # Same token IDs, different original coordinate inside the cached prefix.
    warm = target_request(engine, extended, 1, changed, 2048)
    if warm['engine'].get('reused', 0) > 10:
        raise RuntimeError('Reused target state beyond a changed rotary position')
    target_request(engine, [19, 20, 21], 1)
    cold = target_request(engine, extended, 1, changed, 2048)
    if warm['output_ids'] != cold['output_ids']:
        raise RuntimeError('Position-change warm output differs from cold replay')
    implicit = target_request(engine, extended, 1)
    if implicit['engine'].get('reused', 0):
        raise RuntimeError('Cross-domain cache reuse: sparse to implicit')
    (out / 'position-binding.json').write_text(json.dumps({
        'append_reused': appended['engine'].get('reused'),
        'changed_position_reused': warm['engine'].get('reused'),
        'changed_position_cold_output_equal': True,
        'sparse_to_implicit_reused': implicit['engine'].get('reused'),
    }, indent=2))


def run_pipeline(out, target_cache):
    import torch

    from .cached_student import CachedStudent
    from .chunked_student import trace_chunked
    from .core import compatible_ids, validate_input
    from .stable_selection import StableSelection
    from .student import Student
    sessions = json.loads((out / 'sets.json').read_text())
    tok, _, vocab = load_frontend(STRATA, TOKENIZER)
    allowed = compatible_ids(vocab, json.loads((STUDENT / 'tokenizer.json').read_text()))
    student = Student(STUDENT, allowed)
    cached = CachedStudent(student, block=1024)
    label = 'both-cache' if target_cache else 'student-cache'
    path = out / (label + '.jsonl')
    if path.exists():
        raise ValueError('Refusing to overwrite existing pipeline results')
    engine = start_engine(target_cache, 4096, out / (label + '.log'), selected=True) if target_cache else None
    if not target_cache:
        # Original selected interface, target caching explicitly off.
        sys.path[:0] = [str(STRATA)]
        from serve.server import StrataEngine, child_env
        cfg, args = engine_config(False, 4096)
        args += ['--selected-prefill']
        env = child_env(cfg)
        env.update(STRATA_ROUTE_TAIL_SKIP='0', STRATA_PREFILL_CPU_SHARE='0')
        engine = StrataEngine(cfg['exe'], args, cwd=cfg['cwd'], log=str(out / (label+'.log')), env=env)
    try:
        if target_cache and engine.info.get('selected_prefix_cache') != 1:
            raise RuntimeError('Target does not advertise the position-bound cache extension')
        (out / (label + '-info.json')).write_text(json.dumps(engine.info, indent=2))
        trace_chunked(student.model, torch, student.stop_ids, sessions[0]['turns'][0]['input_ids'][:128],
                      .3, prefill_block=1024)
        target_request(engine, sessions[0]['turns'][0]['input_ids'][:256], 2)
        if target_cache:
            # Score the original full-prefill selections wherever they differ from the cached student.
            reference_path = out / 'reference-quality.jsonl'
            by_key = {(s['id'], t['turn']): t for s in sessions for t in s['turns']}
            for line in (out / 'student.jsonl').read_text().splitlines():
                row = json.loads(line)
                if row['comparison']['reference_positions_equal']:
                    continue
                turn = by_key[(row['session'], row['turn'])]
                ids = turn['input_ids']
                if row['input_hash'] != token_hash(ids):
                    raise RuntimeError('Frozen reference prompt mismatch')
                pos = row['reference_full']['positions']
                target_request(engine, [19, 20, 21], 1)
                ref = target_request(engine, [ids[p] for p in pos], positions=pos, original=len(ids))
                ref.update(session=row['session'], turn=row['turn'],
                           correct=re.findall(r'\d{5}', tok.decode(ref['output_ids'])) == [turn['answer']])
                with reference_path.open('a') as f:
                    f.write(json.dumps(ref)+'\n')
        arms = ['full-colocated-cache', 'stable-both-cache'] if target_cache else ['student-off', 'student-on']
        for arm in arms:
            for ses in sessions:
                cached.reset()
                policy = StableSelection()
                for turn in ses['turns']:
                    ids = turn['input_ids']
                    validate_input(ids, allowed)
                    if token_hash(ids) != turn['input_hash']:
                        raise RuntimeError('Frozen input changed')
                    started = time.perf_counter()
                    tr, audit, positions = None, {}, None
                    if arm != 'full-colocated-cache':
                        if arm == 'student-off':
                            tr = trace_chunked(student.model, torch, student.stop_ids, ids, .3, prefill_block=1024)
                        else:
                            tr = cached.trace(ids, .3)
                        positions = tr['positions']
                        if target_cache:
                            positions, audit = policy.plan(ids, positions)
                    selected_ids = [ids[p] for p in positions] if positions is not None else ids
                    r = target_request(engine, selected_ids, positions=positions,
                                       original=len(ids) if positions is not None else None)
                    r.update(session=ses['id'], turn=turn['turn'], arm=arm,
                             input_tokens=len(ids), input_hash=turn['input_hash'], retained_tokens=len(selected_ids),
                             positions=positions, student_s=tr['student_selection_s'] if tr else 0,
                             student_reused=tr['reused_prefix_tokens'] if tr else 0,
                             pipeline_ttft_s=r['first_token_monotonic_s']-started, **audit)
                    r['correct'] = re.findall(r'\d{5}', tok.decode(r['output_ids'])) == [turn['answer']]
                    if not target_cache and r['engine'].get('reused', 0):
                        raise RuntimeError('Target unexpectedly cached in student-only isolation')
                    if target_cache and positions is not None:
                        if audit['backfilled_tokens'] and r['engine'].get('reused', 0) > audit['prefix_cells_unchanged']:
                            raise RuntimeError('Target reused a prefix beyond backfilled history')
                        # Force a cold coordinate domain, then replay EXACT selected IDs/positions.
                        target_request(engine, [19, 20, 21], 1)
                        cold = target_request(engine, selected_ids, positions=positions, original=len(ids))
                        if cold['engine'].get('reused', 0):
                            raise RuntimeError('Validation replay was not cold')
                        r['cold_output_equal'] = cold['output_ids'] == r['output_ids']
                        r['cold_target_ttft_s'] = cold['target_ttft_s']
                        r['cold_correct'] = re.findall(r'\d{5}', tok.decode(cold['output_ids'])) == [turn['answer']]
                        if not r['cold_output_equal']:
                            # Preserve evidence, but never silently claim exact equivalence.
                            r['cold_output_ids'] = cold['output_ids']
                    with path.open('a') as f:
                        f.write(json.dumps(r)+'\n')
                    print(json.dumps({k:r[k] for k in ('session','turn','arm','input_tokens','retained_tokens','student_s','pipeline_ttft_s','correct')} | {'target_reused':r['engine'].get('reused'), 'student_reused':r['student_reused'], 'cold_output_equal':r.get('cold_output_equal')}), flush=True)
        if target_cache:
            check_position_binding(engine, sessions[0]['turns'][0]['input_ids'], out)
    finally:
        engine.close()
        cached.reset()


def main():
    global STRATA, TOKENIZER, STUDENT, CONFIG
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('phase', choices=['prepare', 'control', 'student', 'student-pipeline', 'both-pipeline'])
    ap.add_argument('--output', type=Path, default=BASE)
    ap.add_argument('--strata-root', type=Path, default=STRATA)
    ap.add_argument('--target-tokenizer', type=Path, default=TOKENIZER)
    ap.add_argument('--config', type=Path, default=CONFIG)
    ap.add_argument('--student', type=Path)
    a = ap.parse_args()
    STRATA, TOKENIZER, STUDENT, CONFIG = a.strata_root, a.target_tokenizer, a.student, a.config
    if a.phase not in ('prepare', 'control') and STUDENT is None:
        ap.error('--student must point to the local 0.8B model')
    if a.phase == 'prepare':
        prepare_sets(a.output)
    elif a.phase == 'control':
        run_control(a.output)
    elif a.phase == 'student':
        run_student(a.output)
    else:
        run_pipeline(a.output, target_cache=a.phase == 'both-pipeline')


if __name__ == '__main__':
    main()
