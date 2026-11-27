"""Policy engine (spec vol. 04 "Autonomy policy", phase 07, ADR-017).

`evaluate()` is a pure function, and every input it trusts comes from the database:
the business's autonomy level, the run's remaining budget, and whether a human
approved this exact action. Nothing a model, a web page or a tool output says
can change those inputs. That is how "prompt injection cannot elevate grants"
is enforced.

Levels: 0 read-only · 1 drafts + simulations · 2 reversible sandbox changes ·
3 approved, bounded production workflows.
"""

import hashlib
import uuid
from dataclasses import dataclass
from enum import StrEnum


class Category(StrEnum):
    READ = "read"
    DRAFT = "draft"
    SIMULATION = "simulation"
    SANDBOX_CHANGE = "sandbox_change"
    SPEND = "spend"
    PUBLIC_POST = "public_post"
    OUTBOUND_CAMPAIGN = "outbound_campaign"
    PAYMENT = "payment"
    ACCOUNT_CREATION = "account_creation"
    PRODUCTION_CHANGE = "production_change"
    SENSITIVE_DATA = "sensitive_data"
    LIVE_TRADE = "live_trade"


# Vol. 04: these always need explicit approval, whatever the autonomy level.
ALWAYS_APPROVE = frozenset({
    Category.PUBLIC_POST, Category.OUTBOUND_CAMPAIGN, Category.PAYMENT,
    Category.ACCOUNT_CREATION, Category.PRODUCTION_CHANGE, Category.SENSITIVE_DATA,
})
# ADR-011: trading is research and simulation only. Not even an approval unlocks it.
NEVER = frozenset({Category.LIVE_TRADE})
# The lowest autonomy level at which a category may run without asking.
MIN_LEVEL = {
    Category.READ: 0,
    Category.DRAFT: 1,
    Category.SIMULATION: 1,
    Category.SANDBOX_CHANGE: 2,
    Category.SPEND: 3,
}


class Outcome(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class Action:
    category: Category
    description: str
    amount_cents: int = 0
    # Which launch channel this belongs to (shopify_publish, social, email, ads) and its
    # target (social account, email list). Used to match a Launch approval's scope. Not part
    # of the fingerprint: a per-action approval stays bound to the description.
    channel: str | None = None
    target: str | None = None

    def fingerprint(self, task_id: uuid.UUID) -> str:
        raw = f"{task_id}|{self.category}|{self.amount_cents}|{self.description}"
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason: str


def evaluate(
    action: Action,
    *,
    autonomy_level: int,
    budget_remaining_cents: int,
    approved: bool,
) -> Decision:
    if action.amount_cents < 0:
        return Decision(Outcome.DENY, "negative amounts are not allowed")
    if action.category in NEVER:
        return Decision(Outcome.DENY, f"{action.category} is disabled (simulation only, ADR-011)")
    if approved:
        return Decision(Outcome.ALLOW, "approved by an owner/admin")
    if action.category in ALWAYS_APPROVE:
        return Decision(Outcome.REQUIRE_APPROVAL, f"{action.category} always needs approval")
    needed = MIN_LEVEL[action.category]
    if autonomy_level < needed:
        return Decision(
            Outcome.REQUIRE_APPROVAL,
            f"{action.category} needs autonomy level {needed}; business is at {autonomy_level}",
        )
    if action.category == Category.SPEND and action.amount_cents > budget_remaining_cents:
        return Decision(
            Outcome.REQUIRE_APPROVAL,
            f"spend of {action.amount_cents}c exceeds remaining budget "
            f"{budget_remaining_cents}c",
        )
    return Decision(Outcome.ALLOW, f"within autonomy level {autonomy_level}")
