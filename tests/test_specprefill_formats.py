"""Phase 6 versioning: every released state format opens with the current code and its
thread continues (fixtures written by each release's own code: fixtures/formats/make.py)."""
import shutil
from pathlib import Path

import pytest
from test_specprefill_gateway_auto import TOOL, chat, service

FIXTURES = Path(__file__).parent / 'fixtures' / 'formats'
RELEASES = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())


def test_every_release_has_a_fixture():
    assert RELEASES == ['v0-pre-plan', 'v1-phase1', 'v2-phase2', 'v3-phase3', 'v4-phase4']


@pytest.mark.parametrize('release', RELEASES)
def test_a_released_state_directory_opens_and_its_thread_continues(release, tmp_path):
    shutil.copytree(FIXTURES / release, tmp_path / 'native')
    s, _ = service(tmp_path)
    history = [('user', TOOL + '\n\nReturn KEYA'), ('assistant', '11111'), ('user', 'Return KEYB'),
               ('assistant', '22222'), ('user', 'Return KEYC')]
    if release == 'v0-pre-plan':  # Threads were named by headers then.
        result = s.complete('proj', 'thr', 'r3', chat(*history))
    else:
        result = s.complete(None, None, None, chat(*history))
        assert result['orchid']['engine'] == 'lean'
    assert result['choices'][0]['message']['content'] == '33333'
    assert len(s.sessions) == 1  # The stored thread continued; no new thread was made.
    session = next(iter(s.sessions.values()))
    assert session.turn == 3  # Two stored turns plus this one: nothing was re-read as new.
    checkpoints = [p.name for p in (tmp_path / 'native').iterdir()]
    assert any(n.endswith('.sqlite3') and n != 'gateway.sqlite3' for n in checkpoints)  # Migrated to the store.
    s.close()
