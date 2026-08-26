from __future__ import annotations

import ast
import hashlib
import inspect
import textwrap
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, update

from app.markdown import prepare_client_document, validate_canonical
from app.models import (
    AgentIdentity,
    Document,
    DocumentVersion,
    PublicExactSearchDocumentSnapshot,
    PublicExactSearchProjectionState,
)
from app.services.exact_search import (
    EXACT_SEARCH_CONTRACT_DIGEST,
    ExactSearchCursorMalformed,
    ExactSearchCursorStale,
    ExactSearchResult,
    ExactSearchService,
    ExactSearchUnavailable,
    _snapshot_values,
)
from app.services.taxonomy import ResolvedSearchFilters

from .helpers import profile_markdown


@pytest.mark.asyncio
async def test_exact_search_never_falls_back_to_meili_when_unready_or_non_postgresql(
    api_client,
) -> None:
    app, client = api_client

    class ForbiddenProjection:
        calls = 0

        async def search(self, **_: object):
            self.calls += 1
            raise AssertionError("exact search must not call Meilisearch")

    projection = ForbiddenProjection()
    app.state.search = projection

    rest_get = await client.get("/v1/search", params={"mode": "exact", "q": "payments"})
    assert rest_get.status_code == 503
    rest_post = await client.post("/v1/search/query", json={"mode": "exact", "q": "payments"})
    assert rest_post.status_code == 503

    mcp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "exact-mcp-unready",
            "method": "tools/call",
            "params": {
                "name": "search_documents",
                "arguments": {"mode": "exact", "q": "payments"},
            },
        },
    )
    assert mcp.status_code == 200
    assert mcp.json()["result"]["isError"] is True
    assert mcp.json()["result"]["structuredContent"] == {
        "code": "service_unavailable",
        "message": "exact search is temporarily unavailable",
    }

    a2a = await client.post(
        "/a2a/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "messageId": "exact-a2a-unready",
                "role": "ROLE_USER",
                "parts": [
                    {
                        "data": {"action": "search", "mode": "exact", "q": "payments"},
                        "mediaType": "application/json",
                    }
                ],
            }
        },
    )
    assert a2a.status_code == 200
    task = a2a.json()["task"]
    assert task["status"]["state"] == "TASK_STATE_FAILED"
    assert task["artifacts"][0]["parts"][0]["data"] == {
        "error": {
            "code": "service_unavailable",
            "message": "public exact search is temporarily unavailable",
        }
    }
    assert projection.calls == 0


@pytest.mark.asyncio
async def test_exact_search_state_requires_ready_projection(api_client) -> None:
    app, _ = api_client
    async with app.state.session_factory() as session:
        session.add(
            PublicExactSearchProjectionState(
                scope="documents",
                revision=0,
                status="backfill_required",
                contract_digest=EXACT_SEARCH_CONTRACT_DIGEST,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()
        with pytest.raises(ExactSearchUnavailable):
            await app.state.exact_search.require_ready(session)


@pytest.mark.asyncio
async def test_exact_write_rechecks_locked_state_before_normal_mutation_but_allows_backfill(
    api_client, monkeypatch
) -> None:
    app, _ = api_client
    service = app.state.exact_search
    now = datetime.now(UTC)
    canonical, _ = prepare_client_document(
        "profile",
        profile_markdown(visibility="public"),
        document_id="00000000-0000-4000-8000-000000000011",
        owner_id="owner-race",
        version=1,
        updated_at=now,
    )
    frontmatter, _ = validate_canonical("profile", canonical)
    document = Document(
        id="00000000-0000-4000-8000-000000000011",
        kind="profile",
        owner_id="owner-race",
        public_identifier="ada-lovelace",
        visibility="public",
        schema_version=1,
        current_version=1,
        created_at=now,
        updated_at=now,
    )

    async with app.state.session_factory() as session:
        session.add(
            PublicExactSearchProjectionState(
                scope="documents",
                revision=0,
                status="ready",
                contract_digest=EXACT_SEARCH_CONTRACT_DIGEST,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    original_state = service._state
    transition = {"done": False}

    async def transition_after_lock(current_session, *, lock=False):
        state = await original_state(current_session, lock=lock)
        if lock and not transition["done"]:
            assert state is not None
            state.status = "building"
            state.contract_digest = "b" * 64
            await current_session.flush()
            transition["done"] = True
        return state

    monkeypatch.setattr(service, "_state", transition_after_lock)
    async with app.state.session_factory() as session:
        with pytest.raises(ExactSearchUnavailable, match="not ready"):
            await service.upsert_document(
                session,
                document=document,
                canonical=canonical,
                frontmatter=frontmatter,
                digest="a" * 64,
                document_version=1,
            )
        await session.rollback()
        assert await session.get(PublicExactSearchDocumentSnapshot, document.id) is None

    transition["done"] = False
    async with app.state.session_factory() as session:
        await service.remove_document(session, document.id)
        await session.rollback()

    monkeypatch.setattr(service, "_state", original_state)
    async with app.state.session_factory() as session:
        state = await session.get(PublicExactSearchProjectionState, "documents")
        assert state is not None
        state.status = "building"
        state.contract_digest = "b" * 64
        session.add(document)
        await session.commit()
        await service.upsert_document(
            session,
            document=document,
            canonical=canonical,
            frontmatter=frontmatter,
            digest="a" * 64,
            document_version=1,
            rebuild=True,
        )
        await session.commit()
        snapshot = await session.get(PublicExactSearchDocumentSnapshot, document.id)
        assert snapshot is not None
        assert snapshot.schema_version == 1


@pytest.mark.asyncio
async def test_exact_search_rejects_external_state_transition_at_final_scalar_check(
    api_client, monkeypatch
) -> None:
    """The SQLite interleaving releases its read transaction; it is not PG race proof."""

    app, _ = api_client
    service = app.state.exact_search
    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        session.add(
            PublicExactSearchProjectionState(
                scope="documents",
                revision=0,
                status="ready",
                contract_digest=EXACT_SEARCH_CONTRACT_DIGEST,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    async def ready(_session, *, require_postgresql=False):
        return await _session.get(PublicExactSearchProjectionState, "documents")

    monkeypatch.setattr(service, "require_ready", ready)
    original_digest = service._taxonomy_revision_digest
    digest_calls = 0

    async def digest(current_session):
        nonlocal digest_calls
        digest_calls += 1
        if digest_calls == 2:
            await current_session.rollback()
            async with app.state.session_factory() as other_session:
                await other_session.execute(
                    update(PublicExactSearchProjectionState)
                    .where(PublicExactSearchProjectionState.scope == "documents")
                    .values(revision=1, status="building")
                )
                await other_session.commit()
        return await original_digest(current_session)

    monkeypatch.setattr(service, "_taxonomy_revision_digest", digest)
    resolved = ResolvedSearchFilters(
        meili={}, canonical={}, filter_values={}, requested={}, installed=True
    )
    with pytest.raises(ExactSearchCursorStale, match="stale"):
        async with app.state.session_factory() as session:
            await service.search(
                session,
                arguments={
                    "mode": "exact",
                    "q": "",
                    "offset": 0,
                    "limit": 20,
                    "cursor": None,
                    "facet_limit": 100,
                },
                resolved=resolved,
            )


@pytest.mark.asyncio
async def test_exact_visibility_withdrawal_removes_snapshot_when_state_is_not_ready(
    api_client,
) -> None:
    app, _ = api_client
    service = app.state.exact_search
    now = datetime.now(UTC)
    document_id = "00000000-0000-4000-8000-000000000061"
    canonical, _ = prepare_client_document(
        "profile",
        profile_markdown(visibility="public"),
        document_id=document_id,
        owner_id="owner-visibility-withdrawal",
        version=1,
        updated_at=now,
    )
    frontmatter, _ = validate_canonical("profile", canonical)
    document = Document(
        id=document_id,
        kind="profile",
        owner_id="owner-visibility-withdrawal",
        public_identifier="visibility-withdrawal",
        visibility="public",
        schema_version=1,
        current_version=1,
        created_at=now,
        updated_at=now,
    )

    async with app.state.session_factory() as session:
        session.add(
            PublicExactSearchProjectionState(
                scope="documents",
                revision=0,
                status="ready",
                contract_digest=EXACT_SEARCH_CONTRACT_DIGEST,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(document)
        await session.commit()
        await service.upsert_document(
            session,
            document=document,
            canonical=canonical,
            frontmatter=frontmatter,
            digest="a" * 64,
            document_version=1,
        )
        await session.commit()
        assert await session.get(PublicExactSearchDocumentSnapshot, document_id) is not None

        document.visibility = "private"
        state = await session.get(PublicExactSearchProjectionState, "documents")
        assert state is not None
        state.status = "failed"
        await session.commit()
        await service.upsert_document(
            session,
            document=document,
            canonical=canonical,
            frontmatter=frontmatter,
            digest="a" * 64,
            document_version=1,
        )
        await session.commit()
        assert await session.get(PublicExactSearchDocumentSnapshot, document_id) is None
        state = await session.get(PublicExactSearchProjectionState, "documents")
        assert state is not None and state.revision == 2 and state.status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("state_status", ["backfill_required", "building", "failed"])
async def test_exact_lifecycle_and_erasure_removal_works_when_state_is_not_ready(
    api_client, state_status: str
) -> None:
    """Lifecycle concealment and account erasure share this destructive helper."""

    app, _ = api_client
    service = app.state.exact_search
    now = datetime.now(UTC)
    document_id = f"00000000-0000-4000-8000-00000000006{state_status[0]}"
    canonical, _ = prepare_client_document(
        "profile",
        profile_markdown(visibility="public"),
        document_id=document_id,
        owner_id="owner-lifecycle-erasure",
        version=1,
        updated_at=now,
    )
    frontmatter, _ = validate_canonical("profile", canonical)
    document = Document(
        id=document_id,
        kind="profile",
        owner_id="owner-lifecycle-erasure",
        public_identifier=f"lifecycle-erasure-{state_status}",
        visibility="public",
        schema_version=1,
        current_version=1,
        created_at=now,
        updated_at=now,
    )

    async with app.state.session_factory() as session:
        session.add(
            PublicExactSearchProjectionState(
                scope="documents",
                revision=0,
                status="ready",
                contract_digest=EXACT_SEARCH_CONTRACT_DIGEST,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(document)
        await session.commit()
        await service.upsert_document(
            session,
            document=document,
            canonical=canonical,
            frontmatter=frontmatter,
            digest="b" * 64,
            document_version=1,
        )
        await session.commit()
        state = await session.get(PublicExactSearchProjectionState, "documents")
        assert state is not None
        state.status = state_status
        await session.commit()
        await service.remove_document(session, document_id)
        await session.commit()
        assert await session.get(PublicExactSearchDocumentSnapshot, document_id) is None
        state = await session.get(PublicExactSearchProjectionState, "documents")
        assert state is not None and state.revision == 2 and state.status == state_status


@pytest.mark.asyncio
async def test_exact_explicit_desc_timestamp_order_has_deterministic_tie_cursor(
    api_client, monkeypatch
) -> None:
    app, _ = api_client
    service = app.state.exact_search
    now = datetime(2026, 1, 1, tzinfo=UTC)
    first_id = "00000000-0000-4000-8000-000000000021"
    second_id = "00000000-0000-4000-8000-000000000022"
    search_sha = hashlib.sha256(b"same searchable text").hexdigest()

    def bundle(document_id: str, identifier: str, digest: str):
        document = Document(
            id=document_id,
            kind="profile",
            owner_id="owner-sort",
            public_identifier=identifier,
            visibility="public",
            schema_version=1,
            current_version=1,
            created_at=now,
            updated_at=now,
        )
        version = DocumentVersion(
            document_id=document_id,
            version=1,
            sha256=digest,
            storage_path=f"profiles/{identifier}/1.md",
            actor_id="owner-sort",
        )
        snapshot = PublicExactSearchDocumentSnapshot(
            document_id=document_id,
            document_version=1,
            source_sha256=digest,
            search_sha256=search_sha,
            kind="profile",
            schema_version=1,
            identifier=identifier,
            name=identifier,
            headline="same searchable text",
            title=None,
            location="",
            availability_status=None,
            availability_from=None,
            representation_status=None,
            contact_disclosure=None,
            updated_at=now,
            normalized_search_text="same searchable text",
            search_vector="same searchable text",
        )
        return document, version, snapshot

    async with app.state.session_factory() as session:
        session.add(
            PublicExactSearchProjectionState(
                scope="documents",
                revision=0,
                status="ready",
                contract_digest=EXACT_SEARCH_CONTRACT_DIGEST,
                created_at=now,
                updated_at=now,
            )
        )
        session.add_all(
            [
                item
                for bundle_item in (
                    bundle(first_id, "sort-first", "c" * 64),
                    bundle(second_id, "sort-second", "d" * 64),
                )
                for item in bundle_item
            ]
        )
        await session.commit()

        async def ready(_session, *, require_postgresql=False):
            return await _session.get(PublicExactSearchProjectionState, "documents")

        monkeypatch.setattr(service, "require_ready", ready)
        resolved = ResolvedSearchFilters(
            meili={}, canonical={}, filter_values={}, requested={}, installed=True
        )
        arguments = {
            "mode": "exact",
            "q": "",
            "sort_updated": "desc",
            "offset": 0,
            "limit": 1,
            "cursor": None,
            "facet_limit": 100,
        }
        first_page = await service.search(session, arguments=arguments, resolved=resolved)
        assert [hit["id"] for hit in first_page.hits] == [second_id, first_id]
        assert first_page.next_cursor is not None

        second_page = await service.search(
            session,
            arguments={**arguments, "cursor": first_page.next_cursor},
            resolved=resolved,
        )
        assert [hit["id"] for hit in second_page.hits] == [first_id]
        assert second_page.total == first_page.total == 2


@pytest.mark.asyncio
async def test_exact_cursor_binds_live_agent_eligibility_only_when_selected(
    api_client, monkeypatch
) -> None:
    app, _ = api_client
    service = app.state.exact_search
    now = datetime(2026, 1, 2, tzinfo=UTC)
    first_id = "00000000-0000-4000-8000-000000000031"
    second_id = "00000000-0000-4000-8000-000000000032"
    identity_one = "00000000-0000-4000-8000-000000000041"
    identity_two = "00000000-0000-4000-8000-000000000042"
    search_sha = hashlib.sha256(b"agent searchable text").hexdigest()

    def bundle(document_id: str, identifier: str, digest: str):
        document = Document(
            id=document_id,
            kind="profile",
            owner_id=f"owner-{identifier}",
            public_identifier=identifier,
            visibility="public",
            schema_version=1,
            current_version=1,
            created_at=now,
            updated_at=now,
        )
        version = DocumentVersion(
            document_id=document_id,
            version=1,
            sha256=digest,
            storage_path=f"profiles/{identifier}/1.md",
            actor_id=f"owner-{identifier}",
        )
        snapshot = PublicExactSearchDocumentSnapshot(
            document_id=document_id,
            document_version=1,
            source_sha256=digest,
            search_sha256=search_sha,
            kind="profile",
            schema_version=1,
            identifier=identifier,
            name=identifier,
            headline="agent searchable text",
            title=None,
            location="",
            availability_status=None,
            availability_from=None,
            representation_status=None,
            contact_disclosure=None,
            updated_at=now,
            normalized_search_text="agent searchable text",
            search_vector="agent searchable text",
        )
        return document, version, snapshot

    first_bundle = bundle(first_id, "agent-first", "e" * 64)
    second_bundle = bundle(second_id, "agent-second", "f" * 64)
    agent_one = AgentIdentity(
        id=identity_one,
        owner_id="owner-agent-first",
        handle="agent-first",
        display_name="Agent First",
        description="First agent",
        profile_document_id=first_id,
        status="active",
        created_at=now,
        updated_at=now,
    )
    agent_two = AgentIdentity(
        id=identity_two,
        owner_id="owner-agent-second",
        handle="agent-second",
        display_name="Agent Second",
        description="Second agent",
        profile_document_id=second_id,
        status="active",
        created_at=now,
        updated_at=now,
    )

    async with app.state.session_factory() as session:
        session.add(
            PublicExactSearchProjectionState(
                scope="documents",
                revision=0,
                status="ready",
                contract_digest=EXACT_SEARCH_CONTRACT_DIGEST,
                created_at=now,
                updated_at=now,
            )
        )
        session.add_all([*first_bundle, *second_bundle, agent_one, agent_two])
        await session.commit()

        async def ready(_session, *, require_postgresql=False):
            return await _session.get(PublicExactSearchProjectionState, "documents")

        monkeypatch.setattr(service, "require_ready", ready)
        resolved = ResolvedSearchFilters(
            meili={}, canonical={}, filter_values={}, requested={}, installed=True
        )
        base_arguments = {
            "mode": "exact",
            "q": "",
            "sort_updated": "desc",
            "offset": 0,
            "limit": 1,
            "cursor": None,
            "facet_limit": 100,
        }
        plain_first = await service.search(session, arguments=base_arguments, resolved=resolved)
        assert plain_first.next_cursor is not None

        selected_arguments = {**base_arguments, "agent_capability": "internal_contact_request"}
        selected_first = await service.search(
            session, arguments=selected_arguments, resolved=resolved
        )
        assert selected_first.next_cursor is not None
        selected_cursor = service._decode_cursor(selected_first.next_cursor)
        assert "agent_eligibility_digest" in selected_cursor

        stored_agent = await session.get(AgentIdentity, identity_two)
        assert stored_agent is not None
        stored_agent.status = "withdrawn"
        await session.commit()
        withdrawn_digest = await service._agent_eligibility_digest(session)
        assert withdrawn_digest != selected_cursor["agent_eligibility_digest"]

        with pytest.raises(ExactSearchCursorStale):
            await service.search(
                session,
                arguments={**selected_arguments, "cursor": selected_first.next_cursor},
                resolved=resolved,
            )

        added_agent = AgentIdentity(
            id="00000000-0000-4000-8000-000000000043",
            owner_id="owner-agent-first",
            handle="agent-third",
            display_name="Agent Third",
            description="Third agent",
            profile_document_id=first_id,
            status="active",
            created_at=now,
            updated_at=now,
        )
        session.add(added_agent)
        await session.commit()
        added_digest = await service._agent_eligibility_digest(session)
        assert added_digest != withdrawn_digest

        plain_second = await service.search(
            session,
            arguments={**base_arguments, "cursor": plain_first.next_cursor},
            resolved=resolved,
        )
        assert plain_second.hits


def test_exact_search_snapshot_preserves_public_v1_for_untyped_search() -> None:
    canonical, _ = prepare_client_document(
        "profile",
        profile_markdown(visibility="public"),
        document_id="00000000-0000-4000-8000-000000000001",
        owner_id="owner-v1",
        version=1,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    frontmatter, body = validate_canonical("profile", canonical)
    fields, compact_values = _snapshot_values(
        "profile",
        "ada-lovelace",
        frontmatter,
        body,
        "a" * 64,
        1,
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert fields["schema_version"] == 1
    assert fields["availability_status"] is None
    assert fields["representation_status"] is None
    assert fields["contact_disclosure"] is None
    assert ("skill", "Python", 0) in compact_values


def test_exact_cursor_continuation_uses_page_bound_not_corpus_bound() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(ExactSearchService.search)))
    continuation_limits: list[ast.expr] = []
    full_limits: list[ast.expr] = []
    for child in ast.walk(tree):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "limit"
            and child.args
        ):
            full_limits.extend(child.args[:1])
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not node.orelse:
            continue
        if not (
            isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "cursor_payload"
            and len(node.test.ops) == 1
            and isinstance(node.test.ops[0], ast.Is)
            and len(node.test.comparators) == 1
            and isinstance(node.test.comparators[0], ast.Constant)
            and node.test.comparators[0].value is None
        ):
            continue
        for child in ast.walk(ast.Module(body=node.orelse, type_ignores=[])):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "limit"
                and child.args
            ):
                continuation_limits.extend(child.args[:1])

    assert any(
        isinstance(argument, ast.Name) and argument.id == "EXACT_SEARCH_MATERIALIZATION_LIMIT"
        for argument in full_limits
    )
    assert any(
        isinstance(argument, ast.BinOp)
        and isinstance(argument.op, ast.Add)
        and isinstance(argument.left, ast.Name)
        and argument.left.id == "limit"
        and isinstance(argument.right, ast.Constant)
        and argument.right.value == 1
        for argument in continuation_limits
    )
    assert not any(
        isinstance(argument, ast.Name) and argument.id == "EXACT_SEARCH_MATERIALIZATION_LIMIT"
        for argument in continuation_limits
    )


@pytest.mark.asyncio
async def test_exact_search_cursor_is_signed_bounded_and_rejects_tampering(api_client) -> None:
    app, _ = api_client
    service = ExactSearchService(app.state.settings)
    payload = {
        "v": 1,
        "kid": service.cursor_keys[0].kid,
        "exp": int(datetime.now(UTC).timestamp()) + 900,
        "revision": 7,
        "taxonomy_revision_digest": "a" * 64,
        "filter_digest": "b" * 64,
        "sort": "desc",
        "anchor": "document-id",
    }
    cursor = service._encode_cursor(payload)
    assert len(cursor) <= 2048
    assert service._decode_cursor(cursor) == payload
    with pytest.raises(ExactSearchCursorMalformed):
        service._decode_cursor(cursor[:-1] + ("A" if cursor[-1] != "A" else "B"))
    for malformed in ("not-a-cursor", "", " \t "):
        with pytest.raises(ExactSearchCursorMalformed):
            service._decode_cursor(malformed)


@pytest.mark.asyncio
async def test_exact_search_ready_state_is_still_postgresql_gated(api_client) -> None:
    app, _ = api_client
    async with app.state.session_factory() as session:
        session.add(
            PublicExactSearchProjectionState(
                scope="documents",
                revision=1,
                status="ready",
                contract_digest=EXACT_SEARCH_CONTRACT_DIGEST,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()
        with pytest.raises(ExactSearchUnavailable, match="requires PostgreSQL"):
            await app.state.exact_search.require_ready(session, require_postgresql=True)


@pytest.mark.asyncio
async def test_exact_verify_rejects_corrupt_tsvector_on_postgresql_only(api_client) -> None:
    source = inspect.getsource(ExactSearchService.verify_integrity)
    assert 'dialect.name == "postgresql"' in source
    assert "to_tsvector" in source
    assert "search_vector" in source
    app, _ = api_client
    now = datetime(2026, 1, 3, tzinfo=UTC)
    async with app.state.session_factory() as session:
        if session.get_bind().dialect.name != "postgresql":
            pytest.skip("PostgreSQL is required to exercise TSVECTOR corruption")
        document_id = "00000000-0000-4000-8000-000000000051"
        canonical, _ = prepare_client_document(
            "profile",
            profile_markdown(visibility="public"),
            document_id=document_id,
            owner_id="owner-vector",
            version=1,
            updated_at=now,
        )
        frontmatter, body = validate_canonical("profile", canonical)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        fields, _ = _snapshot_values(
            "profile",
            "ada-lovelace",
            frontmatter,
            body,
            digest,
            1,
            now,
        )
        document = Document(
            id=document_id,
            kind="profile",
            owner_id="owner-vector",
            public_identifier="ada-lovelace",
            visibility="public",
            schema_version=1,
            current_version=1,
            created_at=now,
            updated_at=now,
        )
        version = DocumentVersion(
            document_id=document_id,
            version=1,
            sha256=digest,
            storage_path="profiles/vector-check/1.md",
            actor_id="owner-vector",
        )
        snapshot = PublicExactSearchDocumentSnapshot(
            document_id=document_id,
            **fields,
            search_vector=func.to_tsvector("simple", fields["normalized_search_text"]),
        )
        session.add_all(
            [
                PublicExactSearchProjectionState(
                    scope="documents",
                    revision=0,
                    status="ready",
                    contract_digest=EXACT_SEARCH_CONTRACT_DIGEST,
                    created_at=now,
                    updated_at=now,
                ),
                document,
                version,
                snapshot,
            ]
        )
        app.state.store.write_immutable(version.storage_path, canonical)
        await session.commit()
        await session.execute(
            update(PublicExactSearchDocumentSnapshot)
            .where(PublicExactSearchDocumentSnapshot.document_id == document_id)
            .values(search_vector=func.to_tsvector("simple", "corrupt vector"))
        )
        await session.commit()
        with pytest.raises(ExactSearchUnavailable, match="vector integrity"):
            await app.state.exact_search.verify_integrity(
                session, app.state.store, require_ready=False
            )


@pytest.mark.asyncio
async def test_exact_search_openapi_and_mcp_contract_is_additive_and_bounded(api_client) -> None:
    _, client = api_client
    openapi = (await client.get("/openapi.json")).json()
    get_operation = openapi["paths"]["/v1/search"]["get"]
    get_parameters = {parameter["name"]: parameter for parameter in get_operation["parameters"]}
    assert get_parameters["cursor"]["schema"]["anyOf"][0]["minLength"] == 1
    assert get_parameters["cursor"]["schema"]["anyOf"][0]["maxLength"] == 2048
    taxonomy_parameters = {
        parameter["name"]: parameter
        for parameter in openapi["paths"]["/v1/taxonomies/{taxonomy}"]["get"]["parameters"]
    }
    assert taxonomy_parameters["cursor"]["schema"]["anyOf"][0]["minLength"] == 1
    assert taxonomy_parameters["cursor"]["schema"]["anyOf"][0]["maxLength"] == 2048
    assert get_parameters["facet_limit"]["schema"] == {
        "type": "integer",
        "maximum": 500,
        "minimum": 1,
        "default": 100,
        "title": "Facet Limit",
    }
    post_operation = openapi["paths"]["/v1/search/query"]["post"]
    assert post_operation["security"] == [{}, {"BearerAuth": []}]
    request_schema = openapi["components"]["schemas"]["SearchQueryRequest"]
    assert request_schema["properties"]["mode"]["enum"] == ["projection", "exact"]
    assert request_schema["properties"]["cursor"]["anyOf"][0]["minLength"] == 1
    assert request_schema["properties"]["cursor"]["anyOf"][0]["maxLength"] == 2048
    assert request_schema["properties"]["facet_limit"]["maximum"] == 500
    response_schema = openapi["components"]["schemas"]["SearchResponse"]
    assert {
        "mode",
        "next_cursor",
        "search_revision",
        "complete",
        "facet_truncated",
    }.issubset(response_schema["properties"])
    assert response_schema["properties"]["next_cursor"]["anyOf"][0]["minLength"] == 1
    taxonomy_response_schema = openapi["components"]["schemas"]["TaxonomyTermListResponse"]
    assert taxonomy_response_schema["properties"]["next_cursor"]["anyOf"][0]["minLength"] == 1

    tools = (
        await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    ).json()
    search_schema = next(
        tool["inputSchema"]
        for tool in tools["result"]["tools"]
        if tool["name"] == "search_documents"
    )
    assert search_schema["properties"]["mode"]["enum"] == ["projection", "exact"]
    assert search_schema["properties"]["cursor"]["minLength"] == 1
    assert search_schema["properties"]["cursor"]["maxLength"] == 2048
    assert search_schema["properties"]["facet_limit"]["maximum"] == 500
    assert search_schema["not"] == {"required": ["q", "query"]}
    taxonomy_schema = next(
        tool["inputSchema"]
        for tool in tools["result"]["tools"]
        if tool["name"] == "list_taxonomy_terms"
    )
    assert taxonomy_schema["properties"]["cursor"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 2048,
    }


@pytest.mark.asyncio
async def test_exact_empty_resolution_cannot_bypass_offset_or_cursor_validation(api_client) -> None:
    app, client = api_client

    class EmptyTaxonomy:
        async def resolve_search(self, *_args: object, **_kwargs: object):
            return ResolvedSearchFilters(
                meili={},
                canonical={},
                filter_values={},
                requested={},
                installed=True,
                empty=True,
            )

        async def hydrate_hits(self, _session: object, hits: list[dict[str, object]], _resolved):
            return hits

        async def taxonomy_facets(
            self, _session: object, _hits: list[dict[str, object]], _requested: list[str]
        ):
            return {}

    class EmptyExactSearch:
        async def search(self, _session: object, *, arguments, resolved):
            del resolved
            supplied_cursor = arguments.get("cursor")
            if supplied_cursor == "malformed" or (
                isinstance(supplied_cursor, str) and not supplied_cursor.strip()
            ):
                raise ExactSearchCursorMalformed("malformed")
            if arguments.get("cursor") == "stale":
                raise ExactSearchCursorStale("stale")
            return ExactSearchResult(hits=[], facet_hits=[], total=0, next_cursor=None, revision=1)

    app.state.taxonomy = EmptyTaxonomy()
    app.state.exact_search = EmptyExactSearch()

    invalid_offset_get = await client.get(
        "/v1/search",
        params=[("mode", "exact"), ("skill_ids", "unknown"), ("offset", "1")],
    )
    invalid_offset_post = await client.post(
        "/v1/search/query",
        json={"mode": "exact", "skill_ids": ["unknown"], "offset": 1},
    )
    assert invalid_offset_get.status_code == 400
    assert invalid_offset_post.status_code == 400

    invalid_cursor_get = await client.get(
        "/v1/search",
        params={"mode": "exact", "skill_ids": "unknown", "cursor": "malformed"},
    )
    invalid_cursor_post = await client.post(
        "/v1/search/query",
        json={"mode": "exact", "skill_ids": ["unknown"], "cursor": "malformed"},
    )
    assert invalid_cursor_get.status_code == 400
    assert invalid_cursor_post.status_code == 400

    duplicate_cursor_get = await client.get(
        "/v1/search",
        params=[
            ("mode", "exact"),
            ("skill_ids", "unknown"),
            ("cursor", "valid-cursor"),
            ("cursor", "valid-cursor"),
        ],
    )
    assert duplicate_cursor_get.status_code == 422

    for supplied_cursor, expected_get_status in (("", 422), (" \t ", 400)):
        blank_cursor_get = await client.get(
            "/v1/search",
            params={"mode": "exact", "skill_ids": "unknown", "cursor": supplied_cursor},
        )
        blank_cursor_post = await client.post(
            "/v1/search/query",
            json={
                "mode": "exact",
                "skill_ids": ["unknown"],
                "cursor": supplied_cursor,
            },
        )
        assert blank_cursor_get.status_code == expected_get_status
        assert blank_cursor_post.status_code == 422

    mcp_offset = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "exact-empty-offset",
            "method": "tools/call",
            "params": {
                "name": "search_documents",
                "arguments": {"mode": "exact", "skill_ids": ["unknown"], "offset": 1},
            },
        },
    )
    mcp_cursor = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "exact-empty-cursor",
            "method": "tools/call",
            "params": {
                "name": "search_documents",
                "arguments": {
                    "mode": "exact",
                    "skill_ids": ["unknown"],
                    "cursor": "malformed",
                },
            },
        },
    )
    assert mcp_offset.json()["result"]["isError"] is True
    assert mcp_cursor.json()["result"]["isError"] is True
    assert mcp_offset.json()["result"]["structuredContent"]["code"] == "bad_request"
    assert mcp_cursor.json()["result"]["structuredContent"]["code"] == "bad_request"

    for index, supplied_cursor in enumerate(("", " \t ")):
        blank_mcp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": f"exact-blank-cursor-{index}",
                "method": "tools/call",
                "params": {
                    "name": "search_documents",
                    "arguments": {
                        "mode": "exact",
                        "skill_ids": ["unknown"],
                        "cursor": supplied_cursor,
                    },
                },
            },
        )
        assert blank_mcp.json()["result"]["isError"] is True
        assert blank_mcp.json()["result"]["structuredContent"]["code"] == ("validation_failed")

    def a2a_search(message_id: str, **fields: object):
        return {
            "message": {
                "messageId": message_id,
                "role": "ROLE_USER",
                "parts": [
                    {"data": {"action": "search", **fields}, "mediaType": "application/json"}
                ],
            }
        }

    a2a_offset = await client.post(
        "/a2a/message:send",
        headers={"A2A-Version": "1.0"},
        json=a2a_search("exact-empty-a2a-offset", mode="exact", skill_ids=["unknown"], offset=1),
    )
    a2a_cursor = await client.post(
        "/a2a/message:send",
        headers={"A2A-Version": "1.0"},
        json=a2a_search(
            "exact-empty-a2a-cursor", mode="exact", skill_ids=["unknown"], cursor="malformed"
        ),
    )
    for response in (a2a_offset, a2a_cursor):
        task = response.json()["task"]
        assert task["status"]["state"] == "TASK_STATE_REJECTED"
        assert task["artifacts"][0]["parts"][0]["data"]["error"]["code"] == "invalid_params"

    for index, supplied_cursor in enumerate(("", " \t ")):
        blank_a2a = await client.post(
            "/a2a/message:send",
            headers={"A2A-Version": "1.0"},
            json=a2a_search(
                f"exact-blank-a2a-cursor-{index}",
                mode="exact",
                skill_ids=["unknown"],
                cursor=supplied_cursor,
            ),
        )
        task = blank_a2a.json()["task"]
        assert task["status"]["state"] == "TASK_STATE_REJECTED"
        assert task["artifacts"][0]["parts"][0]["data"]["error"]["code"] == "invalid_params"

    start_get = await client.get("/v1/search", params={"mode": "exact", "skill_ids": "unknown"})
    start_post = await client.post(
        "/v1/search/query",
        json={"mode": "exact", "skill_ids": ["unknown"], "cursor": None},
    )
    valid_get = await client.get(
        "/v1/search",
        params={"mode": "exact", "skill_ids": "unknown", "cursor": "valid-cursor"},
    )
    assert start_get.status_code == start_post.status_code == valid_get.status_code == 200

    for index, supplied_cursor in enumerate((None, "valid-cursor")):
        valid_mcp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": f"exact-valid-cursor-{index}",
                "method": "tools/call",
                "params": {
                    "name": "search_documents",
                    "arguments": {
                        "mode": "exact",
                        "skill_ids": ["unknown"],
                        "cursor": supplied_cursor,
                    },
                },
            },
        )
        assert valid_mcp.json()["result"].get("isError") is not True

        valid_a2a = await client.post(
            "/a2a/message:send",
            headers={"A2A-Version": "1.0"},
            json=a2a_search(
                f"exact-valid-a2a-cursor-{index}",
                mode="exact",
                skill_ids=["unknown"],
                cursor=supplied_cursor,
            ),
        )
        assert valid_a2a.json()["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


@pytest.mark.asyncio
async def test_exact_cursor_pages_retain_full_surviving_facets(api_client) -> None:
    app, client = api_client
    filter_value = "tx1_" + "a" * 64
    full_hits = [
        {
            "id": "00000000-0000-4000-8000-000000000001",
            "kind": "profile",
            "identifier": "ada-lovelace",
            "name": "Ada Lovelace",
            "headline": "Python engineer",
            "location": "Singapore",
            "skills": ["Python"],
            "skill_ids": ["esco:python"],
            "skill_filter_values": [filter_value],
            "version": 1,
            "excerpt": "Python engineer",
            "html_url": "/p/ada-lovelace",
            "markdown_url": "/v1/profiles/ada-lovelace.md",
        },
        {
            "id": "00000000-0000-4000-8000-000000000002",
            "kind": "profile",
            "identifier": "grace-hopper",
            "name": "Grace Hopper",
            "headline": "Python engineer",
            "location": "Singapore",
            "skills": ["Python"],
            "skill_ids": ["esco:python"],
            "skill_filter_values": [filter_value],
            "version": 1,
            "excerpt": "Python engineer",
            "html_url": "/p/grace-hopper",
            "markdown_url": "/v1/profiles/grace-hopper.md",
        },
    ]

    class FakeTaxonomy:
        async def resolve_search(self, *_args: object, **_kwargs: object):
            return ResolvedSearchFilters(
                meili={},
                canonical={},
                filter_values={},
                requested={},
                installed=True,
            )

        async def hydrate_hits(self, _session: object, hits: list[dict[str, object]], _resolved):
            return hits

        async def taxonomy_facets(
            self, _session: object, hits: list[dict[str, object]], _requested: list[str]
        ):
            count = sum(filter_value in hit.get("skill_filter_values", []) for hit in hits)
            return {
                "skill_ids": [
                    {
                        "taxonomy": "skill",
                        "parameter": "skill_ids",
                        "canonical_id": "esco:python",
                        "filter_value": filter_value,
                        "label": "Python",
                        "label_conflict": False,
                        "vocabulary_version": None,
                        "version_conflict": False,
                        "count": count,
                    }
                ]
            }

    class FakeExactSearch:
        async def search(self, _session: object, *, arguments, resolved):
            if arguments.get("cursor"):
                return ExactSearchResult(
                    hits=[full_hits[1]],
                    facet_hits=full_hits,
                    total=2,
                    next_cursor=None,
                    revision=7,
                )
            return ExactSearchResult(
                hits=full_hits,
                facet_hits=full_hits,
                total=2,
                next_cursor="cursor-page-2",
                revision=7,
            )

    app.state.taxonomy = FakeTaxonomy()
    app.state.exact_search = FakeExactSearch()
    page_one = await client.post(
        "/v1/search/query",
        json={"mode": "exact", "facets": ["skill_ids"], "limit": 1},
    )
    page_two = await client.post(
        "/v1/search/query",
        json={
            "mode": "exact",
            "facets": ["skill_ids"],
            "limit": 1,
            "cursor": "cursor-page-2",
        },
    )
    assert page_one.status_code == 200, page_one.text
    assert page_two.status_code == 200, page_two.text
    first = page_one.json()
    second = page_two.json()
    assert first["hits"][0]["identifier"] == "ada-lovelace"
    assert second["hits"][0]["identifier"] == "grace-hopper"
    assert first["total"] == second["total"] == 2
    assert first["facets"] == second["facets"] == {"skill_ids": {"esco:python": 2}}
    assert first["taxonomy_facets"] == second["taxonomy_facets"]
    assert first["facet_truncated"] == second["facet_truncated"] == {"skill_ids": False}
