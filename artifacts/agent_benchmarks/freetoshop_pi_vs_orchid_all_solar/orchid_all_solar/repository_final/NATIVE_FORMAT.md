# FreetoShop native document format

The user-facing native extension is `.freetoshop` and its MIME type is
`application/x-freetoshop`. Format version 1 is a checksummed binary container
around the canonical serialized document payload. It preserves every layer,
mask, channel, selection, effect, smart-object source, smart filter, transform,
and structured shape/text property represented by the current document model.

## Version 1 container

All integers are unsigned and big-endian.

| Offset | Size | Meaning                                    |
| -----: | ---: | ------------------------------------------ |
|      0 |   11 | ASCII `FREETOSHOP` followed by a zero byte |
|     11 |    2 | Container format version (`1`)             |
|     13 |    4 | UTF-8 payload length                       |
|     17 |   32 | SHA-256 digest of the payload              |
|     49 |    N | UTF-8 canonical document JSON              |

The container version and the document schema version are independent. The
container controls framing and integrity; the payload's `schemaVersion`
controls document migration. Readers reject unsupported container versions,
invalid lengths, invalid UTF-8, checksum mismatches, malformed JSON, and invalid
document invariants before replacing the open document.

The checksum detects corruption; it is not a signature and provides no
authenticity guarantee. Document data remains local and is never executed.

## Current limits and save behavior

- Maximum file size: 512 MiB.
- Schema-20 resources below half-full encode as `sparse-tiles-v1`; denser pixel
  and coverage stores stream row-major base64 from bounded stripes without a
  contiguous store copy. Coverage sparse payloads include their implicit
  `defaultCoverage`, preserving mostly-full selection/mask complements with only
  deviation tiles. Reopening reconstructs canonical tiled stores. The JSON
  envelope and encoded strings remain full in memory.
- Encoding and decoding run in a cancellable module worker.
- Save downloads a complete snapshot after its checksum is complete.
- Open validates the signature from bytes rather than trusting MIME type or
  extension.
- Ctrl/Command+S invokes native Save document.

## Local recovery loading

IndexedDB recovery stores the schema-20 manifest separately from its
content-addressed pixel and coverage resources. Repository consumers can call
`prepareLoad(id)` to inspect validated document identity, name, dimensions,
top-level layer count, and referenced-resource count without reading any tile
blobs. Calling `hydrate()` on the prepared result then reads each referenced
resource, verifies its SHA-256 identity, and constructs the editable document.
Hydration is memoized per prepared result. The compatibility `load(id)` method
performs both phases, and legacy JSON records use the same metadata-first
contract before their deferred deserialization.

Version 1 is intentionally uncompressed and not incremental. It does not yet
provide atomic writes through the File System Access API, content-addressed
tiles, partial loading, encryption, digital signatures, or preservation of
unknown fields from future schema versions. Those are format-evolution targets,
not implied capabilities of version 1.
