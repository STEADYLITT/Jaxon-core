"""Error hierarchy. Every JaxonError maps to a stable error code and HTTP status."""

from __future__ import annotations

from typing import Any


class JaxonError(Exception):
    code: str = "internal_error"
    status_code: int = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class NotFoundError(JaxonError):
    code = "not_found"
    status_code = 404


class ValidationFailed(JaxonError):
    code = "validation_failed"
    status_code = 422


class ConflictError(JaxonError):
    code = "conflict"
    status_code = 409


class Unauthorized(JaxonError):
    code = "unauthorized"
    status_code = 401


class ConfigurationError(JaxonError):
    """A required setting / credential is missing."""

    code = "configuration_error"
    status_code = 503


class PermissionDenied(JaxonError):
    code = "permission_denied"
    status_code = 403


class SkillError(JaxonError):
    code = "skill_error"
    status_code = 500


class SkillTimeout(SkillError):
    code = "skill_timeout"
    status_code = 504


class ProviderError(JaxonError):
    code = "provider_error"
    status_code = 502


class DependencyUnavailable(JaxonError):
    code = "dependency_unavailable"
    status_code = 503
