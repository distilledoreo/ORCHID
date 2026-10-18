"""Replay the student against frozen target outputs; audit original late recall.

Never runs Flash-Next or changes the benchmark archive. Replayed student choices
must match the original event log before they support stage-level conclusions.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from memory_gateway.specprefill.cached_student import CachedStudent
from memory_gateway.specprefill.compacting_session import CompactingSession, Limits, SummaryEntry
from memory_gateway.specprefill.compaction_adapters import AdaptiveSelector, NativeSummarizer
from memory_gateway.specprefill.core import compatible_ids, token_hash
from memory_gateway.specprefill.paged_archive import PagedArchive
from memory_gateway.specprefill.student import Student

from .run import load_frontend


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def function_span(text, symbol):
    match = re.search(r'^def '+re.escape(symbol)+r'\(\):\n    return (\d+)', text, re.M)
    if match is None:
        raise ValueError(f'Missing function {symbol}')
    return {'start':len(text[:match.start()].encode()), 'end':len(text[:match.end()].encode()),
            'text':match[0], 'value':int(match[1])}


def overlapping(chunks, start, end):
    return [h for h,c in chunks.items() if c.byte_start < end and c.byte_end > start]


class FrozenTarget:
    stop_ids = (248044,248046)

    def __init__(self,tok,calls,outputs,probes,owner):
        self.tok,self.calls,self.outputs,self.probes,self.owner = tok,calls,outputs,probes,owner
        self.index=0

    def __call__(self,ids,maximum):
        call=self.calls[self.index]
        self.index+=1
        assert call['turn']==self.owner.turn and call['input_tokens']==len(ids), ('target prompt mismatch',call['turn'],len(ids),call['input_tokens'])
        if call['phase']=='compact':
            assert maximum==1
            output=self.probes[self.owner.turn]
        else:
            output=self.outputs[self.owner.turn]
            if self.owner.turn>=18:
                self.owner.deliveries[self.owner.turn]={'ids':list(ids),
                    'spans':dict(self.owner.session.target_spans), 'new':list(self.owner.last_bounded)}
                with gzip.open(self.owner.output/f'turn-{self.owner.turn+1}-target.json.gz','wt') as f:
                    json.dump({'token_ids':list(ids),'text':self.tok.decode(ids)},f)
        assert len(output)==call['output_tokens']
        return output


class Audit:
    def __init__(self,args):
        self.args,self.output=args,args.output
        self.output.mkdir(parents=True,exist_ok=False)
        self.turn=-1
        self.phase='answer'
        self.session=None
        self.trace_calls=[]
        self.search_calls=[]
        self.bound_calls=[]
        self.hit_calls=[]
        self.deliveries={}
        self.last_bounded=[]
        self.validations=[]
        self.gold={}

    def run(self):
        run=self.args.run
        arm=run/'specprefill32k'
        cfg=json.loads((arm/'engine-config.json').read_text())
        tok,_,vocab=load_frontend(Path(cfg['cwd']),Path(cfg['tokenizer']))
        assert hashlib.sha256((run/'case.json').read_bytes()).hexdigest()==json.loads((run/'metadata.json').read_text())['case_sha256']
        case=json.loads((run/'case.json').read_text())
        turns=read_rows(arm/'turns.jsonl')
        native=read_rows(arm/'calls.jsonl')
        src=sqlite3.connect(f'file:{arm}/session.archive.sqlite3?mode=ro',uri=True)
        try:
            events=[json.loads(x[0]) for x in src.execute("SELECT value FROM events WHERE kind='session' ORDER BY seq")]
            original_state=json.loads(src.execute('SELECT value FROM session_state').fetchone()[0])
            bodies={p:b.decode() for p,b in src.execute('SELECT path,body FROM sources')}
            copy=sqlite3.connect(self.output/'original.archive.sqlite3')
            try:
                src.backup(copy)
            finally:
                copy.close()
        finally:
            src.close()
        original=PagedArchive(self.output/'original.archive.sqlite3',tok)
        try:
            # Check original source bodies, not merely the fresh replay archive.
            for turn in case['turns']:
                s=turn['source']
                if s:
                    sid=original.source_id(s['path'],s['version'])
                    assert original.sources[sid]['text']==s['text']
            for i,t in enumerate(turns):
                if t['kind']!='late-unasked-recall':
                    continue
                fields=re.findall(r'(field_\d+)\(\) from (\S+) revision (\d+)',t['question'])
                assert len(fields)==2
                actual=[s.strip() for s in t['text'].split(',')]
                records=[]
                for j,(symbol,path,version) in enumerate(fields):
                    sid=original.source_id(path,int(version))
                    source=original.sources[sid]
                    span=function_span(source['text'],symbol)
                    assert span['value']==t['expected'][j]
                    cs={h:original.chunks[h] for h in source['chunks']}
                    handles=overlapping(cs,span['start'],span['end'])
                    records.append({'turn':i+1,'field':j+1,'path':path,'version':int(version),
                        'symbol':symbol,'source_id':sid,'gold_handles':handles,'gold_function':span['text'],
                        'expected':span['value'],'actual':actual[j],'correct':actual[j]==str(span['value']),
                        'archive_body_sha256':source['sha256'],'archive_verified':True})
                self.gold[i]=records
            original_sources={sid:{'path':original.sources[sid]['path'],'version':original.sources[sid]['version'],
                'text':original.sources[sid]['text']} for sid in original.sources
                if original.sources[sid]['path'].startswith('config/')}
        finally:
            original.close()
        answers=[e for e in events if e['kind']=='answer']
        compacts=[e for e in events if e['kind']=='compact']
        outputs={i:list(tok.encode(bodies[f'conversation/{i}/assistant'],parse_special=True)) for i in range(22)}
        output_calls={c['turn']:c for c in native if c['phase']=='answer'}
        assert all(len(outputs[i])==output_calls[i]['output_tokens'] for i in outputs)
        assert outputs[21]==original_state['last_answer']['output_ids']
        summary_rows=read_rows(arm/'summaries.jsonl')
        summaries={}
        probes={}
        for row in summary_rows:
            text=re.sub(r'<\|[^>]+\|>','',row['text']).strip()
            try:
                parsed=json.loads(text)
            except json.JSONDecodeError:
                parsed=None
            if isinstance(parsed,list):
                summaries[row['turn']]=parsed
            else:
                probes[row['turn']]=list(tok.encode(row['text'],parse_special=True))
        calls=[c for c in native if c['turn']>=0 and not(c['phase']=='compact' and c['output_tokens']>1)]
        target=FrozenTarget(tok,calls,outputs,probes,self)
        summarizer=NativeSummarizer(target,tok,context_limit=32768,output_tokens=1024)
        real_summary_input=summarizer.input_ids
        def frozen_summary(request):
            c=next(c for c in native if c['turn']==self.turn and c['phase']=='compact' and c['output_tokens']>1)
            assert len(real_summary_input(request))==c['input_tokens']
            return [SummaryEntry(x['text'],tuple(x['references'])) for x in summaries[self.turn]]
        allowed=compatible_ids(vocab,json.loads((self.args.student/'tokenizer.json').read_text()))
        selector=AdaptiveSelector(CachedStudent(Student(self.args.student,allowed),block=512))
        limits=Limits(**original_state['limits'])
        session=CompactingSession(tok,selector,target,frozen_summary,system=case['system'],limits=limits,
                                  checkpoint=self.output/'replay.json', exact_lookup=False)
        self.session=session
        original_trace=session._trace
        original_hits=session._hits
        original_bound=session._bounded
        original_search=session.search.search

        def trace(ids):
            result=original_trace(ids)
            if self.turn>=18:
                number=sum(x['turn']==self.turn+1 and x['phase']==self.phase for x in self.trace_calls)
                record={'turn':self.turn+1,'phase':self.phase,'trace':number,'input_tokens':len(ids),
                    'input_hash':token_hash(ids),'positions':result['positions'],'scores':result['scores'],
                    'student_spans':dict(session.student_spans),'summary_spans':list(session.summary_spans),
                    'entries':[{'text':e.text,'references':e.references} for e in session.entries]}
                self.trace_calls.append(record)
                with gzip.open(self.output/f'turn-{self.turn+1}-{self.phase}-trace-{number}.json.gz','wt') as f:
                    json.dump({**record,'input_ids':ids},f)
            return result

        def hits(spans,result):
            result_hits=original_hits(spans,result)
            if self.turn>=18:
                selected=set(result['positions'])
                self.hit_calls.append({'turn':self.turn+1,'phase':self.phase,'hits':result_hits,
                    'spans':dict(spans),'selected_counts':{h:sum(p in selected for p in range(a,b))
                                                         for h,(a,b) in spans.items()}})
            return result_hits

        def bound(handles,**kwargs):
            handles=list(handles)
            result=original_bound(handles,**kwargs)
            self.last_bounded=result
            if self.turn>=18:
                self.bound_calls.append({'turn':self.turn+1,'phase':self.phase,'input':handles,'accepted':result,
                    'prefix_tokens':len(kwargs['prefix']),'suffix_tokens':len(kwargs['suffix']),
                    'token_budget':kwargs['token_budget'],'maximum':kwargs['maximum'],'count':kwargs['count']})
            return result

        def search(query,handles,**kwargs):
            scope=list(handles)
            result=original_search(query,scope,**kwargs)
            if self.turn>=18:
                self.search_calls.append({'turn':self.turn+1,'scope':scope,'exclude':list(kwargs.get('exclude',())),
                    'hits':[h.handle for h in result], 'limit':kwargs['limit']})
            return result

        session._trace=trace
        session._hits=hits
        session._bounded=bound
        session.search.search=search
        compaction_index=0
        try:
            for i,t in enumerate(case['turns']):
                self.turn=i
                if turns[i]['compacted_this_turn']:
                    self.phase='compact'
                    compact=session.compact(t['question'])
                    assert compact==compacts[compaction_index], ('compaction event differs',i+1)
                    compaction_index+=1
                self.phase='answer'
                if t['source']:
                    s=t['source']
                    session.add_source(s['path'],s['version'],s['text'],
                        cue='Configuration code, exact revision fields and ROUTE_KEY linkage.')
                if i>=18:
                    # The complete function's presence is checked before retrieval.
                    for record in self.gold[i]:
                        record['student_before']=[h for h in record['gold_handles'] if h in session.student_spans]
                        record['target_before']=[h for h in record['gold_handles'] if h in session.target_spans]
                session.answer(t['question'],maximum=64)
                actual={k:v for k,v in session.events[-1].items() if k != 'exact_lookup'}
                assert actual==answers[i], ('answer event differs',i+1)
                self.validations.append({'turn':i+1,'event_exact':True,'target_prompt_length_exact':True})
                if i>=18:
                    delivery=self.deliveries[i]
                    # Capture final spans: includes both old context and new delivery.
                    delivery['spans']=dict(session.target_spans)
                    for record in self.gold[i]:
                        self.audit_field(record,actual,session,original_sources,tok)
                print(json.dumps({'replayed_turn':i+1,'event_exact':True}),flush=True)
            assert session.target_ids==original_state['target_ids'], 'Final target token IDs differ'
            assert session.student_ids==original_state['student_ids'], 'Final student token IDs differ'
            assert target.index==len(calls)
            result={'case_hash_verified':True,'source_bodies_verified':18,'replayed_answers':22,
                'answer_events_exact':True,'final_target_ids_exact':True,'final_student_ids_exact':True,
                'original_trace_saved':False,'fields':[r for rs in self.gold.values() for r in rs],
                'searches':self.search_calls,'bounds':self.bound_calls,'validation':self.validations}
            (self.output/'audit.json').write_text(json.dumps(result,indent=2))
        finally:
            session.close()
            selector.reset()

    def audit_field(self,r,event,session,sources,tok):
        gold=set(r['gold_handles'])
        stage_hits=[h for h in self.hit_calls if h['turn']==r['turn'] and h['phase']=='answer'][-1]
        searches=[s for s in self.search_calls if s['turn']==r['turn']]
        bounds=[s for s in self.bound_calls if s['turn']==r['turn'] and s['phase']=='answer']
        assert len(bounds)==2
        expansion,delivery=bounds
        r.update({'scope_gold':[h for h in r['gold_handles'] if h in searches[-1]['scope']
                                and h not in searches[-1]['exclude']],
            'retrieval_ranks':{h:searches[-1]['hits'].index(h)+1 for h in r['gold_handles'] if h in searches[-1]['hits']},
            'expanded':[h for h in r['gold_handles'] if h in event['expanded']],
            'student_after_expansion':[h for h in r['gold_handles'] if h in stage_hits['spans']],
            'student_selected_counts':{h:stage_hits['selected_counts'].get(h) for h in r['gold_handles']},
            'student_hit_ranks':{h:stage_hits['hits'].index(h)+1 for h in r['gold_handles'] if h in stage_hits['hits']},
            'new_delivered':[h for h in r['gold_handles'] if h in event['delivered']],
            'target_after':[h for h in r['gold_handles'] if h in session.target_spans],
            'expansion_accepted':len(expansion['accepted']),'expansion_candidates':len(expansion['input']),
            'delivery_accepted':len(delivery['accepted']),
            'expansion_frame_tokens':len(session._evidence(expansion['accepted'])[0]),
            'expansion_budget':expansion['token_budget']})
        target_ids=self.deliveries[r['turn']-1]['ids']
        text=tok.decode(target_ids)
        r['gold_function_text_present']=r['gold_function'] in text
        pattern=r'\[EVIDENCE '+re.escape(r['source_id'])+r' [^\n]+\]\n'
        r['gold_headers']=re.findall(pattern,text)
        wrong=[]
        for sid,source in sources.items():
            for m in re.finditer(r'^def (field_\d+)\(\):\n    return (\d+)',source['text'],re.M):
                if m[2]!=r['actual']:
                    continue
                span=function_span(source['text'],m[1])
                chunks={h:session.archive.chunks[h] for h in session.archive.sources[sid]['chunks']}
                handles=overlapping(chunks,span['start'],span['end'])
                wrong.append({'source_id':sid,'path':source['path'],'version':source['version'],'symbol':m[1],
                    'handles':handles,'target_handles':[h for h in handles if h in session.target_spans],
                    'headers':re.findall(r'\[EVIDENCE '+re.escape(sid)+r' [^\n]+\]\n',text)})
        r['actual_value_sources']=wrong
        r['all_gold_chunks_delivered']=gold <= set(session.target_spans)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--student',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    Audit(args).run()


if __name__=='__main__':
    main()
