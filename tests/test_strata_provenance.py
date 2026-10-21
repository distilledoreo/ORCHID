import re

import pytest
from test_strata_omission_archive import ByteTokenizer

from experiments.strata_specprefill.omission_archive import OmissionArchive
from experiments.strata_specprefill.provenance_experiment import (
    add_sources,
    prepare,
    render_evidence,
)


@pytest.mark.parametrize('style', ['bare', 'minimal', 'detailed'])
def test_provenance_retains_exact_payload_order_and_delivery_spans(style):
    archive = OmissionArchive(ByteTokenizer())
    archive.add('src/x.py', 1, 'old café', 0, 0)
    archive.add('src/x.py', 2, 'new 中', 100, 1)
    text, placements = render_evidence(archive, ['S2:0', 'S1:0'], style)
    assert [h for h, _, _ in placements] == ['S2:0', 'S1:0']
    assert [text[a:b] for _, a, b in placements] == ['new 中', 'old café']
    if style == 'bare':
        assert 'src/x.py' not in text and 'historical' not in text and 'version=' not in text
    else:
        assert 'src/x.py version=1' in text and 'historical' in text
        assert ('source_turn=' in text) == (style == 'detailed')
        assert ('bytes=' in text) == (style == 'detailed')
    archive.commit(placements, 0, text)
    assert render_evidence(archive, ['S2:0', 'S1:0'], style) == ('', [])
    with pytest.raises(ValueError, match='provenance'):
        archive.evidence([], 'invalid')


def test_fresh_probes_have_independent_ground_truth_and_matched_payloads(tmp_path, monkeypatch):
    import json

    from experiments.strata_specprefill import provenance_experiment as exp
    prior = tmp_path/'prior'
    prior.mkdir()
    (prior/'cases.json').write_text('{"cases":[]}')
    # Unit tests have no native binary dependency.
    original = exp.Path.read_bytes
    def read_bytes(path):
        return b'fake native binary' if str(path).endswith('/build-cuda12/strata') else original(path)
    monkeypatch.setattr(exp.Path, 'read_bytes', read_bytes)
    cases = prepare(ByteTokenizer(), tmp_path/'run', prior)
    assert len(cases) == 4
    assert len(json.loads((tmp_path/'run/cases.json').read_text())['cases']) == 4
    for case in cases:
        archive = OmissionArchive(ByteTokenizer())
        for i, turn in enumerate(case['turns']):
            add_sources(archive, turn, i)
            source = archive.sources[archive.source_id(turn['evidence']['path'], turn['evidence']['version'])]
            name = re.search(r'What does (\w+)\(\)', turn['question'])[1]
            actual = re.search(r'^def '+name+r'\(\):\n    return (\d{5})', source['text'], re.M)[1]
            assert actual == turn['answer'] == turn['evidence']['needle']
            payloads = []
            for style in ('bare', 'minimal', 'detailed'):
                text, spans = render_evidence(archive, turn['deliver'], style)
                payloads.append([(h, text[a:b]) for h, a, b in spans])
            assert payloads[0] == payloads[1] == payloads[2]
            text, spans = render_evidence(archive, turn['deliver'])
            archive.commit(spans, 0, text)
        assert case['turns'][2]['recovered_chunks'] > 0
        assert case['turns'][3]['recovered_chunks'] > 0
