"""Deterministic MOCK provider, used whenever no real provider is configured.

Every response is marked mode="mock" and costs nothing. Its output is obviously a
placeholder, so it can't be mistaken for real analysis.
"""

import json

from .base import ModelRequest, ModelResponse


class MockProvider:
    name = "mock"

    def available(self) -> bool:
        return True

    def complete(self, req: ModelRequest, model: str) -> ModelResponse:
        if req.json_schema is not None and "steps" in req.json_schema.get("properties", {}):
            text = json.dumps({
                "summary": "[MOCK] No AI model is connected, so this plan is a placeholder.",
                "steps": [
                    {"title": "[MOCK] Connect a model provider (ANTHROPIC_API_KEY)",
                     "category": "read", "why": "Needed before any real planning."},
                ],
                "assumptions": ["[MOCK] none: no model was consulted"],
                "risks": ["[MOCK] this output is not real analysis"],
                "evidence_needed": [],
                "memory_used": [],
            })
        elif req.json_schema is not None:
            text = json.dumps(_fill(req.json_schema))
        else:
            text = "[MOCK] No AI model is connected."
        return ModelResponse(text=text, provider=self.name, model="mock", mode="mock",
                             stop_reason="end_turn", notes=["mock provider: no model called"])


def _fill(schema: dict):
    """Smallest value that satisfies a JSON schema, with [MOCK] markers in strings."""
    t = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if t == "object":
        return {k: _fill(v) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        return []
    if t == "string":
        return "[MOCK] no model connected"
    if t in ("integer", "number"):
        return 0
    if t == "boolean":
        return False
    return None
