"""Tests for the pure DAG tag-filter evaluator (#60)."""

from __future__ import annotations

import pytest

from psc.core.dagfilter import FilterParseError, filter_tags, parse_filter


def matches(expr: str, tags: set[str]) -> bool:
    return parse_filter(expr).matches(tags)


def test_single_tag() -> None:
    assert matches("'prod'", {"prod"})
    assert not matches("'prod'", {"web"})
    assert not matches("'prod'", set())


def test_and() -> None:
    assert matches("'prod' and 'web'", {"prod", "web"})
    assert not matches("'prod' and 'web'", {"prod"})
    assert not matches("'prod' and 'web'", {"web"})


def test_or() -> None:
    assert matches("'prod' or 'web'", {"prod"})
    assert matches("'prod' or 'web'", {"web"})
    assert not matches("'prod' or 'web'", {"db"})


def test_not() -> None:
    # PAN-OS GUI offers no negation, but a hand-authored/CLI config can; the
    # evaluator must accept it rather than crash (#60 Q1).
    assert matches("not 'prod'", {"web"})
    assert not matches("not 'prod'", {"prod"})


def test_precedence_not_binds_tighter_than_and() -> None:
    # not 'a' and 'b'  ==  (not 'a') and 'b'
    assert matches("not 'a' and 'b'", {"b"})
    assert not matches("not 'a' and 'b'", {"a", "b"})


def test_precedence_and_binds_tighter_than_or() -> None:
    # 'a' or 'b' and 'c'  ==  'a' or ('b' and 'c')
    assert matches("'a' or 'b' and 'c'", {"a"})
    assert matches("'a' or 'b' and 'c'", {"b", "c"})
    assert not matches("'a' or 'b' and 'c'", {"b"})


def test_parentheses_override_precedence() -> None:
    # ('a' or 'b') and 'c'
    assert matches("('a' or 'b') and 'c'", {"a", "c"})
    assert not matches("('a' or 'b') and 'c'", {"a"})


def test_tags_with_spaces_dots_and_dashes() -> None:
    # PAN-OS tags routinely contain spaces, dots, dashes (e.g. cloud tags).
    expr = "'aws-tag.env.prod' and 'instanceState.running'"
    assert matches(expr, {"aws-tag.env.prod", "instanceState.running"})
    assert not matches(expr, {"aws-tag.env.prod"})


def test_filter_tags_extracts_referenced_names() -> None:
    assert filter_tags("'prod' and ('web' or 'app')") == {"prod", "web", "app"}
    # Exact-token, not substring: a filter naming 'web' does not reference 'webserver'.
    assert filter_tags("'web'") == {"web"}


def test_filter_tags_is_robust_to_malformed() -> None:
    # filter_tags never raises — it is a best-effort name scan used for the
    # unused-tag check and decommission blockers.
    assert filter_tags("'prod' and") == {"prod"}
    assert filter_tags("") == set()


def test_malformed_filter_raises() -> None:
    with pytest.raises(FilterParseError):
        parse_filter("'prod' and")
    with pytest.raises(FilterParseError):
        parse_filter("'prod' 'web'")  # missing operator
    with pytest.raises(FilterParseError):
        parse_filter("('prod'")  # unbalanced
    with pytest.raises(FilterParseError):
        parse_filter("and 'prod'")  # leading operator


def test_empty_filter_raises() -> None:
    with pytest.raises(FilterParseError):
        parse_filter("")
    with pytest.raises(FilterParseError):
        parse_filter("   ")
