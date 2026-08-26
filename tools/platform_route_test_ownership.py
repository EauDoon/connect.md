#!/usr/bin/env python3
"""Fail-closed source-witness traceability for API and UI routes."""

from __future__ import annotations

import ast
import io
import json
import re
import tokenize
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from tools.platform_route_test_ui import _ui_cases, _ui_page_paths
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    from platform_route_test_ui import _ui_cases, _ui_page_paths

API_ROUTE_FIELDS = {"owner", "witness"}
API_WITNESS_FIELDS = {
    "test_path",
    "test",
    "method",
    "request_anchor",
    "request_path",
    "response_binding",
    "assertion_anchors",
}
UI_ROUTE_FIELDS = {"owner", "witness"}
UI_WITNESS_FIELDS = {
    "test_path",
    "test",
    "evidence_mode",
    "case_witness",
    "page_module",
    "source_binding",
    "assertion_anchors",
}
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
EVIDENCE_MODE = "page_source"
MAX_STATIC_VALUES = 16
CLIENT_NAMES = {"client", "http_client", "api_client", "async_client"}
_UNKNOWN = object()
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_ROUTE_PARAMETER_RE = re.compile(r"^\{[A-Za-z][A-Za-z0-9_]*\}$")


def _error(errors: list[str], location: str, message: str) -> None:
    errors.append(f"{location}: {message}")


def _object(
    value: Any, location: str, required: set[str], errors: list[str]
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        _error(errors, location, "must be an object")
        return None
    unknown = set(value) - required
    missing = required - set(value)
    if unknown:
        _error(errors, location, f"has unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        _error(errors, location, f"is missing fields: {', '.join(sorted(missing))}")
    return value


def _strings(value: Any, location: str, errors: list[str]) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        _error(errors, location, "must be a non-empty array of strings")
        return []
    return value


def _safe_relative_path(
    value: Any, location: str, root: Path, errors: list[str]
) -> Path | None:
    if not isinstance(value, str) or not value or "\\" in value:
        _error(errors, location, "must be a non-empty POSIX-relative path")
        return None
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        _error(errors, location, "must remain within the repository")
        return None
    candidate = root.joinpath(*relative.parts).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        _error(errors, location, "escapes the repository")
        return None
    if not candidate.is_file():
        _error(errors, location, "does not name an existing file")
        return None
    return candidate


def _feature_test_allowlists(features: Any, errors: list[str]) -> dict[str, set[str]]:
    if isinstance(features, dict):
        features = features.get("features")
    if not isinstance(features, list):
        _error(errors, "features", "must be an array")
        return {}
    allowlists: dict[str, set[str]] = {}
    for index, feature in enumerate(features):
        if not isinstance(feature, dict):
            _error(errors, f"features[{index}]", "must be an object")
            continue
        feature_id = feature.get("id")
        tests = feature.get("tests")
        if not isinstance(feature_id, str) or not feature_id:
            _error(errors, f"features[{index}].id", "must be a non-empty string")
            continue
        allowlists[feature_id] = set(
            _strings(tests, f"features[{index}].tests", errors)
        )
    return allowlists


@dataclass(frozen=True)
class _PathValue:
    text: str
    slots: int


_PATH_SLOT = "\x00"
_DYNAMIC = object()


@dataclass(frozen=True)
class _ResponseJsonValue:
    binding: str


def _path_from_text(text: str) -> _PathValue | object:
    if not text.startswith("/") or "#" in text or _PATH_SLOT not in text:
        return _UNKNOWN
    query_index = text.find("?")
    path_text = text if query_index == -1 else text[:query_index]
    path_slots = path_text.count(_PATH_SLOT)
    query_slots = 0 if query_index == -1 else text[query_index + 1 :].count(_PATH_SLOT)
    if path_slots == 0 and query_slots == 0:
        return _UNKNOWN
    for index in (match.start() for match in re.finditer(_PATH_SLOT, path_text)):
        if index == 0 or path_text[index - 1] != "/":
            return _UNKNOWN
        next_char = path_text[index + 1] if index + 1 < len(path_text) else ""
        if next_char not in {"", "/", "."}:
            return _UNKNOWN
    return _PathValue(text, path_slots + query_slots)


def _path_value(prefix: str, suffix: str) -> _PathValue | object:
    return _path_from_text(prefix + _PATH_SLOT + suffix)


def _join_path_values(left: Any, right: Any) -> Any:
    if isinstance(left, str) and isinstance(right, _PathValue):
        return _path_from_text(left + right.text)
    if isinstance(left, _PathValue) and isinstance(right, str):
        return _path_from_text(left.text + right)
    if isinstance(left, _PathValue) and isinstance(right, _PathValue):
        return _path_from_text(left.text + right.text)
    return _UNKNOWN


def _concat_path_parts(left: Any, right: Any) -> Any:
    if isinstance(left, str) and isinstance(right, str):
        return left + right
    if isinstance(left, str) and isinstance(right, _PathValue):
        return _PathValue(left + right.text, right.slots)
    if isinstance(left, _PathValue) and isinstance(right, str):
        return _PathValue(left.text + right, left.slots)
    if isinstance(left, _PathValue) and isinstance(right, _PathValue):
        return _PathValue(left.text + right.text, left.slots + right.slots)
    return _UNKNOWN


def _literal(node: ast.AST, values: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant) and isinstance(
        node.value, (str, int, float, bool, type(None))
    ):
        return node.value
    if isinstance(node, ast.Name):
        return values.get(node.id, _UNKNOWN)
    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "json"
            and isinstance(node.func.value, ast.Name)
            and isinstance(values.get(node.func.value.id), _ApiRequest)
            and not node.args
            and not node.keywords
        ):
            return _ResponseJsonValue(node.func.value.id)
        return _UNKNOWN
    if isinstance(node, ast.Subscript):
        container = _literal(node.value, values)
        index = _literal(node.slice, values)
        if isinstance(container, _ResponseJsonValue) and isinstance(index, (str, int)):
            return container
        return _UNKNOWN
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal(node.left, values)
        right = _literal(node.right, values)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
        if isinstance(left, tuple) and isinstance(right, tuple):
            return left + right
        joined = _join_path_values(left, right)
        if joined is not _UNKNOWN:
            return joined
        return _UNKNOWN
    if isinstance(node, ast.JoinedStr):
        pieces: list[str | _PathValue] = []
        for piece in node.values:
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                pieces.append(piece.value)
                continue
            if isinstance(piece, ast.FormattedValue):
                if piece.conversion != -1 or piece.format_spec is not None:
                    return _UNKNOWN
                value = _literal(piece.value, values)
                if isinstance(value, (str, _PathValue)):
                    pieces.append(value)
                elif isinstance(value, _ResponseJsonValue) or (
                    value is _UNKNOWN
                    and isinstance(piece.value, ast.Name)
                    and piece.value.id not in values
                ):
                    pieces.append(_PathValue(_PATH_SLOT, 1))
                else:
                    return _UNKNOWN
                continue
            return _UNKNOWN
        value: str | _PathValue = ""
        for piece in pieces:
            value = _concat_path_parts(value, piece)
            if value is _UNKNOWN:
                return _UNKNOWN
        if isinstance(value, _PathValue):
            return _path_from_text(value.text)
        return value
    if isinstance(node, (ast.Tuple, ast.List)):
        values_out: list[Any] = []
        if len(node.elts) > MAX_STATIC_VALUES:
            return _UNKNOWN
        for element in node.elts:
            value = _literal(element, values)
            if value is _UNKNOWN:
                return _UNKNOWN
            values_out.append(value)
        return tuple(values_out)
    return _UNKNOWN


def _module_values(tree: ast.Module) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            value = _literal(statement.value, values)
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = value
        elif isinstance(statement, ast.AnnAssign) and isinstance(
            statement.target, ast.Name
        ):
            values[statement.target.id] = _literal(statement.value, values)
    return values


def _position(node: ast.AST) -> tuple[int, int]:
    return (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))


def _span(node: ast.AST) -> tuple[int, int, int, int]:
    return (
        getattr(node, "lineno", 0),
        getattr(node, "col_offset", 0),
        getattr(node, "end_lineno", 0),
        getattr(node, "end_col_offset", 0),
    )


def _span_overlaps(
    left: tuple[int, int, int, int], right: tuple[int, int, int, int]
) -> bool:
    return (left[0], left[1]) < (right[2], right[3]) and (right[0], right[1]) < (
        left[2],
        left[3],
    )


def _offset_span_overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


@lru_cache(maxsize=128)
def _python_comment_spans(source: str) -> tuple[tuple[int, int, int, int], ...]:
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        return tuple(
            (token.start[0], token.start[1], token.end[0], token.end[1])
            for token in tokens
            if token.type == tokenize.COMMENT
        )
    except (IndentationError, tokenize.TokenError):
        return ()


def _ast_dotted_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _client_name(node: ast.AST, clients: set[str]) -> bool:
    return isinstance(node, ast.Name) and node.id in clients


def _call_request(
    node: ast.AST, values: dict[str, Any], clients: set[str], source: str
) -> tuple[str, tuple[str | _PathValue, ...], str] | None:
    if isinstance(node, ast.Await):
        node = node.value
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if not _client_name(node.func.value, clients):
        return None
    method_name = node.func.attr.lower()
    if method_name == "request":
        if len(node.args) < 2:
            return None
        method = _literal(node.args[0], values)
        path = _literal(node.args[1], values)
    elif method_name in {method.lower() for method in HTTP_METHODS}:
        if not node.args:
            return None
        method = method_name.upper()
        path = _literal(node.args[0], values)
    else:
        return None
    if not isinstance(method, str) or method.upper() not in HTTP_METHODS:
        return None
    if isinstance(path, (str, _PathValue)):
        paths = (path,)
    elif (
        isinstance(path, tuple)
        and path
        and all(isinstance(item, (str, _PathValue)) for item in path)
    ):
        paths = path[:MAX_STATIC_VALUES]
    else:
        return None
    anchor = ast.get_source_segment(source, node)
    if not anchor:
        return None
    return method.upper(), paths, anchor


@dataclass
class _ApiRequest:
    method: str
    paths: tuple[str | _PathValue, ...]
    anchor: str
    binding: str
    position: tuple[int, int]
    span: tuple[int, int, int, int]
    assertions: list[tuple[tuple[int, int, int, int], str]] = field(
        default_factory=list
    )


def _path_descriptor(path: str | _PathValue) -> str:
    if isinstance(path, _PathValue):
        return path.text.replace(_PATH_SLOT, "{param}")
    return path


def _static_truth(node: ast.AST, values: dict[str, Any]) -> bool | None:
    value = _literal(node, values)
    if value is _UNKNOWN or isinstance(value, (_PathValue, tuple)):
        return None
    if value is None or isinstance(value, (bool, int, float, str)):
        return bool(value)
    return None


def _assignment_target(statement: ast.AST) -> ast.Name | None:
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        target = statement.targets[0]
    elif isinstance(statement, ast.AnnAssign):
        target = statement.target
    else:
        return None
    return target if isinstance(target, ast.Name) else None


def _assignment_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in target.elts:
            names.extend(_assignment_names(element))
        return names
    return []


def _statement_assignment(statement: ast.AST) -> tuple[list[str], ast.AST | None]:
    if isinstance(statement, ast.Assign):
        if len(statement.targets) != 1:
            return [], statement.value
        return _assignment_names(statement.targets[0]), statement.value
    if isinstance(statement, ast.AnnAssign):
        return _assignment_names(statement.target), statement.value
    return [], None


def _pytest_terminator(statement: ast.AST) -> bool:
    if not isinstance(statement, ast.Expr):
        return False
    node = statement.value
    if isinstance(node, ast.Await):
        node = node.value
    return isinstance(node, ast.Call) and _ast_dotted_name(node.func) in {
        "pytest.skip",
        "pytest.xfail",
    }


def _module_skipped(tree: ast.Module) -> bool:
    return any(
        "pytestmark" in _statement_assignment(statement)[0] for statement in tree.body
    )


def _skipped_test(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        _ast_dotted_name(decorator) != "pytest.mark.asyncio"
        for decorator in function.decorator_list
    )


def _root_name(node: ast.AST) -> str | None:
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "vars"
        and len(node.args) == 1
        and not node.keywords
    ):
        node = node.args[0]
    return node.id if isinstance(node, ast.Name) else None


def _identity_alias_roots(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.BoolOp)):
        items = node.elts if hasattr(node, "elts") else node.values
        return set().union(*(_identity_alias_roots(item) for item in items))
    if isinstance(node, ast.Dict):
        items = [*node.keys, *node.values]
        return set().union(
            *(_identity_alias_roots(item) for item in items if item is not None)
        )
    if isinstance(node, ast.NamedExpr):
        return _identity_alias_roots(node.value)
    if isinstance(node, ast.IfExp):
        return _identity_alias_roots(node.body) | _identity_alias_roots(node.orelse)
    if isinstance(node, ast.Subscript) or (
        isinstance(node, ast.Attribute) and node.attr in {"__class__", "__dict__"}
    ):
        return _identity_alias_roots(node.value)
    if isinstance(node, ast.Lambda):
        parameters = {
            item.arg
            for item in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            )
        }
        return {
            item.id
            for item in ast.walk(node.body)
            if isinstance(item, ast.Name)
            and isinstance(item.ctx, ast.Load)
            and item.id not in parameters
        }
    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "json"
            and not node.args
            and not node.keywords
        ):
            return set()
        callable_roots = (
            _identity_alias_roots(node.func.value)
            if isinstance(node.func, ast.Attribute)
            else _identity_alias_roots(node.func)
        )
        return callable_roots | set().union(
            *(
                _identity_alias_roots(item)
                for item in (*node.args, *(item.value for item in node.keywords))
            )
        )
    return set()


def _mutation_target(
    target: ast.AST, clients: set[str], responses: dict[str, _ApiRequest]
) -> bool:
    root = _root_name(target)
    return root is not None and (root in clients or root in responses)


def _mutation_call(
    node: ast.AST, clients: set[str], responses: dict[str, _ApiRequest]
) -> bool:
    if isinstance(node, ast.Await):
        node = node.value
    if not isinstance(node, ast.Call):
        return False
    name = _ast_dotted_name(node.func)
    if (
        name
        not in {
            "setattr",
            "monkeypatch.setattr",
            "object.__setattr__",
        }
        or len(node.args) < 2
    ):
        return False
    target = _root_name(node.args[0])
    return (
        target in clients | set(responses)
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    )


def _direct_response_assertion(node: ast.AST, binding: str) -> bool:
    walked = tuple(ast.walk(node))
    if any(isinstance(item, (ast.BoolOp, ast.IfExp, ast.Lambda)) for item in walked):
        return False
    if sum(isinstance(item, ast.Name) and item.id == binding for item in walked) != 1:
        return False
    if isinstance(node, ast.Compare):
        operands = [node.left, *node.comparators]
        if len({ast.dump(item, include_attributes=False) for item in operands}) == 1:
            return False
    return True


def _safe_client_cleanup(statement: ast.AST, clients: set[str]) -> bool:
    node: ast.AST = statement.value if isinstance(statement, ast.Expr) else statement
    if isinstance(node, ast.Await):
        node = node.value
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in clients
        and node.func.attr in {"aclose", "close"}
        and not node.args
        and not node.keywords
    )


def _safe_cleanup_statement(
    statement: ast.stmt,
    clients: set[str],
    responses: dict[str, _ApiRequest],
) -> bool:
    if isinstance(statement, ast.Pass):
        return True
    if isinstance(statement, ast.Assign):
        if any(
            _mutation_target(target, clients, responses) for target in statement.targets
        ):
            return False
        return not any(
            isinstance(node, ast.Name) and node.id in clients | set(responses)
            for node in ast.walk(statement.value)
        )
    if isinstance(statement, ast.AnnAssign):
        if _mutation_target(statement.target, clients, responses):
            return False
        return statement.value is None or not any(
            isinstance(node, ast.Name) and node.id in clients | set(responses)
            for node in ast.walk(statement.value)
        )
    if not isinstance(statement, ast.Expr):
        return False
    if _mutation_call(statement.value, clients, responses):
        return False
    if _safe_client_cleanup(statement, clients):
        return True
    node: ast.AST = statement.value
    if isinstance(node, ast.Await):
        node = node.value
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and not any(
            isinstance(item, ast.Name) and item.id in clients | set(responses)
            for item in ast.walk(node)
        )
    )


def _safe_finally(
    finalbody: list[ast.stmt],
    clients: set[str],
    responses: dict[str, _ApiRequest],
) -> bool:
    return all(
        _safe_cleanup_statement(statement, clients, responses)
        for statement in finalbody
    )


def _control_flow_affects_evidence(
    statements: list[ast.stmt],
    clients: set[str],
    responses: dict[str, _ApiRequest],
) -> bool:
    relevant = clients | set(responses)
    for node in statements:
        for item in _executable_nodes(node):
            if isinstance(item, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                return True
            if isinstance(item, ast.Assign):
                if any(
                    name in relevant
                    for target in item.targets
                    for name in _assignment_names(target)
                ):
                    return True
                if any(
                    _mutation_target(target, clients, responses)
                    for target in item.targets
                ):
                    return True
                if any(
                    isinstance(name, ast.Name) and name.id in relevant
                    for name in ast.walk(item.value)
                ):
                    return True
            elif (
                isinstance(item, ast.AnnAssign)
                and (
                    _mutation_target(item.target, clients, responses)
                    or any(name in relevant for name in _assignment_names(item.target))
                )
            ) or (
                isinstance(item, ast.Call)
                and (
                    _mutation_call(item, clients, responses)
                    or any(
                        isinstance(name, ast.Name) and name.id in relevant
                        for name in ast.walk(item)
                    )
                )
            ):
                return True
    return False


def _executable_nodes(node: ast.AST) -> Iterator[ast.AST]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        for child in ast.iter_child_nodes(current):
            if isinstance(
                child,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
            ):
                continue
            stack.append(child)


def _control_flow_assignments(statements: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for statement in statements:
        for node in _executable_nodes(statement):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    names.update(_assignment_names(target))
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                names.update(_assignment_names(node.target))
    return names


def _poison_control_flow_assignments(
    statements: list[ast.stmt],
    values: dict[str, Any],
    responses: dict[str, _ApiRequest],
) -> None:
    for name in _control_flow_assignments(statements):
        values[name] = _DYNAMIC
        responses.pop(name, None)


def _call_parameters_receiving_aliases(
    call: ast.Call,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    aliases: set[str],
) -> tuple[str, ...] | None:
    positional = [
        argument.arg for argument in (*function.args.posonlyargs, *function.args.args)
    ]
    keyword_names = {argument.arg for argument in function.args.kwonlyargs} | set(
        positional
    )
    selected: list[str] = []
    for index, argument in enumerate(call.args):
        used = {
            node.id
            for node in ast.walk(argument)
            if isinstance(node, ast.Name) and node.id in aliases
        }
        if not used:
            continue
        if (
            index >= len(positional)
            or not isinstance(argument, ast.Name)
            or argument.id not in aliases
        ):
            return None
        selected.append(positional[index])
    for keyword in call.keywords:
        used = {
            node.id
            for node in ast.walk(keyword.value)
            if isinstance(node, ast.Name) and node.id in aliases
        }
        if not used:
            continue
        if (
            keyword.arg is None
            or keyword.arg not in keyword_names
            or not isinstance(keyword.value, ast.Name)
            or keyword.value.id not in aliases
        ):
            return None
        selected.append(keyword.arg)
    return tuple(selected)


def _safe_client_method_call(node: ast.AST, aliases: set[str]) -> bool:
    if isinstance(node, ast.Await):
        node = node.value
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in aliases
        and node.func.attr
        in {
            *{method.lower() for method in HTTP_METHODS},
            "request",
            "close",
            "aclose",
        }
    )


def _helper_preserves_client_parameters(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    parameters: tuple[str, ...],
    helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    seen: set[tuple[str, tuple[str, ...]]],
) -> bool:
    key = (function.name, tuple(sorted(parameters)))
    if key in seen:
        return False
    seen = {*seen, key}
    aliases = set(parameters)
    changed = True
    nodes = [
        item for statement in function.body for item in _executable_nodes(statement)
    ]
    while changed:
        changed = False
        for node in nodes:
            targets, value = _statement_assignment(node)
            if (
                len(targets) == 1
                and isinstance(value, ast.Name)
                and value.id in aliases
                and targets[0] not in aliases
            ):
                aliases.add(targets[0])
                changed = True
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            if node is not function and any(
                isinstance(item, ast.Name) and item.id in aliases
                for item in ast.walk(node)
            ):
                return False
            continue
        if isinstance(node, ast.Assign):
            if any(_root_name(target) in aliases for target in node.targets):
                return False
            if any(
                isinstance(item, ast.Name) and item.id in aliases
                for item in ast.walk(node.value)
            ) and not (
                _safe_client_method_call(node.value, aliases)
                or (
                    len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in aliases
                )
            ):
                return False
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            if _root_name(node.target) in aliases:
                return False
        elif isinstance(node, ast.Delete) and any(
            _root_name(target) in aliases for target in node.targets
        ):
            return False
        if not isinstance(node, ast.Call):
            continue
        if _mutation_call(node, aliases, {}):
            return False
        if _safe_client_method_call(node, aliases):
            continue
        if _root_name(node.func) in aliases:
            return False
        if not any(
            isinstance(item, ast.Name) and item.id in aliases
            for argument in (*node.args, *(item.value for item in node.keywords))
            for item in ast.walk(argument)
        ):
            continue
        name = _ast_dotted_name(node.func)
        helper = helpers.get(name or "")
        if helper is None:
            return False
        selected = _call_parameters_receiving_aliases(node, helper, aliases)
        if not selected or not _helper_preserves_client_parameters(
            helper, selected, helpers, seen
        ):
            return False
    return True


def _safe_local_client_helper_call(
    call: ast.Call,
    clients: set[str],
    helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
) -> bool:
    name = _ast_dotted_name(call.func)
    helper = helpers.get(name or "")
    if helper is None:
        return False
    parameters = _call_parameters_receiving_aliases(call, helper, clients)
    return bool(parameters) and _helper_preserves_client_parameters(
        helper, parameters, helpers, set()
    )


def _invalidate_evidence_referenced_by_call(
    node: ast.AST,
    values: dict[str, Any],
    clients: set[str],
    tainted: set[str],
    responses: dict[str, _ApiRequest],
    source: str,
    helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
) -> bool:
    call = node.value if isinstance(node, ast.Expr) else node
    if isinstance(call, ast.Await):
        call = call.value
    if not isinstance(call, ast.Call):
        return False
    if _call_request(call, values, clients, source) is not None:
        return False
    names = {item.id for item in _executable_nodes(call) if isinstance(item, ast.Name)}
    if names & tainted:
        return True
    helper = helpers.get(_ast_dotted_name(call.func) or "")
    if helper is not None:
        if any(
            isinstance(item, ast.Name) and item.id in tainted
            for item in ast.walk(helper)
        ):
            return True
        captured_clients = tuple(
            sorted(
                {
                    item.id
                    for item in ast.walk(helper)
                    if isinstance(item, ast.Name) and item.id in clients
                }
            )
        )
        if captured_clients and not _helper_preserves_client_parameters(
            helper, captured_clients, helpers, set()
        ):
            return True
        captured_responses = {
            item.id
            for item in ast.walk(helper)
            if isinstance(item, ast.Name) and item.id in responses
        }
        invalidated = [responses[binding] for binding in captured_responses]
        for binding, request in tuple(responses.items()):
            if any(request is item for item in invalidated):
                responses.pop(binding, None)
    if names & clients and not _safe_local_client_helper_call(call, clients, helpers):
        return True
    invalidated = [responses[binding] for binding in names & set(responses)]
    for binding, request in tuple(responses.items()):
        if any(request is item for item in invalidated):
            responses.pop(binding, None)
    return False


def _fixture_derived(node: ast.AST | None, fixture_sources: set[str]) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Await):
        return _fixture_derived(node.value, fixture_sources)
    if isinstance(node, ast.Name):
        return node.id in fixture_sources
    if isinstance(node, ast.Call):
        return any(
            _fixture_derived(argument, fixture_sources)
            for argument in (*node.args, *(keyword.value for keyword in node.keywords))
        )
    return False


def _trusted_client_constructors(tree: ast.Module) -> set[str]:
    constructors: set[str] = set()
    modules = {"httpx", "fastapi.testclient", "starlette.testclient"}
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom) and statement.module in modules:
            for name in statement.names:
                if name.name in {"AsyncClient", "TestClient"}:
                    constructors.add(name.asname or name.name)
        elif isinstance(statement, ast.Import):
            for name in statement.names:
                if name.name in modules:
                    alias = name.asname or name.name
                    constructors.update({f"{alias}.AsyncClient", f"{alias}.TestClient"})
    return constructors


def _derived_client_names(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    constructors: set[str],
) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                expression = item.context_expr
                if isinstance(expression, ast.Await):
                    expression = expression.value
                if (
                    isinstance(expression, ast.Call)
                    and _ast_dotted_name(expression.func) in constructors
                    and isinstance(item.optional_vars, ast.Name)
                ):
                    names.add(item.optional_vars.id)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets, value = _statement_assignment(node)
            if isinstance(value, ast.Await):
                value = value.value
            if (
                len(targets) == 1
                and isinstance(value, ast.Call)
                and _ast_dotted_name(value.func) in constructors
            ):
                names.add(targets[0])
    return names


def _walk_api_block(
    statements: list[ast.stmt],
    values: dict[str, Any],
    clients: set[str],
    tainted: set[str],
    fixture_sources: set[str],
    responses: dict[str, _ApiRequest],
    requests: list[_ApiRequest],
    source: str,
    helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
) -> bool:
    for statement in statements:
        if _pytest_terminator(statement):
            return True
        if isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
            return True
        if isinstance(statement, ast.Assign):
            if any(
                _mutation_target(target, clients | tainted, responses)
                for target in statement.targets
            ):
                return True
        elif (
            isinstance(statement, ast.AnnAssign)
            and _mutation_target(statement.target, clients | tainted, responses)
            or isinstance(statement, ast.AugAssign)
            and _mutation_target(statement.target, clients | tainted, responses)
            or isinstance(statement, ast.Delete)
            and any(
                _mutation_target(target, clients | tainted, responses)
                for target in statement.targets
            )
        ):
            return True
        targets, value_node = _statement_assignment(statement)
        target = _assignment_target(statement)
        alias_roots = (
            _identity_alias_roots(value_node) if value_node is not None else set()
        )
        if (
            value_node is not None
            and not targets
            and alias_roots & (clients | tainted | set(responses))
        ):
            return True
        if targets:
            value_node = statement.value
            request = (
                _call_request(value_node, values, clients, source)
                if target is not None
                else None
            )
            if request is not None and len(targets) == 1:
                method, paths, anchor = request
                event = _ApiRequest(
                    method=method,
                    paths=paths,
                    anchor=anchor,
                    binding=target.id,
                    position=_position(value_node),
                    span=_span(value_node),
                )
                requests.append(event)
                responses[target.id] = event
                values[target.id] = event
                clients.discard(target.id)
                tainted.discard(target.id)
            elif (
                target is not None
                and isinstance(value_node, ast.Name)
                and value_node.id in clients
            ):
                responses.pop(target.id, None)
                values[target.id] = _DYNAMIC
                clients.add(target.id)
                tainted.discard(target.id)
            elif (
                target is not None
                and isinstance(value_node, ast.Name)
                and value_node.id in responses
            ):
                event = responses[value_node.id]
                responses[target.id] = event
                values[target.id] = event
                clients.discard(target.id)
                tainted.discard(target.id)
            elif target is not None and alias_roots & (
                clients | tainted | set(responses)
            ):
                responses.pop(target.id, None)
                values[target.id] = _DYNAMIC
                clients.discard(target.id)
                tainted.add(target.id)
            else:
                for name in targets:
                    responses.pop(name, None)
                    values.pop(name, None)
                    tainted.discard(name)
                    if name in CLIENT_NAMES:
                        if _fixture_derived(value_node, fixture_sources):
                            clients.add(name)
                        else:
                            clients.discard(name)
                if target is not None and value_node is not None:
                    values[target.id] = _literal(value_node, values)
            continue
        if isinstance(statement, ast.Assert):
            anchor = ast.get_source_segment(source, statement)
            if not anchor:
                continue
            statement_span = _span(statement)
            names = {
                node.id
                for node in ast.walk(statement.test)
                if isinstance(node, ast.Name)
            }
            for binding, request in responses.items():
                if (
                    binding in names
                    and _direct_response_assertion(statement.test, binding)
                    and _position(statement) >= request.position
                    and not any(
                        span == statement_span for span, _ in request.assertions
                    )
                ):
                    request.assertions.append((statement_span, anchor))
            continue
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            immediate = [
                *statement.decorator_list,
                *statement.args.defaults,
                *(item for item in statement.args.kw_defaults if item is not None),
            ]
            if any(
                isinstance(node, ast.Name)
                and node.id in clients | tainted | set(responses)
                for item in immediate
                for node in ast.walk(item)
            ):
                return True
            continue
        if isinstance(statement, (ast.Lambda, ast.ClassDef)):
            if any(
                isinstance(node, ast.Name)
                and node.id in clients | tainted | set(responses)
                for node in ast.walk(statement)
            ):
                return True
            continue
        if isinstance(statement, ast.Expr):
            if _mutation_call(statement.value, clients | tainted, responses):
                return True
            if _safe_client_cleanup(statement, clients):
                continue
            if _invalidate_evidence_referenced_by_call(
                statement, values, clients, tainted, responses, source, helpers
            ):
                return True
        if isinstance(statement, ast.For):
            loop_values = _literal(statement.iter, values)
            if not isinstance(loop_values, tuple) or not loop_values:
                if _control_flow_affects_evidence(
                    statement.body, clients | tainted, responses
                ):
                    return True
                _poison_control_flow_assignments(statement.body, values, responses)
                continue
            for item in loop_values[:MAX_STATIC_VALUES]:
                loop_values_map = dict(values)
                loop_responses = dict(responses)
                if isinstance(statement.target, ast.Name):
                    loop_values_map[statement.target.id] = item
                    if isinstance(item, _ApiRequest):
                        loop_responses[statement.target.id] = item
                    else:
                        loop_responses.pop(statement.target.id, None)
                elif (
                    isinstance(statement.target, (ast.Tuple, ast.List))
                    and isinstance(item, tuple)
                    and len(statement.target.elts) == len(item)
                ):
                    valid_target = True
                    for target, value in zip(statement.target.elts, item):
                        if not isinstance(target, ast.Name) or value is _UNKNOWN:
                            valid_target = False
                            break
                        loop_values_map[target.id] = value
                        if isinstance(value, _ApiRequest):
                            loop_responses[target.id] = value
                        else:
                            loop_responses.pop(target.id, None)
                    if not valid_target:
                        continue
                else:
                    continue
                if _walk_api_block(
                    statement.body,
                    loop_values_map,
                    set(clients),
                    set(tainted),
                    set(fixture_sources),
                    loop_responses,
                    requests,
                    source,
                    helpers,
                ):
                    return True
            continue
        if isinstance(statement, ast.If):
            truth = _static_truth(statement.test, values)
            if truth is True:
                if _walk_api_block(
                    statement.body,
                    dict(values),
                    set(clients),
                    set(tainted),
                    set(fixture_sources),
                    dict(responses),
                    requests,
                    source,
                    helpers,
                ):
                    return True
            elif truth is False:
                if _walk_api_block(
                    statement.orelse,
                    dict(values),
                    set(clients),
                    set(tainted),
                    set(fixture_sources),
                    dict(responses),
                    requests,
                    source,
                    helpers,
                ):
                    return True
            else:
                if _control_flow_affects_evidence(
                    [*statement.body, *statement.orelse], clients | tainted, responses
                ):
                    return True
                _poison_control_flow_assignments(
                    [*statement.body, *statement.orelse], values, responses
                )
            continue
        if isinstance(statement, ast.Try):
            if statement.handlers or statement.orelse:
                branches = [
                    *statement.body,
                    *statement.finalbody,
                    *(item for handler in statement.handlers for item in handler.body),
                    *statement.orelse,
                ]
                if _control_flow_affects_evidence(
                    branches, clients | tainted, responses
                ):
                    return True
                _poison_control_flow_assignments(branches, values, responses)
                continue
            if not _safe_finally(statement.finalbody, clients | tainted, responses):
                branches = [*statement.body, *statement.finalbody]
                if _control_flow_affects_evidence(
                    branches, clients | tainted, responses
                ):
                    return True
                _poison_control_flow_assignments(branches, values, responses)
                continue
            if _walk_api_block(
                statement.body,
                values,
                clients,
                tainted,
                fixture_sources,
                responses,
                requests,
                source,
                helpers,
            ):
                return True
            continue
        if isinstance(statement, (ast.With, ast.AsyncWith)):
            if _walk_api_block(
                statement.body,
                values,
                clients,
                tainted,
                fixture_sources,
                responses,
                requests,
                source,
                helpers,
            ):
                return True
            continue
        if isinstance(statement, ast.While):
            if _control_flow_affects_evidence(
                statement.body, clients | tainted, responses
            ):
                return True
            _poison_control_flow_assignments(statement.body, values, responses)
            continue
    return False


@lru_cache(maxsize=512)
def _api_test_analysis(source: str, test_name: str) -> list[_ApiRequest]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    if _module_skipped(tree):
        return []
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        and node.name == test_name
        and not _skipped_test(node)
    ]
    if len(functions) != 1:
        return []
    requests: list[_ApiRequest] = []
    helpers = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("test_")
    }
    helpers.update(
        {
            node.name: node
            for node in functions[0].body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
    )
    fixture_sources = {
        argument.arg
        for argument in (
            *functions[0].args.posonlyargs,
            *functions[0].args.args,
            *functions[0].args.kwonlyargs,
        )
        if argument.arg in CLIENT_NAMES or argument.arg.endswith("_client")
    }
    clients = fixture_sources | _derived_client_names(
        functions[0], _trusted_client_constructors(tree)
    )
    _walk_api_block(
        functions[0].body,
        _module_values(tree),
        set(clients),
        set(),
        fixture_sources,
        {},
        requests,
        source,
        helpers,
    )
    return requests


def _route_matches(route_path: str, request_path: str) -> bool:
    request_path = request_path.split("?", 1)[0].split("#", 1)[0]
    if not request_path.startswith("/") or "%" in request_path:
        return False
    route_parts = route_path.split("/")
    request_parts = request_path.split("/")
    if len(route_parts) != len(request_parts):
        return False
    for route_part, request_part in zip(route_parts, request_parts):
        if _ROUTE_PARAMETER_RE.fullmatch(route_part):
            if request_part.endswith(".md") or not request_part:
                return False
        elif "{" in route_part:
            pattern = re.escape(route_part)
            pattern = re.sub(
                r"\\\{[A-Za-z][A-Za-z0-9_]*\\\}",
                r"[^/]+",
                pattern,
            )
            if not re.fullmatch(pattern, request_part):
                return False
        elif route_part != request_part:
            return False
    return True


def _api_witness_errors(
    route: str,
    record: Any,
    owner: str,
    allowlists: dict[str, set[str]],
    root: Path,
    errors: list[str],
    request_ledger: set[tuple[Any, ...]],
    assertion_ledger: set[tuple[Any, ...]],
) -> None:
    location = f"api_routes.{route}"
    item = _object(record, location, API_ROUTE_FIELDS, errors)
    if item is None:
        return
    if item.get("owner") != owner:
        _error(errors, f"{location}.owner", f"must equal route owner {owner!r}")
    witness = _object(
        item.get("witness"), f"{location}.witness", API_WITNESS_FIELDS, errors
    )
    if witness is None:
        return
    test_path = witness.get("test_path")
    if test_path not in allowlists.get(owner, set()):
        _error(
            errors, f"{location}.witness.test_path", "is not allowlisted by its owner"
        )
    path = _safe_relative_path(test_path, f"{location}.witness.test_path", root, errors)
    if path is None:
        return
    if not (
        str(test_path).startswith("apps/api/tests/") and str(test_path).endswith(".py")
    ):
        _error(
            errors, f"{location}.witness.test_path", "must be an API Python test path"
        )
        return
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        _error(errors, f"{location}.witness.test_path", f"cannot read source: {exc}")
        return
    test_name = witness.get("test")
    if not isinstance(test_name, str) or not test_name.startswith("test_"):
        _error(errors, f"{location}.witness.test", "must be a top-level test_ function")
        return
    method, route_path = route.split(" ", 1)
    request_anchor = witness.get("request_anchor")
    request_path = witness.get("request_path")
    binding = witness.get("response_binding")
    if not isinstance(request_anchor, str) or not request_anchor:
        _error(errors, f"{location}.witness.request_anchor", "must be non-empty")
    if not isinstance(request_path, str) or not request_path:
        _error(errors, f"{location}.witness.request_path", "must be non-empty")
    if not isinstance(binding, str) or not _IDENTIFIER_RE.fullmatch(binding):
        _error(errors, f"{location}.witness.response_binding", "must be an identifier")
    if witness.get("method") != method or method not in HTTP_METHODS:
        _error(errors, f"{location}.witness.method", f"must equal {method!r}")
    if not isinstance(request_anchor, str) or not isinstance(request_path, str):
        return
    if not isinstance(binding, str):
        return
    requests = _api_test_analysis(source, test_name)
    candidates = [
        request
        for request in requests
        if request.anchor == request_anchor
        and request.binding == binding
        and request.method == method
        and any(
            _path_descriptor(path) == request_path
            and _route_matches(route_path, _path_descriptor(path))
            for path in request.paths
        )
    ]
    if len(candidates) != 1:
        _error(
            errors,
            f"{location}.witness.request_anchor",
            "must equal one direct assigned request call with exact method and route",
        )
        return
    request = candidates[0]
    comment_spans = _python_comment_spans(source)
    if any(_span_overlaps(request.span, comment) for comment in comment_spans):
        _error(
            errors,
            f"{location}.witness.request_anchor",
            "selected request call overlaps a Python comment",
        )
    request_identity = (str(test_path), test_name, "request", request.span)
    if request_identity in request_ledger:
        _error(
            errors,
            f"{location}.witness.request_anchor",
            "physical request anchor is reused",
        )
    request_ledger.add(request_identity)
    assertions = _strings(
        witness.get("assertion_anchors"),
        f"{location}.witness.assertion_anchors",
        errors,
    )
    if len(assertions) < 2:
        _error(
            errors,
            f"{location}.witness.assertion_anchors",
            "requires at least two assertions",
        )
    if len(set(assertions)) != len(assertions):
        _error(
            errors,
            f"{location}.witness.assertion_anchors",
            "must not reuse an assertion",
        )
    for index, anchor in enumerate(assertions):
        matches = [span for span, text in request.assertions if text == anchor]
        if len(matches) != 1:
            _error(
                errors,
                f"{location}.witness.assertion_anchors[{index}]",
                "must equal an exact ast.Assert span tied to the selected response",
            )
            continue
        if any(_span_overlaps(matches[0], comment) for comment in comment_spans):
            _error(
                errors,
                f"{location}.witness.assertion_anchors[{index}]",
                "selected assert statement overlaps a Python comment",
            )
        assertion_identity = (str(test_path), test_name, "assertion", matches[0])
        if assertion_identity in assertion_ledger:
            _error(
                errors,
                f"{location}.witness.assertion_anchors[{index}]",
                "physical assertion anchor is reused",
            )
        assertion_ledger.add(assertion_identity)


def _ui_witness_errors(
    route: str,
    record: Any,
    owner: str,
    allowlists: dict[str, set[str]],
    root: Path,
    errors: list[str],
    case_ledger: set[tuple[Any, ...]],
    assertion_ledger: set[tuple[Any, ...]],
) -> None:
    location = f"ui_routes.{route}"
    item = _object(record, location, UI_ROUTE_FIELDS, errors)
    if item is None:
        return
    if item.get("owner") != owner:
        _error(errors, f"{location}.owner", f"must equal UI route owner {owner!r}")
    witness = _object(
        item.get("witness"), f"{location}.witness", UI_WITNESS_FIELDS, errors
    )
    if witness is None:
        return
    test_path = witness.get("test_path")
    if test_path not in allowlists.get(owner, set()):
        _error(
            errors, f"{location}.witness.test_path", "is not allowlisted by its owner"
        )
    path = _safe_relative_path(test_path, f"{location}.witness.test_path", root, errors)
    if path is None:
        return
    if not (
        str(test_path).startswith("apps/web/tests/")
        and str(test_path).endswith((".ts", ".tsx"))
    ):
        _error(errors, f"{location}.witness.test_path", "must be a web test path")
        return
    try:
        source = path.read_text(encoding="utf-8")
        cases = _ui_cases(source)
    except OSError as exc:
        _error(errors, f"{location}.witness.test_path", f"cannot read source: {exc}")
        return
    test_name = witness.get("test")
    matches = [case for case in cases if case.name == test_name]
    if len(matches) != 1:
        _error(
            errors,
            f"{location}.witness.test",
            "must select exactly one it/test callback",
        )
        return
    case = matches[0]
    if case.invalid:
        _error(
            errors,
            f"{location}.witness.test",
            "selected case contains unsupported control flow, shadowing, or helper evidence",
        )
        return
    if witness.get("evidence_mode") != EVIDENCE_MODE:
        _error(
            errors, f"{location}.witness.evidence_mode", "only page_source is accepted"
        )
    page_module, relative_module = _ui_page_paths(route)
    if witness.get("page_module") != page_module:
        _error(errors, f"{location}.witness.page_module", f"must equal {page_module!r}")
    binding = witness.get("source_binding")
    case_witness = witness.get("case_witness")
    if not isinstance(binding, str) or not _IDENTIFIER_RE.fullmatch(binding):
        _error(errors, f"{location}.witness.source_binding", "must be an identifier")
        return
    if not isinstance(case_witness, str) or not case_witness:
        _error(errors, f"{location}.witness.case_witness", "must be non-empty")
        return
    reads = [
        read
        for read in case.source_reads
        if read.binding == binding
        and read.relative_module == relative_module
        and read.anchor == case_witness
    ]
    if len(reads) != 1:
        _error(
            errors,
            f"{location}.witness.case_witness",
            "must equal one exact direct readFileSync page-source declaration",
        )
        return
    if any(
        _offset_span_overlaps(reads[0].span, comment) for comment in case.comment_spans
    ):
        _error(
            errors,
            f"{location}.witness.case_witness",
            "selected page-source declaration overlaps a JavaScript comment",
        )
    assertion_anchors = witness.get("assertion_anchors")
    if isinstance(assertion_anchors, list):
        assertion_end = max(
            (
                assertion.span[1]
                for assertion in case.assertions
                if assertion.binding == binding
                and assertion.anchor in assertion_anchors
                and assertion.span[0] > reads[0].span[1]
            ),
            default=reads[0].span[1],
        )
        if any(
            name == binding and reads[0].span[1] < position <= assertion_end
            for name, position in case.reassignments
        ):
            _error(
                errors,
                f"{location}.witness.source_binding",
                "selected page-source binding is reassigned before its assertions",
            )
    case_identity = (str(test_path), str(test_name), "source", reads[0].span)
    if case_identity in case_ledger:
        _error(
            errors,
            f"{location}.witness.case_witness",
            "physical source-read anchor is reused",
        )
    case_ledger.add(case_identity)
    assertions = _strings(
        witness.get("assertion_anchors"),
        f"{location}.witness.assertion_anchors",
        errors,
    )
    if len(assertions) < 2:
        _error(
            errors,
            f"{location}.witness.assertion_anchors",
            "requires at least two assertions",
        )
    if len(set(assertions)) != len(assertions):
        _error(
            errors,
            f"{location}.witness.assertion_anchors",
            "must not reuse an assertion",
        )
    for index, anchor in enumerate(assertions):
        matches = [
            assertion.span
            for assertion in case.assertions
            if (
                assertion.binding == binding
                and assertion.anchor == anchor
                and assertion.span[0] > reads[0].span[1]
            )
        ]
        if len(matches) != 1:
            _error(
                errors,
                f"{location}.witness.assertion_anchors[{index}]",
                "must equal an exact direct expect(sourceBinding) statement",
            )
            continue
        if any(
            _offset_span_overlaps(matches[0], comment) for comment in case.comment_spans
        ):
            _error(
                errors,
                f"{location}.witness.assertion_anchors[{index}]",
                "selected expect statement overlaps a JavaScript comment",
            )
        assertion_identity = (str(test_path), str(test_name), "assertion", matches[0])
        if assertion_identity in assertion_ledger:
            _error(
                errors,
                f"{location}.witness.assertion_anchors[{index}]",
                "physical assertion anchor is reused",
            )
        assertion_ledger.add(assertion_identity)


def validate_source_witnesses(
    root: Path,
    route_ownership: dict[str, str],
    ui_route_ownership: dict[str, str],
    features: Any,
    witness_path: Path | None = None,
) -> list[str]:
    """Validate exhaustive source-witness traceability, not runtime completeness."""
    errors: list[str] = []
    if witness_path is None:
        witness_path = (
            root / "packages/platform-contract/platform-route-test-ownership.json"
        )
    try:
        registry = json.loads(witness_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"route-test registry: cannot load JSON: {exc}"]
    root_object = _object(
        registry,
        "route_test_registry",
        {"schema_version", "registry_id", "api_routes", "ui_routes"},
        errors,
    )
    if root_object is None:
        return errors
    if root_object.get("schema_version") != 1:
        _error(errors, "route_test_registry.schema_version", "must equal 1")
    if root_object.get("registry_id") != "connect-md-platform-route-test-ownership":
        _error(errors, "route_test_registry.registry_id", "has an invalid registry id")
    api_routes = root_object.get("api_routes")
    ui_routes = root_object.get("ui_routes")
    if not isinstance(api_routes, dict):
        _error(errors, "route_test_registry.api_routes", "must be an object")
        api_routes = {}
    if not isinstance(ui_routes, dict):
        _error(errors, "route_test_registry.ui_routes", "must be an object")
        ui_routes = {}
    for label, actual, expected in (
        ("api_routes", api_routes, route_ownership),
        ("ui_routes", ui_routes, ui_route_ownership),
    ):
        missing = sorted(set(expected) - set(actual))
        stale = sorted(set(actual) - set(expected))
        if missing:
            _error(errors, label, f"is missing routes: {', '.join(missing)}")
        if stale:
            _error(errors, label, f"has stale routes: {', '.join(stale)}")
    allowlists = _feature_test_allowlists(features, errors)
    request_ledger: set[tuple[Any, ...]] = set()
    assertion_ledger: set[tuple[Any, ...]] = set()
    case_ledger: set[tuple[Any, ...]] = set()
    for route, owner in route_ownership.items():
        if route in api_routes:
            _api_witness_errors(
                route,
                api_routes[route],
                owner,
                allowlists,
                root,
                errors,
                request_ledger,
                assertion_ledger,
            )
    for route, owner in ui_route_ownership.items():
        if route in ui_routes:
            _ui_witness_errors(
                route,
                ui_routes[route],
                owner,
                allowlists,
                root,
                errors,
                case_ledger,
                assertion_ledger,
            )
    return errors


__all__ = ["validate_source_witnesses"]
