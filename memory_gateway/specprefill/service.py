"""Serialized, restartable text-chat sessions for the opt-in native gateway."""
from __future__ import annotations

import codecs
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from pathlib import Path

from .compacting_session import Limits
from .scheduling import CompactionPolicy
from .spec_session import SpecLimits


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


# Verbatim query when a turn ends with a tool result (the result itself is a source).
TOOL_CONTINUE = 'Continue the task using the tool result above.'


class RequestConflict(ValueError):
    pass


class Warming(RuntimeError):
    """The models are still loading: retry shortly."""


class _StopText:
    """Output text cut at the first stop string. Text that could still begin one is
    held back until it can't (or the output ends), so a stop split across tokens is
    found and never sent."""

    def __init__(self, stops):
        self.stops, self.text, self.held, self.hit = list(stops or ()), '', '', False

    def feed(self, text, final=False):
        if self.hit:
            return ''
        buffer = self.held + text
        cuts = [i for i in (buffer.find(s) for s in self.stops) if i >= 0]
        if cuts:
            out, self.held, self.hit = buffer[:min(cuts)], '', True
        else:
            keep = 0 if final else max((k for s in self.stops for k in range(1, len(s))
                                        if buffer.endswith(s[:k])), default=0)
            out, self.held = buffer[:len(buffer) - keep], buffer[len(buffer) - keep:]
        self.text += out
        return out


@dataclass(frozen=True)
class GatewayOptions:
    state_dir: str
    config_path: str
    student_path: str
    model: str = 'orchid-flash-next-specprefill'
    context_tokens: int = 8192
    compact_tokens: int = 3072
    answer_tokens: int = 128
    expansion_tokens: int = 2048
    summary_tokens: int = 1024
    high_water: float = .70
    resident_sessions: int = 4
    archive_cache_mib: int = 8
    # 'lean': SpecSession (student selects, target reads 5-30%, no target-written
    # summaries). 'compacting': the earlier summary/provenance CompactingSession.
    engine: str = 'compacting'
    student_context_tokens: int = 32768
    new_rate: float = 0.06
    recall_tokens: int = 160
    relative_floor: float = 0.30
    floor_rank: int = 3
    # A final user message longer than this is ingested as a source; only its
    # tail (last paragraph, at most query_tail_tokens) is sent verbatim.
    query_split_tokens: int = 1024
    query_tail_tokens: int = 256
    # VRAM the target engine leaves free for the co-located student. A 256K
    # student needs about 8 GiB (docs/specprefill-goal.md, 256K section).
    vram_reserve_mib: int = 4096
    # Student block-sparse prefill (keys kept per query group); None = dense.
    student_sparse_keys: int | None = None
    # Folded query rows sharing one block selection; None = the kernel default (256).
    student_sparse_rows: int | None = None
    # Target K/V cache type ('int8' or 'fp16'); None keeps the config's. Native
    # 256K needs fp16: int8 K/V returns empty replies past ~50K.
    target_kv: str | None = None
    # Lean engine: generation length when a request names none, and the
    # conversation size below which a new thread is answered by native
    # Flash-Next (SpecPrefill costs a student pass that doesn't pay off there).
    output_tokens: int = 4096
    # Parked student states (production plan, Phase 2): RAM for fast thread
    # switches, disk so a restart resumes instead of re-reading histories.
    student_park_ram_mib: int = 4096
    student_park_disk_mib: int = 16384
    # Flash-Next's parked conversations in RAM (Strata's conversation cache). Off by
    # default: a switch saves and restores session files instead (faster, no RAM).
    target_park_mib: int = 0
    target_disk_mib: int = 16384  # Flash-Next session files (~1.5-2 GB per long thread).
    # Switch-time session files go to RAM-backed /dev/shm first (a save and a restore
    # are memory copies, ~1 s each); a background copy keeps them on disk too.
    target_hot_mib: int = 4096  # Two ~250K-token threads.
    target_hot_dir: str = '/dev/shm'
    target_restore_floor_mib: int = 1024
    # Storage (production plan, Phase 3): archives above the quota (0 = none) or a
    # disk below this much free space make lean turns go native instead of writing.
    state_quota_mib: int = 0
    min_free_disk_mib: int = 1024
    # Load the models (and resume the most recent thread) when the gateway starts;
    # requests meanwhile get a 503 "warming" instead of hanging.
    preload: bool = False
    # Measured (production plan, Phase 1): below the query-split size lean sends the
    # whole message anyway, so its student pass only costs time.
    native_below_tokens: int = 1024

    @property
    def lean(self):
        return self.engine == 'lean'

    @property
    def limits(self):
        if self.lean:
            return SpecLimits(student_context=self.student_context_tokens,
                              target_context=self.context_tokens, answer_tokens=self.answer_tokens,
                              new_rate=self.new_rate, recall_tokens=self.recall_tokens,
                              relative_floor=self.relative_floor, floor_rank=self.floor_rank)
        return Limits(student_context=self.context_tokens, target_context=self.context_tokens,
                      compact_tokens=self.compact_tokens, answer_tokens=self.answer_tokens,
                      expansion_tokens=self.expansion_tokens, summary_tokens=self.summary_tokens)

    @classmethod
    def from_env(cls):
        from ..config import _bool_env
        if not _bool_env('ORCHID_SPECPREFILL_ENABLED', False):
            return None
        required = ['STATE_DIR', 'CONFIG', 'STUDENT']
        values = {key: os.environ.get('ORCHID_SPECPREFILL_' + key) for key in required}
        if not all(values.values()):
            raise ValueError('SpecPrefill requires STATE_DIR, CONFIG, and STUDENT paths')
        for key in ('CONFIG', 'STUDENT'):
            if not Path(values[key]).exists():
                raise ValueError(f'SpecPrefill {key} path does not exist')
        options = cls(state_dir=values['STATE_DIR'], config_path=values['CONFIG'], student_path=values['STUDENT'],
            model=os.environ.get('ORCHID_SPECPREFILL_MODEL', cls.model),
            context_tokens=int(os.environ.get('ORCHID_SPECPREFILL_CONTEXT_TOKENS', '8192')),
            compact_tokens=int(os.environ.get('ORCHID_SPECPREFILL_COMPACT_TOKENS', '3072')),
            answer_tokens=int(os.environ.get('ORCHID_SPECPREFILL_ANSWER_TOKENS', '128')),
            expansion_tokens=int(os.environ.get('ORCHID_SPECPREFILL_EXPANSION_TOKENS', '2048')),
            summary_tokens=int(os.environ.get('ORCHID_SPECPREFILL_SUMMARY_TOKENS', '1024')),
            high_water=float(os.environ.get('ORCHID_SPECPREFILL_HIGH_WATER', '.70')),
            resident_sessions=int(os.environ.get('ORCHID_SPECPREFILL_RESIDENT_SESSIONS', '4')),
            archive_cache_mib=int(os.environ.get('ORCHID_SPECPREFILL_ARCHIVE_CACHE_MIB', '8')),
            engine=os.environ.get('ORCHID_SPECPREFILL_ENGINE', cls.engine),
            student_context_tokens=int(os.environ.get('ORCHID_SPECPREFILL_STUDENT_CONTEXT_TOKENS', '32768')),
            new_rate=float(os.environ.get('ORCHID_SPECPREFILL_NEW_RATE', str(cls.new_rate))),
            recall_tokens=int(os.environ.get('ORCHID_SPECPREFILL_RECALL_TOKENS', str(cls.recall_tokens))),
            relative_floor=float(os.environ.get('ORCHID_SPECPREFILL_RELATIVE_FLOOR', str(cls.relative_floor))),
            floor_rank=int(os.environ.get('ORCHID_SPECPREFILL_FLOOR_RANK', str(cls.floor_rank))),
            query_split_tokens=int(os.environ.get('ORCHID_SPECPREFILL_QUERY_SPLIT_TOKENS', '1024')),
            query_tail_tokens=int(os.environ.get('ORCHID_SPECPREFILL_QUERY_TAIL_TOKENS', '256')),
            vram_reserve_mib=int(os.environ.get('ORCHID_SPECPREFILL_VRAM_RESERVE_MIB', '4096')),
            student_sparse_keys=int(os.environ['ORCHID_SPECPREFILL_STUDENT_SPARSE_KEYS'])
            if os.environ.get('ORCHID_SPECPREFILL_STUDENT_SPARSE_KEYS') else None,
            student_sparse_rows=int(os.environ['ORCHID_SPECPREFILL_STUDENT_SPARSE_ROWS'])
            if os.environ.get('ORCHID_SPECPREFILL_STUDENT_SPARSE_ROWS') else None,
            target_kv=os.environ.get('ORCHID_SPECPREFILL_TARGET_KV') or None,
            output_tokens=int(os.environ.get('ORCHID_SPECPREFILL_OUTPUT_TOKENS', str(cls.output_tokens))),
            student_park_ram_mib=int(os.environ.get('ORCHID_SPECPREFILL_STUDENT_PARK_RAM_MIB',
                                                    str(cls.student_park_ram_mib))),
            student_park_disk_mib=int(os.environ.get('ORCHID_SPECPREFILL_STUDENT_PARK_DISK_MIB',
                                                     str(cls.student_park_disk_mib))),
            target_park_mib=int(os.environ.get('ORCHID_SPECPREFILL_TARGET_PARK_MIB', str(cls.target_park_mib))),
            target_disk_mib=int(os.environ.get('ORCHID_SPECPREFILL_TARGET_DISK_MIB', str(cls.target_disk_mib))),
            target_hot_mib=int(os.environ.get('ORCHID_SPECPREFILL_TARGET_HOT_MIB', str(cls.target_hot_mib))),
            target_restore_floor_mib=int(os.environ.get('ORCHID_SPECPREFILL_TARGET_RESTORE_FLOOR_MIB',
                                                        str(cls.target_restore_floor_mib))),
            state_quota_mib=int(os.environ.get('ORCHID_SPECPREFILL_STATE_QUOTA_MIB', str(cls.state_quota_mib))),
            min_free_disk_mib=int(os.environ.get('ORCHID_SPECPREFILL_MIN_FREE_DISK_MIB', str(cls.min_free_disk_mib))),
            preload=_bool_env('ORCHID_SPECPREFILL_PRELOAD',
                              os.environ.get('ORCHID_SPECPREFILL_ENGINE', cls.engine) == 'lean'),
            native_below_tokens=int(os.environ.get('ORCHID_SPECPREFILL_NATIVE_BELOW_TOKENS',
                                                   str(cls.native_below_tokens))))
        options.validate()
        return options

    def validate(self):
        if self.engine not in ('lean', 'compacting'):
            raise ValueError("SpecPrefill engine must be 'lean' or 'compacting'")
        if self.lean and not (0 <= self.new_rate <= 1 and self.recall_tokens >= 0
                              and 0 < self.query_tail_tokens < self.query_split_tokens):
            raise ValueError('Invalid lean SpecPrefill budgets')
        if self.target_kv not in (None, 'int8', 'fp16'):
            raise ValueError("Target K/V type must be 'int8' or 'fp16'")
        if self.output_tokens < 1 or self.native_below_tokens < 0:
            raise ValueError('Invalid output or native-threshold token count')
        self.limits
        CompactionPolicy(self.high_water)
        if self.resident_sessions < 1:
            raise ValueError('Resident session limit must be positive')
        if type(self.archive_cache_mib) is not int or self.archive_cache_mib < 0:
            raise ValueError('Archive cache size must be a nonnegative MiB count')


class SpecPrefillService:
    def __init__(self, options, *, resources_factory=None):
        options.validate()
        self.options = options
        self.policy = CompactionPolicy(options.high_water)
        self.directory = Path(options.state_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)  # Conversations: readable by their owner only.
        self.owner = (self.directory / 'owner.lock').open('a')
        try:
            fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.owner.close()
            raise RuntimeError('SpecPrefill state directory already has an owner') from None
        self.db = sqlite3.connect(self.directory / 'gateway.sqlite3', check_same_thread=False)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS sessions(key TEXT PRIMARY KEY, history TEXT NOT NULL,
                system TEXT NOT NULL, pending TEXT, growth INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS completions(key TEXT NOT NULL, rid TEXT NOT NULL,
                hash TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(key,rid));
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS history_entries(key TEXT NOT NULL, seq INTEGER NOT NULL,
                hash TEXT NOT NULL, PRIMARY KEY(key,seq));
        ''')
        self._migrate_history_ledger()
        self._migrate_thread_columns()
        configuration = Path(options.config_path)
        student_configuration = Path(options.student_path) / 'config.json'
        # Lean threads stay valid when settings change: only the ledger format and the
        # model name define them. Student and Flash-Next caches check their own settings.
        identity = digest({'runtime': 'specprefill-gateway-v1', 'engine': 'lean', 'ledger': 'identity-v2',
                           'model': options.model}) if options.lean else digest({
            'runtime': 'specprefill-gateway-v1', 'limits': asdict(options.limits),
            **({'engine': 'lean', 'query_split': [options.query_split_tokens, options.query_tail_tokens],
                **({'student_sparse_keys': options.student_sparse_keys}
                   if options.student_sparse_keys else {}),
                **({'student_sparse_rows': options.student_sparse_rows}
                   if options.student_sparse_rows else {})}
               if options.lean else {}),
            **({'target_kv': options.target_kv} if options.target_kv else {}),
            # The lean ledger keys messages by normalized identity (requests.identity).
            **({'ledger': 'identity-v2'} if options.lean else {}),
            'config': str(configuration.resolve()), 'student': str(Path(options.student_path).resolve()),
            'model': options.model,
            'config_hash': hashlib.sha256(configuration.read_bytes()).hexdigest() if configuration.is_file() else None,
            'student_config_hash': hashlib.sha256(student_configuration.read_bytes()).hexdigest()
                if student_configuration.is_file() else None})
        prior = self.db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
        if prior and prior[0] != identity:
            self.db.close()
            self.owner.close()
            raise RequestConflict('Native configuration/model/budgets changed; use a new state directory')
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('identity',?)", (identity,))
        self.lock = threading.RLock()
        self.resources_factory = resources_factory
        self.resources = None
        self.sessions = OrderedDict()
        self.active_key = None
        self.parking = None
        self.target_owner = None  # The thread whose lean turn the engine last ran.
        self.seen = set()  # Threads served since this process started.
        self.warming = False
        self.closed = False

    def _migrate_history_ledger(self):
        """Move whole history-fingerprint snapshots into incremental ledger rows, once."""
        columns = [row[1] for row in self.db.execute('PRAGMA table_info(sessions)')]
        if 'history' not in columns:
            return
        with self.db:
            for key, history in self.db.execute('SELECT key,history FROM sessions'):
                try:
                    hashes = json.loads(history)
                except ValueError:
                    hashes = None
                if not isinstance(hashes, list) or not hashes:
                    continue
                existing = self.db.execute(
                    'SELECT COUNT(*) FROM history_entries WHERE key=?', (key,)).fetchone()[0]
                if existing:
                    continue
                self.db.executemany('INSERT OR IGNORE INTO history_entries(key,seq,hash) VALUES (?,?,?)',
                    [(key, seq, value) for seq, value in enumerate(hashes)])
            for key, pending in self.db.execute('SELECT key,pending FROM sessions WHERE pending IS NOT NULL'):
                try:
                    record = json.loads(pending)
                except ValueError:
                    continue
                if not isinstance(record, dict) or 'appended' in record or 'history' not in record:
                    continue
                ledger = [row[0] for row in self.db.execute(
                    'SELECT hash FROM history_entries WHERE key=? ORDER BY seq', (key,))]
                snapshot = record['history']
                if isinstance(snapshot, list) and snapshot[:len(ledger)] == ledger:
                    record = {'rid': record['rid'], 'hash': record['hash'], 'turn': record['turn'],
                              'base': len(ledger), 'appended': snapshot[len(ledger):]}
                    self.db.execute('UPDATE sessions SET pending=? WHERE key=?', (json.dumps(record), key))
            self.db.execute('ALTER TABLE sessions DROP COLUMN history')

    def _migrate_thread_columns(self):
        """Lean threads: a matching anchor per session and which ledger rows are our replies."""
        if 'anchor' not in [row[1] for row in self.db.execute('PRAGMA table_info(sessions)')]:
            with self.db:
                self.db.execute('ALTER TABLE sessions ADD COLUMN anchor TEXT')
        if 'used_at' not in [row[1] for row in self.db.execute('PRAGMA table_info(sessions)')]:
            with self.db:
                self.db.execute('ALTER TABLE sessions ADD COLUMN used_at REAL')
        if 'ours' not in [row[1] for row in self.db.execute('PRAGMA table_info(history_entries)')]:
            with self.db:
                self.db.execute('ALTER TABLE history_entries ADD COLUMN ours INTEGER NOT NULL DEFAULT 0')
        with self.db:
            self.db.execute('CREATE INDEX IF NOT EXISTS sessions_anchor ON sessions(anchor)')
            self.db.execute('CREATE INDEX IF NOT EXISTS completions_rid ON completions(rid)')

    def _ledger(self, key):
        return [row[0] for row in self.db.execute(
            'SELECT hash FROM history_entries WHERE key=? ORDER BY seq', (key,))]

    def _append_history(self, key, base, hashes):
        self.db.executemany('INSERT INTO history_entries(key,seq,hash) VALUES (?,?,?)',
            [(key, base + seq, value) for seq, value in enumerate(hashes)])

    @staticmethod
    def _normalize_pending(pending, ledger):
        if pending is None or ('base' in pending and 'appended' in pending):
            return pending
        snapshot = pending.get('history')
        if isinstance(snapshot, list) and snapshot[:len(ledger)] == ledger:
            return {'rid': pending['rid'], 'hash': pending['hash'], 'turn': pending['turn'],
                    'base': len(ledger), 'appended': snapshot[len(ledger):]}
        return pending

    def validate(self, payload):
        if self.options.lean:
            from .requests import parse
            return parse(payload, self.options.model)
        return self._validate_compacting(payload)

    def _validate_compacting(self, payload):
        allowed = {'messages', 'model', 'max_tokens', 'max_completion_tokens', 'stream', 'temperature', 'n'}
        if self.options.lean:
            allowed |= {'tools', 'tool_choice'}
        if set(payload) - allowed:
            raise ValueError('Native SpecPrefill currently supports plain text chat without tools or extra sampling fields'
                             if not self.options.lean else 'Unsupported request fields for SpecPrefill')
        if payload.get('model', self.options.model) != self.options.model:
            raise ValueError('Unknown model for the configured SpecPrefill gateway')
        if payload.get('temperature', 0) != 0 or payload.get('n', 1) != 1:
            raise ValueError('Native SpecPrefill currently supports temperature=0 and n=1')
        if 'stream' in payload and type(payload['stream']) is not bool:
            raise ValueError('stream must be boolean')
        if 'tools' in payload:
            from .tools import validate_tools
            validate_tools(payload['tools'], payload.get('tool_choice'))
        maximum = payload.get('max_completion_tokens', payload.get('max_tokens', self.options.answer_tokens))
        if type(maximum) is not int or not 1 <= maximum <= self.options.answer_tokens:
            raise ValueError('Requested completion limit exceeds configured SpecPrefill answer budget')
        messages = payload.get('messages')
        last = ('user', 'tool') if self.options.lean else ('user',)
        if (not isinstance(messages, list) or not messages or not isinstance(messages[-1], dict)
                or messages[-1].get('role') not in last):
            raise ValueError('A text conversation must end with a user message'
                             + (' or a tool result' if self.options.lean else ''))
        past_system = False
        for message in messages:
            if not (self._plain(message) or (self.options.lean and self._tooling(message))):
                raise ValueError('Only nonempty system/user/assistant text messages are supported'
                                 + ('; tool results and assistant tool calls are also accepted'
                                    if self.options.lean else ''))
            if message['role'] == 'system' and past_system:
                raise ValueError('System messages must precede conversation messages')
            past_system |= message['role'] != 'system'
        return maximum

    @staticmethod
    def _plain(message):
        return (isinstance(message, dict) and set(message) == {'role', 'content'}
                and message['role'] in ('system', 'user', 'assistant')
                and isinstance(message['content'], str)
                # An empty reply is a valid turn the gateway itself may have returned.
                and (message['role'] == 'assistant' or bool(message['content'].strip())))

    @staticmethod
    def _tooling(message):
        if not isinstance(message, dict):
            return False
        if message.get('role') == 'tool':
            return (set(message) <= {'role', 'content', 'tool_call_id', 'name'}
                    and isinstance(message.get('content'), str) and bool(message['content'].strip()))
        calls = message.get('tool_calls')
        return (message.get('role') == 'assistant' and set(message) <= {'role', 'content', 'tool_calls'}
                and isinstance(calls, list) and bool(calls)
                and all(isinstance(c, dict) and isinstance(c.get('function'), dict)
                        and isinstance(c['function'].get('name'), str) for c in calls)
                and (message.get('content') is None or isinstance(message['content'], str)))

    def _resources(self):
        if self.resources is None:
            if self.resources_factory:
                self.resources = self.resources_factory()
            else:
                from .native import NativeResources
                self.resources = NativeResources(self.options.config_path, self.options.student_path,
                    self.options.limits, self.directory / 'engine.log',
                    archive_cache_bytes=self.options.archive_cache_mib * 1024**2,
                    lean=self.options.lean, vram_reserve_mib=self.options.vram_reserve_mib,
                    sparse_keys=self.options.student_sparse_keys,
                    sparse_rows=self.options.student_sparse_rows, kv=self.options.target_kv,
                    target_park_mib=self.options.target_park_mib,
                    restore_floor_mib=self.options.target_restore_floor_mib)
        return self.resources

    @staticmethod
    def _text(tok, ids):
        text = re.sub(r'<\|[^>]+\|>', '', tok.decode(ids))
        # The prompt already closes thinking; drop a redundant empty block.
        return re.sub(r'^\s*<think>\s*</think>\s*', '', text)

    def _result(self, session, output, rid, key, tools=False, text=None, stopped=False):
        event = next((e for e in reversed(session.events) if e['kind'] == 'answer'), {})
        prompt = event.get('target_tokens', 0)
        maximum = (session.last_answer or {}).get('maximum', session.limits.answer_tokens)
        ended = stopped or output[-1] in getattr(session.target, 'stop_ids', ())
        message = {'role': 'assistant', 'content': self._text(session.tok, output) if text is None else text}
        finish = 'length' if len(output) >= maximum and not ended else 'stop'
        if tools:
            from .tools import parse
            content, calls = parse(message['content'], digest([key, rid]))
            if calls:
                message, finish = {'role': 'assistant', 'content': content, 'tool_calls': calls}, 'tool_calls'
        return {'id': 'chatcmpl-' + digest([key, rid])[:24], 'object': 'chat.completion',
                'created': int(time.time()), 'model': self.options.model,
                'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                'usage': {'prompt_tokens': prompt, 'completion_tokens': len(output), 'total_tokens': prompt+len(output)},
                'orchid': {'generation': session.generation, 'retrieval': event.get('retrieval'),
                           **({'engine': 'lean', 'new_tokens': event.get('new_tokens'),
                               'evidence_tokens': event.get('evidence_tokens')}
                              if self.options.lean else {})}}

    @staticmethod
    def _label(messages, message, ordinal):
        """Readable source label: a tool result is named by the call that produced it."""
        if message['role'] == 'tool':
            for m in messages:
                for call in m.get('tool_calls') or ():
                    if call.get('id') == message.get('tool_call_id'):
                        args = call['function'].get('arguments', '')
                        args = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
                        return f'tool {call["function"]["name"]} {args}'[:160]
            return f'tool {message.get("name") or "result"}'
        return f'{message["role"]} message {ordinal}'

    @staticmethod
    def _lean_ingest(session, label, text):
        """Retry-safe: unchanged content is skipped, changed content is a new version."""
        versions = [s for s in session.sources if s.path == label]
        if versions:
            latest = max(versions, key=lambda s: s.version)
            if session.tok.decode(latest.ids) == text:
                return
        session.add_source(label, len(versions) + 1, text)

    def _continue_query(self, tok, messages):
        """Verbatim query after a tool result: carry the latest user instruction."""
        task = next((m['content'] for m in reversed(messages) if m['role'] == 'user'), '')
        if task:
            task = self._split_query(tok, task)[1] if self.options.lean else task
        return f'{TOOL_CONTINUE} Task: {task}' if task else TOOL_CONTINUE

    def _split_query(self, tok, text):
        """Lean engine: (body to ingest or None, verbatim query tail)."""
        ids = tok.encode(text)
        if not self.options.lean or len(ids) <= self.options.query_split_tokens:
            return None, text
        paragraphs = text.rstrip().rsplit('\n\n', 1)
        tail = paragraphs[-1]
        if len(tok.encode(tail)) > self.options.query_tail_tokens or len(paragraphs) == 1:
            raw = b''.join(tok.token_bytes(t) for t in ids[-self.options.query_tail_tokens:])
            tail = raw.decode('utf-8', errors='ignore')
            cut = text.rfind(tail)
            body = text[:cut] if cut > 0 else text
        else:
            body = paragraphs[0]
        return (body if body.strip() else None), tail

    @staticmethod
    def _parts(tok, text, block=1024):
        ids = tok.encode(text)
        start = 0
        while start < len(ids):
            end = min(len(ids), start + block)
            raw = b''.join(tok.token_bytes(t) for t in ids[start:end])
            while True:
                try:
                    piece = raw.decode('utf-8')
                    break
                except UnicodeDecodeError:
                    if end == len(ids):
                        raise
                    raw += tok.token_bytes(ids[end])
                    end += 1
            yield piece, end - start
            start = end

    def complete(self, project, thread, rid, payload, on_text=None, on_commit=None):
        """One chat completion. project/thread name the thread explicitly; for the lean
        engine both may be None, and the thread is found from the history itself."""
        if self.options.lean:
            return self._complete_lean(project, thread, rid, payload, on_text, on_commit)
        return self._complete_compacting(project, thread, rid, payload, on_text, on_commit)

    def _complete_compacting(self, project, thread, rid, payload, on_text=None, on_commit=None):
        maximum = self._validate_compacting(payload)
        if not project or not thread or not rid or max(len(project), len(thread), len(rid)) > 512:
            raise ValueError('Explicit bounded project, thread, and request IDs are required')
        key = digest([project, thread])
        fingerprint = digest({k: v for k, v in payload.items() if k != 'stream'})
        with self.lock:
            if self.closed:
                raise RuntimeError('SpecPrefill service is closed')
            done = self.db.execute('SELECT hash,response FROM completions WHERE key=? AND rid=?', (key, rid)).fetchone()
            if done:
                if done[0] != fingerprint:
                    raise RequestConflict('Request ID was already used for a different payload')
                result = json.loads(done[1])
                if on_text and result['choices'][0]['message'].get('content'):
                    on_text(result['choices'][0]['message']['content'])
                if on_commit:
                    on_commit(result)
                return result
            row = self.db.execute('SELECT system,pending,growth FROM sessions WHERE key=?', (key,)).fetchone()
            messages = payload['messages']
            hashes = [digest(m) for m in messages]
            if row:
                system, pending, growth = row[0], json.loads(row[1]) if row[1] else None, row[2]
                history = self._ledger(key)
                pending = self._normalize_pending(pending, history)
                if pending and (pending['rid'] != rid or pending['hash'] != fingerprint
                        or 'appended' not in pending or 'base' not in pending):
                    raise RequestConflict('A prior request is incomplete; retry it with its original request ID')
                if pending and pending['base'] != len(history):
                    raise RequestConflict('Session checkpoint and request ledger disagree')
                if len(messages) == 1 and messages[0]['role'] == 'user':
                    novel = messages
                elif hashes[:len(history)] == history and len(hashes) > len(history):
                    novel = [m for m in messages[len(history):] if m['role'] != 'system']
                else:
                    raise RequestConflict('Conversation history changed; use a new thread for a branch')
            else:
                history, pending, growth = [], None, 0
                system = '\n\n'.join(m['content'] for m in messages if m['role'] == 'system') or 'Use exact source evidence to answer the user.'
                if payload.get('tools'):
                    from .tools import system_text
                    system = system_text(system, payload['tools'])
                novel = [m for m in messages if m['role'] != 'system']
            resource = self._resources()
            if self.active_key != key:
                resource.selector.reset()
                self.active_key = key
            if key not in self.sessions:
                self.sessions[key] = resource.session(self.directory / (key + '.json'), system, self.options.limits)
            session = self.sessions[key]
            if session.system != system:
                raise RequestConflict('Pinned system instructions changed')
            if session.limits != self.options.limits:
                raise RequestConflict('Session limits changed; restore with the original configuration')
            self.sessions.move_to_end(key)
            while len(self.sessions) > self.options.resident_sessions:
                _, evicted = self.sessions.popitem(last=False)
                evicted.close()
            lean = self.options.lean
            tools = bool(payload.get('tools'))
            if novel[-1]['role'] == 'tool':
                from .tools import message_text
                body, question = message_text(novel[-1]), self._continue_query(session.tok, messages)
            else:
                body, question = self._split_query(session.tok, novel[-1]['content'])
            bare = len(session._system()) + len(session._query(question)) + session.limits.answer_tokens + self.policy.safety_tokens
            if bare > min(session.limits.student_context, session.limits.target_context):
                from .scheduling import CapacityError
                raise CapacityError('User request alone exceeds context capacity; split the request')
            if pending and session.turn == pending['turn'] + 1:
                if not session.last_answer or session.last_answer['question'] != question:
                    raise RequestConflict('Cannot reconcile completed model turn')
                output = session.last_answer['output_ids']
                if on_text and not tools:
                    on_text(self._text(session.tok, output))
            else:
                if pending and session.turn != pending['turn']:
                    raise RequestConflict('Session checkpoint and request ledger disagree')
                if pending is None:
                    appended = hashes if not row else [digest(m) for m in novel]
                    pending = {'rid': rid, 'hash': fingerprint, 'turn': session.turn,
                               'base': len(history), 'appended': appended}
                    with self.db:
                        if row:
                            self.db.execute('UPDATE sessions SET pending=? WHERE key=?', (json.dumps(pending), key))
                        else:
                            self.db.execute('INSERT INTO sessions(key,system,pending,growth) VALUES (?,?,?,?)',
                                            (key, system, json.dumps(pending), growth))
                from .tools import message_text
                imports = [(i, m['role'], message_text(m)) for i, m in enumerate(novel[:-1])]
                if body is not None:
                    imports.append((len(novel) - 1, novel[-1]['role'], body))
                if lean:
                    for i, role, content in imports:
                        self._lean_ingest(session, self._label(messages, novel[i], pending['base'] + i), content)
                    imports = []
                for i, role, content in imports:
                    for part, (text, count) in enumerate(self._parts(session.tok, content)):
                        path = f'conversation/import/{pending["base"]+i}/{role}/{part}'
                        known = (session.source_index(path, 1) if lean
                                 else session.archive.source_id(path, 1))
                        if known is not None:
                            continue
                        if not lean:
                            self.policy.prepare(session, question, incoming_tokens=count+256,
                                                recent_growth=growth)
                        session.add_source(path, 1, text, cue=f'{role}: {content[:256]}')
                before = len(session.target_ids)
                if not lean:
                    self.policy.prepare(session, question, recent_growth=growth)
                decoder = codecs.getincrementaldecoder('utf-8')()
                def token(token_id):
                    if token_id in getattr(resource.target, 'stop_ids', ()):
                        return
                    raw = session.tok.token_bytes(token_id)
                    if raw.startswith(b'<|') and raw.endswith(b'|>'):
                        return
                    text = decoder.decode(raw)
                    if text and on_text:
                        on_text(text)
                # With tools, raw output may hold <tool_call> markup: deliver it parsed at the end.
                resource.target.on_token = token if on_text and not tools else None
                try:
                    output = session.answer(question, maximum=maximum)
                    tail = decoder.decode(b'', final=True)
                    if tail and on_text and not tools:
                        on_text(tail)
                finally:
                    resource.target.on_token = None
                growth = max(0, len(session.target_ids) - before)
            result = self._result(session, output, rid, key, tools=tools)
            if tools and on_text and result['choices'][0]['message'].get('content'):
                on_text(result['choices'][0]['message']['content'])
            appended = pending['appended'] + [digest(result['choices'][0]['message'])]
            with self.db:
                current = self.db.execute(
                    'SELECT COUNT(*) FROM history_entries WHERE key=?', (key,)).fetchone()[0]
                if current != pending['base']:
                    raise RequestConflict('Session checkpoint and request ledger disagree')
                self.db.execute('INSERT INTO completions VALUES (?,?,?,?)', (key, rid, fingerprint, json.dumps(result)))
                self._append_history(key, pending['base'], appended)
                self.db.execute('UPDATE sessions SET pending=NULL,growth=? WHERE key=?', (growth, key))
            if on_commit:
                on_commit(result)
            return result

    # ----- lean engine: the default request path ---------------------------
    def _lean_system(self, request):
        system = '\n\n'.join(m['content'] for m in request.messages if m['role'] == 'system')
        system = system or 'Use exact source evidence to answer the user.'
        if request.tools:
            from .tools import system_text
            system = system_text(system, request.tools)
        return system

    @staticmethod
    def _matches(hashes, roles, ledger):
        """The request continues this ledger: every recorded entry is its prefix.

        An assistant entry the gateway itself produced matches any assistant
        message, since clients re-serialize replies (reasoning, IDs, spacing).
        """
        if len(hashes) <= len(ledger):
            return False
        return all(h == hashes[i] or (ours and roles[i] == 'assistant')
                   for i, (h, ours) in enumerate(ledger))

    def _ledger_rows(self, key):
        return self.db.execute('SELECT hash,ours FROM history_entries WHERE key=? ORDER BY seq',
                               (key,)).fetchall()

    def _find_thread(self, anchor, hashes, roles, rid):
        """The longest stored thread this request continues, or None (a new thread)."""
        best, best_len = None, -1
        for key, pending in self.db.execute('SELECT key,pending FROM sessions WHERE anchor=?', (anchor,)):
            if pending and json.loads(pending).get('rid') != rid:
                continue  # Stuck on another request: branch off instead of waiting on it.
            ledger = self._ledger_rows(key)
            if len(ledger) > best_len and self._matches(hashes, roles, ledger):
                best, best_len = key, len(ledger)
        return best

    def _conversation_tokens(self, tok, request):
        return sum(len(tok.encode(m['content'])) for m in request.messages)

    def _native_turn(self, key, rid, fingerprint, payload, request, reason, on_text, on_commit):
        """Answer with plain Flash-Next. The lean session and ledger are untouched:
        the next lean turn on this thread ingests whatever it hasn't seen."""
        resource = self._resources()
        self.target_owner = None  # The engine now holds this native conversation.
        result = resource.native(payload, on_text)
        result = {**result, 'orchid': {'engine': 'native', 'reason': reason, 'thread': key}}
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO completions VALUES (?,?,?,?)',
                            (key, rid, fingerprint, json.dumps(result)))
        if on_commit:
            on_commit(result)
        return result

    def _complete_lean(self, project, thread, rid, payload, on_text=None, on_commit=None):
        from .requests import identity, parse
        request = parse(payload, self.options.model)
        if bool(project) != bool(thread):
            raise ValueError('Send both x-memory-project and x-memory-thread, or neither')
        if max(len(project or ''), len(thread or ''), len(rid or '')) > 512:
            raise ValueError('Project, thread, and request IDs must be at most 512 characters')
        explicit = bool(project)
        hashes = [digest(identity(m)) for m in request.messages]
        roles = [m['role'] for m in request.messages]
        # Everything that can change the answer: the normalized history plus every other
        # request field (streaming only changes how the same answer is delivered).
        fingerprint = digest({'messages': hashes, 'fields': {k: v for k, v in payload.items()
                                                             if k not in ('messages', 'stream', 'stream_options')}})
        # Without a request ID, a repeated request (a client retry) replays its answer.
        rid = rid or 'auto-' + fingerprint
        if self.warming:
            raise Warming('The models are loading; retry in a few seconds')
        with self.lock:
            if self.closed:
                raise RuntimeError('SpecPrefill service is closed')
            done = (self.db.execute('SELECT key,hash,response FROM completions WHERE key=? AND rid=?',
                                    (digest([project, thread]), rid)).fetchone() if explicit
                    else self.db.execute('SELECT key,hash,response FROM completions WHERE rid=?',
                                         (rid,)).fetchone())
            if done:
                if done[1] != fingerprint:
                    raise RequestConflict('Request ID was already used for a different payload')
                result = json.loads(done[2])
                if on_text and result['choices'][0]['message'].get('content'):
                    on_text(result['choices'][0]['message']['content'])
                if on_commit:
                    on_commit(result)
                return result
            system = self._lean_system(request)
            first = next((h for h, r in zip(hashes, roles) if r != 'system'), '')
            anchor = digest([system, first])
            if explicit:
                key = digest([project, thread])
                known = self.db.execute('SELECT 1 FROM sessions WHERE key=?', (key,)).fetchone() is not None
            else:
                key = self._find_thread(anchor, hashes, roles, rid)
                known = key is not None
            reason = request.native_reasons[0] if request.native_reasons else None
            if (reason is None and not known and self.options.native_below_tokens
                    and self._conversation_tokens(self._resources().tok, request) < self.options.native_below_tokens):
                reason = 'short conversation'
            if key is None:
                key = digest(['auto', anchor, rid])
            storage = self._storage_check() if self.options.lean else {}
            reason = reason or storage.get('reason')
            if reason:
                return self._native_turn(key, rid, fingerprint, payload, request, reason, on_text, on_commit)
            streamed = []
            relay = (lambda text: (streamed.append(True), on_text(text))) if on_text else None
            try:
                result = self._lean_turn(key, explicit, rid, fingerprint, request, system, anchor, hashes,
                                         relay, on_commit)
                if storage.get('warning'):
                    result['orchid']['storage_warning'] = True  # Archives past 80% of the quota.
                return result
            except RequestConflict:
                raise
            except Exception as error:
                from .scheduling import CapacityError
                if isinstance(error, CapacityError):
                    raise
                if streamed:
                    raise
                # Nothing reached the client: answer natively. The lean turn rolled back;
                # its ingested sources are kept and skipped when the turn is retried.
                with self.db:
                    self.db.execute('UPDATE sessions SET pending=NULL WHERE key=?', (key,))
                # The in-memory session may be ahead of its checkpoint (a failed write, a
                # full disk): reload it from disk on the next turn.
                dropped = self.sessions.pop(key, None)
                if dropped is not None:
                    dropped.close()
                return self._native_turn(key, rid, fingerprint, payload, request, 'lean failure',
                                         on_text, on_commit)

    def _lean_turn(self, key, explicit, rid, fingerprint, request, system, anchor, hashes, on_text, on_commit):
        from .tools import message_text
        messages = request.messages
        row = self.db.execute('SELECT system,pending,growth FROM sessions WHERE key=?', (key,)).fetchone()
        single = bool(row) and explicit and len(messages) == 1 and messages[0]['role'] == 'user'
        if row:
            pending = json.loads(row[1]) if row[1] else None
            ledger = self._ledger_rows(key)
            if pending and (pending['rid'] != rid or pending['hash'] != fingerprint):
                raise RequestConflict('A prior request is incomplete; retry it with its original request ID')
            if pending and pending['base'] != len(ledger):
                raise RequestConflict('Session checkpoint and request ledger disagree')
            if single:
                novel = messages  # A client that sends only its newest message.
            elif self._matches(hashes, [m['role'] for m in messages], ledger):
                novel = [m for m in messages[len(ledger):] if m['role'] != 'system']
            else:
                raise RequestConflict('Conversation history changed; use a new thread for a branch')
        else:
            pending, ledger = None, []
            novel = [m for m in messages if m['role'] != 'system']
        resource = self._resources()
        resume = {}
        if key not in self.sessions:
            # Before the student switch: restoring a session resets the student, which
            # would discard the parked state the switch is about to resume.
            self.sessions[key] = resource.session(self._session_path(key), system, self.options.limits)
        if self.active_key != key:
            started = time.perf_counter()
            self._switch_student(resource, key)
            resume['student_s'] = round(time.perf_counter() - started, 3)
        self.seen.add(key)
        if self.target_owner != key and hasattr(resource, 'restore_target'):
            # Flash-Next holds another thread (or none): save that one's K/V to its
            # session file, then load this thread's, so neither prompt is read again.
            if self.target_owner:
                started = time.perf_counter()
                self._save_target(resource, self.target_owner, hot=True, protect=(key,))
                resume['target_save_s'] = round(time.perf_counter() - started, 3)
            started = time.perf_counter()
            hot = self._target_hot(key)
            if (hot.exists() and resource.restore_target(hot)) or resource.restore_target(self._target_file(key)):
                resume['target_restore_s'] = round(time.perf_counter() - started, 3)
                self.target_owner = key
        session = self.sessions[key]
        if session.system != system:
            raise RequestConflict('Pinned system instructions changed')
        if session.limits != self.options.limits:
            # New budgets or windows apply from this turn; the chunking of stored sources stays.
            from dataclasses import replace
            session.limits = replace(self.options.limits, chunk_tokens=session.limits.chunk_tokens)
        self.sessions.move_to_end(key)
        while len(self.sessions) > self.options.resident_sessions:
            _, evicted = self.sessions.popitem(last=False)
            evicted.close()
        tools = bool(request.tools)
        if novel[-1]['role'] == 'tool':
            body, question = message_text(novel[-1]), self._continue_query(session.tok, messages)
        else:
            body, question = self._split_query(session.tok, novel[-1]['content'])
        frame = len(session._system()) + len(session._query(question))
        bare = frame + session.limits.answer_tokens + self.policy.safety_tokens
        if bare > min(session.limits.student_context, session.limits.target_context):
            from .scheduling import CapacityError
            raise CapacityError('User request alone exceeds context capacity; split the request')
        # Generation length: what the client asked for (or the default), within the target window.
        maximum = max(1, min(request.max_tokens or self.options.output_tokens,
                             session.limits.target_context - frame - 1024))
        stop = _StopText(request.stop)
        # With tools, text streams until the first <tool_call>; the calls are parsed at the end.
        calls = _StopText(['<tool_call>']) if tools else None

        def emit(text, final=False):
            if calls is not None:
                text = calls.feed(text, final)
            if text and on_text:
                on_text(text)
        if pending and session.turn == pending['turn'] + 1:
            if not session.last_answer or session.last_answer['question'] != question:
                raise RequestConflict('Cannot reconcile completed model turn')
            output = session.last_answer['output_ids']
            emit(stop.feed(self._text(session.tok, output), final=True), final=True)
        else:
            if pending and session.turn != pending['turn']:
                raise RequestConflict('Session checkpoint and request ledger disagree')
            if pending is None:
                # Ledger entries this turn adds (our reply is added on commit).
                appended = hashes if not row or single else hashes[len(ledger):]
                pending = {'rid': rid, 'hash': fingerprint, 'turn': session.turn,
                           'base': len(ledger), 'appended': appended}
                with self.db:
                    if row:
                        self.db.execute('UPDATE sessions SET pending=? WHERE key=?', (json.dumps(pending), key))
                    else:
                        self.db.execute('INSERT INTO sessions(key,system,pending,growth,anchor) VALUES (?,?,?,?,?)',
                                        (key, system, json.dumps(pending), 0, anchor))
            imports = [(i, message_text(m)) for i, m in enumerate(novel[:-1])]
            if body is not None:
                imports.append((len(novel) - 1, body))
            for i, content in imports:
                self._lean_ingest(session, self._label(messages, novel[i], pending['base'] + i), content)
            decoder = codecs.getincrementaldecoder('utf-8')()
            target = resource.target
            target.cancel = threading.Event()
            target.sampling = request.sampling or None

            def token(token_id):
                if token_id in getattr(target, 'stop_ids', ()):
                    return
                raw = session.tok.token_bytes(token_id)
                if raw.startswith(b'<|') and raw.endswith(b'|>'):
                    return
                emit(stop.feed(decoder.decode(raw)))
                if stop.hit:
                    target.cancel.set()
            target.on_token = token if on_text or request.stop else None
            try:
                self.target_owner = None
                output = session.answer(question, maximum=maximum)
                self.target_owner = key
                if target.on_token is None:
                    stop.feed(self._text(session.tok, output), final=True)
                else:
                    emit(stop.feed(decoder.decode(b'', final=True), final=True), final=True)
            finally:
                target.on_token = None
                target.sampling = None
        result = self._result(session, output, rid, key, tools=tools,
                              text=stop.text if request.stop else None, stopped=stop.hit)
        result['orchid']['thread'] = key
        if resume:
            result['orchid']['resume'] = resume
        if tools and on_text:
            # Text after a tool call (rare) was held back with the call: send what's left.
            content, sent = result['choices'][0]['message'].get('content') or '', calls.text
            if content.startswith(sent) and len(content) > len(sent):
                on_text(content[len(sent):])
        from .requests import identity
        appended = [(h, 0) for h in pending['appended']] + [
            (digest(identity(result['choices'][0]['message'])), 1)]
        with self.db:
            current = self.db.execute('SELECT COUNT(*) FROM history_entries WHERE key=?', (key,)).fetchone()[0]
            if current != pending['base']:
                raise RequestConflict('Session checkpoint and request ledger disagree')
            self.db.execute('INSERT INTO completions VALUES (?,?,?,?)', (key, rid, fingerprint, json.dumps(result)))
            self.db.executemany('INSERT INTO history_entries(key,seq,hash,ours) VALUES (?,?,?,?)',
                [(key, pending['base'] + i, h, ours) for i, (h, ours) in enumerate(appended)])
            self.db.execute('UPDATE sessions SET pending=NULL,used_at=? WHERE key=?', (time.time(), key))
        if on_commit:
            on_commit(result)
        return result

    def ready(self):
        return self.resources is not None and not self.warming and not self.closed

    def start_warm_up(self):
        """Load the models in the background, then resume the most recently used thread."""
        self.warming = True
        threading.Thread(target=self.warm_up, name='orchid-specprefill-warm-up', daemon=True).start()

    def warm_up(self):
        try:
            with self.lock:
                self.warming = True
                if self.closed:
                    return
                resource = self._resources()
                if not self.options.lean:
                    return
                row = self.db.execute('SELECT key,system FROM sessions WHERE used_at IS NOT NULL '
                                      'ORDER BY used_at DESC LIMIT 1').fetchone()
                path = row and self._session_path(row[0])
                if not row or not (path.exists() or path.with_suffix('.json').exists()):
                    return
                key, system = row
                self.sessions[key] = resource.session(path, system, self.options.limits)
                self._switch_student(resource, key)
                self.seen.add(key)
                if hasattr(resource, 'restore_target') and resource.restore_target(self._target_file(key)):
                    self.target_owner = key
        finally:
            self.warming = False

    def _storage_check(self):
        """{'reason': ...} when storage forbids a lean write; {'warning': True} past 80% of the quota."""
        import shutil
        if shutil.disk_usage(self.directory).free < self.options.min_free_disk_mib * 2**20:
            return {'reason': 'disk full'}
        if self.options.state_quota_mib:
            from .controls import storage
            used = storage(self)
            if used['over_quota']:
                return {'reason': 'storage quota'}
            if used['warning']:
                return {'warning': True}
        return {}

    def _session_path(self, key):
        """A lean thread's checkpoint (incremental SQLite; a legacy .json migrates on open)."""
        return self.directory / f'{key}.sqlite3'

    def _target_file(self, key):
        return self.directory / 'target-cache' / f'{key}.bin'

    def _target_hot(self, key):
        directory = (Path(self.options.target_hot_dir)
                     / f'orchid-{os.getuid()}-{digest(str(self.directory.resolve()))[:12]}')
        return directory / f'{key}.bin'

    @staticmethod
    def _trim(directory, budget, keep=1):
        files = sorted(directory.glob('*.bin'), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        while len(files) > keep and total > budget:
            oldest = files.pop(0)
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)

    def _save_target(self, resource, key, hot=False, protect=()):
        """Write a thread's Flash-Next K/V: to /dev/shm at a switch (one background copier
        puts it on disk too), straight to disk at shutdown. Hot files are evicted only
        here, only once copied to disk, and never the ones in use (`protect`)."""
        disk = self._target_file(key)
        if not hot or self.options.target_hot_mib <= 0:
            saved = resource.save_target(disk)
            self._trim(disk.parent, self.options.target_disk_mib * 2**20)
            return saved
        path = self._target_hot(key)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        saved = resource.save_target(path)
        if saved and path.exists():
            self._copy_queue().put((key, path.stat().st_mtime))
        files = sorted(path.parent.glob('*.bin'), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        for f in files:
            if total <= self.options.target_hot_mib * 2**20:
                break
            if f.stem in (key, *protect) or getattr(self, '_copied', {}).get(f.stem) != f.stat().st_mtime:
                continue  # In use, or not on disk yet.
            total -= f.stat().st_size
            f.unlink(missing_ok=True)
        return saved

    def _copy_queue(self):
        if getattr(self, '_copies', None) is None:
            import queue
            self._copies, self._copied = queue.Queue(), {}
            threading.Thread(target=self._copy_loop, name='orchid-target-copy', daemon=True).start()
        return self._copies

    def _copy_loop(self):
        import shutil
        while True:
            key, mtime = self._copies.get()
            hot, disk = self._target_hot(key), self._target_file(key)
            try:
                if hot.exists() and hot.stat().st_mtime == mtime:
                    disk.parent.mkdir(parents=True, exist_ok=True)
                    temporary = disk.with_suffix('.tmp')
                    shutil.copyfile(hot, temporary)
                    os.replace(temporary, disk)
                    self._copied[key] = mtime
                    self._trim(disk.parent, self.options.target_disk_mib * 2**20)
            except OSError as error:
                print(f'orchid: session file copy for {key[:12]} failed ({error})', file=sys.stderr)
            finally:
                self._copies.task_done()

    def _switch_student(self, resource, key):
        """Park the outgoing thread's student state and resume the incoming one's."""
        student = resource.selector
        if not hasattr(student, 'export_state'):  # The compacting engine's selector: no parking.
            student.reset()
            self.active_key = key
            return
        if self.parking is None:
            from .parking import StudentParking
            self.parking = StudentParking(self.directory / 'student-cache', self.options.student_park_ram_mib * 2**20,
                                          self.options.student_park_disk_mib * 2**20, student.student.torch)
        # Fetch the incoming state before parking the outgoing one: with room for one state
        # in RAM, parking first would evict exactly the state about to be needed.
        incoming = self.parking.get(key)
        if self.active_key is not None:
            state = student.export_state()
            if state is not None:
                self.parking.put(self.active_key, state)
        student.reset()
        self.active_key = key
        student.import_state(incoming)

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            if self.parking is not None and self.active_key is not None and self.resources:
                state = self.resources.selector.export_state()
                if state is not None:
                    self.parking.put(self.active_key, state)  # Resume after a restart.
                self.parking.flush()
            if self.target_owner and self.resources and hasattr(self.resources, 'save_target'):
                self._save_target(self.resources, self.target_owner)
            if getattr(self, '_copies', None) is not None:
                self._copies.join()  # Hot session files reach disk before a reboot can clear them.
            for session in self.sessions.values():
                session.close()
            self.sessions.clear()
            if self.resources:
                self.resources.close()
                self.resources = None
            self.db.close()
            fcntl.flock(self.owner, fcntl.LOCK_UN)
            self.owner.close()
