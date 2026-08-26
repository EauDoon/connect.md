from __future__ import annotations

import json
from base64 import urlsafe_b64encode
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.auth import Principal, optional_principal, require_principal
from app.markdown import prepare_client_document, validate_canonical
from app.models import Document, Post, PostVersion
from app.schemas import PublicPostInventoryResponse, PublicPostSummary

from .helpers import profile_markdown


def human(subject: str) -> Principal:
    return Principal(subject=subject, method="clerk_jwt", scopes=frozenset({"*"}))


def post_markdown(title: str) -> str:
    return f"""---
schema: connect.md/post
schema_version: 1
title: {title}
topics: [engineering, reliability]
visibility: public
---
# {title}

The private post body must never be included in a public inventory response.
"""


def profile_for(handle: str) -> str:
    return profile_markdown(visibility="public").replace("ada-lovelace", handle)


def as_principal(app, principal: Principal) -> None:
    async def current() -> Principal:
        return principal

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


def as_anonymous(app) -> None:
    async def anonymous() -> None:
        return None

    app.dependency_overrides[optional_principal] = anonymous


async def create_profile(client, app, owner: str, handle: str) -> None:
    as_principal(app, human(owner))
    response = await client.post(
        "/v1/profiles",
        json={"markdown": profile_for(handle)},
        headers={"Idempotency-Key": f"public-post-inventory-profile-{handle}"},
    )
    assert response.status_code == 201, response.text


async def publish_post(client, app, owner: str, title: str) -> dict[str, object]:
    as_principal(app, human(owner))
    response = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown(title)},
        headers={"Idempotency-Key": f"public-post-inventory-{title.lower().replace(' ', '-')}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def retime_post(app, post_id: str, published_at: datetime) -> None:
    """Construct matching canonical bytes so a tied timestamp remains valid state."""
    async with app.state.session_factory() as session:
        post = await session.scalar(select(Post).where(Post.id == post_id))
        assert post is not None
        old_markdown = app.state.store.read_verified(post.storage_path, post.sha256)
        frontmatter, _ = validate_canonical("post", old_markdown)
        canonical, _ = prepare_client_document(
            "post",
            old_markdown,
            document_id=post.id,
            owner_id="",
            version=1,
            updated_at=post.updated_at
            if post.updated_at.tzinfo
            else post.updated_at.replace(tzinfo=UTC),
            expected_server_fields={
                "id": frontmatter["id"],
                "author_profile_handle": frontmatter["author_profile_handle"],
                "version": frontmatter["version"],
                "published_at": frontmatter["published_at"],
                "updated_at": frontmatter["updated_at"],
            },
            author_profile_handle=post.author_profile_handle,
            published_at=published_at,
        )
        digest = sha256(canonical.encode("utf-8")).hexdigest()
        (app.state.store.root / post.storage_path).write_text(
            canonical, encoding="utf-8", newline="\n"
        )
        post.published_at = published_at
        post.sha256 = digest
        version = await session.scalar(
            select(PostVersion).where(PostVersion.post_id == post.id, PostVersion.version == 1)
        )
        assert version is not None
        version.sha256 = digest
        await session.commit()


def public_cursor(*, scope: str = "public_posts", published_at: str, post_id: str) -> str:
    payload = {"v": 1, "scope": scope, "published_at": published_at, "id": post_id}
    return urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")


async def test_public_post_inventory_is_metadata_only_chronological_and_not_search_backed(
    api_client,
) -> None:
    app, client = api_client
    await create_profile(client, app, "author", "author-profile")
    first = await publish_post(client, app, "author", "First public post")
    second = await publish_post(client, app, "author", "Second public post")
    tied_at = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
    await retime_post(app, str(first["id"]), tied_at)
    await retime_post(app, str(second["id"]), tied_at)

    class SearchMustNotBeCalled:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"public post inventory unexpectedly accessed search.{name}")

    app.state.search = SearchMustNotBeCalled()
    result = await client.get("/v1/posts")
    assert result.status_code == 200, result.text
    payload = result.json()
    assert result.headers["cache-control"] == "no-store"
    assert payload["next_cursor"] is None
    assert [item["id"] for item in payload["items"]] == sorted(
        [str(first["id"]), str(second["id"])], reverse=True
    )
    assert set(payload["items"][0]) == {
        "id",
        "author_profile_handle",
        "title",
        "topics",
        "version",
        "published_at",
        "updated_at",
        "html_url",
        "markdown_url",
        "etag",
    }
    serialized = result.text
    for forbidden in (
        '"markdown"',
        '"owner_id"',
        '"storage_path"',
        '"sha256"',
        '"status"',
        '"report"',
        '"moderation"',
        "private post body",
    ):
        assert forbidden not in serialized.casefold()
    assert payload["items"][0]["html_url"] == f"/posts/{payload['items'][0]['id']}"
    assert payload["items"][0]["markdown_url"] == f"/v1/posts/{payload['items'][0]['id']}.md"


async def test_public_post_inventory_cursor_is_strict_and_raw_candidate_progresses(
    api_client,
) -> None:
    app, client = api_client
    for owner, handle, title in (
        ("first", "first-profile", "First post"),
        ("second", "second-profile", "Second post"),
        ("third", "third-profile", "Third post"),
    ):
        await create_profile(client, app, owner, handle)
        await publish_post(client, app, owner, title)
    async with app.state.session_factory() as session:
        newest = await session.scalar(
            select(Post).order_by(Post.published_at.desc(), Post.id.desc())
        )
        assert newest is not None
        private_profile = await session.scalar(
            select(Document).where(Document.id == newest.author_profile_document_id)
        )
        assert private_profile is not None
        private_profile.visibility = "private"
        await session.commit()

    page = await client.get("/v1/posts?limit=1")
    assert page.status_code == 200, page.text
    assert page.json()["items"] == []
    assert page.json()["next_cursor"] is not None
    continued = await client.get(
        "/v1/posts", params={"cursor": page.json()["next_cursor"], "limit": 1}
    )
    assert continued.status_code == 200, continued.text
    assert len(continued.json()["items"]) == 1

    assert (await client.get("/v1/posts?cursor=not-a-cursor")).status_code == 400
    cross_route = public_cursor(
        scope="feed", published_at="2026-08-06T12:00:00+00:00", post_id="post-id"
    )
    assert (await client.get("/v1/posts", params={"cursor": cross_route})).status_code == 400
    extra_payload = {
        "v": 1,
        "scope": "public_posts",
        "published_at": "2026-08-06T12:00:00+00:00",
        "id": "post-id",
        "extra": "not-accepted",
    }
    extra_key = (
        urlsafe_b64encode(json.dumps(extra_payload).encode("utf-8")).decode("ascii").rstrip("=")
    )
    assert (await client.get("/v1/posts", params={"cursor": extra_key})).status_code == 400
    naive = public_cursor(published_at="2026-08-06T12:00:00", post_id="post-id")
    assert (await client.get("/v1/posts", params={"cursor": naive})).status_code == 400
    assert (await client.get("/v1/posts", params={"cursor": "x" * 501})).status_code == 422

    async with app.state.session_factory() as session:
        first_profile = await session.scalar(
            select(Document).where(Document.public_identifier == "first-profile")
        )
        assert first_profile is not None
    archive_scope = f"profile_posts:{first_profile.id}"
    archive_naive = public_cursor(
        scope=archive_scope, published_at="2026-08-06T12:00:00", post_id="post-id"
    )
    assert (
        await client.get("/v1/profiles/first-profile/posts", params={"cursor": archive_naive})
    ).status_code == 400
    archive_empty_id = public_cursor(
        scope=archive_scope, published_at="2026-08-06T12:00:00+00:00", post_id=""
    )
    assert (
        await client.get("/v1/profiles/first-profile/posts", params={"cursor": archive_empty_id})
    ).status_code == 400
    assert (
        await client.get("/v1/profiles/first-profile/posts", params={"cursor": "x" * 501})
    ).status_code == 422


async def test_public_inventory_reauthorizes_detail_and_archive_eligibility(api_client) -> None:
    app, client = api_client
    await create_profile(client, app, "author", "author-profile")
    published = await publish_post(client, app, "author", "Eligibility post")
    post_id = str(published["id"])
    as_anonymous(app)
    assert (await client.get("/v1/posts")).json()["items"][0]["id"] == post_id
    assert (await client.get(f"/v1/posts/{post_id}")).status_code == 200
    assert (await client.get("/v1/profiles/author-profile/posts")).status_code == 200

    async with app.state.session_factory() as session:
        profile = await session.scalar(
            select(Document).where(Document.public_identifier == "author-profile")
        )
        assert profile is not None
        profile.visibility = "private"
        await session.commit()
    assert (await client.get("/v1/posts")).json()["items"] == []
    assert (await client.get(f"/v1/posts/{post_id}")).status_code == 404
    assert (await client.get("/v1/profiles/author-profile/posts")).status_code == 404

    async with app.state.session_factory() as session:
        profile = await session.scalar(
            select(Document).where(Document.public_identifier == "author-profile")
        )
        assert profile is not None
        profile.visibility = "public"
        post = await session.scalar(select(Post).where(Post.id == post_id))
        assert post is not None
        post.status = "withheld"
        await session.commit()
    assert (await client.get("/v1/posts")).json()["items"] == []
    assert (await client.get(f"/v1/posts/{post_id}")).status_code == 404

    async with app.state.session_factory() as session:
        post = await session.scalar(select(Post).where(Post.id == post_id))
        assert post is not None
        post.status = "withdrawn"
        await session.commit()
    assert (await client.get("/v1/posts")).json()["items"] == []

    async with app.state.session_factory() as session:
        post = await session.scalar(select(Post).where(Post.id == post_id))
        assert post is not None
        post.status = "published"
        await session.commit()
    restored = await client.get("/v1/posts")
    assert restored.status_code == 200
    assert restored.json()["items"][0]["id"] == post_id

    async with app.state.session_factory() as session:
        post = await session.scalar(select(Post).where(Post.id == post_id))
        assert post is not None
        post.owner_id = "mismatched-owner"
        await session.commit()
    assert (await client.get("/v1/posts")).json()["items"] == []
    assert (await client.get(f"/v1/posts/{post_id}")).status_code == 404
    mismatched_archive = await client.get("/v1/profiles/author-profile/posts")
    assert mismatched_archive.status_code == 200
    assert mismatched_archive.json()["posts"] == []


async def test_public_inventory_hides_owner_document_handle_mismatch_and_reports_do_not_sanction(
    api_client,
) -> None:
    app, client = api_client
    await create_profile(client, app, "author", "author-profile")
    published = await publish_post(client, app, "author", "Reported post")
    post_id = str(published["id"])
    await create_profile(client, app, "author", "other-profile")

    as_principal(app, human("reader"))
    report = await client.post(
        f"/v1/posts/{post_id}/report",
        json={"reason_code": "spam", "narrative": "private report text"},
        headers={"Idempotency-Key": "public-post-inventory-report-0001"},
    )
    assert report.status_code == 201, report.text
    as_anonymous(app)
    assert (await client.get("/v1/posts")).json()["items"][0]["id"] == post_id

    async with app.state.session_factory() as session:
        post = await session.scalar(select(Post).where(Post.id == post_id))
        other = await session.scalar(
            select(Document).where(Document.public_identifier == "other-profile")
        )
        assert post is not None and other is not None
        post.author_profile_document_id = other.id
        await session.commit()
    assert (await client.get("/v1/posts")).json()["items"] == []
    assert (await client.get(f"/v1/posts/{post_id}")).status_code == 404
    archive = await client.get("/v1/profiles/author-profile/posts")
    assert archive.status_code == 200
    assert archive.json()["posts"] == []


async def test_public_inventory_fails_the_whole_page_for_missing_or_corrupt_canonical_bytes(
    api_client,
) -> None:
    app, client = api_client
    await create_profile(client, app, "author", "author-profile")
    first = await publish_post(client, app, "author", "First integrity post")
    second = await publish_post(client, app, "author", "Second integrity post")
    async with app.state.session_factory() as session:
        post = await session.scalar(select(Post).where(Post.id == str(first["id"])))
        assert post is not None
        (app.state.store.root / post.storage_path).unlink()
    missing = await client.get("/v1/posts?limit=2")
    assert missing.status_code == 503
    assert "items" not in missing.text

    async with app.state.session_factory() as session:
        post = await session.scalar(select(Post).where(Post.id == str(second["id"])))
        assert post is not None
        (app.state.store.root / post.storage_path).write_text("corrupt", encoding="utf-8")
    corrupt = await client.get("/v1/posts?limit=2")
    assert corrupt.status_code == 503
    assert "items" not in corrupt.text


async def test_public_post_inventory_openapi_and_discovery_contract(api_client) -> None:
    app, client = api_client
    schema = app.openapi()
    operation = schema["paths"]["/v1/posts"]["get"]
    parameters = {parameter["name"]: parameter["schema"] for parameter in operation["parameters"]}
    assert parameters["limit"]["minimum"] == 1
    assert parameters["limit"]["maximum"] == 200
    cursor_schema = next(
        item for item in parameters["cursor"]["anyOf"] if item.get("type") == "string"
    )
    assert cursor_schema["minLength"] == 1
    assert cursor_schema["maxLength"] == 500
    assert operation.get("security") in (None, [])
    summary_schema = schema["components"]["schemas"]["PublicPostSummary"]
    assert summary_schema["properties"]["id"]["maxLength"] == 64
    assert summary_schema["properties"]["author_profile_handle"]["maxLength"] == 100
    assert summary_schema["properties"]["title"]["maxLength"] == 160
    assert summary_schema["properties"]["topics"]["maxItems"] == 10
    assert summary_schema["properties"]["topics"]["items"]["maxLength"] == 50
    assert summary_schema["properties"]["html_url"]["maxLength"] == 128
    assert summary_schema["properties"]["markdown_url"]["maxLength"] == 128
    assert summary_schema["properties"]["etag"]["minLength"] == 73
    assert summary_schema["properties"]["etag"]["maxLength"] == 73
    inventory_schema = schema["components"]["schemas"]["PublicPostInventoryResponse"]
    assert inventory_schema["properties"]["items"]["maxItems"] == 200
    with pytest.raises(ValueError):
        PublicPostInventoryResponse(items=[], next_cursor="x" * 501)
    with pytest.raises(ValueError):
        PublicPostSummary(
            id="post-id",
            author_profile_handle="author-profile",
            title="A title",
            topics=["engineering"] * 11,
            version=1,
            published_at=datetime(2026, 8, 6, tzinfo=UTC),
            updated_at=datetime(2026, 8, 6, tzinfo=UTC),
            html_url="/posts/post-id",
            markdown_url="/v1/posts/post-id.md",
            etag='"sha256-' + "a" * 64 + '"',
        )
    read_lock_sql = str(
        select(Document)
        .where(Document.id == "profile-id")
        .order_by(Document.id.asc())
        .with_for_update(read=True)
        .compile(dialect=postgresql.dialect())
    )
    assert "FOR SHARE" in read_lock_sql

    concise = await client.get("/llms.txt")
    complete = await client.get("/llms-full.txt")
    capabilities = await client.get("/v1/capabilities")
    assert concise.status_code == complete.status_code == capabilities.status_code == 200
    for body in (concise.text, complete.text):
        assert "GET /v1/posts?limit=&cursor=" in body
        assert "not a private feed" in body
        assert "Meilisearch" in body
    post_capability = capabilities.json()["posts"]
    assert post_capability["public_inventory"]["endpoint"] == "/v1/posts"
    assert post_capability["public_inventory"]["post_markdown_body_in_meilisearch"] is False
    workspace = Path(__file__).resolve().parents[3]
    for relative_path in (
        "docs/social-network.md",
        "docs/agent-interoperability.md",
        "docs/acceptance.md",
    ):
        body = (workspace / relative_path).read_text(encoding="utf-8")
        assert "GET /v1/posts?limit=&cursor=" in body
        assert "Meilisearch" in body
