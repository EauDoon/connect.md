"""Dependency-light static API and Next.js route inventory helpers."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

ROUTE_RE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) (/.+)$")
NEXT_DYNAMIC_SEGMENT_RE = re.compile(r"^\[([A-Za-z][A-Za-z0-9_]*)\]$")
CONTRACT_DYNAMIC_SEGMENT_RE = re.compile(r"^\{([A-Za-z][A-Za-z0-9_]*)\}$")
NEXT_PAGE_NAMES = {"page.js", "page.jsx", "page.ts", "page.tsx"}


def _error(errors: list[str], location: str, message: str) -> None:
    errors.append(f"{location}: {message}")


def _repo_path(value: str, root: Path, location: str, errors: list[str]) -> None:
    candidate = Path(value)
    if (
        candidate.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        _error(errors, location, f"is not a safe repository-relative path: {value!r}")
        return
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        _error(errors, location, f"escapes repository root: {value!r}")
        return
    if not resolved.is_file():
        _error(errors, location, f"does not exist as a file: {value!r}")


_ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}
_ROUTE_DECORATOR_METHODS = _ROUTE_METHODS | {
    "api_route",
    "head",
    "options",
    "trace",
    "websocket",
}
_RouterRef = tuple[Path, str]


@dataclass(eq=False)
class _RouteRecord:
    hidden: bool
    decorator_body: str
    source_path: str
    line: int


@dataclass(eq=False)
class _RouterSpec:
    prefix: str | None
    include_in_schema: bool | None
    source_path: str
    line: int


@dataclass(eq=False)
class _RouterRoute:
    receiver: ast.AST
    method: str
    path: str | None
    include_in_schema: bool | None
    decorator_body: str
    source_path: str
    line: int


@dataclass(eq=False)
class _RouterInclude:
    receiver: ast.AST
    child: ast.AST | None
    prefix: str | None
    include_in_schema: bool | None
    source_path: str
    line: int


class _RouteModule:
    def __init__(
        self,
        path: Path,
        source: str,
        tree: ast.Module,
        strings: dict[str, str],
        booleans: dict[str, bool],
    ) -> None:
        self.path = path
        self.source = source
        self.tree = tree
        self.strings = strings
        self.booleans = booleans
        self.routers: dict[str, _RouterSpec] = {}
        self.router_bindings: dict[str, _RouterRef] = {}
        self.module_bindings: dict[str, Path] = {}
        self.routes: list[_RouterRoute] = []
        self.includes: list[_RouterInclude] = []


class _RouteInventory:
    def __init__(self, routes: dict[str, _RouteRecord]) -> None:
        self.routes = routes


def _route_source_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _route_keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _route_static_string(node: ast.AST | None, bindings: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _route_static_string(node.left, bindings)
        right = _route_static_string(node.right, bindings)
        if left is not None and right is not None:
            return left + right
    return None


def _route_string_bindings(body: list[ast.stmt]) -> dict[str, str]:
    bindings: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for statement in body:
            name: str | None = None
            value_node: ast.AST | None = None
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
            ):
                name = statement.targets[0].id
                value_node = statement.value
            elif isinstance(statement, ast.AnnAssign) and isinstance(
                statement.target, ast.Name
            ):
                name = statement.target.id
                value_node = statement.value
            if name is None or value_node is None:
                continue
            value = _route_static_string(value_node, bindings)
            if value is not None and bindings.get(name) != value:
                bindings[name] = value
                changed = True
    return bindings


def _route_static_bool(node: ast.AST | None, bindings: dict[str, bool]) -> bool | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    return None


def _route_bool_bindings(body: list[ast.stmt]) -> dict[str, bool]:
    bindings: dict[str, bool] = {}
    changed = True
    while changed:
        changed = False
        for statement in body:
            name: str | None = None
            value_node: ast.AST | None = None
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
            ):
                name = statement.targets[0].id
                value_node = statement.value
            elif isinstance(statement, ast.AnnAssign) and isinstance(
                statement.target, ast.Name
            ):
                name = statement.target.id
                value_node = statement.value
            if name is None or value_node is None:
                continue
            value = _route_static_bool(value_node, bindings)
            if value is not None and bindings.get(name) != value:
                bindings[name] = value
                changed = True
    return bindings


def _route_decorator_body(source_lines: list[bytes], decorator: ast.Call) -> str:
    if decorator.end_lineno is None or decorator.end_col_offset is None:
        return ""
    first = decorator.lineno - 1
    last = decorator.end_lineno - 1
    if first == last:
        segment_bytes = source_lines[first][
            decorator.col_offset : decorator.end_col_offset
        ]
    else:
        segment_bytes = b"".join(
            (
                source_lines[first][decorator.col_offset :],
                *source_lines[first + 1 : last],
                source_lines[last][: decorator.end_col_offset],
            )
        )
    segment = segment_bytes.decode("utf-8")
    opening = segment.find("(")
    closing = segment.rfind(")")
    if opening >= 0 and closing > opening:
        return segment[opening + 1 : closing]
    return segment


def _route_assignment_name(statement: ast.stmt) -> tuple[str, ast.AST] | None:
    if (
        isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
    ):
        return statement.targets[0].id, statement.value
    if (
        isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
        and statement.value is not None
    ):
        return statement.target.id, statement.value
    return None


def _route_module_parts(path: Path, api_root: Path) -> tuple[str, ...]:
    relative = path.resolve().relative_to(api_root.resolve())
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts.pop()
    else:
        parts[-1] = Path(parts[-1]).stem
    return tuple(parts)


def _route_module_candidate(api_root: Path, parts: tuple[str, ...]) -> Path | None:
    if not parts:
        return None
    base = api_root.joinpath(*parts)
    module = base.with_suffix(".py")
    package = base / "__init__.py"
    if module.is_file():
        return module.resolve()
    if package.is_file():
        return package.resolve()
    return None


def _route_local_module(
    root: Path,
    current_path: Path,
    module: str | None,
    level: int,
) -> Path | None:
    api_root = (root / "apps/api").resolve()
    try:
        current_parts = _route_module_parts(current_path, api_root)
    except (OSError, ValueError, IndexError):
        return None
    if level:
        package_parts = (
            current_parts if current_path.name == "__init__.py" else current_parts[:-1]
        )
        up = level - 1
        if up > len(package_parts):
            return None
        parts = list(package_parts[: len(package_parts) - up])
        if module:
            parts.extend(module.split("."))
    else:
        if not module:
            return None
        raw_parts = module.split(".")
        if raw_parts[:3] == ["apps", "api", "app"]:
            parts = raw_parts[2:]
        elif raw_parts[0] == "app":
            parts = raw_parts
        else:
            return None
    return _route_module_candidate(api_root, tuple(parts))


def _route_child_module(path: Path, name: str) -> Path | None:
    if path.name != "__init__.py":
        return None
    base = path.parent / name
    module = base.with_suffix(".py")
    package = base / "__init__.py"
    if module.is_file():
        return module.resolve()
    if package.is_file():
        return package.resolve()
    return None


def _route_call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _route_module_expr(module: _RouteModule, expression: ast.AST) -> Path | None:
    if isinstance(expression, ast.Name):
        return module.module_bindings.get(expression.id)
    if isinstance(expression, ast.Attribute):
        base = _route_module_expr(module, expression.value)
        if base is not None:
            return _route_child_module(base, expression.attr)
    return None


def _route_router_expr(module: _RouteModule, expression: ast.AST) -> _RouterRef | None:
    if isinstance(expression, ast.Name):
        if expression.id in module.routers:
            return module.path, expression.id
        return module.router_bindings.get(expression.id)
    if isinstance(expression, ast.Attribute):
        base = _route_module_expr(module, expression.value)
        if base is not None:
            return base, expression.attr
    return None


def _route_module_analysis(
    root: Path,
    path: Path,
    errors: list[str],
    cache: dict[Path, _RouteModule],
    source_override: str | None = None,
) -> _RouteModule | None:
    path = path.resolve()
    if path in cache:
        return cache[path]
    try:
        source = (
            source_override
            if source_override is not None
            else path.read_text(encoding="utf-8")
        )
        tree = ast.parse(source)
    except (OSError, SyntaxError) as exc:
        _error(
            errors,
            "repository.routes",
            f"cannot parse {_route_source_path(root, path)}: {exc}",
        )
        return None

    source_lines = source.encode("utf-8").splitlines(keepends=True)
    module = _RouteModule(
        path=path,
        source=source,
        tree=tree,
        strings=_route_string_bindings(tree.body),
        booleans=_route_bool_bindings(tree.body),
    )
    cache[path] = module
    factory_names = {"APIRouter"}

    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom):
            if statement.module in {"fastapi", "fastapi.routing"}:
                for alias in statement.names:
                    if alias.name == "APIRouter":
                        factory_names.add(alias.asname or alias.name)
            target = _route_local_module(root, path, statement.module, statement.level)
            if target is None:
                continue
            for alias in statement.names:
                if alias.name == "*":
                    continue
                local_name = alias.asname or alias.name
                child = _route_child_module(target, alias.name)
                if child is not None:
                    module.module_bindings[local_name] = child
                else:
                    module.router_bindings[local_name] = (target, alias.name)
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                target = _route_local_module(root, path, alias.name, 0)
                if target is None:
                    continue
                if alias.asname:
                    module.module_bindings[alias.asname] = target
                else:
                    top = alias.name.split(".", 1)[0]
                    top_target = _route_local_module(root, path, top, 0)
                    if top_target is not None:
                        module.module_bindings[top] = top_target

    for statement in tree.body:
        assignment = _route_assignment_name(statement)
        if assignment is None:
            continue
        name, value = assignment
        if (
            not isinstance(value, ast.Call)
            or _route_call_name(value) not in factory_names
        ):
            continue
        prefix_node = _route_keyword(value, "prefix")
        prefix = (
            ""
            if prefix_node is None
            else _route_static_string(prefix_node, module.strings)
        )
        schema_node = _route_keyword(value, "include_in_schema")
        include_in_schema = (
            True
            if schema_node is None
            else _route_static_bool(schema_node, module.booleans)
        )
        module.routers[name] = _RouterSpec(
            prefix=prefix,
            include_in_schema=include_in_schema,
            source_path=_route_source_path(root, path),
            line=value.lineno,
        )

    for _ in range(len(tree.body) + 1):
        changed = False
        for statement in tree.body:
            assignment = _route_assignment_name(statement)
            if assignment is None:
                continue
            name, value = assignment
            if name in module.routers:
                continue
            binding = _route_router_expr(module, value)
            if binding is not None and module.router_bindings.get(name) != binding:
                module.router_bindings[name] = binding
                changed = True
        if not changed:
            break

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(
                    decorator.func, ast.Attribute
                ):
                    continue
                if decorator.func.attr not in _ROUTE_DECORATOR_METHODS:
                    continue
                path_node = decorator.args[0] if decorator.args else None
                route_path = _route_static_string(path_node, module.strings)
                schema_node = _route_keyword(decorator, "include_in_schema")
                include_in_schema = (
                    True
                    if schema_node is None
                    else _route_static_bool(schema_node, module.booleans)
                )
                module.routes.append(
                    _RouterRoute(
                        receiver=decorator.func.value,
                        method=decorator.func.attr,
                        path=route_path,
                        include_in_schema=include_in_schema,
                        decorator_body=_route_decorator_body(source_lines, decorator),
                        source_path=_route_source_path(root, path),
                        line=decorator.lineno,
                    )
                )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr != "include_router":
                continue
            child = node.args[0] if node.args else _route_keyword(node, "router")
            prefix_node = _route_keyword(node, "prefix")
            prefix = (
                ""
                if prefix_node is None
                else _route_static_string(prefix_node, module.strings)
            )
            schema_node = _route_keyword(node, "include_in_schema")
            include_in_schema = (
                True
                if schema_node is None
                else _route_static_bool(schema_node, module.booleans)
            )
            module.includes.append(
                _RouterInclude(
                    receiver=node.func.value,
                    child=child,
                    prefix=prefix,
                    include_in_schema=include_in_schema,
                    source_path=_route_source_path(root, path),
                    line=node.lineno,
                )
            )
    return module


def _canonical_route_router(
    root: Path,
    reference: _RouterRef,
    errors: list[str],
    cache: dict[Path, _RouteModule],
    active: tuple[_RouterRef, ...] = (),
) -> _RouterRef | None:
    if reference in active:
        _error(
            errors,
            "repository.routes",
            f"router include cycle at {_route_source_path(root, reference[0])}:{reference[1]}",
        )
        return None
    module = _route_module_analysis(root, reference[0], errors, cache)
    if module is None:
        return None
    if reference[1] in module.routers:
        return reference
    binding = module.router_bindings.get(reference[1])
    if binding is not None:
        return _canonical_route_router(
            root, binding, errors, cache, (*active, reference)
        )
    return None


def _route_prefix(
    value: str | None,
    root: Path,
    source_path: str,
    line: int,
    errors: list[str],
) -> str | None:
    location = f"repository.routes.{source_path}:{line}"
    if value is None:
        _error(errors, location, "router prefix must be a statically resolved string")
        return None
    if value and (not value.startswith("/") or value.endswith("/")):
        _error(
            errors,
            location,
            f"router prefix must start with '/' and not end with '/': {value!r}",
        )
        return None
    return value


def _route_record(
    records: dict[str, _RouteRecord],
    route: str,
    record: _RouteRecord,
    errors: list[str],
) -> None:
    if route in records:
        _error(errors, "repository.routes", f"duplicates implemented route {route!r}")
    records[route] = record


def _direct_route_records(
    tree: ast.AST,
    source: str,
    source_path: str,
    errors: list[str],
) -> dict[str, _RouteRecord]:
    records: dict[str, _RouteRecord] = {}
    source_lines = source.encode("utf-8").splitlines(keepends=True)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                not isinstance(decorator, ast.Call)
                or not isinstance(decorator.func, ast.Attribute)
                or not isinstance(decorator.func.value, ast.Name)
                or decorator.func.value.id != "app"
                or decorator.func.attr not in _ROUTE_METHODS
                or not decorator.args
                or not isinstance(decorator.args[0], ast.Constant)
                or not isinstance(decorator.args[0].value, str)
            ):
                continue
            route = f"{decorator.func.attr.upper()} {decorator.args[0].value}"
            hidden = any(
                keyword.arg == "include_in_schema"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is False
                for keyword in decorator.keywords
            )
            _route_record(
                records,
                route,
                _RouteRecord(
                    hidden=hidden,
                    decorator_body=_route_decorator_body(source_lines, decorator),
                    source_path=source_path,
                    line=decorator.lineno,
                ),
                errors,
            )
    return records


def _collect_router_routes(
    root: Path,
    reference: _RouterRef,
    base_prefix: str,
    inherited_hidden: bool,
    records: dict[str, _RouteRecord],
    errors: list[str],
    cache: dict[Path, _RouteModule],
    active: tuple[_RouterRef, ...] = (),
) -> None:
    canonical = _canonical_route_router(root, reference, errors, cache)
    if canonical is None:
        _error(
            errors,
            "repository.routes",
            f"cannot resolve included APIRouter {reference[1]!r} from {_route_source_path(root, reference[0])}",
        )
        return
    if canonical in active:
        _error(
            errors,
            "repository.routes",
            f"router include cycle at {_route_source_path(root, canonical[0])}:{canonical[1]}",
        )
        return
    module = _route_module_analysis(root, canonical[0], errors, cache)
    if module is None:
        return
    spec = module.routers.get(canonical[1])
    if spec is None:
        _error(
            errors,
            "repository.routes",
            f"cannot resolve APIRouter symbol {canonical[1]!r} in {_route_source_path(root, canonical[0])}",
        )
        return
    router_prefix = _route_prefix(
        spec.prefix, root, spec.source_path, spec.line, errors
    )
    if router_prefix is None or spec.include_in_schema is None:
        if spec.include_in_schema is None:
            _error(
                errors,
                f"repository.routes.{spec.source_path}:{spec.line}",
                "APIRouter include_in_schema must be a statically resolved boolean",
            )
        return
    effective_prefix = base_prefix + router_prefix
    hidden = inherited_hidden or not spec.include_in_schema

    for route in sorted(module.routes, key=lambda item: (item.line, item.method)):
        receiver = _route_router_expr(module, route.receiver)
        route_ref = (
            _canonical_route_router(root, receiver, errors, cache)
            if receiver is not None
            else None
        )
        if route_ref != canonical:
            continue
        if route.method not in _ROUTE_METHODS:
            _error(
                errors,
                f"repository.routes.{route.source_path}:{route.line}",
                f"unsupported APIRouter route decorator {route.method!r}",
            )
            continue
        if route.path is None:
            _error(
                errors,
                f"repository.routes.{route.source_path}:{route.line}",
                "APIRouter route path must be a statically resolved string",
            )
            continue
        if route.include_in_schema is None:
            _error(
                errors,
                f"repository.routes.{route.source_path}:{route.line}",
                "route include_in_schema must be a statically resolved boolean",
            )
            continue
        full_path = effective_prefix + route.path
        full_route = f"{route.method.upper()} {full_path}"
        if not ROUTE_RE.fullmatch(full_route):
            _error(
                errors,
                f"repository.routes.{route.source_path}:{route.line}",
                f"has invalid composed route: {full_route!r}",
            )
            continue
        _route_record(
            records,
            full_route,
            _RouteRecord(
                hidden=hidden or not route.include_in_schema,
                decorator_body=route.decorator_body,
                source_path=route.source_path,
                line=route.line,
            ),
            errors,
        )

    for include in sorted(module.includes, key=lambda item: item.line):
        receiver = _route_router_expr(module, include.receiver)
        receiver_ref = (
            _canonical_route_router(root, receiver, errors, cache)
            if receiver is not None
            else None
        )
        if receiver_ref != canonical:
            continue
        if include.child is None:
            _error(
                errors,
                f"repository.routes.{include.source_path}:{include.line}",
                "include_router requires a statically resolvable router argument",
            )
            continue
        child = _route_router_expr(module, include.child)
        child_ref = (
            _canonical_route_router(root, child, errors, cache)
            if child is not None
            else None
        )
        if child_ref is None:
            _error(
                errors,
                f"repository.routes.{include.source_path}:{include.line}",
                "cannot resolve included APIRouter argument",
            )
            continue
        include_prefix = _route_prefix(
            include.prefix,
            root,
            include.source_path,
            include.line,
            errors,
        )
        if include_prefix is None or include.include_in_schema is None:
            if include.include_in_schema is None:
                _error(
                    errors,
                    f"repository.routes.{include.source_path}:{include.line}",
                    "include_router include_in_schema must be a statically resolved boolean",
                )
            continue
        _collect_router_routes(
            root,
            child_ref,
            effective_prefix + include_prefix,
            hidden or not include.include_in_schema,
            records,
            errors,
            cache,
            (*active, canonical),
        )


def _implemented_route_inventory(
    root: Path, api_source: str, errors: list[str]
) -> _RouteInventory:
    main_path = (root / "apps/api/app/main.py").resolve()
    try:
        tree = ast.parse(api_source)
    except SyntaxError as exc:
        _error(errors, "repository.routes", f"cannot parse apps/api/app/main.py: {exc}")
        return _RouteInventory({})
    records = _direct_route_records(
        tree,
        api_source,
        _route_source_path(root, main_path),
        errors,
    )
    cache: dict[Path, _RouteModule] = {}
    main_module = _route_module_analysis(
        root, main_path, errors, cache, source_override=api_source
    )
    if main_module is None:
        return _RouteInventory(records)
    for include in sorted(main_module.includes, key=lambda item: item.line):
        if not (
            isinstance(include.receiver, ast.Name) and include.receiver.id == "app"
        ):
            continue
        if include.child is None:
            _error(
                errors,
                f"repository.routes.{include.source_path}:{include.line}",
                "app.include_router requires a statically resolvable router argument",
            )
            continue
        child = _route_router_expr(main_module, include.child)
        child_ref = (
            _canonical_route_router(root, child, errors, cache)
            if child is not None
            else None
        )
        if child_ref is None:
            _error(
                errors,
                f"repository.routes.{include.source_path}:{include.line}",
                "cannot resolve app.include_router router argument",
            )
            continue
        prefix = _route_prefix(
            include.prefix,
            root,
            include.source_path,
            include.line,
            errors,
        )
        if prefix is None or include.include_in_schema is None:
            if include.include_in_schema is None:
                _error(
                    errors,
                    f"repository.routes.{include.source_path}:{include.line}",
                    "app.include_router include_in_schema must be a statically resolved boolean",
                )
            continue
        _collect_router_routes(
            root,
            child_ref,
            prefix,
            not include.include_in_schema,
            records,
            errors,
            cache,
        )
    return _RouteInventory(records)


def _route_decorator(route: str, source: str | _RouteInventory) -> str | None:
    if isinstance(source, _RouteInventory):
        record = source.routes.get(route)
        return None if record is None else record.decorator_body
    match = ROUTE_RE.fullmatch(route)
    if match is None:
        return None
    method, path = match.groups()
    pattern = re.compile(
        rf"@app\.{method.lower()}\((?P<body>.*?)\)\s*(?:async\s+)?def\s+",
        re.DOTALL,
    )
    literal = re.compile(rf"['\"]{re.escape(path)}['\"]")
    for decorator in pattern.finditer(source):
        body = decorator.group("body")
        if literal.search(body):
            return body
    return None


def _route_exists(route: str, source: str | _RouteInventory) -> bool:
    return _route_decorator(route, source) is not None


def _route_is_hidden_from_openapi(route: str, source: str | _RouteInventory) -> bool:
    decorator = _route_decorator(route, source)
    if decorator is None:
        return False
    if isinstance(source, _RouteInventory) and source.routes[route].hidden:
        return True
    if re.search(r"\binclude_in_schema\s*=\s*False\b", decorator):
        return True
    return bool(
        re.search(
            r"\binclude_in_schema\s*=\s*settings\."
            r"(?:account_lifecycle_enabled|recruiting_enabled)\b",
            decorator,
        )
    )


def _ui_route_exists(route: str, root: Path) -> bool:
    app_roots = _populated_next_app_roots(root)
    if len(app_roots) != 1:
        return False
    parts = [part for part in route.strip("/").split("/") if part]
    if any(part in {".", ".."} or "\\" in part for part in parts):
        return False
    parts = [
        f"[{match.group(1)}]"
        if (match := CONTRACT_DYNAMIC_SEGMENT_RE.fullmatch(part))
        else part
        for part in parts
    ]
    directory = app_roots[0].joinpath(*parts)
    return any((directory / name).is_file() for name in NEXT_PAGE_NAMES)


def _populated_next_app_roots(root: Path) -> list[Path]:
    candidates = [root / "apps/web/app", root / "apps/web/src/app"]
    return [
        candidate
        for candidate in candidates
        if candidate.is_dir()
        and any(path.name in NEXT_PAGE_NAMES for path in candidate.rglob("*"))
    ]


def _implemented_ui_routes(root: Path, errors: list[str]) -> set[str]:
    app_roots = _populated_next_app_roots(root)
    if not app_roots:
        _error(
            errors,
            "repository.ui_routes",
            "cannot find Next.js pages under apps/web/app or apps/web/src/app",
        )
        return set()
    if len(app_roots) > 1:
        _error(
            errors,
            "repository.ui_routes",
            "both apps/web/app and apps/web/src/app contain pages; select one App Router root",
        )
        return set()
    app_root = app_roots[0]
    routes: set[str] = set()
    pages = sorted(path for path in app_root.rglob("*") if path.name in NEXT_PAGE_NAMES)
    for page in pages:
        parts: list[str] = []
        supported = True
        for segment in page.parent.relative_to(app_root).parts:
            if segment.startswith("(") and segment.endswith(")"):
                continue
            dynamic = NEXT_DYNAMIC_SEGMENT_RE.fullmatch(segment)
            if dynamic:
                parts.append(f"{{{dynamic.group(1)}}}")
            elif segment.startswith(("[", "(", "@")):
                _error(
                    errors,
                    "repository.ui_routes",
                    f"uses unsupported Next.js route segment {segment!r} in {page.relative_to(root).as_posix()}",
                )
                supported = False
                break
            else:
                parts.append(segment)
        if not supported:
            continue
        route = "/" + "/".join(parts) if parts else "/"
        if route in routes:
            _error(
                errors,
                "repository.ui_routes",
                f"duplicates implemented UI route {route!r}",
            )
        routes.add(route)
    return routes


__all__ = tuple(
    name
    for name in globals()
    if name
    in {
        "ROUTE_RE",
        "NEXT_DYNAMIC_SEGMENT_RE",
        "CONTRACT_DYNAMIC_SEGMENT_RE",
        "NEXT_PAGE_NAMES",
        "_ROUTE_METHODS",
        "_ROUTE_DECORATOR_METHODS",
        "_repo_path",
        "_direct_route_records",
        "_collect_router_routes",
        "_implemented_route_inventory",
        "_implemented_ui_routes",
        "_populated_next_app_roots",
        "_ui_route_exists",
    }
    or name.startswith(("_Route", "_Router", "_route"))
)
