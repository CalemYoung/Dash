import ast
import operator as op
import math

supported_operators = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.FloorDiv: op.floordiv,
    ast.Pow: op.pow,
    ast.Mod: op.mod,
    ast.USub: op.neg,
}

supported_functions = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "abs": abs,
    "round": round,
}


def eval_expression(expr):
    """Safely evaluate a mathematical expression string"""
    try:
        expr = expr.replace("^", "**")  # use ** precedence instead of XOR's
        return eval_(ast.parse(expr, mode="eval").body)
    except KeyError as e:
        raise ValueError(f"Unsupported operator in expression: {expr}") from e
    except TypeError as e:
        raise ValueError(f"Invalid expression: {expr}") from e
    except ZeroDivisionError:
        raise ValueError("Division by zero")
    except Exception as e:
        raise ValueError(f"Error evaluating expression: {expr}") from e


def eval_(node):
    match node:
        # Numbers (int or float)
        case ast.Constant(value) if isinstance(value, (int, float)):
            return value

        # Binary operations: +, -, *, /, **, etc.
        case ast.BinOp(left, op, right):
            operator_func = supported_operators.get(type(op))
            if operator_func is None:
                raise TypeError(f"Unsupported binary operator: {type(op).__name__}")
            return operator_func(eval_(left), eval_(right))

        # Unary operations: -x, +x
        case ast.UnaryOp(op, operand):
            operator_func = supported_operators.get(type(op))
            if operator_func is None:
                raise TypeError(f"Unsupported unary operator: {type(op).__name__}")
            return operator_func(eval_(operand))

        # Function calls: sqrt(x), sin(x), etc.
        case ast.Call(func=ast.Name(id=name), args=args, keywords=[]):
            if name not in supported_functions:
                raise TypeError(f"Unsupported function: {name}")
            evaluated_args = [eval_(arg) for arg in args]
            return supported_functions[name](*evaluated_args)

        case _:
            raise TypeError(f"Unsupported operation: {ast.dump(node)}")
