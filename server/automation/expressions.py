"""Constrained expression language for the JoeOS Automation Platform.

Conditions, mappings, and variable references are evaluated with a small,
documented, deterministic language. There is no ``eval``, no JavaScript, no
object-prototype access, no filesystem/network access, and no way to invoke
tools or services. Supported operators: equality, comparison, contains,
existence, boolean logic, and simple arithmetic over typed variables.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

_MAX_EXPRESSION_LENGTH = 1000
_MAX_OUTPUT_SIZE = 100_000

_TOKEN = re.compile(
    r"""
    \s*(?:
        (?P<number>-?\d+(?:\.\d+)?)
      | (?P<quote>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
      | (?P<bool>true|false)
      | (?P<null>null)
      | (?P<word>[A-Za-z_][A-Za-z0-9_.-]*)
      | (?P<op>==|!=|<=|>=|&&|\|\||[+\-*/<>()])
    )
    """,
    re.VERBOSE,
)
_OPS = {"==", "!=", "<", "<=", ">", ">=", "+", "-", "*", "/", "&&", "||", "(", ")"}

_MAX_PARSE_DEPTH = 32
_MAX_TOKENS = 64


class ExpressionError(RuntimeError):
    pass


def _lookup_variable(path: str, variables: Dict[str, Any]) -> Any:
    """Resolve a dotted variable path against the workflow variable store."""
    parts = path.split(".")
    current: Any = variables
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise ExpressionError("unknown variable %r" % path)
    return current


class _Token:
    __slots__ = ("kind", "value")

    def __init__(self, kind: str, value: Any) -> None:
        self.kind = kind
        self.value = value


def _tokenize(expression: str) -> list:
    if not isinstance(expression, str) or not expression.strip():
        raise ExpressionError("expression is empty.")
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise ExpressionError("expression is too long.")
    tokens: list = []
    position = 0
    length = len(expression)
    while position < length:
        match = _TOKEN.match(expression, position)
        if match is None or match.end() == position:
            raise ExpressionError("invalid character in expression.")
        position = match.end()
        kind = match.lastgroup
        if kind is None:
            continue
        value = match.group(kind)
        if kind == "number":
            tokens.append(_Token("number", float(value)))
        elif kind == "quote":
            raw = value[1:-1].replace('\\"', '"').replace("\\'", "'")
            tokens.append(_Token("string", raw))
        elif kind == "bool":
            tokens.append(_Token("bool", value == "true"))
        elif kind == "null":
            tokens.append(_Token("null", None))
        elif kind == "word":
            tokens.append(_Token("word", value))
        else:
            tokens.append(_Token("op", value))
    if len(tokens) > _MAX_TOKENS:
        raise ExpressionError("expression has too many tokens.")
    return tokens


class _Parser:
    def __init__(self, tokens: list) -> None:
        self._tokens = tokens
        self._index = 0
        self._depth = 0

    def peek(self) -> Optional[_Token]:
        if self._index < len(self._tokens):
            return self._tokens[self._index]
        return None

    def next(self) -> _Token:
        token = self.peek()
        if token is None:
            raise ExpressionError("unexpected end of expression.")
        self._index += 1
        return token

    def expect(self, op: str) -> None:
        token = self.next()
        if token.kind != "op" or token.value != op:
            raise ExpressionError("expected %r." % op)

    def parse(self, variables: Dict[str, Any]) -> Any:
        value = self.parse_or(variables)
        if self.peek() is not None:
            raise ExpressionError("unexpected trailing tokens.")
        return value

    def parse_or(self, variables: Dict[str, Any]) -> Any:
        self._enter()
        left = self.parse_and(variables)
        while self.peek() is not None and self.peek().kind == "op" and self.peek().value == "||":
            self.next()
            right = self.parse_and(variables)
            left = bool(left) or bool(right)
        self._exit()
        return left

    def parse_and(self, variables: Dict[str, Any]) -> Any:
        self._enter()
        left = self.parse_comparison(variables)
        while self.peek() is not None and self.peek().kind == "op" and self.peek().value == "&&":
            self.next()
            right = self.parse_comparison(variables)
            left = bool(left) and bool(right)
        self._exit()
        return left

    def parse_comparison(self, variables: Dict[str, Any]) -> Any:
        self._enter()
        left = self.parse_additive(variables)
        token = self.peek()
        if token is not None and token.kind == "op" and token.value in {"==", "!=", "<", "<=", ">", ">="}:
            operator = self.next().value
            right = self.parse_additive(variables)
            left = _compare(operator, left, right)
        self._exit()
        return left

    def parse_additive(self, variables: Dict[str, Any]) -> Any:
        self._enter()
        left = self.parse_multiplicative(variables)
        while self.peek() is not None and self.peek().kind == "op" and self.peek().value in {"+", "-"}:
            operator = self.next().value
            right = self.parse_multiplicative(variables)
            if operator == "+":
                if isinstance(left, str) or isinstance(right, str):
                    left = str(left) + str(right)
                else:
                    left = _number(left) + _number(right)
            else:
                left = _number(left) - _number(right)
        self._exit()
        return left

    def parse_multiplicative(self, variables: Dict[str, Any]) -> Any:
        self._enter()
        left = self.parse_primary(variables)
        while self.peek() is not None and self.peek().kind == "op" and self.peek().value in {"*", "/"}:
            operator = self.next().value
            right = self.parse_primary(variables)
            if operator == "*":
                left = _number(left) * _number(right)
            else:
                divisor = _number(right)
                if divisor == 0:
                    raise ExpressionError("division by zero.")
                left = _number(left) / divisor
        self._exit()
        return left

    def parse_primary(self, variables: Dict[str, Any]) -> Any:
        token = self.peek()
        if token is None:
            raise ExpressionError("unexpected end of expression.")
        if token.kind == "number":
            self.next()
            return token.value
        if token.kind == "string":
            self.next()
            return token.value
        if token.kind == "bool":
            self.next()
            return token.value
        if token.kind == "null":
            self.next()
            return None
        if token.kind == "word":
            self.next()
            word = str(token.value)
            if word == "now":
                return datetime.now(timezone.utc).isoformat()
            return _lookup_variable(word, variables)
        if token.kind == "op" and token.value == "(":
            self.next()
            value = self.parse_or(variables)
            self.expect(")")
            return value
        raise ExpressionError("unexpected token.")

    def _enter(self) -> None:
        self._depth += 1
        if self._depth > _MAX_PARSE_DEPTH:
            raise ExpressionError("expression nesting is too deep.")

    def _exit(self) -> None:
        self._depth -= 1


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ExpressionError("expected a numeric value.") from None


def _compare(operator: str, left: Any, right: Any) -> bool:
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    try:
        if operator == "<":
            return left < right
        if operator == "<=":
            return left <= right
        if operator == ">":
            return left > right
        if operator == ">=":
            return left >= right
    except TypeError:
        raise ExpressionError("incomparable values.") from None
    raise ExpressionError("unknown operator %r." % operator)


def evaluate_condition(expression: str, variables: Dict[str, Any]) -> bool:
    """Evaluate a boolean condition expression against typed variables."""
    value = evaluate_expression(expression, variables)
    return bool(value)


def evaluate_expression(expression: str, variables: Dict[str, Any]) -> Any:
    """Evaluate a constrained expression and return its value."""
    tokens = _tokenize(expression)
    parser = _Parser(tokens)
    value = parser.parse(variables or {})
    if isinstance(value, str) and len(value) > _MAX_OUTPUT_SIZE:
        raise ExpressionError("expression output is too large.")
    return value


def contains(expression: str, value: Any, variables: Dict[str, Any]) -> bool:
    """Support 'x contains value' style checks used in conditions."""
    item = evaluate_expression(expression, variables)
    if isinstance(item, (list, tuple, dict, str)):
        return value in item
    return False