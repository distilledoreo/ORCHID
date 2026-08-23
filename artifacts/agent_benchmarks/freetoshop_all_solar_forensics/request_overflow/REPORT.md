# Request overflow reconstruction

The exact generation-4 consolidator run was `run_d6bbece5107c4bd2a50da6f1cb24f01f` for job
`job_1ec4b2339e0c4ac4a4a302a891c78286`. The provider rejected **547,802** input
tokens against a **524,288** limit before returning usage. The persisted client
profile recorded **1,524,039 input-payload characters** and request body hash
`9ffb96fe3c2588bd399f32252104d7fef1b40f3f8f16c799ff3961b1cd3a189e`.

The request was a **consolidator input**, not foreground context, selector
input, canonicalizer input, or a retry payload. It covered 93 raw events,
144,623 stored source tokens, and 245 selected source items. The same selected
item payload was present once under `selected_events` and again under
`lossless_packet.authoritative_events`; the canonicalizer aggregate was also
embedded as `lossless_packet.canonical_text`.

Component counts are estimator-based because the provider rejected before
component usage telemetry was available. The exact provider total and exact
request profile are preserved in `TOKEN_BREAKDOWN.json`.
