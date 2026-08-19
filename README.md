# ORCHID

**Orchestrated Runtime for Context Hibernation, Integration, and Distillation**

ORCHID gives finite-context LLM agents persistent memory by keeping immutable raw history on disk and asynchronously distilling it into durable context capsules.

> **Status:** research prototype / experimental. It is not production-ready.

## Architecture

```text
raw events → selector → canonicalizer → consolidator → capsule + recent tail
     append-only       bounded        lossless       CAS promotion
```

The gateway is an OpenAI-compatible proxy. Raw events remain authoritative in SQLite. A background worker freezes an immutable snapshot, runs composable model stages, validates the result deterministically, and promotes a descendant capsule with compare-and-swap semantics.

The model boundary is deliberately narrow:

- `SelectorEngine` identifies potentially durable source items.
- `CanonicalizerEngine` clarifies selected evidence in deterministic bounded batches.
- Software owns exhaustive source coverage and assembles the lossless packet.
- `ConsolidatorEngine` performs the global semantic merge.
- The worker, never a model, owns candidate state, lineage, validation, leases, and promotion.

Normal events stay whole. Oversized events become deterministic verbatim source spans with parent IDs, character ranges, and hashes. Selector chunks target about 1,200 estimated tokens; canonicalizer batches default to 8,192 estimated input tokens. All three model stages use structured JSON Schema responses.

## Evidence: Bluejay

The generation-9 live lineage test produced the first successful descendant of the preserved Bluejay capsule:

- The descendant used the prior capsule as its base and became `ACTIVE`; the prior capsule became `SUPERSEDED`.
- Two generations of raw evidence were compacted, then the relevant old raw evidence left the protected tail.
- Solar still recalled the updated Bluejay state from the descendant capsule alone.
- The recall preserved the stable facts and updated the lease-race findings without retaining the superseded “pending” or “unapproved” claims.

The sanitized public fixture and short PASS report are in `artifacts/`. Original raw specimens are not part of the public repository.

## Runtime configuration

The first runnable pipeline is enabled when selector, canonicalizer, and consolidator endpoints/models are configured:

```powershell
$env:ORCHID_BACKEND_URL = "https://provider.example/v1"
$env:ORCHID_BACKEND_API_KEY = "..."
$env:ORCHID_BACKEND_MODEL = "your-chat-model"

$env:ORCHID_SELECTOR_URL = "http://127.0.0.1:1234/v1"
$env:ORCHID_SELECTOR_MODEL = "your-local-selector"
$env:ORCHID_CANONICALIZER_URL = "http://127.0.0.1:1234/v1"
$env:ORCHID_CANONICALIZER_MODEL = "your-local-canonicalizer"

$env:ORCHID_CONSOLIDATOR_URL = "https://provider.example/v1/chat/completions"
$env:ORCHID_CONSOLIDATOR_MODEL = "your-consolidator"
$env:ORCHID_CONSOLIDATOR_API_KEY = "..."

$env:ORCHID_CONTEXT_TOKENS = "32768"
$env:ORCHID_SELECTOR_CONTEXT_TOKENS = "32768"
$env:ORCHID_CANONICALIZER_INPUT_TOKENS = "8192"
$env:ORCHID_BACKGROUND_FRACTION = "0.65"
$env:ORCHID_URGENT_FRACTION = "0.85"
$env:ORCHID_LEASE_SECONDS = "900"
$env:ORCHID_LEASE_RENEWAL_SECONDS = "30"
$env:ORCHID_RECOVER_EXPIRED_JOBS = "false"
```

Credentials are transport configuration only. They are never serialized into capsules, telemetry, fixtures, or diagnostic excerpts.

## Development

Python 3.11 and 3.12 are supported:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
pytest
```

Run the gateway after configuring a backend:

```powershell
$env:ORCHID_DB = ".\data\memory.db"
uvicorn memory_gateway.gateway:app --host 127.0.0.1 --port 7331
```

Clients select a project and thread with:

```text
X-Memory-Project: demo
X-Memory-Thread: thread-1
```

Useful observability endpoints:

- `GET /healthz`
- `GET /debug/thread/{thread_id}`
- `GET /debug/jobs`

Provider-dependent integration tests are opt-in; the default test suite uses local fakes and requires no provider credentials.

## Safety contract

Failed or stale candidates never replace the active capsule. Promotion requires a `READY` candidate, unchanged lineage, valid hashes/provenance, current lease ownership, and one conditional active-capsule update. Raw events are never deleted by the gateway or storage layer.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [docs/invariants.md](docs/invariants.md).
