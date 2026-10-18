"""Lossless, versioned source archive and append-only delivery ledger (experimental)."""
from __future__ import annotations

import bisect
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Chunk:
    handle: str
    source: str
    index: int
    byte_start: int
    byte_end: int
    char_start: int
    char_end: int
    token_start: int
    token_end: int
    original_start: int
    original_end: int
    text: str


class OmissionArchive:
    def __init__(self, tokenizer):
        self.tok = tokenizer
        self.sources = {}
        self.chunks = {}
        self.current = {}
        self.delivered = {}  # handle -> target token intervals, never rewritten
        self.cached = set()
        self.placeholder_emitted = set()
        self.events = []
        self.messages = []
        self.cache_epoch = 0

    def add(self, path, version, text, original_start, turn):
        if (type(version) is not int or version < 1 or type(original_start) is not int
                or original_start < 0 or not isinstance(text, str) or not isinstance(path, str)):
            raise ValueError('Invalid source provenance')
        existing = self.source_id(path, version)
        if existing is not None:
            source = self.sources[existing]
            if source['text'] != text:
                raise ValueError('An immutable source version cannot change content')
            occurrence = {'original_start': original_start,
                          'original_end': original_start+len(source['token_ids']), 'turn': turn}
            occurrences = source.setdefault('occurrences', [{'original_start':source['original_start'],
                            'original_end':source['original_end'], 'turn':source['turn']}])
            if occurrence not in occurrences:
                occurrences.append(occurrence)
            self.events.append({'event':'source_reencounter','source':existing, **occurrence})
            return existing
        sid = f'S{len(self.sources)+1}'
        ids = self.tok.encode(text)
        raw = text.encode('utf-8')
        pieces = [self.tok.token_bytes(t) for t in ids]
        if b''.join(pieces) != raw:
            raise ValueError('Tokenizer did not preserve exact source bytes')
        offsets = [0]
        for p in pieces:
            offsets.append(offsets[-1]+len(p))
        chunks = []
        start = 0
        while start < len(ids):
            end = min(start + (32-(original_start+start) % 32), len(ids))
            # Keep boundaries on UTF-8 code points; token positions remain exact.
            while True:
                try:
                    content = raw[offsets[start]:offsets[end]].decode('utf-8', errors='strict')
                    break
                except UnicodeDecodeError:
                    if end == len(ids):
                        raise
                    end += 1
            a, b = offsets[start], offsets[end]
            handle = f'{sid}:{len(chunks)}'
            chunk = Chunk(handle, sid, len(chunks), a, b,
                          len(raw[:a].decode('utf-8')), len(raw[:b].decode('utf-8')),
                          start, end, original_start+start, original_start+end, content)
            self.chunks[handle] = chunk
            chunks.append(handle)
            start = end
        prior = self.current.get(path)
        supersedes = prior if prior and version > self.sources[prior]['version'] else None
        self.sources[sid] = {'id': sid, 'path': path, 'version': version, 'text': text,
                             'sha256': hashlib.sha256(raw).hexdigest(), 'token_ids': ids,
                             'original_start': original_start, 'original_end': original_start+len(ids),
                             'turn': turn, 'chunks': chunks, 'supersedes': supersedes,
                             'occurrences':[{'original_start':original_start, 'original_end':original_start+len(ids), 'turn':turn}]}
        if prior is None or version > self.sources[prior]['version']:
            self.current[path] = sid
        self.events.append({'event': 'archive', 'source': sid, 'turn': turn})
        return sid

    def source_id(self, path, version):
        return next((sid for sid,s in self.sources.items() if (s['path'],s['version'])==(path,version)), None)

    def selected_chunks(self, positions):
        selected = set(positions)
        return [h for h, c in self.chunks.items()
                if any(p in selected for o in self.sources[c.source].get('occurrences', [{'original_start':self.sources[c.source]['original_start']}])
                       for p in range(o['original_start']+c.token_start, o['original_start']+c.token_end))]

    def missing(self, handles):
        return [h for h in dict.fromkeys(handles) if h not in self.delivered]

    def groups(self, handles):
        groups = []
        for h in sorted(set(handles), key=lambda h: (int(self.chunks[h].source[1:]), self.chunks[h].index)):
            c = self.chunks[h]
            if groups and self.chunks[groups[-1][-1]].source == c.source and self.chunks[groups[-1][-1]].index+1 == c.index:
                groups[-1].append(h)
            else:
                groups.append([h])
        return groups

    def placeholders(self, source_ids, mode):
        eligible = [h for sid in source_ids for h in self.sources[sid]['chunks']
                    if h not in self.delivered and h not in self.placeholder_emitted]
        if mode == 'none':
            return '', 0
        if mode not in ('individual', 'grouped'):
            raise ValueError('Unknown placeholder mode')
        text, count = self.format_placeholders(eligible, mode)
        self.placeholder_emitted.update(eligible)
        self.events.append({'event':'placeholders', 'handles':eligible, 'mode':mode, 'tokens':count})
        return text, count

    def format_placeholders(self, handles, style):
        if style not in ('individual','grouped','compact_individual','compact_grouped','source_only'):
            raise ValueError('Unknown placeholder format')
        if style == 'source_only':
            lines = [f'[ARCHIVED {sid} {self.sources[sid]["path"]} v{self.sources[sid]["version"]}]\n'
                     for sid in dict.fromkeys(self.chunks[h].source for h in handles)]
        else:
            groups = [[h] for h in handles] if style.endswith('individual') or style == 'individual' else self.groups(handles)
            lines = []
            for group in groups:
                c,last = self.chunks[group[0]],self.chunks[group[-1]]
                s = self.sources[c.source]
                span = str(c.index) if len(group)==1 else f'{c.index}-{last.index}'
                if style.startswith('compact_'):
                    lines.append(f'[A {c.source}:{span}]\n')
                else:
                    lines.append(f'[ARCHIVED {c.source}:{span} {s["path"]} v{s["version"]}]\n')
        text = ''.join(lines)
        return text,len(self.tok.encode(text))

    def evidence(self, handles, style="detailed", *, present=None):
        """Exact source bytes in provenance frames; skip material already in target history."""
        if style not in ("bare", "minimal", "detailed"):
            raise ValueError("Unknown provenance style")
        text = ''
        placements = []
        # Compaction sessions supply their current-generation presence ledger.
        # The historical ever-delivered ledger remains the default for old experiments.
        missing = self.missing(handles) if present is None else [h for h in handles if h not in present]
        for group in self.groups(missing):
            first, last = self.chunks[group[0]], self.chunks[group[-1]]
            s = self.sources[first.source]
            current = self.current[s['path']]
            status = 'current' if current == s['id'] else f'historical; superseded_by={current}'
            header = (f'[EVIDENCE {s["id"]} {s["path"]} version={s["version"]} '
                      f'chunks={first.index}-{last.index} bytes={first.byte_start}:{last.byte_end} '
                      f'source_turn={s["turn"]} {status}]\n')
            if style == 'minimal':
                header = f'[EVIDENCE {s["path"]} version={s["version"]} {status.split(";")[0]}]\n'
            elif style == 'bare':
                header = '[EVIDENCE]\n'
            text += header
            for h in group:
                a = len(text)
                text += self.chunks[h].text
                placements.append((h, a, len(text)))
            text += '\n[/EVIDENCE]\n'
        return text, placements

    def commit(self, placements, target_start, rendered_text):
        ids = self.tok.encode(rendered_text)
        offsets = [0]
        for token in ids:
            offsets.append(offsets[-1]+len(self.tok.token_bytes(token)))
        for h, a, b in placements:
            if h in self.delivered:
                raise RuntimeError('Duplicate semantic delivery')
            byte_a = len(rendered_text[:a].encode('utf-8'))
            byte_b = len(rendered_text[:b].encode('utf-8'))
            start = target_start + bisect.bisect_right(offsets, byte_a)-1
            end = target_start + bisect.bisect_left(offsets, byte_b)
            self.delivered[h] = {'start': start, 'end': end, 'epoch': self.cache_epoch}
        self.events.append({'event': 'deliver', 'handles': [h for h, _, _ in placements]})

    def resolve(self, handle):
        """Address S1 (whole region), S1:3, or S1:3-7; no filesystem access."""
        import re
        m = re.fullmatch(r'(S[1-9]\d*)(?::(\d+)(?:-(\d+))?)?', handle)
        if not m or m[1] not in self.sources:
            raise ValueError('Unknown archive handle')
        chunks = self.sources[m[1]]['chunks']
        if m[2] is None:
            return chunks
        start, end = int(m[2]), int(m[3] or m[2])
        if not 0 <= start <= end < len(chunks):
            raise ValueError('Archive chunk range is out of bounds')
        return chunks[start:end+1]

    def confirm_cache(self, prompt_tokens, reused, previous_prompt_tokens, reason=None):
        invalidated = previous_prompt_tokens > 0 and reused < previous_prompt_tokens
        if invalidated:
            self.cache_epoch += 1
            self.events.append({'event': 'cache_invalidation', 'expected_prefix': previous_prompt_tokens,
                                'reused': reused, 'reason': reason or 'engine_did_not_reuse_prefix'})
        # Full cumulative input reconstructs any lost cache; no duplicate evidence append.
        self.cached = {h for h, d in self.delivered.items() if d['end'] <= prompt_tokens}
        self.events.append({'event': 'cache_confirmed', 'reused': reused, 'prompt': prompt_tokens,
                            'cached_chunks': len(self.cached), 'epoch': self.cache_epoch})
        return invalidated

    def record_message(self, role, text, start, ids):
        if b''.join(self.tok.token_bytes(t) for t in ids) != text.encode('utf-8'):
            raise ValueError('Original message did not roundtrip')
        self.messages.append({'id': f'M{len(self.messages)+1}', 'role': role, 'text': text,
                              'original_start': start, 'original_end': start+len(ids),
                              'token_ids': list(ids), 'sha256': hashlib.sha256(text.encode()).hexdigest()})

    def to_dict(self):
        return {'schema': 1, 'sources': self.sources,
                 'chunks': {h: asdict(c) for h, c in self.chunks.items()},
                 'current': self.current, 'delivered': self.delivered,
                 'cached': sorted(self.cached), 'placeholder_emitted': sorted(self.placeholder_emitted),
                 'cache_epoch': self.cache_epoch, 'events': self.events, 'messages': self.messages}

    def save(self, path: Path):
        state = self.to_dict()
        tmp = path.with_suffix(path.suffix+'.tmp')
        with tmp.open('w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)

    @classmethod
    def load(cls, path, tokenizer):
        return cls.from_dict(json.loads(Path(path).read_text()), tokenizer)

    @classmethod
    def from_dict(cls, state, tokenizer):
        # Own the loaded ledger; callers may retain the checkpoint dictionary.
        import copy
        state = copy.deepcopy(state)
        if state['schema'] != 1:
            raise ValueError('Unsupported archive schema')
        obj = cls(tokenizer)
        obj.sources, obj.current, obj.delivered = state['sources'], state['current'], state['delivered']
        obj.chunks = {h: Chunk(**c) for h, c in state['chunks'].items()}
        obj.cached, obj.placeholder_emitted = set(), set(state['placeholder_emitted'])
        obj.cache_epoch = state['cache_epoch']+1  # Disk archive is not a live model-cache snapshot.
        obj.messages = state.get('messages', [])
        obj.events = state['events'] + [{'event': 'archive_loaded_cache_unconfirmed'}]
        expected_current = {}
        for sid,source in obj.sources.items():
            prior = expected_current.get(source['path'])
            if prior is None or source['version'] > obj.sources[prior]['version']:
                expected_current[source['path']] = sid
        if obj.current != expected_current:
            raise ValueError('Archive current-version pointer mismatch')
        for message in obj.messages:
            if (hashlib.sha256(message['text'].encode()).hexdigest() != message['sha256']
                    or b''.join(tokenizer.token_bytes(t) for t in message['token_ids']) != message['text'].encode()
                    or message['original_end']-message['original_start'] != len(message['token_ids'])):
                raise ValueError('Archive original message mismatch')
        if not set(obj.delivered) <= set(obj.chunks) or not obj.placeholder_emitted <= set(obj.chunks):
            raise ValueError('Archive ledger references unknown chunks')
        for span in obj.delivered.values():
            if not 0 <= span['start'] < span['end'] or not 0 <= span['epoch'] <= state['cache_epoch']:
                raise ValueError('Archive delivery range mismatch')
        for s in obj.sources.values():
            if hashlib.sha256(s['text'].encode()).hexdigest() != s['sha256']:
                raise ValueError('Archive source checksum mismatch')
            if tokenizer.encode(s['text']) != s['token_ids']:
                raise ValueError('Archive tokenizer identity changed')
            if ''.join(obj.chunks[h].text for h in s['chunks']) != s['text']:
                raise ValueError('Archive chunks do not reproduce source')
            offsets = [0]
            for token in s['token_ids']:
                offsets.append(offsets[-1]+len(tokenizer.token_bytes(token)))
            expected_start = 0
            for index,h in enumerate(s['chunks']):
                c = obj.chunks[h]
                if c.token_start != expected_start or c.index != index or c.handle != h:
                    raise ValueError('Archive chunk sequence mismatch')
                expected_start = c.token_end
                if (c.source != s['id'] or c.byte_start != offsets[c.token_start]
                        or c.byte_end != offsets[c.token_end]
                        or c.original_start != s['original_start']+c.token_start
                        or c.original_end != s['original_start']+c.token_end
                        or s['text'][c.char_start:c.char_end] != c.text
                        or s['text'].encode()[c.byte_start:c.byte_end] != c.text.encode()):
                    raise ValueError('Archive chunk provenance mismatch')
            for occurrence in s.get('occurrences', []):
                if (type(occurrence['original_start']) is not int or occurrence['original_start'] < 0
                        or occurrence['original_end']-occurrence['original_start'] != len(s['token_ids'])):
                    raise ValueError('Archive occurrence range mismatch')
            if expected_start != len(s['token_ids']):
                raise ValueError('Archive token coverage mismatch')
        return obj
