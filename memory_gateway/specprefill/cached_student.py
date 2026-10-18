"""Exact append-only prompt cache, isolated from speculative draft state."""
from .chunked_student import make_growable, trace_chunked
from .core import validate_input

STATE_FORMAT = 'orchid-student-state-v1'


class CachedStudent:
    def __init__(self, student, block=1024, prefill_window=None, sparse_keys=None, sparse_rows=None):
        self.student = student
        self.block = block
        self.prefill_window = prefill_window
        self.sparse_keys = sparse_keys
        self.sparse_rows = sparse_rows
        self.state = {}

    def reset(self):
        self.state.clear()

    def identity(self):
        """What the cached K/V depend on: a parked state is reused only under the same settings."""
        return {'model': str(getattr(self.student, 'path', '')), 'block': self.block,
                'window': self.prefill_window, 'sparse_keys': self.sparse_keys, 'sparse_rows': self.sparse_rows}

    def export_state(self):
        """The prefix cache as CPU tensors, to park a thread (None when there is none)."""
        if not self.state.get('ids'):
            return None
        torch = self.student.torch
        layers = []
        for layer in self.state['cache'].layers:
            if isinstance(getattr(layer, 'conv_states', None), dict):
                layers.append({'kind': 'linear', **{
                    name: {i: t.detach().to('cpu', copy=True) for i, t in getattr(layer, name).items()
                           if t is not None}
                    for name in ('conv_states', 'recurrent_states')}})
            else:
                n = layer.keys.shape[-2]
                layers.append({'kind': 'attention', 'keys': layer.keys[..., :n, :].to('cpu', copy=True),
                               'values': layer.values[..., :n, :].to('cpu', copy=True)})
        return {'format': STATE_FORMAT, 'identity': self.identity(),
                'ids': torch.tensor(self.state['ids'], dtype=torch.int32), 'layers': layers}

    def import_state(self, saved):
        """Resume a parked thread. False (and an empty cache) when it doesn't fit these settings."""
        self.state.clear()
        if not saved or saved.get('format') != STATE_FORMAT or saved.get('identity') != self.identity():
            return False
        torch, device = self.student.torch, self.student.device
        ids = saved['ids'].tolist()
        with torch.inference_mode():
            # One real token gives a cache with the model's own layer types and flags.
            cache = self.student.model(input_ids=torch.tensor([ids[:1]], device=device), use_cache=True,
                                       logits_to_keep=1).past_key_values
        if len(cache.layers) != len(saved['layers']):
            return False
        for layer, part in zip(cache.layers, saved['layers']):
            if part['kind'] == 'linear':
                for name in ('conv_states', 'recurrent_states'):
                    getattr(layer, name).update({i: t.to(device) for i, t in part[name].items()})
            else:
                layer.keys, layer.values = part['keys'].to(device), part['values'].to(device)
        if device == 'cuda':
            make_growable(cache)
        self.state.update(ids=ids, cache=cache)
        return True

    def trace(self, ids, keep_ratio=0.30, **kwargs):
        validate_input(ids, self.student.allowed_ids)
        return trace_chunked(self.student.model, self.student.torch, self.student.stop_ids,
                             ids, keep_ratio, prefill_block=self.block,
                             device=self.student.device, prefix_state=self.state,
                             prefill_window=kwargs.pop('prefill_window', self.prefill_window),
                             sparse_keys=kwargs.pop('sparse_keys', self.sparse_keys),
                             **({'sparse_rows': self.sparse_rows} if self.sparse_rows else {}), **kwargs)

    def score(self, ids, **kwargs):
        """One uncached trace that leaves the conversation prefix cache intact."""
        validate_input(ids, self.student.allowed_ids)
        return trace_chunked(self.student.model, self.student.torch, self.student.stop_ids,
                             ids, 1.0, prefill_block=self.block,
                             device=self.student.device, prefix_state=None, **kwargs)
