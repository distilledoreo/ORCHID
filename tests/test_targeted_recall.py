import pytest

from experiments.strata_specprefill.targeted_recall import exact_block, references


def test_reference_binding_keeps_each_symbol_with_its_own_revision():
    q = ('Return field_071() from config/component_2.py revision 1, then '
         'field_063() from config/component_2.py revision 5. Use the exact revisions.')
    assert references(q) == [('config/component_2.py', 1, 'field_071'),
                             ('config/component_2.py', 5, 'field_063')]


def test_exact_block_follows_source_link_and_stops_a_cycle():
    sources = {
        ('config/a.py', 2): {'id': 'S2', 'text': 'ROUTE_KEY = "config/a.py@v1:linked"\n'
                            'def first():\n    return 11\n'},
        ('config/a.py', 1): {'id': 'S1', 'text': 'ROUTE_KEY = "config/a.py@v2:first"\n'
                            'def linked():\n    return 22\n'},
    }
    q = 'In config/a.py revision 2, return first() first. Second, follow its ROUTE_KEY.'
    text, units = exact_block(q, sources, list)
    assert [(u['revision'], u['symbol']) for u in units] == [(2, 'first'), (1, 'linked')]
    assert 'return 11' in text and 'return 22' in text
    assert 'current]' not in text


def test_exact_block_does_not_substitute_other_revision_for_missing_source():
    sources = {('config/a.py', 2): {'id': 'S2', 'text': 'def f():\n    return 22\n'}}
    with pytest.raises(ValueError, match='Unavailable source'):
        exact_block('Return f() from config/a.py revision 1', sources, list)


def test_exact_block_rejects_over_budget_definition_without_truncation():
    sources = {('config/a.py', 1): {'id': 'S1', 'text': 'def f():\n    return 22\n'}}
    with pytest.raises(ValueError, match='token budget'):
        exact_block('Return f() from config/a.py revision 1', sources, list, budget=10)






