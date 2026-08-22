# Input amplification audit

The corrected 12K local canonicalizer replay retired 16155 estimated source tokens and sent 20060 model input tokens, an input amplification of 1.242x. It produced 1534 output tokens.

Fixed per-call overhead measured from request profiles was approximately 420 system-prompt characters, 48 wrapper characters, and 340–746 response-schema characters in the 12K run. The dynamic exact-ID enum adds bounded schema text proportional to the batch's source IDs; it is correctness-constraining rather than avoidable scaffolding.

Selector and consolidator amplification were not available from the local-only replay. The captured selector totals are preserved in `stage_summary.json` and `stage_profile.jsonl`.
