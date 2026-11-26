# agents/

Agent definitions live in the database registry (`agent_specs`, phase 12, ADR-022) so they can be versioned, evaluated and activated by the owner. Growth OS's six agents import as drafts via `POST /tenants/{id}/agents/import-growth-os`.
