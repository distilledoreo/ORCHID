"""Parked student states, so switching threads or restarting doesn't re-read whole histories.

A thread's student prefix cache (CachedStudent.export_state) is ~12 KB per token
of history: about 3 GB at 250K. The most recently parked states stay in host RAM
(a switch back costs a copy to the GPU); every parked state is also written to
disk in the background, so a restart resumes from the file. Both tiers are
caches: a missing, stale or corrupt entry only means the history is read again.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
from collections import OrderedDict
from pathlib import Path


def state_bytes(state):
    total = state['ids'].numel() * state['ids'].element_size()
    for layer in state['layers']:
        for value in layer.values():
            tensors = value.values() if isinstance(value, dict) else [value]
            total += sum(t.numel() * t.element_size() for t in tensors if hasattr(t, 'numel'))
    return total


class StudentParking:
    def __init__(self, directory, ram_bytes, disk_bytes, torch):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ram_bytes, self.disk_bytes, self.torch = ram_bytes, disk_bytes, torch
        self.ram = OrderedDict()
        self.lock = threading.Lock()
        self.writes = queue.Queue()
        self.writer = threading.Thread(target=self._write_loop, name='orchid-student-parking', daemon=True)
        self.writer.start()

    def path(self, key):
        return self.directory / f'{key}.pt'

    def put(self, key, state):
        """Park a state: kept in RAM within budget, written to disk in the background."""
        size = state_bytes(state)
        with self.lock:
            self.ram.pop(key, None)
            if size <= self.ram_bytes:
                self.ram[key] = (state, size)
                while sum(s for _, s in self.ram.values()) > self.ram_bytes:
                    self.ram.popitem(last=False)
        if self.disk_bytes > 0 and size <= self.disk_bytes:
            self.writes.put((key, state))

    def get(self, key):
        with self.lock:
            entry = self.ram.get(key)
            if entry:
                self.ram.move_to_end(key)
                return entry[0]
        path = self.path(key)
        if not path.exists():
            return None
        try:
            # Memory-mapped: pages go to the GPU without a second copy of the state in RAM.
            return self.torch.load(path, mmap=True, weights_only=True)
        except (RuntimeError, ValueError, EOFError, KeyError) as error:
            # Corrupt or from another version: read the history again.
            print(f'orchid: parked student state {path.name} unreadable ({error}); discarded', file=sys.stderr)
            path.unlink(missing_ok=True)
            return None
        except Exception as error:  # Transient (memory, I/O): keep the file for the next try.
            print(f'orchid: parked student state {path.name} not loaded ({error})', file=sys.stderr)
            return None

    def discard(self, key):
        with self.lock:
            self.ram.pop(key, None)
        self.path(key).unlink(missing_ok=True)

    def flush(self):
        self.writes.join()

    def _write_loop(self):
        while True:
            key, state = self.writes.get()
            try:
                target = self.path(key)
                temporary = target.with_suffix('.tmp')
                self.torch.save(state, temporary)
                os.replace(temporary, target)
                self._trim_disk()
            except Exception:
                pass  # A cache: a failed write costs a later re-read, never correctness.
            finally:
                self.writes.task_done()

    def _trim_disk(self):
        files = sorted(self.directory.glob('*.pt'), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        while files and total > self.disk_bytes:
            oldest = files.pop(0)
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)
