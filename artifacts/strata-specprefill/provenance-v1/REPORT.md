# Matched provenance experiment

Bare, minimal, and detailed evidence frames receive identical archived source bytes and delivery order on every turn. Four existing selector-driven conversation schedules are replayed; four fresh controlled conversations test cross-file/version ambiguity, delayed old evidence, current-version queries, and repeated queries after a stale revision arrives. Each arm retains its own actual replies.

Bare frames contain only a generic EVIDENCE delimiter and exact content. Minimal frames add file, version, and current/historical status. Detailed frames use our existing source ID, file, version, chunk/byte ranges, original turn, and supersession metadata. No omission placeholders, separate source-status notices, or retrieval tools are enabled in any arm. Metadata embedded in original source content is preserved in all arms.

| Format | All correct | Existing replay | Fresh controlled | Fresh conflict turns | Stale-version errors (fresh) | UNKNOWN responses |
|---|---:|---:|---:|---:|---:|---:|
| bare | 22/36 | 12/16 | 10/20 | 2/12 | 4 | 7 |
| minimal | 34/36 | 15/16 | 19/20 | 11/12 | 0 | 2 |
| detailed | 36/36 | 16/16 | 20/20 | 12/12 | 0 | 0 |

| Format | Growing target TTFT | Growing delivery TTFT | Evidence tokens | Extra tokens vs bare | Growing turns with zero reuse |
|---|---:|---:|---:|---:|---:|---:|
| bare | 2.940 s | 3.045 s | 5013 | 0 | 0 |
| minimal | 3.131 s | 3.196 s | 6009 | 996 | 0 |
| detailed | 3.557 s | 3.716 s | 8128 | 3115 | 0 |

Detailed provenance was the most reliable format on this set. Minimal saved framing tokens but did not match detailed accuracy; keep the existing detailed default for now. This compares metadata bundles, so it does not establish which extra field (source ID, turn, ranges, or supersession) contributed to the difference.

Target TTFT measures native inference through its first answer token. Delivery TTFT also includes source archival, rendering, and tokenization. Frozen selector planning and model startup are excluded; these numbers must not be compared directly with the earlier end-to-end student-plus-target timings. Qwen3.5-0.8B remains resident alongside the same Flash-Next Swift IQ3_S target on the P100 16GB; target prefix caching is enabled. Format order rotates by conversation.

Audits: 36 identical evidence schedules, 108 exact prompt reconstructions, 417 exact delivered source byte spans, no duplicate deliveries. All 60 fresh prompts contain the complete required function definition, independently checked against the requested source revision. Failures on those probes cannot be explained by an omitted answer span. Previously delivered source chunks outside the reused native prefix: {'bare': 0, 'minimal': 0, 'detailed': 0}.

| Conversation | Bare correct | Minimal correct | Detailed correct |
|---|---:|---:|---:|
| cache-session-0 | 2 | 4 | 4 |
| cache-session-1 | 4 | 4 | 4 |
| held-recovery | 4 | 4 | 4 |
| held-versions | 2 | 3 | 4 |
| fresh-conflicts-0 | 2 | 4 | 5 |
| fresh-conflicts-1 | 3 | 5 | 5 |
| fresh-conflicts-2 | 2 | 5 | 5 |
| fresh-conflicts-3 | 3 | 5 | 5 |

Limitations: eight synthetic conversations, one greedy sample per format/turn, no real coding edits or tool-use workload. Fresh controlled schedules intentionally create ambiguous revisions without labels; they isolate provenance usefulness rather than estimate ordinary production failure rates. Selection is held fixed, not independently reevaluated by each student. Actual target answers can change later histories. Small latency differences need repeated measurements before being treated as meaningful. No prompts, raw responses, or machine paths are exported in this report.
