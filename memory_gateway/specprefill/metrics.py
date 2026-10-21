"""Gateway metrics with no conversation content (production plan, Phase 6).

Counts, timings and byte totals only: turns by engine, why turns went native, the
per-turn time split (student, selection, Flash-Next prefill and decode), Flash-Next
and student cache reuse, rebuilds and verbatim turns, the queue and storage use. No
message text, thread name or tool argument is ever recorded.
"""
from __future__ import annotations

import threading
import time
from collections import Counter

# The reasons a turn goes native are fixed phrases; anything else is counted as 'other'.
REASONS = ('short conversation', 'whole history', 'agent', 'lean failure', 'disk full', 'storage quota',
           'set by ORCHID_SPECPREFILL_ENGINE=native')


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.started = time.time()
        self.turns = Counter()
        self.native_reasons = Counter()
        self.errors = Counter()
        self.seconds = Counter()      # wall, student, select, prefill, decode
        self.tokens = Counter()       # prompt, reused, read, generated, student_prompt, student_reused, evidence
        self.events = Counter()       # rebuilds, verbatim, grounded, broad, timed out / cancelled

    def record(self, result, wall_s, event=None, engine_stats=None):
        """One finished turn: the result's routing, the session's last event, the engine's figures."""
        orchid = result.get('orchid') or {}
        with self.lock:
            engine = orchid.get('engine') or 'compacting'
            self.turns[engine] += 1
            self.seconds['wall'] += wall_s
            if engine == 'native':
                reason = orchid.get('reason') or 'other'
                self.native_reasons[reason if reason in REASONS or reason.startswith('field ') else 'other'] += 1
            if event:
                self.seconds['student'] += event.get('student_s') or 0
                self.seconds['select'] += event.get('select_s') or 0
                self.tokens['evidence'] += event.get('evidence_tokens') or 0
                self.tokens['student_prompt'] += event.get('student_prompt_tokens') or 0
                self.tokens['student_reused'] += event.get('student_reused') or 0
                for name in ('target_rebuilt', 'verbatim', 'broad'):
                    self.events[name] += bool(event.get(name))
                self.events['grounded'] += bool(event.get('grounded'))
            if engine_stats:
                self.seconds['prefill'] += (engine_stats.get('prompt_ms') or 0) / 1000
                self.seconds['decode'] += (engine_stats.get('decode_ms') or 0) / 1000
                prompt, reused = engine_stats.get('prompt_tokens') or 0, engine_stats.get('reused') or 0
                self.tokens['prompt'] += prompt
                self.tokens['reused'] += reused
                self.tokens['read'] += max(0, prompt - reused)
                self.tokens['generated'] += engine_stats.get('generated') or 0

    def error(self, kind):
        with self.lock:
            self.errors[kind] += 1

    def snapshot(self, **extra):
        with self.lock:
            turns = sum(self.turns.values())
            reuse = self.tokens['reused'] / self.tokens['prompt'] if self.tokens['prompt'] else None
            student_reuse = (self.tokens['student_reused'] / self.tokens['student_prompt']
                             if self.tokens['student_prompt'] else None)
            return {'uptime_s': round(time.time() - self.started, 1), 'turns': dict(self.turns),
                    'native_reasons': dict(self.native_reasons), 'errors': dict(self.errors),
                    'seconds': {k: round(v, 3) for k, v in self.seconds.items()},
                    'seconds_per_turn': {k: round(v / turns, 3) for k, v in self.seconds.items()} if turns else {},
                    'tokens': dict(self.tokens), 'events': dict(self.events),
                    'target_cache_reuse': round(reuse, 4) if reuse is not None else None,
                    'student_cache_reuse': round(student_reuse, 4) if student_reuse is not None else None,
                    **extra}
