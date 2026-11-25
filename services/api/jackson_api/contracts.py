"""Shared enums from spec volume 02 (Data contracts) and volume 04 (Command interface).

These MUST stay identical to packages/contracts/src/index.ts.
tests/test_contract_parity.py at the repo root enforces that.
"""

from enum import IntEnum, StrEnum


class TaskState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


TERMINAL_TASK_STATES = frozenset({TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELED})


class CommandType(StrEnum):
    PLAN = "PLAN"
    EXECUTE = "EXECUTE"
    SIMULATE = "SIMULATE"
    REPORT = "REPORT"
    OPTIMIZE = "OPTIMIZE"
    BUILD = "BUILD"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    STOP = "STOP"


class AutonomyLevel(IntEnum):
    READ_ONLY = 0
    DRAFTS_AND_SIMULATIONS = 1
    REVERSIBLE_SANDBOX = 2
    APPROVED_BOUNDED_PRODUCTION = 3


class EntityName(StrEnum):
    TENANT = "Tenant"
    USER = "User"
    BUSINESS = "Business"
    OPPORTUNITY = "Opportunity"
    AGENT_SPEC = "AgentSpec"
    AGENT_INSTANCE = "AgentInstance"
    SKILL_VERSION = "SkillVersion"
    TOOL_GRANT = "ToolGrant"
    WORKFLOW_RUN = "WorkflowRun"
    TASK = "Task"
    ARTIFACT = "Artifact"
    APPROVAL = "Approval"
    BUDGET = "Budget"
    LEDGER_ENTRY = "LedgerEntry"
    AUDIT_EVENT = "AuditEvent"
    MEMORY_RECORD = "MemoryRecord"
