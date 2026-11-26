# apps/console — Owner console (Next.js)

**Status: hosted console not built yet.** Since phase 19 an interim owner console is served by the API at `/console` (dashboard, approvals inbox, global stop, live visual work, connectors, audit), reading everything from `GET /tenants/{id}/dashboard`. This Next.js app replaces it once Clerk sign-in is configured (ADR-012, ADR-029), importing types from `@jackson-os/contracts`.
