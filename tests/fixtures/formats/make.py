"""Write one lean gateway state directory per released format, with that release's code.

Each release (a git commit) runs in a temporary worktree with its own test fakes and
writes a thread of two turns; the state directory is kept as a fixture so
test_specprefill_formats.py can check the current code reads and continues it.

    python tests/fixtures/formats/make.py   (from the repository root; needs git)
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RELEASES = {  # fixture name: (commit, how its gateway was driven)
    'v0-pre-plan': ('4cd92bc', 'headers'),
    'v1-phase1': ('0cc57a9', 'auto'),
    'v2-phase2': ('71fc509', 'auto'),
    'v3-phase3': ('fa28468', 'auto'),
    'v4-phase4': ('d8c89fc', 'auto'),
}
DRIVER = r'''
import sys
from pathlib import Path
sys.path[:0] = [sys.argv[1], sys.argv[1] + '/tests']
out, mode = Path(sys.argv[2]), sys.argv[3]
from test_spec_session import doc
TOOL = doc({'KEYA': '11111', 'KEYB': '22222', 'KEYC': '33333'})
if mode == 'headers':
    from test_specprefill_gateway_lean import Resources, options
    from memory_gateway.specprefill.service import SpecPrefillService
    resources = Resources()
    s = SpecPrefillService(options(out), resources_factory=lambda: resources)
    first = {'messages': [{'role': 'system', 'content': 'Use excerpts.'}, {'role': 'user', 'content': TOOL + '\n\nReturn KEYA'}]}
    a = s.complete('proj', 'thr', 'r1', first)
    second = {'messages': first['messages'] + [{'role': 'assistant', 'content': a['choices'][0]['message']['content']},
                                               {'role': 'user', 'content': 'Return KEYB'}]}
    s.complete('proj', 'thr', 'r2', second)
else:
    from test_specprefill_gateway_auto import chat, reply, service
    s, _ = service(out)
    a = s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA')))
    s.complete(None, None, None, chat(('user', TOOL + '\n\nReturn KEYA'), ('assistant', reply(a)), ('user', 'Return KEYB')))
s.close()
'''


def main():
    repo = Path(__file__).resolve().parents[3]
    here = Path(__file__).parent
    python = sys.executable
    for name, (commit, mode) in RELEASES.items():
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / 'tree'
            subprocess.run(['git', 'worktree', 'add', '--detach', str(tree), commit], cwd=repo, check=True,
                           capture_output=True)
            try:
                out = Path(tmp) / 'out'
                out.mkdir()
                subprocess.run([python, '-c', DRIVER, str(tree), str(out), mode], check=True, cwd=tree)
                target = here / name
                shutil.rmtree(target, ignore_errors=True)
                # The state directory (gateway db, thread checkpoints); not the scratch hot tier.
                shutil.copytree(out / 'native', target, ignore=shutil.ignore_patterns('*.bin', 'shm', 'student-cache',
                                                                                       'target-cache', 'owner.lock'))
                print(name, commit, sorted(p.name for p in target.iterdir()))
            finally:
                subprocess.run(['git', 'worktree', 'remove', '--force', str(tree)], cwd=repo, capture_output=True)


if __name__ == '__main__':
    main()
