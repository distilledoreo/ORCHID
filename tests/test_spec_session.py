import math
import re

from memory_gateway.specprefill.spec_session import SpecLimits, SpecSession


class Tok:
    def encode(self, text, parse_special=False):
        return list(text.encode())

    def decode(self, ids):
        return bytes(ids).decode(errors='replace')

    def token_bytes(self, token):
        return bytes([token])


class Student:
    """Scores bytes of any needle named in the last question highly."""

    def __init__(self):
        self.calls, self.resets = [], 0

    def reset(self):
        self.resets += 1

    def trace(self, ids, keep_ratio):
        self.calls.append(list(ids))
        text = Tok().decode(ids)
        question = text[text.rfind('<|im_start|>user'):]
        scores = [0.01] * len(ids)
        for word in re.findall(r'KEY[A-Z0-9]+', question):
            at = text.find(word + '=')
            while at >= 0:
                for p in range(at, min(len(ids), at + len(word) + 8)):
                    scores[p] = 1.0
                at = text.find(word + '=', at + 1)
        return {'scores': scores, 'reused_prefix_tokens': 0}


THOUGHT = 'ok\n</think>\n\n'


def thought(ids, out, maximum=None):
    """Like Flash-Next with thinking on: a short reasoning before the answer when the
    prompt opens a think block."""
    out = (Tok().encode(THOUGHT) + list(out)) if Tok().decode(ids).endswith('<think>\n') else list(out)
    return out[:maximum] if maximum else out


def question_at(text):
    """Start of the request in the last user message (after the turn's excerpts, if any)."""
    last = text.rfind('<|im_start|>user')
    message = text[last:]
    cut = message.rfind('…\n\n')
    return last + cut + 3 if message.startswith('<|im_start|>user\nRelevant excerpts:') and cut >= 0 else last


class Target:
    stop_ids = ()

    def __init__(self):
        self.calls = []

    def __call__(self, ids, maximum):
        self.calls.append(list(ids))
        text = Tok().decode(ids)
        asked = question_at(text)
        question = text[asked:]
        answers = []
        for word in re.findall(r'KEY[A-Z0-9]+', question):
            body = text[:asked]
            at = body.find(word + '=')
            answers.append(body[at + len(word) + 1:at + len(word) + 6] if at >= 0 else 'UNKNOWN')
        return thought(ids, Tok().encode(','.join(answers)), maximum)


def filler(n, seed):
    return ''.join(chr(97 + (i * seed) % 26) for i in range(n))


def doc(keys, size=4000, seed=7):
    text, step = filler(size, seed), size // (len(keys) + 1)
    for i, (k, v) in enumerate(keys.items(), 1):
        at = i * step
        text = text[:at] + f' {k}={v} ' + text[at:]
    return text


def make(**limits):
    return SpecSession(Tok(), Student(), Target(), system='Use excerpts.',
                       limits=SpecLimits(**{'student_context': 60000, 'target_context': 60000,
                                            'answer_tokens': 32, **limits}))


def test_delivery_respects_budget_and_finds_needle():
    s = make(new_rate=0.1, recall_tokens=64)
    s.add_source('a.py', 1, doc({'KEYA': '11111', 'KEYB': '22222'}))
    out = s.answer('Return KEYA')
    assert Tok().decode(out) == '11111'
    event = s.events[-1]
    assert event['evidence_tokens'] <= math.ceil(0.1 * event['new_tokens']) + 64
    assert event['evidence_tokens'] < 0.2 * event['new_tokens']


def test_omitted_chunk_is_recalled_later_without_a_new_source():
    s = make(new_rate=0.02, recall_tokens=160)
    s.add_source('a.py', 1, doc({'KEYA': '11111', 'KEYB': '22222'}))
    assert Tok().decode(s.answer('Return KEYA')) == '11111'
    assert Tok().decode(s.answer('Return KEYB')) == '22222'
    assert s.events[-1]['new_tokens'] == 0
    assert s.events[-1]['delivered_recall_chunks'] >= 1


def test_target_transcript_is_append_only_and_never_duplicates():
    s = make(new_rate=0.05, recall_tokens=64)
    s.add_source('a.py', 1, doc({'KEYA': '11111'}))
    s.answer('Return KEYA')
    first = list(s.target.calls[-1])
    s.add_source('b.py', 1, doc({'KEYC': '33333'}, seed=11))
    s.answer('Return KEYA KEYC')
    assert s.target.calls[-1][:len(first)] == first
    chosen = [tuple(k) for e in s.events for k in e['chosen']]
    assert len(chosen) == len(set(chosen))


def test_target_rebuild_when_window_fills_keeps_answering():
    s = make(target_context=1400, new_rate=0.1, recall_tokens=64, rebuild_turns=1, relative_floor=0.0)
    for i in range(6):
        s.add_source(f'f{i}.py', 1, doc({f'KEY{i}': f'{i}{i}{i}{i}{i}'}, seed=3 + i))
        assert Tok().decode(s.answer(f'Return KEY{i}')) == f'{i}' * 5
        assert len(s.target.calls[-1]) + 32 <= 1400
    assert s.target_generation >= 1


def test_student_rolls_out_oldest_sources_when_full():
    s = make(student_context=12000, new_rate=0.1, recall_tokens=64)
    for i in range(5):
        s.add_source(f'f{i}.py', 1, doc({f'KEY{i}': f'{i}{i}{i}{i}{i}'}, seed=3 + i))
        assert Tok().decode(s.answer(f'Return KEY{i}')) == f'{i}' * 5
    assert not any(k[0] == 0 for k in s.pos)
    assert len(s.student.calls[-1]) <= 12000
    assert s.student.resets >= 1


def test_rolled_out_chunk_is_recalled_through_lexical_search():
    s = make(student_context=12000, new_rate=0.1, recall_tokens=160)
    s.add_source('f0.py', 1, doc({'KEYZERO': '77777'}, seed=3))
    s.answer('Return KEYZERO')
    for i in range(1, 5):
        s.add_source(f'f{i}.py', 1, doc({f'KEY{i}': f'{i}{i}{i}{i}{i}'}, seed=3 + i))
        s.answer(f'Return KEY{i}')
    assert s.student_rollouts >= 1
    # Fresh target generation forgets f0; force it by clearing what was delivered.
    s.delivered = {k for k in s.delivered if k[0] != 0}
    s.target_ids = s._frame('system', s.system)
    assert Tok().decode(s.answer('Return KEYZERO')) == '77777'
    assert s.events[-1]['retrieved_sources']


def test_chunks_follow_line_boundaries_and_cover_source():
    s = make(chunk_tokens=32)
    text = ''.join(f'def f{i}():\n    return {100000 + i}\n' for i in range(40)) + 'x' * 90
    s.add_source('a.py', 1, text)
    bounds = s.sources[0].bounds
    assert bounds[0][0] == 0 and bounds[-1][1] == len(text)
    assert all(a == b_prev for (a, _), (_, b_prev) in zip(bounds[1:], bounds))
    for a, b in bounds[:-3]:
        assert text[b - 1] == '\n' and b - a <= 32


def test_named_path_and_version_outrank_other_versions():
    from memory_gateway.specprefill.spec_session import mentioned_sources
    sources = [('app/a.py', 1), ('app/a.py', 2), ('app/b.py', 1)]
    w = mentioned_sources('Return X from app/a.py revision 2 and Y from app/b.py', sources)
    assert w[1] > w.get(0, 1.0) and w[2] > 1.0 and w.get(0, 1.0) == 1.0
    assert mentioned_sources('no paths here', sources) == {}


def test_excerpts_carry_their_enclosing_definition_line():
    s = make(chunk_tokens=16)
    body = ''.join(f'    x{i} = {i}\n' for i in range(12))
    text = 'A = 1\n\ndef target_function(items):\n' + body + '    return 4436\n'
    s.add_source('a.py', 1, text)
    last = len(s.sources[0].bounds) - 1
    rendered, cost = s._render([(0, last)])
    shown = Tok().decode(rendered)
    assert 'def target_function(items):\n' in shown and 'return 4436' in shown
    assert cost['framing_tokens'] > 0


def test_checkpoint_restore_round_trips_and_continues(tmp_path):
    path = tmp_path / 's.json'
    s = SpecSession(Tok(), Student(), Target(), system='Use excerpts.', checkpoint=path,
                    limits=SpecLimits(student_context=60000, target_context=60000, answer_tokens=32,
                                      recall_tokens=160))
    s.add_source('a.py', 1, doc({'KEYA': '11111', 'KEYB': '22222'}))
    assert Tok().decode(s.answer('Return KEYA')) == '11111'
    r = SpecSession.restore(path, Tok(), Student(), Target())
    assert (r.target_ids, r.student_ids, r.delivered, r.turn) == (s.target_ids, s.student_ids, s.delivered, s.turn)
    assert r.last_answer['question'] == 'Return KEYA'
    assert Tok().decode(r.answer('Return KEYB')) == '22222'


def test_failed_target_call_commits_nothing_and_can_retry():
    s = make(new_rate=0.1, recall_tokens=160)
    s.add_source('a.py', 1, doc({'KEYA': '11111'}))
    before = (list(s.target_ids), set(s.delivered), s.pending_new, s.turn)

    def boom(ids, maximum):
        raise RuntimeError('engine down')
    real, s.target = s.target, boom
    try:
        s.answer('Return KEYA')
    except RuntimeError:
        pass
    assert (s.target_ids, s.delivered, s.pending_new, s.turn) == before
    s.target = real
    assert Tok().decode(s.answer('Return KEYA')) == '11111'


def test_headers_never_claim_current_and_manifest_names_latest():
    s = make()
    s.add_source('a.py', 1, 'A = 1\n')
    first = Tok().decode(s._render([(0, 0)])[0])
    s.add_source('a.py', 2, 'A = 2\n')
    assert '[a.py v1]' in first and 'current' not in first
    assert '[a.py v1, superseded by v2]' in Tok().decode(s._render([(0, 0)])[0])
    assert '[a.py v2]' in Tok().decode(s._render([(1, 0)])[0])
    assert Tok().decode(s._manifest('What is A in a.py now?')) == 'Latest versions: a.py v2\n'
    assert s._manifest('no paths') == []


def test_long_session_soak_keeps_windows_and_recall(tmp_path):
    """Hundreds of turns: repeated target rebuilds and student rollouts."""
    path = tmp_path / 'soak.json'
    s = SpecSession(Tok(), Student(), Target(), system='Use excerpts.', checkpoint=path,
                    limits=SpecLimits(student_context=9000, target_context=2600, answer_tokens=32,
                                      new_rate=0.05, recall_tokens=160, rebuild_turns=2))
    for i in range(240):
        key, value = f'KEY{i}', f'{10000 + i}'
        s.add_source(f'f{i % 7}.py', i // 7 + 1, doc({key: value}, size=1200, seed=3 + i % 11))
        assert Tok().decode(s.answer(f'Return {key}')) == value
        assert len(s.target.calls[-1]) + 32 <= 2600
        assert len(s.student.calls[-1]) <= 9000
        if i % 60 == 59:
            s = SpecSession.restore(path, Tok(), s.student, s.target)
    assert s.target_generation >= 5 and s.student_rollouts >= 5
    # A rolled-out early fact is still recoverable through identifier search.
    s.delivered = {k for k in s.delivered if k[0] != 3}
    s.target_ids = s._frame('system', s.system)
    assert Tok().decode(s.answer('Return KEY3')) == '10003'


def test_contiguous_chunks_share_one_anchor():
    s = make(chunk_tokens=16)
    text = 'def f(items):\n' + ''.join(f'    v{i} = {1000 + i}\n' for i in range(12))
    s.add_source('a.py', 1, text)
    shown = Tok().decode(s._render([(0, 2), (0, 3), (0, 4)])[0])
    assert shown.count('def f(items):') == 1


def test_refresh_reshows_far_back_evidence_only_when_enabled():
    for refresh, expect in ((0, 0), (200, 1)):
        s = make(new_rate=0.05, recall_tokens=160, refresh_tokens=refresh, refresh_distance=300)
        s.add_source('a.py', 1, doc({'KEYA': '11111'}))
        s.answer('Return KEYA')
        for i in range(3):
            s.add_source(f'f{i}.py', 1, doc({f'KEY{i}': f'{i}' * 5}, seed=5 + i))
            s.answer(f'Return KEY{i}')
        s.answer('Return KEYA')
        assert (s.events[-1]['refreshed_chunks'] > 0) == bool(expect)


def test_grouped_last_query_logits_match_repeat_interleave():
    import math

    import pytest
    torch = pytest.importorskip('torch')

    from memory_gateway.specprefill.chunked_student import last_query_logits
    torch.manual_seed(0)
    query, key = torch.randn(1, 8, 3, 16), torch.randn(1, 2, 40, 16)
    ref = (query[:, :, -1:] @ key[:, :, :37].repeat_interleave(4, dim=1).transpose(-1, -2)) / math.sqrt(16)
    assert torch.allclose(last_query_logits(query, key, 37), ref.squeeze(0), atol=1e-5)


def test_long_lines_are_delivered_whole():
    s = make(chunk_tokens=16, new_rate=0.0, recall_tokens=400, relative_floor=0.0)
    line = 'raise IOError("KEYLONG=55555 no such file or directory while reading config")\n'
    s.add_source('a.py', 1, 'x = 1\n' * 20 + line + 'y = 2\n' * 20)
    first = next(k for k in s.chunks(0) if b'KEYLONG' in bytes(s.chunk_ids(k)))
    group = s._line_group(first)
    assert len(group) > 1
    rendered = Tok().decode(s._render(s._choose([(1.0, first)], 400))[0])
    assert 'no such file or directory while reading config")' in rendered


def test_quoted_span_in_request_ranks_its_chunk_first():
    s = make(chunk_tokens=16)
    s.add_source('a.py', 1, ''.join(f'line {i} filler text here\n' for i in range(60)))
    scores = [0.0] * 10**5
    ranked = s._candidates(scores, 'quote the complete line that starts with: line 42 filler text')
    assert b'line 42 filler' in bytes(s.chunk_ids(ranked[0][1]))


def test_sparse_past_with_full_coverage_equals_dense():
    import pytest
    torch = pytest.importorskip('torch')

    from memory_gateway.specprefill.chunked_student import _sparse_past, _tiled_past
    torch.manual_seed(0)
    q, k = torch.randn(1, 2, 64, 32), torch.randn(1, 2, 1024, 32)
    v = torch.randn_like(k)
    dense, dense_lse = _tiled_past(torch, q, k, v, 0.2, tile=256)
    for rows in (16, 48, 64):  # per-group, uneven, and one selection for every row
        sparse, sparse_lse = _sparse_past(torch, q, k, v, 0.2, keep_keys=1024, local=128, rows_per_select=rows)
        assert torch.allclose(dense, sparse, atol=1e-5) and torch.allclose(dense_lse, sparse_lse, atol=1e-5)
