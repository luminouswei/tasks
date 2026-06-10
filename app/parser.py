from dataclasses import dataclass
from typing import Any

from app.errors import ToolExecutionError


@dataclass
class Token:
    type: str
    value: float | None = None


def tokenize(expr: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0

    while i < len(expr):
        char = expr[i]

        if char.isspace():
            i += 1
            continue

        if char.isdigit() or char == ".":
            start = i
            dot_count = 0

            while i < len(expr) and (expr[i].isdigit() or expr[i] == "."):
                if expr[i] == ".":
                    dot_count += 1
                i += 1

            raw_number = expr[start:i]

            if dot_count > 1 or raw_number == ".":
                raise ToolExecutionError(f"parse error: invalid number '{raw_number}'")

            try:
                value = float(raw_number)
            except ValueError:
                raise ToolExecutionError(f"parse error: invalid number '{raw_number}'")

            tokens.append(Token("NUMBER", value))
            continue

        if char == "+":
            tokens.append(Token("PLUS"))
        elif char == "-":
            tokens.append(Token("MINUS"))
        elif char == "*":
            tokens.append(Token("STAR"))
        elif char == "/":
            tokens.append(Token("SLASH"))
        elif char == "(":
            tokens.append(Token("LPAREN"))
        elif char == ")":
            tokens.append(Token("RPAREN"))
        else:
            raise ToolExecutionError(f"parse error: invalid character '{char}'")

        i += 1

    return tokens


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def current(self) -> Token | None:
        if self.pos >= len(self.tokens):
            return None
        return self.tokens[self.pos]

    def advance(self) -> Token:
        token = self.current()
        if token is None:
            raise ToolExecutionError("parse error: unexpected end of expression")

        self.pos += 1
        return token

    def match(self, *types: str) -> bool:
        token = self.current()

        if token is None:
            return False

        if token.type not in types:
            return False

        self.pos += 1
        return True

    def expression(self) -> float:
        result = self.term()

        while True:
            if self.match("PLUS"):
                result += self.term()
            elif self.match("MINUS"):
                result -= self.term()
            else:
                break

        return result

    def term(self) -> float:
        result = self.factor()

        while True:
            if self.match("STAR"):
                result *= self.factor()
            elif self.match("SLASH"):
                divisor = self.factor()
                if divisor == 0:
                    raise ToolExecutionError("division by zero")
                result /= divisor
            else:
                break

        return result

    def factor(self) -> float:
        if self.match("MINUS"):
            return -self.factor()

        if self.match("PLUS"):
            raise ToolExecutionError("parse error: unexpected '+'")

        token = self.current()

        if token is None:
            raise ToolExecutionError("parse error: unexpected end of expression")

        if token.type == "NUMBER":
            self.advance()
            if token.value is None:
                raise ToolExecutionError("parse error: invalid number token")
            return token.value

        if self.match("LPAREN"):
            result = self.expression()

            if not self.match("RPAREN"):
                raise ToolExecutionError("parse error: missing ')'")

            return result

        raise ToolExecutionError(f"parse error: unexpected token '{token.type}'")


def parse(tokens: list[Token]) -> float:
    if not tokens:
        raise ToolExecutionError("parse error: empty expression")

    parser = Parser(tokens)
    result = parser.expression()

    if parser.current() is not None:
        raise ToolExecutionError("parse error: unexpected token after expression")

    return result


def parse_and_eval(expr: str) -> float:
    return parse(tokenize(expr))