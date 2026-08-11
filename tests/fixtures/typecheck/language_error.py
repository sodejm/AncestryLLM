"""Intentional language-level type error for checker parity evaluation."""


def double(value: int) -> int:
    return value * 2


invalid_number: int = "not an integer"
double(invalid_number)
