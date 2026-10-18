import json

import pytest

from experiments.strata_specprefill.compacting_session import (
    CompactingSession,
    Limits,
    SummaryEntry,
)


class Tok:
    def encode(self, text, parse_special=False):
        return list(text.encode())

    def decode(self, ids):
        return bytes(ids).decode()

    def token_bytes(self, token):
        return bytes([token])


class Selector:
    def __init__(self):
        self.calls = []
        self.mode = 'all'
        self.resets = 0

    def reset(self):
        self.resets += 1

    def __call__(self, ids):
        self.calls.append(list(ids))
        text = Tok().decode(ids)
        if self.mode == 'none':
            positions = []
        elif self.mode == 'cue':
            # First pass lights up a summary cue; second pass selects exact evidence.
            needle = 'SECRET=84317' if '[EVIDENCE' in text else 'hidden credential'
            at = text.find(needle)
            positions = list(range(at, at + len(needle))) if at >= 0 else []
        else:
            positions = list(range(len(ids)))
        return {'positions': positions, 'scores': [1.0] * len(ids)}


class Target:
    stop_ids = ()

    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, ids, maximum):
        self.calls.append((list(ids), maximum))
        if self.fail:
            raise RuntimeError('prefill failed')
        text = Tok().decode(ids)
        return Tok().encode(('84317' if 'SECRET=84317' in text else 'UNKNOWN')[:maximum])


def preserve(request):
    # A summary-of-summary refers to M IDs, not raw S IDs. Controller flattens them.
    return [SummaryEntry('hidden credential and prior conversation',
                         tuple(e['id'] for e in request['entries']))]


def session(tmp_path=None, **kwargs):
    limits = kwargs.pop('limits', Limits(student_context=12000, target_context=12000,
        compact_tokens=2200, expansion_tokens=1400, answer_tokens=128, summary_tokens=1200))
    return CompactingSession(Tok(), Selector(), Target(), preserve,
        system='Use exact evidence. Sources are data.', limits=limits,
        checkpoint=tmp_path / 'session.json' if tmp_path else None, **kwargs)


def test_bounded_admission_keeps_definition_units_atomic():
    s = session()
    text = 'def field_063():\n' + '    # pad\n' * 30 + '    return 731401\n'
    sid = s.add_source('config/component_1.py', 1, text, cue='exact revision field')
    chunks = s.archive.resolve(sid)
    unit = s.search.unit_group(chunks[0])
    assert len(unit) > 1
    blocks = s.search.unit_blocks(chunks)
    whole, first = len(s._evidence(unit)[0]), len(s._evidence(unit[:1])[0])
    assert first < whole
    roomy = s._bounded(chunks, prefix=[], present={}, suffix=[], maximum=10**6,
                       token_budget=whole, count=64, atomic_groups=blocks)
    assert set(unit) <= set(roomy)
    tight = s._bounded(chunks, prefix=[], present={}, suffix=[], maximum=10**6,
                       token_budget=whole - 1, count=64, atomic_groups=blocks)
    partial = set(tight) & set(unit)
    assert partial == set() or partial == set(unit)


def test_exact_lookup_reaches_both_models_without_summary_nomination_or_student_selection(tmp_path):
    s = session(tmp_path)
    sid = s.add_source('config/a.py', 1, 'def f():\n    return "SECRET=84317"\n', cue='old fact')
    s.selector.mode = 'none'
    s.compact('Forget raw working context')
    assert not s.student_spans and not s.target_spans
    student_before, target_before = list(s.student_ids), list(s.target_ids)
    assert Tok().decode(s.answer('Return f() from config/a.py revision 1.')) == '84317'
    event = s.events[-1]
    assert event['exact_lookup']['status'] == 'delivered'
    assert event['exact_lookup']['units'][0]['source_id'] == sid
    assert not event['delivered']  # No chunks selected; explicit lookup guarantees delivery.
    assert 'SECRET=84317' in Tok().decode(s.selector.calls[-1])
    assert '[EXACT SOURCE' in Tok().decode(s.target.calls[-1][0])
    assert s.student_ids[:len(student_before)] == student_before
    assert s.target_ids[:len(target_before)] == target_before
    restored = CompactingSession.restore(tmp_path / 'session.json', Tok(), Selector(), Target(), preserve)
    assert restored.exact_lookup_enabled and restored.target_ids == s.target_ids
    restored.close()
    s.close()


def test_exact_lookup_pairs_revisions_and_follows_literal_links_in_normal_answer():
    s = session()
    s.add_source('config/a.py', 1, 'ROUTE_KEY = "NONE"\ndef linked():\n    return 11\n', cue='old')
    s.add_source('config/a.py', 2, 'ROUTE_KEY = "config/a.py@v1:linked"\n'
                 'def first():\n    return 22\n', cue='new')
    s.selector.mode = 'none'
    s.answer('In config/a.py revision 2, return first() first. Second, follow its ROUTE_KEY.')
    assert [(u['revision'], u['symbol']) for u in s.events[-1]['exact_lookup']['units']] == [
        (2, 'first'), (1, 'linked')]
    s.answer('Return linked() from config/a.py revision 1, then first() from config/a.py revision 2.')
    assert [(u['revision'], u['symbol']) for u in s.events[-1]['exact_lookup']['units']] == [
        (1, 'linked'), (2, 'first')]
    s.close()


def test_exact_lookup_and_ordinary_expansion_share_budget():
    s = session()
    s.add_source('config/a.py', 1, 'def f():\n    return 11\n' + '# pad\n' * 30, cue='fact')
    s.answer('Return f() from config/a.py revision 1.')
    e = s.events[-1]
    exact = e['exact_lookup']['tokens']
    assert exact > 0
    assert exact + len(s._evidence(e['expanded'])[0]) <= s.limits.expansion_tokens
    assert exact + len(s._evidence(e['delivered'])[0]) <= s.limits.expansion_tokens
    s.close()


def test_exact_lookup_budget_deferral_never_delivers_partial_definition():
    s = session(limits=Limits(student_context=12000, target_context=12000,
        compact_tokens=2200, expansion_tokens=100, answer_tokens=128, summary_tokens=1200))
    s.add_source('config/a.py', 1, 'def f():\n    return "SECRET=84317"\n', cue='fact')
    s.selector.mode = 'none'
    s.answer('Return f() from config/a.py revision 1.')
    assert s.events[-1]['exact_lookup']['status'] == 'deferred'
    assert '[EXACT SOURCE' not in Tok().decode(s.target.calls[-1][0])
    s.close()


def test_exact_lookup_missing_revision_falls_back_without_substituting_newer_source():
    s = session()
    s.add_source('config/a.py', 2, 'def f():\n    return 11\n', cue='newer')
    s.selector.mode = 'none'
    s.answer('Return f() from config/a.py revision 1.')
    assert s.events[-1]['exact_lookup']['status'] == 'deferred'
    assert 'Unavailable source' in s.events[-1]['exact_lookup']['reason']
    assert '[EXACT SOURCE' not in Tok().decode(s.target.calls[-1][0])
    s.close()






def test_recovery_after_two_compactions_flattens_to_originals_and_preserves_bytes(tmp_path):
    s = session(tmp_path)
    sid = s.add_source('secret.txt', 1, 'SECRET=84317\n' + 'routine filler\n' * 30,
                       cue='hidden credential')
    original = s.archive.sources[sid]['text']
    assert Tok().decode(s.answer('What is the secret?')) == '84317'
    s.selector.mode = 'none'  # Force exact evidence out of both new working contexts.
    s.compact('Continue the task')
    assert not s.target_spans
    assert s.representation(s.archive.resolve(sid)[0]) == 'summarized'
    s.compact('Continue again')
    assert s.generation == 2
    assert all(ref.startswith('A') for e in s.entries for ref in e.references)
    assert set(s.index.chunks(s.entries[0].references[0])) == set(s.archive.chunks)
    assert ''.join(s.archive.chunks[h].text for h in s.archive.resolve(sid)) == original
    s.selector.mode = 'cue'
    before = len(s.selector.calls)
    assert Tok().decode(s.answer('Retrieve the secret again')) == '84317'
    assert len(s.selector.calls) - before == 2  # No recursive/unbounded model loop.
    event = s.events[-1]
    assert event['expanded'] and event['delivered']
    assert s.representation(s.archive.resolve(sid)[0]) == 'exact'
    # No stale global ever-delivered ledger suppressed recovery.
    assert s.archive.delivered == {}
    restored = CompactingSession.restore(tmp_path / 'session.json', Tok(), Selector(), Target(), preserve)
    assert not restored.cache_confirmed
    assert restored.generation == 2
    assert restored.target_ids == s.target_ids
    assert restored.archive.sources == s.archive.sources


def test_append_only_between_compactions_and_no_duplicate_semantic_delivery():
    s = session()
    sid = s.add_source('x', 1, 'SECRET=84317', cue='credential')
    s.answer('Read it')
    old_target, old_student = list(s.target_ids), list(s.student_ids)
    s.answer('Read it again')
    assert s.target_ids[:len(old_target)] == old_target
    assert s.student_ids[:len(old_student)] == old_student
    assert not s.events[-1]['delivered']
    assert s.target_ids.count(ord('S')) > 0
    assert set(s.archive.resolve(sid)) <= s.target_spans.keys()


def test_failed_rebuild_does_not_publish_generation_or_spans(tmp_path):
    s = session(tmp_path)
    s.add_source('x', 1, 'SECRET=84317', cue='credential')
    s.answer('Read it')
    before = (s.generation, list(s.target_ids), dict(s.target_spans), list(s.entries))
    s.target.fail = True
    with pytest.raises(RuntimeError, match='prefill failed'):
        s.compact('Continue')
    assert (s.generation, s.target_ids, s.target_spans, s.entries) == before
    assert not s.cache_confirmed
    loaded = CompactingSession.restore(tmp_path / 'session.json', Tok(), Selector(), Target(), preserve)
    assert loaded.generation == 0
    assert loaded.target_ids == before[1]


@pytest.mark.parametrize('entries', [[], [SummaryEntry('Missing refs', ('S99',))],
    [SummaryEntry('Only one source', ('S1',))], [SummaryEntry('', ('S1', 'S2'))]])
def test_bad_summary_cannot_orphan_sources_or_change_active_context(entries):
    s = session()
    s.add_source('a', 1, 'old', cue='old')
    s.add_source('b', 1, 'new', cue='new')
    s.summarize = lambda request: entries
    before = list(s.target_ids)
    with pytest.raises(ValueError):
        s.compact('Continue')
    assert s.generation == 0 and s.target_ids == before
    assert not s.target.calls


def test_version_identity_unicode_and_old_revision_remain_recoverable():
    s = session()
    first = s.add_source('x', 1, '旧 café 🪷', cue='old version')
    second = s.add_source('x', 2, '新 café 🪷', cue='new version')
    assert s.add_source('x', 1, '旧 café 🪷', cue='old again') == first
    with pytest.raises(ValueError, match='immutable'):
        s.add_source('x', 1, 'changed', cue='bad')
    text, _ = s.archive.evidence(s.archive.resolve(first), present=())
    assert f'historical; superseded_by={second}' in text
    assert s.archive.current['x'] == second


def test_expansion_and_rendered_frame_budgets_are_hard_limits():
    s = session(retrieval_mode='scan')
    sid = s.add_source('large', 1, 'SECRET=84317\n' + 'data\n' * 400,
                       cue='hidden credential')
    s.selector.mode = 'none'
    s.compact('Continue')
    s.selector.mode = 'cue'
    s.answer('Recover')
    expanded, _ = s._evidence(s.events[-1]['expanded'])
    assert len(expanded) <= s.limits.expansion_tokens
    assert len(s.events[-1]['expanded']) <= s.limits.max_candidates
    assert len(s.events[-1]['expanded']) < len(s.archive.resolve(sid))
    assert s.events[-1]['expansion_deferred'] > 0


def test_oversized_ingress_rolls_back_archive_and_prompts(tmp_path):
    s = session(tmp_path)
    before = list(s.student_ids)
    with pytest.raises(ValueError, match='budget'):
        s.add_source('huge', 1, 'x' * 15000, cue='too big')
    assert not s.archive.sources and not s.entries
    assert s.student_ids == before and s.next_original == 0


def test_target_failure_leaves_durable_question_and_retry_uses_actual_reply(tmp_path):
    s = session(tmp_path)
    s.add_source('x', 1, 'SECRET=84317', cue='credential')
    s.target.fail = True
    with pytest.raises(RuntimeError):
        s.answer('Read it')
    assert s.turn == 0
    loaded = CompactingSession.restore(tmp_path / 'session.json', Tok(), Selector(), Target(), preserve)
    assert any(e.text == 'User: Read it' for e in loaded.entries)
    assert Tok().decode(loaded.answer('Read it')) == '84317'
    assert '84317' in Tok().decode(loaded.student_ids)
    assert len([e for e in loaded.entries if e.text == 'User: Read it']) == 1


def test_corrupt_checkpoint_rejected(tmp_path):
    s = session(tmp_path)
    path = s.checkpoint
    state = json.loads(path.read_text())
    state['archive_id'] = 'tampered'
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match='checksum'):
        CompactingSession.restore(path, Tok(), Selector(), Target(), preserve)


def test_native_summary_adapter_parses_structured_refs_and_checks_headroom():
    from experiments.strata_specprefill.compaction_adapters import NativeSummarizer
    request = {'transcript_ids': [65], 'entries': [{'id': 'M0', 'text': 'cue', 'references': ['S1']}],
               'summary_token_budget': 1000, 'question': 'Continue'}
    calls = []
    def target(ids, maximum):
        calls.append(ids)
        return Tok().encode('[{"text":"cue","references":["M0"]}]')
    summary = NativeSummarizer(target, Tok(), context_limit=4096)
    assert summary(request) == [SummaryEntry('cue', ('M0',))]
    assert len(calls) == 1
    with pytest.raises(ValueError, match='headroom'):
        NativeSummarizer(target, Tok(), context_limit=100)(request)
    assert len(calls) == 1


def test_large_manifest_is_scanned_finitely_and_late_evidence_can_return():
    s = session(retrieval_mode='scan')
    sid = s.add_source('large', 1, 'irrelevant filler\n' * 180 + '\nSECRET=84317',
                       cue='hidden credential')
    s.selector.mode = 'none'
    s.compact('Continue')
    s.selector.mode = 'cue'
    assert Tok().decode(s.answer('Find the secret')) == '84317'
    event = s.events[-1]
    assert event['scan_passes'] > 1
    assert event['scan_chunks'] == len(s.archive.resolve(sid))
    assert event['scan_chunks'] == event['expansion_candidates']
    assert len(event['expanded']) < event['scan_chunks']
    assert len(s.selector.calls) == event['scan_passes'] + 3  # compact + nomination + final rescore


def test_manifest_tree_preserves_originals_without_growing_prompt_reference_lists():
    s = session()
    for i in range(8):
        s.add_source(f'file-{i}', 1, 'payload-' + str(i), cue=f'cue-{i}')
    s.selector.mode = 'none'
    s.compact('Continue')
    first_handle = s.entries[0].references[0]
    for _ in range(5):
        s.compact('Continue')
        assert len(s.entries[0].references) == 1
        assert len(s.entries[0].references[0]) == len(first_handle)
        assert set(s.index.chunks(s.entries[0].references[0])) == set(s.archive.chunks)
    assert s.generation == 6
    assert '[M0 refs=A' in Tok().decode(s.student_ids)
    # Manifest internals stay outside the prompt.
    assert 'S8' not in Tok().decode(s.student_ids)


def test_bounded_mode_reports_unexamined_candidates_instead_of_claiming_a_full_scan():
    s = session(retrieval_mode='bounded')
    s.add_source('large', 1, 'filler\n' * 450 + 'SECRET=84317', cue='hidden credential')
    s.selector.mode = 'none'
    s.compact('Continue')
    s.selector.mode = 'cue'
    assert Tok().decode(s.answer('Find the secret')) == 'UNKNOWN'
    assert s.events[-1]['scan_chunks'] == 0
    assert s.events[-1]['expansion_deferred'] > 0


def test_manifest_restore_rejects_missing_edges_and_changed_hashes():
    from experiments.strata_specprefill.archive_manifests import ArchiveManifests
    s = session()
    sid = s.add_source('x', 1, 'payload', cue='cue')
    root = s.index.intern((sid,))
    child = s.index.intern((root, sid))
    assert list(s.index.chunks(child)) == s.archive.resolve(sid)  # Duplicate edge yields no duplicate payload.
    with pytest.raises(ValueError, match='Unknown'):
        ArchiveManifests(s.archive).restore({child: [root, sid]})
    with pytest.raises(ValueError, match='checksum'):
        ArchiveManifests(s.archive).restore({root: ['S1:0']})


def test_indexed_default_recovers_late_evidence_without_student_scanning_the_archive():
    s = session()
    assert s.retrieval_mode == 'indexed'
    sid = s.add_source('large', 1, 'irrelevant filler\n' * 180 + '\nSECRET=84317',
                       cue='hidden credential')
    s.selector.mode = 'none'
    s.compact('Continue')
    s.selector.mode = 'cue'
    before = len(s.selector.calls)
    assert Tok().decode(s.answer('What is the SECRET?')) == '84317'
    event = s.events[-1]
    assert event['scan_passes'] == event['scan_chunks'] == 0
    assert len(s.selector.calls) - before == 2
    assert event['retrieval']['scope_chunks'] == len(s.archive.resolve(sid))
    assert len(event['expanded']) < len(s.archive.resolve(sid)) // 4
    assert all(len(ids) < 2500 for ids in s.selector.calls[before:])


def test_empty_nominated_search_widens_once_without_student_full_scan():
    s = session()
    wrong = s.add_source('notes', 1, 'Nothing relevant here.', cue='hidden credential')
    right = s.add_source('config', 1, 'SECRET=84317', cue='configuration')
    s.summarize = lambda request: [SummaryEntry('hidden credential', (wrong,)),
                                  SummaryEntry('other notes', (right,))]
    s.selector.mode = 'none'
    s.compact('Continue')
    s.selector.mode = 'cue'
    assert Tok().decode(s.answer('What is SECRET?')) == '84317'
    event = s.events[-1]
    assert event['retrieval']['widened']
    assert event['retrieval']['initial_scope_chunks'] < event['retrieval']['scope_chunks']
    assert event['scan_passes'] == 0
    assert any(h in s.archive.resolve(right) for h in event['expanded'])


@pytest.mark.parametrize('mode,expected', [('indexed', 'UNKNOWN'), ('indexed-scan', '84317')])
def test_keyword_miss_only_scans_when_explicitly_enabled(mode, expected):
    s = session(retrieval_mode=mode)
    s.add_source('large', 1, 'filler\n' * 100 + '\nSECRET=84317', cue='hidden credential')
    s.selector.mode = 'none'
    s.compact('Continue')
    s.selector.mode = 'cue'
    assert Tok().decode(s.answer('Recover')) == expected
    assert (s.events[-1]['scan_passes'] > 0) == (mode == 'indexed-scan')


def test_session_restore_reuses_semantic_index_and_retains_source_cues(tmp_path):
    class Embedder:
        def __init__(self):
            self.calls = []
        def embed(self, texts):
            self.calls.append(texts)
            return [[1.0, 0.0] for _ in texts]
    embedder = Embedder()
    s = session(tmp_path, embedder=embedder, embedding_id='v1')
    sid = s.add_source('x', 1, 'SECRET=84317', cue='credential cue')
    s.selector.mode = 'none'
    s.compact('Continue')
    s.close()
    before = len(embedder.calls)
    other = CompactingSession.restore(tmp_path / 'session.json', Tok(), Selector(), Target(), preserve,
                                      embedder=embedder, embedding_id='v1')
    assert other.source_cues[sid] == 'credential cue'
    assert len(embedder.calls) == before
    assert other.search.search('SECRET', other.archive.chunks)
    assert len(embedder.calls) == before + 1  # Query only.
    other.close()
