# Plugin SDK

FreetoShop has **no plugin SDK in Phase A**. There is no supported manifest,
extension point, package format, permission model, sandbox, compatibility
version, or third-party distribution channel. Internal workspace APIs are not a
plugin contract and may change without migration support.

A future SDK requires, before enabling third-party code: a capability-based
permission model; process/realm isolation; document and network access rules;
resource/time limits; version negotiation; deterministic lifecycle and cleanup;
accessible UI extension rules; signing/revocation policy; and adversarial
security tests. Plugins must never gain ambient access to local documents,
credentials, clipboard, storage, or network.

Do not instruct users to paste third-party JavaScript into developer tools as a
plugin workaround.
