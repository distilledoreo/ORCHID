# Canonicalizer timeout analysis

The prior full replay timeout is preserved, while the controlled exact/context matrix recorded 0 timeout(s) in 24 requests. The exact request completed in the controlled trials, so input shape alone does not explain the failure. No evidence justifies blindly increasing the deadline; queue depth, KV-cache hits, and first-token progress were not exposed by the exercised API.
