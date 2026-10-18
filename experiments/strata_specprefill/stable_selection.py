"""Keep selected history in original order; backfill requires suffix recomputation."""
from .core import token_hash


class StableSelection:
    def __init__(self):
        self.ids = []
        self.positions = []

    def plan(self, ids, selected):
        if (not selected or any(type(p) is not int or not 0 <= p < len(ids) for p in selected)
                or any(a >= b for a, b in zip(selected, selected[1:]))):
            raise ValueError('Invalid selected positions')
        extending = bool(self.ids) and len(self.ids) <= len(ids) and ids[:len(self.ids)] == self.ids
        previous = self.positions if extending else []
        positions = sorted(set(previous) | set(selected))
        additions = sorted(set(positions) - set(previous))
        backfilled = [p for p in additions if previous and p <= previous[-1]]
        old_tokens = [self.ids[p] for p in previous]
        new_tokens = [ids[p] for p in positions]
        # Both ID and coordinate must match before a prefix can be reused.
        reusable = 0
        for old_p, new_p, a, b in zip(previous, positions, old_tokens, new_tokens):
            if old_p != new_p or a != b:
                break
            reusable += 1
        self.ids, self.positions = list(ids), positions
        return positions, {'backfilled_tokens': len(backfilled), 'prefix_cells_unchanged': reusable,
                           'selected_input_hash': token_hash(new_tokens), 'history_reset': not extending}
