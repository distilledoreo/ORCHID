import json

import pytest

from experiments.strata_specprefill.omission_archive import OmissionArchive


class ByteTokenizer:
    def encode(self, text):
        return list(text.encode())

    def token_bytes(self, token):
        return bytes([token])


def test_lossless_unicode_offsets_persistence_and_load_does_not_assume_live_cache(tmp_path):
    tok = ByteTokenizer()
    a = OmissionArchive(tok)
    text = 'a'*31+'🪷 café\n'+'中'*60
    sid = a.add('src/unicode.py', 1, text, 100, 0)
    assert ''.join(a.chunks[h].text for h in a.resolve(sid)) == text
    for h in a.resolve(sid):
        c = a.chunks[h]
        assert text.encode()[c.byte_start:c.byte_end] == c.text.encode()
        assert text[c.char_start:c.char_end] == c.text
        assert c.original_start == c.token_start+100
    evidence, placements = a.evidence(a.resolve(sid))
    a.commit(placements, 10, evidence)
    a.confirm_cache(10+len(tok.encode(evidence)), 0, 0)
    assert a.cached == set(a.resolve(sid))
    path = tmp_path/'archive.json'
    a.save(path)
    b = OmissionArchive.load(path, tok)
    assert b.sources == a.sources and b.delivered == a.delivered
    assert b.cached == set()  # Loading an archive is not restoring recurrent state.
    data = json.loads(path.read_text())
    data['chunks']['S1:0']['original_start'] += 1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='provenance'):
        OmissionArchive.load(path, tok)


def test_handles_grouping_once_only_placeholders_and_no_duplicate_delivery():
    a = OmissionArchive(ByteTokenizer())
    sid = a.add('src/auth.py', 1, 'a'*200, 0, 0)
    text, placements = a.evidence(['S1:0','S1:1'])
    a.commit(placements, 0, text)
    assert a.evidence(['S1:0'])[0] == ''
    grouped, cost = a.placeholders([sid], 'grouped')
    assert 'S1:2-6' in grouped and cost == len(grouped.encode())
    assert a.placeholders([sid], 'grouped') == ('', 0)
    with pytest.raises(RuntimeError, match='Duplicate'):
        a.commit(placements, 0, text)
    for handle in ('S99','S1:999','S1:4-2','../secret','S1:1; shell'):
        with pytest.raises(ValueError):
            a.resolve(handle)


def test_versions_historical_provenance_and_explicit_cache_invalidation():
    a = OmissionArchive(ByteTokenizer())
    a.add('src/auth.py', 1, 'old', 0, 0)
    a.add('src/auth.py', 2, 'new', 10, 1)
    assert 'historical; superseded_by=S2' in a.evidence(['S1:0'])[0]
    assert 'current' in a.evidence(['S2:0'])[0]
    with pytest.raises(ValueError, match='immutable'):
        a.add('src/auth.py', 1, 'old again', 20, 2)
    assert a.confirm_cache(200, 0, 100, 'forced eviction')
    assert a.cache_epoch == 1
    assert a.events[-2]['reason'] == 'forced eviction'


@pytest.mark.parametrize('arm', ['full-alone','append-none','append-no-recovery'])
def test_stateful_delivery_uses_actual_replies_and_preserves_prefix(monkeypatch, tmp_path, arm):
    from experiments.strata_specprefill import append_experiment as exp

    class Tok(ByteTokenizer):
        def encode(self, text, parse_special=False):
            return super().encode(text)
        def decode(self, ids):
            return bytes(ids).decode()
    tok=Tok()
    calls=[]
    live=[]

    def target(engine, ids, maximum=8, positions=None, original=None):
        nonlocal live
        assert positions is None  # New sequential target positions, never historical backfill.
        reused=len(live) if ids[:len(live)]==live else 0
        text=tok.decode(ids)
        output=tok.encode(('22222' if '22222' in text else 'UNKNOWN') if 'SECOND?' in text else '11111')
        live=list(ids)+output
        calls.append(list(ids))
        return {'output_ids':output,'target_ttft_s':.01,'first_token_monotonic_s':exp.time.perf_counter(),
                'engine':{'reused':reused}}
    last_n=0
    def selection(student,ids):
        nonlocal last_n
        text=tok.decode(ids)
        at=text.index('22222' if 'SECOND?' in text else '11111')
        previous=last_n
        last_n=len(ids)
        return list(range(at,at+5)),{'student_selection_s':.001,'reused_prefix_tokens':previous,
                                     'peak_student_allocator_mib':0,'adaptive_rate':.2,'policy_arm':'keep20'}
    monkeypatch.setattr(exp.engine_api,'target_request',target)
    monkeypatch.setattr(exp,'adaptive_selection',selection)
    case={'id':'stateful','split':'held','turns':[
        {'sources':[{'path':'x.py','version':1,'text':'FIRST=11111\n'+'x'*80+'\nSECOND=22222'}],
         'question':'FIRST?','answer':'11111','evidence':{'path':'x.py','version':1,'needle':'11111'}},
        {'sources':[], 'question':'SECOND?', 'answer':'22222',
         'evidence':{'path':'x.py','version':1,'needle':'22222'}},
    ]}
    from types import SimpleNamespace
    records=exp.run_session(object(),SimpleNamespace(reset=lambda:None),tok,case,arm,tmp_path)
    first,second=calls[1:]
    assert second[:len(first)]==first
    assert tok.encode('11111')==second[len(first):len(first)+5]
    assert records[0]['target_prompt_tokens']==len(first)
    assert records[1]['target_prompt_tokens']==len(second)
    assert records[1]['target_reused']>=len(first)
    assert not records[1]['cache_invalidated']
    if arm=='append-none':
        assert records[1]['previously_omitted_needed'] and records[1]['recovery_succeeded']
        assert records[1]['historical_recovery_chunks']>0
    if arm=='append-no-recovery':
        assert not records[1]['correct'] and not records[1]['recovery_succeeded']
        assert records[1]['needed_permanently_omitted']
        assert records[1]['blocked_selected_handles']
        assert records[1]['historical_recovery_chunks']==0
        assert 'SECOND?' in tok.decode(second) and '22222' not in tok.decode(second)


def test_retrieval_request_is_bounded_to_addressable_handles():
    from experiments.strata_specprefill.append_experiment import retrieval_request
    assert retrieval_request('fetch_context(S17:2-8)')=='S17:2-8'
    assert retrieval_request('fetch_context("S1")')=='S1'
    for text in ('fetch_context(../../secret)','fetch_context(S1); run()', 'please fetch_context(S1)'):
        assert retrieval_request(text) is None


def test_source_chunks_align_with_frozen_global_32_token_selection_grid():
    a=OmissionArchive(ByteTokenizer())
    a.add('x.py',1,'a'*80,13,0)
    assert [(c.original_start,c.original_end) for c in a.chunks.values()]==[(13,32),(32,64),(64,93)]
    assert a.selected_chunks(range(32,64))==['S1:1']


def test_fresh_version_probe_ground_truth_is_bound_to_explicit_source_revision(tmp_path):
    import re

    from experiments.strata_specprefill.append_cases import prepare_cases
    prior=tmp_path/'prior.json'
    prior.write_text('[]')
    cases=prepare_cases(ByteTokenizer(),prior,tmp_path/'cases.json')
    for case in cases:
        sources={}
        for turn in case['turns']:
            for src in turn['sources']:
                sources[(src['path'],src['version'])]=src['text']
            name=re.search(r'What does (\w+)\(\)',turn['question'])[1]
            ev=turn['evidence']
            source=sources[(ev['path'],ev['version'])]
            value=re.search(r'^def '+re.escape(name)+r'\(\):\n    return (\d{5})\b',source,re.M)[1]
            assert value==turn['answer']==ev['needle']


def test_model_directed_recovery_keeps_fetch_call_in_answer_latency(monkeypatch,tmp_path):
    from types import SimpleNamespace

    from experiments.strata_specprefill import append_experiment as exp
    class Tok(ByteTokenizer):
        def encode(self,text,parse_special=False):
            return super().encode(text)
        def decode(self,ids):
            return bytes(ids).decode()
    tok=Tok()
    live=[]
    calls=[]
    def target(engine,ids,maximum=8,positions=None,original=None):
        nonlocal live
        text=tok.decode(ids)
        reused=len(live) if ids[:len(live)]==live else 0
        out=('fetch_context(S1)' if 'SECOND?' in text and '22222' not in text
             else '22222' if 'SECOND?' in text else '11111')
        output=tok.encode(out)
        live=list(ids)+output
        calls.append(out)
        return {'output_ids':output,'target_ttft_s':.01,'first_token_monotonic_s':exp.time.perf_counter(),
                'engine':{'reused':reused}}
    previous=0
    def selection(student,ids):
        nonlocal previous
        text=tok.decode(ids)
        at=text.index('22222' if 'SECOND?' in text else '11111')
        old,previous=previous,len(ids)
        return list(range(at,at+5)),{'student_selection_s':0,'reused_prefix_tokens':old,
                                   'peak_student_allocator_mib':0,'adaptive_rate':.2}
    monkeypatch.setattr(exp.engine_api,'target_request',target)
    monkeypatch.setattr(exp,'adaptive_selection',selection)
    case={'id':'tool','split':'held','turns':[
        {'sources':[{'path':'x.py','version':1,'text':'FIRST=11111\n'+'x'*80+'\nSECOND=22222'}],
         'question':'FIRST?','answer':'11111','evidence':{'path':'x.py','version':1,'needle':'11111'}},
        {'sources':[],'question':'SECOND?','answer':'22222',
         'evidence':{'path':'x.py','version':1,'needle':'22222'}}]}
    r=exp.run_session(object(),SimpleNamespace(reset=lambda:None),tok,case,'fetch-grouped',tmp_path)
    assert r[-1]['correct'] and r[-1]['recovery_succeeded']
    assert len(r[-1]['fetches'])==1 and calls[-2:]==['fetch_context(S1)','22222']
    assert r[-1]['pipeline_ttft_s']>r[-1]['first_visible_ttft_s']
    assert r[-1]['fetches'][0]['delivered'] and r[-1]['fetches'][0]['reused']>0
    archive=OmissionArchive.load(tmp_path/'fetch-grouped/tool/archive.json',tok)
    deliveries=[h for e in archive.events if e['event']=='deliver' for h in e['handles']]
    assert len(deliveries)==len(set(deliveries))


def test_repeated_source_version_keeps_handles_tracks_occurrences_and_never_redelivers(tmp_path):
    a=OmissionArchive(ByteTokenizer())
    sid=a.add('x.py',1,'abc '*50,13,0)
    body,placements=a.evidence(a.resolve(sid))
    a.commit(placements,0,body)
    assert a.add('x.py',1,'abc '*50,1000,1)==sid
    assert len(a.sources)==1 and len(a.sources[sid]['occurrences'])==2
    selected=a.selected_chunks(range(1000,1005))
    assert selected==['S1:0']
    assert a.evidence(selected)==('',[])
    path=tmp_path/'archive.json'
    a.save(path)
    b=OmissionArchive.load(path,ByteTokenizer())
    assert b.selected_chunks(range(1000,1005))==selected
    with pytest.raises(ValueError,match='immutable'):
        a.add('x.py',1,'changed',2000,2)


def test_historical_revision_arriving_later_does_not_become_current(tmp_path):
    from experiments.strata_specprefill.append_experiment import source_status
    a=OmissionArchive(ByteTokenizer())
    current=a.add('x.py',2,'new',0,0)
    historical=a.add('x.py',1,'old',100,1)
    assert a.current['x.py']==current
    assert 'HISTORICAL' in source_status(a,[historical])
    assert 'superseded_by=S1' in a.evidence(a.resolve(historical))[0]
    path=tmp_path/'archive.json'
    a.save(path)
    state=json.loads(path.read_text())
    state['current']['x.py']=historical
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError,match='current-version'):
        OmissionArchive.load(path,ByteTokenizer())


def test_permanent_omissions_survive_repeated_source_and_allow_new_revision():
    from experiments.strata_specprefill.append_experiment import block_permanent_omissions
    archive=OmissionArchive(ByteTokenizer())
    sid=archive.add('x.py',1,'a'*96,0,0)
    blocked=set()
    kept,denied=block_permanent_omissions(archive,['S1:0'],[sid],blocked)
    assert kept==['S1:0'] and not denied and blocked=={'S1:1','S1:2'}
    archive.add('x.py',1,'a'*96,1000,1)
    kept,denied=block_permanent_omissions(archive,['S1:1'],[],blocked)
    assert not kept and denied=={'S1:1'}
    revision=archive.add('x.py',2,'b'*96,2000,2)
    handle=archive.sources[revision]['chunks'][0]
    kept,_=block_permanent_omissions(archive,[handle],[revision],blocked)
    assert kept==[handle] and {'S1:1','S1:2'}<=blocked
