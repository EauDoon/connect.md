"""Pure AST and string helpers for platform discovery validation."""

from __future__ import annotations

import ast


def _error(errors: list[str], location: str, message: str) -> None:
    errors.append(f"{location}: {message}")


def _static_string(node: ast.AST, bindings: dict[str, str] | None = None) -> str | None:
    bindings = bindings or {}
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                resolved = _static_string(value.value, bindings)
                parts.append("{}" if resolved is None else resolved)
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string(node.left, bindings)
        right = _static_string(node.right, bindings)
        if left is not None and right is not None:
            return left + right
    return None


def _string_bindings(
    body: list[ast.stmt], initial: dict[str, str] | None = None
) -> dict[str, str]:
    bindings = dict(initial or {})
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
            value = _static_string(value_node, bindings)
            if value is not None and bindings.get(name) != value:
                bindings[name] = value
                changed = True
    return bindings


def _discovery_function_strings(
    tree: ast.AST,
    root_names: tuple[str, ...],
    location: str,
    errors: list[str],
) -> set[str]:
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    module_bindings = _string_bindings(
        tree.body if isinstance(tree, ast.Module) else []
    )
    missing = sorted(set(root_names) - set(functions))
    if missing:
        errors.append(
            f"repository.discovery.{location}: cannot locate discovery functions: {', '.join(missing)}"
        )
    pending = [name for name in root_names if name in functions]
    visited: set[str] = set()
    strings: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        function = functions[name]
        bindings = _string_bindings(function.body, module_bindings)
        for node in ast.walk(function):
            value = _static_string(node, bindings)
            if value is not None:
                strings.add(value.lower())
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in functions
                and node.func.id not in visited
            ):
                pending.append(node.func.id)
    return strings


def _lifecycle_discovery_errors(
    main_source: str,
    discovery_source: str,
    agent_card_source: str | None = None,
) -> list[str]:
    errors: list[str] = []
    try:
        main_tree = ast.parse(main_source)
        discovery_tree = ast.parse(discovery_source)
        agent_card_tree = ast.parse(
            main_source if agent_card_source is None else agent_card_source
        )
    except SyntaxError as exc:
        return [f"repository.discovery: cannot parse API discovery source: {exc}"]
    regions = {
        "llms": _discovery_function_strings(
            discovery_tree, ("llms_txt", "llms_full_txt"), "llms", errors
        ),
        "capabilities": _discovery_function_strings(
            main_tree, ("capabilities",), "capabilities", errors
        ),
        "agent_card": _discovery_function_strings(
            agent_card_tree, ("agent_card",), "agent_card", errors
        ),
        "mcp_tools": _discovery_function_strings(
            main_tree, ("mcp_tools",), "mcp_tools", errors
        ),
    }
    lifecycle_markers = (
        "/v1/account",
        "account_lifecycle",
        "account deletion",
        "account export",
    )
    for name, strings in regions.items():
        for marker in lifecycle_markers:
            if any(marker in value for value in strings):
                errors.append(
                    f"repository.discovery.{name}: feature-gated account lifecycle is advertised by {marker!r}"
                )
    return errors


def _llms_workflow_errors(source: str) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [
            f"repository.llms_workflow: cannot parse apps/api/app/routes/discovery.py: {exc}"
        ]
    strings = _discovery_function_strings(tree, ("llms_txt",), "llms_workflow", errors)
    surface = "\n".join(strings)
    required_markers = {
        "authenticated bearer write": "authorization: bearer $connectmd_token",
        "authenticated curl header": '-h "authorization: bearer $connectmd_token"',
        "raw Markdown request": "content-type: text/markdown",
        "raw Markdown curl header": "-h 'content-type: text/markdown'",
        "public search": "curl --get '{}/v1/search'",
        "canonical Markdown read": "curl -h 'accept: text/markdown'",
        "raw Markdown create": "curl -x post '{}/v1/profiles'",
        "canonical read capture": "curl -ss -d profile.headers -o current-profile.md",
        "conditional update operation": "curl -x put '{}/v1/profiles/$connectmd_handle'",
        "conditional update": "if-match: $etag",
        "idempotent create": "idempotency-key: profile-create-001",
        "idempotent update": "idempotency-key: profile-update-001",
        "raw Markdown payload": "--data-binary '@profile.md'",
        "conditional raw Markdown payload": "--data-binary '@current-profile.md'",
    }
    for label, marker in required_markers.items():
        if marker not in surface:
            _error(
                errors,
                "repository.llms_workflow",
                f"/llms.txt is missing the copy-ready {label} anchor {marker!r}",
            )
    if surface.count("/v1/profiles") < 3:
        _error(
            errors,
            "repository.llms_workflow",
            "/llms.txt must cover profile create, read, and conditional update",
        )
    return errors


def _literal_string_lists(
    source: str, key: str, location: str, errors: list[str]
) -> list[list[str]]:
    """Extract closed literal string lists from one discovery function."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        _error(
            errors, f"repository.discovery.{location}", f"cannot parse source: {exc}"
        )
        return []
    lists: list[list[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key_node, value_node in zip(node.keys, node.values):
            if not (isinstance(key_node, ast.Constant) and key_node.value == key):
                continue
            if not isinstance(value_node, (ast.List, ast.Tuple)):
                _error(
                    errors,
                    f"repository.discovery.{location}",
                    f"{key!r} must remain a literal string list",
                )
                continue
            values: list[str] = []
            for element in value_node.elts:
                value = _static_string(element)
                if value is None:
                    _error(
                        errors,
                        f"repository.discovery.{location}",
                        f"{key!r} contains a non-literal value",
                    )
                    continue
                values.append(value)
            lists.append(values)
    return lists


def _literal_string_values(
    source: str, key: str, location: str, errors: list[str]
) -> list[str]:
    """Extract scalar string values for a key from one discovery function."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        _error(
            errors, f"repository.discovery.{location}", f"cannot parse source: {exc}"
        )
        return []
    values: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key_node, value_node in zip(node.keys, node.values):
            if not (isinstance(key_node, ast.Constant) and key_node.value == key):
                continue
            value = _static_string(value_node)
            if value is None:
                _error(
                    errors,
                    f"repository.discovery.{location}",
                    f"{key!r} must remain a literal string",
                )
                continue
            values.append(value)
    return values


def _a2a_action_branches(source: str, location: str, errors: list[str]) -> set[str]:
    """Return action names handled by exact ``if action == ...`` branches."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        _error(
            errors, f"repository.discovery.{location}", f"cannot parse source: {exc}"
        )
        return set()
    actions: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], ast.Eq) or len(node.comparators) != 1:
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == "action"):
            continue
        value = _static_string(node.comparators[0])
        if value is not None:
            actions.add(value)
    return actions
