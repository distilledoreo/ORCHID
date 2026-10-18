"""Cheap adaptive signals computed from one student importance vector.

No additional model inference: all signals derive from the per-key scores
and their 32-token chunk means already produced by Student.trace().
"""

from __future__ import annotations

import math


def chunk_means(scores, chunk_size: int = 32) -> list[float]:
    return [
        float(sum(scores[i : i + chunk_size]) / max(1, len(scores[i : i + chunk_size])))
        for i in range(0, len(scores), chunk_size)
    ]


def signals_from_scores(scores, chunk_size: int = 32) -> dict:
    means = chunk_means(scores, chunk_size)
    ordered = sorted(means, reverse=True)
    total = sum(ordered) or 1e-12
    n = len(ordered)
    # Concentration: mass captured by top fractions + entropy + gini.
    def top_mass(frac: float) -> float:
        k = max(1, math.ceil(n * frac))
        return sum(ordered[:k]) / total

    probs = [m / total for m in ordered]
    entropy = -sum(p * math.log(p + 1e-12) for p in probs)
    # Gini over chunk means (0 = equal, ~1 = concentrated).
    sorted_asc = sorted(means)
    cum = 0.0
    for i, m in enumerate(sorted_asc, 1):
        cum += i * m
    gini = (2 * cum) / (n * total) - (n + 1) / n if n > 1 else 0.0
    # Margins: gap between last-kept and first-dropped at candidate rates.
    margins = {}
    for rate in (0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.0):
        k = max(1, math.ceil(n * rate))
        kept_min = ordered[k - 1]
        dropped_max = ordered[k] if k < n else float("-inf")
        margins[str(rate)] = kept_min - dropped_max if k < n else float("inf")
    return {
        "n_chunks": n,
        "chunk_size": chunk_size,
        "mean": total / n,
        "max": ordered[0],
        "max_mean_ratio": ordered[0] / (total / n),
        "top_mass": {str(f): top_mass(f) for f in (0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.0)},
        "entropy": entropy,
        "entropy_norm": entropy / math.log(n) if n > 1 else 0.0,
        "gini": gini,
        "margins": margins,
        "sorted_means": ordered,
    }


def adaptive_rate_by_margin(sig: dict, min_rate: float = 0.05, gap: float = 0.0) -> float:
    """Smallest rate whose keep/drop margin exceeds gap (elbow proxy).

    Falls back to 0.30 when no candidate clears the gap. Pure function of the
    already-computed chunk means — no extra inference.
    """
    for rate in (0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.0):
        if rate < min_rate:
            continue
        if sig["margins"][str(float(rate))] > gap:
            return float(rate)
    return 0.30


def adaptive_rate_by_mass(sig: dict, target_mass: float = 0.80) -> float:
    """Smallest rate whose top-mass captures target_mass of chunk-mean total."""
    for rate in (0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.0):
        if sig["top_mass"][str(float(rate))] >= target_mass:
            return float(rate)
    return 1.0
