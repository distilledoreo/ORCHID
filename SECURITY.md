# Security Policy

## Scope

ORCHID is a research prototype and is not production-ready. Do not use it with
untrusted provider credentials, sensitive conversations, or production data
without an independent security review.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's private vulnerability
reporting or a private security advisory. Do not open a public issue containing
credentials, raw prompts, database files, or exploit details.

Include the affected version or commit, reproduction steps, impact, and any
sanitized logs needed to investigate.

## Credential handling

Provider credentials belong in environment variables or an external secret
store. They must never be committed, placed in fixtures, included in telemetry,
or pasted into issue reports. Diagnostic excerpts intentionally redact
Authorization headers and common credential formats.
