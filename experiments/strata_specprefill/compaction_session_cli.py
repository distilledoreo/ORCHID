"""Run an explicitly supplied source/answer/compact script on the local models.

Uses a private engine configuration; never changes a running service. This is an
integration harness, not a latency benchmark or the production gateway route.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .cached_student import CachedStudent
from .compacting_session import CompactingSession, Limits
from .compaction_adapters import AdaptiveSelector, NativeSummarizer, NativeTarget
from .core import compatible_ids
from .run import load_frontend
from .student import Student


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--student', type=Path, required=True)
    parser.add_argument('--script', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--context', type=int, default=8192)
    parser.add_argument('--compact-tokens', type=int, default=3072)
    parser.add_argument('--summary-tokens', type=int, default=1024)
    parser.add_argument('--expansion-tokens', type=int, default=2048)
    parser.add_argument('--archive-cache-mib', type=int, default=8)
    parser.add_argument('--retrieval-mode', choices=('indexed', 'indexed-scan', 'scan', 'bounded'), default='indexed')
    parser.add_argument('--embedding-model', type=Path)
    parser.add_argument('--embedding-tokenizer', type=Path)
    args = parser.parse_args()
    if args.archive_cache_mib < 0:
        parser.error('--archive-cache-mib must be nonnegative')
    if bool(args.embedding_model) != bool(args.embedding_tokenizer):
        parser.error('Provide both --embedding-model and --embedding-tokenizer')
    embedder, embedding_id = None, None
    if args.embedding_model:
        from memory_gateway.dense_experiment import OnnxTextEmbedder, sha256_file
        embedder = OnnxTextEmbedder(model_path=args.embedding_model,
            tokenizer_path=args.embedding_tokenizer, max_length=256, providers=['CPUExecutionProvider'])
        embedding_id = ('onnx-mean-l2-256-v1:' + sha256_file(args.embedding_model)
                        + ':' + sha256_file(args.embedding_tokenizer))
    script = json.loads(args.script.read_text())
    if not isinstance(script.get('system'), str) or not isinstance(script.get('actions'), list):
        parser.error('Script needs system text and an actions array')
    for action in script['actions']:
        if action.get('kind') not in ('source', 'answer', 'compact'):
            parser.error('Only source, answer, and compact actions are supported')
    limits = Limits(student_context=args.context, target_context=args.context,
                    compact_tokens=args.compact_tokens, summary_tokens=args.summary_tokens,
                    expansion_tokens=args.expansion_tokens)
    cfg = json.loads(args.config.read_text())
    root = Path(cfg['cwd'])
    tok, _, vocab = load_frontend(root, Path(cfg['tokenizer']))
    allowed = compatible_ids(vocab, json.loads((args.student / 'tokenizer.json').read_text()))
    engine_args = list(cfg['args'])
    # This harness uses sequential positions and ordinary native prefix caching.
    for flag in ('--selected-prefill', '--selected-prefix-cache'):
        engine_args = [a for a in engine_args if a != flag]
    for flag, value in (('--max-context', args.context), ('--kv-resident', args.context),
                        ('--vram-reserve-mib', 4096), ('--prompt-cache', 6),
                        ('--conversation-cache-mib', 0), ('--turn-token', 248045)):
        if flag in engine_args:
            engine_args[engine_args.index(flag) + 1] = str(value)
        else:
            engine_args.extend((flag, str(value)))
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'script.json').write_text(json.dumps(script, ensure_ascii=False, indent=2))
    (args.output / 'engine-config.json').write_text(json.dumps({**cfg, 'args': engine_args}, indent=2))
    sys.path[:0] = [str(root), str(root / 'tools')]
    from serve.server import StrataEngine, child_env
    env = child_env(cfg)
    env.update(STRATA_ROUTE_TAIL_SKIP='0', STRATA_PREFILL_CPU_SHARE='0')
    engine = StrataEngine(cfg['exe'], engine_args, cwd=cfg['cwd'],
                          log=str(args.output / 'engine.log'), env=env)
    try:
        student = CachedStudent(Student(args.student, allowed), block=1024)
        target = NativeTarget(engine)
        session = CompactingSession(tok, AdaptiveSelector(student), target,
            NativeSummarizer(target, tok, context_limit=args.context), system=script['system'],
            limits=limits, checkpoint=args.output / 'session.json', retrieval_mode=args.retrieval_mode,
            archive_cache_bytes=args.archive_cache_mib * 1024**2,
            embedder=embedder, embedding_id=embedding_id)
        with (args.output / 'actions.jsonl').open('w') as output:
            for i, action in enumerate(script['actions']):
                if action['kind'] == 'source':
                    result = {'source': session.add_source(action['path'], action['version'],
                              action['text'], cue=action['cue'])}
                elif action['kind'] == 'compact':
                    result = session.compact(action['question'])
                else:
                    ids = session.answer(action['question'])
                    result = {'output_ids': ids, 'text': tok.decode(ids), **session.events[-1]}
                record = {'action': i, 'kind': action['kind'], 'result': result,
                          'target_engine': target.last['engine'] if target.last else None}
                output.write(json.dumps(record, ensure_ascii=False) + '\n')
                output.flush()
                print(json.dumps({'action': i, 'kind': action['kind'], 'generation': session.generation}), flush=True)
    finally:
        if 'session' in locals():
            session.close()
        engine.close()


if __name__ == '__main__':
    main()
