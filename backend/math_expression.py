"""Bounded mathematical grammar. Never evaluates Python or parses strings in SymPy.

The original domain obligations are collected before any algebraic simplification.
All numeric literals are exact Rational objects; identifiers must be declared.
"""
from __future__ import annotations

import re
import sympy as s

DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫×÷−', '01234567890123456789.*/-')
FUNCTIONS = {'sin': s.sin, 'cos': s.cos, 'tan': s.tan, 'exp': s.exp, 'ln': s.log,
             'log': s.log, 'sqrt': None, 'abs': s.Abs}
CONSTANTS = {'pi': s.pi, 'e': s.E, 'I': s.I, 'i': s.I}


class MathInputError(ValueError):
    pass


def normalize(text):
    if type(text) is not str or not 1 <= len(text) <= 512:
        raise MathInputError('expression_length_limit')
    if any(ord(c) < 32 and c not in '\r\n\t' for c in text):
        raise MathInputError('invalid_expression_character')
    text = text.translate(DIGITS).replace('س', 'x').replace('ص', 'y').replace('²', '^2').replace('³', '^3')
    # The radical only has an unambiguous extent when followed by parentheses.
    return re.sub(r'√(?=\s*\()', 'sqrt', text).strip()


def symbols(names, domain='real'):
    if domain not in ('real', 'complex', 'integer'):
        raise MathInputError('invalid_domain')
    if type(names) is not list or not 1 <= len(names) <= 6:
        raise MathInputError('variable_limit')
    names = [normalize(n) for n in names]
    if len(set(names)) != len(names) or any(not re.fullmatch('[A-Za-z][A-Za-z0-9]{0,15}', n) or n in CONSTANTS or n in FUNCTIONS for n in names):
        raise MathInputError('invalid_or_reserved_variable')
    flags = {'real': True} if domain == 'real' else {'integer': True} if domain == 'integer' else {'complex': True}
    return {n: s.Symbol(n, **flags) for n in names}


class Expression:
    def __init__(self, text, declared=None, domain='real'):
        self.original = text
        self.normalized = normalize(text)
        self.symbols = declared if declared is not None else symbols(['x'], domain)
        self.domain = domain
        self.conditions = []
        # Adjacent symbols are never silently split. This specifically preserves
        # the two conventional readings of 1/2x for user clarification.
        if re.search(r'/\s*[0-9]+(?:\.[0-9]+)?\s*[A-Za-z]', self.normalized):
            raise MathInputError('ambiguous_division_implicit_product')
        raw = re.findall(r'[0-9]+(?:\.[0-9]+)?|\.[0-9]+|[A-Za-z][A-Za-z0-9]*|\*\*|[^\s]', self.normalized)
        if len(raw) > 128:
            raise MathInputError('expression_token_limit')
        self.tokens = []
        for token in raw:
            if self.tokens and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?|\.[0-9]+', self.tokens[-1]) and (token in self.symbols or token == '('):
                self.tokens.append('*')
            self.tokens.append(token)
        self.pos = self.depth = 0
        self.value = self.add()
        if self.pos != len(self.tokens):
            raise MathInputError('unexpected_math_token')
        if s.count_ops(self.value) > 128:
            raise MathInputError('expression_operation_limit')

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self):
        token = self.peek()
        if token is None:
            raise MathInputError('incomplete_expression')
        self.pos += 1
        return token

    def add(self):
        value = self.multiply()
        while self.peek() in ('+', '-'):
            op = self.take(); right = self.multiply()
            value = s.Add(value, right if op == '+' else s.Mul(-1, right, evaluate=False), evaluate=False)
        return value

    def multiply(self):
        value = self.unary()
        while self.peek() in ('*', '/'):
            op = self.take(); right = self.unary()
            if op == '/':
                self.conditions.append(s.Ne(right, 0, evaluate=False))
                right = s.Pow(right, -1, evaluate=False)
            value = s.Mul(value, right, evaluate=False)
        return value

    def unary(self):
        if self.peek() in ('+', '-'):
            sign = self.take()
            self.depth += 1
            if self.depth > 16: raise MathInputError('expression_depth_limit')
            value = self.unary(); self.depth -= 1
            return value if sign == '+' else s.Mul(-1, value, evaluate=False)
        return self.power()

    def power(self):
        value = self.atom()
        if self.peek() in ('^', '**'):
            self.take(); exponent = self.unary()
            exponent = s.simplify(exponent)
            if not exponent.is_Rational or abs(exponent) > 64 or abs(exponent.p) > 4096 or exponent.q > 64:
                raise MathInputError('power_limit_or_symbolic_exponent')
            if exponent <= 0: self.conditions.append(s.Ne(value, 0, evaluate=False))
            if self.domain != 'complex' and exponent.q % 2 == 0:
                self.conditions.append(s.Ge(value, 0, evaluate=False))
            # In a real domain an odd root uses the real branch, including
            # negative bases. SymPy Pow alone uses the complex principal branch.
            if self.domain!='complex' and exponent.q>1 and exponent.q%2:
                value=s.Pow(s.real_root(value,exponent.q),exponent.p,evaluate=False)
            else:
                value = s.Pow(value, exponent, evaluate=False)
        return value

    def atom(self):
        self.depth += 1
        if self.depth > 16: raise MathInputError('expression_depth_limit')
        token = self.take()
        if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?|\.[0-9]+', token):
            if len(token) > 64: raise MathInputError('numeric_literal_limit')
            value = s.Rational(token)
        elif token == '(':
            value = self.add()
            if self.take() != ')': raise MathInputError('unbalanced_parentheses')
        elif token == '|':
            value = self.add()
            if self.take() != '|': raise MathInputError('unbalanced_absolute_value')
            value = s.Abs(value, evaluate=False)
        elif token in self.symbols:
            value = self.symbols[token]
        elif token in CONSTANTS:
            value = CONSTANTS[token]
        elif token in FUNCTIONS:
            if self.take() != '(': raise MathInputError('function_parentheses_required')
            arg = self.add()
            if self.take() != ')': raise MathInputError('unbalanced_function')
            if token in ('ln', 'log'):
                self.conditions.append(s.Gt(arg, 0, evaluate=False) if self.domain != 'complex' else s.Ne(arg, 0, evaluate=False))
            if token == 'sqrt':
                if self.domain != 'complex': self.conditions.append(s.Ge(arg, 0, evaluate=False))
                value = s.Pow(arg, s.Rational(1, 2), evaluate=False)
            else:
                value = FUNCTIONS[token](arg, evaluate=False)
                if token == 'tan': self.conditions.append(s.Ne(s.cos(arg), 0, evaluate=False))
        else:
            raise MathInputError('undeclared_symbol_or_operation')
        self.depth -= 1
        return value


def relation(text, declared, domain='real'):
    text = normalize(text).replace('≤', '<=').replace('≥', '>=').replace('≠', '!=')
    parts = re.split(r'(<=|>=|!=|=|<|>)', text)
    if len(parts) != 3: raise MathInputError('one_relation_required')
    left, right = Expression(parts[0], declared, domain), Expression(parts[2], declared, domain)
    ctor = {'=': s.Eq, '!=': s.Ne, '<': s.Lt, '>': s.Gt, '<=': s.Le, '>=': s.Ge}[parts[1]]
    return ctor(left.value, right.value, evaluate=False), left.conditions + right.conditions


def numeric(text, real=True):
    # JSON integer tokens are exact, unlike JSON floats. Preserve them without
    # passing through float; booleans are deliberately not integers here.
    if type(text) is int:
        if len(str(abs(text)))>64:raise MathInputError('numeric_literal_limit')
        text=str(text)
    parsed = Expression(text, {}, 'real' if real else 'complex')
    value = s.simplify(parsed.value)
    if value.free_symbols or value.has(s.zoo, s.nan, s.oo, -s.oo) or (real and value.is_real is not True):
        raise MathInputError('finite_numeric_value_required')
    if any(s.simplify(c) is not s.true for c in parsed.conditions):
        raise MathInputError('numeric_domain_error')
    return value
