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
        else:
            text = "[MOCK] No AI model is connected."
        return ModelResponse(text=text, provider=self.name, model="mock", mode="mock",
                             stop_reason="end_turn", notes=["mock provider: no model called"])
