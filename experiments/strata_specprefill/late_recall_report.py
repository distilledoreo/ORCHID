"""Read-only ranking and budget diagnostics for a validated late-recall replay."""
import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from memory_gateway.specprefill.archive_search import ArchiveSearch, terms
from memory_gateway.specprefill.omission_archive import OmissionArchive

from .run import load_frontend


def direct_ranks(index,query):
    """Reconstruct the original lexical branches without changing their limits."""
    ts=terms(query)
    placeholders=','.join('?' for _ in ts)
    exact=index.db.execute(f'''SELECT s.handle, COUNT(*) AS n FROM symbols s
        JOIN scope x ON x.handle=s.handle WHERE s.term IN ({placeholders})
        GROUP BY s.handle ORDER BY n DESC,s.handle LIMIT 256''',ts).fetchall()
    match=' OR '.join('"'+t.replace('"','""')+'"' for t in ts)
    fts=index.db.execute('''SELECT f.handle FROM search_fts f JOIN scope x ON x.handle=f.handle
        WHERE search_fts MATCH ? ORDER BY bm25(search_fts,0,1,0.4,0.2),f.handle LIMIT 256''',(match,)).fetchall()
    scores=defaultdict(float)
    for rank,(h,_) in enumerate(exact,1):
        scores[h]+=2/(60+rank)
    for rank,(h,) in enumerate(fts,1):
        scores[h]+=1/(60+rank)
    return sorted(scores,key=lambda h:(-scores[h],h))


def diagnostics(run,audit_dir):
    data=json.loads((audit_dir/'audit.json').read_text())
    assert data['answer_events_exact'] and data['final_target_ids_exact'] and data['final_student_ids_exact']
    meta=json.loads((run/'metadata.json').read_text())
    assert hashlib.sha256((run/'case.json').read_bytes()).hexdigest()==meta['case_sha256']
    case=json.loads((run/'case.json').read_text())
    cfg=json.loads((run/'specprefill32k/engine-config.json').read_text())
    tok,_,_=load_frontend(Path(cfg['cwd']),Path(cfg['tokenizer']))
    archive=OmissionArchive(tok)
    index=ArchiveSearch(archive)
    original_start=0

    def add(path,version,text,cue,turn):
        nonlocal original_start
        sid=archive.add(path,version,text,original_start,turn)
        original_start+=len(archive.sources[sid]['token_ids'])
        index.add_source(sid,cue)

    def cost(handles):
        text,_=archive.evidence(handles,present=())
        if not text:
            return 0
        return len(tok.encode('<|im_start|>user\n',parse_special=True))+len(tok.encode(text))+len(tok.encode('<|im_end|>\n',parse_special=True))

    # Reconstruct each historical search index before that turn's question and
    # reply were indexed: future FTS document statistics must not affect ranks.
    import sqlite3
    with sqlite3.connect(f'file:{run}/specprefill32k/session.archive.sqlite3?mode=ro',uri=True) as db:
        replies={int(p.split('/')[1]):b.decode() for p,b in db.execute("SELECT path,body FROM sources WHERE path LIKE 'conversation/%/assistant'")}
    try:
        for i,turn in enumerate(case['turns']):
            if turn['source']:
                s=turn['source']
                add(s['path'],s['version'],s['text'],'Configuration code, exact revision fields and ROUTE_KEY linkage.',i)
            fields=[r for r in data['fields'] if r['turn']==i+1]
            if fields:
                searches=[s for s in data['searches'] if s['turn']==i+1]
                search=searches[-1]
                hits=index.search(turn['question'],search['scope'],limit=64,exclude=search['exclude'])
                assert [h.handle for h in hits]==search['hits'], ('Historical retrieval differs',i+1)
                ranked=direct_ranks(index,turn['question'])
                assert ranked[:min(32,len(ranked))]==search['hits'][:min(32,len(ranked))]
                expansion=next(b for b in data['bounds'] if b['turn']==i+1 and b['phase']=='answer')
                for r in fields:
                    r['direct_fusion_ranks']={h:ranked.index(h)+1 for h in r['gold_handles'] if h in ranked}
                    assert cost(expansion['accepted'])==r['expansion_frame_tokens']
                    r['candidate_budget_trials']=[]
                    for h in r['gold_handles']:
                        if h not in expansion['input']:
                            continue
                        rank=expansion['input'].index(h)
                        before=[x for x in expansion['accepted'] if expansion['input'].index(x)<rank]
                        r['candidate_budget_trials'].append({'handle':h,'candidate_rank':rank+1,
                            'accepted_before':len(before),'trial_frame_tokens':cost(before+[h]),
                            'budget':expansion['token_budget'],'accepted':h in expansion['accepted']})
                    # Offline counterfactual: use only the explicit path+revision
                    # in the question. No answer value enters this lookup query.
                    if not r['correct'] and not r['student_before']:
                        scoped=index.search(f'{r["symbol"]} {r["path"]} revision {r["version"]}',
                            archive.resolve(r['source_id']),limit=64,exclude=search['exclude'])
                        hs=[h.handle for h in scoped]
                        r['path_version_scoped_lookup']={'gold_ranks':{h:hs.index(h)+1 for h in r['gold_handles'] if h in hs},
                            'complete_function':set(r['gold_handles'])<=set(hs),
                            'all_hit_frame_tokens':cost(hs),'answer_value_used_in_query':False}
                    with gzip.open(audit_dir/f'turn-{i+1}-target.json.gz','rt') as f:
                        text=json.load(f)['text']
                    r['gold_value_present_anywhere']=str(r['expected']) in text
                    summary=text.split('Working memory. Summary items are cues;',1)
                    summary=summary[1].split('<|im_end|>',1)[0] if len(summary)>1 else ''
                    r['gold_value_in_summary']=str(r['expected']) in summary
                    if r['actual']!='UNKNOWN' and r['actual'] in summary:
                        at=summary.index(r['actual'])
                        r['actual_value_summary_excerpt']=summary[max(0,at-100):at+len(r['actual'])+100]
            add(f'conversation/{i}/user',1,turn['question'],turn['question'],i)
            add(f'conversation/{i}/assistant',1,replies[i],replies[i][:256],i)
    finally:
        index.close()
    (audit_dir/'findings.json').write_text(json.dumps(data,indent=2))
    return data


def report(data,run,audit_dir,path):
    failures=[r for r in data['fields'] if not r['correct']]
    assert len(failures)==4
    lines=['# Late-recall failure audit: cached 32K SpecPrefill', '',
        '**The counts refer to the same scored fields: four late-recall misses plus one earlier '
        'linked-lookup miss account for 39/44 overall.** All four late gold facts remain exact '
        'in the archive. The failures split into one post-delivery version error, two retrieval '
        'ranking/emission misses, and one partial-retrieval plus expansion-budget failure. '
        'The compaction summary also contains an incorrect version/value association.', '',
        '| Turn / field | Required source | Expected → actual | Archive | Student | Retrieval / expansion | Flash-Next delivery |',
        '|---|---|---|---|---|---|---|']
    descriptions={
        (19,2):('Exact function in student; all 32 chunk tokens selected',
                'Not needed; already in working context','Complete function and correct v3 header delivered'),
        (20,2):('Raw fact absent; correct source admitted to scope',
                'Both gold chunks omitted from the 64 emitted hits','Gold absent; summary supplied wrong value'),
        (21,1):('Raw fact absent; correct source admitted to scope',
                'Gold chunk omitted from the 64 emitted hits','Gold absent; UNKNOWN'),
        (21,2):('Raw fact absent; correct source admitted to scope',
                'Definition chunk at rank 30; return chunk not emitted; expansion rejected definition chunk','Gold absent; UNKNOWN')}
    for r in failures:
        student,retrieval,delivery=descriptions[(r['turn'],r['field'])]
        lines.append(f'| {r["turn"]} / {r["field"]} | `{r["path"]}` v{r["version"]}, `{r["symbol"]}` '
                     f'| {r["expected"]} → {r["actual"]} | Exact bytes verified | {student} | {retrieval} | {delivery} |')
    lines+=['', '## Evidence and attribution', '',
        '### Turn 19, second field: evidence delivered; wrong revision answered', '',
        '`S22:37` contains the complete `field_063` definition returning `731401` from '
        '`config/component_1.py` v3. The student selected all 32 tokens of this chunk; it '
        'was newly appended to the teacher input. Its header correctly identifies version 3. '
        'The returned `618409` belongs to the same symbol in v4 (`S31:36–37`), which was also '
        'already present in the target context. This failure is after delivery; it is not an '
        'archive or retrieval miss.', '',
        'The target had old v4 evidence blocks labelled `current` when they were first appended, '
        'while the newly appended v3 block said `historical; superseded_by=S49`. Those statuses '
        'persist in an append-only prompt. This is an observed prompt inconsistency and a possible '
        'contributor, not a proven internal explanation for the teacher’s choice.', '',
        '### Turn 20, second field: ranking miss and incorrect summary', '',
        'Compaction ran immediately before this question. The correct v4 function spans '
        '`S34:36–37`; both chunks were eligible in the nominated search scope but neither '
        'was emitted by retrieval. Neither the complete function nor its correct value '
        '`476472` appeared anywhere in the teacher input.', '',
        'The working-memory summary instead explicitly said `comp_2 v4 field_063=500609 (S34)`. '
        '`500609` is actually the archived v3 value. The original teacher answer matched that '
        'incorrect summary claim. Structural reference coverage had passed; it did not validate '
        'the truth of the summary’s numeric claims. The same summary also relabelled turn 19’s '
        'wrong answer as a v3 fact. This demonstrates semantic contamination across compaction, '
        'even while the exact underlying archive remains intact.', '',
        '### Turn 21, first field: eligible source lost in retrieval', '',
        '`S7:41` contains the v1 `field_071` value `436414`. It was inside the permitted '
        'retrieval scope, not excluded by version or prior student presence. It was absent '
        'from the 64 emitted retrieval hits, so it never reached the student expansion or '
        'Flash-Next. The teacher’s UNKNOWN is consistent with its missing input.', '',
        '### Turn 21, second field: partial function found, then blocked by expansion budget', '',
        'The v5 function spans `S43:37–38`. Retrieval emitted `S43:37` at rank 30 but omitted '
        'the return-value chunk `S43:38`. The first 29 candidates had already consumed '
        '2,042 of the 2,048 expansion tokens. The rank-30 chunk was not admitted. Neither '
        'chunk was present for the final student scoring or teacher answer; UNKNOWN cannot '
        'be blamed on reasoning over the correct evidence.', '', '## Exact ranking and budget checks', '',
        '| Turn / field | Direct fused ranks of required chunks | Emitted ranks | Rejected expansion trial |',
        '|---|---|---|---|']
    for r in failures:
        trials='; '.join(f'{x["handle"]}: {x["trial_frame_tokens"]}/{x["budget"]} tokens'
                         for x in r['candidate_budget_trials'] if not x['accepted']) or 'Not a candidate / already present'
        lines.append(f'| {r["turn"]} / {r["field"]} | `{r["direct_fusion_ranks"]}` '
                     f'| `{r["retrieval_ranks"]}` | {trials} |')
    lines+=['', 'The lexical retriever ranks direct hits, emits up to 32 seeds, then fills the '
        'remaining 64-hit allowance with dependencies/neighbors before later direct hits. '
        'A high-scoring direct hit can therefore be displaced by earlier seeds’ surrounding '
        'context. Function names and return values can also straddle chunk boundaries.', '',
        '## Bounded offline checks, not a new accuracy benchmark', '',
        'Scoping the existing retriever to each explicit file path and revision in the question '
        '(without using the expected answer value) recovered every required chunk for all three '
        'previously undelivered failed facts. The full scoped hit sets fit within the existing '
        '2,048-token expansion budget:', '',
        '| Turn / field | Correct function complete after scoped lookup? | Scoped hit frame size |',
        '|---|---|---:|']
    for r in failures:
        if 'path_version_scoped_lookup' not in r:
            continue
        scoped=r['path_version_scoped_lookup']
        assert scoped['complete_function'] and scoped['all_hit_frame_tokens']<=2048
        lines.append(f'| {r["turn"]} / {r["field"]} | Yes | {scoped["all_hit_frame_tokens"]} tokens |')
    lines+=['', 'This isolates retrieval precision and admission as fixable bottlenecks. It does '
        'not establish that the teacher would answer correctly after such a change; no new '
        'teacher answers were generated.', '', '## Recommended changes to test next', '',
        '1. Bind explicit path + revision + symbol before fuzzy ranking, and retrieve complete '
        'definitions/returns as units. Preserve those units through expansion admission.',
        '2. Ground numeric summary claims in their cited exact source, or keep summaries as cues '
        'and rehydrate authoritative evidence before answering. Reference coverage alone is insufficient.',
        '3. Replace timeless `current` labels with immutable version/as-of metadata; test whether '
        'that reduces the post-delivery version error. A targeted teacher counterfactual is still needed.', '',
        '## Verification and limits', '',
        'All 18 original configuration source bodies matched the frozen case and passed archive '
        'body checksum validation. Every gold chunk was read through the archive’s chunk/token '
        'checks. The forensic run used a fresh student with the original 512-token block and '
        'expandable allocator, frozen original teacher replies, and the frozen original summary. '
        'All 22 answer events (including ordered retrieval hits, expanded/delivered handles, and '
        'prompt lengths), the compaction event/seed hash, and final student and target token ID '
        'arrays matched the original run exactly. Historical search indexes were separately '
        'reconstructed before each question/reply insertion, avoiding future FTS statistics.', '',
        'Original attention scores were not retained by the benchmark. Student importance '
        'claims here come from this validated replay, not from an original attention dump. '
        'Original teacher outputs are unchanged. No Flash-Next calls, new benchmark sweep, '
        'production retrieval changes, or alterations to the original archive were made.', '',
        f'Original run: [{run}]({run}).', '',
        f'Replayed traces, target prompts, archive snapshot, and machine-readable findings: '
        f'[{audit_dir}]({audit_dir}).', '']
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('\n'.join(lines))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--audit',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    data=diagnostics(args.run,args.audit)
    report(data,args.run,args.audit,args.output)
    print(json.dumps([{k:r[k] for k in ('turn','field','direct_fusion_ranks','retrieval_ranks',
          'candidate_budget_trials','path_version_scoped_lookup') if k in r}
          for r in data['fields'] if not r['correct']],indent=2))


if __name__=='__main__':
    main()
