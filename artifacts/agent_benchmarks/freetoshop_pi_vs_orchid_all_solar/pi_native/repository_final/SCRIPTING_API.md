# Scripting API

FreetoShop has **no public scripting API in Phase A**. Console objects,
component internals, workspace exports, document JSON, and command
implementations are private details, not stable automation interfaces.

The application does not evaluate scripts embedded in documents or metadata.
Users should not paste untrusted code into browser developer tools.

A future API needs a versioned schema and command surface, explicit user
consent, capabilities, isolation, cancellation and quotas, deterministic error
semantics, undo transaction rules, structured-clone-safe values, and tests that
prevent document or network access beyond granted permissions. Plugin and
scripting security must be designed together; see [THREAT_MODEL.md](THREAT_MODEL.md)
and [PLUGIN_SDK.md](PLUGIN_SDK.md).
