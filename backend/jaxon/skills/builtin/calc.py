"""Safe arithmetic evaluator (AST whitelist - never uses eval)."""

from __future__ import annotations

import ast
import math as _m
import operator
from collections.abc import Callable
from typing import Any

from pydantic import Field

from jaxon.core.errors import ValidationFailed
from jaxon.skills.base import SkillContext, SkillInput, SkillOutput, skill

_BIN: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCS: dict[str, Callable[..., Any]] = {
    "sqrt": _m.sqrt,
    "log": _m.log,
    "log10": _m.log10,
    "exp": _m.exp,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "floor": _m.floor,
    "ceil": _m.ceil,
}
_CONSTS = {"pi": _m.pi, "e": _m.e}


def safe_eval(expr: str) -> float | int:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise ValidationFailed(f"Invalid expression: {expr!r}") from None

    def ev(node: ast.AST) -> Any:
        match node:
            case ast.Expression(body=b):
                return ev(b)
            case ast.Constant(value=v) if isinstance(v, int | float) and not isinstance(v, bool):
                return v
            case ast.Name(id=n) if n in _CONSTS:
                return _CONSTS[n]
            case ast.BinOp(left=lft, op=op, right=r) if type(op) in _BIN:
                a, b = ev(lft), ev(r)
                if isinstance(op, ast.Pow) and abs(b) > 1000:
                    raise ValidationFailed("Exponent too large")
                return _BIN[type(op)](a, b)
            case ast.UnaryOp(op=op, operand=o) if type(op) in _UNARY:
                return _UNARY[type(op)](ev(o))
            case ast.Call(func=ast.Name(id=f), args=args, keywords=[]) if f in _FUNCS:
                return _FUNCS[f](*[ev(a) for a in args])
        raise ValidationFailed(f"Unsupported expression element: {type(node).__name__}")

    try:
        return ev(tree)
    except ZeroDivisionError:
        raise ValidationFailed("Division by zero") from None
    except (OverflowError, ValueError) as e:
        raise ValidationFailed(f"Math error: {e}") from None


class CalcIn(SkillInput):
    expression: str = Field(description="Arithmetic, e.g. '(1200*0.15)+sqrt(16)'", max_length=500)


class CalcOut(SkillOutput):
    expression: str
    result: float | int


@skill(
    name="math.calculate",
    description="Evaluate an arithmetic expression exactly. Supports + - * / // % **, "
    "parentheses, sqrt, log, log10, exp, abs, round, min, max, floor, ceil, pi, e.",
    tags=("math", "analysis"),
)
async def calculate(inp: CalcIn, ctx: SkillContext) -> CalcOut:
    return CalcOut(expression=inp.expression, result=safe_eval(inp.expression))
