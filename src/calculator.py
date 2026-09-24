"""Safe calculator for the search box.

Every keystroke that matches no command runs through here on the UI thread,
so every operation is bounded: input length is capped, and powers and
products are checked before they are computed so something like ``9^9^9``
is refused at once instead of freezing Dash.

What it understands:

* ``+ - * / // ^ **`` and parentheses. ``x``, ``X`` and ``×`` multiply
  between numbers (``3x4``), ``÷`` divides.
* ``%`` right after a number or a closing parenthesis, when it is followed
  by the end, a ``)`` or another operator, means percent: ``50*20%`` is 10,
  ``20% of 50`` is 10. Added to or taken from a value it is a share of that
  value, as on any pocket calculator: ``200+10%`` is 220 and ``200-10%``
  is 180. Between two numbers it is
  the remainder: ``10 % 3`` is 1.
* Constants ``pi``, ``e`` and ``tau``.
* Functions ``sqrt``, ``abs``, ``round``, ``floor``, ``ceil``, ``exp``,
  ``ln`` (natural log), ``log`` (base 10, or ``log(x, base)``), ``log10``,
  ``log2`` and the trigonometric ``sin``, ``cos``, ``tan``, ``asin``,
  ``acos``, ``atan``, which work in radians.
* The locale's decimal separator: with ``decimal_separator=","`` the input
  ``3,5`` is 3.5 and function arguments are separated with ``;``.

Errors are raised as :class:`CalculationError` (a ``ValueError``) with a
short message that is safe to show as is.
"""
import ast
import logging
import math
import operator as op
import re
from decimal import Decimal

log = logging.getLogger(__name__)

MAX_INPUT_LENGTH = 200
# Integer results are kept below this many decimal digits. Anything bigger is
# not useful to read in a search box and gets slow to compute and print.
MAX_RESULT_DIGITS = 1000
MAX_RESULT_BITS = int(MAX_RESULT_DIGITS * math.log2(10)) + 1
MAX_ROUND_DIGITS = 100
# Integers up to this many digits are shown in full; longer ones in
# scientific notation.
MAX_PLAIN_INT_DIGITS = 20
SIGNIFICANT_DIGITS = 12

TOO_LARGE = "Number too large"
DIVIDE_BY_ZERO = "Can't divide by zero"
NOT_VALID = "Not a valid calculation"
NOT_A_NUMBER = "Not a number"
TOO_LONG = "Calculation too long"


class CalculationError(ValueError):
    """A calculation that cannot be shown; the message is plain language."""


def _check_int(value):
    if isinstance(value, int) and value.bit_length() > MAX_RESULT_BITS:
        raise CalculationError(TOO_LARGE)
    return value


def _check_float(value):
    if isinstance(value, complex):
        raise CalculationError(NOT_A_NUMBER)
    if isinstance(value, float):
        if math.isnan(value):
            raise CalculationError(NOT_A_NUMBER)
        if math.isinf(value):
            raise CalculationError(TOO_LARGE)
    return value


def _safe_pow(base, exponent):
    if isinstance(base, int) and isinstance(exponent, int):
        if exponent > 0 and abs(base) > 1:
            # log2(|base|) * exponent estimates the bits of the result.
            if exponent > MAX_RESULT_BITS or math.log2(abs(base)) * exponent > MAX_RESULT_BITS:
                raise CalculationError(TOO_LARGE)
        elif exponent < 0 and base == 0:
            raise CalculationError(DIVIDE_BY_ZERO)
        elif exponent < -MAX_RESULT_BITS and abs(base) > 1:
            return 0.0
    else:
        # Float powers are fast, but base**huge raises OverflowError, which
        # the caller turns into "Number too large". A huge int base with a
        # float exponent is converted to float first (and overflows there).
        if base == 0 and exponent < 0:
            raise CalculationError(DIVIDE_BY_ZERO)
    return _check_float(_check_int(op.pow(base, exponent)))


def _safe_mul(left, right):
    if isinstance(left, int) and isinstance(right, int):
        if left.bit_length() + right.bit_length() > MAX_RESULT_BITS + 1:
            raise CalculationError(TOO_LARGE)
    return _check_float(_check_int(left * right))


def _check_result(func):
    def checked(*args):
        return _check_float(_check_int(func(*args)))

    return checked


SUPPORTED_OPERATORS = {
    ast.Add: _check_result(op.add),
    ast.Sub: _check_result(op.sub),
    ast.Mult: _safe_mul,
    ast.Div: _check_result(op.truediv),
    ast.FloorDiv: _check_result(op.floordiv),
    ast.Pow: _safe_pow,
    ast.Mod: _check_result(op.mod),
    ast.USub: op.neg,
    ast.UAdd: op.pos,
}


def _log(value, base=None):
    if base is None:
        return math.log10(value)
    return math.log(value, base)


def _round(value, digits=None):
    if digits is None:
        return round(value)
    if not isinstance(digits, int) or abs(digits) > MAX_ROUND_DIGITS:
        raise CalculationError(NOT_VALID)
    return round(value, digits)


def _percent(value):
    return value / 100


SUPPORTED_FUNCTIONS = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "ln": math.log,
    "log": _log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
    "abs": abs,
    "round": _round,
    "floor": math.floor,
    "ceil": math.ceil,
}

# Internal only: postfix percent is rewritten to this call before parsing.
_PERCENT_FUNCTION = "_pct"

CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}

# Kept for callers that used the old names.
supported_operators = SUPPORTED_OPERATORS
supported_functions = SUPPORTED_FUNCTIONS

_KNOWN_WORDS = set(SUPPORTED_FUNCTIONS) | set(CONSTANTS) | {"x", "of"}
_OPERATOR_CHARS = "+-*/^%×÷"


def locale_decimal_separator() -> str:
    """The decimal separator of the user's locale, "." when Qt is unavailable."""
    try:
        from PyQt6.QtCore import QLocale

        separator = QLocale().decimalPoint()
    except Exception:
        return "."
    return "," if separator == "," else "."


def looks_like_calculation(text: str) -> bool:
    """True when `text` is worth trying as a calculation.

    Needs a digit, or an operator between operands, and every word must be
    a known function or constant. So ``e``, ``pi`` or ``ln`` on their own,
    or ordinary words, are never taken for a calculation, while ``12*8``,
    ``pi*2``, ``pi/e`` and ``sqrt(2)`` are.
    """
    text = (text or "").strip()
    if not text or len(text) > MAX_INPUT_LENGTH:
        return False
    words = re.findall(r"[A-Za-z_]+", text)
    if any(word.lower() not in _KNOWN_WORDS for word in words):
        return False
    if any(ch.isdigit() for ch in text):
        return True
    # No digits: only constants and functions, so it needs an operator with
    # something on its right ("pi*e", "-pi") or a call ("sqrt(pi)").
    if "(" in text and any(word.lower() in SUPPORTED_FUNCTIONS for word in words):
        return True
    return bool(re.search(r"[" + re.escape(_OPERATOR_CHARS) + r"]\s*[A-Za-z_(]", text))


def _find_open_paren(text: str, close_index: int) -> int:
    depth = 0
    for index in range(close_index, -1, -1):
        if text[index] == ")":
            depth += 1
        elif text[index] == "(":
            depth -= 1
            if depth == 0:
                return index
    return -1


_NUMBER = r"(?:\d+(?:[._]\d+)*\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)"
# A postfix % is followed by the end, a closing parenthesis, an argument
# separator or another operator; anything else makes it a remainder.
_POSTFIX_FOLLOW = r"(?=\s*(?:$|[)+\-*/^%,]))"


def _rewrite_percent(expr: str) -> str:
    expr = re.sub(r"%\s*of\b", "%*", expr, flags=re.IGNORECASE)
    # Number followed by a postfix percent.
    expr = re.sub(r"(?<![\w.])(" + _NUMBER + r")\s*%" + _POSTFIX_FOLLOW, _PERCENT_FUNCTION + r"(\1)", expr)
    # A parenthesised group (or call) followed by a postfix percent.
    while True:
        match = re.search(r"\)\s*%" + _POSTFIX_FOLLOW, expr)
        if match is None:
            return expr
        start = _find_open_paren(expr, match.start())
        if start < 0:
            return expr
        while start > 0 and (expr[start - 1].isalnum() or expr[start - 1] == "_"):
            start -= 1
        expr = f"{expr[:start]}{_PERCENT_FUNCTION}({expr[start:match.start() + 1]}){expr[match.end():]}"


def _prepare(expr: str, decimal_separator: str) -> str:
    expr = expr.strip()
    if decimal_separator == ",":
        expr = expr.replace(",", ".").replace(";", ",")
    expr = expr.replace("×", "*").replace("÷", "/")
    # "3x4", "3 x 4", "(2)x(3)": x or X between two operands multiplies.
    expr = re.sub(r"(?<=[\d.)])\s*[xX]\s*(?=[\d.(])", "*", expr)
    expr = _rewrite_percent(expr)
    # Use ** precedence instead of XOR's.
    return expr.replace("^", "**")


def eval_expression(expr, decimal_separator="."):
    """Safely evaluate a mathematical expression string.

    Returns an int or float. Raises :class:`CalculationError` with a short,
    plain message ("Can't divide by zero", "Number too large", "Not a valid
    calculation", ...) that never repeats the input.
    """
    if not isinstance(expr, str) or not expr.strip():
        raise CalculationError(NOT_VALID)
    if len(expr) > MAX_INPUT_LENGTH:
        raise CalculationError(TOO_LONG)
    try:
        tree = ast.parse(_prepare(expr, decimal_separator), mode="eval")
        return _check_float(_check_int(_eval(tree.body)))
    except CalculationError:
        raise
    except ZeroDivisionError:
        raise CalculationError(DIVIDE_BY_ZERO) from None
    except OverflowError:
        raise CalculationError(TOO_LARGE) from None
    except ValueError as error:
        # math raises "math domain error" for sqrt(-1), log(0) and the like.
        message = NOT_A_NUMBER if "domain" in str(error) else NOT_VALID
        raise CalculationError(message) from None
    except (SyntaxError, TypeError, KeyError, RecursionError, MemoryError):
        raise CalculationError(NOT_VALID) from None
    except Exception:
        log.debug("Calculation failed", exc_info=True)
        raise CalculationError(NOT_VALID) from None


def _eval(node):
    match node:
        # Numbers (int or float); bool is an int subclass and is not a number here.
        case ast.Constant(value) if isinstance(value, (int, float)) and not isinstance(value, bool):
            return _check_int(value)

        case ast.Name(id=name) if name.lower() in CONSTANTS:
            return CONSTANTS[name.lower()]

        case ast.BinOp(left, operator, right):
            operator_func = SUPPORTED_OPERATORS.get(type(operator))
            if operator_func is None:
                raise TypeError(type(operator).__name__)
            if isinstance(operator, (ast.Add, ast.Sub)) and _is_percent_call(right):
                # 200+10% means 200 plus 10% of 200, as on a pocket calculator.
                base = _eval(left)
                return operator_func(base, _check_float(base * _eval(right)))
            return operator_func(_eval(left), _eval(right))

        case ast.UnaryOp(operator, operand):
            operator_func = SUPPORTED_OPERATORS.get(type(operator))
            if operator_func is None:
                raise TypeError(type(operator).__name__)
            return operator_func(_eval(operand))

        case ast.Call(func=ast.Name(id=name), args=args, keywords=[]):
            if name == _PERCENT_FUNCTION and len(args) == 1:
                return _check_float(_percent(_eval(args[0])))
            function = SUPPORTED_FUNCTIONS.get(name.lower())
            if function is None:
                raise TypeError(name)
            return _check_float(_check_int(function(*[_eval(arg) for arg in args])))

        case _:
            raise TypeError(type(node).__name__)


def _is_percent_call(node) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == _PERCENT_FUNCTION


# Backwards-compatible name for the evaluator.
eval_ = _eval


def format_result(value, decimal_separator=".") -> str:
    """Show a calculation result the way a person would write it.

    Whole numbers have no ".0", other values are rounded to 12 significant
    digits with trailing zeros dropped (so 0.1+0.2 shows 0.3), and very large
    or small values use scientific notation. NaN and infinity raise
    :class:`CalculationError`.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalculationError(NOT_A_NUMBER)
    if isinstance(value, int):
        text = str(value)
        if len(text.lstrip("-")) > MAX_PLAIN_INT_DIGITS:
            text = _big_int_scientific(value)
    else:
        _check_float(value)
        if value == 0:
            text = "0"
        elif value.is_integer() and abs(value) < 10**15:
            text = str(int(value))
        else:
            text = _scientific(value)
    if decimal_separator != ".":
        text = text.replace(".", decimal_separator)
    return text


def _scientific(value: float) -> str:
    text = format(value, f".{SIGNIFICANT_DIGITS}g")
    if "e" in text:
        mantissa, exponent = text.split("e")
        text = f"{mantissa}e{int(exponent)}"
    return text


def _big_int_scientific(value: int) -> str:
    # Decimal rounds exactly, where float() would overflow past 1e308.
    mantissa, exponent = format(Decimal(value), f".{SIGNIFICANT_DIGITS - 1}e").split("e")
    if "." in mantissa:
        mantissa = mantissa.rstrip("0").rstrip(".")
    return f"{mantissa}e{int(exponent)}"
