"""Held-out coding-agent workload for the SpecPrefill goal gates.

Independent of the 44-field development case: different generator, system
prompt and question phrasing, no ROUTE_KEY syntax, fresh seeds. Each source
turn is a large tool result (a whole Python module read, or a module after an
edit); questions ask for exact values the answer depends on:

- current: a constant or return value in the module just read;
- multihop: a function in this module calls a helper imported from an earlier
  module; return the helper's constant;
- revision: after an edit, ask for both the new and the pre-edit value;
- late: recall-only turns at the end asking about details never asked before.

Never inspect results of a held-out seed while tuning; use --seed for dev.
"""
from __future__ import annotations

import random

SYSTEM = ('You are a coding assistant working in a Python repository. Answer from the tool outputs '
          'in this conversation. Each question asks for exact integers: reply with only those '
          'integers, in the order asked, separated by a comma. If a value is not available reply '
          'UNKNOWN for it; never guess.')

WORDS = ('billing ledger router cache session quota invoice shipment catalog audit token webhook '
         'scheduler parser metrics tenant export import report backup mailer search pricing '
         'inventory checkout refund payout gateway').split()
VERBS = ('load save merge split check build apply scale clamp parse render flush retry sync '
         'collect resolve').split()


def _module(rng, name, helpers, version, base=None):
    """A realistic module; returns text plus its named integer facts."""
    facts = dict(base or {})
    lines = [f'"""{name.title()} service module (revision {version})."""',
             'from __future__ import annotations', '', 'import logging', 'import math', '']
    for mod, fn in helpers:
        lines.append(f'from .{mod} import {fn}')
    lines += ['', 'log = logging.getLogger(__name__)', '']
    consts = [f'{name.upper()}_{w.upper()}' for w in rng.sample(WORDS, 6)]
    for c in consts:
        if c not in facts or rng.random() < 0.5:
            facts[c] = rng.randrange(1000, 99999)
        lines.append(f'{c} = {facts[c]}')
    lines.append('')
    for i in range(24):
        verb, noun = rng.choice(VERBS), rng.choice(WORDS)
        fn = f'{verb}_{noun}_{i}'
        key = f'{name}.{fn}'
        if key not in facts or rng.random() < 0.3:
            facts[key] = rng.randrange(100, 9999)
        body = [f'def {fn}(items, limit={rng.randrange(2, 64)}):',
                f'    """{verb.title()} {noun} records for the {name} pipeline."""',
                '    total = 0',
                '    for index, item in enumerate(items):',
                '        if index >= limit or item is None:',
                '            break',
                f'        total += int(item) % {rng.randrange(3, 97)}',
                f'    log.debug("{fn} processed %d items", total)']
        if helpers and i % 4 == 1:
            mod, helper = helpers[(i // 4) % len(helpers)]
            body.append(f'    total += {helper}()')
            facts[f'{key}->'] = (mod, helper)
        body += [f'    return {facts[key]}', '']
        lines += body
    return '\n'.join(lines) + '\n', facts


def build_agent_case(tok, seed, *, budget=26000):
    rng = random.Random(seed)
    names = rng.sample(WORDS, 14)
    modules, turns, used = [], [], set()
    total = 0

    def pick(facts, kinds):
        options = [k for k in facts if k not in used and not k.endswith('->')
                   and (('.' in k) == (kinds == 'fn'))]
        key = rng.choice(options)
        used.add(key)
        return key

    def ask(mod, key):
        if '.' in key:
            return f'the integer returned by {key.split(".", 1)[1]}() in {mod["path"]}'
        return f'the value of {key} in {mod["path"]}'

    for i, name in enumerate(names):
        helpers = []
        for m in modules[-3:]:
            fns = [k.split('.', 1)[1] for k in m['facts'] if k.startswith(m['name'] + '.') and '->' not in k]
            helpers.append((m['name'], rng.choice(fns)))
        helpers = helpers[:2]
        text, facts = _module(rng, name, helpers, 1)
        path = f'app/{name}.py'
        mod = {'name': name, 'path': path, 'facts': facts, 'version': 1, 'text': text}
        modules.append(mod)
        tool = f'$ cat {path}\n{text}'
        size = len(tok.encode(tool))
        if total + size > budget:
            modules.pop()
            break
        total += size
        a = pick(facts, 'const')
        hop = [k for k in facts if k.endswith('->')]
        if hop and i % 2:
            k = rng.choice(hop)
            src_mod, helper = facts[k]
            src = next(m for m in modules if m['name'] == src_mod)
            question = (f'Return {ask(mod, a)}; then {k[:-2].split(".", 1)[1]}() in {path} calls '
                        f'{helper}() from app/{src_mod}.py: return the integer {helper}() returns.')
            expected = [facts[a], src['facts'][f'{src_mod}.{helper}']]
            used.add(f'{src_mod}.{helper}')
            kind = 'multihop'
        else:
            b = pick(facts, 'fn')
            question = f'Return {ask(mod, a)}, then {ask(mod, b)}.'
            expected = [facts[a], facts[b]]
            kind = 'current'
        turns.append({'source': {'path': path, 'version': 1, 'text': tool},
                      'question': question, 'expected': expected, 'kind': kind})
        if i >= 2 and i % 3 == 2:
            old = modules[i - 2]
            text2, facts2 = old['text'], dict(old['facts'])
            consts = [k for k in facts2 if '.' not in k and not k.endswith('->')]
            edits = rng.sample(consts, 3)
            for k in edits:
                value = rng.randrange(1000, 99999)
                text2 = text2.replace(f'{k} = {facts2[k]}\n', f'{k} = {value}\n', 1)
                facts2[k] = value
            text2 = text2.replace('(revision 1)', '(revision 2)', 1)
            changed = [k for k in edits if k not in used]
            edit = f'$ apply_patch {old["path"]} && cat {old["path"]}\n{text2}'
            size = len(tok.encode(edit))
            if changed and total + size <= budget:
                total += size
                k = rng.choice(changed)
                used.add(k)
                turns.append({'source': {'path': old['path'], 'version': 2, 'text': edit},
                              'question': (f'After this edit, return the value of {k} in {old["path"]}, '
                                           f'then its value before the edit.'),
                              'expected': [facts2[k], old['facts'][k]], 'kind': 'revision'})
                old['facts_v2'] = facts2
    for _ in range(4):
        m1, m2 = rng.sample(modules, 2)
        k1, k2 = pick(m1.get('facts_v2', m1['facts']), 'const'), pick(m2['facts'], 'fn')
        if 'facts_v2' in m2 and m2['facts_v2'].get(k2) != m2['facts'][k2]:
            continue
        turns.append({'source': None,
                      'question': (f'Return {ask(m1, k1)} (current revision), then {ask(m2, k2)}.'),
                      'expected': [m1.get('facts_v2', m1['facts'])[k1], m2['facts'][k2]],
                      'kind': 'late'})
    return {'system': SYSTEM, 'seed': seed, 'generator': 'agent_cases.v1', 'turns': turns}


def build_long_case(tok, seed, *, budget=252000, recall_every=10, late_per_bin=4):
    """A >=250K-token session for the long-context gates.

    Same turn types as build_agent_case, plus a recall turn every
    `recall_every` file reads about a file read at least 20 files earlier, and
    a closing block of late questions balanced over three depth bins: the
    first 50K tokens, the middle, and the last third. Each recall/late turn
    records the depth (source-token offset) of the file it asks about.
    """
    rng = random.Random(seed)
    names = [f'{a}_{b}' for a in WORDS for b in WORDS if a != b]
    rng.shuffle(names)
    modules, turns, used = [], [], set()
    total = 0

    def pick(facts, kind):
        options = [k for k in facts if k not in used and not k.endswith('->')
                   and (('.' in k) == (kind == 'fn'))]
        key = rng.choice(options)
        used.add(key)
        return key

    def ask(mod, key):
        if '.' in key:
            return f'the integer returned by {key.split(".", 1)[1]}() in {mod["path"]}'
        return f'the value of {key} in {mod["path"]}'

    def recall(mod, kind):
        current = mod.get('facts_v2', mod['facts'])
        c, f = pick(current, 'const'), pick(mod['facts'], 'fn')
        return {'source': None, 'kind': kind, 'depth': mod['start'],
                'question': f'Return {ask(mod, c)} (current revision), then {ask(mod, f)}.',
                'expected': [current[c], mod['facts'][f]]}

    reads = 0
    for i, name in enumerate(names):
        helpers = []
        for m in modules[-3:]:
            fns = [k.split('.', 1)[1] for k in m['facts'] if k.startswith(m['name'] + '.') and '->' not in k]
            helpers.append((m['name'], rng.choice(fns)))
        text, facts = _module(rng, name, helpers[:2], 1)
        path = f'app/{name}.py'
        tool = f'$ cat {path}\n{text}'
        size = len(tok.encode(tool))
        if total + size > budget:
            break
        mod = {'name': name, 'path': path, 'facts': facts, 'text': text, 'start': total}
        modules.append(mod)
        total += size
        a = pick(facts, 'const')
        hop = [k for k in facts if k.endswith('->')]
        if hop and i % 2:
            k = rng.choice(hop)
            src_mod, helper = facts[k]
            src = next(m for m in modules if m['name'] == src_mod)
            question = (f'Return {ask(mod, a)}; then {k[:-2].split(".", 1)[1]}() in {path} calls '
                        f'{helper}() from app/{src_mod}.py: return the integer {helper}() returns.')
            expected, kind = [facts[a], src['facts'][f'{src_mod}.{helper}']], 'multihop'
            used.add(f'{src_mod}.{helper}')
        else:
            b = pick(facts, 'fn')
            question, expected, kind = f'Return {ask(mod, a)}, then {ask(mod, b)}.', [facts[a], facts[b]], 'current'
        turns.append({'source': {'path': path, 'version': 1, 'text': tool},
                      'question': question, 'expected': expected, 'kind': kind})
        reads += 1
        if i >= 2 and i % 3 == 2:
            old = modules[-3]
            text2, facts2 = old['text'], dict(old['facts'])
            consts = [k for k in facts2 if '.' not in k and not k.endswith('->')]
            edits = rng.sample(consts, 3)
            for k in edits:
                value = rng.randrange(1000, 99999)
                text2 = text2.replace(f'{k} = {facts2[k]}\n', f'{k} = {value}\n', 1)
                facts2[k] = value
            text2 = text2.replace('(revision 1)', '(revision 2)', 1)
            changed = [k for k in edits if k not in used]
            edit = f'$ apply_patch {old["path"]} && cat {old["path"]}\n{text2}'
            size = len(tok.encode(edit))
            if changed and 'facts_v2' not in old and total + size <= budget:
                total += size
                k = rng.choice(changed)
                used.add(k)
                turns.append({'source': {'path': old['path'], 'version': 2, 'text': edit},
                              'question': (f'After this edit, return the value of {k} in {old["path"]}, '
                                           f'then its value before the edit.'),
                              'expected': [facts2[k], old['facts'][k]], 'kind': 'revision'})
                old['facts_v2'] = facts2
        if reads % recall_every == 0 and len(modules) > 20:
            turns.append(recall(rng.choice(modules[:-20]), 'recall'))
    bins = ([m for m in modules if m['start'] < 50000],
            [m for m in modules if 50000 <= m['start'] < total * 2 / 3],
            [m for m in modules if m['start'] >= total * 2 / 3])
    for group in bins:
        for mod in rng.sample(group, min(late_per_bin, len(group))):
            turns.append(recall(mod, 'late'))
    return {'system': SYSTEM, 'seed': seed, 'generator': 'agent_cases.long.v1', 'turns': turns,
            'source_tokens': total}
