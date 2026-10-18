"""Lazy local-model resources for the gateway. Never touches a running service."""
import json
import sys
import threading
from pathlib import Path

from .cached_student import CachedStudent
from .compacting_session import CompactingSession
from .compaction_adapters import AdaptiveSelector, NativeSummarizer, NativeTarget
from .core import compatible_ids
from .student import Student


class NativeResources:
    def __init__(self, config_path, student_path, limits, log_path, *, archive_cache_bytes=8 * 1024**2,
                 lean=False, vram_reserve_mib=4096, sparse_keys=None, sparse_rows=None, kv=None):
        self.archive_cache_bytes = archive_cache_bytes
        self.lean = lean
        cfg = json.loads(Path(config_path).read_text())
        root = Path(cfg['cwd'])
        sys.path[:0] = [str(root), str(root / 'tools')]
        import strata_tokenizer as st
        from serve.server import StrataEngine, child_env
        directory = Path(cfg['tokenizer'])
        vocab = json.loads((directory / 'vocab.json').read_text())
        tokens = [None] * len(vocab)
        for text, index in vocab.items():
            tokens[index] = text
        self.tok = st.Tokenizer(tokens, (directory / 'merges.txt').read_text().split('\n'),
                               json.loads((directory / 'token_type.json').read_text()))
        template = directory / 'chat_template.jinja'
        self.template_path = template if template.exists() else root / 'serve' / 'chat_template.jinja'
        self.model_name = cfg.get('model_name', 'qwen3.8-flash-next')
        self._native = None
        args = [a for a in cfg['args'] if a not in ('--selected-prefill', '--selected-prefix-cache')]
        for flag, value in (('--max-context', limits.target_context), ('--kv-resident', limits.target_context),
                            ('--vram-reserve-mib', vram_reserve_mib), ('--prompt-cache', 6),
                            ('--conversation-cache-mib', 0), ('--turn-token', 248045),
                            *((('--kv', kv),) if kv else ())):
            if flag in args:
                args[args.index(flag)+1] = str(value)
            else:
                args.extend((flag, str(value)))
        env = child_env(cfg)
        env.update(STRATA_ROUTE_TAIL_SKIP='0', STRATA_PREFILL_CPU_SHARE='0')
        self.engine = StrataEngine(cfg['exe'], args, cwd=cfg['cwd'], log=str(log_path), env=env)
        try:
            allowed = compatible_ids(vocab, json.loads((Path(student_path) / 'tokenizer.json').read_text()))
            self.student = CachedStudent(Student(student_path, allowed), block=2048 if lean else 1024,
                                         sparse_keys=sparse_keys if lean else None,
                                         sparse_rows=sparse_rows if lean else None)
            self.selector = self.student if lean else AdaptiveSelector(self.student)
            self.target = NativeTarget(self.engine)
            self.summarize = NativeSummarizer(self.target, self.tok, context_limit=limits.target_context)
        except BaseException:
            self.engine.close()
            raise

    def session(self, path, system, limits):
        if getattr(self, "lean", False):
            from .spec_session import SpecSession
            if Path(path).exists():
                return SpecSession.restore(path, self.tok, self.student, self.target)
            return SpecSession(self.tok, self.student, self.target, system=system, limits=limits,
                               checkpoint=path)
        if Path(path).exists() or Path(path).with_suffix('.archive.sqlite3').exists():
            return CompactingSession.restore(path, self.tok, self.selector, self.target, self.summarize,
                                             archive_cache_bytes=self.archive_cache_bytes)
        return CompactingSession(self.tok, self.selector, self.target, self.summarize,
                                 system=system, limits=limits, checkpoint=path,
                                 archive_cache_bytes=self.archive_cache_bytes)

    def native(self, payload, on_text=None, cancel=None):
        """One plain Flash-Next chat completion through Strata's own OpenAI path.

        Same engine and prompt cache as the lean route, and the same template,
        tool, stop and sampling handling as Strata's native server. A prompt
        that does not fit the engine's window raises ValueError.
        """
        from serve import server as strata
        if self._native is None:
            from serve.frontend import ChatTemplate
            self._native = strata.Service(self.engine, self.tok, ChatTemplate(self.template_path),
                                          model_name=self.model_name)
        svc = self._native
        req = {**payload, 'model': svc.model}
        messages, tools, kwargs = strata.openai_to_messages(req)
        if strata.tool_choice_of(req.get('tool_choice'))[0] == 'none':
            tools = None
        force = strata.forced_call(req.get('tool_choice'), tools)
        messages, validator = strata.prepare_format(req.get('response_format'), messages)
        strata.stop_strings(req)
        strata.check_request_sampling(req)
        maximum = int(req.get('max_completion_tokens') or req.get('max_tokens') or 0)
        ids, thinking, maximum = svc.prepare(messages, tools, kwargs, maximum, force=force, req=req)
        chunks = strata.openai_chunks(svc, req, ids, thinking, tools, maximum, cancel or threading.Event(),
                                      force=force)
        if validator is not None:
            chunks = strata.structured_chunks(chunks, validator)

        def relay():
            for chunk in chunks:
                if chunk and on_text:
                    text = (chunk['choices'][0].get('delta') or {}).get('content') if chunk.get('choices') else None
                    if text:
                        on_text(text)
                yield chunk
        return strata.openai_collect(relay())

    def close(self):
        self.selector.reset()
        self.engine.close()
        # Release the student's VRAM even if a session still references it, so a
        # restarted service in this process gets the same expert cache.
        model = self.student.student.model
        self.student = self.selector = None
        model.to('meta')
        import gc
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
