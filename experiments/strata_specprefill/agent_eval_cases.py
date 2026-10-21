"""Phase 4 case: a real Codex session replayed with code-edit, long-output and whole-history probes.

The session is replayed in ~2.4K-token parts, like log_cases, but the agent's
patches enter the history in full. At chosen points just before a real patch,
the replay stops and asks for the next change; the patch the agent actually
applied is the reference, and it joins the history right after the probe.

  edit   "write the next change as a patch": recall of up to 20 distinctive added
         lines of the real patch (any number of files; 1,024 output tokens)
  long   "write the new file": recall of sampled lines of a 60+ line new file
         (3,072 output tokens)
  whole  "list every file you have edited so far": recall of the paths of all
         earlier patches (the question needs the whole history)

Scored with three_way_compaction.score(mode='lines'): each expected line or
path found in the reply (whitespace-normalized) is one correct field.
"""
from __future__ import annotations

import json
import random
import re

from .log_cases import SYSTEM, _BOILER, _CMD, _decode_wrapped, render

_PATCH = re.compile(r'apply_patch\(("(?:[^"\\]|\\.)*")')
_FILE = re.compile(r'^\*\*\* (Add|Update|Delete) File: (.+)$', re.M)
PATCH_TOKENS = 6000  # A patch longer than this enters the history cut.


def _patches(raw):
    for match in _PATCH.finditer(raw):
        try:
            yield json.loads(match.group(1))
        except ValueError:
            continue


def events(path):
    """(kind, text, extra) in session order; kind 'patch' carries the full patch text."""
    calls = {}
    for line in open(path):
        item = json.loads(line)
        if item.get('type') != 'response_item':
            continue
        p = item['payload']
        kind = p.get('type')
        if kind == 'message' and p.get('role') in ('user', 'assistant'):
            text = ''.join(c.get('text', '') for c in p.get('content', []) if isinstance(c, dict)).strip()
            if text and not text.lstrip().startswith(_BOILER):
                yield p['role'], text, None
        elif kind in ('custom_tool_call', 'function_call'):
            raw = p.get('input') or p.get('arguments') or ''
            raw = raw if isinstance(raw, str) else json.dumps(raw)
            patches = list(_patches(raw))
            if patches:
                for patch in patches:
                    yield 'patch', patch, None
                calls[p.get('call_id')] = 'apply_patch'
                continue
            match = _CMD.search(raw)
            command = (json.loads(f'"{match.group(1)}"') if match else raw)[:400].strip()
            calls[p.get('call_id')] = command
            yield 'call', command, command
        elif kind in ('custom_tool_call_output', 'function_call_output'):
            out = p.get('output')
            text = (''.join(c.get('text', '') for c in out if isinstance(c, dict))
                    if isinstance(out, list) else str(out or ''))
            yield 'output', _decode_wrapped(text)[:24000], calls.get(p.get('call_id'))


def _norm(line):
    return re.sub(r'\s+', ' ', line.strip())


def added_lines(patch):
    return [l[1:] for l in patch.splitlines() if l.startswith('+') and not l.startswith('+++')]


def distinctive(lines, limit):
    """Non-trivial lines (no blanks, brackets or bare keywords), first occurrence only."""
    seen, out = set(), []
    for line in lines:
        n = _norm(line)
        if len(n) < 12 or n in seen or sum(ch.isalnum() for ch in n) < 8:
            continue
        seen.add(n)
        out.append(n)
    return out[:limit]


def build_agent_eval_case(tok, path, seed, *, budget=200000, turn_tokens=2400, edits=12, longs=4, wholes=4):
    rng = random.Random(seed)
    stream, total = [], 0  # (piece, tokens, patch-or-None)
    for kind, text, _ in events(path):
        if kind == 'patch':
            ids = tok.encode(text)
            shown = text if len(ids) <= PATCH_TOKENS else tok.decode(ids[:PATCH_TOKENS]) + '\n…[patch cut]\n'
            piece, patch = f'$ apply_patch\n{shown}\n', text
        else:
            piece, patch = render(kind, text), None
        size = len(tok.encode(piece))
        if total + size > budget:
            if budget - total < 512:
                break
            continue
        stream.append((piece, size, patch))
        total += size
    # Probe candidates: patches by size, spread over the whole depth.
    edit_at, long_at = [], []
    for i, (_, _, patch) in enumerate(stream):
        if not patch:
            continue
        lines = distinctive(added_lines(patch), 40)
        files = [f for _, f in _FILE.findall(patch)]
        if 'Add File' in patch and len(added_lines(patch)) >= 60 and len(files) == 1 and len(lines) >= 20:
            long_at.append(i)
        elif 3 <= len(lines) and files:
            edit_at.append(i)  # Single- or multi-file edits (an agent's usual change).

    def spread(points, count):
        if len(points) <= count:
            return set(points)
        step = len(points) / count
        return {points[int(step * k + rng.random() * step)] for k in range(count)}
    probes = {i: 'edit' for i in spread(edit_at, edits)}
    probes.update({i: 'long' for i in spread(long_at, longs) if i not in probes})
    whole_at = {int(len(stream) * k / wholes) - 1 for k in range(1, wholes + 1)}
    turns, current, current_tokens, depth, edited = [], [], 0, 0, []

    def flush(question, kind='filler', expected=('OK',), max_tokens=64, score='whole'):
        nonlocal current, current_tokens
        source = {'path': f'session/part-{len(turns):03d}', 'version': 1, 'text': ''.join(current)} if current else None
        turns.append({'source': source, 'question': question, 'expected': list(expected), 'score': score,
                      'kind': kind, 'depth': depth, 'max_tokens': max_tokens})
        current, current_tokens = [], 0

    for i, (piece, size, patch) in enumerate(stream):
        if i in probes:
            files = [f for _, f in _FILE.findall(patch)]
            if probes[i] == 'edit':
                flush('Make the next code change for the task above. Reply with only the patch, in the '
                      'apply_patch format (*** Begin Patch / *** Update File: ... / *** End Patch).',
                      'edit', distinctive(added_lines(patch), 20), 1024, 'lines')
            else:
                flush(f'Write the complete new file {files[0]} for the task above. Reply with only the file '
                      'content.', 'long', rng.sample(distinctive(added_lines(patch), 200), 20), 3072, 'lines')
        if current and current_tokens + size > turn_tokens:
            flush('Reply with the word OK.')
        current.append(piece)
        current_tokens += size
        depth += size
        if patch:
            edited += [f for _, f in _FILE.findall(patch) if f not in edited]
        if i in whole_at and edited:
            flush('List the path of every file you have created or modified with apply_patch so far in this '
                  'session, one path per line, nothing else.', 'whole', list(edited), 2048, 'lines')
    if current:
        flush('Reply with the word OK.')
    return {'system': SYSTEM, 'seed': seed, 'generator': 'agent_eval_cases.v1', 'log': str(path),
            'turns': turns, 'source_tokens': total,
            'probes': {k: sum(t['kind'] == k for t in turns) for k in ('edit', 'long', 'whole')}}
