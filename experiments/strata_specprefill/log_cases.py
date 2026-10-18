"""Long-session benchmark built from a real Codex rollout (the user's own logs).

The session is replayed in order (user messages, assistant replies, tool
commands and their outputs) and grouped into ~2.4K-token turns until the
budget is reached. Each turn ends with an automatically generated probe about
an exact line printed in a tool output: the question quotes the line's exact
beginning and asks for the complete line. Every occurrence of that beginning
in the replayed transcript is followed by the same ending, so the answer is
unambiguous. (Asking only for "the rest of the line" made models truncate and
then imitate their own ever-shorter replies, natively too.) Probes ask about the newest chunk, every
`recall_every` turns about a chunk >= 20 turns back, and finally a block of late
probes balanced over depth bins (first 50K tokens, middle, last third).

Secret-looking pairs (names containing key/token/secret/password/auth/cookie,
or long random-looking values) are never used. The case stays local.
"""
from __future__ import annotations

import json
import random
import re

SYSTEM = ('You are reviewing a recorded coding-agent session: user requests, assistant replies, '
          'shell commands and their outputs. Answer from the session. Reply with only the exact '
          'text asked for, with no quotes, code fences or explanation. If it is not available reply '
          'UNKNOWN; never guess.')

_BOILER = ('<environment_context', '# AGENTS', '<permissions', '<codex_internal', '<user_instructions',
           '<external_codex_apps', '<turn_aborted')
_PAIR = re.compile(r'''^\s*["']?([A-Za-z_][\w.\-/]{2,40})["']?\s*[:=]\s*["']?([^\s,`'"()\[\]{}]{1,32})["']?\s*,?\s*$''')
_SECRET = re.compile(r'key|token|secret|passw|auth|cookie|bearer|session_id|credential', re.I)
_CMD = re.compile(r'cmd:\s*"((?:[^"\\]|\\.)*)"')


def _decode_wrapped(text):
    """Parallel tool runs are logged as JSON lines with the real output escaped
    inside an "output" string; return readable text instead."""
    found = []

    def collect(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == 'output' and isinstance(value, str):
                    found.append(value)
                else:
                    collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    kept = []
    for line in text.splitlines():
        if line.startswith('{') and '"output"' in line:
            try:
                collect(json.loads(line))
                continue
            except json.JSONDecodeError:
                pass
        kept.append(line)
    return '\n'.join(kept + found) if found else text


def events(path):
    """(kind, text, command) in session order."""
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
            match = _CMD.search(raw)
            command = (json.loads(f'"{match.group(1)}"') if match else raw)[:400].strip()
            calls[p.get('call_id')] = command
            yield 'call', command, command
        elif kind in ('custom_tool_call_output', 'function_call_output'):
            out = p.get('output')
            text = (''.join(c.get('text', '') for c in out if isinstance(c, dict))
                    if isinstance(out, list) else str(out or ''))
            yield 'output', _decode_wrapped(text)[:24000], calls.get(p.get('call_id'))


def render(kind, text):
    return {'user': f'USER:\n{text}\n', 'assistant': f'ASSISTANT:\n{text}\n',
            'call': f'$ {text}\n', 'output': f'{text}\n'}[kind]


def candidate_pairs(text):
    for line in text.splitlines():
        m = _PAIR.match(line)
        if not m:
            continue
        key, value = m.group(1).strip(), m.group(2).strip().rstrip('.')
        digits = sum(ch.isdigit() for ch in value)
        if (_SECRET.search(key) or digits < 2 or len(value) < 2
                or (len(value) >= 16 and re.fullmatch(r'[A-Za-z0-9+/=_\-]+', value))):
            continue
        yield key, value, line.strip()


def completion_lines(text):
    """(prefix, rest, line) splits of distinctive lines: rest is short and exact."""
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not 30 <= len(stripped) <= 140 or _SECRET.search(line):
            continue
        if re.search(r'[A-Za-z0-9+=]{24,}', line):  # hashes, ids, base64 blobs (paths are fine)
            continue
        cuts = [m.start() for m in re.finditer(r'[\s(=:,]', line) if len(line) * 0.55 <= m.start() <= len(line) - 4]
        if not cuts:
            continue
        cut = cuts[0] + 1
        prefix, rest = line[:cut], line[cut:].strip()
        if (4 <= len(rest) <= 40 and sum(ch.isalnum() for ch in rest) >= 3
                and len(prefix.strip()) >= 18 and sum(ch.isalpha() for ch in prefix) >= 8):
            yield prefix, rest, line


def build_log_case(tok, path, seed, *, budget=252000, turn_tokens=2400, recall_every=10, late_per_bin=4):
    rng = random.Random(seed)
    chunks, current, current_tokens, facts = [], [], 0, []
    total = 0
    for kind, text, command in events(path):
        piece = render(kind, text)
        size = len(tok.encode(piece))
        if total + size > budget:
            if budget - total < 512:
                break
            continue  # Skip one oversized event; later ones may still fit.
        if current and current_tokens + size > turn_tokens:
            chunks.append({'text': ''.join(current), 'start': total - current_tokens})
            current, current_tokens = [], 0
        current.append(piece)
        current_tokens += size
        total += size
        if kind == 'output' and command:
            for prefix, rest, line in completion_lines(text):
                facts.append({'chunk': len(chunks), 'prefix': prefix, 'rest': rest, 'line': line,
                              'command': command.splitlines()[0][:90]})
    if current:
        chunks.append({'text': ''.join(current), 'start': total - current_tokens})
    transcript = ''.join(c['text'] for c in chunks)
    # Unambiguous probes only: every occurrence of the quoted beginning in the
    # whole replay is followed by the same ending (re-read files are fine).
    seen, usable = set(), []
    for f in facts:
        if f['prefix'] in seen:
            continue
        seen.add(f['prefix'])
        hits = transcript.count(f['prefix'])
        if hits and hits == transcript.count(f['line']):
            usable.append(f)
    by_chunk = {}
    for f in usable:
        by_chunk.setdefault(f['chunk'], []).append(f)
    used = set()

    def probe(fact, kind):
        used.add(id(fact))
        return {'question': (f'In the output of the command `{fact["command"]}` earlier in this session, '
                             f'quote the complete line that starts with: {fact["prefix"].strip()}\n'
                             'Reply with that one line only, exactly as printed.'),
                'expected': [fact['line'].strip()], 'score': 'whole', 'kind': kind,
                'depth': chunks[fact['chunk']]['start']}

    turns = []
    for i, chunk in enumerate(chunks):
        own = [f for f in by_chunk.get(i, []) if id(f) not in used]
        older = [f for c in range(0, max(0, i - 20)) for f in by_chunk.get(c, []) if id(f) not in used]
        if (i + 1) % recall_every == 0 and older:
            q = probe(rng.choice(older), 'recall')
        elif own:
            q = probe(rng.choice(own), 'current')
        else:
            back = [f for c in range(max(0, i - 10), i) for f in by_chunk.get(c, []) if id(f) not in used]
            q = probe(rng.choice(back), 'current') if back else {
                'question': 'Reply with the word OK.', 'expected': ['OK'], 'score': 'whole', 'kind': 'filler',
                'depth': chunk['start']}
        turns.append({'source': {'path': f'session/part-{i:03d}', 'version': 1, 'text': chunk['text']}, **q})
    bins = ([f for f in usable if chunks[f['chunk']]['start'] < 50000],
            [f for f in usable if 50000 <= chunks[f['chunk']]['start'] < total * 2 / 3],
            [f for f in usable if chunks[f['chunk']]['start'] >= total * 2 / 3])
    for group in bins:
        group = [f for f in group if id(f) not in used]
        for fact in rng.sample(group, min(late_per_bin, len(group))):
            turns.append({'source': None, **probe(fact, 'late')})
    return {'system': SYSTEM, 'seed': seed, 'generator': 'log_cases.v1', 'log': str(path),
            'turns': turns, 'source_tokens': total, 'usable_facts': len(usable)}
