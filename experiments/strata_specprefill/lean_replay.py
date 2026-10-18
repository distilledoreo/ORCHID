"""Student-only replay of a saved run, for tuning lean SpecPrefill budgets offline.

``capture`` rebuilds the student transcript from a run's case and recorded
replies, runs the real cached student once per turn and saves each turn's
scores and chunk layout. ``simulate`` then replays the delivery policy for a
grid of budgets and ranking schemes without the target: for every expected
value, is the chunk containing it in the target transcript at answer time, and
how many evidence tokens did that cost. Student scores do not depend on chunk
boundaries, so chunk size is re-laid offline.

Usage:
  python -m experiments.strata_specprefill.lean_replay capture RUN_DIR ARM --config CFG --student PATH
  python -m experiments.strata_specprefill.lean_replay simulate RUN_DIR ARM --config CFG
"""
from __future__ import annotations

import argparse
import copy
import json
import pickle
from pathlib import Path

from memory_gateway.specprefill.spec_session import SpecLimits, SpecSession, mentioned_sources

LATE = ('late-unasked-recall', 'late')


class Replayed:
    """Target stand-in that returns the run's recorded reply text."""
    stop_ids = (248044, 248046)

    def __init__(self, tok, texts):
        self.tok, self.texts, self.turn = tok, texts, 0

    def __call__(self, ids, maximum):
        out = list(self.tok.encode(self.texts[self.turn])) + [248046]
        self.turn += 1
        return out[:maximum]


class Recording:
    def __init__(self, student, lookahead):
        self.student, self.lookahead, self.traces, self.seconds = student, lookahead, [], []

    def reset(self):
        self.student.reset()

    def trace(self, ids, keep_ratio):
        result = self.student.trace(ids, keep_ratio, lookahead=self.lookahead)
        self.traces.append(result['scores'])
        self.seconds.append(result['student_selection_s'])
        return result


def frontend(config):
    from .run import load_frontend
    cfg = json.loads(config.read_text())
    return load_frontend(Path(cfg['cwd']), Path(cfg['tokenizer']))


def capture(args):
    from memory_gateway.specprefill.cached_student import CachedStudent
    from memory_gateway.specprefill.core import compatible_ids
    from memory_gateway.specprefill.student import Student
    tok, _, vocab = frontend(args.config)
    case = json.loads((args.run/'case.json').read_text())
    turns = [json.loads(line) for line in (args.run/args.arm/'turns.jsonl').read_text().splitlines()]
    allowed = compatible_ids(vocab, json.loads((args.student/'tokenizer.json').read_text()))
    student = Recording(CachedStudent(Student(args.student, allowed), block=args.block,
                                      prefill_window=args.prefill_window,
                                      sparse_keys=args.sparse_keys), args.lookahead)
    limits = SpecLimits(student_context=args.student_context, target_context=10**9, answer_tokens=64,
                        new_rate=0.0, recall_tokens=0)
    session = SpecSession(tok, student, Replayed(tok, [t['text'] for t in turns]),
                          system=case['system'], limits=limits)
    layouts = []
    for turn in case['turns'][:len(turns)]:
        if turn['source']:
            s = turn['source']
            session.add_source(s['path'], s['version'], s['text'])
        layouts.append({'pos': dict(session.pos)})
        session.answer(turn['question'], maximum=64)
    sources = [{'path': s.path, 'version': s.version, 'ids': s.ids, 'bounds': s.bounds,
                'turn': s.turn} for s in session.sources]
    with (args.run/args.arm/f'replay{args.tag}.pkl').open('wb') as f:
        pickle.dump({'scores': student.traces, 'prefill_window': args.prefill_window,
                     'student_seconds': student.seconds, 'layouts': layouts, 'sources': sources,
                     'case': case}, f)
    print('captured', len(student.traces), 'turns; student seconds', round(sum(student.seconds), 2))


def line_bounds(tok, ids, size):
    bounds, start, last = [], 0, None
    for i, token in enumerate(ids):
        if tok.token_bytes(token).endswith(b'\n'):
            last = i + 1
        if i + 1 - start >= size:
            cut = last if last and last > start else i + 1
            bounds.append((start, cut))
            start, last = cut, None
    if start < len(ids):
        bounds.append((start, len(ids)))
    return bounds


def rechunk(data, tok, size):
    out = copy.deepcopy(data)
    texts = {}
    for index, source in enumerate(out['sources']):
        source['bounds'] = line_bounds(tok, source['ids'], size)
        for c, (a, b) in enumerate(source['bounds']):
            texts[(index, c)] = tok.decode(source['ids'][a:b])
    for layout, old in zip(out['layouts'], data['layouts']):
        bodies = {k[0]: a - data['sources'][k[0]]['bounds'][k[1]][0] for k, a in old['pos'].items()}
        layout['pos'] = {(s, c): bodies[s] + out['sources'][s]['bounds'][c][0]
                         for s in bodies for c in range(len(out['sources'][s]['bounds']))}
    out['chunk_texts'] = texts
    return out


def gold(data, turn_index):
    """Chunks holding each expected value, as an assignment or a return."""
    turn = data['case']['turns'][turn_index]
    return [[k for k, text in data['chunk_texts'].items()
             if f'return {value}\n' in text or f'= {value}\n' in text]
            for value in turn['expected']]


def ranking(path_boost, version_boost):
    def scheme(data, scores, layout, i):
        weights = mentioned_sources(data['case']['turns'][i]['question'],
                                    [(s['path'], s['version']) for s in data['sources']],
                                    path_boost, version_boost)
        out = []
        for key, a in layout['pos'].items():
            lo, hi = data['sources'][key[0]]['bounds'][key[1]]
            out.append((sum(scores[a:a + hi - lo]) / (hi - lo) * weights.get(key[0], 1.0), key))
        return [k for _, k in sorted(out, key=lambda x: (-x[0], x[1]))]
    return scheme


def simulate(data, scheme, new_rate, recall):
    delivered, tokens = set(), 0
    hits = fields = late_hits = late = 0
    for i, scores in enumerate(data['scores']):
        turn = data['case']['turns'][i]
        new = sum(len(s['ids']) for s in data['sources'] if s['turn'] == i)
        budget, used = -(-int(new_rate * new * 1000) // 1000) + recall, 0
        for key in scheme(data, scores, data['layouts'][i], i):
            lo, hi = data['sources'][key[0]]['bounds'][key[1]]
            if key in delivered or used + hi - lo > budget:
                continue
            delivered.add(key)
            used += hi - lo
        tokens += used
        for keys in gold(data, i):
            ok = any(k in delivered for k in keys)
            hits, fields = hits + ok, fields + 1
            if turn['kind'] in LATE:
                late_hits, late = late_hits + ok, late + 1
    return {'gold': f'{hits}/{fields}', 'late': f'{late_hits}/{late}', 'evidence_tokens': tokens}


def simulate_session(data, tok, limits_kw, new_rate, recall, *, lookups=False):
    """Delivery replay through a real SpecSession chooser/renderer (exact costs)."""
    limits = SpecLimits(student_context=10**9, target_context=10**9, new_rate=new_rate,
                        recall_tokens=recall, **limits_kw)
    session = SpecSession(tok, None, None, system=data['case']['system'], limits=limits)
    for s in data['sources']:
        session.add_source(s['path'], s['version'], tok.decode(s['ids']))
    tokens = hits = fields = late_hits = late = 0
    for i, scores in enumerate(data['scores']):
        turn = data['case']['turns'][i]
        layout = data['layouts'][i]
        bodies = {k[0]: a - data['sources'][k[0]]['bounds'][k[1]][0] for k, a in layout['pos'].items()}
        session.pos = {(s, c): bodies[s] + session.sources[s].bounds[c][0]
                       for s in bodies for c in range(len(session.sources[s].bounds))}
        new = sum(len(s['ids']) for s in data['sources'] if s['turn'] == i)
        budget = -(-int(new_rate * new * 1000) // 1000) + recall
        chosen = session._choose(session._candidates(scores, turn['question']), budget)
        tokens += len(session._render(chosen)[0])
        session.delivered.update(chosen)
        for value in turn['expected']:
            ok = any(f'return {value}\n' in tok.decode(session.chunk_ids(k)) or
                     f'= {value}\n' in tok.decode(session.chunk_ids(k)) for k in session.delivered)
            hits, fields = hits + ok, fields + 1
            if turn['kind'] in LATE:
                late_hits, late = late_hits + ok, late + 1
    return {'gold': f'{hits}/{fields}', 'late': f'{late_hits}/{late}', 'rendered_tokens': tokens}


def chosen_per_turn(data, tok):
    """Production defaults: the chunks delivered each turn, and gold delivery."""
    session = SpecSession(tok, None, None, system=data['case']['system'],
                          limits=SpecLimits(student_context=10**9, target_context=10**9))
    for s in data['sources']:
        session.add_source(s['path'], s['version'], tok.decode(s['ids']))
    turns, hits, fields = [], 0, 0
    for i, scores in enumerate(data['scores']):
        turn = data['case']['turns'][i]
        layout = data['layouts'][i]
        bodies = {k[0]: a - data['sources'][k[0]]['bounds'][k[1]][0] for k, a in layout['pos'].items()}
        session.pos = {(s, c): bodies[s] + session.sources[s].bounds[c][0]
                       for s in bodies for c in range(len(session.sources[s].bounds))}
        new = sum(len(s['ids']) for s in data['sources'] if s['turn'] == i)
        budget = -(-int(session.limits.new_rate * new * 1000) // 1000) + session.limits.recall_tokens
        chosen = session._choose(session._candidates(scores, turn['question']), budget)
        session.delivered.update(chosen)
        turns.append(set(chosen))
        if turn.get('score') == 'whole':
            hits += line_delivered(session, str(turn['expected'][0]))
            fields += 1
            continue
        for value in turn['expected']:
            hits += any(f'return {value}\n' in tok.decode(session.chunk_ids(k)) or
                        f'= {value}\n' in tok.decode(session.chunk_ids(k)) for k in session.delivered)
            fields += 1
    return turns, f'{hits}/{fields}'


def line_delivered(session, line):
    """True if some occurrence of `line` lies entirely in delivered chunks."""
    import bisect
    needle = line.encode()
    for index in range(len(session.sources)):
        text, offsets = session._source_bytes(index)
        starts = [a for a, _ in session.sources[index].bounds]
        at = text.find(needle)
        while at >= 0:
            first = bisect.bisect_right(starts, bisect.bisect_right(offsets, at) - 1) - 1
            last = bisect.bisect_right(starts, bisect.bisect_right(offsets, at + len(needle) - 1) - 1) - 1
            if all((index, c) in session.delivered for c in range(first, last + 1)):
                return True
            at = text.find(needle, at + 1)
    return False


def compare(args, tok):
    def load(tag):
        with (args.run/args.arm/f'replay{tag}.pkl').open('rb') as f:
            return pickle.load(f)
    base, base_gold = chosen_per_turn(load(args.tag), tok)
    print(json.dumps({'tag': args.tag or 'full', 'gold': base_gold}))
    for tag in args.against:
        other, gold = chosen_per_turn(load(tag), tok)
        overlaps = [len(a & b) / max(1, len(a | b)) for a, b in zip(base, other)]
        print(json.dumps({'tag': tag, 'gold': gold,
                          'student_s': round(sum(load(tag).get('student_seconds', [0])), 2),
                          'mean_jaccard_vs_base': round(sum(overlaps) / len(overlaps), 3),
                          'min_jaccard': round(min(overlaps), 3)}))


GRID = ((0.3, 512), (0.2, 256), (0.15, 256), (0.1, 192), (0.1, 128), (0.08, 128),
        (0.05, 128), (0.05, 96), (0.03, 96), (0.03, 64))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('capture', 'simulate', 'compare'))
    parser.add_argument('run', type=Path)
    parser.add_argument('arm')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--student', type=Path)
    parser.add_argument('--block', type=int, default=512)
    parser.add_argument('--lookahead', type=int, default=8)
    parser.add_argument('--chunk', type=int, nargs='+', default=[16])
    parser.add_argument('--prefill-window', type=int, default=None)
    parser.add_argument('--sparse-keys', type=int, default=None, help='block-sparse prefill keys')
    parser.add_argument('--student-context', type=int, default=32768)
    parser.add_argument('--tag', default='', help='replay file suffix, e.g. -w4096')
    parser.add_argument('--against', nargs='+', default=[], help='compare: tags to compare with --tag')
    parser.add_argument('--floor', type=float, nargs='+', default=[0.0])
    parser.add_argument('--floor-rank', type=int, nargs='+', default=[1])
    parser.add_argument('--grid', type=json.loads, default=None, help='[[new_rate, recall], ...]')
    args = parser.parse_args()
    if args.mode == 'capture':
        capture(args)
        return
    tok, _, _ = frontend(args.config)
    if args.mode == 'compare':
        compare(args, tok)
        return
    with (args.run/args.arm/f'replay{args.tag}.pkl').open('rb') as f:
        data = pickle.load(f)
    for size in args.chunk:
        for rank in args.floor_rank:
            for floor in args.floor:
                for new_rate, recall in args.grid or GRID:
                    kw = {'chunk_tokens': size, 'relative_floor': floor, 'floor_rank': rank}
                    print(json.dumps({'chunk': size, 'rank': rank, 'floor': floor, 'new_rate': new_rate,
                                      'recall': recall, **simulate_session(data, tok, kw, new_rate, recall)}))


if __name__ == '__main__':
    main()
