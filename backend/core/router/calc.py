"""Safe arithmetic for instant answers. Parses with `ast`; never calls eval()."""

import ast
import math
import operator
import re
from collections.abc import Callable

MAX_CHARS = 200
MAX_EXPONENT = 1000
_BIN: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}


class CalcError(ValueError):
    pass


def _prepare(text: str) -> str:
    expr = text.strip().lower()
    expr = re.sub(r"(?<=\d),(?=\d{3}\b)", "", expr)  # 1,000 -> 1000
    expr = re.sub(r"(?<=[\d)])\s*[x\u00d7]\s*(?=[\d(])", "*", expr)  # 3 x 4
    expr = expr.replace("\u00f7", "/").replace("^", "**")
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*of\s*", r"(\1/100)*", expr)  # 15% of 200
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", expr)  # 200 * 15%
    return expr


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    ):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        value = _eval(node.operand)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow):
            if abs(right) > MAX_EXPONENT:
                raise CalcError("exponent too large")
            try:
                return math.pow(left, right)
            except (OverflowError, ValueError) as exc:
                raise CalcError("result out of range") from exc
        op = _BIN.get(type(node.op))
        if op is None:
            raise CalcError("unsupported operator")
        try:
            return op(left, right)
        except ZeroDivisionError as exc:
            raise CalcError("division by zero") from exc
    raise CalcError("not arithmetic")


def evaluate(text: str) -> float:
    if len(text) > MAX_CHARS:
        raise CalcError("too long")
    expr = _prepare(text)
    if not re.fullmatch(r"[\d\s.+\-*/%()]+", expr):
        raise CalcError("not arithmetic")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise CalcError("not arithmetic") from exc
    value = _eval(tree)
    if math.isnan(value) or math.isinf(value):
        raise CalcError("result out of range")
    return value


def format_number(value: float) -> str:
    if value.is_integer() and abs(value) < 1e15:
        return f"{int(value):,}"
    return format(value, ".10g")
