"""Grounded edits: find exactly what a patch or a new file needs from the archive.

A sparse selection rarely holds the current text of the lines an edit changes,
so an edit written from it patches lines that aren't there. Instead, the target
drafts the start of each file's change and is stopped there (``Watch``). The
draft's hunk location and context lines, or a new file's imports (``anchors``),
are then found in the lossless archive (``SpecSession._ground``). Only those
regions are delivered, plus the newest earlier patch to the file and the most
recent history, and the target writes the change again from the text before
the draft.
"""
from __future__ import annotations

import bisect
import re

STARTS = (b'*** Begin Patch', b'diff --git ')
HEADS = ((b'*** Update File: ', 'update'), (b'*** Add File: ', 'add'), (b'*** Delete File: ', 'delete'),
         (b'+++ b/', 'update'), (b'diff --git a/', 'update'))
_END = b'*** End Patch'
_THINK_END = b'</think>'
_IMPORT = re.compile(r'^\s*(?:from\s+([\w.]+)\s+import\s+\(?([\w\s,]+?)\)?\s*$|import\s+([\w.]+))')
_DEFINES = re.compile(r'^\s*(?:async\s+def|def|class|fun|function|interface|object|struct|enum|val|var|const|let)'
                      r'\s+([A-Za-z_]\w*)')


def norm(line):
    return re.sub(r'\s+', ' ', line.strip())


def header(line):
    """(kind, path) when a complete line is a patch file header."""
    for head, kind in HEADS:
        if line.startswith(head):
            rest = line[len(head):].split()
            return (kind, rest[0].decode(errors='replace')) if rest else None
    return None


def _could_be_marker(pending):
    return any(m.startswith(pending) or pending.startswith(m) for m in STARTS + tuple(h for h, _ in HEADS))


class Watch:
    """Target token hook that stops the output at the start of an ungrounded file change.

    Tokens pass straight on until a patch names a file not yet grounded; from the
    start of that header line on, they are held back, and the target is stopped
    once the draft (the header plus up to ``lines`` lines) is long enough to
    locate. With ``write``, the whole output is the draft of that new file. A
    line that could still become a marker or header is held until it can't, so
    no ungrounded change reaches the client.

    With ``thinking``, the reasoning (up to ``</think>``) passes untouched: a patch
    the model only thinks about is not a change.

    ``cut`` is the token index the answer continues from; ``draft`` the held text.
    """

    def __init__(self, tok, target, *, prefix=(), grounded=(), write=None, lines=12, tokens=320, thinking=False):
        self.tok, self.target = tok, target
        self.downstream = getattr(target, 'on_token', None)
        self.ids, self.ends, self.data = [], [], b''
        for token in prefix:
            self._add(token)
        self.sent = len(self.ids)
        self.grounded, self.lines, self.tokens = set(grounded), lines, tokens
        end = self.data.find(_THINK_END)
        self.thinking = thinking and end < 0  # Still inside the reasoning.
        answer = self.data[end + len(_THINK_END):] if thinking and end >= 0 else self.data
        self.in_patch = any(m in answer for m in STARTS)
        self.line_start = self.data.rfind(b'\n') + 1
        self.cut = self.path = self.kind = self.draft_from = None
        self.done = self.fed = False
        self.write = write
        if write and not self.thinking:
            self._start_write()

    def _start_write(self):
        self.cut, self.path, self.kind, self.draft_from = len(self.ids), self.write, 'write', len(self.data)


    def _add(self, token):
        self.ids.append(token)
        self.data += self.tok.token_bytes(token)
        self.ends.append(len(self.data))

    def __call__(self, token):
        self.fed = True
        if self.done:
            return
        self._add(token)
        if self.thinking and not self._past_reasoning():
            return
        if self.cut is None:
            self._scan()
        if self.cut is not None:
            self._check_draft()

    def _past_reasoning(self):
        """Pass the reasoning on; True once it has ended (scanning starts after it)."""
        end = self.data.find(_THINK_END)
        if end < 0:
            keep = max((k for k in range(1, len(_THINK_END)) if self.data.endswith(_THINK_END[:k])), default=0)
            self._release(bisect.bisect_right(self.ends, len(self.data) - keep))
            return False
        self.thinking = False
        self.line_start = end + len(_THINK_END)
        self._release(bisect.bisect_right(self.ends, self.line_start))
        if self.write:
            # A new file's draft starts after the reasoning: continue from the tokens passed on.
            self.cut = self.sent
            self.path, self.kind, self.draft_from = self.write, 'write', self.line_start
        return True

    def _scan(self):
        while (nl := self.data.find(b'\n', self.line_start)) >= 0:
            line = self.data[self.line_start:nl]
            if any(line.startswith(m) for m in STARTS):
                self.in_patch = True
            found = header(line) if self.in_patch else None
            if found and found[0] != 'delete' and found[1] not in self.grounded:
                self.kind, self.path = found
                self.cut = bisect.bisect_right(self.ends, self.line_start)  # the token holding the line start
                self.draft_from = self.line_start
                self.line_start = nl + 1
                return
            self.line_start = nl + 1
        pending = self.data[self.line_start:]
        limit = self.line_start if pending and _could_be_marker(pending) else len(self.data)
        self._release(bisect.bisect_right(self.ends, limit))

    def _check_draft(self):
        draft = self.data[self.draft_from:]
        complete = draft.split(b'\n')[:-1]
        body = complete if self.kind == 'write' else complete[1:]
        if (len(body) >= self.lines or len(self.ids) - self.cut > self.tokens
                or any(header(line) or line.startswith(_END) for line in body)):
            self.done = True
            cancel = getattr(self.target, 'cancel', None)
            if cancel is not None:
                cancel.set()

    def _release(self, n):
        while self.sent < n:
            if self.downstream:
                self.downstream(self.ids[self.sent])
            self.sent += 1

    def finish(self, new_tokens):
        """After the call: True when the output began an ungrounded file change."""
        if not self.fed:  # A target that does not call on_token: check its output instead.
            downstream, self.downstream = self.downstream, None
            for token in new_tokens:
                self(token)
            self.downstream = downstream
        if self.cut is None:
            self._release(len(self.ids))
            return False
        self.draft = self.data[self.draft_from:].decode(errors='replace')
        return True

    def release_all(self):
        self._release(len(self.ids))


def anchors(draft, kind):
    """What a draft expects to find: (context lines, defined or imported names, module names).

    For a change to an existing file, its hunk location (``@@ ...``) and its context
    and removed lines; for any draft, the names it imports.
    """
    lines, names, modules = [], [], []
    rows = draft.splitlines()
    for raw in rows if kind == 'write' else rows[1:]:
        if kind == 'update' and raw.startswith('@@'):
            text = norm(raw.strip('@ '))
            if len(text) >= 6:
                lines.append(text)
                names += _DEFINES.findall(text)
            continue
        if kind == 'update' and raw[:1] in (' ', '-') and not raw.startswith('---'):
            text = norm(raw[1:])
            if len(text) >= 8 and sum(ch.isalnum() for ch in text) >= 5:
                lines.append(text)
        body = raw[1:] if kind in ('update', 'add') and raw[:1] in ('+', '-', ' ') else raw
        match = _IMPORT.match(body)
        if match:
            module = match.group(1) or match.group(3)
            modules.append(module.lstrip('.').rsplit('.', 1)[-1])
            names += [n.strip() for n in (match.group(2) or '').split(',') if n.strip().isidentifier()]
    return list(dict.fromkeys(lines)), list(dict.fromkeys(names)), [m for m in dict.fromkeys(modules) if m]


def defines(line, names):
    """The name a line defines (def/class/... NAME, or NAME = ...), if it is one of ``names``."""
    match = _DEFINES.match(line)
    if match and match.group(1) in names:
        return match.group(1)
    head = line.split('=', 1)[0].strip() if '=' in line else ''
    return head if head in names else None


# ----- apply: a patch made exact against the archive's real text --------------
_NUMBERED = re.compile(r'^\s*\d+(?:\t|: ?| {2,})')  # `nl -ba`, `grep -n`, `cat -n` line numbers


def _plain(line):
    """A read's line without its line-number prefix."""
    return _NUMBERED.sub('', line, count=1)


def _close(a, b):
    """Two normalized lines that are the same code (allowing a small slip)."""
    if a == b:
        return True
    if min(len(a), len(b)) < 8:
        return False
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.85


def _best_window(old, sources, span=40, header=''):
    """(score, source, first row, last row, mapping old index -> row) of the archive window that
    holds the hunk's old lines best, or None. Newest source first among equals. Windows start at
    exact matches of an old line, at the hunk header's line, or (when neither is found) at near
    matches."""
    want = [norm(t) for _, t in old]
    header = norm(header)
    best = None
    for rank, rows in enumerate(sources):
        plain = [norm(_plain(r)) for r in rows]
        index = {}
        for j, r in enumerate(plain):
            index.setdefault(r, []).append(j)
        starts = {j - i for i, w in enumerate(want) if len(w) >= 6 for j in index.get(w, ())}
        if len(header) >= 8:
            starts |= {j + 1 for j, r in enumerate(plain) if header in r}
        if not starts:
            starts = {j - i for i, w in enumerate(want) if len(w) >= 12
                      for j, r in enumerate(plain) if r[:6] == w[:6] and _close(w, r)}
        for start in starts:
            mapping, j = {}, max(0, start - span // 4)
            limit = min(len(plain), start + len(want) + span)
            for i, w in enumerate(want):
                k = j
                while k < limit and not _close(w, plain[k]):
                    k += 1
                if k < limit:
                    mapping[i] = k
                    j = k + 1
            # The hunk header's line just before the window counts as one more line found.
            score = len(mapping) + (1 if mapping and len(header) >= 8 and any(
                header in plain[k] for k in range(max(0, min(mapping.values()) - span), min(mapping.values()))) else 0)
            if mapping and (best is None or score > best[0]):
                best = (score, rank, min(mapping.values()), max(mapping.values()), mapping)
    return best


def align_patch(text, sources):
    """Rewrite each hunk of the apply_patch blocks in ``text`` so its context and removed
    lines are the archive's real lines. ``sources``: lists of raw lines, newest first.

    A hunk is rewritten only when at least half of its old lines (and two) are found in
    one window of one source; old lines found nowhere are dropped, real lines between two
    found ones become context, and added lines keep their place. Returns (text, stats).
    """
    stats = {'hunks': 0, 'aligned': 0, 'lines_fixed': 0}
    out, lines = [], text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith('@@') or not any(l.startswith('*** Update File:') for l in lines[max(0, i - 400):i]):
            out.append(line)
            i += 1
            continue
        out.append(line)
        i += 1
        body = []
        while i < len(lines) and not lines[i].startswith(('@@', '*** ')):
            body.append(lines[i])
            i += 1
        stats['hunks'] += 1
        old = [(k, b[1:]) for k, b in enumerate(body) if b[:1] in (' ', '-')]
        found = _best_window(old, sources, header=line.strip('@ ')) if old else None
        if not found or found[0] < max(2, (len(old) + 1) // 2):
            out += body
            continue
        _, rank, lo, hi, mapping = found
        rows = [_plain(r) for r in sources[rank]]
        new, row = [], lo
        for n, b in enumerate(body):
            if b[:1] == '+' or not b[:1] or b[:1] not in (' ', '-'):
                new.append(b)
                continue
            i_old = next(x for x, (k, _) in enumerate(old) if k == n)
            j = mapping.get(i_old)
            if j is None:
                stats['lines_fixed'] += 1  # Not in the file: dropped.
                continue
            while row < j:  # Real lines the hunk skipped are context.
                new.append(' ' + rows[row])
                row += 1
            if rows[j] != b[1:]:
                stats['lines_fixed'] += 1
            new.append(b[0] + rows[j])
            row = j + 1
        stats['aligned'] += 1
        out += new
    return '\n'.join(out), stats
