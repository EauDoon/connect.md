from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "tools" / "platform_route_test_ownership.py"
CHECKER_PATH = REPO_ROOT / "tools" / "check_platform_features.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ownership = _load_module(MODULE_PATH, "platform_route_test_ownership_test_module")
checker = _load_module(CHECKER_PATH, "platform_route_test_ownership_checker_module")


API_SOURCE = dedent(
    """
    async def test_fixture(client):
        response = await client.get(f"/v1/items/{item_id}")
        assert response.status_code == 200
        assert response.json()["id"] == item_id
    """
).lstrip()

UI_IMPORTS = (
    'import { readFileSync } from "node:fs";\n'
    'import { describe, expect, it } from "vitest";\n'
)


def _ui_fixture_source(source: str) -> str:
    if 'from "vitest"' in source or "from 'vitest'" in source:
        return source
    return UI_IMPORTS + source


def _manual_api_witness(
    *,
    request_anchor: str = 'client.get("/v1/items/1")',
    request_path: str = "/v1/items/1",
    test_name: str = "test_fixture",
    binding: str = "response",
    assertions: list[str] | None = None,
) -> dict[str, object]:
    return {
        "test_path": "apps/api/tests/test_fixture.py",
        "test": test_name,
        "method": "GET",
        "request_anchor": request_anchor,
        "request_path": request_path,
        "response_binding": binding,
        "assertion_anchors": assertions
        or [
            f"assert {binding}.status_code == 200",
            f'assert {binding}.json()["id"] == "1"',
        ],
    }


def _api_record(
    source: str,
    route: str = "GET /v1/items/{item_id}",
    *,
    test_name: str = "test_fixture",
    binding: str = "response",
    assertion_anchors: list[str] | None = None,
    request_path: str | None = None,
) -> tuple[dict[str, object], dict[str, str]]:
    requests = ownership._api_test_analysis(source, test_name)
    matches = [request for request in requests if request.binding == binding]
    if len(matches) != 1:
        raise AssertionError(f"expected one {binding} request, got {len(matches)}")
    request = matches[0]
    descriptors = [ownership._path_descriptor(path) for path in request.paths]
    witness = {
        "test_path": "apps/api/tests/test_fixture.py",
        "test": test_name,
        "method": route.split(" ", 1)[0],
        "request_anchor": request.anchor,
        "request_path": request_path or descriptors[0],
        "response_binding": binding,
        "assertion_anchors": assertion_anchors
        or [text for _, text in request.assertions[:2]],
    }
    return witness, {route: "fixture-feature"}


def _validate_api(
    source: str,
    witness: dict[str, object] | None = None,
    routes: dict[str, str] | None = None,
    *,
    test_path: str = "apps/api/tests/test_fixture.py",
) -> list[str]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source_path = root / Path(*test_path.split("/"))
        source_path.parent.mkdir(parents=True)
        source_path.write_text(source, encoding="utf-8")
        route_map = routes or {"GET /v1/items/{item_id}": "fixture-feature"}
        if witness is None:
            witness, _ = _api_record(source, next(iter(route_map)))
        witness = {**witness, "test_path": test_path}
        registry = {
            "schema_version": 1,
            "registry_id": "connect-md-platform-route-test-ownership",
            "api_routes": {
                route: {"owner": owner, "witness": witness}
                for route, owner in route_map.items()
            },
            "ui_routes": {},
        }
        registry_path = root / "registry.json"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        return ownership.validate_source_witnesses(
            root,
            route_map,
            {},
            [{"id": "fixture-feature", "tests": [test_path]}],
            registry_path,
        )


def _ui_record(
    source: str,
    *,
    route: str = "/target",
    case_name: str = "reads the target page",
    binding: str = "pageSource",
    assertions: list[str] | None = None,
    read_path: str = "../app/target/page.tsx",
) -> dict[str, object]:
    cases = ownership._ui_cases(_ui_fixture_source(source))
    matches = [case for case in cases if case.name == case_name]
    if len(matches) != 1:
        raise AssertionError(f"expected one UI case, got {len(matches)}")
    case = matches[0]
    reads = [read for read in case.source_reads if read.binding == binding]
    if len(reads) != 1:
        raise AssertionError(f"expected one source read, got {len(reads)}")
    selected_assertions = [
        assertion.anchor
        for assertion in case.assertions
        if assertion.binding == binding
    ]
    return {
        "test_path": "apps/web/tests/ui-fixture.test.ts",
        "test": case_name,
        "evidence_mode": "page_source",
        "case_witness": reads[0].anchor,
        "page_module": ownership._ui_page_paths(route)[0],
        "source_binding": binding,
        "assertion_anchors": assertions or selected_assertions[:2],
    }


def _validate_ui(
    source: str,
    witness: dict[str, object] | None = None,
    *,
    route: str = "/target",
) -> list[str]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        test_path = root / "apps" / "web" / "tests" / "ui-fixture.test.ts"
        test_path.parent.mkdir(parents=True)
        test_path.write_text(_ui_fixture_source(source), encoding="utf-8")
        witness = witness or _ui_record(source, route=route)
        registry = {
            "schema_version": 1,
            "registry_id": "connect-md-platform-route-test-ownership",
            "api_routes": {},
            "ui_routes": {route: {"owner": "fixture-feature", "witness": witness}},
        }
        registry_path = root / "registry.json"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        return ownership.validate_source_witnesses(
            root,
            {},
            {route: "fixture-feature"},
            [{"id": "fixture-feature", "tests": ["apps/web/tests/ui-fixture.test.ts"]}],
            registry_path,
        )


class PlatformRouteTestOwnershipTests(unittest.TestCase):
    def test_current_registry_is_complete_and_cached(self) -> None:
        route_module = _load_module(
            REPO_ROOT / "tools" / "platform_route_ownership.py",
            "platform_route_ownership_current_test_module",
        )
        route_ownership, ui_ownership, load_errors, _ = (
            route_module.load_route_ownership(
                REPO_ROOT / "packages/platform-contract/platform-route-ownership.json",
                REPO_ROOT
                / "packages/platform-contract/platform-ui-route-ownership.json",
            )
        )
        features = json.loads(
            (REPO_ROOT / "packages/platform-contract/platform-features.json").read_text(
                encoding="utf-8"
            )
        )
        ownership._api_test_analysis.cache_clear()
        ownership._ui_cases.cache_clear()
        started = time.perf_counter()
        self.assertEqual(
            [
                *load_errors,
                *ownership.validate_source_witnesses(
                    REPO_ROOT, route_ownership, ui_ownership, features
                ),
            ],
            [],
        )
        first = time.perf_counter() - started
        started = time.perf_counter()
        self.assertEqual(
            ownership.validate_source_witnesses(
                REPO_ROOT, route_ownership, ui_ownership, features
            ),
            [],
        )
        second = time.perf_counter() - started
        self.assertLess(first + second, 12.0)
        self.assertGreater(ownership._api_test_analysis.cache_info().hits, 0)
        self.assertGreater(ownership._ui_cases.cache_info().hits, 0)

    def test_try_finally_is_supported_but_exception_handler_is_not(self) -> None:
        valid = dedent(
            """
            async def test_fixture(client):
                try:
                    response = await client.get("/healthz")
                finally:
                    await client.aclose()
                assert response.status_code == 200
                assert response.headers["x-ready"] == "yes"
            """
        ).lstrip()
        witness, routes = _api_record(valid, route="GET /healthz")
        witness["request_path"] = "/healthz"
        self.assertEqual(_validate_api(valid, witness, routes), [])
        invalid = valid.replace(
            "    finally:\n        await client.aclose()",
            "    except Exception:\n        return",
        )
        self.assertTrue(_validate_api(invalid, witness, routes))

    def test_comment_only_marker_and_embedded_python_comments_fail_closed(self) -> None:
        comment_only = """\
        async def test_fixture(client):
            # response = await client.get("/v1/items/1")
            # assert response.status_code == 200
        pass
        """
        witness, _ = _api_record(API_SOURCE)
        self.assertTrue(_validate_api(comment_only, witness))
        embedded_call = dedent(
            """
            async def test_fixture(client):
                response = await client.get(
                    "/v1/items/1"  # embedded request comment
                )
                assert response.status_code == 200
                assert response.json()["id"] == "1"
            """
        ).lstrip()
        witness, _ = _api_record(embedded_call)
        self.assertTrue(_validate_api(embedded_call, witness))
        embedded_assert = dedent(
            """
            async def test_fixture(client):
                response = await client.get("/v1/items/1")
                assert (
                    response.status_code == 200  # embedded assertion comment
                )
                assert response.json()["id"] == "1"
            """
        ).lstrip()
        witness, _ = _api_record(embedded_assert)
        self.assertTrue(_validate_api(embedded_assert, witness))

    def test_marker_wrong_method_and_markdown_near_collision_fail(self) -> None:
        source = API_SOURCE.replace("/v1/items/{item_id}", "/v1/items/{item_id}.md")
        witness, _ = _api_record(source, request_path="/v1/items/{param}.md")
        self.assertTrue(_validate_api(source, witness))
        wrong_method = {**witness, "method": "POST"}
        self.assertTrue(_validate_api(API_SOURCE, wrong_method))
        fake_client = API_SOURCE.replace("client.get", "fake_client.get")
        self.assertTrue(_validate_api(fake_client, witness))

    def test_unknown_fstring_expressions_and_encoded_paths_fail_closed(self) -> None:
        for expression in ("make_id()", "obj.id", "items[0]"):
            source = dedent(
                f"""
                async def test_fixture(client):
                    response = await client.get(f"/v1/items/{{{expression}}}")
                    assert response.status_code == 200
                    assert response.json()["id"] == "1"
                """
            ).lstrip()
            witness = _manual_api_witness(
                request_anchor=f'client.get(f"/v1/items/{{{expression}}}")',
                request_path="/v1/items/{param}",
            )
            self.assertTrue(_validate_api(source, witness))

        for expression in ("make_id()", "obj.id", "items[0]"):
            source = dedent(
                f"""
                async def test_fixture(client):
                    item_id = {expression}
                    response = await client.get(f"/v1/items/{{item_id}}")
                    assert response.status_code == 200
                    assert response.json()["id"] == "1"
                """
            ).lstrip()
            witness = _manual_api_witness(
                request_anchor='client.get(f"/v1/items/{item_id}")',
                request_path="/v1/items/{param}",
            )
            self.assertTrue(_validate_api(source, witness))

        response_identifier = dedent(
            """
            async def test_fixture(client):
                created = await client.post("/v1/items")
                payload = created.json()
                item_id = payload["id"]
                response = await client.get(f"/v1/items/{item_id}")
                assert response.status_code == 200
                assert response.json()["id"] == item_id
            """
        ).lstrip()
        witness = _manual_api_witness(
            request_anchor='client.get(f"/v1/items/{item_id}")',
            request_path="/v1/items/{param}",
            assertions=[
                "assert response.status_code == 200",
                'assert response.json()["id"] == item_id',
            ],
        )
        self.assertEqual(_validate_api(response_identifier, witness), [])

        encoded_source = dedent(
            """
            async def test_fixture(client):
                response = await client.get("/v1/items/1%2Emd")
                assert response.status_code == 200
                assert response.json()["id"] == "1"
            """
        ).lstrip()
        for request_path in ("/v1/items/1%2Emd", "/v1/items/1%2Fchild"):
            source = encoded_source.replace("1%2Emd", request_path.rsplit("/", 1)[-1])
            witness = _manual_api_witness(
                request_anchor=f'client.get("{request_path}")',
                request_path=request_path,
            )
            self.assertTrue(_validate_api(source, witness))

    def test_api_terminators_and_skip_decorators_fail_but_asyncio_is_valid(
        self,
    ) -> None:
        terminators = (
            'pytest.skip("not this witness")',
            'pytest.xfail("not this witness")',
        )
        for terminator in terminators:
            source = dedent(
                f"""
                async def test_fixture(client):
                    {terminator}
                    response = await client.get("/v1/items/1")
                    assert response.status_code == 200
                    assert response.json()["id"] == "1"
                """
            ).lstrip()
            self.assertTrue(_validate_api(source, _manual_api_witness()))

        decorators = (
            "@pytest.mark.skip",
            '@pytest.mark.skipif(True, reason="not this witness")',
            "@pytest.mark.xfail",
            "@live_integration",
        )
        for decorator in decorators:
            source = dedent(
                f"""
                {decorator}
                async def test_fixture(client):
                    response = await client.get("/v1/items/1")
                    assert response.status_code == 200
                    assert response.json()["id"] == "1"
                """
            ).lstrip()
            self.assertTrue(_validate_api(source, _manual_api_witness()))

        valid = dedent(
            """
            @pytest.mark.asyncio
            async def test_fixture(client):
                response = await client.get("/v1/items/1")
                assert response.status_code == 200
                assert response.json()["id"] == "1"
            """
        ).lstrip()
        self.assertEqual(_validate_api(valid), [])

    def test_api_reachability_and_provenance_mutations_fail_closed(self) -> None:
        def source_with_body(body: str) -> str:
            indented = "\n".join(
                f"    {line}" for line in dedent(body).strip().splitlines()
            )
            return (
                "async def test_fixture(client, condition=False, monkeypatch=None):\n"
                f"{indented}\n"
                '    response = await client.get("/v1/items/1")\n'
                "    assert response.status_code == 200\n"
                '    assert response.json()["id"] == "1"\n'
            )

        invalid_bodies = (
            "return",
            'raise RuntimeError("stop")',
            'for item in ("one",):\n    break\n    response = await client.get("/v1/items/1")',
            'for item in ("one",):\n    continue\n    response = await client.get("/v1/items/1")',
            "if condition:\n    return",
            "try:\n    pass\nexcept Exception:\n    client = fake_client",
            "client = fake_client",
            "client.get = fake_get",
            'setattr(client, "get", fake_get)',
            'monkeypatch.setattr(client, "get", fake_get)',
            "response.status_code = 500",
            'response["status_code"] = 500',
            'setattr(response, "status_code", 500)',
            'monkeypatch.setattr(response, "status_code", 500)',
        )
        for body in invalid_bodies:
            if body.startswith(("response", "setattr", "monkeypatch.setattr")):
                source = dedent(
                    f"""
                    async def test_fixture(client, condition=False, monkeypatch=None):
                        response = await client.get("/v1/items/1")
                        {body}
                        assert response.status_code == 200
                        assert response.json()["id"] == "1"
                    """
                ).lstrip()
            else:
                source = source_with_body(body)
            self.assertTrue(_validate_api(source, _manual_api_witness()))

        setup_source = dedent(
            """
            async def test_fixture(client):
                prepare_fixture()
                response = await client.get("/v1/items/1")
                assert response.status_code == 200
                assert response.json()["id"] == "1"
            """
        ).lstrip()
        self.assertEqual(_validate_api(setup_source), [])

        helper_between = dedent(
            """
            async def test_fixture(client):
                response = await client.get("/v1/items/1")
                refresh_fixture(response)
                assert response.status_code == 200
                assert response.json()["id"] == "1"
            """
        ).lstrip()
        self.assertTrue(_validate_api(helper_between, _manual_api_witness()))

        unrelated_helper_between = helper_between.replace(
            "refresh_fixture(response)", "refresh_fixture()"
        )
        self.assertEqual(_validate_api(unrelated_helper_between), [])

        unrelated_try = source_with_body(
            "try:\n    prepare_fixture()\nexcept Exception:\n    pass"
        )
        self.assertEqual(_validate_api(unrelated_try), [])

    def test_api_module_markers_alias_mutations_and_tautologies_fail_closed(
        self,
    ) -> None:
        body = dedent(
            """
            async def test_fixture(client):
                response = await client.get("/v1/items/1")
                assert response.status_code == 200
                assert response.json()["id"] == "1"
            """
        ).lstrip()
        for marker in (
            "pytestmark = pytest.mark.skip\n",
            "pytestmark = [pytest.mark.asyncio, pytest.mark.xfail]\n",
            "SKIP = pytest.mark.skip\npytestmark = SKIP\n",
            '@pytest.mark.parametrize("value", [pytest.param("1", marks=pytest.mark.skip)])\n',
            "SKIP = pytest.mark.skip\n@SKIP\n",
            '@getattr(pytest.mark, "skip")\n',
        ):
            self.assertTrue(_validate_api(marker + body, _manual_api_witness()))

        client_mutations = (
            "alias = client\n    alias.get = fake_get",
            'alias = client\n    setattr(alias, "get", fake_get)',
            'object.__setattr__(client, "get", fake_get)',
            "client.__class__.get = fake_get",
            'client.__dict__["get"] = fake_get',
            'vars(client)["get"] = fake_get',
            'namespace = vars(client)\n    namespace["get"] = fake_get',
            'namespace = getattr(client, "__dict__")\n    namespace["get"] = fake_get',
            "holders = [client]\n    holders[0].get = fake_get",
            "holder = identity(client)\n    holder.get = fake_get",
            "holder = (lambda value: value)(client)\n    holder.get = fake_get",
            "holder = (alias := client)\n    holder.get = fake_get",
            "holder = client if condition else fake_client\n    holder.get = fake_get",
            "holder = (lambda: client)()\n    holder.get = fake_get",
            'holder = {"x": client}.get("x")\n    holder.get = fake_get',
            "holder = Box()\n    holder.client = client\n    holder.client.get = fake_get",
            "del client.get",
            "prepare_fixture(client)",
        )
        for mutation in client_mutations:
            source = body.replace(
                '    response = await client.get("/v1/items/1")',
                f'    {mutation}\n    response = await client.get("/v1/items/1")',
            )
            self.assertTrue(_validate_api(source, _manual_api_witness()))

        response_mutations = (
            "alias = response\n    alias.status_code = 200",
            'alias = response\n    setattr(alias, "status_code", 200)',
            'object.__setattr__(response, "status_code", 200)',
            "response.__class__.status_code = 200",
            'response.__dict__["status_code"] = 200',
            'vars(response)["status_code"] = 200',
            'holders = {"response": response}\n    holders["response"].status_code = 200',
            "holder = identity(response)\n    holder.status_code = 200",
            "holder = Box()\n    holder.response = response\n    holder.response.status_code = 200",
            "del response.status_code",
            "alias = response\n    inspect_response(alias)",
        )
        for mutation in response_mutations:
            source = body.replace(
                "    assert response.status_code == 200",
                f"    {mutation}\n    assert response.status_code == 200",
            )
            self.assertTrue(_validate_api(source, _manual_api_witness()))

        tautology = body.replace(
            "assert response.status_code == 200",
            "assert response.status_code == 200 or True",
        )
        witness = _manual_api_witness(
            assertions=[
                "assert response.status_code == 200 or True",
                'assert response.json()["id"] == "1"',
            ]
        )
        self.assertTrue(_validate_api(tautology, witness))

        nested_client = dedent(
            """
            async def test_fixture(client):
                async def poison():
                    client.get = fake_get
                await poison()
                response = await client.get("/v1/items/1")
                assert response.status_code == 200
                assert response.json()["id"] == "1"
            """
        ).lstrip()
        self.assertTrue(_validate_api(nested_client, _manual_api_witness()))
        nested_response = dedent(
            """
            async def test_fixture(client):
                response = await client.get("/v1/items/1")
                async def poison():
                    response.status_code = 200
                await poison()
                assert response.status_code == 200
                assert response.json()["id"] == "1"
            """
        ).lstrip()
        self.assertTrue(_validate_api(nested_response, _manual_api_witness()))

        for binding in ("client", "response"):
            lambda_capture = body.replace(
                '    response = await client.get("/v1/items/1")',
                '    response = await client.get("/v1/items/1")\n'
                f'    poison = lambda: setattr({binding}, "status_code", 200)\n'
                "    poison()",
            )
            self.assertTrue(_validate_api(lambda_capture, _manual_api_witness()))

        self_referential = (
            "assert response.status_code in (200, response.status_code)",
            "assert response.status_code == (200 if condition else response.status_code)",
            "assert response.status_code == response.status_code + 0",
            'assert response.json()["id"] == response.json()["id"] + ""',
        )
        for assertion in self_referential:
            source = body.replace("assert response.status_code == 200", assertion)
            witness = _manual_api_witness(
                assertions=[assertion, 'assert response.json()["id"] == "1"']
            )
            self.assertTrue(_validate_api(source, witness))

    def test_dynamic_method_and_path_reassignment_fail_closed(self) -> None:
        for branch in (
            "if condition:\n    method = 'POST'",
            "if condition:\n    path = '/v1/other'",
            "try:\n    pass\nfinally:\n    path = '/v1/other'",
        ):
            source = dedent(
                f"""
                async def test_fixture(client, condition=False):
                    method = "GET"
                    path = "/v1/items/1"
                    {branch.replace(chr(10), chr(10) + "    ")}
                    response = await client.request(method, path)
                    assert response.status_code == 200
                    assert response.json()["id"] == "1"
                """
            ).lstrip()
            witness = _manual_api_witness(
                request_anchor="client.request(method, path)",
            )
            self.assertTrue(_validate_api(source, witness))

    def test_unrelated_assertion_and_unresolved_reassignment_fail(self) -> None:
        unrelated = dedent(
            """
            async def test_fixture(client):
                response = await client.get("/v1/items/1")
                other = await client.get("/v1/other")
                assert other.status_code == 200
                assert other.json()["id"] == "other"
            """
        ).lstrip()
        witness, _ = _api_record(API_SOURCE)
        self.assertTrue(_validate_api(unrelated, witness))
        reassigned = API_SOURCE.replace(
            "assert response.status_code == 200",
            "response = make_response()\n    assert response.status_code == 200",
        )
        witness, _ = _api_record(API_SOURCE)
        self.assertTrue(_validate_api(reassigned, witness))

    def test_wrong_layer_and_reused_physical_anchor_fail(self) -> None:
        witness, _ = _api_record(API_SOURCE)
        self.assertTrue(
            _validate_api(
                API_SOURCE,
                witness,
                test_path="apps/web/tests/not-an-api-test.ts",
            )
        )
        self.assertTrue(
            _validate_api(
                API_SOURCE,
                witness,
                routes={
                    "GET /v1/items/{item_id}": "fixture-feature",
                    "GET /v1/items/{other_id}": "fixture-feature",
                },
            )
        )

    def test_identical_assertion_text_at_distinct_cases_is_valid(self) -> None:
        source = dedent(
            """
            async def test_first(client):
                response = await client.get("/v1/first")
                assert response.status_code == 200
                assert response.json()["ok"] is True

            async def test_second(client):
                response = await client.get("/v1/second")
                assert response.status_code == 200
                assert response.json()["ok"] is True
            """
        ).lstrip()
        first, _ = _api_record(
            source,
            route="GET /v1/first",
            test_name="test_first",
            request_path="/v1/first",
        )
        second, _ = _api_record(
            source,
            route="GET /v1/second",
            test_name="test_second",
            request_path="/v1/second",
        )
        self.assertEqual(
            _validate_api(
                source,
                first,
                routes={"GET /v1/first": "fixture-feature"},
            ),
            [],
        )
        self.assertEqual(
            _validate_api(
                source,
                second,
                routes={"GET /v1/second": "fixture-feature"},
            ),
            [],
        )

    def test_ui_direct_nested_case_and_distinct_source_bindings_are_valid(self) -> None:
        source = dedent(
            """
            describe("suite", () => {
                it("reads the target page", () => {
                    const pageSource = readFileSync(new URL("../app/target/page.tsx", import.meta.url), "utf8");
                    const otherSource = readFileSync(new URL("../app/other/page.tsx", import.meta.url), "utf8");
                    expect(pageSource).toContain("TARGET");
                    expect(pageSource).toMatch(/PAGE/);
                    expect(otherSource).toContain("OTHER");
                });
            });
            """
        ).lstrip()
        self.assertEqual(_validate_ui(source), [])

    def test_ui_link_only_wrong_chain_and_dynamic_matcher_fail(self) -> None:
        valid_source = dedent(
            """
            it("reads the target page", () => {
                const pageSource = readFileSync(new URL("../app/target/page.tsx", import.meta.url), "utf8");
                expect(pageSource).toContain("TARGET");
                expect(pageSource).toContain("BOUNDARY");
            });
            """
        ).lstrip()
        valid_witness = _ui_record(valid_source)
        link_only = dedent(
            """
            it("reads the target page", () => {
                expect("/target").toContain("target");
                expect("/target").toContain("target");
            });
            """
        ).lstrip()
        self.assertTrue(_validate_ui(link_only, valid_witness))
        wrong_chain = dedent(
            """
            it("reads the target page", () => {
                const pageSource = readFileSync(new URL("../app/other/page.tsx", import.meta.url), "utf8");
                expect(pageSource).toContain("TARGET");
                expect(pageSource).toContain("BOUNDARY");
            });
            """
        ).lstrip()
        self.assertTrue(_validate_ui(wrong_chain, valid_witness))
        dynamic_matcher = dedent(
            """
            it("reads the target page", () => {
                const pageSource = readFileSync(new URL("../app/target/page.tsx", import.meta.url), "utf8");
                const marker = getMarker();
                expect(pageSource).toContain(marker);
                expect(pageSource).toContain("TARGET");
            });
            """
        ).lstrip()
        self.assertTrue(_validate_ui(dynamic_matcher, valid_witness))

    def test_ui_dotted_wrapped_and_reassigned_forms_fail(self) -> None:
        valid_source = dedent(
            """
            it("reads the target page", () => {
                const pageSource = readFileSync(new URL("../app/target/page.tsx", import.meta.url), "utf8");
                expect(pageSource).toContain("TARGET");
                expect(pageSource).toContain("BOUNDARY");
            });
            """
        ).lstrip()
        valid_witness = _ui_record(valid_source)
        for source in (
            'it.skip("reads the target page", () => { const pageSource = readFileSync(new URL("../app/target/page.tsx", import.meta.url), "utf8"); expect(pageSource).toContain("TARGET"); expect(pageSource).toContain("BOUNDARY"); });',
            'foo.it("reads the target page", () => { const pageSource = readFileSync(new URL("../app/target/page.tsx", import.meta.url), "utf8"); expect(pageSource).toContain("TARGET"); expect(pageSource).toContain("BOUNDARY"); });',
            'it.each([["reads the target page"]])("reads the target page", () => { const pageSource = readFileSync(new URL("../app/target/page.tsx", import.meta.url), "utf8"); expect(pageSource).toContain("TARGET"); expect(pageSource).toContain("BOUNDARY"); });',
            '(it)("reads the target page", () => { const pageSource = readFileSync(new URL("../app/target/page.tsx", import.meta.url), "utf8"); expect(pageSource).toContain("TARGET"); expect(pageSource).toContain("BOUNDARY"); });',
        ):
            self.assertTrue(_validate_ui(source, valid_witness))
        reassigned = dedent(
            """
            it("reads the target page", () => {
                const pageSource = readFileSync(new URL("../app/target/page.tsx", import.meta.url), "utf8");
                pageSource = getOtherSource();
                expect(pageSource).toContain("TARGET");
                expect(pageSource).toContain("BOUNDARY");
            });
            """
        ).lstrip()
        self.assertTrue(_validate_ui(reassigned, valid_witness))

    def test_ui_comment_spans_fail_closed(self) -> None:
        valid_source = dedent(
            """
            it("reads the target page", () => {
                const pageSource = readFileSync(new URL("../app/target/page.tsx", import.meta.url), "utf8");
                expect(pageSource).toContain("TARGET");
                expect(pageSource).toContain("BOUNDARY");
            });
            """
        ).lstrip()
        valid_witness = _ui_record(valid_source)
        embedded_declaration = dedent(
            """
            it("reads the target page", () => {
                const pageSource = readFileSync(new URL("../app/target/page.tsx", /* embedded */ import.meta.url), "utf8");
                expect(pageSource).toContain("TARGET");
                expect(pageSource).toContain("BOUNDARY");
            });
            """
        ).lstrip()
        self.assertTrue(_validate_ui(embedded_declaration, valid_witness))
        embedded_expect = dedent(
            """
            it("reads the target page", () => {
                const pageSource = readFileSync(new URL("../app/target/page.tsx", import.meta.url), "utf8");
                expect(pageSource).toContain(/* embedded */ "TARGET");
                expect(pageSource).toContain("BOUNDARY");
            });
            """
        ).lstrip()
        self.assertTrue(_validate_ui(embedded_expect, valid_witness))

    def test_ui_reachability_shadowing_and_unsupported_nesting_fail_closed(
        self,
    ) -> None:
        valid_source = dedent(
            """
            it("reads the target page", () => {
                const pageSource = readFileSync(new URL("../app/target/page.tsx", import.meta.url), "utf8");
                expect(pageSource).toContain("TARGET");
                expect(pageSource).toContain("BOUNDARY");
            });
            """
        ).lstrip()
        valid_witness = _ui_record(valid_source)

        for prefix in ("return;", 'throw new Error("stop");'):
            source = dedent(
                f"""
                it("reads the target page", () => {{
                    {prefix}
                    const pageSource = readFileSync(new URL("../app/target/page.tsx", import.meta.url), "utf8");
                    expect(pageSource).toContain("TARGET");
                    expect(pageSource).toContain("BOUNDARY");
                }});
                """
            ).lstrip()
            self.assertTrue(_validate_ui(source, valid_witness))

        controls = (
            "if (false) { const ignored = true; }",
            "for (;;) { break; }",
            "while (false) { break; }",
            'switch ("route") { case "route": break; }',
            "try { const ignored = true; } finally {}",
            'const selected = true ? "yes" : "no";',
        )
        for control in controls:
            source = dedent(
                f"""
                it("reads the target page", () => {{
                    {control}
                    const pageSource = readFileSync(new URL("../app/target/page.tsx", import.meta.url), "utf8");
                    expect(pageSource).toContain("TARGET");
                    expect(pageSource).toContain("BOUNDARY");
                }});
                """
            ).lstrip()
            self.assertTrue(_validate_ui(source, valid_witness))

        nested_or_skipped = (
            'describe.skip("suite", () => { it("reads the target page", () => { const pageSource = readFileSync(new URL("../app/target/page.tsx", import.meta.url), "utf8"); expect(pageSource).toContain("TARGET"); expect(pageSource).toContain("BOUNDARY"); }); });',
            'if (false) { it("reads the target page", () => { const pageSource = readFileSync(new URL("../app/target/page.tsx", import.meta.url), "utf8"); expect(pageSource).toContain("TARGET"); expect(pageSource).toContain("BOUNDARY"); }); }',
        )
        for source in nested_or_skipped:
            self.assertTrue(_validate_ui(source, valid_witness))

        helper_between = valid_source.replace(
            'expect(pageSource).toContain("TARGET");',
            'refreshFixture();\n                expect(pageSource).toContain("TARGET");',
        )
        self.assertTrue(_validate_ui(helper_between, valid_witness))

        for name, value in (
            ("it", "fakeIt"),
            ("test", "fakeTest"),
            ("expect", "fakeExpect"),
            ("readFileSync", "fakeReadFileSync"),
            ("URL", "fakeURL"),
            ("describe", "fakeDescribe"),
        ):
            source = f"const {name} = {value};\n{valid_source}"
            self.assertTrue(_validate_ui(source, valid_witness))

        response_source = valid_source.replace(
            'expect(pageSource).toContain("TARGET");',
            'pageSource = getOtherSource();\n                expect(pageSource).toContain("TARGET");',
        )
        self.assertTrue(_validate_ui(response_source, valid_witness))

    def test_ui_function_shadows_and_mocking_fail_closed(self) -> None:
        valid_source = dedent(
            """
            it("reads the target page", () => {
                const pageSource = readFileSync(new URL("../app/target/page.tsx", import.meta.url), "utf8");
                expect(pageSource).toContain("TARGET");
                expect(pageSource).toContain("BOUNDARY");
            });
            """
        ).lstrip()
        valid_witness = _ui_record(valid_source)
        for prefix in (
            "function readFileSync() { return 'fabricated'; }\n",
            "function expect() { return { toContain() {} }; }\n",
            "class URL {}\n",
            'vi.mock("node:fs");\n',
            'vi["mock"]("node:fs");\n',
            'vi/*comment*/.mock("node:fs");\n',
            'vi.mock.bind(vi)("node:fs");\n',
            'const mockFs = vi.mock; mockFs("node:fs");\n',
            'const runtime = globalThis["vi"]; runtime["mock"]("node:fs");\n',
            'Reflect.get(globalThis, "vi").mock("node:fs");\n',
            'vi.mock("vitest");\n',
        ):
            self.assertTrue(_validate_ui(prefix + valid_source, valid_witness))

        for statement in (
            "function expect() { return { toContain() {} }; }",
            "function readFileSync() { return 'fabricated'; }",
            "class URL {}",
            "vi.mocked(readFileSync);",
            'vi.spyOn(fs, "readFileSync");',
        ):
            source = valid_source.replace(
                "    const pageSource",
                f"    {statement}\n    const pageSource",
            )
            self.assertTrue(_validate_ui(source, valid_witness))

        nested_mock = valid_source.replace(
            "    const pageSource",
            "    function primeMock() { vi.mocked(readFileSync).mockReturnValue('fabricated'); }\n"
            "    primeMock();\n"
            "    const pageSource",
        )
        self.assertTrue(_validate_ui(nested_mock, valid_witness))

        for describe_setup in (
            "function readFileSync() { return 'fabricated'; }",
            "function expect() { return { toContain() {} }; }",
            "vi.mocked(readFileSync);",
            'jest.mock("node:fs");',
        ):
            nested = dedent(
                f"""
                describe("suite", () => {{
                    {describe_setup}
                    it("reads the target page", () => {{
                        const pageSource = readFileSync(new URL("../app/target/page.tsx", import.meta.url), "utf8");
                        expect(pageSource).toContain("TARGET");
                        expect(pageSource).toContain("BOUNDARY");
                    }});
                }});
                """
            ).lstrip()
            self.assertTrue(_validate_ui(nested, valid_witness))

    def test_checker_orchestrates_source_witness_gate(self) -> None:
        source = CHECKER_PATH.read_text(encoding="utf-8")
        self.assertIn("validate_source_witnesses as _rte", source)
        self.assertIn("errors.extend(_rte(", source)
        with patch.object(checker, "_rte", return_value=["route witness sentinel"]):
            errors = checker.check_registry(
                REPO_ROOT / "packages/platform-contract/platform-features.json",
                REPO_ROOT
                / "packages/platform-contract/platform-feature-registry.schema.json",
                REPO_ROOT,
            )
        self.assertIn("route witness sentinel", errors)


if __name__ == "__main__":
    unittest.main()
