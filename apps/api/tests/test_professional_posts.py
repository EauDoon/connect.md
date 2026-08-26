from __future__ import annotations

import asyncio

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, optional_principal, require_principal
from app.markdown import MarkdownValidationError, prepare_client_document, validate_canonical
from app.models import (
    ChangeEvent,
    Document,
    IdempotencyRecord,
    Post,
    PostContentBlock,
    PostReport,
    PostVersion,
    ProfileFollow,
)
from app.services.artifact_durability import (
    PROFESSIONAL_POST_CREATE_TARGET_ID,
    stage_artifact,
)

from .helpers import profile_markdown


def human(subject: str) -> Principal:
    return Principal(subject=subject, method="clerk_jwt", scopes=frozenset({"*"}))


def post_markdown(title: str = "A durable professional note") -> str:
    return f"""---
schema: connect.md/post
schema_version: 1
title: {title}
topics: [engineering, reliability]
visibility: public
---
# {title}

This is a bounded, immutable professional post.
"""


def profile_for(handle: str) -> str:
    return profile_markdown(visibility="public").replace("ada-lovelace", handle)


def as_principal(app, value: Principal) -> None:
    async def current() -> Principal:
        return value

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


async def create_profile(client, app, owner: str, handle: str) -> None:
    as_principal(app, human(owner))
    response = await client.post(
        "/v1/profiles",
        json={"markdown": profile_for(handle)},
        headers={"Idempotency-Key": f"professional-post-profile-create-{handle}"},
    )
    assert response.status_code == 201, response.text


async def test_post_oversized_input_fails_as_payload_too_large_before_persistence(
    api_client,
) -> None:
    app, client = api_client
    await create_profile(client, app, "clerk_post_size_owner", "post-size-owner")
    oversized = post_markdown() + ("x" * 131_072)

    response = await client.post(
        "/v1/posts",
        json={"markdown": oversized},
        headers={"Idempotency-Key": "post-oversized-input-0001"},
    )

    assert response.status_code == 413
    assert response.json()["detail"].startswith("canonical post Markdown exceeds")
    async with app.state.session_factory() as session:
        assert (
            await session.scalar(select(Post).where(Post.owner_id == "clerk_post_size_owner"))
            is None
        )
        assert (
            await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "post-oversized-input-0001"
                )
            )
            is None
        )


async def test_post_canonicalization_public_reads_and_lost_ack_receipt_recovery(api_client) -> None:
    app, client = api_client
    await create_profile(client, app, "clerk_author_secret", "author-profile")
    markdown = post_markdown()
    created = await client.post(
        "/v1/posts",
        json={"markdown": markdown},
        headers={"Idempotency-Key": "post-lost-ack-0001"},
    )
    assert created.status_code == 201, created.text
    assert created.headers.get("idempotency-replayed") is None
    payload = created.json()
    assert set(payload) == {
        "id",
        "author_profile_handle",
        "title",
        "topics",
        "version",
        "published_at",
        "updated_at",
        "markdown",
        "markdown_url",
        "etag",
    }
    assert "clerk_author_secret" not in payload["markdown"]
    assert payload["author_profile_handle"] == "author-profile"
    assert payload["version"] == 1
    assert created.headers["etag"] == payload["etag"]
    explicit_markdown = await client.get(f"/v1/posts/{payload['id']}.md")
    negotiated_markdown = await client.get(
        f"/v1/posts/{payload['id']}", headers={"Accept": "text/markdown"}
    )
    assert explicit_markdown.status_code == negotiated_markdown.status_code == 200
    assert explicit_markdown.text == negotiated_markdown.text == payload["markdown"]
    assert explicit_markdown.headers["content-type"].startswith("text/markdown")
    assert negotiated_markdown.headers["content-type"].startswith("text/markdown")

    # Simulate a lost acknowledgement after the durable post/receipt transaction:
    # the empty receipt must reconstruct only public-safe canonical data.
    async with app.state.session_factory() as session:
        record = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "post-lost-ack-0001"
            )
        )
        assert record is not None
        record.response_body = ""
        await session.commit()
    recovered = await client.post(
        "/v1/posts",
        json={"markdown": markdown},
        headers={"Idempotency-Key": "post-lost-ack-0001"},
    )
    assert recovered.status_code == 201, recovered.text
    assert recovered.headers["idempotency-replayed"] == "true"
    assert recovered.content == created.content
    assert recovered.json()["id"] == payload["id"]
    assert "clerk_author_secret" not in recovered.text

    withdrawn_create_key = "post-withdrawn-lost-ack-0001"
    withdrawn_create = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown("Withdrawn receipt")},
        headers={"Idempotency-Key": withdrawn_create_key},
    )
    assert withdrawn_create.status_code == 201, withdrawn_create.text
    async with app.state.session_factory() as session:
        record = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == withdrawn_create_key
            )
        )
        assert record is not None
        record.response_body = ""
        await session.commit()
    withdrawn = await client.delete(
        f"/v1/posts/{withdrawn_create.json()['id']}",
        headers={
            "Idempotency-Key": "post-withdrawn-state-0001",
            "If-Match": withdrawn_create.headers["etag"],
        },
    )
    assert withdrawn.status_code == 204
    assert withdrawn.content == b""
    assert "content-type" not in withdrawn.headers
    withdrawn_replay = await client.delete(
        f"/v1/posts/{withdrawn_create.json()['id']}",
        headers={
            "Idempotency-Key": "post-withdrawn-state-0001",
            "If-Match": withdrawn_create.headers["etag"],
        },
    )
    assert withdrawn_replay.status_code == 204
    assert withdrawn_replay.content == b""
    assert "content-type" not in withdrawn_replay.headers
    assert withdrawn_replay.headers["idempotency-replayed"] == "true"
    withdrawn_creation_replay = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown("Withdrawn receipt")},
        headers={"Idempotency-Key": withdrawn_create_key},
    )
    assert withdrawn_creation_replay.status_code == 503
    assert "idempotency-replayed" not in withdrawn_creation_replay.headers

    schema = app.openapi()
    create_operation = schema["paths"]["/v1/posts"]["post"]
    assert create_operation["security"] == [{"ClerkBearerAuth": []}]
    assert "requestBody" in create_operation
    assert create_operation["parameters"][0]["name"] == "Idempotency-Key"
    assert schema["paths"]["/v1/posts/{post_id}"]["get"]["security"] == [
        {},
        {"BearerAuth": []},
    ]
    negotiated_content = schema["paths"]["/v1/posts/{post_id}"]["get"]["responses"]["200"][
        "content"
    ]
    explicit_content = schema["paths"]["/v1/posts/{post_id}.md"]["get"]["responses"]["200"][
        "content"
    ]
    assert {"application/json", "text/markdown"}.issubset(negotiated_content)
    assert set(explicit_content) == {"text/markdown"}
    assert schema["paths"]["/v1/posts/{post_id}/report"]["post"]["security"] == [
        {"ClerkBearerAuth": []}
    ]


async def test_completed_post_create_receipt_replays_byte_identically_after_withdrawal(
    api_client,
) -> None:
    app, client = api_client
    owner = "completed-receipt-author"
    markdown = post_markdown("Completed receipt withdrawal")
    create_key = "completed-receipt-withdrawal-create"
    await create_profile(client, app, owner, "completed-receipt-profile")
    as_principal(app, human(owner))

    created = await client.post(
        "/v1/posts",
        json={"markdown": markdown},
        headers={"Idempotency-Key": create_key},
    )
    assert created.status_code == 201, created.text
    assert "idempotency-replayed" not in created.headers
    original_body = created.content

    withdrawn = await client.delete(
        f"/v1/posts/{created.json()['id']}",
        headers={
            "Idempotency-Key": "completed-receipt-withdrawal-delete",
            "If-Match": created.headers["etag"],
        },
    )
    assert withdrawn.status_code == 204, withdrawn.text

    replay = await client.post(
        "/v1/posts",
        json={"markdown": markdown},
        headers={"Idempotency-Key": create_key},
    )
    assert replay.status_code == 201, replay.text
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.content == original_body
    assert replay.headers["etag"] == created.headers["etag"]
    assert replay.headers["location"] == created.headers["location"]


async def test_post_schema_rejects_private_or_forged_server_content() -> None:
    with_private = post_markdown().replace("visibility: public", "visibility: private")
    try:
        prepare_client_document(
            "post",
            with_private,
            document_id="3e811ba3-8d22-4aaf-a49e-b5b3a03eef0f",
            owner_id="",
            version=1,
            author_profile_handle="author-profile",
        )
    except MarkdownValidationError as exc:
        assert "frontmatter validation failed" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("private posts must be rejected")
    forged = post_markdown().replace(
        "title: A durable", "id: 3e811ba3-8d22-4aaf-a49e-b5b3a03eef0f\ntitle: A durable"
    )
    try:
        prepare_client_document(
            "post",
            forged,
            document_id="3e811ba3-8d22-4aaf-a49e-b5b3a03eef0f",
            owner_id="",
            version=1,
            author_profile_handle="author-profile",
        )
    except MarkdownValidationError as exc:
        assert "server-assigned" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("forged post server fields must be rejected")
    canonical, _ = prepare_client_document(
        "post",
        post_markdown(),
        document_id="3e811ba3-8d22-4aaf-a49e-b5b3a03eef0f",
        owner_id="",
        version=1,
        author_profile_handle="author-profile",
    )
    frontmatter, _ = validate_canonical("post", canonical)
    assert set(frontmatter).isdisjoint({"owner_id"})


async def test_follow_block_feed_report_and_terminal_withdrawal(api_client) -> None:
    app, client = api_client
    await create_profile(client, app, "author", "author-profile")
    await create_profile(client, app, "reader", "reader-profile")

    as_principal(app, human("author"))
    first = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown("First post")},
        headers={"Idempotency-Key": "post-first-0001"},
    )
    second = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown("Second post")},
        headers={"Idempotency-Key": "post-second-0001"},
    )
    assert first.status_code == second.status_code == 201

    as_principal(app, human("reader"))
    follow = await client.post(
        "/v1/follows/author-profile", headers={"Idempotency-Key": "follow-author-0001"}
    )
    assert follow.status_code == 200, follow.text
    assert follow.json()["profile_handle"] == "author-profile"
    duplicate_follow = await client.post(
        "/v1/follows/author-profile",
        headers={"Idempotency-Key": "follow-author-duplicate-0001"},
    )
    assert duplicate_follow.status_code == 200
    page_one = await client.get("/v1/feed?limit=1")
    assert page_one.status_code == 200
    assert page_one.json()["posts"][0]["id"] == second.json()["id"]
    page_two = await client.get("/v1/feed", params={"cursor": page_one.json()["next_cursor"]})
    assert [item["id"] for item in page_two.json()["posts"]] == [first.json()["id"]]

    narrative = "private report narrative must never leave its ledger"
    report = await client.post(
        f"/v1/posts/{first.json()['id']}/report",
        json={"reason_code": "spam", "narrative": narrative},
        headers={"Idempotency-Key": "post-report-0001"},
    )
    assert report.status_code == 201, report.text
    assert narrative not in report.text
    replayed_report = await client.post(
        f"/v1/posts/{first.json()['id']}/report",
        json={"reason_code": "spam", "narrative": narrative},
        headers={"Idempotency-Key": "post-report-0001"},
    )
    assert replayed_report.status_code == 201, replayed_report.text
    assert replayed_report.headers["idempotency-replayed"] == "true"
    assert replayed_report.json() == report.json()
    async with app.state.session_factory() as session:
        report_row = await session.scalar(
            select(PostReport).where(PostReport.id == report.json()["id"])
        )
        receipt = await session.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == "post-report-0001")
        )
        assert report_row is not None and report_row.narrative == narrative
        assert receipt is not None and receipt.response_body == ""
        assert narrative not in receipt.response_body
        assert report.json()["reason_code"] not in receipt.response_body

    blocked = await client.post(
        "/v1/content-blocks/author-profile",
        headers={"Idempotency-Key": "block-author-0001"},
    )
    assert blocked.status_code == 204
    follows_after_block = await client.get("/v1/follows")
    assert follows_after_block.status_code == 200, follows_after_block.text
    assert follows_after_block.json()["follows"] == []
    assert (await client.get("/v1/feed")).json()["posts"] == []
    assert (await client.get("/v1/profiles/author-profile/posts")).status_code == 404
    assert (await client.get(f"/v1/posts/{first.json()['id']}")).status_code == 404

    async def anonymous() -> None:
        return None

    app.dependency_overrides[optional_principal] = anonymous
    assert (await client.get(f"/v1/posts/{first.json()['id']}")).status_code == 200
    assert (await client.get("/v1/profiles/author-profile/posts")).status_code == 200
    as_principal(app, human("reader"))
    assert (
        await client.delete(
            "/v1/content-blocks/author-profile",
            headers={"Idempotency-Key": "unblock-author-0001"},
        )
    ).status_code == 204
    assert (await client.get("/v1/follows")).json()["follows"] == []

    as_principal(app, human("author"))
    missing_if_match = await client.delete(
        f"/v1/posts/{first.json()['id']}", headers={"Idempotency-Key": "withdraw-0001"}
    )
    assert missing_if_match.status_code == 428
    withdrawn = await client.delete(
        f"/v1/posts/{first.json()['id']}",
        headers={"Idempotency-Key": "withdraw-0001", "If-Match": first.headers["etag"]},
    )
    assert withdrawn.status_code == 204
    app.dependency_overrides[optional_principal] = anonymous
    assert (await client.get(f"/v1/posts/{first.json()['id']}")).status_code == 404


async def test_follow_block_pair_lock_prevents_reappearance_after_unblock(api_client) -> None:
    app, client = api_client
    await create_profile(client, app, "author", "author-profile")
    await create_profile(client, app, "reader", "reader-profile")

    async def principal_from_header(request: Request) -> Principal:
        return human(request.headers["X-Test-Owner"])

    app.dependency_overrides[require_principal] = principal_from_header
    app.dependency_overrides[optional_principal] = principal_from_header
    follow, block = await asyncio.gather(
        client.post(
            "/v1/follows/author-profile",
            headers={"X-Test-Owner": "reader", "Idempotency-Key": "gather-follow-0001"},
        ),
        client.post(
            "/v1/content-blocks/author-profile",
            headers={"X-Test-Owner": "reader", "Idempotency-Key": "gather-block-0001"},
        ),
    )
    assert follow.status_code in {200, 404}, follow.text
    assert block.status_code == 204, block.text
    async with app.state.session_factory() as session:
        follows = (await session.scalars(select(ProfileFollow))).all()
        blocks = (await session.scalars(select(PostContentBlock))).all()
        assert follows == []
        assert len(blocks) == 1
    unblocked = await client.delete(
        "/v1/content-blocks/author-profile",
        headers={"X-Test-Owner": "reader", "Idempotency-Key": "gather-unblock-0001"},
    )
    assert unblocked.status_code == 204
    async with app.state.session_factory() as session:
        assert (await session.scalars(select(ProfileFollow))).all() == []


async def test_follow_is_bound_to_the_exact_public_author_profile(api_client) -> None:
    app, client = api_client
    await create_profile(client, app, "author", "author-profile")
    await create_profile(client, app, "author", "author-second")
    await create_profile(client, app, "reader", "reader-profile")

    as_principal(app, human("reader"))
    assert (
        await client.post(
            "/v1/follows/author-profile",
            headers={"Idempotency-Key": "follow-exact-profile-0001"},
        )
    ).status_code == 200
    as_principal(app, human("author"))
    second_profile_post = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown("Post from second profile")},
        headers={"Idempotency-Key": "second-profile-post-0001"},
    )
    assert second_profile_post.status_code == 201, second_profile_post.text
    assert second_profile_post.json()["author_profile_handle"] == "author-second"

    as_principal(app, human("reader"))
    assert (await client.get("/v1/feed")).json()["posts"] == []
    async with app.state.session_factory() as session:
        first_profile = await session.scalar(
            select(Document).where(Document.public_identifier == "author-profile")
        )
        assert first_profile is not None
        first_profile.visibility = "private"
        await session.commit()
    assert (await client.get("/v1/feed")).json()["posts"] == []
    assert (await client.get("/v1/follows")).json()["follows"] == []


async def test_post_quota_rejection_does_not_orphan_immutable_markdown(api_client) -> None:
    app, client = api_client
    await create_profile(client, app, "author", "author-profile")
    for number in range(10):
        created = await client.post(
            "/v1/posts",
            json={"markdown": post_markdown(f"Quota post {number}")},
            headers={"Idempotency-Key": f"post-quota-{number:02d}"},
        )
        assert created.status_code == 201, created.text
    posts_root = app.state.store.root / "posts"
    before = list(posts_root.rglob("*.md"))
    rejected = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown("Quota post eleven")},
        headers={"Idempotency-Key": "post-quota-eleven"},
    )
    assert rejected.status_code == 429
    assert len(list(posts_root.rglob("*.md"))) == len(before) == 10


async def test_post_precommit_failure_deletes_exact_canonical_and_stage(
    api_client, monkeypatch
) -> None:
    app, client = api_client
    await create_profile(client, app, "clerk_author_secret", "author-profile")
    original_commit = AsyncSession.commit

    async def fail_post_commit(session: AsyncSession) -> None:
        if any(isinstance(row, Post) for row in session.identity_map.values()):
            raise ConnectionError("simulated post precommit failure")
        await original_commit(session)

    monkeypatch.setattr(AsyncSession, "commit", fail_post_commit)
    failed = await client.post(
        "/v1/posts",
        json={"markdown": post_markdown("Precommit post")},
        headers={"Idempotency-Key": "post-precommit-durability"},
    )
    assert failed.status_code == 500
    assert failed.headers["content-type"].startswith("application/problem+json")
    assert failed.json()["detail"] == "an unexpected server error occurred"
    assert "post precommit failure" not in failed.text
    monkeypatch.setattr(AsyncSession, "commit", original_commit)
    async with app.state.session_factory() as session:
        assert (await session.scalars(select(Post))).all() == []
        assert (await session.scalars(select(PostVersion))).all() == []
        assert (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "post-precommit-durability"
                )
            )
        ).all() == []
    assert not list((app.state.store.root / "posts").rglob("*.md"))
    scan = app.state.store.scan_staged_artifacts()
    assert scan.descriptors == ()
    assert scan.incomplete_payloads == ()


async def test_post_commit_then_raise_preserves_one_graph_and_replays(
    api_client, monkeypatch
) -> None:
    app, client = api_client
    await create_profile(client, app, "clerk_author_secret", "author-profile")
    markdown = post_markdown("Lost acknowledgement post")
    original_commit = AsyncSession.commit

    async def commit_post_then_raise(session: AsyncSession) -> None:
        if any(isinstance(row, Post) for row in session.identity_map.values()):
            await original_commit(session)
            raise ConnectionError("simulated post commit acknowledgement loss")
        await original_commit(session)

    monkeypatch.setattr(AsyncSession, "commit", commit_post_then_raise)
    lost_acknowledgement = await client.post(
        "/v1/posts",
        json={"markdown": markdown},
        headers={"Idempotency-Key": "post-commit-ack-loss"},
    )
    assert lost_acknowledgement.status_code == 500
    assert lost_acknowledgement.headers["content-type"].startswith("application/problem+json")
    assert lost_acknowledgement.json()["detail"] == "an unexpected server error occurred"
    assert "acknowledgement loss" not in lost_acknowledgement.text
    monkeypatch.setattr(AsyncSession, "commit", original_commit)
    replay = await client.post(
        "/v1/posts",
        json={"markdown": markdown},
        headers={"Idempotency-Key": "post-commit-ack-loss"},
    )
    assert replay.status_code == 201, replay.text
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert "clerk_author_secret" not in replay.text
    async with app.state.session_factory() as session:
        posts = (await session.scalars(select(Post))).all()
        versions = (await session.scalars(select(PostVersion))).all()
        events = (
            await session.scalars(
                select(ChangeEvent).where(ChangeEvent.event_type == "post.published")
            )
        ).all()
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == "post-commit-ack-loss"
                )
            )
        ).all()
        assert len(posts) == len(versions) == len(events) == len(receipts) == 1
        assert posts[0].id == replay.json()["id"]
        assert versions[0].post_id == posts[0].id
        assert receipts[0].resource_id == posts[0].id
        canonical = app.state.store.read_verified(versions[0].storage_path, versions[0].sha256)
        assert canonical == replay.json()["markdown"]
        post_id = posts[0].id
        storage_path = versions[0].storage_path
        request_hash = receipts[0].request_hash
    completed_receipt_stage = stage_artifact(
        app.state.store,
        app.state.settings.api_key_pepper,
        flow="professional_post",
        owner_id="clerk_author_secret",
        target_id=PROFESSIONAL_POST_CREATE_TARGET_ID,
        idempotency_key="post-commit-ack-loss",
        request_hash=request_hash,
        canonical_path=storage_path,
        payload=canonical.encode("utf-8"),
        max_size_bytes=10_240,
        resource_id=post_id,
    )
    assert (
        await app.state.artifact_reconciler.reconcile_descriptor(
            completed_receipt_stage, respect_grace=False
        )
        == "committed"
    )
    scan = app.state.store.scan_staged_artifacts()
    assert scan.descriptors == ()
    assert scan.incomplete_payloads == ()
