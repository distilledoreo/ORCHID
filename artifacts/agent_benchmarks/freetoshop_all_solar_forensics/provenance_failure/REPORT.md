# Provenance failure

Generation 3 returned the previous ACTIVE capsule watermark as an evidence ID.
That ID was not in the current frozen selector source universe, so the
consolidator adapter correctly rejected it. No fuzzy repair was added. The
corrected path keeps this software-owned exact-ID rejection; bounded windows
and the canonicalizer bypass remove the conditions that made the provider emit
the stale ID, but they do not make stale IDs legal.
