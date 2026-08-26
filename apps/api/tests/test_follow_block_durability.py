from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from app.auth import Principal, optional_principal, require_principal
from app.models import (
    ChangeEvent,
    Document,
    FollowRateBucket,
    IdempotencyRecord,
    PostContentBlock,
    ProfileFollow,
)

from .helpers import profile_markdown


def human(subject: str) -> Principal:
    return Principal(subject=subject, method="clerk_jwt", scopes=frozenset({"*"}))


def as_principal(app, value: Principal) -> None:
    async def current() -> Principal:
        return value

    app.dependency_overrides[require_principal] = current
    app.dependency_overrides[optional_principal] = current


async def create_profile(client, app, owner: str, handle: str) -> None:
    as_principal(app, human(owner))
    response = await client.post(
        "/v1/profiles",
        json={"markdown": profile_markdown(visibility="public").replace("ada-lovelace", handle)},
        headers={"Idempotency-Key": f"social-profile-{handle}"},
    )
    assert response.status_code == 201, response.text


def key_header(value: str) -> dict[str, str]:
    return {"Idempotency-Key": value}


def assert_social_unavailable(response, *forbidden: str) -> None:
    assert response.status_code == 503, response.text
    lowered = response.text.lower()
    for marker in (*forbidden, "pepper", "sha256", "hash"):
        assert marker.lower() not in lowered


def assert_same_headers_without_replay(first, replay) -> None:
    def stable_headers(response) -> dict[str, str]:
        return {
            name.lower(): value
            for name, value in response.headers.items()
            if name.lower() not in {"idempotency-replayed", "x-request-id"}
        }

    assert stable_headers(first) == stable_headers(replay)


@pytest.mark.asyncio
async def test_social_mutations_require_visible_ascii_keys_and_advertise_them(api_client) -> None:
    app, client = api_client
    await create_profile(client, app, "target", "target-profile")
    as_principal(app, human("reader"))
    operations = (
        ("POST", "/v1/follows/target-profile"),
        ("DELETE", "/v1/follows/target-profile"),
        ("POST", "/v1/content-blocks/target-profile"),
        ("DELETE", "/v1/content-blocks/target-profile"),
    )
    for method, path in operations:
        missing = await client.request(method, path)
        assert missing.status_code == 428, missing.text
        invalid = await client.request(method, path, headers=key_header("bad key"))
        assert invalid.status_code == 400, invalid.text
        overlong = await client.request(method, path, headers=key_header("x" * 129))
        assert overlong.status_code == 400, overlong.text

    async with app.state.session_factory() as session:
        assert (await session.scalars(select(ProfileFollow))).all() == []
        assert (await session.scalars(select(PostContentBlock))).all() == []
        records = (await session.scalars(select(IdempotencyRecord))).all()
        assert not any(
            record.resource_type in {"social_follow", "social_content_block"} for record in records
        )

    openapi = app.openapi()["paths"]
    for path, method in (
        ("/v1/follows/{profile_handle}", "post"),
        ("/v1/follows/{profile_handle}", "delete"),
        ("/v1/content-blocks/{profile_handle}", "post"),
        ("/v1/content-blocks/{profile_handle}", "delete"),
    ):
        parameter = next(
            item
            for item in openapi[path][method]["parameters"]
            if item["name"] == "Idempotency-Key"
        )
        assert parameter["required"] is True
        assert parameter["schema"] == {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "pattern": r"^[\x21-\x7E]{1,128}$",
        }


@pytest.mark.asyncio
async def test_follow_exact_replay_quota_and_noop_unfollow_are_durable(api_client) -> None:
    app, client = api_client
    await create_profile(client, app, "target", "target-profile")
    as_principal(app, human("reader"))

    first = await client.post("/v1/follows/target-profile", headers=key_header("follow-exact-0001"))
    replay = await client.post(
        "/v1/follows/target-profile", headers=key_header("follow-exact-0001")
    )
    duplicate = await client.post(
        "/v1/follows/target-profile", headers=key_header("follow-duplicate-0001")
    )
    assert first.status_code == replay.status_code == duplicate.status_code == 200
    assert replay.json() == first.json() == duplicate.json()
    assert replay.headers["idempotency-replayed"] == "true"
    assert_same_headers_without_replay(first, replay)
    assert_same_headers_without_replay(first, duplicate)

    async with app.state.session_factory() as session:
        follows = (await session.scalars(select(ProfileFollow))).all()
        bucket = await session.scalar(
            select(FollowRateBucket).where(FollowRateBucket.owner_id == "reader")
        )
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == "reader",
                    IdempotencyRecord.resource_type == "social_follow",
                )
            )
        ).all()
        assert len(follows) == 1
        assert bucket is not None and bucket.follow_count == 1
        assert len(receipts) == 2
        assert all(receipt.response_body for receipt in receipts)

    removed = await client.delete(
        "/v1/follows/target-profile", headers=key_header("unfollow-exact-0001")
    )
    removed_replay = await client.delete(
        "/v1/follows/target-profile", headers=key_header("unfollow-exact-0001")
    )
    noop = await client.delete(
        "/v1/follows/target-profile", headers=key_header("unfollow-noop-0001")
    )
    noop_replay = await client.delete(
        "/v1/follows/target-profile", headers=key_header("unfollow-noop-0001")
    )
    assert (
        removed.status_code
        == removed_replay.status_code
        == noop.status_code
        == noop_replay.status_code
        == 204
    )
    assert removed.text == removed_replay.text == noop.text == noop_replay.text == ""
    assert removed_replay.headers["idempotency-replayed"] == "true"
    assert noop_replay.headers["idempotency-replayed"] == "true"
    assert_same_headers_without_replay(removed, removed_replay)
    assert_same_headers_without_replay(noop, noop_replay)


@pytest.mark.asyncio
async def test_block_receipt_binds_both_follow_side_effects_and_unblock(api_client) -> None:
    app, client = api_client
    await create_profile(client, app, "reader", "reader-profile")
    await create_profile(client, app, "target", "target-profile")

    as_principal(app, human("reader"))
    assert (
        await client.post("/v1/follows/target-profile", headers=key_header("reader-follow-0001"))
    ).status_code == 200
    as_principal(app, human("target"))
    assert (
        await client.post("/v1/follows/reader-profile", headers=key_header("target-follow-0001"))
    ).status_code == 200

    as_principal(app, human("reader"))
    blocked = await client.post(
        "/v1/content-blocks/target-profile", headers=key_header("block-exact-0001")
    )
    blocked_replay = await client.post(
        "/v1/content-blocks/target-profile", headers=key_header("block-exact-0001")
    )
    assert blocked.status_code == blocked_replay.status_code == 204
    assert blocked.text == blocked_replay.text == ""
    assert blocked_replay.headers["idempotency-replayed"] == "true"
    assert_same_headers_without_replay(blocked, blocked_replay)

    async with app.state.session_factory() as session:
        assert len((await session.scalars(select(PostContentBlock))).all()) == 1
        assert (await session.scalars(select(ProfileFollow))).all() == []
        receipt = await session.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == "block-exact-0001")
        )
        assert receipt is not None
        assert receipt.response_body == ""
        assert "reader" not in receipt.resource_id
        assert "target" not in receipt.resource_id
        assert (
            await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.event_type.in_({"social.followed", "social.blocked"})
                )
            )
        ).all() == []

    blocked_follow = await client.post(
        "/v1/follows/target-profile", headers=key_header("blocked-follow-0001")
    )
    assert blocked_follow.status_code == 404
    unblocked = await client.delete(
        "/v1/content-blocks/target-profile", headers=key_header("unblock-exact-0001")
    )
    unblocked_replay = await client.delete(
        "/v1/content-blocks/target-profile", headers=key_header("unblock-exact-0001")
    )
    assert unblocked.status_code == unblocked_replay.status_code == 204
    assert unblocked_replay.headers["idempotency-replayed"] == "true"
    assert_same_headers_without_replay(unblocked, unblocked_replay)
    assert (
        await client.post(
            "/v1/follows/target-profile", headers=key_header("follow-after-unblock-0001")
        )
    ).status_code == 200


@pytest.mark.asyncio
async def test_social_replay_precedes_target_not_found_and_collisions_are_bounded(
    api_client,
) -> None:
    app, client = api_client
    await create_profile(client, app, "target", "target-profile")
    await create_profile(client, app, "other", "other-profile")
    as_principal(app, human("reader"))
    first = await client.post(
        "/v1/follows/target-profile", headers=key_header("social-replay-order-0001")
    )
    assert first.status_code == 200
    async with app.state.session_factory() as session:
        target = await session.scalar(
            select(Document).where(Document.public_identifier == "target-profile")
        )
        assert target is not None
        target.visibility = "private"
        await session.commit()
    unavailable = await client.post(
        "/v1/follows/target-profile", headers=key_header("social-replay-order-0001")
    )
    assert unavailable.status_code == 503
    assert "reader" not in unavailable.text
    assert "social-replay-order-0001" not in unavailable.text

    collision_handle = await client.post(
        "/v1/follows/other-profile", headers=key_header("social-replay-order-0001")
    )
    collision_method = await client.delete(
        "/v1/follows/target-profile", headers=key_header("social-replay-order-0001")
    )
    assert collision_handle.status_code == collision_method.status_code == 409


@pytest.mark.asyncio
async def test_social_receipt_corruption_and_target_substitution_fail_closed(api_client) -> None:
    app, client = api_client
    await create_profile(client, app, "target", "target-profile")
    await create_profile(client, app, "other", "other-profile")
    await create_profile(client, app, "clean-target", "clean-target-profile")
    await create_profile(client, app, "document-target", "document-target-profile")
    await create_profile(client, app, "handle-target", "handle-target-profile")
    as_principal(app, human("reader"))

    first = await client.post(
        "/v1/follows/target-profile", headers=key_header("social-corrupt-body-0001")
    )
    assert first.status_code == 200
    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "social-corrupt-body-0001"
            )
        )
        assert receipt is not None
        receipt.response_body = "{}"
        await session.commit()
    corrupt_body = await client.post(
        "/v1/follows/target-profile", headers=key_header("social-corrupt-body-0001")
    )
    assert corrupt_body.status_code == 503
    assert "reader" not in corrupt_body.text

    second = await client.post(
        "/v1/follows/other-profile", headers=key_header("social-corrupt-resource-0001")
    )
    assert second.status_code == 200
    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "social-corrupt-resource-0001"
            )
        )
        assert receipt is not None
        receipt.resource_type = "social_content_block"
        await session.commit()
    corrupt_resource = await client.post(
        "/v1/follows/other-profile", headers=key_header("social-corrupt-resource-0001")
    )
    assert corrupt_resource.status_code == 503

    clean = await client.post(
        "/v1/follows/clean-target-profile",
        headers=key_header("social-clean-target-0001"),
    )
    assert clean.status_code == 200
    async with app.state.session_factory() as session:
        clean_receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "social-clean-target-0001"
            )
        )
        clean_target = await session.scalar(
            select(Document).where(Document.public_identifier == "clean-target-profile")
        )
        assert clean_receipt is not None and clean_target is not None
        clean_resource_id = clean_receipt.resource_id or ""
        clean_target.owner_id = "substituted-owner"
        await session.commit()
    substituted = await client.post(
        "/v1/follows/clean-target-profile",
        headers=key_header("social-clean-target-0001"),
    )
    assert substituted.status_code == 503
    assert "substituted-owner" not in substituted.text
    assert clean_resource_id not in substituted.text

    document_follow = await client.post(
        "/v1/follows/document-target-profile",
        headers=key_header("social-document-target-0001"),
    )
    assert document_follow.status_code == 200
    async with app.state.session_factory() as session:
        document_receipt = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "social-document-target-0001"
            )
        )
        document_target = await session.scalar(
            select(Document).where(Document.public_identifier == "document-target-profile")
        )
        other_target = await session.scalar(
            select(Document).where(Document.public_identifier == "other-profile")
        )
        assert document_receipt is not None and document_target is not None
        assert other_target is not None and document_receipt.resource_id is not None
        document_resource_parts = document_receipt.resource_id.rsplit(":", 1)
        document_receipt.resource_id = (
            f"{document_resource_parts[0].rsplit(':', 1)[0]}:{other_target.id}:"
            f"{document_resource_parts[1]}"
        )
        document_resource_id = document_receipt.resource_id
        await session.commit()
    document_substituted = await client.post(
        "/v1/follows/document-target-profile",
        headers=key_header("social-document-target-0001"),
    )
    assert_social_unavailable(
        document_substituted, "reader", "social-document-target-0001", document_resource_id
    )

    handle_follow = await client.post(
        "/v1/follows/handle-target-profile",
        headers=key_header("social-handle-target-0001"),
    )
    assert handle_follow.status_code == 200
    async with app.state.session_factory() as session:
        handle_target = await session.scalar(
            select(Document).where(Document.public_identifier == "handle-target-profile")
        )
        assert handle_target is not None
        handle_target.public_identifier = "substituted-profile"
        await session.commit()
    handle_substituted = await client.post(
        "/v1/follows/handle-target-profile",
        headers=key_header("social-handle-target-0001"),
    )
    assert_social_unavailable(handle_substituted, "reader", "social-handle-target-0001")


@pytest.mark.parametrize(
    "corruption",
    (
        "response_status",
        "response_body",
        "response_headers",
        "resource_type",
        "malformed_resource_id",
        "resource_digest",
    ),
)
@pytest.mark.asyncio
async def test_social_follow_receipt_metadata_corruption_fails_closed(
    api_client, corruption: str
) -> None:
    app, client = api_client
    await create_profile(client, app, "target", "target-profile")
    as_principal(app, human("reader"))
    key = f"social-corrupt-{corruption}-0001"
    first = await client.post("/v1/follows/target-profile", headers=key_header(key))
    assert first.status_code == 200, first.text
    async with app.state.session_factory() as session:
        receipt = await session.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == key)
        )
        assert receipt is not None and receipt.resource_id is not None
        original_resource_id = receipt.resource_id
        if corruption == "response_status":
            receipt.response_status = 204
        elif corruption == "response_body":
            receipt.response_body = "{}"
        elif corruption == "response_headers":
            receipt.response_headers = '{"X-Private-Graph": "reader"}'
        elif corruption == "resource_type":
            receipt.resource_type = "social_content_block"
        elif corruption == "malformed_resource_id":
            receipt.resource_id = "not-a-social-receipt"
        else:
            digest = original_resource_id.rsplit(":", 1)[1]
            replacement = ("0" if digest[0] != "0" else "1") + digest[1:]
            receipt.resource_id = f"{original_resource_id.rsplit(':', 1)[0]}:{replacement}"
        await session.commit()
    replay = await client.post("/v1/follows/target-profile", headers=key_header(key))
    assert_social_unavailable(replay, "reader", key, original_resource_id)


@pytest.mark.parametrize(
    "case",
    (
        "missing_follow",
        "follow_opposite_transition",
        "missing_block",
        "block_opposite_transition",
        "block_follow_side_effect",
    ),
)
@pytest.mark.asyncio
async def test_social_receipt_result_state_corruption_fails_closed(api_client, case: str) -> None:
    app, client = api_client
    await create_profile(client, app, "reader", "reader-profile")
    await create_profile(client, app, "target", "target-profile")
    as_principal(app, human("reader"))

    if case.startswith("follow") or case == "missing_follow":
        key = f"social-state-{case}-0001"
        created = await client.post("/v1/follows/target-profile", headers=key_header(key))
        assert created.status_code == 200, created.text
        if case == "missing_follow":
            async with app.state.session_factory() as session:
                await session.execute(
                    delete(ProfileFollow).where(
                        ProfileFollow.follower_owner_id == "reader",
                        ProfileFollow.followed_owner_id == "target",
                    )
                )
                await session.commit()
        else:
            cleared = await client.delete(
                "/v1/follows/target-profile",
                headers=key_header("social-state-follow-clear-0001"),
            )
            assert cleared.status_code == 204, cleared.text
        replay = await client.post("/v1/follows/target-profile", headers=key_header(key))
    else:
        key = f"social-state-{case}-0001"
        if case == "block_follow_side_effect":
            as_principal(app, human("target"))
            reverse = await client.post(
                "/v1/follows/reader-profile",
                headers=key_header("social-state-reverse-follow-0001"),
            )
            assert reverse.status_code == 200, reverse.text
            as_principal(app, human("reader"))
            direct = await client.post(
                "/v1/follows/target-profile",
                headers=key_header("social-state-direct-follow-0001"),
            )
            assert direct.status_code == 200, direct.text
        created = await client.post("/v1/content-blocks/target-profile", headers=key_header(key))
        assert created.status_code == 204, created.text
        if case == "missing_block":
            async with app.state.session_factory() as session:
                await session.execute(
                    delete(PostContentBlock).where(
                        PostContentBlock.blocker_owner_id == "reader",
                        PostContentBlock.blocked_owner_id == "target",
                    )
                )
                await session.commit()
        elif case == "block_opposite_transition":
            cleared = await client.delete(
                "/v1/content-blocks/target-profile",
                headers=key_header("social-state-block-clear-0001"),
            )
            assert cleared.status_code == 204, cleared.text
        else:
            async with app.state.session_factory() as session:
                session.add(
                    ProfileFollow(
                        follower_owner_id="reader",
                        followed_owner_id="target",
                        followed_profile_handle="target-profile",
                        created_at=datetime.now(UTC),
                    )
                )
                await session.commit()
        replay = await client.post("/v1/content-blocks/target-profile", headers=key_header(key))
    assert_social_unavailable(replay, "reader", key)


@pytest.mark.asyncio
async def test_same_key_block_gather_has_one_atomic_effect_and_receipt(api_client) -> None:
    app, client = api_client
    await create_profile(client, app, "reader", "reader-profile")
    await create_profile(client, app, "target", "target-profile")
    as_principal(app, human("reader"))
    first_follow = await client.post(
        "/v1/follows/target-profile", headers=key_header("social-block-gather-follow-0001")
    )
    assert first_follow.status_code == 200, first_follow.text
    as_principal(app, human("target"))
    reverse_follow = await client.post(
        "/v1/follows/reader-profile", headers=key_header("social-block-gather-reverse-0001")
    )
    assert reverse_follow.status_code == 200, reverse_follow.text
    as_principal(app, human("reader"))
    key = "social-block-gather-0001"
    responses = await asyncio.gather(
        client.post("/v1/content-blocks/target-profile", headers=key_header(key)),
        client.post("/v1/content-blocks/target-profile", headers=key_header(key)),
    )
    assert all(response.status_code == 204 for response in responses), [
        response.text for response in responses
    ]
    assert (
        sum(response.headers.get("idempotency-replayed") == "true" for response in responses) == 1
    )
    async with app.state.session_factory() as session:
        assert len((await session.scalars(select(PostContentBlock))).all()) == 1
        assert (await session.scalars(select(ProfileFollow))).all() == []
        receipts = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == key,
                    IdempotencyRecord.resource_type == "social_content_block",
                )
            )
        ).all()
        assert len(receipts) == 1


@pytest.mark.asyncio
async def test_rejected_social_requests_and_noop_replay_state_are_not_recipted(api_client) -> None:
    app, client = api_client
    await create_profile(client, app, "reader", "reader-profile")
    await create_profile(client, app, "target", "target-profile")
    as_principal(app, human("reader"))
    invalid_key = await client.post("/v1/follows/target-profile", headers=key_header("bad key"))
    assert invalid_key.status_code == 400
    self_follow = await client.post(
        "/v1/follows/reader-profile", headers=key_header("social-self-follow-0001")
    )
    self_block = await client.post(
        "/v1/content-blocks/reader-profile", headers=key_header("social-self-block-0001")
    )
    assert self_follow.status_code == self_block.status_code == 409

    blocked = await client.post(
        "/v1/content-blocks/target-profile", headers=key_header("social-rejection-block-0001")
    )
    assert blocked.status_code == 204
    blocked_follow_key = "social-rejected-follow-0001"
    blocked_follow = await client.post(
        "/v1/follows/target-profile", headers=key_header(blocked_follow_key)
    )
    assert blocked_follow.status_code == 404

    noop_unfollow_key = "social-noop-unfollow-0001"
    noop_unfollow = await client.delete(
        "/v1/follows/target-profile", headers=key_header(noop_unfollow_key)
    )
    assert noop_unfollow.status_code == 204
    unblocked = await client.delete(
        "/v1/content-blocks/target-profile", headers=key_header("social-rejection-unblock-0001")
    )
    assert unblocked.status_code == 204
    created_follow = await client.post(
        "/v1/follows/target-profile", headers=key_header("social-after-noop-follow-0001")
    )
    assert created_follow.status_code == 200
    noop_unfollow_replay = await client.delete(
        "/v1/follows/target-profile", headers=key_header(noop_unfollow_key)
    )
    assert_social_unavailable(noop_unfollow_replay, "reader", noop_unfollow_key)

    noop_unblock_key = "social-noop-unblock-0001"
    noop_unblock = await client.delete(
        "/v1/content-blocks/target-profile", headers=key_header(noop_unblock_key)
    )
    assert noop_unblock.status_code == 204
    block_again = await client.post(
        "/v1/content-blocks/target-profile", headers=key_header("social-after-noop-block-0001")
    )
    assert block_again.status_code == 204
    noop_unblock_replay = await client.delete(
        "/v1/content-blocks/target-profile", headers=key_header(noop_unblock_key)
    )
    assert_social_unavailable(noop_unblock_replay, "reader", noop_unblock_key)

    async with app.state.session_factory() as session:
        records = (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key.in_(
                        {
                            "bad key",
                            "social-self-follow-0001",
                            "social-self-block-0001",
                            blocked_follow_key,
                        }
                    )
                )
            )
        ).all()
        assert records == []


@pytest.mark.asyncio
async def test_same_key_social_gather_has_one_effect_and_one_receipt(api_client) -> None:
    app, client = api_client
    await create_profile(client, app, "target", "target-profile")
    as_principal(app, human("reader"))
    responses = await asyncio.gather(
        client.post("/v1/follows/target-profile", headers=key_header("social-gather-0001")),
        client.post("/v1/follows/target-profile", headers=key_header("social-gather-0001")),
    )
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json() == responses[1].json()
    assert (
        sum(response.headers.get("idempotency-replayed") == "true" for response in responses) == 1
    )
    async with app.state.session_factory() as session:
        assert len((await session.scalars(select(ProfileFollow))).all()) == 1
        assert (
            len(
                (
                    await session.scalars(
                        select(IdempotencyRecord).where(
                            IdempotencyRecord.idempotency_key == "social-gather-0001"
                        )
                    )
                ).all()
            )
            == 1
        )


@pytest.mark.asyncio
async def test_social_discovery_excludes_graph_writes(api_client) -> None:
    app, client = api_client
    paths = app.openapi()["paths"]
    assert paths["/v1/follows/{profile_handle}"]["post"]["x-connectmd-human-only"] is True
    assert paths["/v1/content-blocks/{profile_handle}"]["post"]["x-connectmd-human-only"] is True
    capabilities = (await client.get("/v1/capabilities")).json()["follows"]
    assert capabilities["human_only"] is True
    assert capabilities["public_counts_or_enumeration"] is False
    assert capabilities["mutation_idempotency"] == {
        "required_header": "Idempotency-Key",
        "pattern": r"^[\x21-\x7E]{1,128}$",
        "operations": [
            "POST /v1/follows/{profile_handle}",
            "DELETE /v1/follows/{profile_handle}",
            "POST /v1/content-blocks/{profile_handle}",
            "DELETE /v1/content-blocks/{profile_handle}",
        ],
        "replay": "exact safe response only; state or authority mismatch fails closed",
        "mcp_or_a2a_actions": False,
    }
    tools = await client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": "social-tools", "method": "tools/list"}
    )
    names = {tool["name"] for tool in tools.json()["result"]["tools"]}
    assert names.isdisjoint(
        {"follow_profile", "unfollow_profile", "block_profile_content", "unblock_profile_content"}
    )
    card = await client.get("/.well-known/agent-card.json")
    assert card.status_code == 200
    skill_ids = {skill["id"] for skill in card.json()["skills"]}
    assert skill_ids.isdisjoint(
        {"follow-profile", "unfollow-profile", "block-profile", "unblock-profile"}
    )
