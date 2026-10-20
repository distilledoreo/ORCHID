"""Phase 5: card settings from the measured table, native fallback, the consented student download."""
import hashlib
import http.server
import threading
from pathlib import Path

import pytest

from memory_gateway.specprefill import setup
from memory_gateway.specprefill.service import GatewayOptions

P100 = [('Tesla P100-PCIE-16GB', 16384, 6.0)]


def test_measured_settings_fill_only_what_the_user_left_unset():
    env = {'ORCHID_SPECPREFILL_CONTEXT_TOKENS': '32768'}
    label, reason = setup.apply_auto(env, P100)
    assert reason is None and '16 GB' in label
    assert env['ORCHID_SPECPREFILL_CONTEXT_TOKENS'] == '32768'  # The user's value stays.
    assert env['ORCHID_SPECPREFILL_STUDENT_CONTEXT_TOKENS'] == '262144' and env['ORCHID_SPECPREFILL_TARGET_KV'] == 'fp16'
    assert setup.apply_auto({'ORCHID_SPECPREFILL_AUTO': 'false'}, P100) == (None, None)


def test_unfit_cards_give_a_reason_to_run_native():
    assert 'no NVIDIA GPU' in setup.auto_settings([])[2]
    assert 'measured for 16 GB' in setup.auto_settings([('RTX 3060 Ti', 8192, 8.6)])[2]
    assert 'older than' in setup.auto_settings([('GTX 980', 4096, 5.2)])[2]


def environment(monkeypatch, tmp_path, student):
    config = tmp_path / 'strata.json'
    config.write_text('{}')
    for key, value in {'ENABLED': 'true', 'ENGINE': 'lean', 'STATE_DIR': str(tmp_path / 'state'),
                       'CONFIG': str(config), 'STUDENT': str(student)}.items():
        monkeypatch.setenv('ORCHID_SPECPREFILL_' + key, value)
    monkeypatch.setattr(setup, 'gpus', lambda: P100)


def test_a_missing_student_runs_native_with_a_one_line_notice(monkeypatch, tmp_path, capsys):
    environment(monkeypatch, tmp_path, tmp_path / 'no-student')
    options = GatewayOptions.from_env()
    assert options.engine == 'native' and 'not at' in options.native_reason and options.serves_clients
    err = capsys.readouterr().err
    assert err.count('\n') == 1 and 'SpecPrefill is off' in err and 'native Flash-Next' in err


def test_an_unfit_card_runs_native(monkeypatch, tmp_path):
    environment(monkeypatch, tmp_path, tmp_path)
    monkeypatch.setattr(setup, 'gpus', lambda: [('RTX 3060 Ti', 8192, 8.6)])
    assert GatewayOptions.from_env().engine == 'native'


def test_native_is_a_one_setting_opt_out(monkeypatch, tmp_path):
    environment(monkeypatch, tmp_path, tmp_path / 'no-student')
    monkeypatch.setenv('ORCHID_SPECPREFILL_ENGINE', 'native')
    options = GatewayOptions.from_env()
    assert options.engine == 'native' and 'ENGINE=native' in options.native_reason


def test_native_engine_answers_through_strata(tmp_path):
    from memory_gateway.specprefill.service import SpecPrefillService

    class Native:
        def native(self, payload, on_text=None):
            return {'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'hi'},
                                 'finish_reason': 'stop'}], 'usage': {}}

        def close(self):
            pass
    options = GatewayOptions(str(tmp_path / 's'), 'unused', '', engine='native', native_reason='test')
    s = SpecPrefillService(options, resources_factory=Native)
    result = s.complete(None, None, None, {'messages': [{'role': 'user', 'content': 'hello'}]})
    assert result['choices'][0]['message']['content'] == 'hi' and result['orchid'] == {'engine': 'native',
                                                                                       'reason': 'test'}
    s.close()


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    """A local stand-in for the model host, with two small files and their pinned checksums."""
    root = tmp_path / 'host'
    root.mkdir()
    files = {'config.json': b'{"a": 1}', 'tokenizer.json': b'{"t": 2}'}
    for name, data in files.items():
        (root / name).write_bytes(data)
    monkeypatch.setattr(setup, 'STUDENT_FILES', {n: (hashlib.sha256(d).hexdigest(), len(d)) for n, d in files.items()})
    handler = lambda *a: http.server.SimpleHTTPRequestHandler(*a, directory=str(root))  # noqa: E731
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, f'http://127.0.0.1:{server.server_address[1]}/{{name}}'
    server.shutdown()


def test_the_student_is_fetched_only_after_consent_and_checked(mirror, tmp_path):
    root, url = mirror
    dest = tmp_path / 'student'
    lines = []
    assert not setup.fetch_student(dest, confirm=lambda _: 'n', source=url, out=lines.append)
    assert not dest.exists() and 'Not downloaded' in lines[-1]
    assert setup.fetch_student(dest, confirm=lambda _: 'y', source=url, out=lines.append)
    assert setup.check_student(dest, full=True) is None
    (root / 'config.json').write_bytes(b'{"a": 9}')  # The host now serves something else.
    (dest / 'config.json').unlink()
    assert not setup.fetch_student(dest, confirm=lambda _: 'y', source=url, out=lines.append)
    assert 'checksum' in lines[-1] and not (dest / 'config.json').exists()
    assert not list(Path(dest).glob('*.part'))


def test_fast_kernels_are_off_unless_installed_and_checked():
    use, why = setup.fast_kernels(None)
    assert not use and ('not installed' in why or 'no student' in why)


def test_turning_specprefill_off_and_on_keeps_the_same_state(tmp_path):
    from test_specprefill_gateway_auto import TOOL, chat, reply, service

    from memory_gateway.specprefill.service import SpecPrefillService
    s, _ = service(tmp_path)
    first = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA')))
    s.close()
    off = SpecPrefillService(GatewayOptions(str(tmp_path / 'native'), 'unused', '', engine='native'),
                             resources_factory=lambda: None)  # Opens the same state: no conflict.
    off.close()
    s2, _ = service(tmp_path)
    later = s2.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA'), ('assistant', reply(first)),
                                               ('user', 'Return KEYB')))
    assert reply(later) == '22222' and later['orchid']['thread'] == first['orchid']['thread']
    s2.close()
