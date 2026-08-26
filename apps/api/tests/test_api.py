from __future__ import annotations

import inspect
from base64 import b64encode
from datetime import UTC, datetime
from hashlib import sha256

from fastapi.routing import APIRoute
from sqlalchemy import select

import app.main as main_module
from app.auth import Principal, optional_principal, require_principal
from app.main import create_app
from app.markdown import canonical_document_max_utf8_bytes
from app.models import (
    AgentProposal,
    ApiKey,
    Document,
    IdempotencyRecord,
    PublicTaxonomyDocumentSnapshot,
    PublicTaxonomyTerm,
)
from app.services.documents import public_owner_id

from .helpers import profile_markdown, resume_markdown
from .test_taxonomy import _install_ready, _profile_v2_markdown

_DELEGATED_KEY_ENDPOINTS = frozenset(
    {
        "create_profile",
        "create_resume",
        "update_profile",
        "update_resume",
        "create_contact_request",
        "create_agent_outreach",
        "submit_proposal",
    }
)
_IF_MATCH_HEADER_PATTERNS = {
    ("put", "/v1/profiles/{handle}"): main_module.STRONG_DOCUMENT_ETAG_PATTERN,
    ("put", "/v1/resumes/{slug}"): main_module.STRONG_DOCUMENT_ETAG_PATTERN,
    ("delete", "/v1/posts/{post_id}"): main_module.STRONG_DOCUMENT_ETAG_PATTERN,
    ("put", "/v1/contact-policy"): r'^"policy-(0|[1-9][0-9]*)"$',
    (
        "put",
        "/v1/organizations/{organization_slug}",
    ): main_module.STRONG_DOCUMENT_ETAG_PATTERN,
    (
        "put",
        "/v1/organizations/{organization_slug}/jobs/{job_slug}",
    ): main_module.STRONG_DOCUMENT_ETAG_PATTERN,
    (
        "post",
        "/v1/organizations/{organization_slug}/jobs/{job_slug}/lifecycle/{action}",
    ): main_module.STRONG_DOCUMENT_ETAG_PATTERN,
}


def _assert_runtime_openapi_header_contract(app) -> None:
    schema = app.openapi()
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        source = inspect.getsource(route.endpoint)
        for method in route.methods or ():
            operation = schema["paths"][route.path][method.lower()]
            parameters = operation.get("parameters", [])
            key_parameters = [
                parameter
                for parameter in parameters
                if parameter.get("in") == "header" and parameter.get("name") == "Idempotency-Key"
            ]
            requires_key = (
                "idempotency_key(request, required=True)" in source
                or route.endpoint.__name__ in _DELEGATED_KEY_ENDPOINTS
            )
            if requires_key:
                assert len(key_parameters) == 1, (method, route.path)
                key_parameter = key_parameters[0]
                assert key_parameter["required"] is True
                assert key_parameter["schema"] == {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "pattern": main_module._IDEMPOTENCY_KEY_PATTERN,
                }
            else:
                assert not key_parameters, (method, route.path)

            expected_if_match_pattern = _IF_MATCH_HEADER_PATTERNS.get((method.lower(), route.path))
            if expected_if_match_pattern is None:
                assert not [
                    parameter
                    for parameter in parameters
                    if parameter.get("in") == "header" and parameter.get("name") == "If-Match"
                ], (method, route.path)
                continue
            if_match_parameters = [
                parameter
                for parameter in parameters
                if parameter.get("in") == "header" and parameter.get("name") == "If-Match"
            ]
            assert len(if_match_parameters) == 1, (method, route.path)
            if_match_parameter = if_match_parameters[0]
            assert if_match_parameter["required"] is True
            assert if_match_parameter["schema"] == {
                "type": "string",
                "pattern": expected_if_match_pattern,
            }


async def test_json_and_markdown_reads_match_and_versions_are_append_only(api_client) -> None:
    _, client = api_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown()},
        headers={"Idempotency-Key": "api-profile-create-0001"},
    )
    assert created.status_code == 201, created.text
    first = created.json()
    assert first["owner_id"] != "user_test"
    assert "owner_id: user_test" not in first["markdown"]
    markdown = await client.get("/v1/profiles/ada-lovelace", headers={"Accept": "text/markdown"})
    explicit = await client.get("/v1/profiles/ada-lovelace.md")
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert set(item.strip() for item in markdown.headers["vary"].split(",")) == {
        "Accept",
        "Authorization",
    }
    assert markdown.headers["cache-control"] == "no-store"
    assert markdown.content == explicit.content == first["markdown"].encode("utf-8")

    updated = await client.put(
        "/v1/profiles/ada-lovelace",
        json={
            "markdown": profile_markdown(headline="Principal backend engineer", visibility="public")
        },
        headers={
            "If-Match": created.headers["etag"],
            "Idempotency-Key": "api-profile-update-0001",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    round_tripped = updated.json()["markdown"].replace(
        "headline: Principal backend engineer", "headline: Staff platform engineer"
    )
    canonical_update = await client.put(
        "/v1/profiles/ada-lovelace",
        json={"markdown": round_tripped},
        headers={
            "If-Match": updated.headers["etag"],
            "Idempotency-Key": "api-profile-update-0002",
        },
    )
    assert canonical_update.status_code == 200, canonical_update.text
    assert canonical_update.json()["version"] == 3
    assert canonical_update.json()["visibility"] == "public"
    stale_update = await client.put(
        "/v1/profiles/ada-lovelace",
        json={"markdown": round_tripped},
        headers={
            "If-Match": canonical_update.headers["etag"],
            "Idempotency-Key": "api-profile-stale-version-0001",
        },
    )
    assert stale_update.status_code == 409, stale_update.text
    versions = await client.get("/v1/profiles/ada-lovelace/versions")
    assert [item["version"] for item in versions.json()["versions"]] == [1, 2, 3]
    assert all(item["actor_id"] != "user_test" for item in versions.json()["versions"])
    historical = await client.get(
        "/v1/profiles/ada-lovelace/versions/1", headers={"Accept": "text/markdown"}
    )
    assert historical.content == markdown.content
    historical_json = await client.get("/v1/profiles/ada-lovelace/versions/1")
    assert historical_json.status_code == 200
    assert historical_json.json()["visibility"] == "private"
    historical_explicit = await client.get("/v1/profiles/ada-lovelace/versions/1.md")
    assert historical_explicit.status_code == 200
    assert historical_explicit.content == markdown.content
    assert versions.json()["versions"][0]["markdown_url"].endswith("/versions/1.md")


async def test_private_resume_versions_and_markdown_reads_are_owner_bound(api_client) -> None:
    app, client = api_client
    created = await client.post(
        "/v1/resumes",
        json={"markdown": resume_markdown()},
        headers={"Idempotency-Key": "private-resume-create-0001"},
    )
    assert created.status_code == 201, created.text
    v1_markdown = created.json()["markdown"]
    v1_sha256 = sha256(v1_markdown.encode("utf-8")).hexdigest()
    v1_etag = f'"sha256-{v1_sha256}"'
    v1_digest = f"sha-256=:{b64encode(bytes.fromhex(v1_sha256)).decode('ascii')}:"
    assert created.headers["etag"] == v1_etag

    updated_markdown = resume_markdown().replace(
        "headline: Builds reliable systems", "headline: Designs reliable systems"
    )
    updated = await client.put(
        "/v1/resumes/ada-lovelace-resume",
        json={"markdown": updated_markdown},
        headers={
            "If-Match": created.headers["etag"],
            "Idempotency-Key": "private-resume-update-0001",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    current_markdown = updated.json()["markdown"]

    current_negotiated = await client.get(
        "/v1/resumes/ada-lovelace-resume", headers={"Accept": "text/markdown"}
    )
    current_explicit = await client.get("/v1/resumes/ada-lovelace-resume.md")
    for response in (current_negotiated, current_explicit):
        assert response.status_code == 200, response.text
        assert response.content == current_markdown.encode("utf-8")
        assert response.headers["content-type"].startswith("text/markdown")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["etag"] == updated.headers["etag"]
        assert response.headers["content-digest"] == (
            f"sha-256=:{b64encode(bytes.fromhex(updated.headers['etag'][8:-1])).decode('ascii')}:"
        )
    assert {item.strip() for item in current_negotiated.headers["vary"].split(",")} == {
        "Accept",
        "Authorization",
    }
    assert {item.strip() for item in current_explicit.headers["vary"].split(",")} == {
        "Authorization",
    }

    versions = await client.get("/v1/resumes/ada-lovelace-resume/versions")
    assert versions.status_code == 200, versions.text
    assert [item["version"] for item in versions.json()["versions"]] == [1, 2]
    assert versions.json()["versions"][0]["etag"] == v1_etag
    assert versions.json()["versions"][0]["markdown_url"].endswith("/versions/1.md")

    version_json = await client.get("/v1/resumes/ada-lovelace-resume/versions/1")
    assert version_json.status_code == 200, version_json.text
    assert version_json.json()["version"] == 1
    assert version_json.json()["markdown"] == v1_markdown
    assert version_json.json()["etag"] == v1_etag

    version_negotiated = await client.get(
        "/v1/resumes/ada-lovelace-resume/versions/1", headers={"Accept": "text/markdown"}
    )
    version_explicit = await client.get("/v1/resumes/ada-lovelace-resume/versions/1.md")
    assert version_negotiated.content == version_explicit.content == v1_markdown.encode("utf-8")
    assert version_negotiated.headers["content-type"].startswith("text/markdown")
    assert version_explicit.headers["content-type"].startswith("text/markdown")
    for response in (version_negotiated, version_explicit):
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["etag"] == v1_etag
        assert response.headers["content-digest"] == v1_digest
    assert {item.strip() for item in version_negotiated.headers["vary"].split(",")} == {
        "Accept",
        "Authorization",
    }
    assert {item.strip() for item in version_explicit.headers["vary"].split(",")} == {
        "Authorization",
    }

    default_optional = app.dependency_overrides[optional_principal]

    async def anonymous() -> None:
        return None

    async def other_clerk() -> Principal:
        return Principal(
            subject="other-clerk",
            method="clerk_jwt",
            scopes=frozenset({"documents:read"}),
        )

    try:
        for override in (anonymous, other_clerk):
            app.dependency_overrides[optional_principal] = override
            for path in (
                "/v1/resumes/ada-lovelace-resume",
                "/v1/resumes/ada-lovelace-resume.md",
            ):
                hidden = await client.get(path)
                assert hidden.status_code == 404, hidden.text
                assert hidden.json()["detail"] == "document was not found"
                assert "owner_id" not in hidden.text
                assert "markdown" not in hidden.text
    finally:
        app.dependency_overrides[optional_principal] = default_optional


async def test_anonymous_public_documents_use_pseudonymous_owner_ids(api_client) -> None:
    app, client = api_client
    profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "anonymous-public-profile-create-0001"},
    )
    resume = await client.post(
        "/v1/resumes",
        json={
            "markdown": resume_markdown().replace("visibility: private", "visibility: public", 1)
        },
        headers={"Idempotency-Key": "anonymous-public-resume-create-0001"},
    )
    assert profile.status_code == 201, profile.text
    assert resume.status_code == 201, resume.text

    async def anonymous() -> None:
        return None

    app.dependency_overrides[optional_principal] = anonymous
    expected_owner = public_owner_id("user_test")
    expected_fields = {
        "id",
        "kind",
        "owner_id",
        "identifier",
        "visibility",
        "version",
        "updated_at",
        "markdown",
        "markdown_url",
        "etag",
    }
    for path, kind in (
        ("/v1/profiles/ada-lovelace", "profile"),
        ("/v1/resumes/ada-lovelace-resume", "resume"),
    ):
        response = await client.get(path)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert set(payload) == expected_fields
        assert payload["kind"] == kind
        assert payload["visibility"] == "public"
        assert payload["owner_id"] == expected_owner
        assert payload["owner_id"] != "user_test"
        assert expected_owner in payload["markdown"]
        assert "user_test" not in response.text


async def test_canonical_update_accepts_yaml_timestamp_value(api_client) -> None:
    _, client = api_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown()},
        headers={"Idempotency-Key": "timestamp-profile-create-0001"},
    )
    canonical = created.json()["markdown"]
    # Browser YAML serializers commonly emit RFC 3339 timestamps without quotes,
    # which PyYAML loads as datetime objects.
    canonical = canonical.replace("updated_at: '", "updated_at: ").replace("Z'\n", "Z\n")
    canonical = canonical.replace("headline: Backend engineer", "headline: Platform engineer")
    updated = await client.put(
        "/v1/profiles/ada-lovelace",
        json={"markdown": canonical},
        headers={
            "If-Match": created.headers["etag"],
            "Idempotency-Key": "timestamp-profile-update-0001",
        },
    )
    assert updated.status_code == 200, updated.text


async def test_accept_q_values_choose_json_or_markdown(api_client) -> None:
    _, client = api_client
    await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown()},
        headers={"Idempotency-Key": "accept-profile-create-0001"},
    )
    json_response = await client.get(
        "/v1/profiles/ada-lovelace",
        headers={"Accept": "application/json, text/markdown;q=0.2"},
    )
    assert json_response.headers["content-type"].startswith("application/json")
    assert set(item.strip() for item in json_response.headers["vary"].split(",")) == {
        "Accept",
        "Authorization",
    }
    markdown_response = await client.get(
        "/v1/profiles/ada-lovelace",
        headers={"Accept": "application/json;q=0.2, text/markdown;q=0.8"},
    )
    assert markdown_response.headers["content-type"].startswith("text/markdown")
    rejected_markdown = await client.get(
        "/v1/profiles/ada-lovelace", headers={"Accept": "text/markdown;q=0, */*"}
    )
    assert rejected_markdown.headers["content-type"].startswith("application/json")


async def test_raw_markdown_write_has_no_json_wrapper_requirement(api_client) -> None:
    _, client = api_client
    response = await client.post(
        "/v1/resumes",
        content=resume_markdown().encode("utf-8"),
        headers={
            "Content-Type": "text/markdown",
            "Idempotency-Key": "raw-resume-create-0001",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["kind"] == "resume"
    assert response.json()["identifier"] == "ada-lovelace-resume"
    by_slug = await client.get("/v1/resumes/ada-lovelace-resume")
    by_id = await client.get(f"/v1/resumes/{response.json()['id']}")
    assert by_slug.status_code == 200
    assert by_id.status_code == 404

    canonical = response.json()["markdown"].replace(
        "headline: Builds reliable systems", "headline: Builds durable systems"
    )
    updated = await client.put(
        "/v1/resumes/ada-lovelace-resume",
        json={"markdown": canonical},
        headers={
            "If-Match": response.headers["etag"],
            "Idempotency-Key": "raw-resume-update-0001",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2


async def test_json_markdown_body_rejects_invalid_utf8_and_extra_keys(api_client) -> None:
    _, client = api_client
    invalid_utf8 = await client.post(
        "/v1/profiles",
        content=b"\xff",
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": "invalid-utf8-profile-create-0001",
        },
    )
    assert invalid_utf8.status_code == 422
    extra = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(), "unexpected": True},
        headers={"Idempotency-Key": "extra-key-profile-create-0001"},
    )
    assert extra.status_code == 422


async def test_validation_problem_does_not_echo_raw_input(api_client) -> None:
    _, client = api_client
    sentinel = "validation-private-sentinel-7f2c"
    response = await client.post("/v1/search/query", json={"q": [sentinel]})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["type"] == "https://connect.md/problems/validation-failed"
    assert body["status"] == 422
    assert body["request_id"]
    assert body["errors"]
    assert sentinel not in response.text
    for error in body["errors"]:
        assert {"type", "loc", "msg"} <= set(error)
        assert set(error) <= {"type", "loc", "msg", "ctx"}
        assert "input" not in error
        assert "url" not in error
        assert sentinel not in str(error)

    invalid_markdown = profile_markdown().replace(
        "name: Ada Lovelace",
        f"name:\n  private_note: {sentinel}",
        1,
    )
    markdown_response = await client.post(
        "/v1/profiles",
        json={"markdown": invalid_markdown},
        headers={"Idempotency-Key": "private-validation-profile-create-0001"},
    )
    assert markdown_response.status_code == 422
    assert markdown_response.headers["content-type"].startswith("application/problem+json")
    assert markdown_response.json()["detail"] == main_module.PUBLIC_MARKDOWN_VALIDATION_DETAIL
    assert sentinel not in markdown_response.text


async def test_private_document_is_hidden_and_non_owner_cannot_update(api_client) -> None:
    app, client = api_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown()},
        headers={"Idempotency-Key": "private-profile-create-0001"},
    )
    assert created.status_code == 201

    async def anonymous():
        return None

    app.dependency_overrides[optional_principal] = anonymous
    hidden = await client.get("/v1/profiles/ada-lovelace")
    assert hidden.status_code == 404

    async def other_owner() -> Principal:
        return Principal(subject="user_other", method="clerk_jwt", scopes=frozenset({"*"}))

    app.dependency_overrides[require_principal] = other_owner
    denied = await client.put(
        "/v1/profiles/ada-lovelace",
        json={"markdown": profile_markdown()},
        headers={
            "If-Match": created.headers["etag"],
            "Idempotency-Key": "private-profile-other-owner-update-0001",
        },
    )
    assert denied.status_code == 404


async def test_search_skill_filters_enforce_item_contract(api_client) -> None:
    _, client = api_client
    blank = await client.get("/v1/search", params={"skills": " "})
    assert blank.status_code == 422
    too_long = await client.get("/v1/search", params={"skills": "x" * 81})
    assert too_long.status_code == 422
    invalid_agent_capability = await client.get(
        "/v1/search", params={"agent_capability": "external_contact"}
    )
    assert invalid_agent_capability.status_code == 422


async def test_post_search_query_uses_taxonomy_registry(api_client) -> None:
    app, client = api_client
    await _install_ready(app)
    async with app.state.session_factory() as session:
        from app.services.documents import DocumentService

        await DocumentService(session, app.state.store, app.state.settings).create(
            "profile", _profile_v2_markdown(), "user_test"
        )
    terms = (await client.get("/v1/taxonomies/skill?limit=1")).json()["terms"]
    assert terms
    calls: list[dict[str, object]] = []

    class CapturingProjection:
        async def search(self, **kwargs: object):
            calls.append(kwargs)
            return ([], 0)

    app.state.search = CapturingProjection()
    response = await client.post(
        "/v1/search/query",
        json={"q": "", "skill_ids": [terms[0]["canonical_id"]], "limit": 10},
    )
    assert response.status_code == 200, response.text
    assert calls[0]["skill_ids"] == [terms[0]["canonical_id"]]
    assert response.json()["indexing_available"] is True
    assert "taxonomy_facets" in response.json()


async def test_search_boundaries_unknown_values_and_anonymous_post_are_fail_closed(
    api_client,
) -> None:
    app, client = api_client
    await _install_ready(app)
    calls: list[dict[str, object]] = []

    class CapturingProjection:
        async def search(self, **kwargs: object):
            calls.append(kwargs)
            return ([], 0)

    app.state.search = CapturingProjection()
    compact_80 = "a:" + "x" * 78
    compact_81 = "a:" + "x" * 79
    canonical_336 = "s" * 80 + ":" + "x" * 255
    canonical_337 = "s" * 80 + ":" + "x" * 256

    get_compact = await client.get("/v1/search", params={"skill_ids": [compact_80]})
    assert get_compact.status_code == 200, get_compact.text
    assert get_compact.json()["total"] == 0
    assert get_compact.json()["indexing_available"] is True
    assert calls == []
    get_too_long = await client.get("/v1/search", params={"skill_ids": [compact_81]})
    assert get_too_long.status_code == 422

    post_canonical = await client.post(
        "/v1/search/query", json={"skill_ids": [canonical_336], "limit": 7}
    )
    assert post_canonical.status_code == 200, post_canonical.text
    assert post_canonical.json()["total"] == 0
    assert post_canonical.json()["limit"] == 7
    assert calls == []
    post_too_long = await client.post("/v1/search/query", json={"skill_ids": [canonical_337]})
    assert post_too_long.status_code == 422

    fifty_plus_one = [("seniority_ids", compact_80) for _ in range(49)] + [
        ("skill_ids", compact_80),
        ("facets", "skills"),
    ]
    get_overbound = await client.get("/v1/search", params=fifty_plus_one)
    assert get_overbound.status_code == 422
    post_overbound = await client.post(
        "/v1/search/query",
        json={
            "seniority_ids": [compact_80] * 49,
            "skill_ids": [compact_80],
            "facets": ["skills"],
        },
    )
    assert post_overbound.status_code == 422
    assert calls == []

    async def anonymous():
        return None

    app.dependency_overrides[optional_principal] = anonymous
    anonymous_post = await client.post("/v1/search/query", json={"q": "anonymous"})
    assert anonymous_post.status_code == 200, anonymous_post.text
    assert anonymous_post.json()["indexing_available"] is True
    assert calls and calls[-1]["query"] == "anonymous"


async def test_search_uses_authoritative_updated_bounds_sort_and_taxonomy_facets(
    api_client,
) -> None:
    app, client = api_client
    await _install_ready(app)
    async with app.state.session_factory() as session:
        from app.services.documents import DocumentService

        service = DocumentService(session, app.state.store, app.state.settings)
        older = await service.create(
            "profile", _profile_v2_markdown(name="Older Person", skills=("Python",)), "user_test"
        )
        newer = await service.create(
            "profile", _profile_v2_markdown(name="Newer Person", skills=("Python",)), "user_test"
        )
        snapshots = (
            await session.scalars(
                select(PublicTaxonomyDocumentSnapshot).where(
                    PublicTaxonomyDocumentSnapshot.document_id.in_([older.id, newer.id])
                )
            )
        ).all()
        snapshot_by_id = {snapshot.document_id: snapshot for snapshot in snapshots}
        snapshot_by_id[older.id].updated_at = datetime(2026, 1, 1, tzinfo=UTC)
        snapshot_by_id[newer.id].updated_at = datetime(2026, 2, 1, tzinfo=UTC)
        await session.commit()
        skill = await session.scalar(
            select(PublicTaxonomyTerm).where(PublicTaxonomyTerm.taxonomy == "skill")
        )
        assert skill is not None

    def hit(document: Document, forged_updated_at: str) -> dict[str, object]:
        return {
            "id": document.id,
            "kind": "profile",
            "identifier": document.public_identifier,
            "name": document.public_identifier,
            "headline": "Test profile",
            "location": "Singapore",
            "skills": ["forged"],
            "version": document.current_version,
            "updated_at": forged_updated_at,
        }

    class ForgedProjection:
        async def search(self, **kwargs: object):
            return (
                [
                    hit(newer, "1900-01-01T00:00:00Z"),
                    hit(older, "9999-12-31T00:00:00Z"),
                ],
                2,
            )

    app.state.search = ForgedProjection()
    bounded = await client.get(
        "/v1/search",
        params={
            "updated_after": "2026-01-15T00:00:00Z",
            "sort_updated": "asc",
            "limit": 2,
        },
    )
    assert bounded.status_code == 200, bounded.text
    assert [hit["id"] for hit in bounded.json()["hits"]] == [newer.id]
    assert bounded.json()["hits"][0]["updated_at"].startswith("2026-02-01")

    ascending = await client.get("/v1/search", params={"sort_updated": "asc", "limit": 2})
    descending = await client.get("/v1/search", params={"sort_updated": "desc", "limit": 2})
    assert [hit["id"] for hit in ascending.json()["hits"]] == [older.id, newer.id]
    assert [hit["id"] for hit in descending.json()["hits"]] == [newer.id, older.id]

    equal_updated_at = datetime(2026, 3, 1, tzinfo=UTC)
    async with app.state.session_factory() as session:
        equal_snapshots = (
            await session.scalars(
                select(PublicTaxonomyDocumentSnapshot).where(
                    PublicTaxonomyDocumentSnapshot.document_id.in_([older.id, newer.id])
                )
            )
        ).all()
        for snapshot in equal_snapshots:
            snapshot.updated_at = equal_updated_at
        await session.commit()

    ascending_equal = await client.get("/v1/search", params={"sort_updated": "asc", "limit": 2})
    descending_equal = await client.get("/v1/search", params={"sort_updated": "desc", "limit": 2})
    expected_tie_ascending = sorted([older.id, newer.id])
    assert [hit["id"] for hit in ascending_equal.json()["hits"]] == expected_tie_ascending
    assert [hit["id"] for hit in descending_equal.json()["hits"]] == list(
        reversed(expected_tie_ascending)
    )

    async with app.state.session_factory() as session:
        from app.models import PublicTaxonomyMembership
        from app.services.taxonomy import _recalculate_terms

        memberships = (
            await session.scalars(
                select(PublicTaxonomyMembership).where(PublicTaxonomyMembership.term_id == skill.id)
            )
        ).all()
        assert len(memberships) >= 2
        memberships[0].label_assertion = "Python"
        memberships[1].label_assertion = "Python (asserted)"
        await _recalculate_terms(session, [skill.id])
        await session.commit()

    faceted = await client.get(
        "/v1/search",
        params=[
            ("skill_ids", skill.filter_value),
            ("facets", "skill_ids"),
            ("facets", "skills"),
            ("limit", "2"),
        ],
    )
    assert faceted.status_code == 200, faceted.text
    entries = faceted.json()["taxonomy_facets"]["skill_ids"]
    assert len(entries) == 1
    assert entries[0]["count"] == 2
    assert entries[0]["label"] is None
    assert entries[0]["label_conflict"] is True
    assert faceted.json()["facets"]["skills"] == {"Python": 1, "Python (asserted)": 1}


async def test_search_kind_and_singleton_location_are_rechecked(api_client) -> None:
    app, client = api_client
    response = await client.get(
        "/v1/search",
        params=[("location_id", "connect.md:one"), ("location_id", "connect.md:two")],
    )
    assert response.status_code == 422
    calls: list[dict[str, object]] = []

    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "search-kind-forged-0001"},
    )
    assert created.status_code == 201

    class CapturingProjection:
        async def search(self, **kwargs: object):
            calls.append(kwargs)
            return (
                [
                    {
                        "id": created.json()["id"],
                        "kind": "resume",
                        "identifier": created.json()["identifier"],
                        "name": "Ada Lovelace",
                        "headline": "Backend engineer",
                        "location": "Singapore",
                        "skills": ["Python"],
                        "version": created.json()["version"],
                        "markdown_url": created.json()["markdown_url"],
                    }
                ],
                1,
            )

    app.state.search = CapturingProjection()
    wrong_kind = await client.get("/v1/search", params={"kind": "profile"})
    assert wrong_kind.status_code == 200
    assert wrong_kind.json()["hits"] == []


async def test_typed_search_fails_closed_without_taxonomy_registry(api_client) -> None:
    app, client = api_client

    class CapturingProjection:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def search(self, **kwargs: object):
            self.calls.append(kwargs)
            return ([], 0)

    projection = CapturingProjection()
    app.state.search = projection
    response = await client.get(
        "/v1/search",
        params=[
            ("seniority_ids", "esco:senior"),
            ("seniority_ids", "esco:lead"),
            ("seniority_ids", "esco:senior"),
            ("seniority_id", "esco:legacy"),
        ],
    )

    assert response.status_code == 503
    assert projection.calls == []


async def test_typed_facet_fails_closed_without_taxonomy_registry(api_client) -> None:
    app, client = api_client
    calls: list[dict[str, object]] = []

    class CapturingProjection:
        async def search(self, **kwargs: object):
            calls.append(kwargs)
            return ([], 0)

    app.state.search = CapturingProjection()
    response = await client.get("/v1/search", params={"facets": "skill_ids"})
    assert response.status_code == 503
    assert calls == []


async def test_agent_key_is_returned_once_hashed_and_revocable(api_client) -> None:
    app, client = api_client
    created = await client.post(
        "/v1/api-keys",
        json={"scopes": ["documents:write", "documents:read"]},
        headers={"Idempotency-Key": "api-key-create-0001"},
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["key"].startswith("cnd_")
    async with app.state.session_factory() as session:
        record = await session.scalar(select(ApiKey).where(ApiKey.id == payload["id"]))
        assert record is not None
        assert payload["key"] not in record.secret_hash
        assert record.prefix == payload["prefix"]

    app.dependency_overrides.clear()
    profile = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown()},
        headers={
            "Authorization": f"Bearer {payload['key']}",
            "Idempotency-Key": "api-key-profile-create-0001",
        },
    )
    assert profile.status_code == 201, profile.text
    async with app.state.session_factory() as session:
        record = await session.scalar(select(ApiKey).where(ApiKey.id == payload["id"]))
        assert record is not None and record.last_used_at is not None

    async def clerk_owner() -> Principal:
        return Principal(subject="user_test", method="clerk_jwt", scopes=frozenset({"*"}))

    app.dependency_overrides[require_principal] = clerk_owner
    revoked = await client.delete(
        f"/v1/api-keys/{payload['id']}", headers={"Idempotency-Key": "api-key-revoke-0001"}
    )
    assert revoked.status_code == 204
    app.dependency_overrides.clear()
    denied = await client.post(
        "/v1/resumes",
        json={"markdown": resume_markdown()},
        headers={"Authorization": f"Bearer {payload['key']}"},
    )
    assert denied.status_code == 401


async def test_ingest_accepts_canonical_target_schema_without_publishing(api_client) -> None:
    app, client = api_client
    response = await client.post(
        "/v1/ingest",
        data={"target_schema": "connect.md/resume"},
        files={"file": ("source.md", b"# Imported\n\nSource content", "text/markdown")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["target_schema"] == "connect.md/resume"
    assert response.json()["published"] is False
    async with app.state.session_factory() as session:
        assert (await session.scalars(select(Document))).all() == []


async def test_search_is_explicitly_degraded_without_meilisearch(api_client) -> None:
    _, client = api_client
    response = await client.get("/v1/search?q=python&kind=profile&skills=Python&location=Singapore")
    assert response.status_code == 200
    assert response.json()["indexing_available"] is False
    assert response.headers["cache-control"] == "no-store"
    assert "Authorization" in response.headers["vary"]


async def test_search_rechecks_visibility_and_version_against_postgres(api_client) -> None:
    app, client = api_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown()},
        headers={"Idempotency-Key": "search-recheck-profile-create-0001"},
    )
    assert created.status_code == 201
    document = created.json()
    received: dict[str, object] = {}

    class StaleProjection:
        async def search(self, **kwargs: object):
            received.update(kwargs)
            return (
                [
                    {
                        "id": document["id"],
                        "kind": "profile",
                        "identifier": document["identifier"],
                        "name": "Ada Lovelace",
                        "headline": "stale public projection",
                        "title": None,
                        "location": "Singapore",
                        "skills": ["Python"],
                        "version": document["version"],
                        "markdown_url": document["markdown_url"],
                    }
                ],
                1,
            )

    app.state.search = StaleProjection()
    response = await client.get("/v1/search?q=ada")
    assert response.status_code == 200
    assert received["owner_id"] is None
    assert response.json()["hits"] == []
    assert response.json()["total"] == 0


async def test_search_recomputes_facets_after_public_reauthorization_for_every_caller(
    api_client,
) -> None:
    app, client = api_client
    public = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "search-facets-profile-create-0001"},
    )
    assert public.status_code == 201, public.text
    assert public.json()["visibility"] == "public"
    private = await client.post(
        "/v1/resumes",
        json={"markdown": resume_markdown()},
        headers={"Idempotency-Key": "search-facets-resume-create-0001"},
    )
    assert private.status_code == 201, private.text

    class MaliciousProjection:
        async def search(self, **_: object):
            return (
                [
                    {
                        "id": public.json()["id"],
                        "kind": "profile",
                        "identifier": public.json()["identifier"],
                        "name": "Ada Lovelace",
                        "headline": "Backend engineer",
                        "title": None,
                        "location": "Singapore",
                        "skills": ["Python"],
                        "version": public.json()["version"],
                        "markdown_url": public.json()["markdown_url"],
                    },
                    {
                        "id": private.json()["id"],
                        "kind": "resume",
                        "identifier": private.json()["identifier"],
                        "name": "Private Person",
                        "headline": "Secret role",
                        "title": "Secret role",
                        "location": "Secret location",
                        "skills": ["Secret skill"],
                        "version": private.json()["version"],
                        "markdown_url": private.json()["markdown_url"],
                    },
                ],
                999,
                {
                    "kind": {"resume": 999},
                    "skills": {"Secret skill": 999},
                },
            )

    app.state.search = MaliciousProjection()
    principals = [
        None,
        Principal(subject="user_test", method="clerk_jwt", scopes=frozenset({"*"})),
        Principal(
            subject="user_test",
            method="agent_api_key",
            scopes=frozenset({"search:read"}),
        ),
        Principal(
            subject="user_test",
            method="agent_grant",
            scopes=frozenset({"search:read"}),
            grant_mode="direct",
            resource_type="owner",
        ),
    ]
    responses = []
    for principal in principals:

        async def current(value: Principal | None = principal) -> Principal | None:
            return value

        app.dependency_overrides[optional_principal] = current
        response = await client.get(
            "/v1/search",
            params=[("q", "ada"), ("facets", "kind"), ("facets", "skills")],
        )
        assert response.status_code == 200, response.text
        responses.append(response.json())

    assert all(response == responses[0] for response in responses)
    assert [hit["id"] for hit in responses[0]["hits"]] == [public.json()["id"]]
    assert responses[0]["total"] == 1
    assert responses[0]["facets"] == {
        "kind": {"profile": 1},
        "skills": {"Python": 1},
    }
    assert responses[0]["warning"] is None


async def test_search_preserves_total_when_page_has_no_stale_hits(api_client) -> None:
    app, client = api_client
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public")},
        headers={"Idempotency-Key": "search-total-profile-create-0001"},
    )
    document = created.json()

    class CurrentProjection:
        async def search(self, **_: object):
            hit = {
                "id": document["id"],
                "kind": "profile",
                "identifier": document["identifier"],
                "name": "Ada Lovelace",
                "headline": "Backend engineer",
                "title": None,
                "location": "Singapore",
                "skills": ["Python"],
                "version": document["version"],
                "markdown_url": document["markdown_url"],
            }
            return ([hit.copy() for _ in range(100)], 100)

    app.state.search = CurrentProjection()
    response = await client.get("/v1/search?q=ada&limit=1")
    assert response.status_code == 200
    assert len(response.json()["hits"]) == 1
    assert response.json()["total"] == 100


async def test_ingest_accepts_long_markdown_extension(api_client) -> None:
    _, client = api_client
    response = await client.post(
        "/v1/ingest",
        data={"target_schema": "connect.md/profile"},
        files={"file": ("source.markdown", b"# Imported\n\nSource content", "text/markdown")},
    )
    assert response.status_code == 200, response.text


async def test_openapi_describes_bearer_and_raw_markdown(api_client) -> None:
    app, client = api_client
    document = (await client.get("/openapi.json")).json()
    _assert_runtime_openapi_header_contract(app)
    schemes = document["components"]["securitySchemes"]
    assert {"BearerAuth", "ClerkBearerAuth"}.issubset(schemes)
    assert "ClerkSessionCookie" not in schemes
    create = document["paths"]["/v1/profiles"]["post"]
    canonical_limit = canonical_document_max_utf8_bytes()
    assert "text/markdown" in create["requestBody"]["content"]
    assert (
        create["requestBody"]["content"]["application/json"]["schema"]["properties"]["markdown"][
            "x-connectmd-canonical-max-utf8-bytes"
        ]
        == canonical_limit
    )
    assert (
        create["requestBody"]["content"]["text/markdown"]["schema"][
            "x-connectmd-canonical-max-utf8-bytes"
        ]
        == canonical_limit
    )
    assert str(canonical_limit) in create["responses"]["413"]["description"]
    proposal_markdown_schema = document["components"]["schemas"]["AgentProposalCreateRequest"][
        "properties"
    ]["markdown"]
    assert proposal_markdown_schema["x-connectmd-canonical-max-utf8-bytes"] == canonical_limit
    proposal_accept = document["paths"]["/v1/proposals/{proposal_id}/{action}"]["post"]
    assert proposal_accept["x-connectmd-canonical-max-utf8-bytes"] == canonical_limit
    assert str(canonical_limit) in proposal_accept["responses"]["413"]["description"]
    assert create["security"] == [{"BearerAuth": []}]
    assert {parameter["name"]: parameter["required"] for parameter in create["parameters"]} == {
        "Idempotency-Key": True
    }
    assert create["responses"]["201"]["headers"]["X-Connectmd-Search"]["schema"] == {
        "type": "string",
        "enum": ["queued"],
    }
    assert document["paths"]["/v1/search"]["get"]["security"] == [
        {},
        {"BearerAuth": []},
    ]
    assert document["paths"]["/v1/search/query"]["post"]["security"] == [
        {},
        {"BearerAuth": []},
    ]
    assert (
        document["paths"]["/v1/search"]["get"]["summary"]
        == "Search public current profiles and resumes"
    )
    search_parameters = {
        parameter["name"]: parameter
        for parameter in document["paths"]["/v1/search"]["get"]["parameters"]
    }
    agent_capability_schema = search_parameters["agent_capability"]["schema"]
    assert any(
        branch.get("const") == "internal_contact_request"
        for branch in agent_capability_schema.get("anyOf", [agent_capability_schema])
    )
    location_id_schema = search_parameters["location_id"]["schema"]
    assert location_id_schema.get("type") != "array"
    assert "maxItems" not in location_id_schema
    location_id_string_schema = next(
        branch
        for branch in location_id_schema.get("anyOf", [location_id_schema])
        if branch.get("type") == "string"
    )
    assert location_id_string_schema["maxLength"] == 80
    assert search_parameters["skill_ids"]["schema"]["type"] == "array"
    assert search_parameters["skill_ids"]["schema"]["maxItems"] == 50
    assert document["paths"]["/v1/profiles/{handle}"]["get"]["security"] == [
        {},
        {"BearerAuth": []},
    ]
    assert document["paths"]["/v1/api-keys"]["post"]["security"] == [{"ClerkBearerAuth": []}]
    assert {"413", "415", "422", "503"}.issubset(
        document["paths"]["/v1/ingest"]["post"]["responses"]
    )
    assert "/v1/resumes/{slug}" in document["paths"]

    required_key_routes = {
        ("post", "/v1/organizations"): False,
        ("put", "/v1/organizations/{organization_slug}"): True,
        (
            "post",
            "/v1/organizations/{organization_slug}/verification-submissions",
        ): False,
        ("post", "/v1/organizations/{organization_slug}/jobs"): False,
        ("put", "/v1/organizations/{organization_slug}/jobs/{job_slug}"): True,
        (
            "post",
            "/v1/organizations/{organization_slug}/jobs/{job_slug}/lifecycle/{action}",
        ): True,
        (
            "post",
            "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications",
        ): False,
        ("post", "/v1/connection-requests"): False,
        ("post", "/v1/connection-requests/{connection_request_id}/{action}"): False,
        ("delete", "/v1/connections/{connection_id}"): False,
        ("post", "/v1/connections/{connection_id}/block"): False,
        ("post", "/v1/conversations"): False,
        ("post", "/v1/conversations/{conversation_id}/messages"): False,
        ("post", "/v1/notifications/{notification_id}/read"): False,
    }
    for (method, path), requires_if_match in required_key_routes.items():
        operation = document["paths"][path][method]
        headers = {
            parameter["name"]: parameter
            for parameter in operation["parameters"]
            if parameter["in"] == "header"
        }
        key_parameters = [
            parameter
            for parameter in operation["parameters"]
            if parameter["in"] == "header" and parameter["name"] == "Idempotency-Key"
        ]
        assert len(key_parameters) == 1
        key_parameter = key_parameters[0]
        assert key_parameter["required"] is True
        assert key_parameter["schema"] == {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "pattern": main_module._IDEMPOTENCY_KEY_PATTERN,
        }
        if requires_if_match:
            if_match_parameters = [
                parameter
                for parameter in operation["parameters"]
                if parameter["in"] == "header" and parameter["name"] == "If-Match"
            ]
            assert len(if_match_parameters) == 1
            if_match_parameter = if_match_parameters[0]
            assert if_match_parameter["required"] is True
            assert if_match_parameter["schema"] == {
                "type": "string",
                "pattern": main_module.STRONG_DOCUMENT_ETAG_PATTERN,
            }
        else:
            assert "If-Match" not in headers

    recruiting_only_paths = {
        path for _, path in required_key_routes if path.startswith("/v1/organizations")
    } | {
        "/v1/organizations/{organization_slug}/jobs/{job_slug}/applications",
    }
    disabled_app = create_app(app.state.settings.model_copy(update={"recruiting_enabled": False}))
    try:
        disabled_schema = disabled_app.openapi()
        _assert_runtime_openapi_header_contract(disabled_app)
        assert recruiting_only_paths.isdisjoint(disabled_schema["paths"])
        for _, path in required_key_routes:
            if path not in recruiting_only_paths:
                assert path in disabled_schema["paths"]
    finally:
        await disabled_app.state.engine.dispose()

    for method, path in (
        ("post", "/v1/ingest"),
        ("post", "/v1/search/query"),
        ("delete", "/v1/agent-grants/{grant_id}"),
        ("delete", "/v1/agent-identities/{agent_handle}/mandates/{mandate_id}"),
    ):
        operation = document["paths"][path][method]
        assert all(
            parameter["name"] != "Idempotency-Key" for parameter in operation.get("parameters", [])
        )

    missing_social_key = await client.post(
        "/v1/connection-requests",
        json={"recipient_profile_handle": "not-a-real-profile"},
    )
    assert missing_social_key.status_code == 428


async def test_oversized_canonical_writes_fail_before_persistence_and_replay_size_oracle(
    api_client,
) -> None:
    app, client = api_client
    limit = canonical_document_max_utf8_bytes()
    oversized = profile_markdown() + "\n" + ("x" * limit)
    failed = await client.post(
        "/v1/profiles",
        json={"markdown": oversized},
        headers={"Idempotency-Key": "oversized-profile-create-0001"},
    )
    assert failed.status_code == 413, failed.text
    assert failed.json()["detail"] == (
        f"canonical Profile/Resume Markdown exceeds {limit} UTF-8 bytes"
    )
    async with app.state.session_factory() as session:
        assert (await session.scalars(select(Document))).all() == []
        assert (
            await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "oversized-profile-create-0001"
                )
            )
            is None
        )

    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown()},
        headers={"Idempotency-Key": "oversized-size-oracle-0001"},
    )
    assert created.status_code == 201, created.text
    oversized_replay = await client.post(
        "/v1/profiles",
        json={"markdown": oversized},
        headers={"Idempotency-Key": "oversized-size-oracle-0001"},
    )
    assert oversized_replay.status_code == 409, oversized_replay.text

    failed_update = await client.put(
        "/v1/profiles/ada-lovelace",
        json={"markdown": oversized},
        headers={
            "If-Match": created.headers["etag"],
            "Idempotency-Key": "oversized-profile-update-0001",
        },
    )
    assert failed_update.status_code == 413, failed_update.text
    async with app.state.session_factory() as session:
        document = await session.scalar(
            select(Document).where(Document.public_identifier == "ada-lovelace")
        )
        assert document is not None and document.current_version == 1
        assert (
            await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "oversized-profile-update-0001"
                )
            )
            is None
        )


async def test_http_proposal_submit_and_accept_apply_canonical_byte_limit(api_client) -> None:
    app, client = api_client
    limit = canonical_document_max_utf8_bytes()
    oversized = profile_markdown() + "\n" + ("x" * limit)
    created = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown()},
        headers={"Idempotency-Key": "proposal-size-base-0001"},
    )
    assert created.status_code == 201, created.text
    grant = await client.post(
        "/v1/agent-grants",
        json={
            "name": "Proposal size test",
            "mode": "proposal_only",
            "resource": {"type": "owner"},
            "scopes": ["documents:write"],
        },
        headers={"Idempotency-Key": "proposal-size-grant-0001"},
    )
    assert grant.status_code == 201, grant.text
    grant_headers = {"Authorization": f"Bearer {grant.json()['key']}"}
    app.dependency_overrides.clear()
    oversized_submit = await client.post(
        "/v1/proposals",
        json={
            "kind": "profile",
            "identifier": "ada-lovelace",
            "markdown": oversized,
            "if_match": created.headers["etag"],
        },
        headers={**grant_headers, "Idempotency-Key": "proposal-size-submit-0001"},
    )
    assert oversized_submit.status_code == 413, oversized_submit.text

    valid_submit = await client.post(
        "/v1/proposals",
        json={
            "kind": "profile",
            "identifier": "ada-lovelace",
            "markdown": profile_markdown(headline="Proposal accepted later"),
            "if_match": created.headers["etag"],
        },
        headers={**grant_headers, "Idempotency-Key": "proposal-size-valid-0001"},
    )
    assert valid_submit.status_code == 201, valid_submit.text
    proposal_id = valid_submit.json()["id"]
    async with app.state.session_factory() as session:
        proposal = await session.get(AgentProposal, proposal_id)
        assert proposal is not None
        proposal.markdown = oversized
        await session.commit()

    async def owner() -> Principal:
        return Principal(subject="user_test", method="clerk_jwt", scopes=frozenset({"*"}))

    app.dependency_overrides[require_principal] = owner
    oversized_accept = await client.post(
        f"/v1/proposals/{proposal_id}/accept",
        headers={"Idempotency-Key": "proposal-size-accept-0001"},
    )
    assert oversized_accept.status_code == 413, oversized_accept.text
    async with app.state.session_factory() as session:
        document = await session.scalar(
            select(Document).where(Document.public_identifier == "ada-lovelace")
        )
        assert document is not None and document.current_version == 1
        assert (
            await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "proposal-size-accept-0001"
                )
            )
            is None
        )
