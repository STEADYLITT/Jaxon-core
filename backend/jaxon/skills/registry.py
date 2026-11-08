"""Skill registry: registration, discovery, validation and safe invocation."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import pkgutil
import time
from importlib.metadata import entry_points
from types import ModuleType
from typing import Any

from pydantic import BaseModel, ValidationError

from jaxon.core.errors import ConflictError, JaxonError, NotFoundError
from jaxon.core.logging import get_logger
from jaxon.skills.base import Skill, SkillContext, SkillManifest

log = get_logger(__name__)

ENTRY_POINT_GROUP = "jaxon.skills"


class SkillResult(BaseModel):
    skill: str
    ok: bool
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    duration_ms: float


class SkillRegistry:
    def __init__(self, default_timeout: float = 60.0) -> None:
        self._skills: dict[str, Skill[Any, Any]] = {}
        self.default_timeout = default_timeout

    # ---- registration -------------------------------------------------
    def register(self, skill_cls: type[Skill[Any, Any]], *, replace: bool = False) -> None:
        skill_cls.validate_definition()
        if skill_cls.name in self._skills and not replace:
            existing = type(self._skills[skill_cls.name])
            if existing is skill_cls:
                return
            raise ConflictError(
                f"Skill '{skill_cls.name}' already registered by {existing.__module__}"
            )
        self._skills[skill_cls.name] = skill_cls()

    def unregister(self, name: str) -> None:
        self._skills.pop(name, None)

    def discover_module(self, module: ModuleType) -> int:
        count = 0
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, Skill)
                and obj is not Skill
                and not inspect.isabstract(obj)
                and obj.__module__ == module.__name__
                and hasattr(obj, "name")
            ):
                self.register(obj)
                count += 1
        return count

    def discover_package(self, package: str) -> int:
        """Import every module under `package` and register the Skill classes found."""
        pkg = importlib.import_module(package)
        count = self.discover_module(pkg)
        for info in pkgutil.walk_packages(getattr(pkg, "__path__", []), prefix=f"{package}."):
            count += self.discover_module(importlib.import_module(info.name))
        log.info("skills.discovered", package=package, count=count)
        return count

    def discover_entry_points(self) -> int:
        """Load skills shipped by separately installed packages (entry point group jaxon.skills)."""
        count = 0
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            target = ep.load()
            if isinstance(target, ModuleType):
                count += self.discover_module(target)
            elif inspect.isclass(target) and issubclass(target, Skill):
                self.register(target)
                count += 1
        return count

    # ---- lookup -------------------------------------------------------
    def get(self, name: str) -> Skill[Any, Any]:
        try:
            return self._skills[name]
        except KeyError:
            raise NotFoundError(f"Unknown skill '{name}'") from None

    def __contains__(self, name: object) -> bool:
        return name in self._skills

    def __len__(self) -> int:
        return len(self._skills)

    def names(self) -> list[str]:
        return sorted(self._skills)

    def manifests(self, tag: str | None = None) -> list[SkillManifest]:
        out = [type(s).manifest() for _, s in sorted(self._skills.items())]
        return [m for m in out if tag is None or tag in m.tags]

    # ---- invocation ---------------------------------------------------
    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        ctx: SkillContext,
        *,
        approved: bool = False,
    ) -> SkillResult:
        start = time.perf_counter()

        def done(ok: bool, output: Any = None, error: dict[str, Any] | None = None) -> SkillResult:
            ms = round((time.perf_counter() - start) * 1000, 2)
            log.info(
                "skill.invoked",
                skill=name,
                ok=ok,
                duration_ms=ms,
                error_code=(error or {}).get("code"),
                run_id=str(ctx.run_id or ""),
            )
            return SkillResult(skill=name, ok=ok, output=output, error=error, duration_ms=ms)

        try:
            sk = self.get(name)
        except NotFoundError as e:
            return done(False, error=e.to_dict())
        cls = type(sk)
        if cls.requires_approval and not approved:
            return done(
                False,
                error={
                    "code": "approval_required",
                    "message": f"Skill '{name}' requires human approval",
                },
            )
        try:
            inp = cls.Input.model_validate(arguments)
        except ValidationError as e:
            return done(
                False,
                error={
                    "code": "validation_failed",
                    "message": "Invalid input",
                    "details": {"errors": e.errors(include_url=False, include_context=False)},
                },
            )
        timeout = cls.timeout_seconds or self.default_timeout
        try:
            async with asyncio.timeout(timeout):
                result = await sk.run(inp, ctx)
        except TimeoutError:
            return done(
                False,
                error={"code": "skill_timeout", "message": f"Skill '{name}' exceeded {timeout}s"},
            )
        except JaxonError as e:
            return done(False, error=e.to_dict())
        except Exception as e:  # skill bug: report, never crash the agent loop
            log.exception("skill.crashed", skill=name)
            return done(False, error={"code": "skill_error", "message": f"{type(e).__name__}: {e}"})
        if not isinstance(result, cls.Output):
            return done(
                False,
                error={
                    "code": "skill_error",
                    "message": f"Skill returned {type(result).__name__}, "
                    f"expected {cls.Output.__name__}",
                },
            )
        return done(True, output=result.model_dump(mode="json"))


def build_default_registry(
    extra_packages: list[str] | None = None, default_timeout: float = 60.0
) -> SkillRegistry:
    reg = SkillRegistry(default_timeout=default_timeout)
    reg.discover_package("jaxon.skills.builtin")
    for pkg in extra_packages or []:
        reg.discover_package(pkg)
    reg.discover_entry_points()
    return reg
