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
                 lean=False, vram_reserve_mib=4096, sparse_keys=None, sparse_rows=None, kv=None,
                 target_park_mib=0, restore_floor_mib=1024, elastic=None):
        self.archive_cache_bytes = archive_cache_bytes
        # Elastic engine (a Strata build with VRAM <mib> under --kv-grow): Flash-Next keeps whole agent
        # conversations in a 256K elastic K/V and hands the student its VRAM only when a lean turn needs it.
        # {'exe', 'window', 'prefill', 'student_mib', 'base_mib'}; None: the fixed lean configuration.
        self.elastic = elastic
        self.student_on_gpu = True
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
        window = elastic['window'] if elastic else limits.target_context
        for flag, value in (('--max-context', window), ('--kv-resident', 0 if elastic else window),
                            # Elastic: the cache starts at its full size (it can't grow past it later)
                            # and frees the student's VRAM right after the engine is up.
                            ('--vram-reserve-mib', elastic['base_mib'] if elastic else vram_reserve_mib),
                            *((('--prefill', elastic['prefill']),) if elastic else ()),
                            ('--prompt-cache', 6),
                            # Parked conversations: a thread switch keeps the other thread's
                            # Flash-Next K/V in RAM instead of re-reading its prompt.
                            ('--conversation-cache-mib', target_park_mib if lean else 0),
                            *((('--conversation-cache-slots', 4), ('--conversation-cache-min-tokens', 4096))
                              if lean and target_park_mib else ()),
                            ('--turn-token', 248045),
                            # RAM kept free when saving or restoring a session file (Strata's
                            # default 2560 refuses restores on a 62 GB machine running 47 GB of experts).
                            ('--conversation-cache-min-free-mib', restore_floor_mib),
                            *((('--kv', kv),) if kv else ())):
            if flag in args:
                args[args.index(flag)+1] = str(value)
            else:
                args.extend((flag, str(value)))
        if elastic and '--kv-grow' not in args:
            args.append('--kv-grow')
        env = child_env(cfg)
        env.update(STRATA_ROUTE_TAIL_SKIP='0', STRATA_PREFILL_CPU_SHARE='0')
        self.engine = StrataEngine(elastic['exe'] if elastic else cfg['exe'], args, cwd=cfg['cwd'],
                                   log=str(log_path), env=env)
        try:
            if elastic:
                self.vram(elastic['student_mib'])
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
            if Path(path).exists() or Path(path).with_suffix('.json').exists():
                return SpecSession.restore(path, self.tok, self.student, self.target)
            return SpecSession(self.tok, self.student, self.target, system=system, limits=limits,
                               checkpoint=path)
        if Path(path).exists() or Path(path).with_suffix('.archive.sqlite3').exists():
            return CompactingSession.restore(path, self.tok, self.selector, self.target, self.summarize,
                                             archive_cache_bytes=self.archive_cache_bytes)
        return CompactingSession(self.tok, self.selector, self.target, self.summarize,
                                 system=system, limits=limits, checkpoint=path,
                                 archive_cache_bytes=self.archive_cache_bytes)

    def vram(self, reserve_mib, drop=False, timeout=120.0):
        """Elastic engine: keep `reserve_mib` of VRAM free (`drop`: the live conversation goes too)."""
        import queue
        import time
        from serve.server import EngineDied
        engine = self.engine
        if not engine.alive():
            raise EngineDied('the engine is not running')
        engine.proc.stdin.write(f'VRAM {int(reserve_mib)}' + (' drop' if drop else '') + '\n')
        engine.proc.stdin.flush()
        deadline = time.time() + timeout
        while True:
            try:
                line = engine.lines.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                raise EngineDied('the engine did not answer the VRAM command') from None
            if line is None:
                raise EngineDied('the engine ended')
            line = line.strip()
            if line.startswith('ERR'):
                raise ValueError(line[4:].strip() or 'the engine refused the VRAM command')
            if line.startswith('VRAM '):
                return dict(kv.partition('=')[::2] for kv in line.split()[1:])
            if time.time() > deadline:
                raise EngineDied('the engine did not answer the VRAM command')

    def student_device(self, device):
        """Move the student's weights (its cache is parked by the caller first)."""
        model = self.student.student.model
        model.to(device)
        self.student_on_gpu = device == 'cuda'
        if device != 'cuda':
            import torch
            torch.cuda.empty_cache()

    def save_target(self, path):
        """Write the engine's current conversation (a thread's Flash-Next K/V) to a session file."""
        from serve.server import EngineDied, SessionRefused
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        try:
            return self.engine.session_file('save', str(path))
        except SessionRefused as error:
            print(f'orchid: session save of {Path(path).name[:12]} refused ({error.kind}: {error})', file=sys.stderr)
            return None
        except (EngineDied, OSError):
            return None  # Not saved: this thread's prompt is read again when it returns.

    def restore_target(self, path):
        """Load a thread's saved Flash-Next K/V, so its next prompt is read from where it stopped."""
        from serve.server import EngineDied, SessionRefused
        if not Path(path).exists():
            return None
        try:
            return self.engine.session_file('restore', str(path))
        except EngineDied:
            return None  # The next request restarts the engine and reads the prompt.
        except SessionRefused as error:
            print(f'orchid: session restore of {Path(path).name[:12]} refused ({error.kind}: {error})', file=sys.stderr)
            if error.kind == 'invalid':
                Path(path).unlink(missing_ok=True)  # Another engine version or a damaged file.
            return None  # Memory, storage or I/O: the file stays for a later try.
        except OSError:
            return None

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
                if chunk and on_text and chunk.get('choices'):
                    delta = chunk['choices'][0].get('delta') or {}
                    if delta.get('reasoning_content'):
                        on_text(delta['reasoning_content'], 'reasoning')
                    if delta.get('content'):
                        on_text(delta['content'])
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
