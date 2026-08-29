from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = REPO_ROOT / "tools" / "check_platform_features.py"
SCHEMA_PATH = (
    REPO_ROOT
    / "packages"
    / "platform-contract"
    / "platform-feature-registry.schema.json"
)
REGISTRY_PATH = REPO_ROOT / "packages" / "platform-contract" / "platform-features.json"
ROUTE_REGISTRY_PATH = (
    REPO_ROOT / "packages" / "platform-contract" / "platform-route-ownership.json"
)
UI_ROUTE_REGISTRY_PATH = (
    REPO_ROOT / "packages" / "platform-contract" / "platform-ui-route-ownership.json"
)
EVIDENCE_SCHEMA_PATH = (
    REPO_ROOT
    / "packages"
    / "platform-contract"
    / "platform-evidence-receipt.schema.json"
)

spec = importlib.util.spec_from_file_location("check_platform_features", CHECKER_PATH)
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class PlatformFeatureRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        self.route_registry = json.loads(
            ROUTE_REGISTRY_PATH.read_text(encoding="utf-8")
        )
        self.ui_route_registry = json.loads(
            UI_ROUTE_REGISTRY_PATH.read_text(encoding="utf-8")
        )
        self.evidence_schema = json.loads(
            EVIDENCE_SCHEMA_PATH.read_text(encoding="utf-8")
        )

    def check(
        self,
        registry: dict[str, object],
        route_registry: dict[str, object] | None = None,
        ui_route_registry: dict[str, object] | None = None,
        evidence_schema: dict[str, object] | None = None,
    ) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            routes_path = Path(directory) / "routes.json"
            ui_routes_path = Path(directory) / "ui-routes.json"
            evidence_schema_path = Path(directory) / "evidence-schema.json"
            path.write_text(json.dumps(registry), encoding="utf-8")
            routes_path.write_text(
                json.dumps(route_registry or self.route_registry), encoding="utf-8"
            )
            ui_routes_path.write_text(
                json.dumps(ui_route_registry or self.ui_route_registry),
                encoding="utf-8",
            )
            evidence_schema_path.write_text(
                json.dumps(
                    self.evidence_schema if evidence_schema is None else evidence_schema
                ),
                encoding="utf-8",
            )
            return checker.check_registry(
                path,
                SCHEMA_PATH,
                REPO_ROOT,
                routes_path,
                ui_routes_path,
                evidence_schema_path,
            )

    def test_current_registry_passes(self) -> None:
        self.assertEqual(self.check(self.registry), [])

    def test_function_source_cache_tracks_content_and_utf8_offsets(self) -> None:
        path = "fixture.py"
        errors: list[str] = []
        first = 'def outer():\n    def target():\n        return "café"\n'
        second = first.replace("café", "茶")

        self.assertEqual(
            checker._function_source(first, "target", path, errors),
            'def target():\n        return "café"',
        )
        self.assertEqual(
            checker._function_source(second, "target", path, errors),
            'def target():\n        return "茶"',
        )
        self.assertEqual(errors, [])

    def test_private_workspace_navigation_runtime_fails_closed(self) -> None:
        files = (
            "apps/web/app/layout.tsx",
            "apps/web/components/site-header.tsx",
            "apps/web/middleware.ts",
            "apps/web/tests/private-route-gate.test.ts",
            "apps/web/tests/site-header-truthfulness.test.ts",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker.workspace_navigation_errors(root), [])

            layout = root / "apps/web/app/layout.tsx"
            original_layout = layout.read_text(encoding="utf-8")
            layout.write_text(
                original_layout.replace(
                    "<SiteHeader />",
                    "<SiteHeader privateWorkspacesEnabled />",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker.workspace_navigation_errors(root)
            self.assertTrue(any("static root header" in error for error in errors))

            layout.write_text(original_layout, encoding="utf-8")
            header = root / "apps/web/components/site-header.tsx"
            header.write_text(
                header.read_text(encoding="utf-8").replace(
                    "PUBLIC_PRIMARY_NAVIGATION",
                    "PRIMARY_NAVIGATION",
                ),
                encoding="utf-8",
            )
            errors = checker.workspace_navigation_errors(root)
            self.assertTrue(
                any("standalone primary navigation" in error for error in errors)
            )

    def test_public_profile_agent_identity_failure_state_is_release_bound(self) -> None:
        files = (
            "apps/web/app/p/[handle]/page.tsx",
            "apps/web/components/public-document-page.tsx",
            "apps/web/tests/public-detail-ux.test.ts",
            "packages/platform-contract/platform-features.json",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker.public_profile_identity_errors(root), [])

            component = root / "apps/web/components/public-document-page.tsx"
            valid_component = component.read_text(encoding="utf-8")
            component.write_text(
                valid_component.replace(
                    "This profile remains available",
                    "Identity data is unavailable",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker.public_profile_identity_errors(root)
            self.assertTrue(
                any("profile continuity disclosure" in error for error in errors)
            )
            component.write_text(valid_component, encoding="utf-8")

            test_path = root / "apps/web/tests/public-detail-ux.test.ts"
            valid_test = test_path.read_text(encoding="utf-8")
            test_path.write_text(
                valid_test.replace(
                    'expect(unavailableMarkup).toContain("Ari Chen");',
                    'expect(unavailableMarkup).not.toContain("Ari Chen");',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker.public_profile_identity_errors(root)
            self.assertTrue(
                any("profile content continuity assertion" in error for error in errors)
            )

    def test_release_matrix_covers_every_registered_feature(self) -> None:
        self.assertEqual(
            checker._release_matrix_feature_mapping_errors(
                REPO_ROOT, self.registry["features"]
            ),
            [],
        )

    def test_release_matrix_missing_owner_mapping_fails_closed(self) -> None:
        matrix = (REPO_ROOT / "docs/platform/release-matrix.md").read_text(
            encoding="utf-8"
        )
        matrix = "\n".join(
            line
            for line in matrix.splitlines()
            if "| Bounded document ingestion |" not in line
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            matrix_path = root / "docs/platform/release-matrix.md"
            matrix_path.parent.mkdir(parents=True)
            matrix_path.write_text(matrix, encoding="utf-8")
            errors = checker._release_matrix_feature_mapping_errors(
                root, self.registry["features"]
            )
        self.assertTrue(any("document-ingestion" in error for error in errors))

    def test_ast_route_inventory_matches_current_direct_route_contract(self) -> None:
        main = REPO_ROOT / "apps/api/app/main.py"
        errors: list[str] = []
        inventory = checker._implemented_route_inventory(
            REPO_ROOT, main.read_text(encoding="utf-8"), errors
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(inventory.routes), 133)
        self.assertEqual(set(inventory.routes), set(self.route_registry["routes"]))
        self.assertEqual(sum(record.hidden for record in inventory.routes.values()), 27)
        self.assertTrue(
            checker._route_is_hidden_from_openapi(
                "POST /v1/account-deletion-requests/{deletion_id}/confirm",
                inventory,
            )
        )

    def test_ast_route_inventory_follows_included_routers_without_execution(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "apps/api/app"
            routes = app / "routes"
            routes.mkdir(parents=True)
            (app / "__init__.py").write_text("", encoding="utf-8")
            (routes / "__init__.py").write_text("", encoding="utf-8")
            (app / "main.py").write_text(
                """
from fastapi import FastAPI
from app.routes.people import router as people_router
import app.routes.admin as admin_module

app = FastAPI()
app.include_router(people_router, prefix="/v1")
app.include_router(admin_module.router, prefix="/v1", include_in_schema=False)
""".strip(),
                encoding="utf-8",
            )
            (routes / "people.py").write_text(
                """
from fastapi import APIRouter

raise RuntimeError("the checker must not import this module")
PREFIX = "/people"
PATH = "/{handle}"
router = APIRouter(prefix=PREFIX)

@router.get(PATH)
async def read_person():
    return None
""".strip(),
                encoding="utf-8",
            )
            (routes / "admin.py").write_text(
                """
from fastapi import APIRouter

root_router = APIRouter(prefix="/admin")
private_router = APIRouter(prefix="/private")

@root_router.post("/visible", include_in_schema=False)
async def visible_admin_route():
    return None

@private_router.get("/item")
async def private_admin_route():
    return None

root_router.include_router(
    private_router, prefix="/nested", include_in_schema=False
)
router = root_router
""".strip(),
                encoding="utf-8",
            )
            errors: list[str] = []
            inventory = checker._implemented_route_inventory(
                root,
                (app / "main.py").read_text(encoding="utf-8"),
                errors,
            )

        self.assertEqual(errors, [])
        self.assertEqual(
            set(inventory.routes),
            {
                "GET /v1/people/{handle}",
                "POST /v1/admin/visible",
                "GET /v1/admin/nested/private/item",
            },
        )
        self.assertFalse(
            checker._route_is_hidden_from_openapi("GET /v1/people/{handle}", inventory)
        )
        self.assertTrue(
            checker._route_is_hidden_from_openapi("POST /v1/admin/visible", inventory)
        )
        self.assertTrue(
            checker._route_is_hidden_from_openapi(
                "GET /v1/admin/nested/private/item", inventory
            )
        )

    def test_ast_route_inventory_fails_closed_on_duplicate_and_dynamic_router_data(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "apps/api/app"
            routes = app / "routes"
            routes.mkdir(parents=True)
            (app / "main.py").write_text(
                """
from fastapi import FastAPI
from app.routes.extra import router

app = FastAPI()

@app.get("/v1/duplicate/item")
async def existing_route():
    return None

app.include_router(router, prefix="/v1")
""".strip(),
                encoding="utf-8",
            )
            (routes / "extra.py").write_text(
                """
from fastapi import APIRouter

router = APIRouter(prefix="/duplicate")

@router.get("/item")
async def duplicate_route():
    return None

@router.post(ROUTE_PATH)
async def dynamic_route():
    return None
""".strip(),
                encoding="utf-8",
            )
            errors: list[str] = []
            inventory = checker._implemented_route_inventory(
                root,
                (app / "main.py").read_text(encoding="utf-8"),
                errors,
            )

        self.assertIn("GET /v1/duplicate/item", inventory.routes)
        self.assertTrue(
            any(
                "duplicates implemented route 'GET /v1/duplicate/item'" in e
                for e in errors
            )
        )
        self.assertTrue(any("statically resolved string" in e for e in errors))

    def test_ast_route_inventory_ignores_unmounted_modules_and_rejects_unsupported_methods(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "apps/api/app"
            routes = app / "routes"
            routes.mkdir(parents=True)
            (app / "main.py").write_text(
                """
from fastapi import FastAPI
from app.routes.used import router

app = FastAPI()
app.include_router(router)
""".strip(),
                encoding="utf-8",
            )
            (routes / "used.py").write_text(
                """
from fastapi import APIRouter

router = APIRouter()

@router.api_route("/unsupported")
async def unsupported_route():
    return None
""".strip(),
                encoding="utf-8",
            )
            (routes / "unused.py").write_text(
                """
from fastapi import APIRouter

router = APIRouter()

@router.get("/must-not-be-invented")
async def unmounted_route():
    return None
""".strip(),
                encoding="utf-8",
            )
            errors: list[str] = []
            inventory = checker._implemented_route_inventory(
                root,
                (app / "main.py").read_text(encoding="utf-8"),
                errors,
            )

        self.assertEqual(inventory.routes, {})
        self.assertTrue(
            any(
                "unsupported APIRouter route decorator 'api_route'" in e for e in errors
            )
        )
        self.assertFalse(any("must-not-be-invented" in e for e in errors))

    def test_public_trust_surface_fails_closed_on_owner_or_semantic_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in (
                "apps/web/app/trust/page.tsx",
                "apps/web/tests/public-trust.test.ts",
            ):
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            ui_routes = dict(self.ui_route_registry["routes"])
            self.assertEqual(checker._public_trust_surface_errors(root, ui_routes), [])

            ui_routes["/trust"] = "agent-authority"
            errors = checker._public_trust_surface_errors(root, ui_routes)
            self.assertTrue(any("must map '/trust'" in error for error in errors))

            ui_routes["/trust"] = "public-search"
            page = root / "apps/web/app/trust/page.tsx"
            page.write_text(
                page.read_text(encoding="utf-8").replace(
                    "This Vercel deployment is a standalone drafting site.",
                    "This deployment stores drafts remotely.",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._public_trust_surface_errors(root, ui_routes)
            self.assertTrue(
                any("plain-language current behavior" in error for error in errors)
            )

    def test_api_semantic_parity_controls_fail_closed(self) -> None:
        relative_paths = (
            "apps/api/app/main.py",
            "apps/api/app/services/public_search.py",
            "apps/api/app/routes/taxonomy.py",
            "apps/api/app/protocol_arguments.py",
            "apps/api/app/services/documents.py",
            "apps/api/tests/test_agent_identity_mandates.py",
            "apps/api/tests/test_public_post_inventory.py",
            "apps/api/tests/test_api.py",
            "apps/api/tests/test_protocol_core.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in relative_paths:
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )

            self.assertEqual(checker._api_semantic_parity_errors(root), [])

            protocol = root / "apps/api/app/protocol_arguments.py"
            valid_protocol = protocol.read_text(encoding="utf-8")
            protocol.unlink()
            errors = checker._api_semantic_parity_errors(root)
            self.assertTrue(
                any(
                    "apps/api/app/protocol_arguments.py: cannot read file" in error
                    for error in errors
                )
            )
            protocol.write_text(valid_protocol, encoding="utf-8")

            protocol.write_text(
                valid_protocol.replace(
                    "if normalized != value:",
                    "if normalized.lower() != value:",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._api_semantic_parity_errors(root)
            self.assertTrue(
                any("lowercase canonical equality" in error for error in errors)
            )
            protocol.write_text(valid_protocol, encoding="utf-8")

            main = root / "apps/api/app/main.py"
            valid_main = main.read_text(encoding="utf-8")
            main.write_text(
                valid_main.replace(
                    '_canonical_agent_outreach_request_id(data.get("request_id"))',
                    'str(data.get("request_id"))',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._api_semantic_parity_errors(root)
            self.assertTrue(any("canonical UUID parser" in error for error in errors))

            main.write_text(valid_main, encoding="utf-8")
            main.write_text(
                valid_main.replace(
                    "responses=_POST_READ_RESPONSES",
                    "responses=_POST_JSON_RESPONSES",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._api_semantic_parity_errors(root)
            self.assertTrue(any("post JSON route binding" in error for error in errors))

            main.write_text(valid_main, encoding="utf-8")
            post_read_start = valid_main.index(
                "_POST_READ_RESPONSES: dict[int | str, dict[str, Any]]"
            )
            post_markdown_start = valid_main.index(
                "_POST_MARKDOWN_ONLY_RESPONSES: dict[int | str, dict[str, Any]]",
                post_read_start,
            )
            post_read_source = valid_main[post_read_start:post_markdown_start].replace(
                'content": {MARKDOWN_MEDIA_TYPE:',
                'content_removed": {MARKDOWN_MEDIA_TYPE:',
                1,
            )
            main.write_text(
                valid_main[:post_read_start]
                + post_read_source
                + valid_main[post_markdown_start:],
                encoding="utf-8",
            )
            errors = checker._api_semantic_parity_errors(root)
            self.assertTrue(
                any("JSON/Markdown media type" in error for error in errors)
            )

            main.write_text(valid_main, encoding="utf-8")
            main.write_text(
                valid_main.replace(
                    'detail="location_id accepts one value"',
                    'detail="location_id is invalid"',
                    2,
                ),
                encoding="utf-8",
            )
            errors = checker._api_semantic_parity_errors(root)
            self.assertTrue(any("singleton rejection" in error for error in errors))

            main.write_text(valid_main, encoding="utf-8")
            update_start = valid_main.index("    async def _update_document_write(")
            update_end = valid_main.index(
                "    async def update_document(", update_start
            )
            update_source = valid_main[update_start:update_end].replace(
                "replay = await idempotency_replay(",
                "replay = await missing_replay(",
                1,
            )
            main.write_text(
                valid_main[:update_start] + update_source + valid_main[update_end:],
                encoding="utf-8",
            )
            errors = checker._api_semantic_parity_errors(root)
            self.assertTrue(
                any("must consult replay before" in error for error in errors)
            )

            service = root / "apps/api/app/services/documents.py"
            valid_service = service.read_text(encoding="utf-8")
            service.write_text(
                valid_service.replace(
                    "return header == current_etag",
                    "return header != current_etag",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._api_semantic_parity_errors(root)
            self.assertTrue(any("byte-exact comparison" in error for error in errors))

    def test_cursor_contract_controls_fail_closed(self) -> None:
        relative_paths = (
            "apps/api/app/main.py",
            "apps/api/app/services/public_search.py",
            "apps/api/app/routes/taxonomy.py",
            "apps/api/app/protocol_arguments.py",
            "apps/api/tests/test_exact_search.py",
            "apps/api/tests/test_protocol_core.py",
            "apps/api/tests/test_agent_identity_directory.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in relative_paths:
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )

            self.assertEqual(checker._cursor_contract_errors(root), [])

            main = root / "apps/api/app/main.py"
            valid_main = main.read_text(encoding="utf-8")
            main.write_text(
                valid_main.replace(
                    'len(request.query_params.getlist("cursor")) > 1',
                    'len(request.query_params.getlist("cursor")) > 2',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._cursor_contract_errors(root)
            self.assertTrue(
                any("duplicate cursor query guard" in error for error in errors)
            )

            taxonomy = root / "apps/api/app/routes/taxonomy.py"
            valid_taxonomy = taxonomy.read_text(encoding="utf-8")
            taxonomy.write_text(
                valid_taxonomy.replace(
                    "cursor: Annotated[str | None, Query(min_length=1, max_length=2048)] = None",
                    "cursor: Annotated[str | None, Query(min_length=1, max_length=2047)] = None",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._cursor_contract_errors(root)
            self.assertTrue(
                any("taxonomy terms OpenAPI cursor bound" in error for error in errors)
            )
            taxonomy.write_text(valid_taxonomy, encoding="utf-8")

            protocol = root / "apps/api/app/protocol_arguments.py"
            valid_protocol = protocol.read_text(encoding="utf-8")
            protocol.write_text(
                valid_protocol.replace(
                    "not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 2048",
                    "not isinstance(cursor, str) or cursor.strip() or len(cursor) > 2048",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._cursor_contract_errors(root)
            self.assertTrue(
                any("protocol blank/bounds guard" in error for error in errors)
            )
            protocol.write_text(
                valid_protocol.replace(
                    "not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 2048",
                    "not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 2048"
                    " or not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 2048",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._protocol_argument_contract_errors(root)
            self.assertTrue(
                any(
                    "exactly one cursor blank/bounds guard" in error for error in errors
                )
            )

            protocol.write_text(
                valid_protocol
                + "\n\ndef protocol_search_arguments(arguments: dict[str, object], **kwargs: object) -> dict[str, object]:\n    return {}\n",
                encoding="utf-8",
            )
            errors = checker._protocol_argument_contract_errors(root)
            self.assertTrue(
                any(
                    "must define exactly one 'protocol_search_arguments' function"
                    in error
                    for error in errors
                )
            )
            protocol.write_text(valid_protocol, encoding="utf-8")

            directory_start = valid_main.index("def agent_directory_statement(")
            directory_end = valid_main.index(
                "\n    def reject_duplicate_cursor_query_parameter", directory_start
            )
            directory_source = valid_main[directory_start:directory_end].replace(
                "where(*public_agent_identity_eligibility_filters())", "where()", 1
            )
            main.write_text(
                valid_main[:directory_start]
                + directory_source
                + valid_main[directory_end:],
                encoding="utf-8",
            )
            errors = checker._cursor_contract_errors(root)
            self.assertTrue(
                any("live eligibility predicate" in error for error in errors)
            )

    def test_invalid_schema_fails_closed(self) -> None:
        invalid_schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        invalid_schema["$schema"] = "not-a-json-schema-dialect"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schema.json"
            path.write_text(json.dumps(invalid_schema), encoding="utf-8")
            self.assertTrue(checker.check_registry(REGISTRY_PATH, path, REPO_ROOT))

        invalid_evidence_schema = json.loads(json.dumps(self.evidence_schema))
        invalid_evidence_schema["additionalProperties"] = True
        invalid_evidence_schema["$id"] = "https://example.invalid/permissive.json"
        invalid_evidence_schema["required"].remove("source_revision")
        invalid_evidence_schema["properties"]["source_revision"]["pattern"] = ".*"
        invalid_evidence_schema["properties"]["checks"]["items"]["properties"][
            "check_id"
        ]["pattern"] = ".*"
        errors = self.check(self.registry, evidence_schema=invalid_evidence_schema)
        self.assertTrue(any(error.startswith("evidence_schema.") for error in errors))

    def test_duplicate_feature_id_fails(self) -> None:
        duplicate = json.loads(json.dumps(self.registry))
        duplicate["features"][1]["id"] = duplicate["features"][0]["id"]
        self.assertTrue(
            any("duplicates feature id" in error for error in self.check(duplicate))
        )

    def test_advertised_protocol_needs_route_and_test(self) -> None:
        invalid = json.loads(json.dumps(self.registry))
        protocol = invalid["features"][4]["surfaces"]["protocols"][0]
        protocol["routes"] = []
        protocol["tests"] = []
        errors = self.check(invalid)
        self.assertTrue(any("must not be empty" in error for error in errors))

    def test_invalid_repository_path_fails(self) -> None:
        invalid = json.loads(json.dumps(self.registry))
        invalid["features"][0]["implementation"]["paths"] = ["../outside.py"]
        self.assertTrue(
            any(
                "safe repository-relative path" in error
                for error in self.check(invalid)
            )
        )

    def test_nonexistent_api_and_ui_routes_fail(self) -> None:
        invalid = json.loads(json.dumps(self.registry))
        invalid["features"][0]["surfaces"]["api"]["routes"].append(
            "GET /v1/not-a-real-route"
        )
        invalid["features"][0]["surfaces"]["ui"]["routes"].append("/not-a-real-page")
        errors = self.check(invalid)
        self.assertTrue(
            any("not implemented by apps/api/app/main.py" in error for error in errors)
        )
        self.assertTrue(
            any("not implemented by apps/web/app" in error for error in errors)
        )

    def test_every_implemented_route_requires_exact_feature_ownership(self) -> None:
        missing = json.loads(json.dumps(self.route_registry))
        missing["routes"].pop("POST /v1/profiles")
        self.assertTrue(
            any(
                "does not own implemented routes" in error
                for error in self.check(self.registry, missing)
            )
        )

        stale = json.loads(json.dumps(self.route_registry))
        stale["routes"]["GET /v1/not-a-real-route"] = "canonical-documents"
        self.assertTrue(
            any(
                "owns routes absent" in error
                for error in self.check(self.registry, stale)
            )
        )

        wrong_owner = json.loads(json.dumps(self.route_registry))
        wrong_owner["routes"]["POST /v1/profiles"] = "public-search"
        self.assertTrue(
            any(
                "is owned by feature 'public-search'" in error
                for error in self.check(self.registry, wrong_owner)
            )
        )

    def test_route_ownership_domain_extraction_fails_closed_on_shape_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            route_path = root / "routes.json"
            ui_route_path = root / "ui-routes.json"
            route_path.write_text(json.dumps(self.route_registry), encoding="utf-8")
            ui_route_path.write_text(
                json.dumps(self.ui_route_registry), encoding="utf-8"
            )

            route_ownership, ui_route_ownership, errors, fatal = (
                checker._load_route_ownership(route_path, ui_route_path)
            )
            self.assertFalse(fatal)
            self.assertEqual(errors, [])
            self.assertIn("POST /v1/profiles", route_ownership)
            self.assertIn("/search", ui_route_ownership)

            invalid_route = json.loads(json.dumps(self.route_registry))
            invalid_route["unexpected"] = True
            route_path.write_text(json.dumps(invalid_route), encoding="utf-8")
            errors = checker._load_route_ownership(route_path, ui_route_path)[2]
            self.assertTrue(
                any("route_registry: has unknown fields" in error for error in errors)
            )

            invalid_ui = json.loads(json.dumps(self.ui_route_registry))
            invalid_ui["routes"]["not-a-route"] = "public-search"
            route_path.write_text(json.dumps(self.route_registry), encoding="utf-8")
            ui_route_path.write_text(json.dumps(invalid_ui), encoding="utf-8")
            errors = checker._load_route_ownership(route_path, ui_route_path)[2]
            self.assertTrue(
                any(
                    "has invalid UI route key: 'not-a-route'" in error
                    for error in errors
                )
            )

            record = type("Record", (), {"hidden": False})()
            inventory = type(
                "Inventory", (), {"routes": {"GET /v1/profiles": record}}
            )()
            errors = checker._route_ownership_parity_errors(
                {}, {}, inventory, {"canonical-documents"}
            )
            self.assertTrue(
                any("does not own implemented routes" in error for error in errors)
            )

    def test_api_route_cannot_be_claimed_by_multiple_features(self) -> None:
        invalid = json.loads(json.dumps(self.registry))
        public_search = next(
            feature
            for feature in invalid["features"]
            if feature["id"] == "public-search"
        )
        public_search["surfaces"]["api"]["routes"].append("POST /v1/profiles")
        self.assertTrue(
            any(
                "is also claimed by feature 'canonical-documents'" in error
                for error in self.check(invalid)
            )
        )

    def test_every_implemented_ui_route_requires_a_registered_owner(self) -> None:
        missing = json.loads(json.dumps(self.ui_route_registry))
        missing["routes"].pop("/search")
        self.assertTrue(
            any(
                "does not own implemented UI routes" in error
                for error in self.check(self.registry, ui_route_registry=missing)
            )
        )

        stale = json.loads(json.dumps(self.ui_route_registry))
        stale["routes"]["/not-a-real-page"] = "public-search"
        self.assertTrue(
            any(
                "owns UI routes absent" in error
                for error in self.check(self.registry, ui_route_registry=stale)
            )
        )

        unknown_owner = json.loads(json.dumps(self.ui_route_registry))
        unknown_owner["routes"]["/search"] = "missing-feature"
        self.assertTrue(
            any(
                "references unregistered feature 'missing-feature'" in error
                for error in self.check(self.registry, ui_route_registry=unknown_owner)
            )
        )

        wrong_owner = json.loads(json.dumps(self.ui_route_registry))
        wrong_owner["routes"]["/account"] = "public-search"
        self.assertTrue(
            any(
                "owner feature 'public-search' does not declare the UI route" in error
                for error in self.check(self.registry, ui_route_registry=wrong_owner)
            )
        )

        missing_anchor = json.loads(json.dumps(self.registry))
        lifecycle = next(
            feature
            for feature in missing_anchor["features"]
            if feature["id"] == "account-lifecycle"
        )
        lifecycle["surfaces"]["ui"]["routes"] = []
        self.assertTrue(
            any(
                "owner feature 'account-lifecycle' does not declare the UI route"
                in error
                for error in self.check(missing_anchor)
            )
        )

    def test_next_page_enumeration_covers_supported_roots_and_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "apps" / "web" / "src" / "app"
            pages = {
                app / "page.js": "/",
                app / "(public)" / "search" / "page.jsx": "/search",
                app / "p" / "[handle]" / "page.ts": "/p/{handle}",
                app / "jobs" / "page.tsx": "/jobs",
            }
            for page in pages:
                page.parent.mkdir(parents=True, exist_ok=True)
                page.write_text("export default function Page() {}", encoding="utf-8")
            errors: list[str] = []
            self.assertEqual(
                checker._implemented_ui_routes(root, errors), set(pages.values())
            )
            self.assertEqual(errors, [])

            catch_all = app / "docs" / "[...slug]" / "page.tsx"
            catch_all.parent.mkdir(parents=True)
            catch_all.write_text("export default function Page() {}", encoding="utf-8")
            errors = []
            checker._implemented_ui_routes(root, errors)
            self.assertTrue(
                any("unsupported Next.js route segment" in e for e in errors)
            )

            second_root = root / "apps" / "web" / "app"
            second_root.mkdir(parents=True)
            (second_root / "page.tsx").write_text(
                "export default function Page() {}", encoding="utf-8"
            )
            errors = []
            self.assertEqual(checker._implemented_ui_routes(root, errors), set())
            self.assertTrue(any("select one App Router root" in e for e in errors))

    def test_feature_gated_api_route_must_be_hidden_from_openapi(self) -> None:
        invalid = json.loads(json.dumps(self.registry))
        lifecycle = next(
            feature
            for feature in invalid["features"]
            if feature["id"] == "account-lifecycle"
        )
        lifecycle["surfaces"]["api"]["routes"].append("POST /v1/profiles")
        self.assertTrue(
            any(
                "route declared hidden is visible in OpenAPI" in error
                for error in self.check(invalid)
            )
        )

    def test_recruiting_release_gate_is_default_off_and_counterexamples_fail_closed(
        self,
    ) -> None:
        main_source = (REPO_ROOT / "apps/api/app/main.py").read_text(encoding="utf-8")
        route_errors: list[str] = []
        inventory = checker._implemented_route_inventory(
            REPO_ROOT, main_source, route_errors
        )
        self.assertEqual(route_errors, [])
        self.assertEqual(
            checker._recruiting_release_gate_errors(REPO_ROOT, inventory), []
        )
        self.assertEqual(
            main_source.count("include_in_schema=settings.recruiting_enabled,"), 27
        )

        relative_files = (
            ".env.example",
            "compose.yaml",
            "apps/api/app/config.py",
            "apps/api/app/main.py",
            "apps/api/app/cli.py",
            "apps/api/tests/conftest.py",
            "apps/api/tests/test_config.py",
            "apps/api/tests/test_cli_recruiting_evidence.py",
            "apps/api/tests/test_recruiting_release_gate.py",
            "apps/web/app/discover/page.tsx",
            "apps/web/app/jobs/[organizationSlug]/[jobSlug]/page.tsx",
            "apps/web/app/jobs/page.tsx",
            "apps/web/app/organizations/[slug]/page.tsx",
            "apps/web/app/organizations/page.tsx",
            "apps/web/app/page.tsx",
            "apps/web/app/robots.ts",
            "apps/web/app/sitemap.ts",
            "apps/web/app/trust/page.tsx",
            "apps/web/components/discover-hub.tsx",
            "apps/web/e2e/public-release.spec.ts",
            "apps/web/lib/recruiting-release.ts",
            "apps/web/middleware.ts",
            "apps/web/public/llms.txt",
            "apps/web/tests/agent-first-landing.test.ts",
            "apps/web/tests/discover-hub.test.ts",
            "apps/web/tests/public-trust.test.ts",
            "apps/web/tests/recruiting-release.test.ts",
            "apps/web/tests/sitemap.test.ts",
            "docs/deployment.md",
            "docs/trust-safety.md",
            "docs/platform/release-matrix.md",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in relative_files:
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO_ROOT / relative_path, target)
            shutil.copytree(
                REPO_ROOT / "apps/api/app/routes",
                root / "apps/api/app/routes",
                dirs_exist_ok=True,
            )

            def gate_errors() -> list[str]:
                source = (root / "apps/api/app/main.py").read_text(encoding="utf-8")
                inventory_errors: list[str] = []
                current_inventory = checker._implemented_route_inventory(
                    root, source, inventory_errors
                )
                self.assertEqual(inventory_errors, [])
                return checker._recruiting_release_gate_errors(root, current_inventory)

            config = root / "apps/api/app/config.py"
            original_config = config.read_text(encoding="utf-8")
            config.write_text(
                original_config.replace(
                    "recruiting_enabled: bool = False",
                    "recruiting_enabled: bool = True",
                    1,
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                any("literal false default" in error for error in gate_errors())
            )
            config.write_text(original_config, encoding="utf-8")

            main = root / "apps/api/app/main.py"
            original_main = main.read_text(encoding="utf-8")
            main.write_text(
                original_main.replace(
                    "include_in_schema=settings.recruiting_enabled,",
                    "include_in_schema=True,",
                    1,
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                any("dynamic OpenAPI gate" in error for error in gate_errors())
            )

            submission_gate = (
                ") -> ApplicationResponse | Response:\n"
                "        require_recruiting_release()\n"
                "        require_application_human(principal)"
            )
            self.assertIn(submission_gate, original_main)
            main.write_text(
                original_main.replace(
                    submission_gate,
                    ") -> ApplicationResponse | Response:\n"
                    "        require_application_human(principal)",
                    1,
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                any(
                    "gate before application authority and lookup" in error
                    for error in gate_errors()
                )
            )

            discovery = root / "apps/api/app/routes/discovery.py"
            original_discovery = discovery.read_text(encoding="utf-8")
            discovery.write_text(
                original_discovery.replace(
                    "request.app.state.settings.recruiting_enabled",
                    "True",
                ),
                encoding="utf-8",
            )
            main.write_text(original_main, encoding="utf-8")
            self.assertTrue(any("runtime setting" in error for error in gate_errors()))

            discovery.write_text(original_discovery, encoding="utf-8")
            protocol_metadata = root / "apps/api/app/routes/protocol_metadata.py"
            original_protocol_metadata = protocol_metadata.read_text(encoding="utf-8")
            protocol_metadata.write_text(
                original_protocol_metadata.replace(
                    '"organizations:read"', '"organizations:unknown"', 1
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                any("organization read scope" in error for error in gate_errors())
            )
            protocol_metadata.write_text(original_protocol_metadata, encoding="utf-8")

            main.write_text(
                original_main.replace(
                    "from app.routes.protocol_metadata import router as protocol_metadata_router",
                    "from app.routes.missing_protocol_metadata import router as protocol_metadata_router",
                    1,
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                any(
                    "router import" in error
                    for error in checker._recruiting_release_gate_errors(
                        root, inventory
                    )
                )
            )
            main.write_text(original_main, encoding="utf-8")
            main.write_text(
                original_main.replace(
                    "    app.include_router(protocol_metadata_router)\n", "", 1
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                any(
                    "router inclusion" in error
                    for error in checker._recruiting_release_gate_errors(
                        root, inventory
                    )
                )
            )
            main.write_text(original_main, encoding="utf-8")

    def test_required_feature_and_model_coverage_fail_closed(self) -> None:
        missing_feature = json.loads(json.dumps(self.registry))
        missing_feature["features"] = [
            feature
            for feature in missing_feature["features"]
            if feature["id"] != "account-lifecycle"
        ]
        self.assertTrue(
            any(
                "missing required feature ids" in error
                for error in self.check(missing_feature)
            )
        )

        missing_model = json.loads(json.dumps(self.registry))
        missing_model["features"][0]["data"]["models"].remove("Document")
        self.assertTrue(
            any(
                "does not classify persistent models" in error
                for error in self.check(missing_model)
            )
        )

    def test_discovery_states_enforce_feature_gates_and_disabled_absence(self) -> None:
        advertised_lifecycle = json.loads(json.dumps(self.registry))
        lifecycle = next(
            feature
            for feature in advertised_lifecycle["features"]
            if feature["id"] == "account-lifecycle"
        )
        lifecycle["surfaces"]["discovery"]["capabilities"] = "advertised"
        self.assertTrue(
            any(
                "feature-gated features must remain hidden" in error
                for error in self.check(advertised_lifecycle)
            )
        )

        weak_disabled_gate = json.loads(json.dumps(self.registry))
        external_egress = next(
            feature
            for feature in weak_disabled_gate["features"]
            if feature["id"] == "external-egress"
        )
        external_egress["evidence"]["feature_gate"] = "disabled_by_default"
        self.assertTrue(
            any(
                "requires feature_gate 'absence_enforced'" in error
                for error in self.check(weak_disabled_gate)
            )
        )

    def test_lifecycle_is_absent_from_public_discovery_regions(self) -> None:
        source = (REPO_ROOT / "apps" / "api" / "app" / "main.py").read_text(
            encoding="utf-8"
        )
        discovery = (
            REPO_ROOT / "apps" / "api" / "app" / "routes" / "discovery.py"
        ).read_text(encoding="utf-8")
        agent_card = (
            REPO_ROOT / "apps" / "api" / "app" / "routes" / "agent_card.py"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            checker._lifecycle_discovery_errors(source, discovery, agent_card), []
        )
        advertised = source.replace(
            '"outbound_delivery": False,',
            '"outbound_delivery": False, "lifecycle": "/v1/account/export",',
            1,
        )
        self.assertTrue(
            checker._lifecycle_discovery_errors(advertised, discovery, agent_card)
        )
        concatenated = source.replace(
            '"outbound_delivery": False,',
            '"outbound_delivery": False, "lifecycle": "/v1/" + "account/export",',
            1,
        )
        self.assertTrue(
            checker._lifecycle_discovery_errors(concatenated, discovery, agent_card)
        )
        module_constant = (
            'LIFECYCLE_DISCOVERY_URL = "/v1/account/export"\n' + source
        ).replace(
            '"outbound_delivery": False,',
            '"outbound_delivery": False, "lifecycle": LIFECYCLE_DISCOVERY_URL,',
            1,
        )
        self.assertTrue(
            checker._lifecycle_discovery_errors(module_constant, discovery, agent_card)
        )
        formatted = source.replace(
            '"outbound_delivery": False,',
            '"outbound_delivery": False, "lifecycle": f"/v1/{\'account\'}/export",',
            1,
        )
        self.assertTrue(
            checker._lifecycle_discovery_errors(formatted, discovery, agent_card)
        )
        advertised_llms = discovery.replace(
            "## Primary operations",
            "## Account export\n\nUse /v1/account/export.\n\n## Primary operations",
            1,
        )
        self.assertTrue(
            checker._lifecycle_discovery_errors(source, advertised_llms, agent_card)
        )

    def test_llms_copy_ready_workflow_is_ast_guarded(self) -> None:
        source = (
            REPO_ROOT / "apps" / "api" / "app" / "routes" / "discovery.py"
        ).read_text(encoding="utf-8")
        self.assertEqual(checker._llms_workflow_errors(source), [])
        function_start = source.index("async def llms_txt")
        function_end = source.index('@router.get("/llms-full.txt"', function_start)
        function_source = source[function_start:function_end]
        mutations = {
            "authenticated bearer write": (
                "Authorization: Bearer $CONNECTMD_TOKEN",
                "Authorization: Token $CONNECTMD_TOKEN",
            ),
            "raw Markdown request": (
                "Content-Type: text/markdown",
                "Content-Type: application/json",
            ),
            "public search": (
                "curl --get '{base}/v1/search'",
                "curl --get '{base}/search-example-removed'",
            ),
            "canonical Markdown read": (
                "Accept: text/markdown",
                "Accept: application/json",
            ),
            "raw Markdown create": ("curl -X POST", "curl --request CREATE"),
            "canonical read capture": (
                "-D profile.headers -o current-profile.md",
                "-o current-profile.md",
            ),
            "conditional update": ("If-Match: $ETAG", "X-Stale-Check: $ETAG"),
            "conditional update operation": ("curl -X PUT", "curl --request REPLACE"),
            "idempotent write": ("Idempotency-Key:", "Retry-Key:"),
            "raw Markdown payload": (
                "--data-binary '@profile.md'",
                "--data '{}'",
            ),
            "conditional raw Markdown payload": (
                "--data-binary '@current-profile.md'",
                "--data '{} --conditional'",
            ),
        }
        for label, (old, new) in mutations.items():
            with self.subTest(label=label):
                mutated_function = function_source.replace(old, new)
                self.assertNotEqual(mutated_function, function_source)
                mutated = (
                    source[:function_start] + mutated_function + source[function_end:]
                )
                self.assertTrue(checker._llms_workflow_errors(mutated))

    def test_discovery_agreement_fails_closed_on_each_surface_drift(self) -> None:
        source = (REPO_ROOT / "apps/api/app/main.py").read_text(encoding="utf-8")
        discovery = (REPO_ROOT / "apps/api/app/routes/discovery.py").read_text(
            encoding="utf-8"
        )
        agent_card = (REPO_ROOT / "apps/api/app/routes/agent_card.py").read_text(
            encoding="utf-8"
        )

        def errors_for(
            mutated: str,
            mutated_discovery: str = discovery,
            mutated_agent_card: str = agent_card,
        ) -> list[str]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / "apps/api/app/main.py"
                path.parent.mkdir(parents=True)
                path.write_text(mutated, encoding="utf-8")
                for relative_path in (
                    "apps/api/app/routes/health.py",
                    "apps/api/app/routes/taxonomy.py",
                    "apps/api/app/routes/protocol_metadata.py",
                    "apps/api/app/routes/agent_card.py",
                    "apps/api/app/routes/schemas.py",
                ):
                    fixture_path = root / relative_path
                    fixture_path.parent.mkdir(parents=True, exist_ok=True)
                    fixture_path.write_text(
                        mutated_agent_card
                        if relative_path == "apps/api/app/routes/agent_card.py"
                        else (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                        encoding="utf-8",
                    )
                discovery_path = root / "apps/api/app/routes/discovery.py"
                discovery_path.parent.mkdir(parents=True, exist_ok=True)
                discovery_path.write_text(mutated_discovery, encoding="utf-8")
                return checker._discovery_agreement_errors(root)

        self.assertEqual(errors_for(source), [])

        mcp_start = source.index("    def mcp_tools()")
        mcp_end = source.index('\n    @app.get("/mcp"', mcp_start)
        mcp = source[mcp_start:mcp_end].replace(
            '"name": "list_taxonomies"', '"name": "removed_taxonomy_tool"', 1
        )
        self.assertTrue(
            any(
                "MCP tool(s) absent" in error
                for error in errors_for(source[:mcp_start] + mcp + source[mcp_end:])
            )
        )

        a2a_start = source.index('    @app.post("/a2a/message:send"')
        a2a_end = source.index("\n    def mcp_tool_result", a2a_start)
        a2a = source[a2a_start:a2a_end].replace(
            'if action == "list_taxonomies":',
            'if action == "removed_taxonomy_action":',
            1,
        )
        self.assertTrue(
            any(
                "A2A action(s) absent" in error
                for error in errors_for(source[:a2a_start] + a2a + source[a2a_end:])
            )
        )

        card = agent_card.replace(
            '{"action":"list_taxonomies"}',
            '{"action":"removed_taxonomy_action"}',
            1,
        )
        self.assertTrue(
            any(
                "Agent Card is missing" in error
                for error in errors_for(source, mutated_agent_card=card)
            )
        )

        unmounted = source.replace(
            "    app.include_router(agent_card_router)\n",
            "",
            1,
        )
        self.assertTrue(
            any("missing discovery route" in error for error in errors_for(unmounted))
        )

        llms_start = discovery.index('@router.get("/llms-full.txt"')
        llms = discovery[llms_start:].replace("list_taxonomies", "removed_taxonomy")
        self.assertTrue(
            any(
                "llms-full.txt is missing" in error
                for error in errors_for(source, discovery[:llms_start] + llms)
            )
        )

        self.assertTrue(
            any(
                "must remain advertised in OpenAPI" in error
                for error in errors_for(
                    source.replace(
                        '@app.get("/v1/capabilities", tags=["protocols"])',
                        '@app.get("/v1/capabilities", tags=["protocols"], include_in_schema=False)',
                        1,
                    )
                )
            )
        )

    def test_mcp_write_parity_anchors_fail_closed(self) -> None:
        files = (
            "apps/api/app/main.py",
            "apps/api/tests/test_protocol_core.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker._mcp_write_surface_errors(root), [])
            main = root / "apps/api/app/main.py"
            main.write_text(
                main.read_text(encoding="utf-8").replace(
                    '"name": "propose_document_update"',
                    '"name": "unsafe_document_write"',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._mcp_write_surface_errors(root)
            self.assertTrue(any("MCP proposal tool" in error for error in errors))

            main.write_text(
                (REPO_ROOT / "apps/api/app/main.py").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            tests = root / "apps/api/tests/test_protocol_core.py"
            tests.write_text(
                tests.read_text(encoding="utf-8").replace(
                    'assert unchanged.json()["version"] == 1',
                    'assert unchanged.json()["version"] >= 1',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._mcp_write_surface_errors(root)
            self.assertTrue(
                any("proposal leaves document unchanged" in error for error in errors)
            )

    def test_agent_authority_receipts_fail_closed(self) -> None:
        files = (
            "apps/api/app/main.py",
            "apps/api/app/services/api_key_replay.py",
            "apps/api/app/services/documents.py",
            "apps/api/tests/test_protocol_core.py",
            "apps/api/tests/test_api_key_atomicity.py",
            "apps/api/tests/test_impersonation_authority.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(
                checker._agent_authority_idempotency_surface_errors(root), []
            )

            main = root / "apps/api/app/main.py"
            original_main = main.read_text(encoding="utf-8")
            api_key_replay = root / "apps/api/app/services/api_key_replay.py"
            original_api_key_replay = api_key_replay.read_text(encoding="utf-8")
            api_key_replay.write_text(
                original_api_key_replay.replace(
                    "record.resource_id != recorded_key_id", "False", 1
                ),
                encoding="utf-8",
            )
            errors = checker._agent_authority_idempotency_surface_errors(root)
            self.assertTrue(
                any(
                    "API-key revocation owner-bound receipt" in error
                    for error in errors
                )
            )
            api_key_replay.write_text(
                original_api_key_replay.replace(
                    'if operation == "POST:/v1/api-keys":',
                    'raw_key = "unsafe"\n    if operation == "POST:/v1/api-keys":',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._agent_authority_idempotency_surface_errors(root)
            self.assertTrue(
                any(
                    "API-key replay must not expose credential 'raw_key'" in error
                    for error in errors
                )
            )
            api_key_replay.write_text(original_api_key_replay, encoding="utf-8")
            authority_start = original_main.index(
                "    def require_non_impersonated_clerk_human("
            )
            authority_end = original_main.index(
                "    def assert_direct(", authority_start
            )
            authority = original_main[authority_start:authority_end]
            main.write_text(
                original_main[:authority_start]
                + authority.replace(
                    "or principal.is_impersonated:",
                    "or False:",
                    1,
                )
                + original_main[authority_end:],
                encoding="utf-8",
            )
            errors = checker._agent_authority_idempotency_surface_errors(root)
            self.assertTrue(
                any(
                    "persistent authority impersonation denial" in error
                    for error in errors
                )
            )

            proposal_start = original_main.index(
                '    @app.post(\n        "/v1/proposals/{proposal_id}/{action}",'
            )
            proposal_end = original_main.index(
                "    async def decide_proposal(", proposal_start
            )
            proposal_route = original_main[proposal_start:proposal_end]
            main.write_text(
                original_main[:proposal_start]
                + proposal_route.replace(
                    '"pattern": _IDEMPOTENCY_KEY_PATTERN,', '"pattern": "unbounded",', 1
                )
                + original_main[proposal_end:],
                encoding="utf-8",
            )
            errors = checker._agent_authority_idempotency_surface_errors(root)
            self.assertTrue(any("proposal decision-route" in error for error in errors))

            main.write_text(
                original_main.replace(
                    "or not compare_digest(version_row.sha256, parts[4])",
                    "or False",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._agent_authority_idempotency_surface_errors(root)
            self.assertTrue(
                any("actual-version digest comparison" in error for error in errors)
            )

            create_start = original_main.index("    async def create_api_key(")
            create_end = original_main.index(
                '\n    @app.get(\n        "/v1/api-keys"', create_start
            )
            create = original_main[create_start:create_end]
            main.write_text(
                original_main[:create_start]
                + create.replace(
                    "require_non_impersonated_clerk_human(",
                    "missing_non_impersonated_clerk_human(",
                    1,
                )
                + original_main[create_end:],
                encoding="utf-8",
            )
            errors = checker._agent_authority_idempotency_surface_errors(root)
            self.assertTrue(
                any(
                    "API-key create non-impersonated Clerk dependency" in error
                    for error in errors
                )
            )

            main.write_text(
                original_main.replace(
                    "return await replay_api_key_receipt(",
                    "return await missing_api_key_replay_receipt(",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._agent_authority_idempotency_surface_errors(root)
            self.assertTrue(any("API-key replay dispatch" in error for error in errors))

            main.write_text(
                original_main.replace(
                    '"name": "create_document"',
                    '"name": "create_api_key"',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._agent_authority_idempotency_surface_errors(root)
            self.assertTrue(
                any("MCP API-key management tool" in error for error in errors)
            )

            main.write_text(original_main, encoding="utf-8")
            api_key_tests = root / "apps/api/tests/test_api_key_atomicity.py"
            api_key_tests.write_text(
                api_key_tests.read_text(encoding="utf-8").replace(
                    "test_concurrent_same_key_create_has_one_credential_and_one_event",
                    "test_concurrent_same_key_create_removed",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._agent_authority_idempotency_surface_errors(root)
            self.assertTrue(
                any("API-key create concurrency test" in error for error in errors)
            )

    def test_impersonation_read_only_guard_fails_closed_on_mutation_drift(self) -> None:
        files = (
            "apps/api/app/auth.py",
            "apps/api/tests/test_impersonation_authority.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker._impersonation_read_only_surface_errors(root), [])

            auth = root / "apps/api/app/auth.py"
            original_auth = auth.read_text(encoding="utf-8")
            auth.write_text(
                original_auth.replace(
                    "if clerk_principal.is_impersonated and _is_mutation(request):",
                    "if clerk_principal.is_impersonated:",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._impersonation_read_only_surface_errors(root)
            self.assertTrue(
                any("impersonation mutation guard" in error for error in errors)
            )

            auth.write_text(original_auth, encoding="utf-8")
            tests = root / "apps/api/tests/test_impersonation_authority.py"
            original_tests = tests.read_text(encoding="utf-8")
            tests.write_text(
                original_tests.replace(
                    "assert ordinary_create.status_code == 201",
                    "assert ordinary_create.status_code == 200",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._impersonation_read_only_surface_errors(root)
            self.assertTrue(any("ordinary Clerk success" in error for error in errors))

    def test_agent_authority_registry_requires_impersonation_anchor(self) -> None:
        registry = json.loads(json.dumps(self.registry))
        feature = next(
            item for item in registry["features"] if item["id"] == "agent-authority"
        )
        self.assertIn(
            "apps/api/tests/test_impersonation_authority.py",
            feature["tests"],
        )
        feature["tests"].remove("apps/api/tests/test_impersonation_authority.py")
        errors = checker._required_feature_anchor_errors(registry["features"])
        self.assertTrue(
            any(
                "agent-authority.tests" in error
                and "test_impersonation_authority.py" in error
                for error in errors
            )
        )

    def test_agent_grant_creation_durability_fails_closed(self) -> None:
        files = (
            "apps/api/app/auth.py",
            "apps/api/app/main.py",
            "apps/api/tests/test_agent_grant_atomicity.py",
            "apps/api/tests/test_protocol_core.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker._agent_grant_creation_durability_errors(root), [])

            main = root / "apps/api/app/main.py"
            original_main = main.read_text(encoding="utf-8")
            route_start = original_main.index(
                '    @app.post(\n        "/v1/agent-grants",'
            )
            route_end = original_main.index(
                "    async def create_agent_grant(", route_start
            )
            route = original_main[route_start:route_end]
            main.write_text(
                original_main[:route_start]
                + route.replace(
                    '"pattern": _IDEMPOTENCY_KEY_PATTERN,',
                    '"pattern": "unbounded",',
                    1,
                )
                + original_main[route_end:],
                encoding="utf-8",
            )
            errors = checker._agent_grant_creation_durability_errors(root)
            self.assertTrue(
                any("agent-grant-create-route" in error for error in errors)
            )

            create_start = original_main.index("    async def create_agent_grant(")
            create_end = original_main.index(
                '\n    @app.get(\n        "/v1/agent-grants",', create_start
            )
            create = original_main[create_start:create_end]
            main.write_text(
                original_main[:create_start]
                + create.replace(
                    "require_non_impersonated_clerk_human(",
                    "missing_non_impersonated_clerk_human(",
                    1,
                )
                + original_main[create_end:],
                encoding="utf-8",
            )
            errors = checker._agent_grant_creation_durability_errors(root)
            self.assertTrue(
                any(
                    "non-impersonated Clerk grant dependency" in error
                    for error in errors
                )
            )

            main.write_text(
                original_main[:create_start]
                + create.replace(
                    "return await agent_grant_recovery_replay(session, principal, existing, grant_context)",
                    "return await missing_agent_grant_recovery(session, principal, existing, grant_context)",
                    1,
                )
                + original_main[create_end:],
                encoding="utf-8",
            )
            errors = checker._agent_grant_creation_durability_errors(root)
            self.assertTrue(any("pre-resource recovery" in error for error in errors))

            recovery_start = original_main.index(
                "    async def agent_grant_recovery_replay("
            )
            recovery_end = original_main.index(
                "    async def idempotency_replay(", recovery_start
            )
            recovery = original_main[recovery_start:recovery_end]
            main.write_text(
                original_main[:recovery_start]
                + recovery.replace(
                    'record.response_headers != "{}"',
                    'record.response_headers != "not-empty"',
                    1,
                )
                + original_main[recovery_end:],
                encoding="utf-8",
            )
            errors = checker._agent_grant_creation_durability_errors(root)
            self.assertTrue(
                any("empty receipt header guard" in error for error in errors)
            )

            main.write_text(
                original_main[:create_start]
                + create.replace("commit=False,", "commit=True,", 1)
                + original_main[create_end:],
                encoding="utf-8",
            )
            errors = checker._agent_grant_creation_durability_errors(root)
            self.assertTrue(
                any("atomic Agent Grant create" in error for error in errors)
            )

            main.write_text(
                original_main.replace(
                    '"name": "create_document"', '"name": "create_agent_grant"', 1
                ),
                encoding="utf-8",
            )
            errors = checker._agent_grant_creation_durability_errors(root)
            self.assertTrue(
                any(
                    "mcp_tools must not expose Agent Grant issuance" in error
                    for error in errors
                )
            )

            main.write_text(original_main, encoding="utf-8")
            auth = root / "apps/api/app/auth.py"
            original_auth = auth.read_text(encoding="utf-8")
            auth.write_text(
                original_auth.replace(
                    'event_type="agent_grant.created"',
                    'event_type="agent_grant.created_missing"',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._agent_grant_creation_durability_errors(root)
            self.assertTrue(any("safe Agent Grant event" in error for error in errors))

            auth.write_text(original_auth, encoding="utf-8")
            grant_tests = root / "apps/api/tests/test_agent_grant_atomicity.py"
            original_grant_tests = grant_tests.read_text(encoding="utf-8")
            corruption_start = original_grant_tests.index(
                "async def test_agent_grant_corruption_never_replays_secret("
            )
            corruption_end = original_grant_tests.index(
                "\n\nasync def test_agent_grant_same_key_sqlite_gather_keeps_one_safe_receipt(",
                corruption_start,
            )
            corruption = original_grant_tests[corruption_start:corruption_end]
            grant_tests.write_text(
                original_grant_tests[:corruption_start]
                + corruption.replace(
                    "assert replay.status_code == 503",
                    "assert replay.status_code == 500",
                    1,
                )
                + original_grant_tests[corruption_end:],
                encoding="utf-8",
            )
            errors = checker._agent_grant_creation_durability_errors(root)
            self.assertTrue(
                any("corruption 503 assertion" in error for error in errors)
            )

    def test_organization_membership_durability_fails_closed(self) -> None:
        files = (
            "apps/api/app/main.py",
            "apps/api/tests/test_social_core.py",
            "apps/api/tests/conftest.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(
                checker._organization_membership_durability_errors(root), []
            )

            main = root / "apps/api/app/main.py"
            original_main = main.read_text(encoding="utf-8")
            invite_route_start = original_main.index(
                '@app.post(\n        "/v1/organizations/{organization_slug}/admins",'
            )
            accept_route_start = original_main.index(
                '@app.post(\n        "/v1/organizations/{organization_slug}/memberships/{membership_id}/accept",',
                invite_route_start,
            )
            invite_route = original_main[invite_route_start:accept_route_start]
            main.write_text(
                original_main[:invite_route_start]
                + invite_route.replace(
                    '"pattern": _IDEMPOTENCY_KEY_PATTERN,',
                    '"pattern": "unbounded",',
                    1,
                )
                + original_main[accept_route_start:],
                encoding="utf-8",
            )
            errors = checker._organization_membership_durability_errors(root)
            self.assertTrue(
                any("membership invitation-route" in error for error in errors)
            )

            invite_function_start = original_main.index(
                "    async def add_organization_admin("
            )
            invite_function_end = original_main.index(
                '\n    @app.post(\n        "/v1/organizations/{organization_slug}/memberships/{membership_id}/accept",',
                invite_function_start,
            )
            invite_function = original_main[invite_function_start:invite_function_end]
            main.write_text(
                original_main[:invite_function_start]
                + invite_function.replace("owner_only=True", "owner_only=False", 1)
                + original_main[invite_function_end:],
                encoding="utf-8",
            )
            errors = checker._organization_membership_durability_errors(root)
            self.assertTrue(
                any("owner-only invitation authority" in error for error in errors)
            )

            remove_function_start = original_main.index(
                "    async def remove_organization_admin("
            )
            remove_function_end = original_main.index(
                '\n    @app.post(\n        "/v1/organizations/{organization_slug}/jobs",',
                remove_function_start,
            )
            remove_function = original_main[remove_function_start:remove_function_end]
            main.write_text(
                original_main[:remove_function_start]
                + remove_function.replace(
                    "await assert_organization_authority(",
                    "await missing_organization_authority(",
                    1,
                )
                + original_main[remove_function_end:],
                encoding="utf-8",
            )
            errors = checker._organization_membership_durability_errors(root)
            self.assertTrue(any("current owner authority" in error for error in errors))

            main.write_text(
                original_main.replace(
                    "receipt_digest,\n                    _organization_membership_generation_digest(membership),",
                    'receipt_digest,\n                    "unbound-generation",',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._organization_membership_durability_errors(root)
            self.assertTrue(
                any("generation-bound acceptance replay" in error for error in errors)
            )

            replay_start = original_main.index("    async def idempotency_replay(")
            replay_end = original_main.index(
                "\n    async def store_idempotency(", replay_start
            )
            membership_replay = original_main[replay_start:replay_end]
            main.write_text(
                original_main[:replay_start]
                + membership_replay.replace(
                    'return Response(status_code=204, headers={"Idempotency-Replayed": "true"})',
                    'return Response(status_code=200, headers={"Idempotency-Replayed": "true"})',
                    1,
                )
                + original_main[replay_end:],
                encoding="utf-8",
            )
            errors = checker._organization_membership_durability_errors(root)
            self.assertTrue(
                any("exact empty removal replay" in error for error in errors)
            )

            main.write_text(
                original_main.replace(
                    '"name": "list_taxonomies"',
                    '"name": "manage_membership"',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._organization_membership_durability_errors(root)
            self.assertTrue(
                any(
                    "MCP must not expose organization membership" in error
                    for error in errors
                )
            )

            main.write_text(original_main, encoding="utf-8")
            social_tests = root / "apps/api/tests/test_social_core.py"
            social_tests.write_text(
                social_tests.read_text(encoding="utf-8").replace(
                    "test_membership_same_key_accept_and_remove_concurrency_replays_once",
                    "test_membership_same_key_accept_and_remove_concurrency_removed",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._organization_membership_durability_errors(root)
            self.assertTrue(
                any("same-key concurrency coverage" in error for error in errors)
            )

    def test_contact_durability_fails_closed(self) -> None:
        files = (
            "apps/api/app/main.py",
            "apps/api/app/services/contact_policy_replay.py",
            "apps/api/tests/test_contact_durability.py",
            "apps/api/tests/conftest.py",
            "apps/web/lib/outreach-api.ts",
            "apps/web/components/outreach-inbox.tsx",
            "apps/web/tests/agent-outreach-api.test.ts",
            "apps/web/tests/outreach-inbox.test.ts",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker._contact_durability_errors(root), [])

            main = root / "apps/api/app/main.py"
            original_main = main.read_text(encoding="utf-8")
            helper = root / "apps/api/app/services/contact_policy_replay.py"
            original_helper = helper.read_text(encoding="utf-8")
            main.write_text(
                original_main.replace(
                    "replay_contact_policy_receipt(",
                    "removed_contact_policy_receipt(",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._contact_durability_errors(root)
            self.assertTrue(any("policy replay adapter" in error for error in errors))
            main.write_text(original_main, encoding="utf-8")

            helper.write_text(
                original_helper.replace(
                    "content=record.response_body",
                    "content=canonical_body",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._contact_durability_errors(root)
            self.assertTrue(
                any("content=record.response_body" in error for error in errors)
            )
            helper.write_text(original_helper, encoding="utf-8")

            helper.write_text(
                original_helper.replace(
                    'record.resource_type != "contact_policy"',
                    'record.resource_type != "other"',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._contact_durability_errors(root)
            self.assertTrue(
                any(
                    'record.resource_type != "contact_policy"' in error
                    for error in errors
                )
            )
            helper.write_text(original_helper, encoding="utf-8")

            policy_start = original_main.index(
                '@app.put(\n        "/v1/contact-policy",'
            )
            policy_end = original_main.index(
                "    async def update_contact_policy(", policy_start
            )
            policy_route = original_main[policy_start:policy_end]
            main.write_text(
                original_main[:policy_start]
                + policy_route.replace(
                    '"pattern": _IDEMPOTENCY_KEY_PATTERN,',
                    '"pattern": "unbounded",',
                    1,
                )
                + original_main[policy_end:],
                encoding="utf-8",
            )
            errors = checker._contact_durability_errors(root)
            self.assertTrue(any("contact-policy-route" in error for error in errors))

            main.write_text(
                original_main[:policy_start]
                + policy_route[: policy_route.index('"name": "If-Match",')]
                + policy_route[policy_route.index('"name": "If-Match",') :].replace(
                    '"required": True,', '"required": False,', 1
                )
                + original_main[policy_end:],
                encoding="utf-8",
            )
            errors = checker._contact_durability_errors(root)
            self.assertTrue(any("mandatory If-Match flag" in error for error in errors))

            main.write_text(
                original_main.replace(
                    "body.model_dump_json(), conditional_fingerprint",
                    'body.model_dump_json(), "unbound-conditional"',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._contact_durability_errors(root)
            self.assertTrue(
                any("body and conditional fingerprint" in error for error in errors)
            )

            main.write_text(
                original_main.replace(
                    "if not compare_digest(supplied, current.etag)",
                    "if supplied != current.etag",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._contact_durability_errors(root)
            self.assertTrue(any("stale If-Match guard" in error for error in errors))

            main.write_text(
                original_main.replace(
                    "pg_advisory_xact_lock(hashtextextended(:lock_key, 0))",
                    "pg_advisory_xact_lock(hashtextextended(:other_key, 0))",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._contact_durability_errors(root)
            self.assertTrue(
                any("owner-namespaced advisory lock" in error for error in errors)
            )

            main.write_text(
                original_main.replace(
                    'row_conditions.append(ContactRequest.origin != "agent_outreach")',
                    'row_conditions.append(ContactRequest.origin != "profile_contact")',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._contact_durability_errors(root)
            self.assertTrue(
                any("non-Clerk outreach query exclusion" in error for error in errors)
            )

            decision_replay_start = original_main.index(
                '        if operation.startswith("POST:/v1/contact-requests/"):'
            )
            decision_replay_end = original_main.index(
                '        if (\n            record.operation == "POST:/v1/contact-requests"',
                decision_replay_start,
            )
            decision_replay = original_main[decision_replay_start:decision_replay_end]
            main.write_text(
                original_main[:decision_replay_start]
                + decision_replay.replace(
                    'record.response_body != ""',
                    'record.response_body != "non-empty"',
                    1,
                )
                + original_main[decision_replay_end:],
                encoding="utf-8",
            )
            errors = checker._contact_durability_errors(root)
            self.assertTrue(
                any("empty decision body guard" in error for error in errors)
            )

            main.write_text(
                original_main.replace(
                    '"name": "list_taxonomies"',
                    '"name": "decide_contact_request"',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._contact_durability_errors(root)
            self.assertTrue(
                any(
                    "MCP must not expose contact decision authority" in error
                    for error in errors
                )
            )

            main.write_text(original_main, encoding="utf-8")
            durability_tests = root / "apps/api/tests/test_contact_durability.py"
            durability_tests.write_text(
                durability_tests.read_text(encoding="utf-8").replace(
                    "response.status_code == nonexistent.status_code == 404",
                    "response.status_code == nonexistent.status_code == 403",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._contact_durability_errors(root)
            self.assertTrue(
                any("real opaque 404 comparison" in error for error in errors)
            )

            outreach_api = root / "apps/web/lib/outreach-api.ts"
            original_outreach_api = outreach_api.read_text(encoding="utf-8")
            outreach_api.write_text(
                original_outreach_api.replace(
                    '"If-Match": etag',
                    '"If-Match": "unbound"',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._contact_durability_errors(root)
            self.assertTrue(
                any("caller If-Match forwarding" in error for error in errors)
            )

    def test_application_transition_durability_fails_closed(self) -> None:
        files = (
            "apps/api/app/main.py",
            "apps/api/app/routes/protocol_metadata.py",
            "apps/api/tests/test_application_decision_durability.py",
            "apps/api/tests/test_application_snapshot_atomicity.py",
            "apps/api/tests/test_live_stack.py",
            "apps/api/tests/conftest.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(
                checker._application_transition_durability_errors(root), []
            )

            main = root / "apps/api/app/main.py"
            original_main = main.read_text(encoding="utf-8")
            main.write_text(
                original_main.replace(
                    '"organization_verification",',
                    '"organization_verification_public",',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._application_transition_durability_errors(root)
            self.assertTrue(
                any("verification event exclusion" in error for error in errors)
            )

            main.write_text(original_main, encoding="utf-8")
            withdrawal_start = original_main.index(
                '@app.post(\n        "/v1/applications/{application_id}/withdraw",'
            )
            withdrawal_end = original_main.index(
                "    async def withdraw_application(", withdrawal_start
            )
            withdrawal_route = original_main[withdrawal_start:withdrawal_end]
            main.write_text(
                original_main[:withdrawal_start]
                + withdrawal_route.replace(
                    '"pattern": _IDEMPOTENCY_KEY_PATTERN,',
                    '"pattern": "unbounded",',
                    1,
                )
                + original_main[withdrawal_end:],
                encoding="utf-8",
            )
            errors = checker._application_transition_durability_errors(root)
            self.assertTrue(
                any("application withdrawal-route" in error for error in errors)
            )

            decision_start = original_main.index("    async def decide_application(")
            decision_end = original_main.index(
                '\n    @app.post(\n        "/v1/connection-requests",', decision_start
            )
            decision = original_main[decision_start:decision_end]
            main.write_text(
                original_main[:decision_start]
                + decision.replace(
                    "await assert_active_employer_application_authority(",
                    "await missing_employer_application_authority(",
                    1,
                )
                + original_main[decision_end:],
                encoding="utf-8",
            )
            errors = checker._application_transition_durability_errors(root)
            self.assertTrue(
                any("live recruiting authority" in error for error in errors)
            )

            main.write_text(
                original_main[:decision_start]
                + decision.replace(
                    "if retention_expired(row.retention_expires_at):",
                    "if False and retention_expired(row.retention_expires_at):",
                    1,
                )
                + original_main[decision_end:],
                encoding="utf-8",
            )
            errors = checker._application_transition_durability_errors(root)
            self.assertTrue(
                any("fresh decision retention guard" in error for error in errors)
            )

            withdrawal_function_start = original_main.index(
                "    async def withdraw_application("
            )
            withdrawal_function_end = original_main.index(
                '\n    @app.post(\n        "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications/{application_id}/{action}",',
                withdrawal_function_start,
            )
            withdrawal = original_main[
                withdrawal_function_start:withdrawal_function_end
            ]
            main.write_text(
                original_main[:withdrawal_function_start]
                + withdrawal.replace(
                    "Application.applicant_owner_id == principal.subject",
                    "Application.applicant_owner_id == missing_subject",
                    1,
                )
                + original_main[withdrawal_function_end:],
                encoding="utf-8",
            )
            errors = checker._application_transition_durability_errors(root)
            self.assertTrue(
                any("applicant-bound application probe" in error for error in errors)
            )

            replay_start = original_main.index(
                "    async def application_transition_replay("
            )
            replay_end = original_main.index(
                "    async def idempotency_replay(", replay_start
            )
            replay = original_main[replay_start:replay_end]
            main.write_text(
                original_main[:replay_start]
                + replay.replace(
                    'record.response_body != ""',
                    'record.response_body != "non-empty"',
                    1,
                )
                + original_main[replay_end:],
                encoding="utf-8",
            )
            errors = checker._application_transition_durability_errors(root)
            self.assertTrue(
                any("empty transition body guard" in error for error in errors)
            )

            main.write_text(
                original_main.replace(
                    '"name": "list_taxonomies"',
                    '"name": "application_transition"',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._application_transition_durability_errors(root)
            self.assertTrue(
                any(
                    "mcp_tools must not expose application transition" in error
                    for error in errors
                )
            )

            main.write_text(original_main, encoding="utf-8")
            decision_tests = (
                root / "apps/api/tests/test_application_decision_durability.py"
            )
            decision_tests.write_text(
                decision_tests.read_text(encoding="utf-8").replace(
                    "assert failed_replay.status_code == 503",
                    "assert failed_replay.status_code == 500",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._application_transition_durability_errors(root)
            self.assertTrue(
                any("corruption 503 assertion" in error for error in errors)
            )

            decision_tests.write_text(
                (
                    REPO_ROOT / "apps/api/tests/test_application_decision_durability.py"
                ).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            submission_start = original_main.index("    async def submit_application(")
            submission_end = original_main.index(
                '\n    @app.get(\n        "/v1/applications",', submission_start
            )
            submission = original_main[submission_start:submission_end]
            submission_receipt_start = submission.index(
                "replay_after_commit = await commit_artifact_transaction("
            )
            submission_receipt = submission[submission_receipt_start:]
            main.write_text(
                original_main[:submission_start]
                + submission[:submission_receipt_start]
                + submission_receipt.replace(
                    'resource_type="application",',
                    'resource_type="application_transition",',
                    1,
                )
                + original_main[submission_end:],
                encoding="utf-8",
            )
            errors = checker._application_transition_durability_errors(root)
            self.assertTrue(any("submission receipt type" in error for error in errors))

            live_tests = root / "apps/api/tests/test_live_stack.py"
            live_tests.write_text(
                live_tests.read_text(encoding="utf-8").replace(
                    "assert {withdrawn.status_code, accepted.status_code} == {200, 409}",
                    "assert {withdrawn.status_code, accepted.status_code} == {200, 200}",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._application_transition_durability_errors(root)
            self.assertTrue(any("one terminal response" in error for error in errors))

    def test_human_mode_checker_is_a_bounded_domain(self) -> None:
        checker_source = CHECKER_PATH.read_text(encoding="utf-8")
        domain_path = REPO_ROOT / "tools/platform_human_mode.py"
        domain_source = domain_path.read_text(encoding="utf-8")
        shared_source = (REPO_ROOT / "tools/platform_checker_source.py").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "human_mode_surface_errors as _human_mode_surface_errors",
            checker_source,
        )
        self.assertNotIn("def _human_mode_surface_errors", checker_source)
        self.assertIn("def human_mode_surface_errors", domain_source)
        self.assertIn("from .platform_checker_source import (", domain_source)
        self.assertIn("append_error,", domain_source)
        self.assertIn("def ordered_anchor_positions", shared_source)
        self.assertEqual(
            Path(checker._human_mode_surface_errors.__code__.co_filename).resolve(),
            domain_path.resolve(),
        )

    def test_human_mode_control_anchors_fail_closed(self) -> None:
        files = (
            "apps/web/components/human-builder.tsx",
            "apps/web/components/human-buffered-fields.tsx",
            "apps/web/components/human-guided-fields.tsx",
            "apps/web/lib/guided-sections.ts",
            "apps/web/lib/human-input.ts",
            "apps/web/components/load-existing-panel.tsx",
            "apps/web/tests/guided-sections.test.ts",
            "apps/web/tests/human-builder-v2.test.ts",
            "apps/web/tests/human-input.test.ts",
            "apps/web/tests/load-existing-panel.test.ts",
            "apps/web/e2e/public-release.spec.ts",
            "apps/web/e2e/production-harness.mjs",
            "apps/web/e2e/production-runtime.mjs",
            "apps/web/package.json",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker._human_mode_surface_errors(root), [])
            builder = root / "apps/web/components/human-builder.tsx"
            builder.write_text(
                builder.read_text(encoding="utf-8").replace(
                    'GuidedEntriesEditor kind="education"',
                    'GuidedEntriesEditor kind="other"',
                    1,
                ),
                encoding="utf-8",
            )
            self.assertTrue(checker._human_mode_surface_errors(root))

            builder.write_text(
                (REPO_ROOT / "apps/web/components/human-builder.tsx")
                .read_text(encoding="utf-8")
                .replace(
                    'name="human-document-kind"',
                    'name="independent-document-kind"',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._human_mode_surface_errors(root)
            self.assertTrue(
                any("shared document-kind radio name" in error for error in errors)
            )

            builder.write_text(
                (REPO_ROOT / "apps/web/components/human-builder.tsx")
                .read_text(encoding="utf-8")
                .replace(
                    "id={`human-stage-${stage}-title`} tabIndex={-1}",
                    "id={`human-stage-${stage}-title`} tabIndex={0}",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._human_mode_surface_errors(root)
            self.assertTrue(any("stage heading target" in error for error in errors))

            builder.write_text(
                (REPO_ROOT / "apps/web/components/human-builder.tsx")
                .read_text(encoding="utf-8")
                .replace(
                    "function activateStage(stage: HumanJourneyStage) {\n    flushBufferedFields();",
                    "function activateStage(stage: HumanJourneyStage) {\n    // unsafe: pending narrative is not committed",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._human_mode_surface_errors(root)
            self.assertTrue(
                any("flush before stage change" in error for error in errors)
            )

            builder.write_text(
                (REPO_ROOT / "apps/web/components/human-builder.tsx")
                .read_text(encoding="utf-8")
                .replace(
                    "setHumanStage(stage);",
                    "setActiveStage(stage);",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._human_mode_surface_errors(root)
            self.assertTrue(any("stage state transition" in error for error in errors))

            builder.write_text(
                "import monaco from 'monaco-editor';\n"
                + (REPO_ROOT / "apps/web/components/human-builder.tsx").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            errors = checker._human_mode_surface_errors(root)
            self.assertTrue(
                any("must not import or embed Monaco" in error for error in errors)
            )

            helper = root / "apps/web/lib/human-input.ts"
            helper.write_text(
                (REPO_ROOT / "apps/web/lib/human-input.ts")
                .read_text(encoding="utf-8")
                .replace(
                    "if (pendingValue === null) return false;",
                    "if (false) return false;",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._human_mode_surface_errors(root)
            self.assertTrue(
                any("idempotent buffered flush" in error for error in errors)
            )

            buffered = root / "apps/web/components/human-buffered-fields.tsx"
            buffered.write_text(
                (REPO_ROOT / "apps/web/components/human-buffered-fields.tsx")
                .read_text(encoding="utf-8")
                .replace(
                    "onBlur={() => { const committed = commitBufferedInputValue(draftValue); setDraftValue(committed); committer.flush(); }}",
                    "onBlur={() => { const committed = commitBufferedInputValue(draftValue); setDraftValue(committed); committer.cancel(); }}",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._human_mode_surface_errors(root)
            self.assertTrue(
                any("buffered input blur flush" in error for error in errors)
            )

            helper.write_text(
                (REPO_ROOT / "apps/web/lib/human-input.ts").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            buffered.write_text(
                (REPO_ROOT / "apps/web/components/human-buffered-fields.tsx")
                .read_text(encoding="utf-8")
                .replace(
                    "useEffect(() => () => { committer.flush(); }, [committer]);",
                    "useEffect(() => () => { committer.cancel(); }, [committer]);",
                    2,
                ),
                encoding="utf-8",
            )
            errors = checker._human_mode_surface_errors(root)
            self.assertTrue(
                any("buffered input cleanup flush" in error for error in errors)
            )

            e2e = root / "apps/web/e2e/public-release.spec.ts"
            e2e.write_text(
                (REPO_ROOT / "apps/web/e2e/public-release.spec.ts")
                .read_text(encoding="utf-8")
                .replace(
                    'const editedNarrative = "Browser progression keeps this canonical narrative.";',
                    'const editedNarrative = "Browser progression no longer checks the flush.";',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._human_mode_surface_errors(root)
            self.assertTrue(
                any("buffered narrative browser fixture" in error for error in errors)
            )

            e2e.write_text(
                (REPO_ROOT / "apps/web/e2e/public-release.spec.ts")
                .read_text(encoding="utf-8")
                .replace(
                    'test("Human Mode preserves the canonical stage journey and signed-out release boundary"',
                    'test("Human Mode stage journey was renamed"',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._human_mode_surface_errors(root)
            self.assertTrue(
                any(
                    "exactly nine named production Playwright" in error
                    for error in errors
                )
            )

            e2e.write_text(
                (REPO_ROOT / "apps/web/e2e/public-release.spec.ts").read_text(
                    encoding="utf-8"
                )
                + '\ntest("unlisted extra production release test", async () => {});\n',
                encoding="utf-8",
            )
            errors = checker._human_mode_surface_errors(root)
            self.assertTrue(
                any(
                    "unexpected: unlisted extra production release test" in error
                    for error in errors
                )
            )

    def test_recruiting_evidence_controls_fail_closed(self) -> None:
        files = (
            "apps/api/app/main.py",
            "apps/api/app/routes/health.py",
            "apps/api/app/services/artifact_durability.py",
            "apps/api/app/services/recruiting_evidence.py",
            "apps/api/alembic/versions/0006_organization_verification.py",
            "apps/api/alembic/versions/0007_retention_executor.py",
            "apps/api/tests/test_artifact_durability.py",
            "apps/api/tests/test_migrations.py",
            "apps/api/tests/test_recruiting_evidence_service.py",
            "apps/api/tests/test_recruiting_verification_evidence.py",
            "apps/web/app/verification-review/page.tsx",
            "apps/web/components/verification-evidence-viewer.tsx",
            "apps/web/components/verification-review-queue.tsx",
            "apps/web/lib/recruiting-evidence-api.ts",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            routes = dict(self.route_registry["routes"])
            self.assertEqual(
                checker._recruiting_evidence_surface_errors(root, routes), []
            )

            routes.pop(
                "GET /v1/internal/recruiting-verifications/{verification_id}/evidence"
            )
            errors = checker._recruiting_evidence_surface_errors(root, routes)
            self.assertTrue(any("must map" in error for error in errors))

            routes = dict(self.route_registry["routes"])
            main = root / "apps/api/app/main.py"
            main_source = main.read_text(encoding="utf-8")
            route_marker = (
                '"/v1/internal/recruiting-verifications/{verification_id}/evidence"'
            )
            route_start = main_source.rfind(
                "@app.get(", 0, main_source.index(route_marker)
            )
            route_end = main_source.index(
                "    async def read_recruiting_verification_evidence(", route_start
            )
            decorator = main_source[route_start:route_end].replace(
                "include_in_schema=False", "include_in_schema=True", 1
            )
            main.write_text(
                main_source[:route_start] + decorator + main_source[route_end:],
                encoding="utf-8",
            )
            errors = checker._recruiting_evidence_surface_errors(root, routes)
            self.assertTrue(any("include_in_schema=False" in error for error in errors))

            main.write_text(
                (REPO_ROOT / "apps/api/app/main.py").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            service = root / "apps/api/app/services/recruiting_evidence.py"
            service.write_text(
                service.read_text(encoding="utf-8").replace(
                    "store.read_verified_bytes(", "store.read_bytes(", 1
                ),
                encoding="utf-8",
            )
            errors = checker._recruiting_evidence_surface_errors(root, routes)
            self.assertTrue(any("verified bytes" in error for error in errors))

    def test_verification_event_scrub_controls_fail_closed(self) -> None:
        files = (
            "apps/api/alembic/versions/0028_scrub_verification_change_payloads.py",
            "apps/api/tests/test_migrations.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )

            self.assertEqual(checker._verification_event_scrub_errors(root), [])
            migration = root / files[0]
            original = migration.read_text(encoding="utf-8")

            migration.write_text(
                original.replace(
                    "resource_type = 'organization_verification'",
                    "resource_type = 'change_event'",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._verification_event_scrub_errors(root)
            self.assertTrue(
                any("verification resource predicate" in error for error in errors)
            )

            migration.write_text(
                original.replace("ORDER BY sequence", "ORDER BY payload", 1),
                encoding="utf-8",
            )
            errors = checker._verification_event_scrub_errors(root)
            self.assertTrue(any("ordered cursor" in error for error in errors))

            migration.write_text(
                original.replace(
                    "AND sequence > :last_sequence",
                    "AND sequence >= :last_sequence",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._verification_event_scrub_errors(root)
            self.assertTrue(
                any("sequence cursor predicate" in error for error in errors)
            )

            migration.write_text(
                original.replace(
                    '_SANITIZED_PAYLOAD = json.dumps({"state": "submitted"}, sort_keys=True)',
                    '_SANITIZED_PAYLOAD = json.dumps({"artifact_sha256": "leaky", "state": "submitted"}, sort_keys=True)',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._verification_event_scrub_errors(root)
            self.assertTrue(
                any("state-only sanitized payload" in error for error in errors)
            )

            migration.write_text(
                original.replace(
                    "Privacy minimization is intentionally irreversible.",
                    "Privacy minimization is reversible.",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._verification_event_scrub_errors(root)
            self.assertTrue(
                any("irreversible privacy boundary" in error for error in errors)
            )

            migration.write_text(original, encoding="utf-8")
            migrations_test = root / files[1]
            test_source = migrations_test.read_text(encoding="utf-8")
            migrations_test.write_text(
                test_source.replace(
                    "test_0028_rejects_malformed_target_payload_without_rewriting_it",
                    "test_0028_malformed_target_payload_without_rewriting_it",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._verification_event_scrub_errors(root)
            self.assertTrue(any("malformed payload test" in error for error in errors))

    def test_verified_recruitment_registry_requires_0028_anchor(self) -> None:
        registry = json.loads(json.dumps(self.registry))
        verified = next(
            feature
            for feature in registry["features"]
            if feature["id"] == "verified-recruitment"
        )
        verified["implementation"]["paths"].remove(
            "apps/api/alembic/versions/0028_scrub_verification_change_payloads.py"
        )
        errors = checker._required_feature_anchor_errors(registry["features"])
        self.assertTrue(
            any(
                "verified-recruitment.implementation" in error
                and "0028_scrub_verification_change_payloads.py" in error
                for error in errors
            )
        )

    def test_contact_request_status_invariant_fails_closed(self) -> None:
        files = (
            "apps/api/app/models.py",
            "apps/api/alembic/versions/0023_contact_request_status_constraint.py",
            "apps/api/tests/test_migrations.py",
            "docs/agent-interoperability.md",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker._contact_request_status_invariant_errors(root), [])
            migration = (
                root
                / "apps/api/alembic/versions/0023_contact_request_status_constraint.py"
            )
            migration.write_text(
                migration.read_text(encoding="utf-8").replace(
                    "'blocked', 'reported'",
                    "'blocked'",
                ),
                encoding="utf-8",
            )
            errors = checker._contact_request_status_invariant_errors(root)
            self.assertTrue(any("canonical status set" in error for error in errors))

    def test_auth_return_intent_fails_closed_on_allowlist_drift(self) -> None:
        files = (
            "apps/web/lib/auth-return-intent.ts",
            "apps/web/components/profile-connect-control.tsx",
            "apps/web/components/profile-post-controls.tsx",
            "apps/web/tests/auth-return-intent.test.ts",
            "apps/web/tests/profile-connect-control.test.ts",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker._auth_return_intent_surface_errors(root), [])
            helper = root / "apps/web/lib/auth-return-intent.ts"
            helper.write_text(
                helper.read_text(encoding="utf-8").replace(
                    "const PROFILE_RETURN_PATH_PATTERN = /^\\/p\\/([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)$/u;",
                    "const PROFILE_RETURN_PATH_PATTERN = /^.*$/u;",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._auth_return_intent_surface_errors(root)
            self.assertTrue(
                any("canonical return-path allowlist" in error for error in errors)
            )

    def test_a2a_action_errors_fail_closed_on_enumeration_drift(self) -> None:
        files = (
            "apps/api/app/main.py",
            "apps/api/tests/test_protocol_core.py",
            "apps/api/tests/test_agent_identity_mandates.py",
            "docs/agent-interoperability.md",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker._a2a_action_error_surface_errors(root), [])
            main = root / "apps/api/app/main.py"
            mutated_source = main.read_text(encoding="utf-8").replace(
                "elif status_code in {403, 404}:",
                "elif status_code == 403:",
                1,
            )
            self.assertIn(
                "if exc.status_code in {403, 404}:",
                mutated_source,
            )
            main.write_text(
                mutated_source,
                encoding="utf-8",
            )
            errors = checker._a2a_action_error_surface_errors(root)
            self.assertTrue(
                any("non-enumerating rejection statuses" in error for error in errors)
            )

    def test_protected_agent_actions_fail_closed_on_protocol_authority_drift(
        self,
    ) -> None:
        files = (
            "apps/api/app/main.py",
            "apps/api/app/routes/agent_card.py",
            "apps/api/tests/test_protocol_core.py",
            "apps/api/tests/test_agent_identity_mandates.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker._protected_agent_action_protocol_errors(root), [])

            main = root / "apps/api/app/main.py"
            card_path = root / "apps/api/app/routes/agent_card.py"
            original_main = main.read_text(encoding="utf-8")
            original_card = card_path.read_text(encoding="utf-8")
            contact_start = original_main.index(
                '@app.post(\n        "/v1/contact-requests",'
            )
            outreach_start = original_main.index(
                '@app.post(\n        "/v1/agent-outreach",', contact_start
            )
            contact_route = original_main[contact_start:outreach_start]
            main.write_text(
                original_main[:contact_start]
                + contact_route.replace('"required": True,', '"required": False,', 1)
                + original_main[outreach_start:],
                encoding="utf-8",
            )
            errors = checker._protected_agent_action_protocol_errors(root)
            self.assertTrue(
                any("required Idempotency-Key flag" in error for error in errors)
            )

            card_path.write_text(
                original_card.replace(
                    '"mandate_agent_grant": {',
                    '"eligible_agent_outreach": {',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._protected_agent_action_protocol_errors(root)
            self.assertTrue(any("mandate card scheme" in error for error in errors))

            taxonomy_start = original_main.index('if action == "list_taxonomies":')
            taxonomy_end = original_main.index(
                'if action == "list_taxonomy_terms":', taxonomy_start
            )
            taxonomy_branch = original_main[taxonomy_start:taxonomy_end]
            main.write_text(
                original_main[:taxonomy_start]
                + taxonomy_branch.replace("status_code=422", "status_code=400", 1)
                + original_main[taxonomy_end:],
                encoding="utf-8",
            )
            errors = checker._protected_agent_action_protocol_errors(root)
            self.assertTrue(
                any("list_taxonomies validation status" in error for error in errors)
            )

            protocol_test = root / "apps/api/tests/test_protocol_core.py"
            protocol_test.write_text(
                protocol_test.read_text(encoding="utf-8").replace(
                    "invalid_actions = (",
                    "invalid_action_cases = (",
                    1,
                ),
                encoding="utf-8",
            )
            main.write_text(original_main, encoding="utf-8")
            card_path.write_text(original_card, encoding="utf-8")
            errors = checker._protected_agent_action_protocol_errors(root)
            self.assertTrue(
                any("valid-envelope action cases" in error for error in errors)
            )

    def test_protected_agent_action_constraints_fail_closed(self) -> None:
        for feature_id in ("agent-protocols", "agent-representation-outreach"):
            for constraint in checker.REQUIRED_FEATURE_CONSTRAINTS[feature_id]:
                invalid = json.loads(json.dumps(self.registry))
                feature = next(
                    item for item in invalid["features"] if item["id"] == feature_id
                )
                feature["authority"]["constraints"].remove(constraint)
                errors = self.check(invalid)
                self.assertTrue(
                    any("is missing required constraints" in error for error in errors)
                )

    def test_agent_identity_and_mcp_outreach_controls_fail_closed(self) -> None:
        identity_files = (
            "apps/api/app/main.py",
            "apps/api/tests/test_agent_identity_lifecycle_durability.py",
            "apps/web/tests/agent-identity-api.test.ts",
            "docs/agent-interoperability.md",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in identity_files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker._agent_identity_durability_errors(root), [])
            main = root / "apps/api/app/main.py"
            main.write_text(
                main.read_text(encoding="utf-8").replace(
                    'operation.startswith("DELETE:/v1/agent-identities/")',
                    'operation.startswith("DELETE:/v1/agent-identity/")',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._agent_identity_durability_errors(root)
            self.assertTrue(any("withdraw operation" in error for error in errors))

            main.write_text(
                (REPO_ROOT / "apps/api/app/main.py").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            lifecycle = (
                root / "apps/api/tests/test_agent_identity_lifecycle_durability.py"
            )
            lifecycle.write_text(
                lifecycle.read_text(encoding="utf-8").replace(
                    'assert directory.json()["identities"] == []',
                    'assert directory.json()["identities"] is not None',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._agent_identity_durability_errors(root)
            self.assertTrue(
                any("public directory removal" in error for error in errors)
            )

        mcp_files = (
            "apps/api/app/main.py",
            "apps/api/app/routes/discovery.py",
            "apps/api/tests/test_protocol_core.py",
            "docs/agent-interoperability.md",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in mcp_files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker._mcp_outreach_parity_errors(root), [])
            main = root / "apps/api/app/main.py"
            main.write_text(
                main.read_text(encoding="utf-8").replace(
                    'parts["grant_digest"]',
                    'parts["unsafe_grant_digest"]',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._mcp_outreach_parity_errors(root)
            self.assertTrue(any("grant digest binding" in error for error in errors))

            main.write_text(
                (REPO_ROOT / "apps/api/app/main.py").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            protocol = root / "apps/api/tests/test_protocol_core.py"
            protocol.write_text(
                protocol.read_text(encoding="utf-8").replace(
                    '"get-mandate-bound-agent-outreach-status",',
                    '"unsafe-mcp-status",',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._mcp_outreach_parity_errors(root)
            self.assertTrue(any("A2A status skill" in error for error in errors))

    def test_document_ingestion_built_image_gate_fails_closed(self) -> None:
        paths = (
            "infra/tests/converter-built-image.sh",
            ".github/workflows/ci.yml",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in paths:
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )

            self.assertEqual(checker._document_ingestion_built_image_errors(root), [])

            script = root / "infra/tests/converter-built-image.sh"
            valid_script = script.read_text(encoding="utf-8")
            script.write_text(
                valid_script.replace(
                    '[ "$network_mode" = "none" ]',
                    '[ "$network_mode" = "bridge" ]',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._document_ingestion_built_image_errors(root)
            self.assertTrue(
                any("network isolation assertion" in error for error in errors)
            )

            script.write_text(valid_script, encoding="utf-8")
            workflow = root / ".github/workflows/ci.yml"
            workflow.write_text(
                workflow.read_text(encoding="utf-8").replace(
                    "bash infra/tests/converter-built-image.sh",
                    "bash infra/tests/converter-heartbeat-only.sh",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._document_ingestion_built_image_errors(root)
            self.assertTrue(
                any("built-image CI invocation" in error for error in errors)
            )

    def test_production_container_hardening_controls_fail_closed(self) -> None:
        files = (
            "compose.yaml",
            "compose.prod.yaml",
            "infra/tests/operational-contracts.py",
            ".github/workflows/ci.yml",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker._production_container_hardening_errors(root), [])
            compose = root / "compose.yaml"
            valid_compose = compose.read_text(encoding="utf-8")
            compose.write_text(
                valid_compose.replace(
                    "postgresql+asyncpg://connectmd_migrator:",
                    "postgresql+asyncpg://connectmd_api:",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_container_hardening_errors(root)
            self.assertTrue(
                any("least-privilege migrator URL" in error for error in errors)
            )
            compose.write_text(valid_compose, encoding="utf-8")

            compose.write_text(
                valid_compose.replace(
                    "PGUSER: connectmd_backup",
                    "PGUSER: connectmd_api",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_container_hardening_errors(root)
            self.assertTrue(any("database role" in error for error in errors))
            compose.write_text(valid_compose, encoding="utf-8")

            compose.write_text(
                compose.read_text(encoding="utf-8").replace(
                    "    cap_drop:\n      - ALL",
                    "    cap_drop: []",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_container_hardening_errors(root)
            self.assertTrue(any("all capability drop" in error for error in errors))

            compose.write_text(
                (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            operational = root / "infra/tests/operational-contracts.py"
            operational.write_text(
                operational.read_text(encoding="utf-8").replace(
                    "duplicate Compose mapping keys must fail closed",
                    "duplicate Compose mapping keys are tolerated",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_container_hardening_errors(root)
            self.assertTrue(
                any("duplicate Compose key rejection" in error for error in errors)
            )

            operational.write_text(
                (REPO_ROOT / "infra/tests/operational-contracts.py").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            ci = root / ".github/workflows/ci.yml"
            ci.write_text(
                ci.read_text(encoding="utf-8")
                + "\n      - run: python ../../infra/tests/operational-contracts.py\n",
                encoding="utf-8",
            )
            errors = checker._production_container_hardening_errors(root)
            self.assertTrue(any("exactly once" in error for error in errors))

            ci.write_text(
                (REPO_ROOT / ".github/workflows/ci.yml")
                .read_text(encoding="utf-8")
                .replace("POSTGRES_USER: postgres", "POSTGRES_USER: connectmd", 1),
                encoding="utf-8",
            )
            errors = checker._production_container_hardening_errors(root)
            self.assertTrue(any("offline database owner" in error for error in errors))

    def test_outreach_inbox_read_surface_fails_closed_on_race_guard_drift(self) -> None:
        files = (
            "apps/web/lib/private-read-epoch.ts",
            "apps/web/components/outreach-inbox.tsx",
            "apps/web/tests/outreach-inbox.test.ts",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker._outreach_inbox_read_surface_errors(root), [])
            component = root / "apps/web/components/outreach-inbox.tsx"
            component.write_text(
                component.read_text(encoding="utf-8").replace(
                    'inboxLoadState !== "loaded" || !privateReadAllowsDependentWrite(inboxReadEpochRef.current) || busy',
                    'inboxLoadState !== "loaded" || busy',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._outreach_inbox_read_surface_errors(root)
            self.assertTrue(
                any("load-more refresh exclusion" in error for error in errors)
            )

    def test_lifecycle_defaults_fail_closed_across_api_ui_and_compose(self) -> None:
        files = (
            ".env.example",
            "apps/api/app/config.py",
            "apps/web/Dockerfile",
            "apps/web/lib/account-lifecycle-api.ts",
            "apps/api/tests/test_account_lifecycle.py",
            "compose.yaml",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker._lifecycle_default_errors(root), [])
            config = root / "apps/api/app/config.py"
            config.write_text(
                config.read_text(encoding="utf-8").replace(
                    "account_lifecycle_enabled: bool = False",
                    "account_lifecycle_enabled: bool = True",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._lifecycle_default_errors(root)
            self.assertTrue(any("literal default False" in error for error in errors))

    def test_lifecycle_confirmation_and_terminal_proof_anchors_fail_closed(
        self,
    ) -> None:
        paths = (
            "apps/api/app/auth.py",
            "apps/api/app/main.py",
            "apps/api/app/models.py",
            "apps/api/alembic/versions/0024_lifecycle_confirmation_idempotency.py",
            "apps/api/tests/test_account_lifecycle.py",
            "apps/api/tests/test_account_erasure.py",
            "apps/api/tests/test_auth.py",
            "apps/api/tests/test_deletion_journal.py",
            "apps/web/lib/account-lifecycle-api.ts",
            "apps/web/components/account-privacy-center.tsx",
            "apps/web/tests/account-lifecycle-api.test.ts",
            "apps/web/tests/account-privacy-center.test.ts",
            "docs/account-lifecycle.md",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in paths:
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(
                checker._account_lifecycle_confirmation_surface_errors(root), []
            )

            main = root / "apps/api/app/main.py"
            original_main = main.read_text(encoding="utf-8")
            main.write_text(
                original_main.replace(
                    "include_in_schema=settings.account_lifecycle_enabled",
                    "include_in_schema=True",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._account_lifecycle_confirmation_surface_errors(root)
            self.assertTrue(
                any(
                    "confirmation route must remain hidden" in error for error in errors
                )
            )

            main.write_text(original_main, encoding="utf-8")
            main.write_text(
                main.read_text(encoding="utf-8").replace(
                    "await verify_live_deletion_mirror(session, journal)",
                    "await unsafe_live_deletion_mirror(session, journal)",
                    3,
                ),
                encoding="utf-8",
            )
            errors = checker._account_lifecycle_confirmation_surface_errors(root)
            self.assertTrue(any("live-mirror proof" in error for error in errors))

            main.write_text(original_main, encoding="utf-8")
            auth = root / "apps/api/app/auth.py"
            auth.write_text(
                auth.read_text(encoding="utf-8").replace(
                    'credential.startswith(("cnd_", "cng_"))',
                    'credential.startswith(("cnd_", "grant_"))',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._account_lifecycle_confirmation_surface_errors(root)
            self.assertTrue(
                any("API-key and Agent-Grant denial" in error for error in errors)
            )

    def test_frontend_docker_context_anchors_fail_closed(self) -> None:
        paths = (
            "apps/web/.dockerignore",
            "infra/tests/operational-contracts.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in paths:
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker._frontend_docker_context_errors(root), [])

            dockerignore = root / "apps/web/.dockerignore"
            original = dockerignore.read_text(encoding="utf-8")
            dockerignore.write_text(
                original.replace("!.env.example\n", "", 1), encoding="utf-8"
            )
            errors = checker._frontend_docker_context_errors(root)
            self.assertTrue(
                any("explicitly retain .env.example" in error for error in errors)
            )

            dockerignore.write_text(original + "public/**\n", encoding="utf-8")
            errors = checker._frontend_docker_context_errors(root)
            self.assertTrue(
                any(
                    "must retain required frontend build input 'public'" in error
                    for error in errors
                )
            )

            dockerignore.write_text(original + "scripts/**\n", encoding="utf-8")
            errors = checker._frontend_docker_context_errors(root)
            self.assertTrue(
                any(
                    "must retain required frontend build input 'scripts'" in error
                    for error in errors
                )
            )

            dockerignore.write_text(original + "next.config.ts\n", encoding="utf-8")
            errors = checker._frontend_docker_context_errors(root)
            self.assertTrue(
                any(
                    "must retain required frontend build input 'next.config.ts'"
                    in error
                    for error in errors
                )
            )

            dockerignore.write_text(original, encoding="utf-8")
            operational = root / "infra/tests/operational-contracts.py"
            operational.write_text(
                operational.read_text(encoding="utf-8").replace(
                    "frontend_build_input not in frontend_dockerignore_rules",
                    "frontend_build_input in frontend_dockerignore_rules",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._frontend_docker_context_errors(root)
            self.assertTrue(
                any("build-input retention assertion" in error for error in errors)
            )

    def test_search_key_bootstrap_is_bounded_and_authority_free(self) -> None:
        files = (
            "compose.yaml",
            "apps/api/app/main.py",
            "apps/api/app/models.py",
            "apps/api/app/search_key_bootstrap.py",
            "apps/api/app/services/documents.py",
            "apps/api/app/services/search.py",
            "apps/api/app/services/search_projection.py",
            "apps/api/alembic/versions/0019_search_projection_outbox.py",
            "apps/api/tests/test_search_projection_worker.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker._search_projection_contract_errors(root), [])
            compose = root / "compose.yaml"
            valid_compose = compose.read_text(encoding="utf-8")
            compose.write_text(
                valid_compose.replace(
                    'profiles: ["search-bootstrap"]',
                    'profiles: ["account-lifecycle"]',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._search_projection_contract_errors(root)
            self.assertTrue(any("explicit opt-in profile" in error for error in errors))
            bootstrap_start = valid_compose.index("  search-key-bootstrap:")
            bootstrap_end = valid_compose.index(
                "  account-erasure-worker:", bootstrap_start
            )
            bootstrap = valid_compose[bootstrap_start:bootstrap_end].replace(
                "    environment:\n",
                "    environment:\n      CONNECTMD_DATABASE_URL: forbidden\n",
                1,
            )
            compose.write_text(
                valid_compose[:bootstrap_start]
                + bootstrap
                + valid_compose[bootstrap_end:],
                encoding="utf-8",
            )
            errors = checker._search_projection_contract_errors(root)
            self.assertTrue(
                any(
                    "application authority marker 'CONNECTMD_DATABASE_URL'" in error
                    for error in errors
                )
            )

    def test_taxonomy_public_search_guard_fails_closed_on_route_model_protocol_or_test_drift(
        self,
    ) -> None:
        required_routes = {
            "GET /v1/taxonomies": "public-search",
            "GET /v1/taxonomies/{taxonomy}": "public-search",
            "POST /v1/search/query": "public-search",
        }
        required_models = {
            "PublicTaxonomyProjectionState": "public-search",
            "PublicTaxonomyDocumentSnapshot": "public-search",
            "PublicTaxonomyTerm": "public-search",
            "PublicTaxonomyMembership": "public-search",
        }
        relative_paths = (
            "apps/api/app/main.py",
            "apps/api/app/services/public_search.py",
            "apps/api/app/routes/taxonomy.py",
            "apps/api/tests/test_taxonomy.py",
            "apps/api/tests/test_api.py",
            "apps/api/tests/test_protocol_core.py",
            "apps/web/lib/public-search-api.ts",
            "apps/web/lib/public-search-contract.ts",
            "apps/web/lib/taxonomy-api.ts",
            "apps/web/lib/taxonomy-search-state.ts",
            "apps/web/components/search-experience.tsx",
            "apps/web/components/taxonomy-filter-panel.tsx",
            "apps/web/tests/public-search-api.test.ts",
            "apps/web/tests/taxonomy-search-state.test.ts",
            "apps/web/tests/taxonomy-filter-panel.test.ts",
            "infra/tests/operational-contracts.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in relative_paths:
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )

            taxonomy_tests = root / "apps/api/tests/test_taxonomy.py"
            valid_taxonomy_tests = taxonomy_tests.read_text(encoding="utf-8")
            taxonomy_tests.write_text(
                valid_taxonomy_tests
                + "\nasync def test_public_taxonomy_catalog_and_terms_are_ready():\n    pass\n",
                encoding="utf-8",
            )
            api_tests = root / "apps/api/tests/test_api.py"
            api_tests.write_text(
                api_tests.read_text(encoding="utf-8")
                + "\nasync def test_post_search_query_uses_taxonomy_registry():\n    pass\n",
                encoding="utf-8",
            )
            protocol_tests = root / "apps/api/tests/test_protocol_core.py"
            protocol_tests.write_text(
                protocol_tests.read_text(encoding="utf-8")
                + "\nasync def test_mcp_and_a2a_taxonomy_list_share_public_registry():\n    pass\n"
                + "\nasync def test_mcp_and_a2a_search_share_taxonomy_registry():\n    pass\n"
                + "\nasync def test_mcp_and_a2a_search_fail_closed_when_taxonomy_is_not_ready():\n    pass\n",
                encoding="utf-8",
            )

            def errors(
                routes: dict[str, str] | None = None,
                models: dict[str, str] | None = None,
            ) -> list[str]:
                return checker._taxonomy_public_search_contract_errors(
                    self.registry["features"],
                    required_routes if routes is None else routes,
                    required_models if models is None else models,
                    root,
                )

            self.assertEqual(errors(), [])

            missing_route = dict(required_routes)
            missing_route.pop("POST /v1/search/query")
            self.assertTrue(
                any("POST /v1/search/query" in error for error in errors(missing_route))
            )

            missing_model = dict(required_models)
            missing_model.pop("PublicTaxonomyTerm")
            self.assertTrue(
                any(
                    "PublicTaxonomyTerm" in error
                    for error in errors(models=missing_model)
                )
            )

            api = root / "apps/api/app/main.py"
            valid_api = api.read_text(encoding="utf-8")
            public_search = root / "apps/api/app/services/public_search.py"
            valid_public_search = public_search.read_text(encoding="utf-8")
            public_search.write_text(
                valid_public_search.replace(
                    "await request.app.state.taxonomy.hydrate_hits(",
                    "await request.app.state.taxonomy.not_hydrate_hits(",
                    1,
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                any("taxonomy hit hydration" in error for error in errors())
            )
            public_search.write_text(valid_public_search, encoding="utf-8")
            api.write_text(
                valid_api.replace(
                    "result = await execute_public_search(",
                    "result = await nonshared_public_search(",
                    1,
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                any("shared public search delegation" in error for error in errors())
            )
            api.write_text(valid_api, encoding="utf-8")

            api.write_text(
                valid_api.replace("    app.include_router(taxonomy_router)\n", "", 1),
                encoding="utf-8",
            )
            self.assertTrue(
                any("taxonomy router inclusion" in error for error in errors())
            )
            api.write_text(valid_api, encoding="utf-8")

            protocol_tests.write_text(
                protocol_tests.read_text(encoding="utf-8").replace(
                    "test_mcp_and_a2a_search_fail_closed_when_taxonomy_is_not_ready",
                    "test_mcp_and_a2a_search_missing_non_ready_coverage",
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                any("non-ready taxonomy parity test" in error for error in errors())
            )
            protocol_tests.write_text(
                (REPO_ROOT / "apps/api/tests/test_protocol_core.py").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            taxonomy_tests.write_text(
                valid_taxonomy_tests.replace(
                    "test_taxonomy_compact_cursor_replays_maximum_legal_unicode_query_and_labels",
                    "test_taxonomy_missing_compact_cursor_replay_coverage",
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                any("worst-case taxonomy cursor" in error for error in errors())
            )

            public_search_api = root / "apps/web/lib/public-search-api.ts"
            valid_public_search_api = public_search_api.read_text(encoding="utf-8")
            public_search_api.write_text(
                valid_public_search_api.replace(
                    "if (filters.invalidTypedValues.length > 0) throw new ApiRequestError(",
                    "if (false) throw new ApiRequestError(",
                    1,
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                any("raw taxonomy value rejection" in error for error in errors())
            )

    def test_operational_transition_anchors_fail_closed(self) -> None:
        files = (
            "apps/api/app/main.py",
            "apps/api/app/routes/health.py",
            "apps/api/tests/test_readiness.py",
            "packages/platform-contract/platform-features.json",
            "infra/postgres/database-role-contract.sql",
            "infra/scripts/deploy.sh",
            "infra/scripts/health.sh",
            "infra/scripts/lib.sh",
            "infra/scripts/release-accept.sh",
            "infra/scripts/restore.sh",
            "apps/api/app/services/backup_archive.py",
            "infra/tests/operational-contracts.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker._production_operations_errors(root), [])
            api = root / "apps/api/app/main.py"
            valid_api = api.read_text(encoding="utf-8")
            api.write_text(
                valid_api.replace(
                    "from app.routes.health import router as health_router",
                    "from app.routes.missing_health import router as health_router",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(any("health router import" in error for error in errors))
            api.write_text(valid_api, encoding="utf-8")
            api.write_text(
                valid_api.replace("    app.include_router(health_router)\n", "", 1),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(any("health router inclusion" in error for error in errors))
            api.write_text(valid_api, encoding="utf-8")
            health = root / "apps/api/app/routes/health.py"
            valid_health = health.read_text(encoding="utf-8")
            health.write_text(
                valid_health.replace(
                    '"search": "unavailable"', '"search": "unknown"', 1
                ),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any("configured search failure state" in error for error in errors)
            )
            health.write_text(valid_health, encoding="utf-8")
            release_accept = root / "infra/scripts/release-accept.sh"
            valid_release_accept = release_accept.read_text(encoding="utf-8")
            required_acceptance_services = (
                "postgres",
                "meilisearch",
                "converter",
                "search-projection-worker",
                "api",
                "frontend",
                "nginx",
            )
            acceptance_runtime_loop = (
                "for service in " + " ".join(required_acceptance_services) + "; do\n"
                '  wait_for_service "$service" '
                '"$acceptance_service_health_attempts"\n'
                "done"
            )
            self.assertIn(acceptance_runtime_loop, valid_release_accept)
            for required_service in required_acceptance_services:
                weakened_loop = acceptance_runtime_loop.replace(
                    f" {required_service}", "", 1
                )
                release_accept.write_text(
                    valid_release_accept.replace(
                        acceptance_runtime_loop, weakened_loop, 1
                    ),
                    encoding="utf-8",
                )
                errors = checker._production_operations_errors(root)
                self.assertTrue(
                    any("ordinary runtime health" in error for error in errors),
                    required_service,
                )
            lifecycle_gate = (
                'if [ "${lifecycle_enabled:-false}" = "true" ]; then\n'
                "  wait_for_profiled_service account-lifecycle "
                "account-erasure-worker "
                '"$acceptance_lifecycle_health_attempts"\n'
                "fi"
            )
            self.assertIn(lifecycle_gate, valid_release_accept)
            release_accept.write_text(
                valid_release_accept.replace(lifecycle_gate, "", 1),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any("enabled lifecycle health" in error for error in errors)
            )
            runtime_health_start = valid_release_accept.index(
                "readonly acceptance_service_health_attempts=30"
            )
            runtime_health_end = valid_release_accept.index(
                "\n# A retry after", runtime_health_start
            )
            runtime_health_block = valid_release_accept[
                runtime_health_start:runtime_health_end
            ]
            post_promotion_acceptance = (
                valid_release_accept[:runtime_health_start]
                + valid_release_accept[runtime_health_end:]
                + "\n"
                + runtime_health_block
                + "\n"
            )
            release_accept.write_text(post_promotion_acceptance, encoding="utf-8")
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any("fail-closed control order" in error for error in errors)
            )
            release_accept.write_text(valid_release_accept, encoding="utf-8")
            role_contract = root / "infra/postgres/database-role-contract.sql"
            valid_role_contract = role_contract.read_text(encoding="utf-8")
            role_contract.write_text(
                valid_role_contract.replace(
                    "current_database(), session_user",
                    "current_database(), connectmd_migrator",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any("operator database-owner identity" in error for error in errors)
            )
            role_contract.write_text(valid_role_contract, encoding="utf-8")

            role_contract.write_text(
                valid_role_contract.replace(
                    "has_database_privilege('connectmd_migrator',current_database(),'TEMPORARY')",
                    "has_database_privilege('connectmd_migrator',current_database(),'CONNECT')",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any("migrator database TEMPORARY denial" in error for error in errors)
                or any("must deny both migrator database" in error for error in errors)
            )
            role_contract.write_text(valid_role_contract, encoding="utf-8")

            role_contract.write_text(
                valid_role_contract.replace(
                    "public tables/sequences must be migrator-owned",
                    "public tables/sequences may be operator-owned",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any(
                    "migrator table and sequence ownership" in error for error in errors
                )
            )
            role_contract.write_text(valid_role_contract, encoding="utf-8")

            role_contract.write_text(
                valid_role_contract
                + "\nALTER DATABASE connectmd OWNER TO connectmd_migrator;\n",
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any("must not assign database ownership" in error for error in errors)
            )
            role_contract.write_text(valid_role_contract, encoding="utf-8")

            deploy = root / "infra/scripts/deploy.sh"
            valid_deploy = deploy.read_text(encoding="utf-8")
            deploy.write_text(
                valid_deploy.replace(
                    "compose --profile database-operations run --rm --no-deps -T db-migrate alembic upgrade head",
                    "compose --profile database-operations run --rm --no-deps -T db-migrate alembic downgrade base",
                    1,
                ),
                encoding="utf-8",
            )
            self.assertTrue(checker._production_operations_errors(root))
            staged_release = (
                'write_staged_release "$source_revision" "$image_tag" '
                '"$api_image_id" "$web_image_id" "$nginx_image_id" >/dev/null\n'
            )
            start = "compose up -d --no-build converter search-projection-worker api frontend nginx\n"
            self.assertIn(staged_release, valid_deploy)
            self.assertIn(start, valid_deploy)
            unsafe_deploy = valid_deploy.replace(staged_release, "", 1).replace(
                start, staged_release + start, 1
            )
            self.assertNotEqual(unsafe_deploy, valid_deploy)
            deploy.write_text(unsafe_deploy, encoding="utf-8")
            self.assertTrue(checker._production_operations_errors(root))
            deploy.write_text(valid_deploy, encoding="utf-8")
            missing_role_bootstrap = valid_deploy.replace(
                "bootstrap_database_roles\ncompose --profile database-operations run --rm --no-deps -T db-migrate",
                "compose --profile database-operations run --rm --no-deps -T db-migrate",
                1,
            )
            self.assertNotEqual(missing_role_bootstrap, valid_deploy)
            deploy.write_text(missing_role_bootstrap, encoding="utf-8")
            errors = checker._production_operations_errors(root)
            self.assertTrue(any("database role bootstrap" in error for error in errors))
            deploy.write_text(valid_deploy, encoding="utf-8")

            missing_role_reconcile = valid_deploy.replace(
                "reconcile_database_roles\n",
                "",
                1,
            )
            self.assertNotEqual(missing_role_reconcile, valid_deploy)
            deploy.write_text(missing_role_reconcile, encoding="utf-8")
            errors = checker._production_operations_errors(root)
            self.assertTrue(any("database role reconcile" in error for error in errors))
            deploy.write_text(valid_deploy, encoding="utf-8")

            restore = root / "infra/scripts/restore.sh"
            valid_restore = restore.read_text(encoding="utf-8")
            missing_restore_privileges = valid_restore.replace(
                "--no-owner --no-privileges",
                "--no-owner",
                1,
            )
            self.assertNotEqual(missing_restore_privileges, valid_restore)
            restore.write_text(missing_restore_privileges, encoding="utf-8")
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any("migrator database restore" in error for error in errors)
            )
            restore.write_text(valid_restore, encoding="utf-8")

            legacy_restore_helper = valid_restore.replace(
                "bootstrap_database_roles\nattest_restore_migrator_role",
                'ensure_search_projection_cluster_role "$db_user"\nattest_restore_migrator_role',
                1,
            )
            self.assertNotEqual(legacy_restore_helper, valid_restore)
            restore.write_text(legacy_restore_helper, encoding="utf-8")
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any("legacy cluster-role helper" in error for error in errors)
            )
            restore.write_text(valid_restore, encoding="utf-8")

            missing_restore_attestation = valid_restore.replace(
                "attest_restore_migrator_role\n",
                "",
                1,
            )
            self.assertNotEqual(missing_restore_attestation, valid_restore)
            restore.write_text(missing_restore_attestation, encoding="utf-8")
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any(
                    "restore migrator pre-work attestation" in error for error in errors
                )
            )
            restore.write_text(valid_restore, encoding="utf-8")

            restore.write_text(
                valid_restore.replace(
                    "Destructive restore requires an existing durable registration receipt",
                    "Destructive restore receipt is unavailable",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any("pre-existing receipt requirement" in error for error in errors)
            )
            restore.write_text(valid_restore, encoding="utf-8")
            release_preflight = (
                'assert_release_images_match "$backup_image_tag" "$backup_api_image_id" '
                '"$backup_web_image_id" "$backup_nginx_image_id"'
            )
            api_probe = (
                "docker run --rm --network none --entrypoint python "
                '"connectmd-api:$backup_image_tag"'
            )
            restore.write_text(
                valid_restore.replace(release_preflight, "", 1), encoding="utf-8"
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any(
                    "source, release, acceptance, and image identities" in error
                    for error in errors
                )
            )
            unsafe_restore = valid_restore.replace(api_probe, "", 1).replace(
                release_preflight,
                f"{api_probe}\n{release_preflight}",
                1,
            )
            self.assertNotEqual(unsafe_restore, valid_restore)
            restore.write_text(unsafe_restore, encoding="utf-8")
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any(
                    "source, release, acceptance, and image identities" in error
                    for error in errors
                )
            )
            restore.write_text(valid_restore, encoding="utf-8")
            source_marker = (
                '[ "$(current_source_revision)" = "$backup_source_revision" ]'
            )
            archive_invocation = (
                "-m app.services.backup_archive /restore/markdown-storage.tar.gz"
            )
            unsafe_archive_order = valid_restore.replace(
                archive_invocation,
                "",
                1,
            ).replace(
                source_marker,
                f"{archive_invocation}\n{source_marker}",
                1,
            )
            restore.write_text(unsafe_archive_order, encoding="utf-8")
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any(
                    "source, release, acceptance, and image identities" in error
                    for error in errors
                )
            )
            restore.write_text(valid_restore, encoding="utf-8")
            archive_invocation = (
                "-m app.services.backup_archive /restore/markdown-storage.tar.gz"
            )
            verify_only_marker = 'if [ "$mode" = "--verify-only" ]; then'
            unsafe_verify_order = valid_restore.replace(
                verify_only_marker,
                "",
                1,
            ).replace(
                archive_invocation,
                f"{verify_only_marker}\n{archive_invocation}",
                1,
            )
            restore.write_text(unsafe_verify_order, encoding="utf-8")
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any(
                    "source, release, acceptance, and image identities" in error
                    for error in errors
                )
            )
            restore.write_text(valid_restore, encoding="utf-8")
            restore.write_text(
                valid_restore.replace(
                    archive_invocation,
                    "tar -tzf /restore/markdown-storage.tar.gz",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any("archive member preflight" in error for error in errors)
            )
            restore.write_text(valid_restore, encoding="utf-8")
            restore.write_text(
                valid_restore.replace(
                    "--network none --read-only",
                    "--network host --read-only",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any("no-network read-only validator" in error for error in errors)
            )
            restore.write_text(valid_restore, encoding="utf-8")
            exact_archive_mount = '-v "$directory/markdown-storage.tar.gz:/restore/markdown-storage.tar.gz:ro"'
            restore.write_text(
                valid_restore.replace(
                    exact_archive_mount,
                    '-v "$directory:/restore:ro"',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(any("read-only archive mount" in error for error in errors))
            restore.write_text(valid_restore, encoding="utf-8")
            acceptance_digest = (
                '[ "$(digest_of_file "$backup_acceptance_receipt")" = '
                '"$backup_acceptance_receipt_digest" ]'
            )
            restore.write_text(
                valid_restore.replace(
                    acceptance_digest,
                    '[ "$(digest_of_file "$backup_acceptance_receipt_missing")" = '
                    '"$backup_acceptance_receipt_digest" ]',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any("acceptance receipt digest preflight" in error for error in errors)
            )
            restore.write_text(valid_restore, encoding="utf-8")
            archive = root / "apps/api/app/services/backup_archive.py"
            valid_archive = archive.read_text(encoding="utf-8")
            archive.write_text(
                valid_archive.replace(
                    "if archive_path.is_symlink() or not archive_path.is_file():",
                    "if archive_path.is_file():",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any("archive symlink rejection" in error for error in errors)
            )
            archive.write_text(valid_archive, encoding="utf-8")
            archive.write_text(
                valid_archive.replace(
                    "if archive_path.stat().st_size > max_archive_bytes:",
                    "if False:",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(any("compressed-size bound" in error for error in errors))
            archive.write_text(valid_archive, encoding="utf-8")
            archive.write_text(
                valid_archive.replace(
                    "if not (member.isdir() or member.isreg()):",
                    "if False:",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any(
                    "regular-file/directory-only member check" in error
                    for error in errors
                )
            )
            archive.write_text(valid_archive, encoding="utf-8")
            archive.write_text(
                valid_archive.replace(
                    'raise BackupArchiveError("duplicate_member")',
                    'raise BackupArchiveError("archive_duplicate")',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any("duplicate-name rejection" in error for error in errors)
            )
            archive.write_text(valid_archive, encoding="utf-8")
            archive.write_text(
                valid_archive.replace(
                    "if canonical in members:",
                    "if False:",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any("duplicate normalized-name check" in error for error in errors)
            )
            archive.write_text(valid_archive, encoding="utf-8")
            archive.write_text(
                valid_archive.replace(
                    'if member.isreg() and any(name.startswith(f"{canonical}/") for name in members):',
                    "if False:",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any("file-ancestor conflict rejection" in error for error in errors)
            )
            archive.write_text(valid_archive, encoding="utf-8")
            archive.write_text(
                valid_archive.replace(
                    "if members.get(parent_name) is False:",
                    "if False:",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any(
                    "directory-ancestor conflict rejection" in error for error in errors
                )
            )
            archive.write_text(valid_archive, encoding="utf-8")
            health = root / "apps/api/app/routes/health.py"
            valid_health = health.read_text(encoding="utf-8")
            health.write_text(
                valid_health.replace("if not await search.health():", "if False:", 1),
                encoding="utf-8",
            )
            errors = checker._production_operations_errors(root)
            self.assertTrue(
                any("configured search health gate" in error for error in errors)
            )
            health.write_text(valid_health, encoding="utf-8")

    def test_new_control_paths_are_required_feature_anchors(self) -> None:
        self.assertEqual(
            checker._required_feature_anchor_errors(self.registry["features"]), []
        )
        for required_path in (
            "apps/api/app/http/origin.py",
            "apps/api/app/routes/discovery.py",
        ):
            with self.subTest(agent_protocol_path=required_path):
                invalid = json.loads(json.dumps(self.registry))
                agent_protocols = next(
                    feature
                    for feature in invalid["features"]
                    if feature["id"] == "agent-protocols"
                )
                agent_protocols["implementation"]["paths"].remove(required_path)
                errors = checker._required_feature_anchor_errors(invalid["features"])
                self.assertTrue(any(required_path in error for error in errors))

        invalid = json.loads(json.dumps(self.registry))
        canonical = next(
            feature
            for feature in invalid["features"]
            if feature["id"] == "canonical-documents"
        )
        canonical["tests"].remove("apps/web/tests/guided-sections.test.ts")
        errors = checker._required_feature_anchor_errors(invalid["features"])
        self.assertTrue(any("guided-sections.test.ts" in error for error in errors))

        invalid = json.loads(json.dumps(self.registry))
        canonical = next(
            feature
            for feature in invalid["features"]
            if feature["id"] == "canonical-documents"
        )
        canonical["implementation"]["paths"].remove("apps/web/lib/human-input.ts")
        errors = checker._required_feature_anchor_errors(invalid["features"])
        self.assertTrue(any("human-input.ts" in error for error in errors))

        invalid = json.loads(json.dumps(self.registry))
        agent_authority = next(
            feature
            for feature in invalid["features"]
            if feature["id"] == "agent-authority"
        )
        agent_authority["implementation"]["paths"].remove(
            "apps/web/lib/logical-mutation.ts"
        )
        errors = checker._required_feature_anchor_errors(invalid["features"])
        self.assertTrue(any("logical-mutation.ts" in error for error in errors))

        invalid = json.loads(json.dumps(self.registry))
        agent_authority = next(
            feature
            for feature in invalid["features"]
            if feature["id"] == "agent-authority"
        )
        agent_authority["implementation"]["paths"].remove("apps/web/lib/agent-api.ts")
        errors = checker._required_feature_anchor_errors(invalid["features"])
        self.assertTrue(any("agent-api.ts" in error for error in errors))

        invalid = json.loads(json.dumps(self.registry))
        representation = next(
            feature
            for feature in invalid["features"]
            if feature["id"] == "agent-representation-outreach"
        )
        representation["tests"].remove("apps/web/tests/sitemap.test.ts")
        errors = checker._required_feature_anchor_errors(invalid["features"])
        self.assertTrue(any("sitemap.test.ts" in error for error in errors))

        invalid = json.loads(json.dumps(self.registry))
        operations = next(
            feature
            for feature in invalid["features"]
            if feature["id"] == "production-operations"
        )
        operations["tests"].remove("infra/tests/recovery-roundtrip.sh")
        errors = checker._required_feature_anchor_errors(invalid["features"])
        self.assertTrue(any("recovery-roundtrip.sh" in error for error in errors))

        invalid = json.loads(json.dumps(self.registry))
        lifecycle = next(
            feature
            for feature in invalid["features"]
            if feature["id"] == "account-lifecycle"
        )
        lifecycle["implementation"]["paths"].remove(
            "apps/api/alembic/versions/0024_lifecycle_confirmation_idempotency.py"
        )
        errors = checker._required_feature_anchor_errors(invalid["features"])
        self.assertTrue(
            any(
                "0024_lifecycle_confirmation_idempotency.py" in error
                for error in errors
            )
        )

        invalid = json.loads(json.dumps(self.registry))
        operations = next(
            feature
            for feature in invalid["features"]
            if feature["id"] == "production-operations"
        )
        operations["implementation"]["paths"].remove("apps/web/.dockerignore")
        errors = checker._required_feature_anchor_errors(invalid["features"])
        self.assertTrue(any("apps/web/.dockerignore" in error for error in errors))

    def test_agent_web_helpers_remain_extracted_into_agent_api(self) -> None:
        relative_paths = (
            "apps/web/lib/agent-api.ts",
            "apps/web/components/agent-delegation-manager.tsx",
            "apps/web/components/agent-delegation-panels.tsx",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in relative_paths:
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )

            self.assertEqual(checker._agent_web_helper_extraction_errors(root), [])

            agent = root / "apps/web/lib/agent-api.ts"
            valid_agent = agent.read_text(encoding="utf-8")
            agent.write_text(
                valid_agent.replace(
                    "export async function listDelegationAudit(",
                    "export async function listDelegationAudit_removed(",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._agent_web_helper_extraction_errors(root)
            self.assertTrue(any("listDelegationAudit" in error for error in errors))
            agent.write_text(valid_agent, encoding="utf-8")

            panel = root / "apps/web/components/agent-delegation-panels.tsx"
            valid_panel = panel.read_text(encoding="utf-8")
            panel.write_text(
                valid_panel.replace(
                    "export function AgentGrantInventoryPanel(",
                    "export function AgentGrantInventoryPanel_removed(",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._agent_web_helper_extraction_errors(root)
            self.assertTrue(
                any("AgentGrantInventoryPanel" in error for error in errors)
            )
            panel.write_text(valid_panel, encoding="utf-8")

            manager = root / "apps/web/components/agent-delegation-manager.tsx"
            valid_manager = manager.read_text(encoding="utf-8")
            manager.write_text(
                valid_manager.replace(
                    'from "@/components/agent-delegation-panels"',
                    'from "@/components/agent-delegation-panels-removed"',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._agent_web_helper_extraction_errors(root)
            self.assertTrue(
                any(
                    'from "@/components/agent-delegation-panels"' in error
                    for error in errors
                )
            )
            manager.write_text(
                valid_manager.replace(
                    'from "@/lib/agent-api"',
                    'from "@/lib/agent-api-removed"',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._agent_web_helper_extraction_errors(root)
            self.assertTrue(any('from "@/lib/agent-api"' in error for error in errors))
            manager.write_text(
                valid_manager + "\n// function CopyGrantHandoff(\n",
                encoding="utf-8",
            )
            errors = checker._agent_web_helper_extraction_errors(root)
            self.assertTrue(any("must not retain" in error for error in errors))

    def test_agent_authority_registry_requires_extracted_panel_evidence(self) -> None:
        invalid = json.loads(json.dumps(self.registry))
        feature = next(
            item for item in invalid["features"] if item["id"] == "agent-authority"
        )
        feature["implementation"]["paths"].remove(
            "apps/web/components/agent-delegation-panels.tsx"
        )
        errors = checker._required_feature_anchor_errors(invalid["features"])
        self.assertTrue(any("agent-delegation-panels.tsx" in error for error in errors))

        invalid = json.loads(json.dumps(self.registry))
        feature = next(
            item for item in invalid["features"] if item["id"] == "agent-authority"
        )
        feature["tests"].remove("apps/web/tests/agent-integration-panel.test.ts")
        errors = checker._required_feature_anchor_errors(invalid["features"])
        self.assertTrue(
            any("agent-integration-panel.test.ts" in error for error in errors)
        )

    def test_public_html_mirror_controls_fail_closed_on_semantic_drift(self) -> None:
        self.assertEqual(checker._public_html_mirror_surface_errors(REPO_ROOT), [])
        paths = (
            "apps/web/lib/public-document.ts",
            "apps/web/components/public-document-page.tsx",
            "apps/web/app/p/[handle]/page.tsx",
            "apps/web/app/r/[slug]/page.tsx",
            "apps/web/app/sitemap.ts",
            "apps/web/app/robots.ts",
            "apps/web/tests/public-projections.test.ts",
            "apps/web/tests/sitemap.test.ts",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in paths:
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            projection = root / "apps/web/lib/public-document.ts"
            projection.write_text(
                projection.read_text(encoding="utf-8").replace(
                    'return document.kind === "profile" ? profilePageJsonLd(document, canonicalUrl) : resumeJsonLd(document, canonicalUrl);',
                    "return profilePageJsonLd(document, canonicalUrl);",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._public_html_mirror_surface_errors(root)
            self.assertTrue(
                any(
                    "profile versus resume structured-data branch" in error
                    for error in errors
                )
            )

            projection.write_text(
                (REPO_ROOT / "apps/web/lib/public-document.ts").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            projection.write_text(
                projection.read_text(encoding="utf-8").replace(
                    "const name = boundedMetadataText(stringValue(attributes.name), 160);\n  if (!name) return null;\n  const description = boundedMetadataText(view.fields.headline, 280);",
                    "const name = boundedMetadataText(stringValue(attributes.name), 160);\n  if (!name) return profilePageJsonLd(document, canonicalUrl);\n  const description = boundedMetadataText(view.fields.headline, 280);",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._public_html_mirror_surface_errors(root)
            self.assertTrue(
                any(
                    "resume omission without explicit name" in error for error in errors
                )
            )

            projection.write_text(
                (REPO_ROOT / "apps/web/lib/public-document.ts").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            projections_test = root / "apps/web/tests/public-projections.test.ts"
            projections_test.write_text(
                projections_test.read_text(encoding="utf-8").replace(
                    'owner_id: "private-owner"',
                    'owner_id: "public-owner"',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._public_html_mirror_surface_errors(root)
            self.assertTrue(
                any("resume private inference rejection" in error for error in errors)
            )

            projections_test.write_text(
                (REPO_ROOT / "apps/web/tests/public-projections.test.ts").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            sitemap = root / "apps/web/app/sitemap.ts"
            sitemap.write_text(
                sitemap.read_text(encoding="utf-8").replace(
                    'absoluteSiteUrl("/trust")',
                    'absoluteSiteUrl("/discover")',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._public_html_mirror_surface_errors(root)
            self.assertTrue(any("trust route" in error for error in errors))

            sitemap.write_text(
                (REPO_ROOT / "apps/web/app/sitemap.ts").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            sitemap.write_text(
                sitemap.read_text(encoding="utf-8").replace(
                    "MetadataRoute.Sitemap",
                    "MetadataRoute.Robots",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._public_html_mirror_surface_errors(root)
            self.assertTrue(
                any("static metadata route" in error for error in errors)
            )

    def test_logical_mutation_controls_fail_closed_on_subject_or_retry_drift(
        self,
    ) -> None:
        self.assertEqual(checker._logical_mutation_surface_errors(REPO_ROOT), [])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in (
                "apps/web/lib/logical-mutation.ts",
                "apps/web/tests/logical-mutation.test.ts",
            ):
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            helper = root / "apps/web/lib/logical-mutation.ts"
            helper.write_text(
                helper.read_text(encoding="utf-8").replace(
                    "fingerprintMutationIntent({ subject, intent })",
                    "fingerprintMutationIntent(intent)",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._logical_mutation_surface_errors(root)
            self.assertTrue(
                any("subject-scoped intent fingerprint" in error for error in errors)
            )

            helper.write_text(
                (REPO_ROOT / "apps/web/lib/logical-mutation.ts").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            tests = root / "apps/web/tests/logical-mutation.test.ts"
            tests.write_text(
                tests.read_text(encoding="utf-8").replace(
                    'new ApiRequestError("offline", undefined, "offline")',
                    'new ApiRequestError("offline", undefined, "request")',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._logical_mutation_surface_errors(root)
            self.assertTrue(any("offline non-retention" in error for error in errors))

    def test_private_conversation_coordinator_fails_closed_on_scope_or_recovery_drift(
        self,
    ) -> None:
        self.assertEqual(checker._private_conversation_surface_errors(REPO_ROOT), [])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in (
                "apps/web/components/conversation-thread.tsx",
                "apps/web/tests/conversation-thread.test.ts",
            ):
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            component = root / "apps/web/components/conversation-thread.tsx"
            original_component = component.read_text(encoding="utf-8")
            test_source = root / "apps/web/tests/conversation-thread.test.ts"
            original_test_source = test_source.read_text(encoding="utf-8")
            component.write_text(
                original_component.replace(
                    "key={`${subject}:${conversationId}`}",
                    "key={subject}",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._private_conversation_surface_errors(root)
            self.assertTrue(
                any("subject and conversation remount" in error for error in errors)
            )

            component.write_text(
                original_component.replace(
                    'loadState === "loaded" && messages.length === 0',
                    "messages.length === 0",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._private_conversation_surface_errors(root)
            self.assertTrue(any("successful empty branch" in error for error in errors))

            component.write_text(
                original_component.replace(
                    "  coordinator.generation += 1;\n  coordinator.primaryClaimId = null;",
                    "  coordinator.primaryClaimId = null;",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._private_conversation_surface_errors(root)
            self.assertTrue(
                any("scope reset generation invalidation" in error for error in errors)
            )

            primary_start = original_component.index('if (kind === "primary") {')
            primary_end = original_component.index("  } else if (", primary_start)
            primary_claim = original_component[primary_start:primary_end]
            component.write_text(
                original_component[:primary_start]
                + primary_claim.replace(
                    "coordinator.generation += 1;",
                    "coordinator.generation += 0;",
                    1,
                )
                + original_component[primary_end:],
                encoding="utf-8",
            )
            errors = checker._private_conversation_surface_errors(root)
            self.assertTrue(
                any("primary generation invalidation" in error for error in errors)
            )

            component.write_text(
                original_component.replace(
                    "coordinator.interactionClaimId !== null",
                    "false",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._private_conversation_surface_errors(root)
            self.assertTrue(
                any(
                    "synchronous cursor/send mutual exclusion" in error
                    for error in errors
                )
            )

            component.write_text(
                original_component.replace(
                    "coordinator.primaryClaimId === claim.id",
                    "coordinator.primaryClaimId !== null",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._private_conversation_surface_errors(root)
            self.assertTrue(
                any("primary owner-only release" in error for error in errors)
            )

            component.write_text(
                original_component.replace(
                    'if (cursor && loadStateRef.current !== "loaded") return;',
                    "if (cursor) return;",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._private_conversation_surface_errors(root)
            self.assertTrue(
                any(
                    "cursor gate after failed primary read" in error for error in errors
                )
            )

            test_source.write_text(
                original_test_source.replace(
                    "does not append a pagination response after a newer refresh wins",
                    "refresh scenario",
                    1,
                ),
                encoding="utf-8",
            )
            component.write_text(original_component, encoding="utf-8")
            errors = checker._private_conversation_surface_errors(root)
            self.assertTrue(
                any(
                    "deferred refresh-versus-pagination test" in error
                    for error in errors
                )
            )

            component.write_text(
                original_component.replace(
                    'disabled={busy || loadState !== "loaded"}',
                    "disabled={busy}",
                    1,
                ),
                encoding="utf-8",
            )
            test_source.write_text(original_test_source, encoding="utf-8")
            errors = checker._private_conversation_surface_errors(root)
            self.assertTrue(
                any(
                    "must gate both private-message composing" in error
                    for error in errors
                )
            )

            component.write_text(original_component, encoding="utf-8")
            test_source.write_text(
                original_test_source.replace(
                    "expect(source).not.toMatch(/localStorage|sessionStorage|console\\.|URLSearchParams/u);",
                    "expect(source).not.toMatch(/localStorage|sessionStorage|URLSearchParams/u);",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._private_conversation_surface_errors(root)
            self.assertTrue(
                any(
                    "private-content non-persistence/logging assertion" in error
                    for error in errors
                )
            )

    def test_private_network_reads_fail_closed_on_subject_or_slice_guard_drift(
        self,
    ) -> None:
        self.assertEqual(checker._private_network_read_surface_errors(REPO_ROOT), [])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in (
                "apps/web/lib/private-read-epoch.ts",
                "apps/web/lib/social-api.ts",
                "apps/web/components/network-hub.tsx",
                "apps/web/components/private-network-reads.ts",
                "apps/web/components/network-panels.tsx",
                "apps/web/tests/social-api.test.ts",
                "apps/web/tests/network-hub.test.ts",
            ):
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            social_api = root / "apps/web/lib/social-api.ts"
            social_api.write_text(
                social_api.read_text(encoding="utf-8").replace(
                    "listConnectionsForSubject(",
                    "listConnectionsWithoutSubjectGuard(",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._private_network_read_surface_errors(root)
            self.assertTrue(
                any("subject-bound connections reader" in error for error in errors)
            )

            private_reads = root / "apps/web/components/private-network-reads.ts"
            private_reads.write_text(
                private_reads.read_text(encoding="utf-8").replace(
                    'initialLoadInFlightRef.current.has("notifications")',
                    "false",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._private_network_read_surface_errors(root)
            self.assertTrue(
                any("notifications initial guard" in error for error in errors)
            )

            def restore(relative_path: str) -> Path:
                target = root / relative_path
                target.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                return target

            private_reads = restore("apps/web/components/private-network-reads.ts")
            private_reads.write_text(
                private_reads.read_text(encoding="utf-8").replace(
                    '} from "@/lib/private-read-epoch";',
                    '} from "@/lib/other-private-read-epoch";',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._private_network_read_surface_errors(root)
            self.assertTrue(
                any("shared private-read epoch import" in error for error in errors)
            )

            private_reads = restore("apps/web/components/private-network-reads.ts")
            epoch = root / "apps/web/lib/private-read-epoch.ts"
            epoch.write_text(
                epoch.read_text(encoding="utf-8").replace(
                    "return state.ready && !state.inFlight;",
                    "return !state.inFlight;",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._private_network_read_surface_errors(root)
            self.assertTrue(
                any(
                    "ready-and-settled dependent-action gate" in error
                    for error in errors
                )
            )

            private_reads = restore("apps/web/components/private-network-reads.ts")
            private_reads.write_text(
                private_reads.read_text(encoding="utf-8").replace(
                    "export function privateNetworkReadAllowsDependentAction(",
                    "function privateNetworkReadAllowsDependentAction(",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._private_network_read_surface_errors(root)
            self.assertTrue(
                any(
                    "retained private-network dependent-action adapter" in error
                    for error in errors
                )
            )

            private_reads = restore("apps/web/components/private-network-reads.ts")
            private_reads.write_text(
                private_reads.read_text(encoding="utf-8").replace(
                    "if (!current(requestSubject)) return;",
                    "if (false) return;",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._private_network_read_surface_errors(root)
            self.assertTrue(
                any("requests pre-dispatch subject guard" in error for error in errors)
            )

            private_reads = restore("apps/web/components/private-network-reads.ts")
            private_reads.write_text(
                private_reads.read_text(encoding="utf-8").replace(
                    'moreInFlightRef.current.has("notifications")',
                    "false",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._private_network_read_surface_errors(root)
            self.assertTrue(
                any(
                    "notifications pagination in-flight guard" in error
                    for error in errors
                )
            )

            network_panels = restore("apps/web/components/network-panels.tsx")
            network_panels.write_text(
                network_panels.read_text(encoding="utf-8").replace(
                    'label="Notifications could not be refreshed"',
                    'label="Notifications are unavailable"',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._private_network_read_surface_errors(root)
            self.assertTrue(
                any("notification retained-data error" in error for error in errors)
            )

            network_test = restore("apps/web/tests/network-hub.test.ts")
            network_test.write_text(
                network_test.read_text(encoding="utf-8").replace(
                    "keeps retained rows visible but blocks dependent actions after a current refresh fails",
                    "keeps retained rows visible",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._private_network_read_surface_errors(root)
            self.assertTrue(
                any("retained-data write-exclusion test" in error for error in errors)
            )

    def test_private_network_registry_constraints_fail_closed(self) -> None:
        invalid = json.loads(json.dumps(self.registry))
        feature = next(
            item for item in invalid["features"] if item["id"] == "private-social-graph"
        )
        required = checker.REQUIRED_FEATURE_CONSTRAINTS["private-social-graph"]
        feature["authority"]["constraints"] = [
            constraint
            for constraint in feature["authority"]["constraints"]
            if constraint not in required
        ]
        errors = self.check(invalid)
        for constraint in required:
            with self.subTest(constraint=constraint):
                self.assertTrue(
                    any(
                        "is missing required constraints" in error
                        and constraint in error
                        for error in errors
                    )
                )

    def test_private_idempotency_surfaces_fail_closed_on_receipt_and_race_drift(
        self,
    ) -> None:
        self.assertEqual(checker._private_idempotency_surface_errors(REPO_ROOT), [])
        relative_paths = (
            "apps/api/app/main.py",
            "apps/api/tests/test_private_social_graph.py",
            "apps/api/tests/test_organization_verification.py",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in relative_paths:
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )

            main = root / "apps/api/app/main.py"
            original_main = main.read_text(encoding="utf-8")
            replay_before_lookup = (
                "        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)\n"
                "        if replay is not None:\n"
                "            return replay\n"
                "        row = await active_connection_for_participant(\n"
                "            session, connection_id, principal, for_update=True\n"
                "        )"
            )
            replay_after_lookup = (
                "        row = await active_connection_for_participant(\n"
                "            session, connection_id, principal, for_update=True\n"
                "        )\n"
                "        replay = await idempotency_replay(session, request, principal, key, operation, fingerprint)\n"
                "        if replay is not None:\n"
                "            return replay"
            )
            mutated = original_main.replace(
                replay_before_lookup, replay_after_lookup, 1
            )
            self.assertNotEqual(mutated, original_main)
            main.write_text(mutated, encoding="utf-8")
            errors = checker._private_idempotency_surface_errors(root)
            self.assertTrue(
                any("required fail-closed control order" in error for error in errors)
            )

            main.write_text(original_main, encoding="utf-8")
            missing_empty_receipt = original_main.replace(
                '            status_code=204,\n            body="",\n            headers={},\n            resource_type="connection",',
                '            status_code=204,\n            body="removed",\n            headers={},\n            resource_type="connection",',
                1,
            )
            self.assertNotEqual(missing_empty_receipt, original_main)
            main.write_text(missing_empty_receipt, encoding="utf-8")
            errors = checker._private_idempotency_surface_errors(root)
            self.assertTrue(any("empty 204 receipt body" in error for error in errors))

            main.write_text(original_main, encoding="utf-8")
            missing_rollback = original_main.replace(
                "                await session.rollback()\n                replay = await idempotency_replay(\n",
                "                replay = await idempotency_replay(\n",
                1,
            )
            self.assertNotEqual(missing_rollback, original_main)
            main.write_text(missing_rollback, encoding="utf-8")
            errors = checker._private_idempotency_surface_errors(root)
            self.assertTrue(any("loser rollback" in error for error in errors))

            main.write_text(original_main, encoding="utf-8")
            overwrite_winner = original_main.replace(
                "                await session.rollback()\n                replay = await idempotency_replay(\n",
                '                await session.rollback()\n                existing.response_body = "winner-overwrite"\n                replay = await idempotency_replay(\n',
                1,
            )
            self.assertNotEqual(overwrite_winner, original_main)
            main.write_text(overwrite_winner, encoding="utf-8")
            errors = checker._private_idempotency_surface_errors(root)
            self.assertTrue(
                any(
                    "must not overwrite the committed receipt" in error
                    for error in errors
                )
            )

            main.write_text(original_main, encoding="utf-8")
            missing_provisional_gate = original_main.replace(
                "if provisional_record is not None and existing is provisional_record:",
                "if provisional_record is not None:",
                1,
            )
            self.assertNotEqual(missing_provisional_gate, original_main)
            main.write_text(missing_provisional_gate, encoding="utf-8")
            errors = checker._private_idempotency_surface_errors(root)
            self.assertTrue(
                any("object-identity provisional gate" in error for error in errors)
            )

            concurrent_test = root / "apps/api/tests/test_organization_verification.py"
            original_concurrent_test = concurrent_test.read_text(encoding="utf-8")
            response_drift = original_concurrent_test.replace(
                "assert first.json() == second.json()",
                "assert first.json() != second.json()",
                1,
            )
            self.assertNotEqual(response_drift, original_concurrent_test)
            concurrent_test.write_text(response_drift, encoding="utf-8")
            main.write_text(original_main, encoding="utf-8")
            errors = checker._private_idempotency_surface_errors(root)
            self.assertTrue(any("equal response payloads" in error for error in errors))

            concurrent_test.write_text(original_concurrent_test, encoding="utf-8")
            event_count_drift = original_concurrent_test.replace(
                "assert len(review_events) == 1",
                "assert len(review_events) == 2",
                1,
            )
            self.assertNotEqual(event_count_drift, original_concurrent_test)
            concurrent_test.write_text(event_count_drift, encoding="utf-8")
            errors = checker._private_idempotency_surface_errors(root)
            self.assertTrue(any("one review event" in error for error in errors))

    def test_follow_content_block_durability_fails_closed(self) -> None:
        files = (
            "apps/api/app/main.py",
            "apps/api/tests/test_follow_block_durability.py",
            "apps/web/components/profile-post-controls.tsx",
            "apps/web/lib/posts-api.ts",
            "apps/web/tests/posts-api.test.ts",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in files:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            self.assertEqual(checker._follow_content_block_durability_errors(root), [])

            main = root / "apps/api/app/main.py"
            original_main = main.read_text(encoding="utf-8")
            missing_delegation = original_main.replace(
                '"parameters": [_idempotency_openapi_parameter()],',
                '"parameters": [],',
                1,
            )
            self.assertNotEqual(missing_delegation, original_main)
            main.write_text(missing_delegation, encoding="utf-8")
            errors = checker._follow_content_block_durability_errors(root)
            self.assertTrue(
                any("Idempotency-Key helper delegation" in error for error in errors)
            )

            main.write_text(original_main, encoding="utf-8")
            main.write_text(
                original_main.replace(
                    '"pattern": _IDEMPOTENCY_KEY_PATTERN,',
                    '"pattern": "unbounded",',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._follow_content_block_durability_errors(root)
            self.assertTrue(
                any(
                    "visible-ASCII Idempotency-Key pattern" in error for error in errors
                )
            )

            follow_start = original_main.index("    async def follow_profile(")
            follow_end = original_main.index(
                '    @app.delete(\n        "/v1/follows/{profile_handle}"', follow_start
            )
            follow = original_main[follow_start:follow_end]
            first_replay = follow.index("replay = await idempotency_replay(")
            second_replay = follow.index(
                "replay = await idempotency_replay(", first_replay + 1
            )
            main.write_text(
                original_main[:follow_start]
                + follow[:second_replay]
                + follow[second_replay:].replace(
                    "replay = await idempotency_replay(",
                    "replay = await missing_social_replay(",
                    1,
                )
                + original_main[follow_end:],
                encoding="utf-8",
            )
            errors = checker._follow_content_block_durability_errors(root)
            self.assertTrue(any("post-lock replay" in error for error in errors))

            block_start = original_main.index("    async def block_post_content(")
            block_end = original_main.index(
                '    @app.delete(\n        "/v1/content-blocks/{profile_handle}"',
                block_start,
            )
            block = original_main[block_start:block_end]
            main.write_text(
                original_main[:block_start]
                + block.replace(
                    "ProfileFollow.follower_owner_id == profile.owner_id",
                    "ProfileFollow.follower_owner_id == missing_profile_owner",
                    1,
                )
                + original_main[block_end:],
                encoding="utf-8",
            )
            errors = checker._follow_content_block_durability_errors(root)
            self.assertTrue(any("reverse-follow deletion" in error for error in errors))

            replay_start = original_main.index("    async def social_graph_replay(")
            replay_end = original_main.index(
                "    async def idempotency_replay(", replay_start
            )
            replay = original_main[replay_start:replay_end]
            main.write_text(
                original_main[:replay_start]
                + replay.replace(
                    'record.response_headers != "{}"',
                    'record.response_headers != "not-empty"',
                    1,
                )
                + original_main[replay_end:],
                encoding="utf-8",
            )
            errors = checker._follow_content_block_durability_errors(root)
            self.assertTrue(any("receipt header guard" in error for error in errors))

            main.write_text(
                original_main.replace(
                    '"name": "create_document"', '"name": "follow_profile"', 1
                ),
                encoding="utf-8",
            )
            errors = checker._follow_content_block_durability_errors(root)
            self.assertTrue(
                any(
                    "mcp_tools must not expose follow or content-block mutations"
                    in error
                    for error in errors
                )
            )

            main.write_text(original_main, encoding="utf-8")
            posts_api = root / "apps/web/lib/posts-api.ts"
            original_posts_api = posts_api.read_text(encoding="utf-8")
            posts_api.write_text(
                original_posts_api.replace(
                    "export async function followProfile(",
                    "export async function socialFollow(",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._follow_content_block_durability_errors(root)
            self.assertTrue(any("caller-owned follow key" in error for error in errors))

            posts_api.write_text(original_posts_api, encoding="utf-8")
            controls = root / "apps/web/components/profile-post-controls.tsx"
            controls.write_text(
                controls.read_text(encoding="utf-8").replace(
                    "const requestIsCurrent = () => isSubjectCurrent() && claim.isCurrent();",
                    "const requestIsCurrent = () => false;",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._follow_content_block_durability_errors(root)
            self.assertTrue(
                any("current subject and claim guard" in error for error in errors)
            )

            controls.write_text(
                (REPO_ROOT / "apps/web/components/profile-post-controls.tsx").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            api_tests = root / "apps/api/tests/test_follow_block_durability.py"
            api_tests.write_text(
                api_tests.read_text(encoding="utf-8").replace(
                    "assert collision_handle.status_code == collision_method.status_code == 409",
                    "assert collision_handle.status_code == collision_method.status_code == 400",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._follow_content_block_durability_errors(root)
            self.assertTrue(any("collision assertion" in error for error in errors))

    def test_immutable_supply_chain_pins_fail_closed_on_mutable_drift(self) -> None:
        self.assertEqual(checker._immutable_supply_chain_surface_errors(REPO_ROOT), [])
        paths = (
            ".github/workflows/ci.yml",
            "apps/api/Dockerfile",
            "apps/api/tests/test_dockerfile_supply_chain.py",
            "apps/web/Dockerfile",
            "infra/nginx/Dockerfile",
            "compose.yaml",
            "compose.prod.yaml",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in paths:
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )

            workflow = root / ".github/workflows/ci.yml"
            workflow.write_text(
                workflow.read_text(encoding="utf-8").replace(
                    "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6.0.3",
                    "actions/checkout@v6",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._immutable_supply_chain_surface_errors(root)
            self.assertTrue(
                any("checkout v6.0.3 release commit" in error for error in errors)
            )

            workflow.write_text(
                (REPO_ROOT / ".github/workflows/ci.yml")
                .read_text(encoding="utf-8")
                .replace(
                    "postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777",
                    "postgres:16-alpine",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._immutable_supply_chain_surface_errors(root)
            self.assertTrue(
                any("CI PostgreSQL manifest index" in error for error in errors)
            )

            workflow.write_text(
                (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            dockerfile = root / "apps/api/Dockerfile"
            dockerfile.write_text(
                dockerfile.read_text(encoding="utf-8").replace(
                    "python:3.12-slim@sha256:646fb0bca3dd3ea1bcc6feb72c17ed16eed6e10cffc732fcc1478bd3e7f02d7b",
                    "python:3.12-slim",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._immutable_supply_chain_surface_errors(root)
            self.assertTrue(
                any("API Python manifest index" in error for error in errors)
            )

            dockerfile.write_text(
                (REPO_ROOT / "apps/api/Dockerfile")
                .read_text(encoding="utf-8")
                .replace(
                    "ARG DEBIAN_SNAPSHOT=20260805T010740Z",
                    "ARG DEBIAN_SNAPSHOT=latest",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._immutable_supply_chain_surface_errors(root)
            self.assertTrue(
                any("immutable Debian snapshot" in error for error in errors)
            )

            dockerfile.write_text(
                (REPO_ROOT / "apps/api/Dockerfile")
                .read_text(encoding="utf-8")
                .replace(
                    "poppler-utils=25.03.0-5+deb13u4",
                    "poppler-utils",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._immutable_supply_chain_surface_errors(root)
            self.assertTrue(
                any("exact Poppler utility package" in error for error in errors)
            )

    def test_production_operations_requires_immutable_supply_chain_constraint(
        self,
    ) -> None:
        for constraint in checker.REQUIRED_FEATURE_CONSTRAINTS["production-operations"]:
            with self.subTest(constraint=constraint):
                invalid = json.loads(json.dumps(self.registry))
                feature = next(
                    item
                    for item in invalid["features"]
                    if item["id"] == "production-operations"
                )
                feature["authority"]["constraints"].remove(constraint)
                self.assertTrue(
                    any(
                        "is missing required constraints" in error
                        and constraint in error
                        for error in self.check(invalid)
                    )
                )

    def test_agent_directory_search_is_live_enriched_but_projection_free(self) -> None:
        self.assertEqual(
            checker._agent_directory_search_contract_errors(
                self.registry["features"], REPO_ROOT
            ),
            [],
        )
        mutations = {
            "mode": (("surfaces", "search", "mode"), "indexed"),
            "fields": (("surfaces", "search", "fields"), ["agent handle"]),
            "projection": (("data", "search_projection"), "public"),
        }
        for label, (path, value) in mutations.items():
            with self.subTest(label=label):
                invalid = json.loads(json.dumps(self.registry))
                agent = next(
                    feature
                    for feature in invalid["features"]
                    if feature["id"] == "agent-representation-outreach"
                )
                target = agent
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                errors = checker._agent_directory_search_contract_errors(
                    invalid["features"], REPO_ROOT
                )
                self.assertTrue(any(path[-1] in error for error in errors))

        invalid = json.loads(json.dumps(self.registry))
        agent = next(
            feature
            for feature in invalid["features"]
            if feature["id"] == "agent-representation-outreach"
        )
        agent["surfaces"]["api"]["routes"].remove(
            "GET /v1/agent-identities/{agent_handle}"
        )
        errors = checker._agent_directory_search_contract_errors(
            invalid["features"], REPO_ROOT
        )
        self.assertTrue(
            any(
                "must declare public Agent Identity read routes" in error
                for error in errors
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            search = root / "apps/api/app/services/search.py"
            search.parent.mkdir(parents=True)
            search.write_text("AgentIdentity", encoding="utf-8")
            errors = checker._agent_directory_search_contract_errors(
                self.registry["features"], root
            )
            self.assertTrue(
                any("must not project AgentIdentity" in error for error in errors)
            )

            search.write_text("agent_capability", encoding="utf-8")
            errors = checker._agent_directory_search_contract_errors(
                self.registry["features"], root
            )
            self.assertTrue(
                any(
                    "must not pass the SQL-only agent capability" in error
                    for error in errors
                )
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = root / "apps/api/app/main.py"
            main.parent.mkdir(parents=True)
            source = (REPO_ROOT / "apps/api/app/main.py").read_text(encoding="utf-8")
            public_search = root / "apps/api/app/services/public_search.py"
            public_search.parent.mkdir(parents=True, exist_ok=True)
            public_search_source = (
                REPO_ROOT / "apps/api/app/services/public_search.py"
            ).read_text(encoding="utf-8")
            public_search.write_text(public_search_source, encoding="utf-8")
            card = root / "apps/api/app/routes/agent_card.py"
            card.parent.mkdir(parents=True, exist_ok=True)
            card.write_text(
                (REPO_ROOT / "apps/api/app/routes/agent_card.py").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            without_visibility_guard = public_search_source.replace(
                '            document.visibility != "public"\n            or ',
                "",
                1,
            )
            self.assertNotEqual(without_visibility_guard, public_search_source)
            public_search.write_text(
                without_visibility_guard,
                encoding="utf-8",
            )
            errors = checker._agent_directory_search_contract_errors(
                self.registry["features"], root
            )
            self.assertTrue(
                any(
                    "rejecting public visibility authorization guard" in error
                    for error in errors
                )
            )
            public_search.write_text(public_search_source, encoding="utf-8")

            main.write_text(
                source.replace(
                    '"name": "list_agent_directory"',
                    '"name": "missing_global_agent_directory"',
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._agent_directory_search_contract_errors(
                self.registry["features"], root
            )
            self.assertTrue(
                any("MCP global directory tool" in error for error in errors)
            )

            capabilities_tool_list = """"agent_tools": [
                    "get_agent_identity",
                    "list_agent_directory",
                    "list_profile_agents",
                ],"""
            capabilities_action_list = """"a2a_actions": [
                    "get_agent_identity",
                    "list_agent_directory",
                    "list_profile_agents",
                ],"""
            for label, marker, current, replacement in (
                (
                    "MCP tool",
                    "capabilities Agent Identity MCP read-tool parity",
                    capabilities_tool_list,
                    """"agent_tools": [
                    "list_agent_directory",
                    "list_profile_agents",
                ],""",
                ),
                (
                    "A2A action",
                    "capabilities Agent Identity A2A read-action parity",
                    capabilities_action_list,
                    """"a2a_actions": [
                    "list_agent_directory",
                    "list_profile_agents",
                ],""",
                ),
            ):
                with self.subTest(capability_list=label):
                    mutated = source.replace(current, replacement, 1)
                    self.assertNotEqual(mutated, source)
                    self.assertIn('"name": "get_agent_identity",', mutated)
                    main.write_text(mutated, encoding="utf-8")
                    errors = checker._agent_directory_search_contract_errors(
                        self.registry["features"], root
                    )
                    self.assertTrue(any(marker in error for error in errors))

            main.write_text(
                source.replace(
                    "handle: Annotated[str, Path(min_length=1, max_length=100)]",
                    "handle: str",
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._agent_directory_search_contract_errors(
                self.registry["features"], root
            )
            self.assertTrue(
                any("bounded HTTP profile-agent handle" in error for error in errors)
            )

    def test_employer_inventory_is_human_only_and_protocol_excluded(self) -> None:
        self.assertEqual(checker._employer_inventory_surface_errors(REPO_ROOT), [])

    def test_moderation_review_routes_are_individually_hidden_from_openapi(
        self,
    ) -> None:
        source = (REPO_ROOT / "apps/api/app/main.py").read_text(encoding="utf-8")
        routes = (
            "GET /v1/internal/post-moderation/cases",
            "GET /v1/internal/post-moderation/cases/{case_id}",
            "POST /v1/internal/post-moderation/cases/{case_id}/decision",
            "GET /v1/internal/post-moderation/appeals",
            "GET /v1/internal/post-moderation/appeals/{appeal_id}",
            "POST /v1/internal/post-moderation/appeals/{appeal_id}/decision",
        )
        for route in routes:
            self.assertTrue(checker._route_is_hidden_from_openapi(route, source))

        mutated = source.replace(
            '@app.get("/v1/internal/post-moderation/cases", include_in_schema=False)',
            '@app.get("/v1/internal/post-moderation/cases", include_in_schema=True)',
            1,
        )
        self.assertFalse(
            checker._route_is_hidden_from_openapi(
                "GET /v1/internal/post-moderation/cases", mutated
            )
        )
        relative_paths = (
            "apps/api/app/main.py",
            "apps/api/app/routes/agent_card.py",
            "apps/api/app/schemas.py",
            "apps/api/app/services/search.py",
            "apps/api/tests/test_social_core.py",
            "apps/web/lib/recruitment-api.ts",
            "apps/web/components/employer-inventory-panels.tsx",
            "apps/web/components/employer-workspace.tsx",
            "apps/web/tests/recruitment-api.test.ts",
            "apps/web/tests/private-workspace-isolation.test.ts",
            "apps/web/tests/private-workspace-truth.test.ts",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in relative_paths:
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            panels = root / "apps/web/components/employer-inventory-panels.tsx"
            panels.write_text(
                panels.read_text(encoding="utf-8").replace(
                    "No empty state is assumed", "Empty state assumed"
                ),
                encoding="utf-8",
            )
            errors = checker._employer_inventory_surface_errors(root)
            self.assertTrue(
                any("truthful inventory error state" in error for error in errors)
            )
            panels.write_text(
                (
                    REPO_ROOT / "apps/web/components/employer-inventory-panels.tsx"
                ).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            api = root / "apps/api/app/main.py"
            api.write_text(
                api.read_text(encoding="utf-8").replace(
                    "    def mcp_tools() -> list[dict[str, Any]]:\n",
                    (
                        "    def mcp_tools() -> list[dict[str, Any]]:\n"
                        '        _private_inventory_route = "/v1/employer/jobs"\n'
                    ),
                    1,
                ),
                encoding="utf-8",
            )
            errors = checker._employer_inventory_surface_errors(root)
            self.assertTrue(
                any(
                    "must not advertise private human-only route" in error
                    and "mcp_tools" in error
                    for error in errors
                )
            )

    def test_invalid_stage_claim_fails(self) -> None:
        invalid = json.loads(json.dumps(self.registry))
        invalid["features"][0]["stage"] = "repository_verified"
        invalid["features"][0]["evidence"]["repository_paths"] = []
        self.assertTrue(
            any(
                "requires repository evidence" in error for error in self.check(invalid)
            )
        )

    def test_advanced_stage_rejects_unstructured_or_false_evidence(self) -> None:
        promoted = json.loads(json.dumps(self.registry))
        promoted["features"][0]["stage"] = "releasable"
        promoted["features"][0]["evidence"]["repository_paths"] = ["README.md"]
        promoted["features"][0]["evidence"]["deployment_paths"] = ["README.md"]
        errors = self.check(promoted)
        self.assertGreaterEqual(
            sum(
                "advanced stages require JSON evidence receipts" in error
                for error in errors
            ),
            2,
        )

        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            receipt.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "evidence_type": "deployment",
                        "feature_id": "wrong-feature",
                        "source_revision": "not-a-revision",
                        "recorded_at": "not-a-timestamp",
                        "reviewer": "",
                        "target": "",
                        "configuration_scope": "",
                        "checks": [
                            {
                                "check_id": "INVALID ID",
                                "command": "",
                                "result": "failed",
                                "output_path": "README.md",
                                "output_sha256": "not-a-digest",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            receipt_errors: list[str] = []
            checker._validate_evidence_receipt(
                receipt,
                "repository",
                "canonical-documents",
                "receipt",
                receipt_errors,
                "a" * 40,
                Path(directory),
            )
            self.assertGreaterEqual(len(receipt_errors), 10)

    def test_evidence_receipt_binds_real_time_revision_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "evidence" / "platform" / "outputs" / "check.txt"
            output.parent.mkdir(parents=True)
            output.write_text("all checks passed\n", encoding="utf-8")
            digest = hashlib.sha256(output.read_bytes()).hexdigest()
            receipt = root / "evidence" / "platform" / "receipt.json"
            payload = {
                "schema_version": 1,
                "evidence_type": "repository",
                "feature_id": "canonical-documents",
                "source_revision": "a" * 40,
                "recorded_at": "2026-08-04T14:30:00Z",
                "reviewer": "independent-reviewer",
                "target": "repository",
                "configuration_scope": "python-3.12-local",
                "checks": [
                    {
                        "check_id": check_id,
                        "command": "python tools/check_platform_features.py",
                        "result": "pass",
                        "output_path": "evidence/platform/outputs/check.txt",
                        "output_sha256": digest,
                    }
                    for check_id in (
                        "llms-raw-markdown-workflow",
                        "human-guided-losslessness",
                        "human-owned-inventory-states",
                    )
                ],
            }
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_text(json.dumps(payload), encoding="utf-8")
            errors: list[str] = []
            checker._validate_evidence_receipt(
                receipt,
                "repository",
                "canonical-documents",
                "receipt",
                errors,
                "a" * 40,
                root,
            )
            self.assertEqual(errors, [])

            valid_checks = json.loads(json.dumps(payload["checks"]))
            payload["checks"] = payload["checks"][:-1]
            receipt.write_text(json.dumps(payload), encoding="utf-8")
            errors = []
            checker._validate_evidence_receipt(
                receipt,
                "repository",
                "canonical-documents",
                "receipt",
                errors,
                "a" * 40,
                root,
            )
            self.assertTrue(
                any("human-owned-inventory-states" in error for error in errors)
            )

            payload["checks"] = json.loads(json.dumps(valid_checks))
            payload["checks"][1]["check_id"] = payload["checks"][0]["check_id"]
            receipt.write_text(json.dumps(payload), encoding="utf-8")
            errors = []
            checker._validate_evidence_receipt(
                receipt,
                "repository",
                "canonical-documents",
                "receipt",
                errors,
                "a" * 40,
                root,
            )
            self.assertTrue(
                any("duplicates control check" in error for error in errors)
            )

            payload["checks"] = valid_checks
            payload["recorded_at"] = "2026-99-99T99:99:99Z"
            payload["checks"][0]["output_sha256"] = "0" * 64
            receipt.write_text(json.dumps(payload), encoding="utf-8")
            errors = []
            checker._validate_evidence_receipt(
                receipt,
                "repository",
                "canonical-documents",
                "receipt",
                errors,
                "a" * 40,
                root,
            )
            self.assertTrue(any("real calendar timestamp" in error for error in errors))
            self.assertTrue(
                any("does not match captured output" in error for error in errors)
            )

    def test_advanced_evidence_requires_a_clean_current_git_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(
                ["git", "init"],
                cwd=root,
                check=True,
                capture_output=True,
                timeout=10,
            )
            (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "tracked.txt"],
                cwd=root,
                check=True,
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Platform Contract Test",
                    "-c",
                    "user.email=platform-contract@example.invalid",
                    "commit",
                    "-m",
                    "fixture",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                timeout=10,
            )
            errors: list[str] = []
            revision = checker._clean_git_revision(root, errors)
            self.assertIsNotNone(revision)
            self.assertEqual(errors, [])

            (root / "untracked.txt").write_text("drift\n", encoding="utf-8")
            errors = []
            self.assertIsNone(checker._clean_git_revision(root, errors))
            self.assertTrue(any("clean working tree" in error for error in errors))

    def test_unclassified_data_and_lifecycle_fail(self) -> None:
        invalid = json.loads(json.dumps(self.registry))
        invalid["features"][0]["data"]["classification"] = "unknown"
        invalid["features"][0]["lifecycle"]["erase"] = "unknown"
        errors = self.check(invalid)
        self.assertTrue(any("data.classification" in error for error in errors))
        self.assertTrue(any("lifecycle.erase" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
