"""Stateful append-only evidence delivery, frozen adaptive selector, isolated engines."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

from . import cache_experiment as engine_api
from .core import token_hash
from .omission_archive import OmissionArchive

SYSTEM = ('Read versioned source evidence and answer with only the requested five digits. '
          'Use the explicitly requested source version. A historical source retrieved later does not supersede '
          'a newer version. If the answer is unavailable, reply UNKNOWN. Archive handles refer to exact stored text.')
TOOL_RULE = (' If evidence is missing, request it by replying exactly fetch_context(S1), using the actual '
             'ARCHIVED source ID or chunk range such as fetch_context(S1:2-8). An external lookup will append '
             'the missing source text. You may make at most two fetch requests. Then answer the original question.')
ARMS = ('full-alone', 'full-matched', 'union30', 'union-adaptive', 'append-none',
        'append-individual', 'append-grouped', 'fetch-grouped', 'fetch-explicit')
ABLATION_ARMS = ('append-no-recovery',)



def target_system(arm):
    if arm == 'fetch-explicit':
        return ("Read versioned source evidence. Final answers contain only the requested five digits. "
                "Use the explicitly requested version; later retrieval of historical text does not supersede a newer revision. "
                "You have an external archive lookup tool. A tool request is an EXCEPTION to the five-digit answer format. "
                "If the requested value is missing from visible EVIDENCE and ARCHIVED references exist, "
                "you MUST request the corresponding source before answering. Your whole assistant turn must be "
                "fetch_context(SOURCE_ID), replacing SOURCE_ID with an actual S-number from ARCHIVED, "
                "for example the identifier of the file named in the question. "
                "The executor will append the missing exact source text. Then answer the original question. "
                "Do not return UNKNOWN until you have attempted retrieval. At most two fetches are allowed. "
                "Do not fetch material already present in EVIDENCE; use it from the cached history.")
    return SYSTEM+(TOOL_RULE if arm.startswith('fetch-') else '')


def encode(tok, text, special=False):
    return tok.encode(text, parse_special=special)


def role(tok, who):
    return encode(tok, f'<|im_start|>{who}\n', True)


def question_end(tok, question):
    return encode(tok, '\n'+question+'<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n', True)


def finish_reply(tok, ids, output):
    ids.extend(output)
    if not output or output[-1] not in (248044, 248046):
        ids.extend(encode(tok, '<|im_end|>', True))
    ids.extend(encode(tok, '\n'))


def clean_output(tok, ids):
    return re.sub(r'<\|[^>]+?\|>', '', tok.decode(ids)).strip()


def adaptive_selection(student, ids):
    import torch

    from .adaptive_signals import adaptive_rate_by_mass, signals_from_scores
    from .core import select_positions
    from .retention_policy import TARGET_MASS, decide, enforce_floor
    tr = student.trace(ids, 1.0)
    rate = adaptive_rate_by_mass(signals_from_scores(tr['scores']), TARGET_MASS)
    arm, chosen = decide(len(ids), rate)
    pos = list(range(len(ids))) if chosen is None else select_positions(torch.tensor(tr['scores'], dtype=torch.float16), chosen)
    if chosen is not None:
        arm, chosen = enforce_floor(len(pos), chosen)
        if chosen is None:
            pos = list(range(len(ids)))
    tr['policy_arm'], tr['adaptive_rate'] = arm, rate
    return pos, tr


def add_original_turn(tok, archive, student_ids, turn, index):
    def add(text, kind, special=False):
        ids = encode(tok, text, special)
        archive.record_message(kind, text, len(student_ids), ids)
        student_ids.extend(ids)
    add('<|im_start|>user\n', 'frame', True)
    new_sources = []
    for src in turn['sources']:
        known = archive.source_id(src['path'], src['version'])
        sid = known or f'S{len(archive.sources)+1}'
        add(f'[SOURCE {sid} {src["path"]} version={src["version"]} source_turn={index}]\n', 'source_header')
        actual = archive.add(src['path'], src['version'], src['text'], len(student_ids), index)
        assert actual == sid
        add(src['text'], 'original_source')
        add('\n[/SOURCE]\n', 'source_footer')
        if known is None:
            new_sources.append(sid)
    add('\n'+turn['question'], 'original_question')
    add('<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n', 'frame', True)
    return new_sources


def append_evidence(tok, archive, target_ids, handles):
    text, placements = archive.evidence(handles)
    archive.commit(placements, len(target_ids), text)
    target_ids.extend(encode(tok, text))
    return len(encode(tok, text)), [h for h,_,_ in placements]


def source_status(archive, new_sources):
    lines = []
    for sid in new_sources:
        source = archive.sources[sid]
        current = archive.current[source['path']]
        if source['supersedes']:
            lines.append(f'[SUPERSEDED {source["supersedes"]} -> {sid}; {source["path"]} current_version={source["version"]}]\n')
        elif current != sid:
            lines.append(f'[HISTORICAL {sid} {source["path"]} version={source["version"]}; current={current}]\n')
    return ''.join(lines)


def retrieval_request(text):
    m = re.fullmatch(r'''fetch_context\(\s*(?P<q>["']?)(?P<h>S[1-9]\d*(?::\d+(?:-\d+)?)?)(?P=q)\s*\)''', text)
    return m['h'] if m else None


def block_permanent_omissions(archive, handles, new_sources, blocked):
    """Freeze first-encounter omissions without consulting questions or answer labels."""
    selected = set(handles)
    for sid in new_sources:
        blocked.update(set(archive.sources[sid]['chunks']) - selected)
    denied = selected & blocked
    if blocked & set(archive.delivered):
        raise RuntimeError('Permanently omitted evidence was delivered')
    return [h for h in handles if h not in blocked], denied


def run_session(engine, student, tok, case, arm, output):
    from .stable_selection import StableSelection
    archive = OmissionArchive(tok)
    system = role(tok, 'system')+encode(tok, SYSTEM)+encode(tok, '<|im_end|>\n', True)
    archive.record_message('system', tok.decode(system), 0, system)
    student_ids = list(system)
    target_ids = role(tok, 'system')+encode(tok, target_system(arm))+encode(tok, '<|im_end|>\n', True)
    policy = StableSelection()
    previous_prompt = 0
    previous_target_sequence = list(target_ids)
    previous_student_prompt = 0
    if student:
        student.reset()
    records = []
    permanently_omitted = set()
    session_dir = output / arm / case['id']
    session_dir.mkdir(parents=True, exist_ok=False)
    engine_api.target_request(engine, [19,20,21], 1)  # explicit conversation-cache isolation
    for index, turn in enumerate(case['turns']):
        started = time.perf_counter()  # archive, student, retrieval, tokenization and target all charged
        before_delivered = set(archive.delivered)
        before_sources = set(archive.sources)
        new_sources = add_original_turn(tok, archive, student_ids, turn, index)
        if not arm.startswith('full-'):
            archive.save(session_dir/'archive.json')  # Durable original input before omission or inference.
        tr, selected, fetches = None, [], []
        blocked_selection = set()
        if arm.startswith('full-'):
            prompt = list(student_ids)
            positions = None
            target_ids = list(prompt)
        else:
            selected, tr = adaptive_selection(student, student_ids)
            if previous_student_prompt and tr['reused_prefix_tokens'] != previous_student_prompt:
                raise RuntimeError('Student did not reuse the unchanged original prefix')
            if arm.startswith('union'):
                if arm == 'union30':
                    import torch

                    from .core import select_positions
                    selected = select_positions(torch.tensor(tr['scores'], dtype=torch.float16), .3)
                positions, _ = policy.plan(student_ids, selected)
                prompt = [student_ids[p] for p in positions]
            else:
                positions = None
                if target_ids[:len(previous_target_sequence)] != previous_target_sequence:
                    raise RuntimeError('Append-only target prefix was rewritten')
                target_ids.extend(role(tok, 'user'))
                target_ids.extend(encode(tok, source_status(archive, new_sources)))
                handles = archive.selected_chunks(selected)
                if arm == 'append-no-recovery':
                    handles, blocked_selection = block_permanent_omissions(
                        archive, handles, new_sources, permanently_omitted)
                if arm.startswith('fetch-'):
                    handles = [h for h in handles if archive.chunks[h].source in new_sources]
                evidence_tokens, delivered_now = append_evidence(tok, archive, target_ids, handles)
                mode = ('none' if arm == 'append-no-recovery' else
                        'grouped' if arm.startswith('fetch-') else arm.split('-', 1)[1])
                placeholder_text, placeholder_tokens = archive.placeholders(new_sources, mode)
                target_ids.extend(encode(tok, placeholder_text))
                target_ids.extend(question_end(tok, turn['question']))
                prompt = list(target_ids)
        if arm.startswith('full-'):
            for h,c in archive.chunks.items():
                archive.delivered[h] = {'start':c.original_start, 'end':c.original_end, 'epoch':archive.cache_epoch}
        elif arm.startswith('union'):
            compact = {p:i for i,p in enumerate(positions)}
            for h,c in archive.chunks.items():
                if all(p in compact for p in range(c.original_start,c.original_end)):
                    archive.delivered[h] = {'start':compact[c.original_start],
                                            'end':compact[c.original_end-1]+1, 'epoch':archive.cache_epoch}
        first_result = result = engine_api.target_request(engine, prompt, maximum=48 if arm.startswith('fetch-') else 12,
                                                        positions=positions,
                                                        original=len(student_ids) if positions is not None else None)
        first_token = result['first_token_monotonic_s']
        initial_reused = result['engine'].get('reused', 0)
        invalidated = False
        invalidated = archive.confirm_cache(len(prompt), initial_reused, previous_prompt)
        final_prompt = prompt
        if arm.startswith('fetch-'):
            finish_reply(tok, target_ids, result['output_ids'])
            for attempt in range(2):
                handle = retrieval_request(clean_output(tok, result['output_ids']))
                if handle is None:
                    break
                tool_start = time.perf_counter()
                target_ids.extend(role(tok, 'user'))
                target_ids.extend(encode(tok, f'[FETCH_RESULT {handle}]\n'))
                try:
                    handles = archive.resolve(handle)
                    missing = archive.missing(handles)
                    tokens, delivered_tool = append_evidence(tok, archive, target_ids, handles)
                    if not missing:
                        target_ids.extend(encode(tok, 'All requested chunks were delivered earlier; use cached EVIDENCE.\n'))
                    error = None
                except ValueError as exc:
                    tokens, delivered_tool, error = 0, [], str(exc)
                    target_ids.extend(encode(tok, f'Lookup failed: {error}\n'))
                target_ids.extend(question_end(tok, 'Answer the original question now: '+turn['question']))
                tool_s = time.perf_counter()-tool_start
                final_prompt = list(target_ids)
                result = engine_api.target_request(engine, final_prompt, maximum=48)
                archive.confirm_cache(len(final_prompt), result['engine'].get('reused', 0), len(prompt))
                fetches.append({'handle': handle, 'retrieval_s': tool_s, 'appended_evidence_tokens': tokens,
                                'delivered': delivered_tool, 'error': error,
                                'model_ttft_s': result['target_ttft_s'], 'reused': result['engine'].get('reused', 0)})
                finish_reply(tok, target_ids, result['output_ids'])
                prompt = final_prompt
        else:
            if not arm.startswith('union'):
                finish_reply(tok, target_ids, result['output_ids'])
        text = clean_output(tok, result['output_ids'])
        unresolved_fetch = retrieval_request(text) is not None
        correct = re.findall(r'\d{5}', text) == [turn['answer']]
        needed = [h for h,c in archive.chunks.items()
                  if archive.sources[c.source]['path'] == turn['evidence']['path']
                  and archive.sources[c.source]['version'] == turn['evidence']['version']
                  and turn['evidence']['needle'] in c.text]
        # Exact answer spans can cross chunk boundaries: use overlap with source bytes.
        if not needed:
            for s in archive.sources.values():
                if (s['path'],s['version']) != (turn['evidence']['path'],turn['evidence']['version']):
                    continue
                raw, needle = s['text'].encode(), turn['evidence']['needle'].encode()
                at = raw.find(needle)
                needed = [h for h in s['chunks'] if at >= 0 and archive.chunks[h].byte_start < at+len(needle)
                          and archive.chunks[h].byte_end > at]
        was_omitted = bool(needed) and not all(h in before_delivered for h in needed) and all(archive.chunks[h].source in before_sources for h in needed)
        recovered = was_omitted and all(h in archive.delivered for h in needed)
        newly_delivered = set(archive.delivered)-before_delivered
        old_recovered = [h for h in newly_delivered if archive.chunks[h].source in before_sources]
        row = {'case':case['id'], 'split':case['split'], 'turn':index, 'arm':arm,
               'correct':correct, 'output_ids':result['output_ids'], 'text':text,
               'original_tokens':len(student_ids), 'target_prompt_tokens':len(final_prompt),
               'target_input_hash':token_hash(final_prompt), 'target_reused':initial_reused,
               'expected_cached_prefix':previous_prompt, 'cache_invalidated':invalidated,
               'first_visible_ttft_s':first_token-started,
               'pipeline_ttft_s':result['first_token_monotonic_s']-started if not unresolved_fetch else None,
               'charged_latency_s':(result['first_token_monotonic_s']-started if not unresolved_fetch else time.perf_counter()-started),
               'student_s':tr['student_selection_s'] if tr else 0,
               'student_reused':tr['reused_prefix_tokens'] if tr else 0,
               'adaptive_rate':tr.get('adaptive_rate') if tr else None,
               'policy_arm':tr.get('policy_arm') if tr else None,
               'placeholder_tokens':placeholder_tokens if arm.startswith(('append-','fetch-')) else 0,
               'evidence_tokens':evidence_tokens if arm.startswith(('append-','fetch-')) else 0,
               'delivered_chunks':len(newly_delivered), 'historical_recovery_chunks':len(old_recovered),
               'already_delivered_selection_skipped':len(set(archive.selected_chunks(selected)) & before_delivered) if tr else 0,
               'previously_omitted_needed':was_omitted, 'recovery_succeeded':recovered,
               'selection_hash':token_hash(selected),
               'selected_source_handles':archive.selected_chunks(selected),
               'permanently_omitted_handles':sorted(permanently_omitted),
               'blocked_selected_handles':sorted(blocked_selection),
               'needed_permanently_omitted':bool(set(needed) & permanently_omitted),
               'needed_handles':needed, 'needed_chunks_delivered':all(h in archive.delivered for h in needed) if needed else False,
               'off_task_delivered_chunks':len(newly_delivered-set(needed)),
               'fetches':fetches, 'unresolved_fetch':unresolved_fetch,
               'peak_student_mib':tr['peak_student_allocator_mib'] if tr else None,
               'prefix_exact':True if not arm.startswith('union') else None,
               'position_mode':'historical' if positions is not None else 'new_sequential'}
        if arm.startswith('union'):
            row['target_reused'] = first_result['engine'].get('reused',0)
        previous_prompt = len(final_prompt)
        if not arm.startswith('union'):
            previous_target_sequence = list(target_ids)
        previous_student_prompt = len(student_ids)
        finish_reply(tok, student_ids, result['output_ids'])
        archive.record_message('actual_assistant_reply', tok.decode(result['output_ids']), previous_student_prompt, result['output_ids'])
        records.append(row)
        with (output/'requests.jsonl').open('a') as f:
            f.write(json.dumps(row)+'\n')
        archive.save(session_dir/'archive.json')
        (session_dir/'transcript.json').write_text(json.dumps({'student_ids':student_ids, 'target_ids':target_ids,
                                                             'last_prompt_ids':final_prompt, 'actual_outputs':[r['output_ids'] for r in records]})+'\n')
        print(json.dumps({k:row[k] for k in ('case','turn','arm','correct','target_prompt_tokens','target_reused','pipeline_ttft_s','previously_omitted_needed','recovery_succeeded','placeholder_tokens')}), flush=True)
    # One explicit eviction/rebuild probe, outside measured growing turns.
    if arm == 'append-grouped' and case['id'] == 'held-versions':
        engine_api.target_request(engine, [19,20,21], 1)
        replay = engine_api.target_request(engine, final_prompt, maximum=12)
        if replay['engine'].get('reused',0):
            raise RuntimeError('Eviction probe did not clear target state')
        archive.confirm_cache(len(final_prompt), 0, previous_prompt, 'explicit eviction validation')
        if replay['output_ids'] != result['output_ids']:
            raise RuntimeError('Evicted-cache replay changed the exact output')
        archive.save(session_dir/'archive.json')
        (output/'eviction-check.json').write_text(json.dumps({'reused':0,'output_equal':True,'duplicate_semantic_appends':0})+'\n')
    return records


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('phase', choices=['prepare','run'])
    ap.add_argument('--output',type=Path,default=engine_api.ROOT/'data/strata_specprefill/append-v1')
    ap.add_argument('--student',type=Path)
    ap.add_argument('--arms',nargs='+',choices=ARMS+ABLATION_ARMS,default=list(ARMS[1:]))
    ap.add_argument('--cases',nargs='+')
    ap.add_argument('--strata-root',type=Path,default=engine_api.STRATA)
    ap.add_argument('--config',type=Path,default=engine_api.CONFIG)
    ap.add_argument('--target-tokenizer',type=Path,default=engine_api.TOKENIZER)
    a = ap.parse_args()
    engine_api.STRATA,engine_api.CONFIG,engine_api.TOKENIZER=a.strata_root,a.config,a.target_tokenizer
    tok, _, vocab = engine_api.load_frontend(a.strata_root,a.target_tokenizer)
    if a.phase == 'prepare':
        from .append_cases import prepare_cases
        cases = prepare_cases(tok,engine_api.BASE/'sets.json',a.output/'cases.json')
        print(json.dumps({'frozen_cases':len(cases),'turns':sum(len(c['turns']) for c in cases)}))
        return
    case_path=a.output/'cases.json'
    frozen_hash=hashlib.sha256(case_path.read_bytes()).hexdigest()
    cases=json.loads(case_path.read_text())['cases']
    if a.cases:
        cases=[c for c in cases if c['id'] in a.cases]
        if set(a.cases)!={c['id'] for c in cases}:
            raise ValueError('Unknown case filter')
    student=None
    if any(arm!='full-alone' for arm in a.arms):
        if a.student is None:
            ap.error('--student is required for colocated arms')
        from .cached_student import CachedStudent
        from .core import compatible_ids
        from .student import Student
        allowed=compatible_ids(vocab,json.loads((a.student/'tokenizer.json').read_text()))
        model=Student(a.student,allowed)
        student=CachedStudent(model,block=1024)
        student.trace(encode(tok,'Warmup. '*64),1)
        student.reset()
    for arm in a.arms:
        # Full-alone must be invoked in a separate process with no student resident.
        if arm=='full-alone' and student is not None:
            raise ValueError('Run full-alone separately to preserve standalone GPU residency')
    engine=engine_api.start_engine(True,700 if a.arms==['full-alone'] else 4096,
                                   a.output/('engine-'+a.arms[0]+'.log'), selected=a.arms!=['full-alone'])
    try:
        (a.output/('info-'+a.arms[0]+'.json')).write_text(json.dumps(engine.info,indent=2)+'\n')
        from .gpu_memory import MemorySampler
        sampler = MemorySampler()
        for arm in a.arms:
            sampler.start()
            for case in cases:
                if hashlib.sha256(case_path.read_bytes()).hexdigest()!=frozen_hash:
                    raise RuntimeError('Frozen cases changed during inference')
                run_session(engine,student,tok,case,arm,a.output)
            (a.output/('memory-'+arm+'.json')).write_text(json.dumps(sampler.stop(),indent=2)+'\n')
    finally:
        engine.close()
    (a.output/('COMPLETE-'+a.arms[0]+'.json')).write_text(json.dumps({'arms':a.arms,'cases':[c['id'] for c in cases],
                                           'frozen_sha256':frozen_hash})+'\n')


if __name__=='__main__':
    main()
