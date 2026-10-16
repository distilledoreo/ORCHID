# ARM C second-batch stall reproduction

Prior recorded second-batch hash: `e4fa098579b59eb99d73183a982eea0b02a06d9df93a4bf4d33cc32d08a9509b`.

Controlled trials: 2.

The request is considered an exact-payload reproduction only when `prior_hash_match` is true; otherwise the report treats it as a same-policy/same-source reconstruction.

Results are preserved in `trials.jsonl`; no timeout threshold was changed and no indefinite retry was used.
