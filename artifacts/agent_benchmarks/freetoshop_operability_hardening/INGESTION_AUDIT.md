# Ingestion audit

## Measured facts

- Captured authoritative rows: `172` (`86` request rows).
- Request payload tokens: `8691344`.
- Existing stored event tokens: `10607383`.
- Reconstructed unique logical conversational events: `199` / `166107` tokens.
- Reused resent-prefix tokens: `7804154`.
- Request-payload amplification over the exact ordered replay: `52.32x`.
- Existing event-journal amplification over the replay: `63.86x`.

## Interpretation

The captured gateway did not append each message as a separate event. It stored each full request payload and response blob, so repeated OpenAI history prefixes were retained inside successive request events. The new ingestion path uses exact ordered canonical message hashes and stores only the novel suffix; it does not fuzzy-deduplicate legitimate repeated messages.
