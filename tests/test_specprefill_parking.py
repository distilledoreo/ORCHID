"""Parked student states: RAM tier, disk tier, corruption, and the service's switch/restart path."""
import pytest

torch = pytest.importorskip('torch')

from memory_gateway.specprefill.parking import StudentParking, state_bytes  # noqa: E402


def state(n, fill=1.0):
    return {'format': 'f', 'identity': {}, 'ids': torch.arange(n, dtype=torch.int32),
            'layers': [{'kind': 'attention', 'keys': torch.full((1, 2, n, 4), fill), 'values': torch.zeros(1, 2, n, 4)},
                       {'kind': 'linear', 'conv_states': {0: torch.ones(1, 3)}, 'recurrent_states': {0: torch.ones(2)}}]}


def test_ram_tier_is_lru_and_disk_tier_survives_eviction_and_restart(tmp_path):
    one = state_bytes(state(64))
    park = StudentParking(tmp_path, ram_bytes=2 * one, disk_bytes=10 * one, torch=torch)
    for i, key in enumerate('abc'):
        park.put(key, state(64, fill=float(i)))
    park.flush()
    assert list(park.ram) == ['b', 'c']  # 'a' fell out of RAM ...
    assert park.get('a')['layers'][0]['keys'][0, 0, 0, 0] == 0  # ... and comes back from disk.
    again = StudentParking(tmp_path, ram_bytes=0, disk_bytes=10 * one, torch=torch)  # A restart.
    assert torch.equal(again.get('c')['layers'][0]['keys'], state(64, fill=2)['layers'][0]['keys'])
    assert again.get('missing') is None


def test_corrupt_files_are_dropped_and_disk_stays_within_budget(tmp_path):
    probe = StudentParking(tmp_path / 'probe', ram_bytes=0, disk_bytes=2**30, torch=torch)
    probe.put('x', state(64))
    probe.flush()
    one = (tmp_path / 'probe' / 'x.pt').stat().st_size  # On-disk size, container overhead included.
    park = StudentParking(tmp_path, ram_bytes=0, disk_bytes=int(2.5 * one), torch=torch)
    (tmp_path / 'bad.pt').write_bytes(b'not a tensor file')
    assert park.get('bad') is None and not (tmp_path / 'bad.pt').exists()
    for key in 'abcd':
        park.put(key, state(64))
        park.flush()
    assert sorted(f.stem for f in tmp_path.glob('*.pt')) == ['c', 'd']
    park.discard('d')
    assert park.get('d') is None


def test_service_parks_on_switch_and_resumes_after_restart(tmp_path):
    from test_specprefill_gateway_auto import TOOL, Resources, chat, reply, service

    class ParkingStudent:
        """The fake student, plus the CachedStudent parking interface (state = last traced ids)."""

        def __init__(self, inner):
            self.inner, self.student, self.ids, self.imports = inner, type('S', (), {'torch': torch}), None, []
            self.resumed = []  # Per trace: did it continue from the state it held?

        def trace(self, ids, keep_ratio=0.30, **kwargs):
            self.resumed.append(bool(self.ids) and list(ids[:len(self.ids)]) == self.ids)
            self.ids = list(ids)
            return self.inner.trace(ids, keep_ratio, **kwargs)

        def reset(self):
            self.ids = None

        def export_state(self):
            return None if self.ids is None else {'format': 'x', 'identity': {}, 'layers': [],
                                                  'ids': torch.tensor(self.ids, dtype=torch.int32)}

        def import_state(self, saved):
            self.imports.append(None if saved is None else len(saved['ids']))
            self.ids = None if saved is None else saved['ids'].tolist()
            return saved is not None

    def resources():
        r = Resources()
        r.student = r.selector = ParkingStudent(r.student)
        return r
    s, r = service(tmp_path)
    s.resources_factory = lambda: r
    r.student = r.selector = ParkingStudent(r.student)
    a = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA')))
    s.complete(None, None, None, chat(('user', doc_b := 'Other thread. ' + TOOL + '\n\nReturn KEYB')))
    assert r.student.imports == [None, None]  # Neither thread had a parked state yet.
    s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA'), ('assistant', reply(a)),
                                      ('user', 'Return KEYC')))
    assert r.student.imports[-1] > 0  # Thread A came back from parking, not from scratch.
    s.close()  # Parks the active thread to disk.
    s2, _ = service(tmp_path)
    r2 = resources()
    s2.resources_factory = lambda: r2
    s2.complete(None, None, None, chat(('user', doc_b), ('assistant', '22222'), ('user', 'Return KEYA')))
    assert r2.student.imports[-1] > 0 and r2.student.resumed[-1]  # After a restart, B resumed from disk.
    s2.close()


def test_switching_threads_saves_the_outgoing_flash_next_state_and_restores_the_incoming(tmp_path):
    from test_specprefill_gateway_auto import TOOL, Resources, chat, reply, service

    class SessionFiles(Resources):
        def __init__(self):
            super().__init__()
            self.files, self.log = {}, []

        def save_target(self, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'session')  # The engine writes the file.
            self.files[path.name] = True
            self.log.append(('save', path.stem[:6]))
            return {'tokens': 1}

        def restore_target(self, path):
            self.log.append(('restore', path.stem[:6], path.name in self.files))
            return {'tokens': 1} if path.name in self.files else None
    r = SessionFiles()
    s, _ = service(tmp_path)
    s.resources_factory = lambda: r
    a = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA')))
    b = s.complete(None, None, None, chat(('user', 'Other. ' + TOOL + '\n\nReturn KEYB')))
    again = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA'), ('assistant', reply(a)),
                                              ('user', 'Return KEYC')))
    ka, kb = a['orchid']['thread'][:6], b['orchid']['thread'][:6]
    assert r.log == [('restore', ka, False), ('save', ka), ('restore', kb, False),
                     ('save', kb), ('restore', ka, True)]
    assert set(again['orchid']['resume']) >= {'student_s', 'target_save_s', 'target_restore_s'}
    s.close()
    assert r.log[-1] == ('save', ka)  # Shutdown keeps the active thread's Flash-Next state.


def test_alternating_threads_always_find_their_session_file_under_a_tight_hot_budget(tmp_path):
    from test_specprefill_gateway_auto import TOOL, Resources, chat, reply, service

    class SessionFiles(Resources):
        def __init__(self):
            super().__init__()
            self.restores = []

        def save_target(self, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'x' * 1000)
            return {'tokens': 1}

        def restore_target(self, path):
            found = path.exists()
            self.restores.append(found)
            return {'tokens': 1} if found else None
    r = SessionFiles()
    s, _ = service(tmp_path, target_hot_mib=0.0015)  # ~1,570 bytes: room for one 1,000-byte file, not two.
    s.resources_factory = lambda: r
    history = {'A': [('user', TOOL + '\n\nReturn KEYA')], 'B': [('user', 'Other. ' + TOOL + '\n\nReturn KEYB')]}
    for step in range(6):
        name = 'AB'[step % 2]
        result = s.complete(None, None, None, chat(*history[name]))
        history[name] += [('assistant', reply(result)), ('user', f'Return KEY{"ABC"[step % 3]}')]
    s._copies.join()
    assert r.restores[2:] == [True] * 4  # After the first visit of each, every switch found its file.
    assert len(list((tmp_path / 'native' / 'target-cache').glob('*.bin'))) == 2  # Both reached disk.
    s.close()
