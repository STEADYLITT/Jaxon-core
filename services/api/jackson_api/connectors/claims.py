"""Shared checks for customer-facing copy (spec vol. 03: "Never invent reviews or product
claims"; vol. 03 influencer pipeline: "No impersonation or fake engagement")."""

import re

_RULES = [
    (r"\b\d(\.\d)?\s*(/\s*5\s*)?stars?\b|\b\d{2,}\+?\s*(five-star|5-star)?\s*reviews?\b",
     "review or rating claims must come from real reviews, not copy"),
    (r"\b(customers|people|users) (love|rave|swear)\b|\bas seen on\b",
     "social-proof claims need a real, citable source"),
    (r"\b(#\s?1|number one|best[- ]selling|best seller)\b",
     "ranking claims need a verifiable source"),
    (r"\b(clinically|scientifically) (proven|tested)|\bdoctor[- ]recommended\b|\bfda[- ]approved\b",
     "medical/scientific claims need substantiation and legal review"),
    (r"\b(cures?|treats?|prevents?) (cancer|disease|diabetes|anxiety|depression)\b",
     "health-cure claims are not allowed"),
    (r"\bguaranteed (results|income|profit|returns)\b|\bget rich\b|\brisk[- ]free\b",
     "guaranteed-results or risk-free claims are not allowed"),
    (r"\b(make|earn)\s+\$\s?\d[\d,]*k?\s*(a|per|/)\s*(day|week|month)\b"
     r"|\bguaranteed (to )?(win|profit)",
     "income promises are not allowed"),
    (r"\b(buy|get) (followers|likes|views)\b|\bfake reviews?\b|\bengagement pods?\b",
     "fake engagement is not allowed"),
]


def check_claims(text: str) -> list[str]:
    """Return problems found in customer-facing copy (empty list = clean)."""
    found = []
    for pattern, reason in _RULES:
        m = re.search(pattern, text or "", re.IGNORECASE)
        if m:
            found.append(f"'{m.group(0)}': {reason}")
    return found
