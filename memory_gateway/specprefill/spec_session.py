"""Lean append-only SpecPrefill session: the student reads, the target reads less.

The student holds the raw conversation (sources, questions, replies) in an
append-only prompt cache. Each answer scores every not-yet-delivered source
chunk with one cached student trace, then appends only the top-ranked chunks
to the target's append-only transcript, under a hard token budget derived from
the tokens that are new this turn plus a small recall budget for previously
omitted chunks. The target never writes summaries.

When the target window fills, it is rebuilt from the system prompt, a
student-selected subset of all source chunks and the most recent turns. When
the student window fills, its oldest sources roll out; they stay in the
archive and come back through lexical search (see ``archive_search``) before
student rescoring.

Token IDs are shared between student and target (``compatible_ids``), so
delivered evidence is an exact token slice of the archived source.
"""
from __future__ import annotations

import bisect
import json
import math
import os
import re
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import grounding

SNAPSHOT_FORMAT = 'orchid-specsession-v1'

# Questions that need the whole history rather than a few excerpts.
BROAD = re.compile(r'\b(summar(y|ize|ise|izing)|recap|overview of (the|this) (session|conversation)'
                   r'|every (file|change|edit|step|command)|all (of )?(the |my |your )?(files|changes|edits|steps|commands)'
                   r'|(entire|whole) (session|conversation|history)|so far in this (session|conversation))\b', re.I)


_QUOTED = re.compile(r'`[^`]*`|"[^"\n]*"|(?:starts|begins)(?: exactly)? with\s*:.*', re.I)
# "without summarizing", "don't summarize", "no summary": the request says not to.
_NEGATED = re.compile(r"\b(?:without|no|not|never|don'?t|do not)\s+(?:a\s+|any\s+)?(?:summar\w*|recap\w*)", re.I)


def is_broad(question):
    """Whether the request itself asks about the whole history; quoted material
    (code spans, quoted text, a line to find) doesn't count."""
    return bool(BROAD.search(_NEGATED.sub(' ', _QUOTED.sub(' ', question or ''))))


# Grounded edits (see grounding.py): requests to write a file, and how an
# earlier read of a file looks in a transcript.
_WRITE_FILE = re.compile(r'\b(?:write|create|rewrite|add)\b[^.\n]{0,40}?\bfile\s+`?([\w./-]+\.\w+)', re.I)
_READ_LINE = re.compile(rb'(^\$ |^\*\*\* |\b(sed|cat|nl|head|tail|less|bat|Get-Content|type)\b)')


def write_paths(question):
    """Paths of files the request asks to have written in full."""
    return list(dict.fromkeys(_WRITE_FILE.findall(question or '')))


_IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]{2,}')


@dataclass(frozen=True)
class SpecLimits:
    student_context: int = 32768
    target_context: int = 32768
    answer_tokens: int = 64
    # Line-aligned chunks; 16 tokens holds about one short definition.
    chunk_tokens: int = 16
    # Rendered evidence per turn, framing included:
    # ceil(new_rate * new source tokens) + recall_tokens.
    new_rate: float = 0.06
    recall_tokens: int = 160
    # Multipliers for sources the request names by path, and by path plus version.
    path_boost: float = 4.0
    version_boost: float = 16.0
    # Stop filling once a chunk scores below this fraction of the best candidate:
    # concentrated (confident) turns stop early, diffuse ones use the full cap.
    relative_floor: float = 0.30
    # The floor is relative to the floor_rank-th best chunk, so one dominant
    # chunk (e.g. the first of several asked values) cannot cut the others.
    floor_rank: int = 3
    # Evidence budget for a question about the whole history ("summarize",
    # "every file you edited"): ~10% of history can't answer it.
    broad_tokens: int = 12288
    # Grounded edits (0 disables; grounding.py): when the answer starts changing a
    # file (or the request asks to write one), the target drafts patch_draft_lines
    # of it and is stopped. The archive regions the draft points at (its context
    # lines or the names it imports, +-patch_region_lines lines, up to
    # patch_regions), the newest earlier patch to the file (patch_prior_tokens)
    # and the most recent history (patch_recent_tokens) are shown for that answer
    # only, within patch_tokens per file; with no region found, the file's newest
    # reads (patch_read_tokens each). Up to patch_files files per answer.
    patch_tokens: int = 4096
    patch_recent_tokens: int = 1024
    patch_read_tokens: int = 2048
    patch_region_lines: int = 40
    patch_regions: int = 2
    patch_prior_tokens: int = 768
    patch_files: int = 3
    patch_draft_lines: int = 12
    # Sources answer(fresh=...) names (the tool results a model just asked for) are
    # delivered in full up to this many tokens per turn, committed; beyond it,
    # their best-scoring chunks.
    fresh_tokens: int = 4096
    # fresh_mode 'intent': a fresh result longer than fresh_small is read by the
    # student, asked what the model's last step (its reasoning and call, the last
    # intent_tokens of its reply) was looking for, and fresh_rate of it (at least
    # fresh_floor tokens, whole lines) reaches the target, marked as partial. A
    # result asked for again (the same output) is delivered in full.
    fresh_mode: str = 'full'
    fresh_rate: float = 0.30
    fresh_small: int = 1024
    fresh_floor: int = 512
    intent_tokens: int = 384
    # Recent question/answer turns kept verbatim when the target is rebuilt.
    rebuild_turns: int = 4
    # Fraction of the target window filled by a rebuild (rest is headroom).
    rebuild_fill: float = 0.5
    # Rolled-out chunks lexical recall may bring back into the student per turn.
    recall_search_chunks: int = 24
    # Evidence refresh (opt-in): re-show top-scoring chunks delivered more than
    # refresh_distance target tokens ago, next to the question, within
    # refresh_tokens. 0 disables it.
    refresh_tokens: int = 0
    refresh_distance: int = 2048


@dataclass
class Source:
    path: str
    version: int
    ids: list
    turn: int
    header: list = field(default_factory=list)
    bounds: list = field(default_factory=list)  # (start, end) token spans of chunks
    anchors: list = field(default_factory=list)  # per chunk: enclosing top-level line span


class SpecSession:
    def __init__(self, tokenizer, student, target, *, system, limits=None, checkpoint=None):
        self.tok, self.student, self.target = tokenizer, student, target
        self.limits = limits or SpecLimits()
        self.system = system
        self.checkpoint = Path(checkpoint) if checkpoint else None
        if self.checkpoint and self.checkpoint.exists():
            raise FileExistsError('Use restore() to reopen an existing session')
        self.last_answer = None
        self.index = LexicalIndex()
        self.pos = {}  # resident chunk key -> student coordinate of its first token
        self.student_rollouts = 0
        self.sources: list[Source] = []
        self.delivered: set = set()  # (source index, chunk index) present in the target now
        self.delivered_at = {}  # chunk -> target position of its latest rendering
        self.turn = 0
        self.pending_new = 0
        self.history = []  # (query ids, reply ids) per completed turn
        self.events = []
        self.target_generation = 0
        self.student_ids = self._frame('system', system)
        self.target_ids = self._frame('system', system)
        self._sep = self._encode('…\n')
        self._open = self._encode('<|im_start|>user\n', True) + self._encode('Relevant excerpts:\n')
        self._close = self._encode('<|im_end|>\n', True)
        self._headers = {}
        self._line_ends = {}
        self._bytes = {}
        self._line_cache = {}
        self._save()

    # ----- compatibility with the gateway service -------------------------
    @property
    def generation(self):
        return self.target_generation

    @property
    def conversation_tokens(self):
        """Tokens of the whole conversation (system, every source, every turn): what the
        prompt would be without selection, as a client counts its context."""
        return (len(self._frame('system', self.system)) + sum(len(s.ids) for s in self.sources)
                + sum(len(q) + len(r) for q, r in self.history))

    def _system(self):
        return self._frame('system', self.system)

    def source_index(self, path, version):
        return next((i for i, s in enumerate(self.sources)
                     if s.path == path and s.version == version), None)

    def close(self):
        store = getattr(self, '_store', None)
        if store is not None:
            store.close()

    # ----- framing -------------------------------------------------------
    def _encode(self, text, special=False):
        return list(self.tok.encode(text, parse_special=special))

    def _frame(self, role, text):
        return (self._encode(f'<|im_start|>{role}\n', True) + self._encode(text)
                + self._encode('<|im_end|>\n', True))

    def _query(self, question, thinking=False):
        """The question and the assistant's opening, as the chat template renders it:
        an open think block when thinking, else an empty one."""
        opening = '<|im_start|>assistant\n<think>\n' if thinking else '<|im_start|>assistant\n<think>\n\n</think>\n\n'
        return self._frame('user', question) + self._encode(opening, True)

    def _reply(self, ids):
        stop = set(getattr(self.target, 'stop_ids', (248044, 248046)))
        suffix = [] if ids and ids[-1] in stop else self._encode('<|im_end|>', True)
        return list(ids) + suffix + self._encode('\n')

    # ----- sources -------------------------------------------------------
    def chunks(self, index):
        return [(index, c) for c in range(len(self.sources[index].bounds))]

    def chunk_ids(self, key):
        source = self.sources[key[0]]
        a, b = source.bounds[key[1]]
        return source.ids[a:b]

    def _bounds(self, ids):
        """Pack whole lines into chunks of at most chunk_tokens tokens.

        Splitting inside a line can cut a number or identifier in half (digits
        are single tokens), so a line longer than a chunk is split after
        whitespace or punctuation when possible, and hard-cut only as a last
        resort.
        """
        size, bounds, start, line, soft = self.limits.chunk_tokens, [], 0, None, None
        for i, token in enumerate(ids):
            raw = self.tok.token_bytes(token)
            if raw.endswith(b'\n'):
                line = i + 1
            elif raw and not (raw[-1:].isalnum() or raw[-1:] == b'_' or raw[-1] >= 0x80):
                soft = i + 1
            if i + 1 - start >= size:
                cut = next((c for c in (line, soft) if c and c > start), i + 1)
                bounds.append((start, cut))
                start, line, soft = cut, None, None
        if start < len(ids):
            bounds.append((start, len(ids)))
        return bounds

    def _anchors(self, ids, bounds):
        """Per chunk, the token span of the nearest top-level line at or before it.

        A top-level line starts with a non-space character (a def, class or
        assignment in code; a heading-like line elsewhere). Delivering it with
        an excerpt keeps values attributable to their definition.
        """
        lines, start = [], 0
        for i, token in enumerate(ids):
            if self.tok.token_bytes(token).endswith(b'\n'):
                lines.append((start, i + 1))
                start = i + 1
        if start < len(ids):
            lines.append((start, len(ids)))
        cap = 2 * self.limits.chunk_tokens
        tops = [(a, b) for a, b in lines
                if b - a <= cap
                and (raw := b''.join(self.tok.token_bytes(t) for t in ids[a:b])).strip()
                and not raw[:1].isspace()]
        out, j = [], -1
        for a, _ in bounds:
            while j + 1 < len(tops) and tops[j + 1][0] <= a:
                j += 1
            out.append(tops[j] if j >= 0 else None)
        return out

    def add_source(self, path, version, text, *, cue=None):
        """Append raw input to the student; the target sees only what is selected."""
        if not isinstance(text, str) or not text:
            raise ValueError('Source text must be nonempty')
        for i, s in enumerate(self.sources):
            if s.path == path and s.version == version:
                return i
        ids = self._encode(text)
        if b''.join(self.tok.token_bytes(t) for t in ids) != text.encode():
            raise ValueError('Tokenizer did not preserve exact source bytes')
        header = self._encode(f'[{path} v{version}]\n')
        prefix = self._encode('<|im_start|>user\n', True) + header
        frame = prefix + ids + self._close
        self._ensure_student_room(len(frame))
        index = len(self.sources)
        bounds = self._bounds(ids)
        self.sources.append(Source(path, version, ids, self.turn, header, bounds,
                                   self._anchors(ids, bounds)))
        body = len(self.student_ids) + len(prefix)
        for key in self.chunks(index):
            self.pos[key] = body + self.sources[index].bounds[key[1]][0]
        self.student_ids += frame
        self.pending_new += len(ids)
        self.index.add(index, [(key, self.chunk_ids(key)) for key in self.chunks(index)], self.tok)
        self._save()
        return index

    # ----- student window ------------------------------------------------
    def _ensure_student_room(self, extra):
        """Roll the oldest resident chunks out of the student when it would overflow.

        Rolled-out chunks stay in the archive; lexical recall can bring them back.
        """
        limit = self.limits.student_context - self.limits.answer_tokens - 512
        if len(self.student_ids) + extra <= limit:
            return
        while self.pos and len(self.student_ids) + extra > limit * 0.75:
            oldest = min(k[0] for k in self.pos)
            for key in [k for k in self.pos if k[0] == oldest]:
                del self.pos[key]
            self._rebuild_student_layout()
        if len(self.student_ids) + extra > limit:
            raise ValueError('Input exceeds the student window')
        self.student_rollouts += 1
        if hasattr(self.student, 'reset'):
            self.student.reset()

    def _excerpt(self, keys, base, lead=()):
        """Student frame for resident chunks; records each chunk's coordinates."""
        ids = self._encode('<|im_start|>user\n', True) + list(lead)
        by_source = {}
        for key in sorted(keys):
            by_source.setdefault(key[0], []).append(key)
        for index, group in by_source.items():
            ids += self.sources[index].header
            for i, key in enumerate(group):
                if i and key[1] != group[i - 1][1] + 1:
                    ids += self._sep
                self.pos[key] = base + len(ids)
                ids += self.chunk_ids(key)
            ids += self._sep
        return ids + self._close

    def _rebuild_student_layout(self):
        """Re-lay the student transcript: system, resident chunks, recent turns."""
        ids = self._frame('system', self.system)
        ids += self._excerpt(list(self.pos), len(ids))
        for query, reply in self.history[-self.limits.rebuild_turns:]:
            ids += query + reply
        self.student_ids = ids

    def _recall(self, question):
        """Bring rolled-out chunks matching the question back into the student."""
        keys = self.index.search(question, self.tok, exclude=self.pos,
                                 limit=self.limits.recall_search_chunks)
        if not keys:
            return []
        lead = self._encode('Recalled from archive:\n')
        trial = self._excerpt(keys, 0, lead)
        for key in keys:
            self.pos.pop(key, None)
        self._ensure_student_room(len(trial) + 512)
        block = self._excerpt(keys, len(self.student_ids), lead)
        self.student_ids += block
        return [list(k) for k in keys]

    # ----- selection -----------------------------------------------------
    def _candidates(self, scores, question='', *, delivered=False):
        """Rank resident chunks; delivered=True ranks only already-delivered ones.

        Chunks containing a span the request quotes verbatim rank first.
        """
        weights = mentioned_sources(question, [(s.path, s.version) for s in self.sources],
                                    self.limits.path_boost, self.limits.version_boost)
        quoted = self._quoted_chunks(question)
        out = []
        for key, a in self.pos.items():
            if (key in self.delivered) != delivered:
                continue
            b = a + len(self.chunk_ids(key))
            out.append((sum(scores[a:b]) / (b - a) * weights.get(key[0], 1.0), key))
        if quoted and out:
            top = max(score for score, _ in out)
            boost = 2 * top if top > 0 else 1.0
            out = [(score + boost if key in quoted else score, key) for score, key in out]
        out.sort(key=lambda item: (-item[0], item[1]))
        return out

    QUOTE_MIN = 16  # characters

    def _quoted_chunks(self, question):
        """Resident chunks where a span the request quotes verbatim begins.

        Spans are backtick, quote or line segments of the request (after a
        trailing "...:" label is removed). People point at evidence by quoting
        it ("the line that starts with ...", "the error `No such file`"); an
        exact match is the strongest retrieval signal there is.
        """
        spans = set()
        for segment in re.findall(r'`([^`]+)`|"([^"]+)"|\'([^\']+)\'', question):
            spans.update(s for s in segment if len(s.strip()) >= self.QUOTE_MIN)
        for line in question.splitlines():
            line = re.sub(r'^.*?(?:starts with|begins with|begins exactly with)\s*:\s*', '', line).strip()
            if len(line) >= self.QUOTE_MIN:
                spans.add(line)
        hits = set()
        for span in spans:
            needle = span.strip().encode()
            if len(needle) < self.QUOTE_MIN:
                continue
            for index, source in enumerate(self.sources):
                text, offsets = self._source_bytes(index)
                at = text.find(needle)
                while at >= 0:
                    token = bisect.bisect_right(offsets, at) - 1
                    chunk = bisect.bisect_right([lo for lo, _ in source.bounds], token) - 1
                    if (index, chunk) in self.pos:
                        hits.add((index, chunk))
                    at = text.find(needle, at + 1)
        return hits

    def _source_bytes(self, index):
        cached = self._bytes.get(index)
        if cached is None:
            pieces = [self.tok.token_bytes(t) for t in self.sources[index].ids]
            offsets, total = [], 0
            for piece in pieces:
                offsets.append(total)
                total += len(piece)
            cached = (b''.join(pieces), offsets)
            self._bytes[index] = cached
        return cached

    def _refresh(self, scores, question, ranked):
        """Far-back delivered chunks that outrank this turn's best new candidate."""
        if not self.limits.refresh_tokens:
            return []
        floor = ranked[0][0] if ranked else 0.0
        end = len(self.target_ids)
        far = [(s, k) for s, k in self._candidates(scores, question, delivered=True)
               if s >= floor and end - self.delivered_at.get(k, end) > self.limits.refresh_distance]
        out, used = [], 0
        for _, key in far:
            cost = len(self.chunk_ids(key)) + 4
            if used + cost > self.limits.refresh_tokens:
                break
            out.append(key)
            used += cost
        return out

    LINE_GROUP_MAX = 8  # chunks: a line longer than this may still be delivered in part

    def _line_group(self, key):
        """The chunks of the (long) line this chunk belongs to, in order.

        Long lines are split across chunks; delivering only part of one hands
        the target half a statement or message, so lines are delivered whole.
        """
        index, c = key
        source = self.sources[index]
        ends = self._line_ends.get(index)
        if ends is None:
            ends = [b == len(source.ids) or self.tok.token_bytes(source.ids[b - 1]).endswith(b'\n')
                    for _, b in source.bounds]
            self._line_ends[index] = ends
        lo = c
        while lo > 0 and not ends[lo - 1] and c - lo < self.LINE_GROUP_MAX - 1:
            lo -= 1
        hi = c
        while not ends[hi] and hi + 1 < len(ends) and hi - lo < self.LINE_GROUP_MAX - 1:
            hi += 1
        return [(index, i) for i in range(lo, hi + 1)]

    def _choose(self, ranked, budget, floor=True):
        """Fill the budget in rank order, charging each pick its rendered cost.

        A pick is the chunk plus the rest of its line. Cost includes scope
        anchors, source headers and gap markers, so scattered picks pay for
        their framing and contiguous picks are cheap.
        """
        chosen, misses = [], 0
        best = ranked[min(len(ranked), self.limits.floor_rank) - 1][0] if ranked else 0.0
        for score, key in ranked:
            if floor and self.limits.relative_floor and score < best * self.limits.relative_floor:
                break
            if key in chosen:
                continue
            line = self._line_group(key)
            tail = line[line.index(key):]
            # Prefer the whole line, then the chunk to the end of its line, then the chunk.
            for group in (line, tail, [key]):
                group = [k for k in group if k not in chosen and k not in self.delivered]
                if len(self._render(chosen + group)[0]) <= budget:
                    chosen.extend(group)
                    misses = 0
                    break
            else:
                misses += 1
                if misses >= 64:
                    break
        return chosen

    def _status_header(self, index):
        """Target-side header: path, version, and supersession once it is known.

        The transcript is append-only, so a header must never claim a status
        that can later become false (such as "current").
        """
        source = self.sources[index]
        latest = max(s.version for s in self.sources if s.path == source.path)
        status = '' if source.version == latest else f', superseded by v{latest}'
        key = (index, status)
        if key not in self._headers:
            self._headers[key] = self._encode(f'[{source.path} v{source.version}{status}]\n')
        return self._headers[key]

    def _manifest(self, question):
        """One line naming the latest version of each path the request mentions."""
        latest = {}
        for s in self.sources:
            if s.path in question:
                latest[s.path] = max(latest.get(s.path, 0), s.version)
        if not latest:
            return []
        return self._encode('Latest versions: ' + ', '.join(f'{p} v{v}' for p, v in latest.items()) + '\n')

    def _render(self, keys, lead=()):
        """One header per source, chunks in source order, gap markers between runs."""
        if not keys and not lead:
            return [], {'chunk_tokens': 0, 'framing_tokens': 0}
        ids, body = list(self._open) + list(lead), 0
        by_source = {}
        for key in sorted(keys):
            by_source.setdefault(key[0], []).append(key[1])
        for index, numbers in by_source.items():
            source = self.sources[index]
            ids += self._status_header(index)
            for i, c in enumerate(numbers):
                a, b = source.bounds[c]
                starts_run = i == 0 or c != numbers[i - 1] + 1
                if i and starts_run:
                    ids += self._sep
                # Each run (not each chunk) carries its enclosing top-level line.
                anchor = source.anchors[c] if source.anchors and starts_run else None
                if anchor and anchor[0] < a:
                    ids += source.ids[anchor[0]:anchor[1]] + self._sep
                piece = source.ids[a:b]
                ids += piece
                body += len(piece)
            ids += self._sep
        ids += self._close
        return ids, {'chunk_tokens': body, 'framing_tokens': len(ids) - body}

    def _lines(self, index):
        """(normalized lines, byte offset of each line start) of a source, cached."""
        cached = self._line_cache.get(index)
        if cached is None:
            text = self._source_bytes(index)[0]
            starts, rows, at = [], [], 0
            for raw in text.split(b'\n'):
                starts.append(at)
                rows.append(grounding.norm(raw.decode(errors='replace')))
                at += len(raw) + 1
            cached = self._line_cache[index] = (rows, starts)
        return cached

    def _line_chunks(self, index, lo, hi):
        """Chunk keys covering lines lo..hi (inclusive) of a source."""
        rows, starts = self._lines(index)
        lo, hi = max(0, lo), min(len(rows) - 1, hi)
        text, offsets = self._source_bytes(index)
        first = bisect.bisect_right(offsets, starts[lo]) - 1
        end = starts[hi + 1] - 1 if hi + 1 < len(starts) else len(text)
        last = bisect.bisect_right(offsets, max(starts[lo], end - 1)) - 1
        bounds = [a for a, _ in self.sources[index].bounds]
        return [(index, c) for c in range(bisect.bisect_right(bounds, first) - 1,
                                          bisect.bisect_right(bounds, last))]

    def _mentions(self, terms, read_like=True):
        """(rank, source, chunk) of each mention of terms, newest and read-like first."""
        out, starts = [], {}
        for rank, term in enumerate(dict.fromkeys(t for t in terms if len(t) >= 4)):
            needle = term.encode()
            for index in range(len(self.sources) - 1, -1, -1):
                source = self.sources[index]
                if term in source.path:  # A tool result labelled by the call that read the file.
                    out.append((0, rank, -index, 0, index, 0))
                text, offsets = self._source_bytes(index)
                at = text.rfind(needle)
                while at >= 0:
                    line = text[text.rfind(b'\n', 0, at) + 1:text.find(b'\n', at) % (len(text) + 1)]
                    token = bisect.bisect_right(offsets, at) - 1
                    if index not in starts:
                        starts[index] = [lo for lo, _ in source.bounds]
                    chunk = bisect.bisect_right(starts[index], token) - 1
                    out.append((0 if read_like and _READ_LINE.search(line) else 1, rank, -index, -chunk,
                                index, chunk))
                    at = text.rfind(needle, 0, at)
        return [(index, chunk) for *_, index, chunk in sorted(out)]

    def _regions(self, path, lines, names):
        """Archive regions a draft points at, best first: clusters of its context lines
        or of definitions of the names it uses, newest first among equals."""
        wanted = {line: i for i, line in enumerate(lines)}
        span = self.limits.patch_region_lines
        base = path.rsplit('/', 1)[-1]
        clusters = []
        for index in range(len(self.sources) - 1, -1, -1):
            rows, _ = self._lines(index)
            hits = []
            for n, row in enumerate(rows):
                if row in wanted:
                    hits.append((n, ('line', wanted[row])))
                elif lines and len(lines[0]) >= 12 and lines[0] in row:
                    hits.append((n, ('line', 0)))  # The hunk location line, quoted in part.
                if names and (name := grounding.defines(row, names)):
                    hits.append((n, ('name', name)))
            group = []
            for n, what in hits + [(None, None)]:
                if group and (n is None or n - group[-1][0] > 2 * span):
                    found = {w for _, w in group}
                    near = '\n'.join(rows[max(0, group[0][0] - 400):group[0][0]])
                    bonus = 0.5 if base in self.sources[index].path or base in near else 0.0
                    if len(found) >= 2 or any(w[0] == 'name' for w in found):
                        clusters.append((len(found) + bonus, index, group[0][0], group[-1][0], found))
                    group = []
                if n is not None:
                    group.append((n, what))
        clusters.sort(key=lambda c: (-c[0], -c[1]))
        picked, covered = [], set()
        for score, index, lo, hi, found in clusters:
            if found <= covered:
                continue  # The same code again (an older read of it).
            picked.append((index, lo - span, hi + span))
            covered |= found
            if len(picked) >= self.limits.patch_regions:
                break
        return picked

    def _ground(self, path, kind, draft, skip, budget):
        """Chunks grounding a change to ``path`` from its draft, and what was found."""
        limits, taken = self.limits, set(skip) | self.delivered
        lines, names, modules = grounding.anchors(draft, kind)
        groups = []  # Priority order: first region, prior patch, recent history, others.

        def window(index, chunk, tokens):
            keys = []
            while index < len(self.sources) and tokens > 0:
                for c in range(chunk, len(self.sources[index].bounds)):
                    keys.append((index, c))
                    tokens -= len(self.chunk_ids((index, c)))
                    if tokens <= 0:
                        break
                index, chunk = index + 1, 0
            return keys
        regions = self._regions(path, lines, names)
        region_keys = [self._line_chunks(*r) for r in regions]
        if region_keys:
            groups.append(region_keys[0])
        prior = self._mentions([f'File: {path}', f'+++ b/{path}'], read_like=False)
        if prior and kind != 'write':
            groups.append(window(*prior[0], limits.patch_prior_tokens))
        recent, used = [], 0
        for index in range(len(self.sources) - 1, -1, -1):
            for c in range(len(self.sources[index].bounds) - 1, -1, -1):
                recent.append((index, c))
                used += len(self.chunk_ids((index, c)))
                if used >= limits.patch_recent_tokens:
                    break
            if used >= limits.patch_recent_tokens:
                break
        groups.append(recent)
        groups += region_keys[1:]
        if not region_keys:  # Nothing located: the file's (or its modules') newest reads.
            name = path.rsplit('/', 1)[-1]
            terms = [path, name] + ([name[5:]] if name.startswith('test_') else []) + [f'{m}.' for m in modules]
            for index, chunk in self._mentions(terms)[:4]:
                groups.append(window(index, chunk, limits.patch_read_tokens))
        order = []
        for group in groups:
            for key in group:
                if key not in taken:
                    taken.add(key)
                    order.append(key)
        base = len(self._render(list(skip))[0])
        lo, hi = 0, len(order)  # Longest prefix of the picks whose rendering fits.
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(self._render(list(skip) + order[:mid])[0]) - base <= budget:
                lo = mid
            else:
                hi = mid - 1
        info = {'path': path, 'kind': kind, 'regions': len(regions), 'anchors': len(lines) + len(names),
                'chunks': lo, 'tokens': sum(len(self.chunk_ids(k)) for k in order[:lo])}
        return order[:lo], info

    def align_patches(self, text):
        """``text`` with its apply_patch hunks made exact against the archive (grounding.align_patch)."""
        if '*** Begin Patch' not in text or not self.limits.patch_tokens:
            return text, None
        sources = [self._source_bytes(i)[0].decode(errors='replace').split('\n')
                   for i in range(len(self.sources) - 1, -1, -1)]
        return grounding.align_patch(text, sources)

    def _patch_budget(self, query, maximum):
        room = self.limits.target_context - len(self._frame('system', self.system)) - len(query) - maximum
        return max(0, min(self.limits.patch_tokens, room // 2))

    def _with_evidence(self, evidence, query):
        """The turn's excerpts and question as one user message.

        As two consecutive user messages, Flash-Next often ends its turn at once
        (an empty reply) when the excerpts end mid-transcript, e.g. on a shell command.
        """
        user = self._encode('<|im_start|>user\n', True)
        if not evidence or query[:len(user)] != user:
            return evidence + query
        return evidence[:len(evidence) - len(self._close)] + self._encode('\n') + query[len(user):]

    def _generate(self, scores, keys, question, query, maximum, prefix=(), ephemeral=()):
        """One target call on the current transcript plus evidence.

        ``keys`` are committed: they stay in the transcript. ``ephemeral`` chunks
        (a grounded edit's file, a whole-history answer's evidence) are shown after
        them for this answer only, so they never fill the window; the next turn
        re-reads only this turn's question and reply.
        Returns (transcript before the reply, output, prompt evidence, cost, rebuilt).
        """
        committed, cost = self._render(keys, list(getattr(self, '_note', [])) + list(self._manifest(question)))
        evidence = committed
        if ephemeral:
            extra, extra_cost = self._render(ephemeral)
            body = extra[len(self._open):len(extra) - len(self._close)]
            evidence = (committed[:len(committed) - len(self._close)] + body + self._close if committed
                        else extra)
            cost = {k: cost[k] + extra_cost[k] for k in cost}
        rebuilt = False
        if len(self.target_ids) + len(evidence) + len(query) + maximum > self.limits.target_context:
            if scores is None:  # A verbatim turn that still needs a rebuild: the student scores now.
                prompt = self.student_ids + query
                scores = self.student.trace(prompt, 1.0)['scores']
            self._rebuild_target(scores, list(keys) + list(ephemeral), question,
                                 reserve=len(evidence) + len(query) + maximum)
            rebuilt = True
        target_prompt = self.target_ids + self._with_evidence(evidence, query)
        output = list(self.target(target_prompt + list(prefix), maximum - len(prefix)))
        if not output or len(output) > maximum - len(prefix):
            raise ValueError('Invalid target output length')
        return self.target_ids + self._with_evidence(committed, query), list(prefix) + output, evidence, cost, rebuilt

    # ----- answering -----------------------------------------------------
    def answer(self, question, *, maximum=None, fresh=(), thinking=False, broad=None):
        maximum = maximum or self.limits.answer_tokens
        started = time.perf_counter()
        query = self._query(question, thinking)
        new_tokens, self.pending_new = self.pending_new, 0
        self._ensure_student_room(len(query) + maximum)
        prompt = self.student_ids + query
        broad = is_broad(question) if broad is None else broad
        # Verbatim turn: everything new is what the model just asked for (fresh tool results)
        # and fits, so it goes straight to the target and the student is not waited on; its
        # prompt cache reads this turn's text at its next selection.
        new_sources = [i for i, s in enumerate(self.sources) if s.turn == self.turn]
        new_keys = [k for i in new_sources for k in self.chunks(i) if k not in self.delivered]
        # Intent mode: large fresh results are read by the student, for what the last step wanted.
        large = [i for i in fresh if self.limits.fresh_mode == 'intent' and self.sources[i].turn == self.turn
                 and len(self.sources[i].ids) > self.limits.fresh_small]
        if large and self.history:
            intent = self.tok.decode(self.history[-1][1][-self.limits.intent_tokens:])
            intent = re.sub(r'<\|[^>]+\|>', '', intent).strip()
            prompt = self.student_ids + self._query(
                'Find what the assistant needs from the tool results above for its next step. '
                f'Its last step:\n{intent}', False)
        verbatim = (bool(new_sources) and not broad and not large and set(new_sources) <= set(fresh)
                    and 0 < new_tokens <= self.limits.fresh_tokens
                    and len(self.target_ids) + 2 * new_tokens + len(query) + maximum
                    + 2 * self.limits.patch_tokens <= self.limits.target_context)
        trace, scores, retrieved = {}, None, []
        if not verbatim:
            retrieved = self._recall(question) if self.student_rollouts else []
            trace = self.student.trace(prompt, 1.0)
            scores = trace['scores']
            if len(scores) != len(prompt):
                raise ValueError('Student scores do not cover the prompt')
        student_s = time.perf_counter() - started
        if verbatim:
            budget, ranked, pinned, refreshed, extra = 0, [], list(new_keys), [], []
            chosen = list(new_keys)
        if not verbatim and broad:
            # At most half the window left beside the system text, question and answer,
            # so a rebuild can always make room for it.
            room = self.limits.target_context - len(self._frame('system', self.system)) - len(query) - maximum
            budget = max(0, min(self.limits.broad_tokens, room // 2))
        elif not verbatim:
            budget = math.ceil(self.limits.new_rate * new_tokens) + self.limits.recall_tokens
        self._note = []
        if not verbatim:
            ranked = self._candidates(scores, question)
            pinned = []
            if fresh and self.limits.fresh_tokens:
                again = [i for i in fresh if self.sources[i].turn < self.turn]
                wanted = set(fresh) - set(large) - set(again)
                pinned = self._choose([r for r in ranked if r[1][0] in wanted], self.limits.fresh_tokens, floor=False)
                for index in again:  # Asked for again: the whole output, in one piece, even what was shown before.
                    if len(self.sources[index].ids) <= self.limits.fresh_tokens:
                        pinned += [k for k in self.chunks(index) if k not in pinned]
                for index in large:  # The part of each large result the last step was looking for.
                    size = len(self.sources[index].ids)
                    room = max(self.limits.fresh_floor, math.ceil(self.limits.fresh_rate * size))
                    pinned += self._choose([r for r in ranked if r[1][0] == index], room, floor=False)
                if large:
                    self._note = self._encode('(Long tool output is shown in part: the lines most relevant to '
                                              'the current step. Run the same command again to see all of it.)\n')
                ranked = [r for r in ranked if r[1] not in set(pinned)]
            chosen = pinned + self._choose(ranked, budget, floor=not broad)
            refreshed = self._refresh(scores, question, ranked)
            # Whole-history evidence is for this answer only (see _generate).
            extra, chosen = (chosen[len(pinned):], pinned) if broad else ([], chosen)
        grounded = []
        saved = (self.target_ids, set(self.delivered), self.target_generation, dict(self.delivered_at))
        selected_s = time.perf_counter() - started
        limits = self.limits
        write = (write_paths(question) or [None])[0]
        done, prefix, rebuilt = set(), [], False
        try:
            for attempt in range(1 + (limits.patch_files if limits.patch_tokens else 0)):
                watch = None
                if limits.patch_tokens and attempt < limits.patch_files and maximum > 1:
                    watch = grounding.Watch(self.tok, self.target, prefix=prefix, grounded=done,
                                            write=write if attempt == 0 else None, lines=limits.patch_draft_lines,
                                            thinking=thinking)
                    self.target.on_token = watch
                try:
                    target_prompt, output, evidence, cost, again = self._generate(
                        scores, chosen + refreshed, question, query, maximum, prefix, ephemeral=extra)
                finally:
                    if watch:
                        self.target.on_token = watch.downstream
                rebuilt = rebuilt or again
                if not watch or not watch.finish(output[len(prefix):]):
                    break
                if watch.cut >= maximum - 1 or not watch.path:
                    watch.release_all()  # No room left to continue: keep the output as it is.
                    break
                # The answer began changing a file: show what its draft points at, then redo it.
                spent = sum(g['tokens'] for g in grounded)
                budget = min(self._patch_budget(query, maximum), 2 * limits.patch_tokens - spent)
                fetched, info = self._ground(watch.path, watch.kind, watch.draft,
                                             chosen + refreshed + extra, max(0, budget))
                info['discarded_tokens'] = len(watch.ids) - watch.cut
                grounded.append(info)
                extra = extra + fetched
                done.add(watch.path)
                prefix = watch.ids[:watch.cut]
                if getattr(self.target, 'cancel', None) is not None:
                    self.target.cancel = threading.Event()
        except BaseException:
            # Nothing is committed: the same request can be retried.
            self.target_ids, self.delivered, self.target_generation, self.delivered_at = saved
            self.pending_new += new_tokens
            raise
        reply = self._reply(output)
        self.delivered.update(chosen + refreshed)
        committed_at = len(target_prompt) - len(query) - len(self._render(chosen + refreshed)[0])
        for key in chosen + refreshed:
            self.delivered_at[key] = max(0, committed_at)
        self.target_ids = target_prompt + reply
        self.student_ids = prompt + reply
        self.history.append((query, reply))
        self.turn += 1
        self.last_answer = {'question': question, 'output_ids': list(output), 'turn': self.turn,
                            'maximum': maximum}
        new_keys = sum(1 for k in chosen if self.sources[k[0]].turn == self.turn - 1)
        event = {'kind': 'answer', 'turn': self.turn, 'target_tokens': len(target_prompt),
                 'refreshed_chunks': len(refreshed),
                 'new_tokens': new_tokens, 'budget': budget, 'broad': broad,
                 'delivered_chunks': len(chosen), 'delivered_new_chunks': new_keys,
                 'ephemeral_chunks': len(extra), 'fresh_chunks': len(pinned),
                 'delivered_recall_chunks': len(chosen) - new_keys,
                 'evidence_tokens': len(evidence), **cost,
                 'query_tokens': len(query), 'reply_tokens': len(reply),
                 'candidates': len(ranked), 'retrieved_sources': retrieved,
                 'target_prompt_tokens': len(target_prompt), 'student_prompt_tokens': len(prompt),
                 'student_reused': trace.get('reused_prefix_tokens'), 'verbatim': verbatim,
                 'student_s': student_s, 'select_s': selected_s - student_s,
                 'target_rebuilt': rebuilt, 'target_generation': self.target_generation,
                 'grounded': grounded or None,
                 'chosen': [list(k) for k in chosen + extra]}
        self.events.append(event)
        self._save()
        return output

    # ----- target window -------------------------------------------------
    def _rebuild_target(self, scores, chosen, question, *, reserve):
        """Fresh target transcript: system, top-scored chunks, recent turns.

        Costs one uncached target prefill of about rebuild_fill * window; it is
        rare because the target only holds the selected fraction.
        """
        limits = self.limits
        recent = [q + r for q, r in self.history[-limits.rebuild_turns:]]
        base = self._frame('system', self.system)
        room = (min(int(limits.target_context * limits.rebuild_fill), limits.target_context - reserve)
                - len(base) - sum(map(len, recent)) - len(self._open) - len(self._close))
        self.delivered = set()
        skip = set(chosen)
        ranked = [(s, k) for s, k in self._candidates(scores, question) if k not in skip]
        keep = self._choose(ranked, max(0, room))
        evidence, _ = self._render(keep)
        while keep and len(evidence) > room:  # Headers and gap markers count too.
            keep.pop()
            evidence, _ = self._render(keep)
        self.target_ids = base + evidence + [t for turn in recent for t in turn]
        self.delivered = set(keep)
        self.delivered_at = {k: len(base) for k in keep}
        self.target_generation += 1
        if len(self.target_ids) + reserve > limits.target_context:
            raise ValueError('Target rebuild exceeds its window')

    # ----- persistence ---------------------------------------------------
    def _save(self):
        """Atomic checkpoint after every committed change (no-op without a path).

        A `.sqlite3` checkpoint is written incrementally (session_store); a `.json`
        one is the earlier whole-file format.
        """
        if not self.checkpoint:
            return
        if self.checkpoint.suffix == '.sqlite3':
            if getattr(self, '_store', None) is None:
                from .session_store import SessionStore
                self._store = SessionStore(self.checkpoint)
            self._store.write(self.snapshot())
            return
        self.checkpoint.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.checkpoint.with_name(self.checkpoint.name + '.tmp')
        with tmp.open('w') as f:
            json.dump(self.snapshot(), f, separators=(',', ':'))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.checkpoint)

    @classmethod
    def restore(cls, path, tokenizer, student, target):
        path, store = Path(path), None
        if path.suffix == '.sqlite3':
            legacy = path.with_suffix('.json')
            if not path.exists() and legacy.exists():
                # One-time migration from the whole-file JSON checkpoint; the old file is
                # kept (renamed), not deleted.
                obj = cls.restore(legacy, tokenizer, student, target)
                obj.checkpoint = path
                obj._save()
                legacy.rename(legacy.with_name(legacy.name + '.migrated'))
                return obj
            from .session_store import SessionStore
            store = SessionStore(path)
            state = store.read()
        else:
            state = json.loads(path.read_text())
        if state.get('format') != SNAPSHOT_FORMAT:
            raise ValueError('Unrecognized SpecSession checkpoint')
        obj = cls.__new__(cls)
        obj.tok, obj.student, obj.target = tokenizer, student, target
        obj.limits = SpecLimits(**state['limits'])
        obj.system, obj.checkpoint = state['system'], Path(path)
        obj.sources = [Source(**{**s, 'bounds': [tuple(b) for b in s['bounds']],
                                 'anchors': [tuple(a) if a else None for a in s['anchors']]})
                       for s in state['sources']]
        obj.delivered = {tuple(k) for k in state['delivered']}
        obj.delivered_at = {tuple(k): v for k, v in state.get('delivered_at', [])}
        obj.pos = {tuple(k): v for k, v in state['pos']}
        obj.history = [(q, r) for q, r in state['history']]
        obj.events = state['events']
        for key in ('turn', 'pending_new', 'student_ids', 'target_ids', 'target_generation',
                    'student_rollouts', 'last_answer'):
            setattr(obj, key, state[key])
        obj.index = LexicalIndex()
        for i in range(len(obj.sources)):
            obj.index.add(i, [(k, obj.chunk_ids(k)) for k in obj.chunks(i)], tokenizer)
        obj._sep = obj._encode('…\n')
        obj._open = obj._encode('<|im_start|>user\n', True) + obj._encode(
            'Relevant excerpts:\n')
        obj._close = obj._encode('<|im_end|>\n', True)
        obj._headers = {}
        obj._line_ends = {}
        obj._bytes = {}
        obj._line_cache = {}
        obj._store = store
        # The student's prefix cache is process memory; the next trace rebuilds it.
        if hasattr(student, 'reset'):
            student.reset()
        return obj

    def snapshot(self):
        return {'format': SNAPSHOT_FORMAT, 'system': self.system, 'events': self.events[-64:],
                'last_answer': self.last_answer,
                'limits': asdict(self.limits), 'turn': self.turn,
                'sources': [asdict(s) for s in self.sources],
                'delivered': sorted(list(k) for k in self.delivered),
                'history': [[q, r] for q, r in self.history],
                'student_ids': self.student_ids, 'target_ids': self.target_ids,
                'target_generation': self.target_generation, 'pending_new': self.pending_new,
                'pos': sorted([list(k), v] for k, v in self.pos.items()),
                'delivered_at': sorted([list(k), v] for k, v in self.delivered_at.items()),
                'student_rollouts': self.student_rollouts}


class LexicalIndex:
    """Identifier index over archived chunks, ranked by inverse frequency."""

    def __init__(self):
        self.terms = {}  # chunk key -> set of identifiers
        self.df = Counter()

    def add(self, index, chunks, tok):
        for key, ids in chunks:
            text = b''.join(tok.token_bytes(t) for t in ids).decode('utf-8', errors='replace')
            terms = set(_IDENT.findall(text))
            self.terms[key] = terms
            self.df.update(terms)

    def search(self, question, tok, *, exclude, limit):
        wanted = set(_IDENT.findall(question))
        n = max(1, len(self.terms))
        scored = []
        for key, terms in self.terms.items():
            if key in exclude:
                continue
            hit = wanted & terms
            if hit:
                scored.append((sum(math.log(n / self.df[t]) for t in hit), key))
        scored.sort(key=lambda item: (-item[0], item[1]))
        keys = []
        for _, key in scored:
            for k in ((key[0], key[1] - 1), key, (key[0], key[1] + 1)):
                if k in self.terms and k not in exclude and k not in keys:
                    keys.append(k)
            if len(keys) >= limit:
                break
        return keys[:limit]



_VERSION = r'\s*(?:,\s*)?(?:revision|rev\.?|version|v|@v)\s*(\d+)'


def mentioned_sources(question, sources, boost_path=4.0, boost_version=16.0):
    """Score multipliers for sources a request names explicitly.

    A named path boosts every version of it; a version named right after the
    path ("app/x.py revision 2", "app/x.py v2") boosts that version further.
    Requests naming no path leave all weights at 1.
    """
    weights = {}
    for index, (path, version) in enumerate(sources):
        if path not in question:
            continue
        named = {int(v) for v in re.findall(re.escape(path) + _VERSION, question)}
        weights[index] = boost_version if version in named else (
            boost_path if not named else 1.0)
    return weights
