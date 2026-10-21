# Paged SpecPrefill archive

Persistent compacting sessions keep raw data in a per-session SQLite database on
the volume containing the configured state directory. It can be SSD or HDD;
indexed random reads will generally favor SSD. No model or KV cache is moved to disk.

Each immutable source revision stores its UTF-8 bytes once. Chunk records retain
byte/token offsets and provenance; reading a chunk fetches its byte range rather
than loading its whole source. Original token IDs use checksummed pages of 1,024
uint32 IDs. Manifests, cues, and historical events are also disk-backed. Retrieval
candidate sets and manifest traversal use SQL metadata rather than corpus-sized
Python sets. The separate derived search database keeps lexical/symbol indexes
and optional embeddings; it is reconciled against the archive on restore.

## Memory and configuration

`ORCHID_SPECPREFILL_ARCHIVE_CACHE_MIB` defaults to 8 per resident session. The
standalone CLI exposes `--archive-cache-mib`; the Python session API accepts
`archive_cache_bytes`. Zero disables retained payload caching. The LRU accounts
for Python payload/key sizes plus an entry allowance. Oversized payloads are
returned uncached. The budget covers retained payloads, not transient ingestion,
returned values retained by callers, active prompt IDs, or total process RSS.

Each resident session additionally configures approximately 4 MiB of SQLite raw
archive page cache and 2 MiB of search page cache. Memory mapping is disabled;
large temporary SQL sets may spill to disk. OS filesystem caches, model weights,
KV/recurrent states, and gateway history fingerprints are separate. Paging bounds
retained raw payloads; it does not promise a constant total RAM footprint.

## Transactions and recovery

New archive records, events, and the bounded active session snapshot commit in one
SQLite transaction with WAL and synchronous FULL. A source admission savepoint
rolls back rejected sources; failed compaction rolls back unpublished archive
changes. Previously committed logical state remains restorable. Numerical model
caches are rebuilt rather than deserialized from disk.

The JSON checkpoint is a small checksummed descriptor naming the archive UUID and
local database basename. It stays unchanged during normal turns. Missing
descriptors can be recovered from a committed archive database. A corrupt or
mismatched descriptor fails closed. Active state and fetched records have
checksums; lazy restore does not perform a full corpus integrity scan.

Legacy schema-1 JSON checkpoints migrate on first restore, retaining source IDs,
exact bytes, spans, manifest provenance, and delivery state. The original becomes
an exact `.legacy.json` backup before the descriptor is published. Migration can
resume after a database commit interrupted before publication. This one-time
conversion parses the full old JSON and needs corresponding RAM and extra disk
space; subsequent schema-2 opens are lazy.

## Backup and limits

With the gateway stopped, copy its state directory including the JSON descriptors,
archive/search databases, and gateway ledger. For a running service, use a
coordinated SQLite backup procedure; copying only a main database can miss WAL
transactions. Retain the legacy backup until the migrated session is verified.

Disk usage grows with immutable revisions, metadata, indexes, events, and one
small history-ledger row per accepted message. This change provides neither
compression nor automatic retention, quotas, or deletion. Long-running
capacity/soak checks remain in the production plan (`specprefill-production-plan.md`). Existing model accuracy and latency baselines
are unchanged; storage tests do not establish a new live inference speedup.

## Verification

The full offline suite passed with 536 tests and 5 skips; Ruff passed for the
changed runtime and tests. Seventeen storage cases cover memory-backend parity,
Unicode/revisions, lazy restore, cache bounds, deep manifests, corruption, rollback,
legacy migration, interrupted publication, and embedding reuse. A 160-source test
walks more than 300 KB of raw text through a 16 KiB payload cache with less than
1 MB peak traced Python allocation during the chunk walk; SQLite and OS pages are
outside that measurement. No live inference latency benchmark was rerun.
