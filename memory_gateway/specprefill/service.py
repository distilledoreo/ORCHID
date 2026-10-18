"""Serialized, restartable text-chat sessions for the opt-in native gateway."""
from __future__ import annotations

import codecs
import fcntl
import hashlib
import json
import os
import re
import sqlite3
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
    # Target K/V cache type ('int8' or 'fp16'); None keeps the config's. Native
    # 256K needs fp16: int8 K/V returns empty replies past ~50K.
    target_kv: str | None = None

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
            target_kv=os.environ.get('ORCHID_SPECPREFILL_TARGET_KV') or None)
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
        configuration = Path(options.config_path)
        student_configuration = Path(options.student_path) / 'config.json'
        identity = digest({'runtime': 'specprefill-gateway-v1', 'limits': asdict(options.limits),
            **({'engine': 'lean', 'query_split': [options.query_split_tokens, options.query_tail_tokens],
                **({'student_sparse_keys': options.student_sparse_keys}
                   if options.student_sparse_keys else {})}
               if options.lean else {}),
            **({'target_kv': options.target_kv} if options.target_kv else {}),
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
                self.db.executemany('INSERT OR IGNORE INTO history_entries VALUES (?,?,?)',
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

    def _ledger(self, key):
        return [row[0] for row in self.db.execute(
            'SELECT hash FROM history_entries WHERE key=? ORDER BY seq', (key,))]

    def _append_history(self, key, base, hashes):
        self.db.executemany('INSERT INTO history_entries VALUES (?,?,?)',
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
                    sparse_keys=self.options.student_sparse_keys, kv=self.options.target_kv)
        return self.resources

    @staticmethod
    def _text(tok, ids):
        text = re.sub(r'<\|[^>]+\|>', '', tok.decode(ids))
        # The prompt already closes thinking; drop a redundant empty block.
        return re.sub(r'^\s*<think>\s*</think>\s*', '', text)

    def _result(self, session, output, rid, key, tools=False):
        event = next((e for e in reversed(session.events) if e['kind'] == 'answer'), {})
        prompt = event.get('target_tokens', 0)
        maximum = (session.last_answer or {}).get('maximum', session.limits.answer_tokens)
        ended = output[-1] in getattr(session.target, 'stop_ids', ())
        message = {'role': 'assistant', 'content': self._text(session.tok, output)}
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
        maximum = self.validate(payload)
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

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            for session in self.sessions.values():
                session.close()
            self.sessions.clear()
            if self.resources:
                self.resources.close()
                self.resources = None
            self.db.close()
            fcntl.flock(self.owner, fcntl.LOCK_UN)
            self.owner.close()
