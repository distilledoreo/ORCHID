import hashlib
import json
import sqlite3
import tracemalloc

import pytest
from test_strata_archive_search import Embedder
from test_strata_compacting_session import Selector, Target, Tok, preserve, session

from memory_gateway.specprefill.archive_manifests import ArchiveManifests
from memory_gateway.specprefill.compacting_session import CompactingSession, Limits
from memory_gateway.specprefill.native import NativeResources
from memory_gateway.specprefill.omission_archive import OmissionArchive
from memory_gateway.specprefill.paged_archive import PagedArchive, seal


def test_paged_records_match_memory_archive_exactly_with_unicode_revisions_and_occurrences(tmp_path):
    tok = Tok()
    old, new = OmissionArchive(tok), PagedArchive(tmp_path/'archive.sqlite3', tok, cache_bytes=4096)
    records = [('src/π.py',1,'a'*31+'🪷 café\n中'*25,13,0),
               ('src/π.py',2,'new = 37\n',900,1), ('src/π.py',1,'a'*31+'🪷 café\n中'*25,1200,2)]
    for record in records:
        assert old.add(*record) == new.add(*record)
    assert dict(new.current) == old.current
    assert dict(new.sources) == old.sources
    assert dict(new.chunks) == old.chunks
    for ref in ('S1','S1:1','S1:1-2','S2'):
        assert new.resolve(ref) == old.resolve(ref)
        assert new.evidence(new.resolve(ref)) == old.evidence(old.resolve(ref))
    new.save_session({'schema':2})
    new.close()
    reopened = PagedArchive(tmp_path/'archive.sqlite3', tok, cache_bytes=4096)
    assert reopened.reads['source_bodies'] == reopened.reads['chunks'] == 0
    assert reopened.sources['S1']['text'] == records[0][2]
    assert list(reopened.sources['S1']['token_ids']) == old.sources['S1']['token_ids']
    # Explicit compatibility export is lossless, but is never a checkpoint path.
    restored = OmissionArchive.from_dict(reopened.to_dict(), tok)
    assert restored.sources == old.sources
    reopened.close()


def test_session_descriptor_is_small_stable_and_never_serializes_the_archive(tmp_path, monkeypatch):
    s = session(tmp_path)
    descriptor = s.checkpoint.read_bytes()
    assert len(descriptor) < 1024
    assert json.loads(descriptor)['schema'] == 2
    def forbidden():
        pytest.fail('Paged checkpoints must not materialize the archive')
    monkeypatch.setattr(s.archive, 'to_dict', forbidden)
    s.add_source('first',1,'UNIQUE_RAW_PAYLOAD_84317',cue='first fact')
    s.add_source('second',1,'SECRET=84317',cue='second fact')
    s.answer('What is SECRET?')
    assert s.checkpoint.read_bytes() == descriptor
    state = s.archive.load_session()
    assert not {'archive','events','source_cues','manifests'} & state.keys()
    assert 'UNIQUE_RAW_PAYLOAD' not in s.checkpoint.read_text()
    assert isinstance(s.archive, PagedArchive)
    s.close()


def test_uncheckpointed_source_and_state_are_rolled_back_together(tmp_path):
    path = tmp_path/'archive.sqlite3'
    store = PagedArchive(path,Tok())
    store.add('accepted',1,'first',0,0)
    store.save_session({'schema':2,'turn':1})
    store.add('uncommitted',1,'second',5,1)
    store.session_events.append({'kind':'not committed'})
    store.db.execute('INSERT OR REPLACE INTO session_state VALUES (1,?,?)',
                     ('{"turn":999}',seal({'turn':999})))
    store.close()  # Crash-like close, no commit.
    reopened = PagedArchive(path,Tok())
    assert reopened.load_session()['turn'] == 1
    assert len(reopened.sources) == 1
    assert reopened.source_id('uncommitted',1) is None
    assert not len(reopened.session_events)
    reopened.close()


def test_failed_source_admission_uses_sql_savepoint_without_archive_copy(tmp_path,monkeypatch):
    s = session(tmp_path)
    s.add_source('kept',1,'SECRET=84317',cue='credential')
    initial = s.next_original
    monkeypatch.setattr(s.archive,'to_dict',lambda:pytest.fail('Unexpected archive copy'))
    with pytest.raises(ValueError,match='budget'):
        s.add_source('too large',1,'x'*15000,cue='too large')
    assert s.next_original == initial
    assert len(s.archive.sources) == 1
    assert s.archive.source_id('too large',1) is None
    s.close()
    restored = CompactingSession.restore(tmp_path/'session.json',Tok(),Selector(),Target(),preserve)
    assert len(restored.archive.sources) == 1
    restored.close()


def legacy_session(tmp_path):
    s = CompactingSession(Tok(),Selector(),Target(),preserve,system='Use evidence.',
        limits=Limits(student_context=12000, target_context=12000, compact_tokens=2200,
                      expansion_tokens=1400, answer_tokens=128, summary_tokens=1200), checkpoint=tmp_path/'session.json', _storage=OmissionArchive(Tok()))
    s.add_source('src/π.py',1,'SECRET=84317\n🪷 café',cue='hidden credential')
    s.answer('What is SECRET?')
    s.selector.mode='none'
    s.compact('Continue')
    return s


def test_legacy_checkpoint_migration_preserves_ids_provenance_and_backup(tmp_path):
    old = legacy_session(tmp_path)
    original = old.checkpoint.read_bytes()
    sources, chunks = dict(old.archive.sources), dict(old.archive.chunks)
    target_ids = list(old.target_ids)
    old.close()
    new = CompactingSession.restore(tmp_path/'session.json',Tok(),Selector(),Target(),preserve)
    assert isinstance(new.archive,PagedArchive)
    assert dict(new.archive.sources) == sources
    assert dict(new.archive.chunks) == chunks
    assert new.target_ids == target_ids
    assert new.generation == 1
    assert (tmp_path/'session.legacy.json').read_bytes() == original
    assert len(new.checkpoint.read_bytes()) < 1024
    assert new.archive.load_session()['schema'] == 2
    assert not new.cache_confirmed
    new.selector.mode='cue'
    assert Tok().decode(new.answer('Retrieve SECRET again')) == '84317'
    new.close()


def test_migration_recovers_commit_before_descriptor_publication_without_duplicate_sources(tmp_path):
    old = legacy_session(tmp_path)
    original = old.checkpoint.read_bytes()
    old.close()
    migrated = CompactingSession.restore(tmp_path/'session.json',Tok(),Selector(),Target(),preserve)
    count = len(migrated.archive.sources)
    migrated.close()
    # SQLite committed and backup made, but the process lost the final pointer replace.
    (tmp_path/'session.json').write_bytes(original)
    recovered = CompactingSession.restore(tmp_path/'session.json',Tok(),Selector(),Target(),preserve)
    assert len(recovered.archive.sources) == count
    assert json.loads((tmp_path/'session.json').read_text())['schema'] == 2
    recovered.close()


def test_missing_descriptor_can_be_reconstructed_from_committed_database(tmp_path):
    old = session(tmp_path)
    old.add_source('x',1,'SECRET=84317',cue='credential')
    old.close()
    path=tmp_path/'session.json'
    path.unlink()
    # Exercise the actual native factory's restart decision without loading models.
    native = object.__new__(NativeResources)
    native.tok,native.selector,native.target,native.summarize=Tok(),Selector(),Target(),preserve
    native.archive_cache_bytes=4096
    restored = native.session(path,'unused',session().limits)
    assert path.exists() and len(restored.archive.sources) == 1
    assert restored.archive.cache.budget == 4096
    restored.close()


def test_paged_state_corruption_is_detected_before_inference(tmp_path):
    s = session(tmp_path)
    s.close()
    database=tmp_path/'session.archive.sqlite3'
    with sqlite3.connect(database) as connection:
        value = json.loads(connection.execute('SELECT value FROM session_state').fetchone()[0])
        value['turn']=900
        connection.execute('UPDATE session_state SET value=?',(json.dumps(value),))
    with pytest.raises(ValueError,match='checksum'):
        CompactingSession.restore(tmp_path/'session.json',Tok(),Selector(),Target(),preserve)


@pytest.mark.parametrize('kind',['body','tokens','metadata','chunk'])
def test_source_chunk_and_token_corruption_fail_on_demand(tmp_path,kind):
    store=PagedArchive(tmp_path/'archive.sqlite3',Tok(),cache_bytes=1024)
    store.add('x',1,'ORIGINAL_TEXT',0,0)
    store.save_session({'schema':2})
    if kind=='body':
        store.db.execute("UPDATE sources SET body=?",(b'CORRUPT__TEXT',))
    elif kind=='tokens':
        store.db.execute("UPDATE token_pages SET value=?",(b'bad',))
    elif kind=='metadata':
        store.db.execute("UPDATE sources SET metadata='{}'")
    else:
        store.db.execute("UPDATE chunks SET metadata='{}'")
    store.db.commit()
    store.cache.clear()
    with pytest.raises(ValueError,match='checksum|token page'):
        if kind=='body':
            store.sources['S1']['text']
        else:
            store.chunks['S1:0']
    store.close()


def test_manifest_dedup_and_long_summary_chains_are_streamed_from_disk(tmp_path):
    store=PagedArchive(tmp_path/'archive.sqlite3',Tok(),cache_bytes=4096)
    store.add('x',1,'a'*70,0,0)
    index=ArchiveManifests(store)
    handle=index.intern(('S1','S1:0'))
    for _ in range(1100):
        handle=index.intern((handle,))
    assert list(index.chunks(handle)) == store.resolve('S1')
    assert store.covers((handle,))
    assert store.cache.bytes <= store.cache.budget
    store.save_session({'schema':2})
    store.close()


def test_raw_payload_cache_and_streaming_lookup_stay_bounded_as_archive_grows(tmp_path):
    store=PagedArchive(tmp_path/'archive.sqlite3',Tok(),cache_bytes=16*1024)
    total=0
    for i in range(160):
        text=f'record_{i}=84317\n'+'x'*2048
        store.add(f'file-{i}.txt',1,text,total,0)
        total+=len(text.encode())
    index=ArchiveManifests(store)
    root=index.intern(tuple(store.sources))
    store.save_session({'schema':2})
    store.close()
    cold=PagedArchive(tmp_path/'archive.sqlite3',Tok(),cache_bytes=16*1024)
    assert cold.reads['source_bodies']==cold.reads['chunks']==0
    # Traversing a corpus-sized reference touches IDs/metadata, not raw text.
    selected=cold.select_handles((root,))
    assert len(selected)==len(cold.chunks)
    assert cold.reads['source_bodies']==cold.reads['chunks']==0
    tracemalloc.start()
    for handle in selected:
        assert cold.chunks[handle].text
        assert cold.cache.bytes<=16*1024
    _,peak=tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert cold.reads['source_bodies']==0
    assert total>300_000
    assert peak<1_000_000  # Python payload allocations, not total process RSS/SQLite/OS pages.
    assert cold.db.execute('PRAGMA cache_size').fetchone()[0]==-4096
    cold.close()


def test_restore_with_summary_only_memory_does_not_read_old_raw_payloads(tmp_path):
    s=session(tmp_path)
    s.add_source('x',1,'SECRET=84317'+'a'*2000,cue='hidden credential')
    s.selector.mode='none'
    s.compact('Continue')
    s.close()
    restored=CompactingSession.restore(tmp_path/'session.json',Tok(),Selector(),Target(),preserve,archive_cache_bytes=4096)
    assert restored.archive.reads['source_bodies']==restored.archive.reads['chunks']==0
    assert restored.archive.cache.bytes<=4096
    restored.close()


def test_checkpoint_database_identity_cannot_be_redirected(tmp_path):
    s=session(tmp_path)
    s.close()
    path=tmp_path/'session.json'
    descriptor=json.loads(path.read_text())
    descriptor.pop('state_hash')
    descriptor['archive_id']='other-archive'
    descriptor['state_hash']=hashlib.sha256(json.dumps(descriptor,sort_keys=True).encode()).hexdigest()
    path.write_text(json.dumps(descriptor))
    with pytest.raises(ValueError,match='identity'):
        CompactingSession.restore(path,Tok(),Selector(),Target(),preserve)


def test_legacy_migration_preserves_existing_embedding_index(tmp_path):
    embedder = Embedder()
    s = CompactingSession(Tok(), Selector(), Target(), preserve, system='Use evidence.',
        checkpoint=tmp_path/'session.json', _storage=OmissionArchive(Tok()),
        embedder=embedder, embedding_id='test-v1')
    s.add_source('x.py',1,'SECRET=84317',cue='credential')
    s.close()
    embedder.calls.clear()
    restored = CompactingSession.restore(tmp_path/'session.json', Tok(), Selector(), Target(), preserve,
        embedder=embedder, embedding_id='test-v1')
    assert embedder.calls == []
    assert len(restored.archive.sources) == 1
    restored.close()
