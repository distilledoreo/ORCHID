# Addressable SpecPrefill compaction (opt-in)

`CompactingSession` implements a bounded working transcript backed by the original
versioned archive. It is an experimental integration API and CLI, not a change to
the earlier benchmark arms. An opt-in gateway route is documented in
[specprefill-gateway.md](../../docs/specprefill-gateway.md); existing service defaults stay unchanged.

## Data flow

1. `add_source(path, version, text, cue=...)` records exact source bytes and a
   readable retrieval cue. The student sees the original evidence initially.
2. `answer(question)` scores the student context. Selected summary cues nominate
   archive manifests. Indexed search shortlists exact evidence inside that scope.
   Evidence is expanded into the student, rescored, and
   selected chunks are appended to the target at new sequential positions.
3. `compact(question)` scores the **whole current target transcript**, asks the
   summarizer for working-memory entries with provenance, and combines the new
   summary with selected exact evidence. It prefills the target before publishing
   a new generation. The student resets and cold-prefills the same seed on its
   next trace. System instructions remain pinned.
4. Subsequent turns append to the new generation. Previously delivered chunks
   that became summary-only can be recovered again. Historical delivery never
   suppresses recovery into a new generation.

User questions and actual target replies are also archived. Selected original
conversation passages are reframed as source evidence with their original role
in the source path; this is a new prompt, not a claim of identical inference.
`exact` means present in the replayable target transcript. It does not assert
that a particular backend has kept every token physically resident in VRAM.

## Summary-of-summary references

Summarizers return `SummaryEntry(text, references)`. References may be raw source
IDs/ranges or the `M0`, `M1`, ... entries supplied in that compaction request.
The controller resolves old M IDs into archive references and interns them in an
immutable, content-addressed **external manifest**, such as `A<sha256>`. Only that
compact handle appears next to the new summary text. No source list or old summary
text needs to expand recursively in the prompt.

Manifest nodes may point to older manifests, but these are archive membership
records, not summaries that a model must interpret. Traversal uses a visited set
and emits each exact source chunk once. New nodes can only reference existing
nodes, preventing cycles. Every compaction must preserve reachability of every
archived chunk. Provenance coverage is validated; summary factual quality is not
thereby proven.

A manifest can cover more text than either model can hold. Four explicit policies
are available:

- `indexed` (default): search the nominated archive scope before reading any of
  its contents through the student. With no nominated scope, search the archive;
  with no scoped hits, widen once. Return a bounded shortlist, then rescore in the
  student. No automatic full student scan.
- `indexed-scan`: the same search, with a finite full-archive student scan only
  if indexed search returns no candidates. This is an explicit fallback, not a
  confidence-based accuracy guarantee.
- `scan`: when nominated evidence exceeds one expansion window, score
  it in finite student-sized batches, visiting every nominated chunk once. Keep a
  bounded shortlist and rescore that shortlist in the working context.
- `bounded`: expand one bounded window without scanning the rest. Report the
  deferred count; do not imply that the unexamined text was considered.

All retain the originals. Scan cost grows with nominated archive size. Attention
scores from separate windows are heuristic rankings; they are not equivalent to
attention over the joint corpus. A summary nomination can still miss a relevant
manifest, and multi-hop evidence may span windows. Lossless storage and finite
traversal do **not** establish lossless model behavior. There is no full-corpus
recall or infinite-context accuracy claim.

## Indexed retrieval

`ArchiveSearch` builds SQLite FTS5 documents and exact identifier postings at
ingestion, including identifiers crossing archive-chunk boundaries. Query terms
are extracted and quoted, so user text cannot inject SQL or FTS operators.
Snake-case and camel-case terms participate in text search. Reciprocal-rank
fusion combines symbol matches, text ranking, and optional semantic matches.

Results include bounded neighboring chunks and one hop of Python AST-derived
definition dependencies. Every returned neighbor/dependency must belong to the
current permitted scope; lookup never opens a filesystem path. Python snippets
that cannot be parsed still have lexical/symbol retrieval. This is not a complete
cross-language call graph, import resolver, or dynamic dependency analyzer.

Explicit path, version, and symbol mentions in the question bind to complete
definition units (a whole function body, including its return-value chunk)
ahead of fuzzy ranking; a bare version or symbol without a path never binds.
All fused direct hits are emitted before any dependency/neighbor context, so
context can no longer displace a later direct hit from the bounded emission.
Expansion admission takes definition units atomically: a unit whose remaining
chunks do not fit is deferred whole rather than split. Together these keep a
return value with its definition from retrieval through the expansion budget.

When a checkpoint is configured, the derived index lives beside it as
`session.search.sqlite3`. It updates incrementally. On restart it reconciles
against authoritative source hashes and saved retrieval cues, removing stale
rows and reusing valid embeddings. The raw archive database remains authoritative;
the index can be rebuilt. Original archive data is never replaced by embeddings.

Optional local ONNX embeddings use the existing `OnnxTextEmbedder` on CPU, with a
model/tokenizer identity tied to file hashes. Vectors are normalized and validated
for finite nonzero values and consistent dimensions. Changing model identity
rebuilds vectors. Queries embed only the question; source chunks are not
re-embedded on each turn. Semantic lookup currently uses exact cosine search
over scoped cached vectors, not an ANN index. Without an embedding model, the
working default is lexical/symbol retrieval; semantic retrieval is not implied.

Answer telemetry records the search scope, widening, hit handles/reasons,
embedding identity, expanded/deferred candidates, and any student scan passes.
Empty search results do not turn into fabricated source matches. Search results
and student attention are not calibrated evidence-sufficiency scores. Widening
currently happens on an empty search result, not a judgment that an eventual
answer is correct; relevant evidence can still be missed.

## Local usage

The existing cached student is wrapped by `AdaptiveSelector`; `NativeTarget` uses
the existing Strata transport and ordinary prefix caching. `NativeSummarizer`
asks the target for JSON working-memory entries. It rejects malformed output and
the controller rejects dangling or incomplete provenance. There is no silent
heuristic summary fallback.

Use a dedicated engine and student per session. Outside the gateway, compaction is explicit: invoke it
while there is room for the summary instruction, summary completion, and next
question. Oversized ingress and exhausted budgets raise errors rather than
silently truncating. Oversized single documents must be ingested in bounded
source sections. Chunked prefill does not enlarge the model's context limit.

A private script has this structure:

```json
{
  "system": "Use versioned evidence to answer the user's questions.",
  "actions": [
    {"kind": "source", "path": "src/config.py", "version": 1,
     "text": "TIMEOUT = 37\n", "cue": "Configured request timeout"},
    {"kind": "answer", "question": "What is the timeout?"},
    {"kind": "compact", "question": "Continue work on the configuration"},
    {"kind": "answer", "question": "Remind me of the exact timeout."}
  ]
}
```

Run from the student Python environment (with the local Strata frontend dependencies):

```bash
python -m experiments.strata_specprefill.compaction_session_cli \
  --config /path/to/existing-isolated-strata-config.json \
  --student /path/to/Qwen3.5-0.8B \
  --script /private/script.json \
  --output /private/new-compaction-run
```

The CLI copies the config, sets context/reserve and ordinary caching for its own
engine, and preserves the original config and service. It saves raw actions,
engine metrics, and the session checkpoint in the private output directory. It
refuses an existing output directory. These action timings are not matched
latency benchmarks.

To add semantic retrieval, provide both `--embedding-model /local/model.onnx`
and `--embedding-tokenizer /local/tokenizer.json`. The existing ONNX adapter
expects a compatible encoder with token-state output and BERT-style input names.
Required experiment dependencies must already be installed. No downloads,
network embedding calls, new GPU model, or service changes happen automatically.
Use `--retrieval-mode indexed-scan` only when the extra fallback cost is wanted.

## Persistence and failure behavior

Persistent sessions use `<session>.archive.sqlite3` for raw source bodies, token
pages, manifests, cues, and events, with a small checksummed JSON descriptor.
Archive updates and active replayable state commit together. Restore validates
active tokenization, reference coverage, context limits, and exact active spans;
archived payload checksums are validated when those records are read. It resets student cache state
and does not claim a live target cache. A failed rebuild leaves the old logical
prompt/generation available for replay, even if the backend's physical cache was
changed. A failed answer retains its question for retry. Reuse that same question
when retrying the failed turn. This is a single-owner session API; multi-process access to the same model/session store is unsupported. The optional
gateway adds exclusive process ownership, serialized inference, automatic scheduling,
and a persistent request-replay ledger; the standalone session API does not.

Raw text and token pages now load on demand through a bounded payload cache
(default 8 MiB; CLI `--archive-cache-mib`). SQLite page caches and OS file caches
are separate from that budget. Ephemeral sessions without a checkpoint retain
the original in-memory backend. Legacy JSON checkpoints migrate once on restore,
preserving an exact `.legacy.json` backup; that initial migration still parses the
whole old file. See [paged storage](../../docs/specprefill-paged-storage.md).
Gateway history fingerprints persist as incremental per-message ledger rows, separately from the paged archive.

## Verification and remaining production work

Controller tests cover repeated compaction, original-source recovery, large
manifest scans, stable prompt handle size, bounded-mode omissions, exact Unicode
and revision identity, append-only turns, failed rebuild/answer, restart, and
checkpoint corruption. Model-independent tests do not establish real-model
summary quality, selection accuracy, or cache performance. The new real-model
compaction/retrieval loop has not yet been evaluated live.
Search tests additionally cover split identifiers, scoped versions/dependencies,
query escaping, paraphrase fusion with controlled embedding vectors, invalid
vectors, persistent embedding reuse, index reconciliation, finite widening,
late-evidence retrieval without scans, and explicitly enabled scan fallback.
The embedding tests do not establish recall for a real encoder.

Before routing production traffic, validate:

- Matched full-context versus compacted multi-turn tasks across repeated resets,
  including relevant details absent from summaries and multi-hop retrieval.
- Total TTFT and compaction/scan cost against ordinary target prefix caching,
  plus peak student/target memory and native cache reuse at boundaries.
- Summary cue quality and retrieval recall separately from archive integrity.
- Live validation of the opt-in gateway, automatic headroom-aware triggers,
  cancellation/retries, exclusive session ownership, paged archive behavior under sustained load,
  and storage capacity/retention management.

Earlier latency/correctness baselines and native binaries remain unchanged.

The production punchlist, including paged raw archive storage, is maintained in
[specprefill-production-punchlist.md](../../docs/specprefill-production-punchlist.md).

If generated summary text fits but per-item provenance addresses push its rendered
seed over budget, the controller coalesces adjacent items in finite rounds. It
preserves every text item and the union of its exact references, reducing address
overhead rather than truncating content. A single item that still cannot fit is
rejected. Compaction events record `summary_merge_rounds`.

Explicit Python references also have a bounded exact-lookup path, enabled by
default in `CompactingSession` and the opt-in gateway sessions that use it.
Recognized questions pair `function() from path.py revision N`, or
`In path.py revision N, return function()`, with that exact archived revision.
This lookup does not depend on summary nomination. If the question mentions
`ROUTE_KEY`, literal `path.py@vN:function` links are followed with a visited set
and a 32-definition limit. Sources are read only from the session archive;
their paths never trigger filesystem or network reads. Parsing is limited to
256 KiB per source, and unsupported syntax or missing definitions defer to
ordinary retrieval without substituting another revision.

The resulting complete definitions use immutable source/revision/hash headers.
Both the student and target receive the block immediately before the question;
explicitly addressed definitions are guaranteed delivery rather than subject
to attention filtering. The block and ordinary retrieved chunks share the
existing expansion-token allowance. Full framing and answer headroom are
checked for both models; an oversized block is deferred as a whole. Existing
prompt prefixes are preserved. This explicit path intentionally repeats a
small requested definition even if its source was delivered earlier.

Answer events record `exact_lookup` status (`delivered`, `deferred`,
`not_applicable`, or `disabled`), frame token count and source provenance when
delivered, or a deferral reason. The constructor's `exact_lookup=False` supports
controlled comparisons and historical replay; the setting survives restore.
This narrow lookup does not validate arbitrary numeric summary claims or
provide semantic retrieval for questions without recognized source references.
