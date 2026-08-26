"""Fail-closed TypeScript page-source witness parsing."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class _JsToken:
    kind: str
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class _JsAssertion:
    binding: str
    anchor: str
    span: tuple[int, int]


@dataclass(frozen=True)
class _JsSourceRead:
    binding: str
    relative_module: str
    anchor: str
    span: tuple[int, int]


@dataclass(frozen=True)
class _JsCase:
    name: str
    body_start: int
    body_end: int
    tokens: tuple[_JsToken, ...]
    source_reads: tuple[_JsSourceRead, ...]
    assertions: tuple[_JsAssertion, ...]
    comment_spans: tuple[tuple[int, int], ...]
    reassignments: tuple[tuple[str, int], ...]
    invalid: bool = False


def _js_string(raw: str) -> str | None:
    try:
        value = ast.literal_eval(raw)
    except (SyntaxError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _regex_start(tokens: list[_JsToken]) -> bool:
    if not tokens:
        return True
    return tokens[-1].value in {
        "(",
        "[",
        "{",
        ",",
        "=",
        ":",
        "=>",
        "return",
    }


def _lex_js(source: str) -> list[_JsToken]:
    tokens: list[_JsToken] = []
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if char.isspace():
            index += 1
            continue
        if source.startswith("//", index):
            start = index
            newline = source.find("\n", index + 2)
            index = length if newline == -1 else newline + 1
            tokens.append(_JsToken("comment", "", start, index))
            continue
        if source.startswith("/*", index):
            start = index
            end = source.find("*/", index + 2)
            index = length if end == -1 else end + 2
            tokens.append(_JsToken("comment", "", start, index))
            continue
        if char in {"'", '"'}:
            quote = char
            start = index
            index += 1
            while index < length:
                if source[index] == "\\":
                    index += 2
                    continue
                if source[index] == quote:
                    index += 1
                    break
                index += 1
            raw = source[start:index]
            tokens.append(_JsToken("string", _js_string(raw) or "", start, index))
            continue
        if ord(char) == 96:
            start = index
            index += 1
            while index < length:
                if source[index] == "\\":
                    index += 2
                    continue
                if ord(source[index]) == 96:
                    index += 1
                    break
                index += 1
            tokens.append(_JsToken("template", "", start, index))
            continue
        if char == "/" and _regex_start(tokens):
            start = index
            index += 1
            in_class = False
            while index < length:
                if source[index] == "\\":
                    index += 2
                    continue
                if source[index] == "[":
                    in_class = True
                elif source[index] == "]":
                    in_class = False
                elif source[index] == "/" and not in_class:
                    index += 1
                    while index < length and source[index].isalpha():
                        index += 1
                    break
                index += 1
            tokens.append(_JsToken("regex", "", start, index))
            continue
        if char.isalpha() or char in "_$":
            start = index
            index += 1
            while index < length and (source[index].isalnum() or source[index] in "_$"):
                index += 1
            tokens.append(_JsToken("identifier", source[start:index], start, index))
            continue
        if source.startswith("=>", index):
            tokens.append(_JsToken("punctuation", "=>", index, index + 2))
            index += 2
            continue
        tokens.append(_JsToken("punctuation", char, index, index + 1))
        index += 1
    return tokens


def _matching(
    tokens: list[_JsToken], index: int, opening: str, closing: str
) -> int | None:
    depth = 0
    for cursor in range(index, len(tokens)):
        if tokens[cursor].value == opening:
            depth += 1
        elif tokens[cursor].value == closing:
            depth -= 1
            if depth == 0:
                return cursor
    return None


def _js_brace_depths(tokens: list[_JsToken]) -> tuple[int, ...]:
    depths: list[int] = []
    depth = 0
    for token in tokens:
        depths.append(depth)
        if token.value == "{":
            depth += 1
        elif token.value == "}":
            depth = max(0, depth - 1)
    return tuple(depths)


def _direct_named_imports(tokens: list[_JsToken]) -> bool:
    required = {"describe", "expect", "it", "readFileSync"}
    imported: set[str] = set()
    depths = _js_brace_depths(tokens)
    index = 0
    while index < len(tokens):
        if depths[index] != 0 or tokens[index].value != "import":
            index += 1
            continue
        if index + 1 >= len(tokens) or tokens[index + 1].value != "{":
            index += 1
            continue
        close = _matching(tokens, index + 1, "{", "}")
        if close is None or close + 3 >= len(tokens):
            return False
        if tokens[close + 1].value != "from" or tokens[close + 2].kind != "string":
            return False
        module_name = tokens[close + 2].value
        names: list[str] = []
        cursor = index + 2
        while cursor < close:
            if tokens[cursor].kind != "identifier":
                return False
            name = tokens[cursor].value
            if name in {"URL", "describe", "expect", "it", "test", "readFileSync"}:
                imported.add(name)
            names.append(name)
            cursor += 1
            if cursor == close:
                break
            if tokens[cursor].value != ",":
                return False
            cursor += 1
        expected = {
            "vitest": {"describe", "expect", "it"},
            "node:fs": {"readFileSync"},
        }.get(module_name)
        if expected is None or set(names) != expected:
            return False
        index = close + 3
        continue
    return imported == required


_PROVENANCE_NAMES = {"URL", "describe", "expect", "it", "test", "readFileSync"}


def _declaration_shadows(tokens: list[_JsToken], index: int) -> bool:
    if tokens[index].value not in {"const", "let", "var"}:
        return False
    bracket_depth = 0
    cursor = index + 1
    while cursor < len(tokens):
        token = tokens[cursor]
        if token.value in {"(", "[", "{"}:
            bracket_depth += 1
        elif token.value in {
            ")",
            "]",
            "}",
        }:
            bracket_depth = max(0, bracket_depth - 1)
        if bracket_depth == 0 and token.value in {"=", ";"}:
            return False
        if token.kind == "identifier" and token.value in _PROVENANCE_NAMES:
            next_value = tokens[cursor + 1].value if cursor + 1 < len(tokens) else ""
            if bracket_depth > 0 or next_value in {",", "}", "="}:
                return True
        cursor += 1
    return False


def _shadows_provenance(tokens: list[_JsToken], index: int) -> bool:
    if _declaration_shadows(tokens, index):
        return True
    return (
        tokens[index].value in {"function", "class"}
        and index + 1 < len(tokens)
        and tokens[index + 1].kind == "identifier"
        and tokens[index + 1].value in _PROVENANCE_NAMES
    )


def _mocking_call(tokens: list[_JsToken], index: int) -> bool:
    return tokens[index].kind == "identifier" and tokens[index].value in {"vi", "jest"}


def _ordinary_describe_ranges(
    tokens: list[_JsToken], depths: tuple[int, ...]
) -> tuple[tuple[int, int, int], ...]:
    ranges: list[tuple[int, int, int]] = []
    for index, token in enumerate(tokens):
        if (
            token.kind != "identifier"
            or token.value != "describe"
            or depths[index] != 0
            or (index and tokens[index - 1].value not in {";", "}"})
            or index + 1 >= len(tokens)
            or tokens[index + 1].value != "("
        ):
            continue
        close_call = _matching(tokens, index + 1, "(", ")")
        if close_call is None:
            continue
        cursor = index + 2
        if cursor >= close_call or tokens[cursor].kind != "string":
            continue
        cursor += 1
        if cursor >= close_call or tokens[cursor].value != ",":
            continue
        cursor += 1
        if (
            cursor + 4 >= close_call
            or tokens[cursor].value != "("
            or tokens[cursor + 1].value != ")"
            or tokens[cursor + 2].value != "=>"
            or tokens[cursor + 3].value != "{"
        ):
            continue
        body_open = cursor + 3
        body_close = _matching(tokens, body_open, "{", "}")
        if body_close is None or body_close + 1 != close_call:
            continue
        ranges.append((body_open + 1, body_close, depths[body_open] + 1))
    return tuple(ranges)


def _case_tokens(tokens: list[_JsToken]) -> list[_JsCase]:
    cases: list[_JsCase] = []
    depths = _js_brace_depths(tokens)
    describe_ranges = _ordinary_describe_ranges(tokens, depths)
    for index, token in enumerate(tokens):
        if (
            token.kind != "identifier"
            or token.value not in {"it", "test"}
            or (index and tokens[index - 1].value not in {";", "{", "}"})
            or not (
                depths[index] == 0
                or any(
                    start <= index < end and depths[index] == body_depth
                    for start, end, body_depth in describe_ranges
                )
            )
        ):
            continue
        open_call = index + 1
        if open_call >= len(tokens) or tokens[open_call].value != "(":
            continue
        close_call = _matching(tokens, open_call, "(", ")")
        if close_call is None:
            continue
        cursor = index + 2
        if cursor >= close_call or tokens[cursor].kind != "string":
            continue
        name_token = tokens[cursor]
        cursor += 1
        if cursor >= close_call or tokens[cursor].value != ",":
            continue
        cursor += 1
        if cursor < close_call and tokens[cursor].value == "async":
            cursor += 1
        if (
            cursor + 4 >= close_call
            or tokens[cursor].value != "("
            or tokens[cursor + 1].value != ")"
            or tokens[cursor + 2].value != "=>"
            or tokens[cursor + 3].value != "{"
        ):
            continue
        body_open = cursor + 3
        body_close = _matching(tokens, body_open, "{", "}")
        if body_close is None or body_close + 1 != close_call:
            continue
        cases.append(
            _JsCase(
                name=name_token.value,
                body_start=tokens[body_open].end,
                body_end=tokens[body_close].start,
                tokens=tuple(tokens[body_open + 1 : body_close]),
                source_reads=(),
                assertions=(),
                comment_spans=(),
                reassignments=(),
            )
        )
    return cases


def _source_read_at(
    tokens: tuple[_JsToken, ...], index: int, source: str
) -> tuple[_JsSourceRead, int] | None:
    if index + 18 >= len(tokens) or tokens[index].value != "const":
        return None
    binding = tokens[index + 1]
    if binding.kind != "identifier" or tokens[index + 2].value != "=":
        return None
    sequence: list[str | None] = [
        "readFileSync",
        "(",
        "new",
        "URL",
        "(",
        None,
        ",",
        "import",
        ".",
        "meta",
        ".",
        "url",
        ")",
        ",",
        None,
        ")",
    ]
    cursor = index + 3
    values: list[str | None] = []
    for expected in sequence:
        if cursor >= len(tokens):
            return None
        token = tokens[cursor]
        if expected is None:
            if token.kind != "string":
                return None
            values.append(token.value)
        elif token.value != expected:
            return None
        cursor += 1
    if values[0] is None or values[1] != "utf8":
        return None
    if cursor >= len(tokens) or tokens[cursor].value != ";":
        return None
    return (
        _JsSourceRead(
            binding=binding.value,
            relative_module=values[0],
            anchor=source[tokens[index].start : tokens[cursor].end],
            span=(tokens[index].start, tokens[cursor].end),
        ),
        cursor,
    )


def _assertion_at(
    tokens: tuple[_JsToken, ...], index: int, source: str
) -> tuple[_JsAssertion, int] | None:
    if index + 4 >= len(tokens) or tokens[index].value != "expect":
        return None
    if tokens[index + 1].value != "(":
        return None
    binding = tokens[index + 2]
    if binding.kind != "identifier" or tokens[index + 3].value != ")":
        return None
    cursor = index + 4
    if cursor >= len(tokens) or tokens[cursor].value != ".":
        return None
    cursor += 1
    if cursor < len(tokens) and tokens[cursor].value == "not":
        cursor += 1
        if cursor >= len(tokens) or tokens[cursor].value != ".":
            return None
        cursor += 1
    if cursor >= len(tokens) or tokens[cursor].value not in {"toContain", "toMatch"}:
        return None
    cursor += 1
    if cursor >= len(tokens) or tokens[cursor].value != "(":
        return None
    argument = cursor + 1
    if argument >= len(tokens) or tokens[argument].kind not in {"string", "regex"}:
        return None
    close = _matching(list(tokens), cursor, "(", ")")
    if (
        close is None
        or close != argument + 1
        or close + 1 >= len(tokens)
        or tokens[close + 1].value != ";"
    ):
        return None
    return (
        _JsAssertion(
            binding=binding.value,
            anchor=source[tokens[index].start : tokens[close + 1].end],
            span=(tokens[index].start, tokens[close + 1].end),
        ),
        close + 1,
    )


def _scan_case(case: _JsCase, source: str) -> _JsCase:
    reads: list[_JsSourceRead] = []
    assertions: list[_JsAssertion] = []
    reassignments: list[tuple[str, int]] = []
    declared_bindings: set[str] = set()
    brace_depth = 0
    index = 0
    invalid = False
    tokens = case.tokens
    while index < len(tokens):
        token = tokens[index]
        if brace_depth == 0 and token.value in {
            "if",
            "for",
            "while",
            "switch",
            "try",
            "catch",
            "finally",
            "?",
        }:
            invalid = True
            break
        if brace_depth == 0 and token.value in {"return", "throw"}:
            invalid = True
            break
        if brace_depth == 0 and (
            _shadows_provenance(tokens, index) or _mocking_call(tokens, index)
        ):
            invalid = True
            break
        if token.value == "{":
            brace_depth += 1
        elif token.value == "}":
            brace_depth = max(0, brace_depth - 1)
        if brace_depth == 0:
            read = _source_read_at(tokens, index, source)
            if read is not None:
                item, index = read
                reads.append(item)
                declared_bindings.add(item.binding)
                index += 1
                continue
            assertion = _assertion_at(tokens, index, source)
            if assertion is not None:
                item, index = assertion
                assertions.append(item)
                index += 1
                continue
            if (
                reads
                and token.kind == "identifier"
                and index + 1 < len(tokens)
                and tokens[index + 1].value == "("
                and token.value not in {"expect", "readFileSync"}
            ):
                invalid = True
                break
            if (
                token.kind == "identifier"
                and token.value in _PROVENANCE_NAMES
                and index + 1 < len(tokens)
                and tokens[index + 1].value == "="
            ):
                invalid = True
                break
            if token.kind == "identifier" and token.value in declared_bindings:
                if index + 1 < len(tokens) and tokens[index + 1].value == "=":
                    reassignments.append((token.value, token.start))
            elif (
                token.value in {"const", "let", "var"}
                and index + 1 < len(tokens)
                and tokens[index + 1].kind == "identifier"
                and tokens[index + 1].value in declared_bindings
            ):
                reassignments.append((tokens[index + 1].value, token.start))
        index += 1
    return _JsCase(
        name=case.name,
        body_start=case.body_start,
        body_end=case.body_end,
        tokens=case.tokens,
        source_reads=tuple(reads),
        assertions=tuple(assertions),
        comment_spans=tuple(
            (token.start, token.end) for token in case.tokens if token.kind == "comment"
        ),
        reassignments=tuple(reassignments),
        invalid=invalid,
    )


@lru_cache(maxsize=64)
def _ui_cases(source: str) -> list[_JsCase]:
    tokens = _lex_js(source)
    if not _direct_named_imports(tokens):
        return []
    for index in range(len(tokens)):
        if _shadows_provenance(tokens, index) or _mocking_call(tokens, index):
            return []
    cases = [_scan_case(case, source) for case in _case_tokens(tokens)]
    bindings = {item.binding for case in cases for item in case.source_reads}
    grammar = set(
        "URL const describe expect from import it meta new readFileSync test toContain toMatch url".split()  # noqa: SIM905
    )
    if any(
        token.kind == "identifier" and token.value not in grammar | bindings
        for token in tokens
    ):
        return []
    return cases


def _ui_page_paths(route: str) -> tuple[str, str]:
    parts = []
    for part in route.strip("/").split("/") if route != "/" else []:
        parts.append(f"[{part[1:-1]}]" if part.startswith("{") else part)
    suffix = "/".join(parts)
    module = f"apps/web/app/{suffix + '/' if suffix else ''}page.tsx"
    relative = f"../app/{suffix + '/' if suffix else ''}page.tsx"
    return module, relative


__all__ = ["_ui_cases", "_ui_page_paths"]
