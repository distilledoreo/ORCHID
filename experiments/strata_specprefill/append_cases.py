"""Freeze previous incoming texts and fresh recovery/version probes before inference."""
import hashlib
import json
import random
import re


def prepare_cases(tok, prior, output):
    if output.exists():
        raise ValueError('Refusing to overwrite frozen cases')
    cases = []
    old = json.loads(prior.read_text())
    for ses in old:
        turns = []
        for t in ses['turns']:
            raw = tok.decode(t['input_ids'])
            content = raw.rsplit('<|im_start|>user\n', 1)[1].split('<|im_end|>', 1)[0]
            m = re.search(r'\n(What is the access code.*)$', content, re.S)
            if not m:
                raise ValueError('Frozen user message format changed')
            turns.append({'sources': [{'path': f'frozen/{ses["id"]}/turn{t["turn"]}.txt',
                                       'version': 1, 'text': content[:m.start()]}],
                          'question': m[1], 'answer': t['answer'],
                          'evidence': {'path': f'frozen/{ses["id"]}/turn0.txt', 'version': 1,
                                       'needle': t['answer']}})
        cases.append({'id': ses['id'], 'split': 'frozen', 'turns': turns})
    rng = random.Random(92861403)  # Fresh seed; no tuning using inference outcomes.

    def filler(label, tokens):
        lines = ''
        i = 0
        while len(tok.encode(lines)) < tokens:
            lines += f'Log {label}-{i}: green triangle, blue square, routine operation completed.\n'
            i += 1
        return lines

    for kind in ('recovery', 'versions'):
        path = f'src/{kind}/session.py'
        nonce = ''.join(rng.choice('abcdefghjkmnpqrstuvwxyz') for _ in range(6))
        names = [f'{nonce}_value_{i}' for i in range(80)]
        values = rng.sample(range(10000, 99999), 81)
        source = '\n\n'.join(f'def {name}():\n    return {value}' for name,value in zip(names,values))
        source += '\n\n'+filler(kind+'initial', max(0, 2048-len(tok.encode(source))))
        turns = []
        for i, growth in enumerate((0, 500, 1000, 2000)):
            if i == 0:
                sources = [{'path': path, 'version': 1, 'text': source}]
                index, version, answer = 0, 1, values[0]
            elif kind == 'versions' and i == 1:
                replacement = (f'# Complete file revision 2. Revision 1 is superseded.\n'
                               f'def {names[37]}():\n    return {values[80]}\n'+filler('revision2', growth))
                sources = [{'path': path, 'version': 2, 'text': replacement}]
                index, version, answer = 37, 2, values[80]
            else:
                sources = [{'path': f'logs/{kind}/turn{i}.txt', 'version': 1,
                            'text': filler(kind+str(i), growth)}]
                if kind == 'versions':
                    index, version, answer = (37, 1, values[37]) if i == 2 else (37, 2, values[80])
                else:
                    index, version = (37 if i in (1, 3) else 71), 1
                    answer = values[index]
            question = (f'In {path}, explicitly use version {version}. What does {names[index]}() return? '
                        'Reply with only its five-digit value.')
            turns.append({'sources': sources, 'question': question, 'answer': str(answer),
                          'evidence': {'path': path, 'version': version, 'needle': str(answer)}})
        cases.append({'id': 'held-'+kind, 'split': 'held', 'turns': turns})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'schema': 1, 'seed': 92861403,
                                 'prior_sha256': hashlib.sha256(prior.read_bytes()).hexdigest(),
                                 'cases': cases}, indent=2)+'\n')
    return cases
