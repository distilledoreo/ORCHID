"""Exact append-only prompt cache, isolated from speculative draft state."""
from .chunked_student import trace_chunked
from .core import validate_input


class CachedStudent:
    def __init__(self, student, block=1024, prefill_window=None, sparse_keys=None):
        self.student = student
        self.block = block
        self.prefill_window = prefill_window
        self.sparse_keys = sparse_keys
        self.state = {}

    def reset(self):
        self.state.clear()

    def trace(self, ids, keep_ratio=0.30, **kwargs):
        validate_input(ids, self.student.allowed_ids)
        return trace_chunked(self.student.model, self.student.torch, self.student.stop_ids,
                             ids, keep_ratio, prefill_block=self.block,
                             device=self.student.device, prefix_state=self.state,
                             prefill_window=kwargs.pop('prefill_window', self.prefill_window),
                             sparse_keys=kwargs.pop('sparse_keys', self.sparse_keys), **kwargs)

    def score(self, ids, **kwargs):
        """One uncached trace that leaves the conversation prefix cache intact."""
        validate_input(ids, self.student.allowed_ids)
        return trace_chunked(self.student.model, self.student.torch, self.student.stop_ids,
                             ids, 1.0, prefill_block=self.block,
                             device=self.student.device, prefix_state=None, **kwargs)
