"""SpecPrefill setup (production plan, Phase 5): card settings, the student model, fast kernels.

- ``auto_settings``: the gateway's settings for the GPU it runs on, from a table of
  measured configurations; an unfit card (or none) gives a reason to run native.
- ``check_student`` / ``fetch_student``: the student model is a one-time download,
  only when the user confirms it, verified against pinned checksums (the files
  SpecPrefill was measured with). Never automatic.
- ``fast_kernels``: ``flash-linear-attention`` and ``causal_conv1d`` are used only
  if they import and the student's scores with them match the plain path.

Command line (``orchid-specprefill`` once installed): ``doctor``, ``fetch-student DIR``,
``serve`` (the gateway, with the settings file the installer wrote).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

STUDENT_REPO = 'Qwen/Qwen3.5-0.8B'
STUDENT_URL = 'https://huggingface.co/{repo}/resolve/main/{name}'
# sha256 and size of each file of the student SpecPrefill was measured with.
STUDENT_FILES = {
    'chat_template.jinja': ('273d8e0e683b885071fb17e08d71e5f2a5ddfb5309756181681de4f5a1822d80', 7755),
    'config.json': ('b90b86f35c8e6925ef74ee04d0e758f0a845c83a42089ad82bbaa948de9b4204', 2907),
    'merges.txt': ('a9d356d7bdf1ef4949e3e748e95b8e10ad9d4e2e838eddc38a0a7b6b94d1db8d', 3353259),
    'model.safetensors-00001-of-00001.safetensors':
        ('04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696', 1746942600),
    'model.safetensors.index.json': ('d8a08838a613b025eb7952ed9db11696213e57e76a375661ef5c12f9dd5dcf4e', 50900),
    'preprocessor_config.json': ('27225450ac9c6529872ee1924fcb0962ff5634834f817040f444118116f4e516', 390),
    'tokenizer_config.json': ('49e2b6e395f959f077f1e992b338919c0d4a9732fc6e613995e06557f843500c', 16709),
    'tokenizer.json': ('5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42', 12807982),
    'video_preprocessor_config.json': ('7768af27c1fafa9cc9011c1dc20067e03f8915e03b63504550e11d5066986d13', 385),
    'vocab.json': ('ce99b4cb2983d118806ce0a8b777a35b093e2000a503ebde25853284c9dfa003', 6722759),
}

# Measured configurations (docs/specprefill-goal.md, the production plan's Phase 4):
# (smallest total VRAM in MiB, card class, ORCHID_SPECPREFILL_* settings).
MEASURED = [
    (15500, '16 GB (measured on a Tesla P100: 256K, 1.94x the matched benchmark)', {
        'CONTEXT_TOKENS': '49152', 'STUDENT_CONTEXT_TOKENS': '262144', 'VRAM_RESERVE_MIB': '8192',
        'TARGET_KV': 'fp16', 'STUDENT_SPARSE_KEYS': '16384', 'STUDENT_SPARSE_ROWS': '8192',
    }),
]
MIN_COMPUTE = 6.0  # Pascal: what the student's PyTorch build and Strata's engine support.


def gpus(run=subprocess.run):
    """[(name, total MiB, compute capability)] of the NVIDIA GPUs, or [] when there are none."""
    try:
        out = run(['nvidia-smi', '--query-gpu=name,memory.total,compute_cap', '--format=csv,noheader,nounits'],
                  capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    cards = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(',')]
        try:
            cards.append((parts[0], int(float(parts[1])), float(parts[2])))
        except (IndexError, ValueError):
            continue
    return cards


def auto_settings(cards):
    """(settings, card class, reason to run native or None) for the first GPU."""
    if not cards:
        return {}, None, 'no NVIDIA GPU found'
    name, total, compute = cards[0]
    if compute < MIN_COMPUTE:
        return {}, None, f'{name} (compute {compute}) is older than the student needs (6.0)'
    for least, label, settings in MEASURED:
        if total >= least:
            return dict(settings), label, None
    return {}, None, (f'{name} has {total // 1024} GB; SpecPrefill is measured for 16 GB cards '
                      '(set the ORCHID_SPECPREFILL_* settings by hand to try it)')


def apply_auto(environ=os.environ, cards=None):
    """Fill the settings the user did not set from the measured table.

    Returns (card class, reason to run native or None). ORCHID_SPECPREFILL_AUTO=false
    leaves everything as set.
    """
    if environ.get('ORCHID_SPECPREFILL_AUTO', 'true').lower() in ('0', 'false', 'no'):
        return None, None
    settings, label, reason = auto_settings(gpus() if cards is None else cards)
    for key, value in settings.items():
        environ.setdefault(f'ORCHID_SPECPREFILL_{key}', value)
    return label, reason


def check_student(path, full=False):
    """None when the student at ``path`` is complete (``full``: and every checksum matches), else why not."""
    if not path:
        return 'no student model is set (ORCHID_SPECPREFILL_STUDENT)'
    root = Path(path)
    if not root.is_dir():
        return f'the student model is not at {root}'
    for name, (digest, size) in STUDENT_FILES.items():
        file = root / name
        if not file.is_file():
            return f'the student model at {root} is missing {name}'
        if file.stat().st_size != size:
            return f'the student model at {root} has a different {name}'
        if full and _sha256(file) != digest:
            return f'the student model at {root} has a different {name} (checksum)'
    return None


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 22), b''):
            h.update(block)
    return h.hexdigest()


def fetch_student(dest, *, confirm=input, source=STUDENT_URL, repo=STUDENT_REPO, out=print):
    """Download the student into ``dest`` after the user confirms; every file is checked
    against its pinned checksum before it is kept. Returns True when the model is ready."""
    dest = Path(dest)
    if check_student(dest, full=True) is None:
        out(f'The student model is already at {dest} (checksums match).')
        return True
    total = sum(size for _, size in STUDENT_FILES.values())
    out(f'SpecPrefill uses a small student model, {repo} ({total / 2**30:.1f} GB, Apache-2.0),\n'
        f'downloaded once from {source.format(repo=repo, name="...")} into {dest}.')
    if confirm('Download it now? [y/N] ').strip().lower() not in ('y', 'yes'):
        out('Not downloaded. SpecPrefill stays off (native Flash-Next) until a student is set.')
        return False
    dest.mkdir(parents=True, exist_ok=True)
    for name, (digest, size) in STUDENT_FILES.items():
        target = dest / name
        if target.is_file() and target.stat().st_size == size and _sha256(target) == digest:
            continue
        part = target.with_name(target.name + '.part')
        with urllib.request.urlopen(source.format(repo=repo, name=name), timeout=120) as r, open(part, 'wb') as f:
            shutil.copyfileobj(r, f, 1 << 22)
        if part.stat().st_size != size or _sha256(part) != digest:
            part.unlink()
            out(f'{name} does not match its checksum: not kept. Nothing was changed for SpecPrefill.')
            return False
        part.replace(target)
        out(f'  {name} ok')
    out(f'The student model is ready at {dest}.')
    return True


def fast_kernels(student_path=None):
    """(use them, why): the optional kernels import, and on this card the student's
    scores with them match the plain path."""
    missing = []
    for module in ('fla', 'causal_conv1d'):
        try:
            __import__(module)
        except Exception:
            missing.append(module)
    if missing:
        return False, f'not installed: {", ".join(missing)} (the plain path is used)'
    if not student_path:
        return False, 'installed, but no student to check them with'
    try:
        same = _kernel_equivalence(student_path)
    except Exception as error:  # A kernel that fails on this card is not used.
        return False, f'installed, but they fail on this card ({type(error).__name__}: {error})'
    return (True, 'installed and equivalent on this card') if same else (
        False, 'installed, but their scores differ from the plain path on this card')


def _kernel_equivalence(student_path, tolerance=1e-2):
    """The student's selection scores on a fixed input, with the fast kernels and without."""
    import importlib

    import torch
    text = 'def f(x):\n    return x + 1\n' * 64
    from transformers import AutoTokenizer
    ids = AutoTokenizer.from_pretrained(student_path, local_files_only=True)(text)['input_ids'][:1024]

    def scores(fast):
        modules = [m for name, m in sys.modules.items() if name.startswith('transformers.models.qwen3')]
        for module in modules:  # The model files pick their kernels when imported.
            for flag in ('is_fast_path_available', 'is_flash_linear_attention_available'):
                if hasattr(module, flag) and not fast:
                    setattr(module, flag, lambda: False)
            importlib.reload(module) if fast else None
        from .student import Student
        student = Student(student_path, None)
        out = student.trace(ids, 1.0)['scores']
        del student
        torch.cuda.empty_cache()
        return torch.tensor(out)
    a, b = scores(True), scores(False)
    return bool(torch.allclose(a, b, atol=tolerance, rtol=tolerance))


def doctor(out=print):
    """What the gateway would do on this machine, and why."""
    cards = gpus()
    settings, label, reason = auto_settings(cards)
    out(f'GPU: {", ".join(f"{n} ({m // 1024} GB, compute {c})" for n, m, c in cards) or "none"}')
    out(f'Settings: {label or "-"}' + (f' -> {settings}' if settings else ''))
    student = os.environ.get('ORCHID_SPECPREFILL_STUDENT')
    missing = check_student(student)
    out(f'Student: {student or "-"} ({missing or "complete"})')
    config = os.environ.get('ORCHID_SPECPREFILL_CONFIG')
    out(f'Flash-Next (Strata) config: {config or "-"} ({"found" if config and Path(config).is_file() else "missing"})')
    elastic = os.environ.get('ORCHID_SPECPREFILL_ELASTIC_ENGINE')
    out(f'Elastic engine: {elastic or "-"} (' + ('found' if elastic and Path(elastic).is_file() else
        'missing: agents run on plain SpecPrefill' if elastic else 'not set: agents run on plain SpecPrefill') + ')')
    use, why = fast_kernels(None if missing else student)
    out(f'Fast kernels: {why}')
    verdict = reason or missing or (None if config and Path(config).is_file() else 'no Strata config')
    out('SpecPrefill: ' + ('on' if verdict is None else f'off, native Flash-Next ({verdict})'))
    return verdict is None


ENV_FILE = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'orchid' / 'specprefill.env'


def load_env(path=None, environ=os.environ):
    """KEY=VALUE lines of the settings file into the environment (what is set already wins)."""
    path = Path(path or ENV_FILE)
    if path.is_file():
        for line in path.read_text().splitlines():
            key, sep, value = line.strip().partition('=')
            if sep and key and not key.startswith('#'):
                environ.setdefault(key.strip(), value.strip())
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='what the gateway would do on this machine')
    serve = sub.add_parser('serve', help='run the gateway (settings from ~/.config/orchid/specprefill.env)')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=18080)
    fetch = sub.add_parser('fetch-student', help='download the student model (asks first)')
    fetch.add_argument('dest', type=Path)
    fetch.add_argument('--yes', action='store_true', help='do not ask (the user already agreed)')
    args = parser.parse_args(argv)
    load_env()
    if args.command == 'doctor':
        return 0 if doctor() else 1
    if args.command == 'serve':
        import uvicorn
        uvicorn.run('memory_gateway.gateway:app', host=args.host, port=args.port, workers=1)
        return 0
    return 0 if fetch_student(args.dest, confirm=(lambda _: 'y') if args.yes else input) else 1


if __name__ == '__main__':
    sys.exit(main())
