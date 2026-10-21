import pytest

from experiments.strata_specprefill.three_way_compaction import MeasuredTarget, score
from memory_gateway.specprefill.compacting_session import SummaryEntry


@pytest.mark.parametrize('text,correct,exact', [
    ('123456,789123',2,True),
    ('<|im_end|>123456, 789123\n',2,True),
    ('789123,123456',0,False),
    ('123456,UNKNOWN',1,False),
    ('123456,789123,444444',2,False),
    ('The answer is 123456,789123',1,False),
])
def test_scoring_requires_correct_order_and_does_not_accept_substring_matches(text,correct,exact):
    assert score(text,[123456,789123]) == {'correct_fields':correct,'fields':2,'exact':exact}


def test_every_native_call_checks_answer_reserve_before_inference(tmp_path):
    target = MeasuredTarget(None, None, 8192, tmp_path)
    with pytest.raises(ValueError,match='exceeds 8192'):
        target([0]*8190,3)


@pytest.mark.parametrize('persistent',[False,True])
def test_reference_overhead_coalesces_summary_without_losing_text_or_provenance(tmp_path,persistent):
    from test_strata_compacting_session import session
    s = session(tmp_path if persistent else None)
    for i in range(18):
        s.add_source(f'file-{i}',1,f'x={i}',cue=f'fact {i}')
    def summarize(request):
        return [SummaryEntry(f'unique fact {i}',(e['id'],)) for i,e in enumerate(request['entries'])]
    s.summarize = summarize
    s.selector.mode = 'none'
    original = [f'unique fact {i}' for i in range(18)]
    event = s.compact('Continue')
    assert event['summary_merge_rounds'] > 0
    assert len(s.entries) < 18
    assert '\n'.join(e.text for e in s.entries).splitlines() == original
    assert {h for e in s.entries for r in e.references for h in s.index.chunks(r)} == set(s.archive.chunks)
    assert len(s._seed(s.entries)[0])-len(s._system()) <= s.limits.summary_tokens
    s.close()


def test_single_oversized_summary_still_fails_closed(tmp_path):
    from test_strata_compacting_session import session
    s = session(tmp_path)
    s.add_source('x',1,'original',cue='original fact')
    s.summarize = lambda request:[SummaryEntry('z'*2000,('M0',))]
    with pytest.raises(ValueError,match='Summary/index exceeds'):
        s.compact('Continue')
    assert s.generation == 0
    s.close()
