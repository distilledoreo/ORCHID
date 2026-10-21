from types import SimpleNamespace

import pytest

from experiments.strata_specprefill.late_recall_audit import function_span, overlapping


def test_gold_span_includes_symbol_and_complete_return_with_utf8_byte_offsets():
    text='# 🪷\ndef field_063():\n    return 731401\n'
    span=function_span(text,'field_063')
    assert text.encode()[span['start']:span['end']]==b'def field_063():\n    return 731401'
    assert span['value']==731401


def test_complete_function_requires_both_chunks_when_return_crosses_boundary():
    chunks={'A':SimpleNamespace(byte_start=0,byte_end=20),
            'B':SimpleNamespace(byte_start=20,byte_end=40),
            'C':SimpleNamespace(byte_start=40,byte_end=60)}
    assert overlapping(chunks,8,25)==['A','B']
    assert overlapping(chunks,20,40)==['B']


def test_missing_function_is_rejected_instead_of_using_a_different_field():
    with pytest.raises(ValueError,match='Missing function'):
        function_span('def field_064():\n    return 11','field_063')
