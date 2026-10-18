import math

import pytest

from experiments.strata_specprefill.divergence_metrics import compare_rows

np = pytest.importorskip("numpy")


def test_full_vocabulary_kl_direction_and_reference_log_probability():
    p = np.array([[.6, .3, .1], [.1, .2, .7]])
    q = np.array([[.5, .4, .1], [.6, .3, .1]])
    rows = compare_rows(np.log(p), np.log(q), [1, 2])
    assert rows[0]['kl_full_to_selected'] == pytest.approx(sum(a*math.log(a/b) for a, b in zip(p[0], q[0])))
    assert rows[0]['top1_agree'] and not rows[1]['top1_agree']
    assert rows[1]['reference_logp_delta'] == pytest.approx(math.log(.1/.7))


def test_metrics_are_shift_invariant_and_stable_with_extreme_logits():
    a = np.array([[10000., 9999., -10000.]])
    rows = compare_rows(a, a-50000, [2])
    assert rows[0]['kl_full_to_selected'] == pytest.approx(0)
    assert rows[0]['top1_agree']
    assert math.isfinite(rows[0]['full_reference_logp'])
    assert rows[0]['full_reference_logp'] < -19999


@pytest.mark.parametrize('full,selected,reference', [
    ([[1., 2.]], [[1., 2.], [2., 1.]], [0]),
    ([[1., float('nan')]], [[1., 2.]], [0]),
    ([[1., 2.]], [[1., 2.]], [2]),
    ([[1., 2.]], [[1., 2.]], [-1]),
])
def test_metrics_reject_invalid_or_misaligned_inputs(full, selected, reference):
    with pytest.raises(ValueError):
        compare_rows(full, selected, reference)


def test_frozen_case_lengths_and_independent_reference_ground_truth(tmp_path):
    import ast
    import re

    from experiments.strata_specprefill.divergence_experiment import prepare

    class Tok:
        def encode(self, text, parse_special=False):
            return list(text.encode())
        def decode(self, ids):
            return bytes(ids).decode()
    cases = prepare(Tok(), tmp_path)
    assert len(cases) == 12
    for case in cases:
        assert len(case['input_ids']) == case['length']
        text = Tok().decode(case['input_ids'])
        if case['family'] == 'coding':
            actual = [int(re.search(r'\b'+field+r'_\w+ = (\d+)', text)[1])
                      for field in ('BASE', 'MULTIPLIER', 'OFFSET')]
            function = ast.parse(case['reference']).body[0]
            assignments = [s.value.value for s in function.body if isinstance(s, ast.Assign)]
            assert actual == assignments
            assert actual[0]*actual[1]+actual[2] == case['truth']['result']
        else:
            edges = dict(re.findall(r'Relay (\w+) forwards to (\w+)\.', text))
            expected = case['truth']['nodes']
            current = expected[0]
            walked = [current]
            while current in edges:
                current = edges[current]
                walked.append(current)
                assert len(walked) <= 5
            assert walked == expected
            code = int(re.search(r'Terminal '+current+r' stores access code (\d+)\.', text)[1])
            assert code == case['truth']['code']
            assert case['reference'].endswith(f'Code: {code}\n')


def test_reference_content_positions_use_token_byte_overlap_and_word_boundaries():
    from experiments.strata_specprefill.divergence_metrics import content_positions
    text = '119 9 node_7'
    pieces = [b'11', b'9 ', b'9', b' node', b'_7']
    assert content_positions(text, pieces, [9, 'node_7']) == [2, 3, 4]
    with pytest.raises(ValueError, match='roundtrip'):
        content_positions(text, [b'wrong'], [9])
