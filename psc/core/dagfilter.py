"""Evaluate PAN-OS dynamic address-group (DAG) match filters.

A DAG selects addresses by a boolean *tag* expression instead of a static
member list, e.g. ``'prod' and ('web' or 'app')``. This module parses that
expression once and answers the two questions the reference graph needs:

- which tag names it references (:func:`filter_tags`) — for the unused-*tag*
  check and decommission's DAG-selection blocker, and
- whether a given set of tags satisfies it (:meth:`Filter.matches`) — for
  resolving which addresses a DAG reaches, so an address used *only* via a
  rule-referenced DAG is not reported unused (#60).

Grammar (PAN-OS match-criteria, plus a defensive ``not``)::

    expr     := or_expr
    or_expr  := and_expr ('or' and_expr)*
    and_expr := unary ('and' unary)*
    unary    := 'not' unary | atom
    atom     := TAG | '(' expr ')'
    TAG      := "'" <chars> "'"

PAN-OS keeps tags single-quoted and operators lowercase, and `and`/`or` bind in
the usual way (`not` tightest, then `and`, then `or`). The GUI offers no
negation, but a hand-authored or CLI config can carry one, so we accept and
evaluate `not` rather than crash on a config the device accepted (#60 Q1).
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Set
from dataclasses import dataclass

# A quoted tag, or one of the structural tokens. Anything the tokenizer cannot
# classify makes the whole filter unparseable (see `tokenize`).
_TOKEN_RE = re.compile(r"\s*(?:'([^']*)'|(\(|\)|\band\b|\bor\b|\bnot\b))", re.IGNORECASE)


class FilterParseError(ValueError):
    """Raised when a DAG filter does not parse as a tag expression."""


class _Node(ABC):
    @abstractmethod
    def evaluate(self, tags: Set[str]) -> bool: ...


@dataclass(frozen=True)
class _Tag(_Node):
    name: str

    def evaluate(self, tags: Set[str]) -> bool:
        return self.name in tags


@dataclass(frozen=True)
class _Not(_Node):
    child: _Node

    def evaluate(self, tags: Set[str]) -> bool:
        return not self.child.evaluate(tags)


@dataclass(frozen=True)
class _And(_Node):
    left: _Node
    right: _Node

    def evaluate(self, tags: Set[str]) -> bool:
        return self.left.evaluate(tags) and self.right.evaluate(tags)


@dataclass(frozen=True)
class _Or(_Node):
    left: _Node
    right: _Node

    def evaluate(self, tags: Set[str]) -> bool:
        return self.left.evaluate(tags) or self.right.evaluate(tags)


@dataclass(frozen=True)
class Filter:
    """A parsed DAG match expression. Immutable; evaluate with :meth:`matches`."""

    _root: _Node

    def matches(self, tags: Set[str]) -> bool:
        """True if `tags` (an address's static tag set) satisfies this filter."""
        return self._root.evaluate(tags)


@dataclass(frozen=True)
class _Token:
    kind: str  # "tag" | "and" | "or" | "not" | "(" | ")"
    value: str  # the tag name for "tag", else the keyword/paren


def _tokenize(expr: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    for m in _TOKEN_RE.finditer(expr):
        if m.start() != pos:
            # A gap between matches means an unrecognized token (e.g. an
            # unquoted word, or stray punctuation).
            raise FilterParseError(f"unexpected token at {pos}: {expr[pos:]!r}")
        pos = m.end()
        tag, struct = m.group(1), m.group(2)
        if tag is not None:
            tokens.append(_Token("tag", tag))
        else:
            tokens.append(_Token(struct.lower(), struct.lower()))
    if pos != len(expr) and expr[pos:].strip():
        raise FilterParseError(f"unexpected trailing input: {expr[pos:]!r}")
    return tokens


class _Parser:
    """Recursive-descent parser for the grammar in the module docstring."""

    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._i = 0

    def _peek(self) -> _Token | None:
        return self._tokens[self._i] if self._i < len(self._tokens) else None

    def _advance(self) -> _Token:
        tok = self._tokens[self._i]
        self._i += 1
        return tok

    def parse(self) -> _Node:
        node = self._or()
        if self._peek() is not None:
            raise FilterParseError(f"unexpected token: {self._peek()!r}")
        return node

    def _or(self) -> _Node:
        node = self._and()
        while (tok := self._peek()) is not None and tok.kind == "or":
            self._advance()
            node = _Or(node, self._and())
        return node

    def _and(self) -> _Node:
        node = self._unary()
        while (tok := self._peek()) is not None and tok.kind == "and":
            self._advance()
            node = _And(node, self._unary())
        return node

    def _unary(self) -> _Node:
        tok = self._peek()
        if tok is not None and tok.kind == "not":
            self._advance()
            return _Not(self._unary())
        return self._atom()

    def _atom(self) -> _Node:
        tok = self._peek()
        if tok is None:
            raise FilterParseError("unexpected end of filter")
        if tok.kind == "tag":
            self._advance()
            return _Tag(tok.value)
        if tok.kind == "(":
            self._advance()
            node = self._or()
            close = self._peek()
            if close is None or close.kind != ")":
                raise FilterParseError("unbalanced parenthesis")
            self._advance()
            return node
        raise FilterParseError(f"expected a tag or '(', got {tok.kind!r}")


def parse_filter(expr: str) -> Filter:
    """Parse a DAG match expression into a :class:`Filter`.

    Raises :class:`FilterParseError` on an empty or malformed expression.
    """
    tokens = _tokenize(expr)
    if not tokens:
        raise FilterParseError("empty filter")
    return Filter(_Parser(tokens).parse())


def filter_tags(expr: str) -> set[str]:
    """The set of tag names a DAG filter references.

    Best-effort and never raises: this is a name scan (used by the unused-tag
    check and decommission blockers), so it tolerates a malformed expression by
    returning whatever quoted tokens it can find. Exact-token, not substring —
    a filter naming ``'web'`` does not reference ``'webserver'``.
    """
    return set(re.findall(r"'([^']*)'", expr))
