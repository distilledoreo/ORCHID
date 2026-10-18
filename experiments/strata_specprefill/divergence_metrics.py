"""Full-vocabulary KL and teacher-forced agreement in float64, without truncation."""
from __future__ import annotations


def content_positions(text, pieces, literals):
    """Locate reference facts by UTF-8 byte overlap, without exposing them to selection."""
    import re
    raw = text.encode()
    if b''.join(pieces) != raw:
        raise ValueError('Reference token byte roundtrip failed')
    spans = []
    for literal in literals:
        for match in re.finditer(r'(?<!\w)'+re.escape(str(literal))+r'(?!\w)', text):
            spans.append((len(text[:match.start()].encode()), len(text[:match.end()].encode())))
    positions, offset = [], 0
    for i, piece in enumerate(pieces):
        end = offset+len(piece)
        if any(offset < b and end > a for a, b in spans):
            positions.append(i)
        offset = end
    return positions


def compare_rows(full, selected, reference):
    import numpy as np
    p_raw, q_raw = np.asarray(full), np.asarray(selected)
    if p_raw.ndim != 2 or p_raw.shape != q_raw.shape or p_raw.shape[0] != len(reference):
        raise ValueError('Aligned logits/reference shape mismatch')
    if p_raw.shape[1] < 2 or not np.isfinite(p_raw).all() or not np.isfinite(q_raw).all():
        raise ValueError('Nonfinite or invalid vocabulary logits')
    if any(type(t) is not int or t < 0 or t >= p_raw.shape[1] for t in reference):
        raise ValueError('Invalid reference token')
    rows = []
    for i, token in enumerate(reference):
        a = p_raw[i].astype(np.float64)
        b = q_raw[i].astype(np.float64)
        a -= a.max()
        b -= b.max()
        lp = a-np.log(np.exp(a).sum())
        lq = b-np.log(np.exp(b).sum())
        kl = float(np.dot(np.exp(lp), lp-lq))
        if kl < -1e-10:
            raise ValueError('Negative KL beyond numerical precision')
        rows.append({'position':i, 'kl_full_to_selected':max(0., kl),
                     'top1_agree':bool(np.argmax(a)==np.argmax(b)),
                     'full_reference_logp':float(lp[token]), 'selected_reference_logp':float(lq[token]),
                     'reference_logp_delta':float(lq[token]-lp[token]),
                     'full_top1_margin':float(np.partition(a, -2)[-1]-np.partition(a, -2)[-2])})
    return rows
