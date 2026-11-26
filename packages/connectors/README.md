# packages/connectors — External service connectors

**Status: none live.** Every connector starts as a clearly labeled MOCK and must report `mode: "mock"` in every result until the owner provides authorized credentials and approves the switch to `sandbox` or `live`. Planned: Shopify (phase 15), Whop, Apify, Upload-Post, ElevenLabs, FAL, model providers (phase 08). Verify current provider docs before implementing each one.


## Implemented
- **Shopify drafts** (phase 15): `services/api/jackson_api/connectors/shopify.py`, with a mock backend by default and a real Admin API 2026-10 backend behind a dev-store grant and `SHOPIFY_ADMIN_API_TOKEN`.
