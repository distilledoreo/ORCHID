# PanoRef bounded direct 1K/2K repeat experiment

Independent workload: frozen terminal slice of a completed PanoRef coding trajectory.
The direct raw-to-Solar apparatus, timeout, prompt policy, no-retry rule, and 12K source batching are unchanged from the FreetoShop repeat harness.
The slice preserves the authoritative review, final request, and corrective/audit/validation tail; it is not the full 1.3M-token PanoRef session.

Frozen replay SHA256: `aa0e7326c2514501a8c53a5a2c4669b1512383507989e71403d4e6f00f7eaf07`; raw plan `0e4ef153c1ab8658aaf3d087203736a5873d060c2f350fe76b26d3f264da2419`; 143,670 source tokens; 11 oracle checks.

| Budget | Full traces | Generations | Median wall s | Median retired tok/s | Final semantic P/F (full only) | All applicable semantic P/F |
|---:|:---:|:---:|---:|---:|---:|---:|
| 1000 | 1/3 | 13/13, 7/13, 8/13 | 454.8 | 315.89 | 5/6 | 99/47 |
| 2000 | 2/3 | 7/13, 13/13, 13/13 | 842.8 | 190.66 | 14/8 | 141/36 |

## Reliability

A full trace means every planned direct generation completed. Partial semantic rows are reported separately and are not treated as full-trace semantic evidence.

## Miss pattern

Full-trace-only misses are primary; partial-prefix rows are retained as diagnostics.

### Full-trace misses

```json
{
  "by_category": {
    "BLOCKER_PRESERVATION": 2,
    "CONTINUATION_SUFFICIENCY": 5,
    "CURRENT_FACT_PRESERVATION": 6,
    "CURRENT_INTENT_PRESERVATION": 1
  },
  "by_check": {
    "spot-001": 1,
    "spot-002": 2,
    "spot-003": 2,
    "spot-004": 1,
    "spot-006": 3,
    "spot-008": 1,
    "spot-009": 1,
    "spot-010": 1,
    "spot-011": 2
  },
  "missing_terms": {
    "100,000": 2,
    "100k": 2,
    "101,250": 1,
    "101250": 1,
    "33,120,308": 1,
    "36,788 triangles": 1,
    "36.9 seconds": 1,
    "36.9s": 1,
    "allowed floor": 1,
    "allowed-floor": 1,
    "connected component": 2,
    "connected-component": 2,
    "floor region": 1,
    "large imported": 2,
    "rebase": 1,
    "rounds five and six": 2,
    "stale analysis": 3,
    "stale-analysis": 3,
    "valid": 1,
    "valid 33,120,308-byte": 1
  }
}
```

### All applicable prefix diagnostics

```json
{
  "by_category": {
    "BLOCKER_PRESERVATION": 11,
    "CONTINUATION_SUFFICIENCY": 13,
    "CURRENT_FACT_PRESERVATION": 50,
    "CURRENT_INTENT_PRESERVATION": 9
  },
  "by_check": {
    "spot-001": 6,
    "spot-002": 41,
    "spot-003": 11,
    "spot-004": 9,
    "spot-005": 8,
    "spot-006": 3,
    "spot-008": 1,
    "spot-009": 1,
    "spot-010": 1,
    "spot-011": 2
  },
  "missing_terms": {
    "100,000": 11,
    "100k": 11,
    "101,250": 1,
    "101250": 1,
    "33,120,308": 1,
    "36,788 triangles": 1,
    "36.9 seconds": 1,
    "36.9s": 1,
    "adversarial audit": 8,
    "allowed floor": 6,
    "allowed-floor": 6,
    "connected component": 41,
    "connected-component": 41,
    "disprove": 8,
    "floor region": 6,
    "large imported": 11,
    "rebase": 9,
    "rounds five and six": 2,
    "stale analysis": 3,
    "stale-analysis": 3,
    "subagent": 8,
    "valid": 1,
    "valid 33,120,308-byte": 1
  }
}
```

## Provenance

- No provider retry occurred inside a run.
- Each repeat has a fresh output root and immutable replay, plan, oracle, and prompt hashes.
- This experiment does not tune FreetoShop or change ORCHID production code.
