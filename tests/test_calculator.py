import math
import time
import unittest

from src.calculator import (
    CalculationError,
    eval_expression,
    format_result,
    locale_decimal_separator,
    looks_like_calculation,
)


def calc(expr, sep="."):
    return format_result(eval_expression(expr, decimal_separator=sep), decimal_separator=sep)


class ArithmeticTests(unittest.TestCase):
    def test_basic_arithmetic(self):
        self.assertEqual(eval_expression("12*8"), 96)
        self.assertEqual(eval_expression("2^10"), 1024)
        self.assertEqual(eval_expression("(1+2)*3"), 9)
        self.assertEqual(eval_expression("-3+5"), 2)
        self.assertEqual(eval_expression("7//2"), 3)

    def test_multiply_and_divide_symbols(self):
        self.assertEqual(eval_expression("3x4"), 12)
        self.assertEqual(eval_expression("3 X 4"), 12)
        self.assertEqual(eval_expression("3×4"), 12)
        self.assertEqual(eval_expression("(2)x(3)"), 6)
        self.assertEqual(eval_expression("12÷4"), 3)

    def test_constants_and_functions(self):
        self.assertAlmostEqual(eval_expression("pi"), math.pi)
        self.assertAlmostEqual(eval_expression("tau/2"), math.pi)
        self.assertAlmostEqual(eval_expression("ln(e)"), 1)
        self.assertAlmostEqual(eval_expression("log(1000)"), 3)
        self.assertAlmostEqual(eval_expression("log10(100)"), 2)
        self.assertAlmostEqual(eval_expression("log(8, 2)"), 3)
        self.assertAlmostEqual(eval_expression("exp(0)"), 1)
        self.assertAlmostEqual(eval_expression("asin(1)"), math.pi / 2)
        self.assertAlmostEqual(eval_expression("acos(1)"), 0)
        self.assertAlmostEqual(eval_expression("atan(0)"), 0)
        self.assertEqual(eval_expression("floor(2.7)"), 2)
        self.assertEqual(eval_expression("ceil(2.1)"), 3)
        self.assertEqual(eval_expression("sqrt(16)"), 4)
        self.assertEqual(eval_expression("round(2.567, 2)"), 2.57)
        self.assertEqual(eval_expression("SQRT(16)"), 4)

    def test_percent_after_a_number_and_modulo_between_numbers(self):
        self.assertEqual(calc("50*20%"), "10")
        self.assertEqual(calc("20%"), "0.2")
        self.assertEqual(calc("20% of 50"), "10")
        self.assertEqual(calc("200+10%"), "220")
        self.assertEqual(calc("200-10%"), "180")
        self.assertEqual(calc("(100+50)+10%"), "165")
        self.assertEqual(calc("(10+10)%*50"), "10")
        self.assertEqual(calc("sqrt(400)%"), "0.2")
        self.assertEqual(calc("2^50%"), str(format_result(2 ** 0.5)))
        self.assertEqual(eval_expression("10%3"), 1)
        self.assertEqual(eval_expression("10 % 3"), 1)
        self.assertEqual(eval_expression("10 % (4)"), 2)

    def test_comma_decimal_separator(self):
        self.assertEqual(calc("3,5*2", ","), "7")
        self.assertEqual(calc("1,5+1", ","), "2,5")
        self.assertEqual(calc("log(8;2)", ","), "3")
        self.assertEqual(calc("1.5+1", ","), "2,5")
        self.assertIn(locale_decimal_separator(), (".", ","))


class LimitTests(unittest.TestCase):
    def assertRejectedQuickly(self, expr, message="Number too large"):
        start = time.perf_counter()
        with self.assertRaises(CalculationError) as caught:
            eval_expression(expr)
        self.assertLess(time.perf_counter() - start, 0.1, expr)
        self.assertEqual(str(caught.exception), message)

    def test_huge_powers_are_refused_at_once(self):
        for expr in ("9^9^9", "2^2^2^2^2", "10**10**10", "(10**500)**(10**4)", "(10**999)*(10**999)",
                     "99999999999^99999", "2.0**10**9", "(10**999)**0.5", "10**999*1.5"):
            self.assertRejectedQuickly(expr)

    def test_big_but_allowed_results(self):
        self.assertEqual(eval_expression("2^100"), 2**100)
        self.assertEqual(calc("2^100"), "1.26765060023e30")
        self.assertEqual(calc("10^999"), "1e999")
        self.assertEqual(eval_expression("2**-3"), 0.125)

    def test_long_input_is_refused(self):
        self.assertRejectedQuickly("1+" * 150 + "1", "Calculation too long")

    def test_round_digits_are_bounded(self):
        self.assertRejectedQuickly("round(5, -1000000)", "Not a valid calculation")

    def test_plain_error_messages(self):
        cases = {
            "1/0": "Can't divide by zero",
            "5%0": "Can't divide by zero",
            "0**-1": "Can't divide by zero",
            "sqrt(-1)": "Not a number",
            "(-8)**(1/3)": "Not a number",
            "hello": "Not a valid calculation",
            "__import__('os')": "Not a valid calculation",
            "True+1": "Not a valid calculation",
            "3j": "Not a valid calculation",
            "2+": "Not a valid calculation",
            "": "Not a valid calculation",
        }
        for expr, message in cases.items():
            with self.subTest(expr=expr):
                with self.assertRaises(CalculationError) as caught:
                    eval_expression(expr)
                self.assertEqual(str(caught.exception), message)
                self.assertIsInstance(caught.exception, ValueError)


class FormatTests(unittest.TestCase):
    def test_results_read_naturally(self):
        self.assertEqual(calc("0.1+0.2"), "0.3")
        self.assertEqual(calc("10/2"), "5")
        self.assertEqual(calc("1/3"), "0.333333333333")
        self.assertEqual(calc("-0.0"), "0")
        self.assertEqual(format_result(1e-20), "1e-20")
        self.assertEqual(format_result(1.5e20), "1.5e20")
        self.assertEqual(format_result(123), "123")
        self.assertEqual(format_result(2.5, ","), "2,5")

    def test_not_a_number_is_refused(self):
        with self.assertRaisesRegex(CalculationError, "Not a number"):
            format_result(float("nan"))
        with self.assertRaisesRegex(CalculationError, "Number too large"):
            format_result(float("inf"))


class LooksLikeCalculationTests(unittest.TestCase):
    def test_words_and_lone_constants_are_not_calculations(self):
        for text in ("e", "pi", "ln", "tau", "hello", "notepad", "mp3 player", "", "   ", "sqrt"):
            with self.subTest(text=text):
                self.assertFalse(looks_like_calculation(text))

    def test_calculations(self):
        for text in ("12*8", "7", "pi*2", "pi/e", "-pi", "sqrt(2)", "sqrt(pi)", "3x4", "20% of 50", "3,5+1"):
            with self.subTest(text=text):
                self.assertTrue(looks_like_calculation(text))


if __name__ == "__main__":
    unittest.main()
