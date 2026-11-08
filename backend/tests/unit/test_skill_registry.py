import asyncio

import pytest
from pydantic import BaseModel

from jaxon.core.errors import ConflictError, NotFoundError
from jaxon.skills.base import Skill, SkillContext, SkillInput, SkillOutput, skill
from jaxon.skills.registry import SkillRegistry


class In(SkillInput):
    n: int


class Out(SkillOutput):
    n: int


@skill(name="test.double", description="double a number")
async def double(inp: In, ctx: SkillContext) -> Out:
    return Out(n=inp.n * 2)


@skill(name="test.slow", description="sleeps", timeout_seconds=0.05)
async def slow(inp: In, ctx: SkillContext) -> Out:
    await asyncio.sleep(1)
    return Out(n=0)


@skill(name="test.boom", description="raises")
async def boom(inp: In, ctx: SkillContext) -> Out:
    raise RuntimeError("kaboom")


@skill(name="test.danger", description="needs approval", requires_approval=True)
async def danger(inp: In, ctx: SkillContext) -> Out:
    return Out(n=inp.n)


class WrongOut(Skill[In, Out]):
    name = "test.wrong_out"
    description = "returns wrong type"
    Input = In
    Output = Out

    async def run(self, inp: In, ctx: SkillContext) -> Out:
        class Other(BaseModel):
            x: int = 1

        return Other()  # type: ignore[return-value]


@pytest.fixture
def reg() -> SkillRegistry:
    r = SkillRegistry()
    for s in (double, slow, boom, danger, WrongOut):
        r.register(s)
    return r


async def test_invoke_ok(reg, ctx):
    r = await reg.invoke("test.double", {"n": 21}, ctx)
    assert r.ok and r.output == {"n": 42} and r.duration_ms >= 0


async def test_invalid_input(reg, ctx):
    r = await reg.invoke("test.double", {"n": "x"}, ctx)
    assert not r.ok and r.error["code"] == "validation_failed"
    r = await reg.invoke("test.double", {"n": 1, "extra": 2}, ctx)
    assert not r.ok and r.error["code"] == "validation_failed"


async def test_timeout(reg, ctx):
    r = await reg.invoke("test.slow", {"n": 1}, ctx)
    assert r.error["code"] == "skill_timeout"


async def test_crash_is_contained(reg, ctx):
    r = await reg.invoke("test.boom", {"n": 1}, ctx)
    assert not r.ok and "kaboom" in r.error["message"]


async def test_approval_gate(reg, ctx):
    assert (await reg.invoke("test.danger", {"n": 1}, ctx)).error["code"] == "approval_required"
    assert (await reg.invoke("test.danger", {"n": 1}, ctx, approved=True)).ok


async def test_wrong_output_type(reg, ctx):
    r = await reg.invoke("test.wrong_out", {"n": 1}, ctx)
    assert r.error["code"] == "skill_error"


async def test_unknown_skill(reg, ctx):
    assert (await reg.invoke("nope", {}, ctx)).error["code"] == "not_found"
    with pytest.raises(NotFoundError):
        reg.get("nope")


def test_duplicate_name_conflict(reg):
    reg.register(double)  # same class: idempotent

    @skill(name="test.double", description="impostor")
    async def other(inp: In, ctx: SkillContext) -> Out:
        return Out(n=0)

    with pytest.raises(ConflictError):
        reg.register(other)


def test_invalid_definitions():
    with pytest.raises(TypeError):

        @skill(name="Bad Name", description="x")
        async def bad(inp: In, ctx: SkillContext) -> Out:
            return Out(n=0)

    with pytest.raises(TypeError):

        @skill(name="test.sync", description="x")
        def not_async(inp: In, ctx: SkillContext) -> Out:  # type: ignore[arg-type]
            return Out(n=0)


def test_builtin_discovery(registry):
    expected = {
        "core.echo",
        "core.current_time",
        "math.calculate",
        "text.extract_contacts",
        "web.fetch_url",
        "browser.page_text",
        "memory.remember",
        "memory.recall",
    }
    assert expected <= set(registry.names())
    m = type(registry.get("math.calculate")).manifest()
    assert m.input_schema["properties"]["expression"]["type"] == "string"
    assert m.input_schema.get("additionalProperties") is False


def test_manifest_filter_by_tag(registry):
    assert {m.name for m in registry.manifests("memory")} == {"memory.remember", "memory.recall"}


def test_discover_extra_package(tmp_path, monkeypatch):
    pkg = tmp_path / "acme_skills"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "greet.py").write_text(
        "from jaxon.skills.base import SkillInput, SkillOutput, SkillContext, skill\n"
        "class I(SkillInput):\n    name: str\n"
        "class O(SkillOutput):\n    msg: str\n"
        "@skill(name='acme.greet', description='greet')\n"
        "async def greet(inp: I, ctx: SkillContext) -> O:\n    return O(msg='hi ' + inp.name)\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    r = SkillRegistry()
    assert r.discover_package("acme_skills") == 1
    assert "acme.greet" in r
