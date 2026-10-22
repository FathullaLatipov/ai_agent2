"""Safe calculator: AST-whitelisted arithmetic. No eval, no names except allowed functions."""

import ast
import math
import operator

from pydantic import BaseModel, Field

from app.tools.base import ToolContext, ToolInputError, ToolSpec
from app.tools.registry import registry

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
}
_MAX_POW = 1000


def _eval_node(node: ast.AST) -> float:
    match node:
        case ast.Expression(body=body):
            return _eval_node(body)
        case ast.Constant(value=value) if isinstance(value, (int, float)) and not isinstance(
            value, bool
        ):
            return value
        case ast.BinOp(left=left, op=op, right=right) if type(op) in _BIN_OPS:
            lv, rv = _eval_node(left), _eval_node(right)
            if isinstance(op, ast.Pow) and (abs(lv) > _MAX_POW or abs(rv) > _MAX_POW):
                raise ToolInputError("exponent too large")
            return _BIN_OPS[type(op)](lv, rv)
        case ast.UnaryOp(op=op, operand=operand) if type(op) in _UNARY_OPS:
            return _UNARY_OPS[type(op)](_eval_node(operand))
        case ast.Call(func=ast.Name(id=name), args=args, keywords=[]) if name in _FUNCTIONS:
            return _FUNCTIONS[name](*[_eval_node(a) for a in args])
        case _:
            raise ToolInputError(f"unsupported expression element: {type(node).__name__}")


class CalculatorInput(BaseModel):
    expression: str = Field(min_length=1, max_length=500, description="Arithmetic expression")


class CalculatorOutput(BaseModel):
    expression: str
    result: float


async def _calculate(_ctx: ToolContext, args: CalculatorInput) -> CalculatorOutput:
    try:
        tree = ast.parse(args.expression, mode="eval")
    except SyntaxError as exc:
        raise ToolInputError(f"invalid expression: {exc.msg}") from exc
    try:
        value = _eval_node(tree)
    except ZeroDivisionError as exc:
        raise ToolInputError("division by zero") from exc
    except (ValueError, OverflowError) as exc:
        raise ToolInputError(f"math error: {exc}") from exc
    return CalculatorOutput(expression=args.expression, result=float(value))


registry.register(
    ToolSpec(
        name="calculator.evaluate",
        description="Evaluate an arithmetic expression deterministically (no LLM math).",
        input_model=CalculatorInput,
        output_model=CalculatorOutput,
        handler=_calculate,
        timeout_s=5.0,
    )
)
