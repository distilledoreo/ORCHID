import math

import pytest

from experiments.strata_specprefill.archive_search import ArchiveSearch
from experiments.strata_specprefill.omission_archive import OmissionArchive


class Tok:
    def encode(self, text):
        return list(text.encode())

    def token_bytes(self, token):
        return bytes([token])


class Embedder:
    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[1.0, 0.0] if any(w in text.lower() for w in ('automobile', 'motorcar')) else [0.0, 1.0]
                for text in texts]


def setup_index(tmp_path=None, **kwargs):
    archive = OmissionArchive(Tok())
    path = tmp_path / 'search.sqlite3' if tmp_path else None
    return archive, ArchiveSearch(archive, path=path, **kwargs)


def add(archive, index, path, text, version=1, cue=''):
    start = sum(len(s['token_ids']) for s in archive.sources.values())
    sid = archive.add(path, version, text, start, 0)
    index.add_source(sid, cue)
    return sid


def test_exact_identifier_crossing_chunk_boundary_and_camel_case_components():
    a, index = setup_index()
    sid = add(a, index, 'config.py', '#' * 29 + '\nHTTP_TIMEOUT_SECONDS = 37\nclass RequestRouter: pass\n')
    hits = index.search('What is HTTP_TIMEOUT_SECONDS?', a.resolve(sid), limit=8)
    assert hits and any('symbol' in h.reasons for h in hits)
    covered = ''.join(a.chunks[h.handle].text for h in sorted(hits, key=lambda h: a.chunks[h.handle].index))
    assert 'HTTP_TIMEOUT_SECONDS' in covered
    assert index.search('request router', a.resolve(sid), limit=8)


def test_scope_excludes_other_sources_versions_and_nearby_chunks():
    a, index = setup_index()
    old = add(a, index, 'x.py', 'SECRET = 11111\n' + 'x' * 80, version=1)
    add(a, index, 'x.py', 'SECRET = 22222\n' + 'y' * 80, version=2)
    scope = a.resolve(old)[:1]
    hits = index.search('SECRET', scope, limit=20, neighbors=4)
    assert {h.handle for h in hits} <= set(scope)
    assert index.search('SECRET', scope, exclude=scope) == []


def test_fts_query_cannot_inject_sql_or_fts_operators_and_empty_result_is_not_fabricated():
    a, index = setup_index()
    add(a, index, 'config', 'SECRET=37', cue='configuration')
    assert index.search('" OR SECRET*; DROP TABLE chunks; --', a.chunks)
    assert index.db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0] == 1
    assert index.search('totally_absent_unicorn_identifier', a.chunks) == []
    assert index.search('the and of', a.chunks) == []


def test_python_function_lookup_brings_scoped_dependency_definition():
    a, index = setup_index()
    helper = add(a, index, 'helper.py', 'def helper():\n    return 37\n')
    target = add(a, index, 'worker.py', 'def entrypoint():\n    return helper()\n')
    hits = index.search('entrypoint', a.chunks, limit=8, neighbors=0)
    assert any(h.handle in a.resolve(target) for h in hits)
    assert any(h.handle in a.resolve(helper) and 'dependency' in h.reasons for h in hits)
    narrow = index.search('entrypoint', a.resolve(target), limit=8, neighbors=0)
    assert all(h.handle in a.resolve(target) for h in narrow)


def test_semantic_retrieval_finds_paraphrase_without_a_keyword_match_and_does_not_reembed_sources():
    embedder = Embedder()
    a, index = setup_index(embedder=embedder, embedding_id='test-v1')
    car = add(a, index, 'vehicle.txt', 'An automobile carries passengers.')
    add(a, index, 'weather.txt', 'Rain falls from clouds.')
    before = len(embedder.calls)
    hits = index.search('motorcar', a.chunks, limit=4, neighbors=0)
    assert hits and hits[0].handle in a.resolve(car)
    assert 'semantic' in hits[0].reasons
    assert len(embedder.calls) == before + 1
    assert embedder.calls[-1] == ['motorcar']


def test_persistent_index_reuses_embeddings_and_rebuilds_when_model_identity_changes(tmp_path):
    embedder = Embedder()
    a, index = setup_index(tmp_path, embedder=embedder, embedding_id='v1')
    sid = add(a, index, 'vehicle.txt', 'An automobile.')
    before = len(embedder.calls)
    index.close()
    other = ArchiveSearch(a, path=tmp_path / 'search.sqlite3', embedder=embedder, embedding_id='v1')
    other.sync({sid: ''})
    assert len(embedder.calls) == before
    other.close()
    changed = ArchiveSearch(a, path=tmp_path / 'search.sqlite3', embedder=embedder, embedding_id='v2')
    changed.sync({sid: ''})
    assert len(embedder.calls) > before
    changed.close()


def test_restart_reconciles_stale_or_reused_source_ids_against_authoritative_archive(tmp_path):
    a, index = setup_index(tmp_path)
    add(a, index, 'old', 'STALE_SECRET=99999')
    add(a, index, 'orphan', 'STALE_SECRET=88888')
    index.close()
    b = OmissionArchive(Tok())
    b.add('new', 1, 'FRESH_KEY=37', 0, 0)
    restored = ArchiveSearch(b, path=tmp_path / 'search.sqlite3')
    restored.sync({})
    assert not restored.search('STALE_SECRET', b.chunks)
    assert restored.search('FRESH_KEY', b.chunks)
    assert restored.db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 1
    restored.close()


@pytest.mark.parametrize('vector', [[0, 0], [math.nan, 1], [math.inf, 1], []])
def test_invalid_embeddings_fail_and_roll_back_derived_source_index(vector):
    class Bad:
        def embed(self, texts):
            return [vector for _ in texts]
    a, index = setup_index(embedder=Bad(), embedding_id='bad')
    sid = a.add('x', 1, 'body', 0, 0)
    with pytest.raises(ValueError):
        index.add_source(sid)
    assert index.db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 0


def test_pinned_definition_unit_emitted_whole_before_fuzzy_hits():
    a, index = setup_index()
    text = 'def field_063():\n' + '    # pad\n' * 40 + '    return 731401\n'
    sid = add(a, index, 'config/component_1.py', text, version=3)
    unit = index.unit_group(a.resolve(sid)[0])
    assert len(unit) > 1  # Definition and return value span chunks.
    hits = index.search('unrelated filler question', a.resolve(sid), limit=32,
                        neighbors=0, dependencies=False, pinned=unit[:1])
    assert [h.handle for h in hits[:len(unit)]] == unit
    assert all('pinned' in h.reasons for h in hits[:len(unit)])
    assert any('unit' in h.reasons for h in hits[1:len(unit)])


def test_direct_hits_precede_dependency_and_neighbor_fill():
    a, index = setup_index()
    body = ''.join(f'def target_{i}():\n    return {i}\n' for i in range(6))
    sid = add(a, index, 'many.py', body)
    scope = a.resolve(sid)
    query = ' '.join(f'target_{i}' for i in range(6))
    hits = index.search(query, scope, limit=6, neighbors=1)
    direct = [h for h in hits if 'symbol' in h.reasons or 'text' in h.reasons]
    fill_only = [h for h in hits if not ({'symbol', 'text', 'pinned'} & set(h.reasons))]
    assert len(direct) >= 6
    if fill_only:
        assert hits.index(direct[-1]) < hits.index(fill_only[0])


def test_bind_references_pins_exact_path_version_symbol_unit():
    a, index = setup_index()
    v3 = add(a, index, 'config/component_1.py', 'def field_063():\n    return 731401\n', version=3)
    v4 = add(a, index, 'config/component_1.py', 'def field_063():\n    return 618409\n', version=4)
    other = add(a, index, 'config/component_2.py', 'def field_063():\n    return 111\n', version=4)
    pinned = index.bind_references('Recall field_063() from config/component_1.py revision 3.')
    assert pinned == index.unit_group(a.resolve(v3)[0])
    assert not set(pinned) & set(a.resolve(v4))
    assert not set(pinned) & set(a.resolve(other))
    assert index.bind_references('Recall field_063 without any path.') == []
    assert index.bind_references('Recall field_063() from config/component_1.py revision 9.') == []
    assert index.bind_references('What is the value?') == []


def test_backfill_units_populates_old_index_without_rebuild(tmp_path):
    a, index = setup_index(tmp_path)
    sid = add(a, index, 'old.py', 'def field_063():\n    return 731401\n')
    assert index.db.execute('SELECT COUNT(*) FROM symbols').fetchone()[0] > 0
    index.db.execute('DELETE FROM definition_units')
    index.db.commit()
    index.close()
    reopened = ArchiveSearch(a, path=tmp_path / 'search.sqlite3')
    reopened.sync({sid: ''})
    try:
        assert reopened.unit_group(a.resolve(sid)[0]) == a.resolve(sid)
        assert reopened.db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 1
    finally:
        reopened.close()


def test_search_bounds_and_embedding_identity_are_required():
    a, index = setup_index()
    with pytest.raises(ValueError, match='identity'):
        ArchiveSearch(a, embedder=Embedder())
    for kwargs in ({'limit': 0}, {'limit': True}, {'neighbors': 5}):
        with pytest.raises(ValueError):
            index.search('query', a.chunks, **kwargs)
