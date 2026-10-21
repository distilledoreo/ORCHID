"""Frozen evidence schedules isolate provenance rendering from retrieval policy."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import time
from pathlib import Path

from . import cache_experiment as api
from .append_experiment import SYSTEM, clean_output, encode, finish_reply, question_end, role
from .core import token_hash
from .gpu_memory import MemorySampler
from .omission_archive import OmissionArchive

STYLES = ('bare', 'minimal', 'detailed')
BASE = api.ROOT/'data/strata_specprefill/provenance-v1'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def add_sources(archive, turn, index):
    for src in turn['sources']:
        archive.add(src['path'], src['version'], src['text'], src['original_start'], index)


def symbol_handles(archive, sid, symbol):
    """Fixed delivery by symbol boundaries, independent of its numeric return value."""
    src = archive.sources[sid]
    match = re.search(r'^def '+re.escape(symbol)+r'\(\):\n    return [^\n]+', src['text'], re.M)
    if not match:
        raise ValueError('Missing scheduled symbol')
    start, end = match.span()
    return [h for h in src['chunks'] if archive.chunks[h].char_start < end
            and archive.chunks[h].char_end > start]


def render_evidence(archive, handles, style='detailed'):
    """Keep a frozen delivery order, including deliberately late historical revisions."""
    groups = []
    for h in archive.missing(handles):
        c = archive.chunks[h]
        if (groups and archive.chunks[groups[-1][-1]].source == c.source
                and archive.chunks[groups[-1][-1]].index+1 == c.index):
            groups[-1].append(h)
        else:
            groups.append([h])
    text, placements = '', []
    for group in groups:
        block, spans = archive.evidence(group, style)
        placements.extend((h, a+len(text), b+len(text)) for h, a, b in spans)
        text += block
    return text, placements


def prepare(tok, output, prior):
    output.mkdir(parents=True, exist_ok=False)
    cases = json.loads((prior/'cases.json').read_text())['cases']
    for case in cases:
        archived = json.loads((prior/'append-none'/case['id']/'archive.json').read_text())
        deliveries = [e['handles'] for e in archived['events'] if e['event'] == 'deliver']
        if len(deliveries) != len(case['turns']):
            raise ValueError('Prior delivery event count changed')
        for i, turn in enumerate(case['turns']):
            for src in turn['sources']:
                match = [s for s in archived['sources'].values()
                         if (s['path'], s['version']) == (src['path'], src['version'])]
                src['original_start'] = match[0]['original_start']
            turn['deliver'] = deliveries[i]
            turn['stale_answers'] = []
            turn['wrong_source_answers'] = []
        case['split'] = 'existing-selector-replay'
    rng = random.Random(2026101607)
    for n in range(4):
        paths = [f'src/tenant_{n}/{name}.py' for name in ('primary', 'secondary')]
        prefix = ''.join(rng.choice('abcdefghjkmnpqrstuvwxyz') for _ in range(7))
        symbols = [f'{prefix}_field_{i}' for i in range(48)]
        values = rng.sample(range(10000, 99999), 2*3*48)
        records = {(p, v):values[(p*3+v-1)*48:(p*3+v)*48] for p in range(2) for v in range(1, 4)}
        archive = OmissionArchive(tok)
        cursor, turns = 0, []
        for i in range(5):
            sources = []
            if i < 3:
                for p in range(2):
                    text = '\n\n'.join(f'def {symbol}():\n    return {val}'
                                        for symbol, val in zip(symbols, records[p, i+1]))+'\n'
                    src = {'path':paths[p], 'version':i+1, 'text':text, 'original_start':cursor}
                    cursor += len(tok.encode(text))+32
                    sources.append(src)
            turn = {'sources':sources}
            add_sources(archive, turn, i)
            # First two queries use just-delivered beginnings. Later recoveries are out of revision order.
            if i < 2:
                p, version, symbol_index = 0, i+1, 0
                scheduled = [(0, i+1, 0)]
            elif i == 2:
                p, version, symbol_index = 0, 3, 37
                scheduled = [(0, 1, 37), (0, 3, 37)]
                if n % 2 == 0:
                    scheduled.reverse()  # old evidence is deliberately most recent in half the cases
            elif i == 3:
                p, version, symbol_index = 1, 1, 37
                scheduled = [(1, 3, 37), (1, 1, 37), (0, 2, 37)]
                if n % 2:
                    scheduled.reverse()
            else:
                p, version, symbol_index = 0, 3, 37
                scheduled = []  # v2 arrived after v3; current-version answer must survive recency bias
            handles = []
            for sp, sv, si in scheduled:
                sid = archive.source_id(paths[sp], sv)
                handles.extend(symbol_handles(archive, sid, symbols[si]))
            handles = sorted(set(handles), key=lambda h:(int(h.split(':')[0][1:]), int(h.split(':')[1]))) if i < 2 else list(dict.fromkeys(handles))
            wanted = 'the current version' if i in (2, 4) else f'version {version}'
            question = (f'In {paths[p]}, use {wanted}. What does {symbols[symbol_index]}() return? '
                        'Reply with only its five-digit value.')
            turn.update(question=question, answer=str(records[p, version][symbol_index]),
                        evidence={'path':paths[p], 'version':version,
                                  'needle':str(records[p, version][symbol_index])}, deliver=handles,
                        stale_answers=[str(records[p, v][symbol_index]) for v in range(1, 4) if v != version],
                        wrong_source_answers=[str(records[1-p, version][symbol_index])])
            turns.append(turn)
        cases.append({'id':f'fresh-conflicts-{n}', 'split':'fresh-controlled', 'turns':turns})
    # Freeze exact payload hashes and validate every source/chunk schedule before model inference.
    for case in cases:
        archive = OmissionArchive(tok)
        for i, turn in enumerate(case['turns']):
            add_sources(archive, turn, i)
            for h in turn['deliver']:
                if h not in archive.chunks:
                    raise ValueError('Unknown scheduled chunk')
            text, placements = render_evidence(archive, turn['deliver'])
            turn['payload_sha256'] = digest(''.join(archive.chunks[h].text for h, _, _ in placements).encode())
            turn['delivered_handles'] = [h for h, _, _ in placements]
            turn['recovered_chunks'] = sum(archive.sources[archive.chunks[h].source]['turn'] < i
                                          for h, _, _ in placements)
            archive.commit(placements, 0, text)
    frozen = {'seed':2026101607, 'prior_cases_sha256':digest((prior/'cases.json').read_bytes()), 'cases':cases}
    (output/'cases.json').write_text(json.dumps(frozen, indent=2)+'\n')
    runtime = output/'runtime'
    runtime.mkdir()
    for name in ('provenance_experiment.py', 'omission_archive.py'):
        shutil.copy2(Path(__file__).with_name(name), runtime/name)
    (output/'protocol.json').write_text(json.dumps({
        'cases_sha256':digest((output/'cases.json').read_bytes()),
        'styles':list(STYLES), 'student':'Qwen3.5-0.8B resident; retrieval schedules frozen, no timed student inference',
        'target':'Flash-Next Swift IQ3_S', 'hardware':'Tesla P100 16GB',
        'selection':'Existing actual append-none schedules plus fresh predetermined symbol-boundary schedules; no per-arm reselection.',
        'latency':'Observed rendering/archive + target TTFT, excludes frozen selector planning and model startup.',
        'source_status':'No separate source-status messages in any arm; evidence frames are the only provenance variable.',
        'source_sha256':{p.name:digest(p.read_bytes()) for p in runtime.iterdir()},
        'native_binary_sha256':digest(Path('/home/distilledoreo/AI/Strata/build-cuda12/strata').read_bytes())}, indent=2)+'\n')
    return cases


def run_session(engine, tok, case, style, output):
    archive = OmissionArchive(tok)
    target = role(tok, 'system')+encode(tok, SYSTEM)+encode(tok, '<|im_end|>\n', True)
    previous_prompt, prior_sequence = 0, list(target)
    session = output/style/case['id']
    session.mkdir(parents=True, exist_ok=False)
    api.target_request(engine, [19, 20, 21], 1)
    rows = []
    for i, turn in enumerate(case['turns']):
        started = time.perf_counter()
        add_sources(archive, turn, i)
        archive.save(session/'archive.json')
        if target[:len(prior_sequence)] != prior_sequence:
            raise RuntimeError('Prior target sequence rewritten')
        target.extend(role(tok, 'user'))
        text, placements = render_evidence(archive, turn['deliver'], style)
        handles = [h for h, _, _ in placements]
        payload = ''.join(archive.chunks[h].text for h in handles)
        if handles != turn['delivered_handles'] or digest(payload.encode()) != turn['payload_sha256']:
            raise RuntimeError('Evidence payload/order changed across styles')
        archive.commit(placements, len(target), text)
        evidence_tokens = len(encode(tok, text))
        target.extend(encode(tok, text))
        target.extend(question_end(tok, turn['question']))
        prompt = list(target)
        result = api.target_request(engine, prompt, maximum=12)
        response = clean_output(tok, result['output_ids'])
        codes = re.findall(r'\d{5}', response)
        correct = codes == [turn['answer']]
        reused = result['engine'].get('reused', 0)
        archive.confirm_cache(len(prompt), reused, previous_prompt)
        # Calculate framing overhead against the exact same payload in a bare wrapper.
        clone = OmissionArchive(tok)
        for j in range(i+1):
            add_sources(clone, case['turns'][j], j)
        bare_text, _ = render_evidence(clone, handles, 'bare')
        row = {'case':case['id'], 'split':case['split'], 'turn':i, 'style':style,
               'correct':correct, 'stale_version_error':len(codes)==1 and codes[0] in turn['stale_answers'],
               'wrong_source_error':len(codes)==1 and codes[0] in turn['wrong_source_answers'],
               'unknown':response=='UNKNOWN', 'text':response, 'output_ids':result['output_ids'],
               'target_prompt_tokens':len(prompt), 'target_input_hash':token_hash(prompt),
               'target_reused':reused, 'expected_cached_prefix':previous_prompt,
               'target_ttft_s':result['target_ttft_s'],
               'delivery_ttft_s':result['first_token_monotonic_s']-started,
               'evidence_tokens':evidence_tokens, 'extra_vs_bare_tokens':evidence_tokens-len(encode(tok, bare_text)),
               'payload_sha256':digest(payload.encode()), 'delivered_handles':handles,
               'recovered_chunks':turn['recovered_chunks'], 'prefix_exact':True}
        finish_reply(tok, target, result['output_ids'])
        previous_prompt, prior_sequence = len(prompt), list(target)
        rows.append(row)
        with (output/'requests.jsonl').open('a') as f:
            f.write(json.dumps(row)+'\n')
        archive.save(session/'archive.json')
        (session/'transcript.json').write_text(json.dumps({'target_ids':target})+'\n')
        print(json.dumps({k:row[k] for k in ('case', 'turn', 'style', 'correct', 'stale_version_error', 'delivery_ttft_s')}), flush=True)
    return rows


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('phase', choices=['prepare', 'run'])
    ap.add_argument('--output', type=Path, default=BASE)
    ap.add_argument('--prior', type=Path, default=api.ROOT/'data/strata_specprefill/append-v1')
    ap.add_argument('--student', type=Path, default=Path('/media/distilledoreo/Storage/AI/state-projection/models/Qwen3.5-0.8B'))
    a = ap.parse_args()
    tok, _, vocab = api.load_frontend(api.STRATA, api.TOKENIZER)
    if a.phase == 'prepare':
        cases = prepare(tok, a.output, a.prior)
        print(json.dumps({'cases':len(cases), 'turns_per_style':sum(len(c['turns']) for c in cases)}))
        return
    frozen = (a.output/'cases.json').read_bytes()
    cases = json.loads(frozen)['cases']
    from .core import compatible_ids
    from .student import Student
    allowed = compatible_ids(vocab, json.loads((a.student/'tokenizer.json').read_text()))
    student = Student(a.student, allowed)
    engine = api.start_engine(True, 4096, a.output/'engine.log', selected=True)
    try:
        (a.output/'info.json').write_text(json.dumps(engine.info, indent=2)+'\n')
        sampler = MemorySampler()
        # Rotate format order across conversations to reduce systematic warm-order bias.
        for n, case in enumerate(cases):
            for style in STYLES[n % 3:]+STYLES[:n % 3]:
                if digest((a.output/'cases.json').read_bytes()) != digest(frozen):
                    raise RuntimeError('Frozen inputs changed')
                sampler.start()
                run_session(engine, tok, case, style, a.output)
                (a.output/f'memory-{case["id"]}-{style}.json').write_text(json.dumps(sampler.stop())+'\n')
    finally:
        engine.close()
        del student
    (a.output/'COMPLETE.json').write_text(json.dumps({'styles':list(STYLES), 'cases_sha256':digest(frozen)})+'\n')


if __name__ == '__main__':
    main()
