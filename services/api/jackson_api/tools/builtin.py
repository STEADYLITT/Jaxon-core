"""Built-in tools. More connectors (Shopify draft, content, CRM) arrive in phases 15-18."""

import ast
import operator
import os
import resource
import subprocess
import sys
import tempfile
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from ..policy import Category
from ..worker_errors import NonRetryableError
from . import ToolEnv, ToolSpec, register

# --- math.calculate: arithmetic without eval ------------------------------------------------

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
        ast.USub: operator.neg, ast.UAdd: operator.pos, ast.FloorDiv: operator.floordiv}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 64:
            raise NonRetryableError("exponent too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise NonRetryableError(f"unsupported expression element: {type(node).__name__}")


class CalcIn(BaseModel):
    expression: str = Field(min_length=1, max_length=500)


def calculate(args: CalcIn, env: ToolEnv) -> dict:
    try:
        tree = ast.parse(args.expression, mode="eval")
    except SyntaxError as e:
        raise NonRetryableError(f"not an arithmetic expression: {e.msg}") from None
    return {"expression": args.expression, "result": _eval(tree.body)}


register(ToolSpec(
    name="math.calculate", description="Exact arithmetic (+ - * / ** %). No variables or calls.",
    input_model=CalcIn, category=Category.READ, run=calculate, timeout_s=2,
    verification="deterministic: same input gives same output"))


# --- web.fetch: allowlisted, read-only page fetch ------------------------------------------------

class FetchIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


MAX_BYTES = 1_000_000


def fetch(args: FetchIn, env: ToolEnv) -> dict:
    """GET one page from a domain the owner allowlisted on the grant. No redirects
    (a redirect could leave the allowlist), text content only, size-capped.
    The text is UNTRUSTED: it may contain instructions aimed at the agent."""
    parsed = urlparse(args.url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise NonRetryableError("only http(s) URLs are allowed")
    allowed = [d.lower() for d in env.grant_config.get("allowed_domains", [])]
    host = parsed.hostname.lower()
    if not any(host == d or host.endswith("." + d) for d in allowed):
        raise NonRetryableError(f"domain {host} is not on this grant's allowlist")
    with httpx.Client(follow_redirects=False, timeout=8.0) as c:
        with c.stream("GET", args.url, headers={"User-Agent": "JacksonOS/0.1 (+research)"}) as r:
            if r.is_redirect:
                return {"url": args.url, "status": r.status_code,
                        "redirect_to": r.headers.get("location"), "text": ""}
            ctype = r.headers.get("content-type", "")
            if not ctype.startswith(("text/", "application/json")):
                raise NonRetryableError(f"refusing non-text content: {ctype}")
            body = b""
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    body = body[:MAX_BYTES]
                    break
    return {"url": args.url, "status": r.status_code, "content_type": ctype,
            "text": body.decode("utf-8", errors="replace"), "truncated": len(body) >= MAX_BYTES}


register(ToolSpec(
    name="web.fetch", description="Fetch one text page from an allowlisted domain (read-only).",
    input_model=FetchIn, category=Category.READ, run=fetch, timeout_s=10,
    verification="returns HTTP status and content type; caller cross-checks facts"))


# --- code.run_python: resource-limited subprocess -----------------------------------------------

SANDBOX_WALL_TIMEOUT_S = 8.0


class PythonIn(BaseModel):
    code: str = Field(min_length=1, max_length=20_000)


def _limits():  # runs in the child before exec
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 2**20, 512 * 2**20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 2**20, 2 * 2**20))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    os.setsid()


def run_python(args: PythonIn, env: ToolEnv) -> dict:
    """PROCESS-LEVEL isolation only: isolated interpreter, empty environment (no secrets),
    throwaway working dir, CPU/memory/file limits and a wall-clock timeout. This is not a
    security boundary for hostile code (network and filesystem reads are not blocked);
    production needs a container sandbox (phase 20)."""
    with tempfile.TemporaryDirectory(prefix="jackson-sbx-") as tmp:
        try:
            p = subprocess.run([sys.executable, "-I", "-S", "-c", args.code], cwd=tmp,
                               env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True,
                               timeout=SANDBOX_WALL_TIMEOUT_S, preexec_fn=_limits)
        except subprocess.TimeoutExpired:
            raise NonRetryableError(
                f"code ran longer than {SANDBOX_WALL_TIMEOUT_S}s") from None
    return {"exit_code": p.returncode, "stdout": p.stdout[-20_000:], "stderr": p.stderr[-5_000:]}


register(ToolSpec(
    name="code.run_python", description="Run a short Python snippet in a limited subprocess.",
    input_model=PythonIn, category=Category.SANDBOX_CHANGE, run=run_python, timeout_s=10,
    idempotent=False, mode="sandbox",
    verification="exit code + captured output; process-level isolation only"))


# --- browser.session: grant entry for the visual browser (used via ctx.browser()) ---------

class BrowserIn(BaseModel):
    pass


def _browser_via_ctx(args: BrowserIn, env: ToolEnv) -> dict:
    raise NonRetryableError("browser sessions are opened with ctx.browser(), not use_tool()")


register(ToolSpec(
    name="browser.session",
    description="Watched sandbox browser. Grant config: {\"allowed_domains\": [...]}. "
                "Reading is 'read'; click/fill are 'sandbox_change' (autonomy level 2+).",
    input_model=BrowserIn, category=Category.READ, run=_browser_via_ctx, timeout_s=600,
    idempotent=False, mode="sandbox",
    verification="screenshot artifact after every action; owner can pause/terminate"))


# --- finance.unit_economics: deterministic margin math for agents (phase 14) ----------------

def _unit_economics(args, env: ToolEnv) -> dict:
    from ..economics import calculate

    return calculate(args)


def _register_economics() -> None:
    from ..economics import EconomicsIn

    register(ToolSpec(
        name="finance.unit_economics",
        description="Per-order landed cost, contribution before/after ads, breakeven CAC and "
                    "ROAS, orders needed for a profit target, sensitivity. Estimates only.",
        input_model=EconomicsIn, category=Category.READ, run=_unit_economics, timeout_s=2,
        verification="deterministic Decimal math; assumptions listed in the output"))


_register_economics()


# --- shopify.drafts: grant entry for the Shopify connector (used via ctx.shopify()) --------

def _shopify_via_ctx(args: BrowserIn, env: ToolEnv) -> dict:
    raise NonRetryableError("Shopify is used through ctx.shopify(), not use_tool()")


register(ToolSpec(
    name="shopify.drafts",
    description="Create and verify DRAFT products on a Shopify store. Grant config: "
                "{\"mode\": \"mock\"} (default) or {\"mode\": \"shopify\", "
                "\"store_domain\": \"x.myshopify.com\", \"dev_store\": true}. "
                "Publishing always needs owner approval.",
    input_model=BrowserIn, category=Category.SANDBOX_CHANGE, run=_shopify_via_ctx,
    idempotent=True, mode="mock",
    verification="read back after create: product must still be DRAFT with the same handle"))
