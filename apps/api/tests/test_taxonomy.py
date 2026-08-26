from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select

from app.markdown import client_template, prepare_client_document, validate_canonical
from app.models import (
    Document,
    DocumentVersion,
    PublicTaxonomyDocumentSnapshot,
    PublicTaxonomyMembership,
    PublicTaxonomyProjectionState,
    PublicTaxonomyTerm,
)
from app.services.documents import DocumentConflictError, DocumentService, strong_etag
from app.services.storage import StorageIntegrityError, VersionStore
from app.services.taxonomy import (
    TAXONOMY_CONTRACT_DIGEST,
    TAXONOMY_TYPES,
    ResolvedSearchFilters,
    TaxonomyCursorMalformed,
    TaxonomyCursorStale,
    TaxonomyInvalidValue,
    TaxonomyService,
    TaxonomyUnavailable,
    _cursor_decode,
    _cursor_encode,
    _get_or_create_term,
    _reference_entries,
    canonical_id,
    remove_document_projection,
    replace_document_projection,
    taxonomy_filter_value,
)


def _profile_v2_markdown(
    name: str = "Ada Lovelace",
    headline: str = "Computing pioneer",
    skills: tuple[str, ...] = ("Mathematics", "Programming"),
) -> str:
    draft = client_template("profile", "\n".join((name, headline, "Skills", *skills)))
    draft = draft.replace("visibility: private", "visibility: public")
    draft = draft.replace(
        "languages: []",
        "languages:\n"
        "  - scheme: iso-639-1\n"
        "    id: en\n"
        "    label: English\n"
        "    proficiency: native_or_bilingual\n",
    )
    return draft.replace("work_modes: []", "work_modes:\n  - hybrid\n  - remote")


def _profile_v1_markdown() -> str:
    return client_template(
        "profile", "Ada Lovelace\nLegacy profile\nSkills\nPython", schema_version=1
    ).replace("visibility: private", "visibility: public")


async def _install_states(app: object, *, status: str = "ready") -> None:
    async with app.state.session_factory() as session:
        session.add_all(
            PublicTaxonomyProjectionState(
                taxonomy=taxonomy,
                revision=1,
                status=status,
                contract_digest=TAXONOMY_CONTRACT_DIGEST,
                updated_at=datetime.now(UTC),
            )
            for taxonomy in TAXONOMY_TYPES
        )
        await session.commit()


async def _install_ready(app: object) -> None:
    await _install_states(app)


def _resolved(**canonical: object) -> ResolvedSearchFilters:
    return ResolvedSearchFilters(
        meili={},
        canonical=canonical,
        filter_values={},
        requested=canonical,
        installed=True,
    )


@pytest.mark.asyncio
async def test_public_taxonomy_catalog_and_terms_are_ready(api_client) -> None:
    app, client = api_client
    await _install_ready(app)
    # The route only needs the current projection rows; create through the
    # service session so the fixture remains source-faithful.
    async with app.state.session_factory() as session:
        service = DocumentService(session, app.state.store, app.state.settings)
        await service.create("profile", _profile_v2_markdown(), "user_test")

    catalog = await client.get("/v1/taxonomies")
    assert catalog.status_code == 200, catalog.text
    assert catalog.headers["cache-control"] == "no-store"
    assert {entry["taxonomy"] for entry in catalog.json()} == set(TAXONOMY_TYPES)
    assert all(
        {"owner_id", "document_id", "count", "source_document"}.isdisjoint(entry)
        for entry in catalog.json()
    )

    terms = await client.get("/v1/taxonomies/skill?limit=1")
    assert terms.status_code == 200, terms.text
    payload = terms.json()
    assert payload["terms"]
    term = payload["terms"][0]
    assert term["canonical_id"] == f"{term['scheme']}:{term['external_id']}"
    assert term["filter_value"].startswith("tx1_")
    assert len(payload.get("next_cursor") or "") <= 2048
    exact_alias = await client.get(
        "/v1/taxonomies/skill", params={"q": term["filter_value"], "limit": 10}
    )
    assert [item["filter_value"] for item in exact_alias.json()["terms"]] == [term["filter_value"]]
    assert (await client.get("/v1/taxonomies/not-a-taxonomy")).status_code == 404


@pytest.mark.asyncio
async def test_taxonomy_compact_cursor_replays_maximum_legal_unicode_query_and_labels(
    api_client,
) -> None:
    app, client = api_client
    await _install_ready(app)
    scheme = "s" * 80
    query = "😀" * 100
    first_external_id = "e" * 254 + "a"
    second_external_id = "e" * 254 + "b"
    first_label = "😀" * 159 + "a"
    second_label = "😀" * 159 + "b"
    async with app.state.session_factory() as session:
        session.add_all(
            [
                PublicTaxonomyTerm(
                    taxonomy="skill",
                    scheme=scheme,
                    external_id=external_id,
                    canonical_id=canonical_id(scheme, external_id),
                    filter_value=taxonomy_filter_value("skill", scheme, external_id),
                    label=label,
                    label_conflict=False,
                    vocabulary_version=None,
                    version_conflict=False,
                )
                for external_id, label in (
                    (first_external_id, first_label),
                    (second_external_id, second_label),
                )
            ]
        )
        await session.commit()

    service = app.state.taxonomy
    async with app.state.session_factory() as session:
        service_first, service_cursor, _ = await service.terms(
            session, taxonomy="skill", query=query, cursor=None, limit=1
        )
        assert len(service_first) == 1
        assert service_cursor is not None
        service_second, _, _ = await service.terms(
            session, taxonomy="skill", query=query, cursor=service_cursor, limit=1
        )
        assert len(service_second) == 1
        assert service_second[0]["canonical_id"] != service_first[0]["canonical_id"]

    first = await client.get("/v1/taxonomies/skill", params={"q": query, "limit": 1})
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert len(first_body["terms"]) == 1
    cursor = first_body["next_cursor"]
    assert cursor is not None
    assert len(cursor) <= 2048
    cursor_payload = _cursor_decode(cursor, service.cursor_secret)
    assert cursor_payload["v"] == 2
    assert cursor_payload["query_digest"] == hashlib.sha256(query.encode()).hexdigest()
    assert "query" not in cursor_payload
    assert "label" not in cursor_payload
    assert len(first_body["terms"][0]["canonical_id"]) == 336
    assert len(first_body["terms"][0]["external_id"]) == 255
    assert len(first_body["terms"][0]["label"]) == 160
    assert not first_body["terms"][0]["label"].isascii()

    missing_payload = {**cursor_payload, "term_id": "missing-term"}
    missing_cursor = _cursor_encode(missing_payload, service.cursor_secret)
    async with app.state.session_factory() as session:
        with pytest.raises(TaxonomyCursorMalformed):
            await service.terms(
                session, taxonomy="skill", query=query, cursor=missing_cursor, limit=1
            )

    second = await client.get(
        "/v1/taxonomies/skill", params={"q": query, "cursor": cursor, "limit": 1}
    )
    assert second.status_code == 200, second.text
    assert len(second.json()["terms"]) == 1
    assert second.json()["terms"][0]["canonical_id"] != first_body["terms"][0]["canonical_id"]
    assert second.json()["next_cursor"] is None


@pytest.mark.asyncio
async def test_projection_preserves_source_order_and_membership_versions(api_client) -> None:
    app, _ = api_client
    await _install_ready(app)
    async with app.state.session_factory() as session:
        service = DocumentService(session, app.state.store, app.state.settings)
        document = await service.create("profile", _profile_v2_markdown(), "user_test")
        version = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.version == document.current_version,
            )
        )
        assert version is not None
        frontmatter, _ = validate_canonical("profile", service.read_markdown(version))
        frontmatter["languages"].append(
            {
                "scheme": "iso-639-1",
                "id": "fr",
                "label": "French",
                "proficiency": "professional",
            }
        )
        await replace_document_projection(
            session,
            document=document,
            frontmatter=frontmatter,
            document_version=document.current_version,
        )
        await session.commit()

        rows = (
            await session.execute(
                select(PublicTaxonomyTerm, PublicTaxonomyMembership)
                .join(
                    PublicTaxonomyMembership,
                    PublicTaxonomyMembership.term_id == PublicTaxonomyTerm.id,
                )
                .where(
                    PublicTaxonomyMembership.document_id == document.id,
                    PublicTaxonomyTerm.taxonomy == "language",
                )
                .order_by(PublicTaxonomyMembership.source_ordinal)
            )
        ).all()
        assert [term.canonical_id for term, _ in rows] == [
            "iso-639-1:en",
            "iso-639-1:fr",
        ]
        assert [membership.source_ordinal for _, membership in rows] == [0, 1]
        assert [membership.vocabulary_version for _, membership in rows] == [None, None]

        hydrated = await TaxonomyService(b"test").hydrate_hits(
            session,
            [{"id": document.id, "version": document.current_version}],
            _resolved(),
        )
        assert hydrated[0]["languages"] == ["English", "French"]
        assert hydrated[0]["language_proficiencies"] == [
            "native_or_bilingual",
            "professional",
        ]


@pytest.mark.asyncio
async def test_none_membership_versions_are_not_a_conflict_and_exact_identity_is_verified(
    api_client,
) -> None:
    app, _ = api_client
    await _install_ready(app)
    async with app.state.session_factory() as session:
        service = DocumentService(session, app.state.store, app.state.settings)
        await service.create("profile", _profile_v2_markdown(), "user_test")
        term = await session.scalar(
            select(PublicTaxonomyTerm).where(
                PublicTaxonomyTerm.taxonomy == "work_mode",
                PublicTaxonomyTerm.external_id == "hybrid",
            )
        )
        assert term is not None
        assert term.vocabulary_version is None
        assert term.version_conflict is False
        assert term.canonical_id == "connect.md:hybrid"
        assert term.filter_value == taxonomy_filter_value("work_mode", "connect.md", "hybrid")
        await TaxonomyService(b"test").verify_integrity(
            session, require_ready=True, deterministic=True
        )
        term.canonical_id = "wrong:identity"
        with pytest.raises(TaxonomyUnavailable):
            await TaxonomyService(b"test").verify_integrity(
                session, require_ready=True, deterministic=True
            )
        await session.rollback()


@pytest.mark.asyncio
async def test_if_required_reuses_only_verified_ready_projection_and_force_rebuilds(
    api_client,
) -> None:
    app, _ = api_client
    await _install_ready(app)
    service = TaxonomyService(b"test")
    async with app.state.session_factory() as session:
        reused = await service.backfill(
            session, VersionStore(app.state.settings.storage_path), if_required=True
        )
        assert reused == {"status": "ready", "backfilled": 0, "reused": True}
        rebuilt = await service.backfill(
            session, VersionStore(app.state.settings.storage_path), if_required=False
        )
        assert rebuilt["status"] == "ready"
        assert rebuilt["reused"] is False


@pytest.mark.asyncio
async def test_backfill_ignores_private_corrupt_documents(api_client) -> None:
    app, _ = api_client
    await _install_ready(app)
    private_id = "private-corrupt"
    async with app.state.session_factory() as session:
        session.add(
            Document(
                id=private_id,
                kind="profile",
                owner_id="private-owner",
                public_identifier="private-corrupt",
                visibility="private",
                schema_version=None,
                current_version=1,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                versions=[
                    DocumentVersion(
                        version=1,
                        sha256="0" * 64,
                        storage_path="missing/private.md",
                        actor_id="private-owner",
                        actor_method="clerk_jwt",
                        created_at=datetime.now(UTC),
                    )
                ],
            )
        )
        await session.commit()
        result = await TaxonomyService(b"test").backfill(
            session, VersionStore(app.state.settings.storage_path), if_required=False
        )
        assert result["status"] == "ready"
        private = await session.get(Document, private_id)
        assert private is not None
        assert private.schema_version is None


@pytest.mark.asyncio
async def test_public_corrupt_backfill_fails_non_ready_but_zero_public_docs_rebuild(
    api_client,
) -> None:
    app, _ = api_client
    await _install_ready(app)
    async with app.state.session_factory() as session:
        session.add(
            Document(
                id="public-corrupt",
                kind="profile",
                owner_id="public-owner",
                public_identifier="public-corrupt",
                visibility="public",
                schema_version=2,
                current_version=1,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                versions=[
                    DocumentVersion(
                        version=1,
                        sha256="0" * 64,
                        storage_path="missing/public.md",
                        actor_id="public-owner",
                        actor_method="clerk_jwt",
                        created_at=datetime.now(UTC),
                    )
                ],
            )
        )
        await session.commit()
        with pytest.raises(StorageIntegrityError):
            await TaxonomyService(b"test").backfill(
                session, VersionStore(app.state.settings.storage_path), if_required=False
            )
        states = (await session.scalars(select(PublicTaxonomyProjectionState))).all()
        assert states and {state.status for state in states} == {"failed"}
        corrupt = await session.get(Document, "public-corrupt")
        assert corrupt is not None
        corrupt.visibility = "private"
        await session.commit()
        rebuilt = await TaxonomyService(b"test").backfill(
            session, VersionStore(app.state.settings.storage_path), if_required=False
        )
        assert rebuilt == {"status": "ready", "backfilled": 0, "reused": False}
        states = (await session.scalars(select(PublicTaxonomyProjectionState))).all()
        assert {state.status for state in states} == {"ready"}


@pytest.mark.asyncio
async def test_public_create_unavailable_removes_immutable_file(api_client) -> None:
    app, _ = api_client
    await _install_states(app, status="backfill_required")
    async with app.state.session_factory() as session:
        service = DocumentService(session, app.state.store, app.state.settings)
        with pytest.raises(TaxonomyUnavailable):
            await service.create("profile", _profile_v2_markdown(), "user_test")
        assert not list(app.state.store.root.rglob("*.md"))


@pytest.mark.asyncio
async def test_public_update_unavailable_cleans_new_version_file(api_client) -> None:
    app, _ = api_client
    async with app.state.session_factory() as session:
        service = DocumentService(session, app.state.store, app.state.settings)
        document = await service.create("profile", _profile_v2_markdown(), "user_test")
        version = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.version == document.current_version,
            )
        )
        assert version is not None
        markdown = service.read_markdown(version)
        etag = strong_etag(version.sha256)
    before = {path for path in app.state.store.root.rglob("*.md")}
    await _install_states(app, status="backfill_required")
    updated_markdown = markdown.replace(
        "headline: Computing pioneer", "headline: Updated computing pioneer"
    )
    async with app.state.session_factory() as session:
        with pytest.raises(TaxonomyUnavailable):
            await DocumentService(session, app.state.store, app.state.settings).update(
                "profile",
                document.public_identifier,
                updated_markdown,
                "user_test",
                if_match=etag,
            )
    assert {path for path in app.state.store.root.rglob("*.md")} == before
    async with app.state.session_factory() as session:
        unchanged = await session.get(Document, document.id)
        assert unchanged is not None
        assert unchanged.current_version == 1
        versions = (
            await session.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document.id)
                .order_by(DocumentVersion.version)
            )
        ).all()
        assert [version.version for version in versions] == [1]


@pytest.mark.asyncio
async def test_shared_term_survives_first_private_transition_and_prunes_on_last(
    api_client,
) -> None:
    app, _ = api_client
    await _install_ready(app)
    async with app.state.session_factory() as session:
        service = DocumentService(session, app.state.store, app.state.settings)
        first = await service.create("profile", _profile_v2_markdown(), "owner-one")
        second = await service.create("profile", _profile_v2_markdown("Grace Hopper"), "owner-two")
        shared = await session.scalar(
            select(PublicTaxonomyTerm).where(
                PublicTaxonomyTerm.taxonomy == "skill",
                PublicTaxonomyTerm.external_id == "connectmd-user-skill-mathematics",
            )
        )
        assert shared is not None
        first_version = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == first.id,
                DocumentVersion.version == first.current_version,
            )
        )
        assert first_version is not None
        first_private = service.read_markdown(first_version).replace(
            "visibility: public", "visibility: private"
        )
        await service.update(
            "profile",
            first.public_identifier,
            first_private,
            "owner-one",
            if_match=strong_etag(first_version.sha256),
        )
        assert await session.get(PublicTaxonomyTerm, shared.id) is not None
        assert (
            await session.scalar(
                select(PublicTaxonomyMembership.id).where(
                    PublicTaxonomyMembership.document_id == first.id,
                    PublicTaxonomyMembership.term_id == shared.id,
                )
            )
            is None
        )
        assert (
            await session.scalar(
                select(PublicTaxonomyMembership.id).where(
                    PublicTaxonomyMembership.document_id == second.id,
                    PublicTaxonomyMembership.term_id == shared.id,
                )
            )
            is not None
        )

        second_version = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == second.id,
                DocumentVersion.version == second.current_version,
            )
        )
        assert second_version is not None
        second_private = service.read_markdown(second_version).replace(
            "visibility: public", "visibility: private"
        )
        await service.update(
            "profile",
            second.public_identifier,
            second_private,
            "owner-two",
            if_match=strong_etag(second_version.sha256),
        )
        assert await session.get(PublicTaxonomyTerm, shared.id) is None


@pytest.mark.asyncio
async def test_public_v2_reference_removal_private_and_v1_transitions_clear_projection(
    api_client,
) -> None:
    app, _ = api_client
    await _install_ready(app)
    async with app.state.session_factory() as session:
        service = DocumentService(session, app.state.store, app.state.settings)
        document = await service.create("profile", _profile_v2_markdown(), "user_test")
        programming = await session.scalar(
            select(PublicTaxonomyTerm).where(
                PublicTaxonomyTerm.taxonomy == "skill",
                PublicTaxonomyTerm.external_id == "connectmd-user-skill-programming",
            )
        )
        assert programming is not None
        current = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.version == document.current_version,
            )
        )
        assert current is not None
        document = await service.update(
            "profile",
            document.public_identifier,
            _profile_v2_markdown(skills=("Mathematics",)),
            "user_test",
            if_match=strong_etag(current.sha256),
        )
        assert document.schema_version == 2
        assert await session.get(PublicTaxonomyTerm, programming.id) is None

        current = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.version == document.current_version,
            )
        )
        assert current is not None
        document = await service.update(
            "profile",
            document.public_identifier,
            service.read_markdown(current).replace("visibility: public", "visibility: private"),
            "user_test",
            if_match=strong_etag(current.sha256),
        )
        assert document.visibility == "private" and document.schema_version == 2
        assert (
            await session.scalar(
                select(PublicTaxonomyMembership.id).where(
                    PublicTaxonomyMembership.document_id == document.id
                )
            )
            is None
        )

        current = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.version == document.current_version,
            )
        )
        assert current is not None
        document = await service.update(
            "profile",
            document.public_identifier,
            _profile_v1_markdown(),
            "user_test",
            if_match=strong_etag(current.sha256),
        )
        assert document.visibility == "public" and document.schema_version == 1
        assert await session.get(PublicTaxonomyDocumentSnapshot, document.id) is None


@pytest.mark.asyncio
async def test_projection_rollback_restores_concealment_membership(api_client) -> None:
    app, _ = api_client
    await _install_ready(app)
    async with app.state.session_factory() as session:
        document = await DocumentService(session, app.state.store, app.state.settings).create(
            "profile", _profile_v2_markdown(), "user_test"
        )
        document_id = document.id
        before = (
            await session.scalars(
                select(PublicTaxonomyMembership).where(
                    PublicTaxonomyMembership.document_id == document.id
                )
            )
        ).all()
        assert before
        await remove_document_projection(session, document.id)
        await session.flush()
        assert (
            await session.scalar(
                select(PublicTaxonomyMembership.id).where(
                    PublicTaxonomyMembership.document_id == document_id
                )
            )
            is None
        )
        await session.rollback()
    async with app.state.session_factory() as session:
        restored = (
            await session.scalars(
                select(PublicTaxonomyMembership).where(
                    PublicTaxonomyMembership.document_id == document_id
                )
            )
        ).all()
        assert len(restored) == len(before)


@pytest.mark.asyncio
async def test_same_identity_reuses_term_and_filter_collision_fails_closed(
    api_client, monkeypatch
) -> None:
    app, _ = api_client
    await _install_ready(app)
    async with app.state.session_factory() as session:
        service = DocumentService(session, app.state.store, app.state.settings)
        first = await service.create("profile", _profile_v2_markdown(), "owner-one")
        second = await service.create("profile", _profile_v2_markdown("Grace Hopper"), "owner-two")
        same_identity = (
            await session.scalars(
                select(PublicTaxonomyTerm).where(
                    PublicTaxonomyTerm.taxonomy == "skill",
                    PublicTaxonomyTerm.external_id == "connectmd-user-skill-mathematics",
                )
            )
        ).all()
        assert len(same_identity) == 1
        term_id = same_identity[0].id
        assert (
            await session.scalar(
                select(PublicTaxonomyMembership.id).where(
                    PublicTaxonomyMembership.document_id == first.id,
                    PublicTaxonomyMembership.term_id == same_identity[0].id,
                )
            )
            is not None
        )
        assert (
            await session.scalar(
                select(PublicTaxonomyMembership.id).where(
                    PublicTaxonomyMembership.document_id == second.id,
                    PublicTaxonomyMembership.term_id == same_identity[0].id,
                )
            )
            is not None
        )
        existing_filter = same_identity[0].filter_value
        real_filter_value = taxonomy_filter_value

        def collide(taxonomy: str, scheme: str, external_id: str) -> str:
            if external_id == "connectmd-user-skill-rust":
                return existing_filter
            return real_filter_value(taxonomy, scheme, external_id)

        monkeypatch.setattr("app.services.taxonomy.taxonomy_filter_value", collide)
        with pytest.raises(DocumentConflictError):
            await service.create(
                "profile",
                _profile_v2_markdown("Katherine Johnson", skills=("Rust",)),
                "owner-three",
            )
        assert (
            await session.scalar(
                select(Document).where(Document.public_identifier == "katherine-johnson")
            )
            is None
        )
        survivor = await session.get(PublicTaxonomyTerm, term_id)
        assert survivor is not None and survivor.filter_value == existing_filter
        memberships = (
            await session.scalars(
                select(PublicTaxonomyMembership).where(PublicTaxonomyMembership.term_id == term_id)
            )
        ).all()
        assert len(memberships) == 2


@pytest.mark.asyncio
async def test_legitimate_integrity_error_rereads_identical_existing_term(
    api_client, monkeypatch
) -> None:
    app, _ = api_client
    await _install_ready(app)
    async with app.state.session_factory() as session:
        await DocumentService(session, app.state.store, app.state.settings).create(
            "profile", _profile_v2_markdown(), "user_test"
        )
        existing = await session.scalar(
            select(PublicTaxonomyTerm).where(
                PublicTaxonomyTerm.taxonomy == "skill",
                PublicTaxonomyTerm.external_id == "connectmd-user-skill-mathematics",
            )
        )
        assert existing is not None
        real_scalar = session.scalar
        first_lookup = True

        async def hide_first_lookup(statement, *args, **kwargs):
            nonlocal first_lookup
            if first_lookup:
                first_lookup = False
                return None
            return await real_scalar(statement, *args, **kwargs)

        monkeypatch.setattr(session, "scalar", hide_first_lookup)
        reused = await _get_or_create_term(
            session,
            {
                "taxonomy": existing.taxonomy,
                "scheme": existing.scheme,
                "external_id": existing.external_id,
            },
        )
        assert reused.id == existing.id
        await session.rollback()


@pytest.mark.asyncio
async def test_unready_any_taxonomy_blocks_unfiltered_resolution_before_backend(api_client) -> None:
    app, _ = api_client
    await _install_ready(app)
    async with app.state.session_factory() as session:
        state = await session.get(PublicTaxonomyProjectionState, "skill")
        assert state is not None
        state.status = "building"
        await session.commit()
        with pytest.raises(TaxonomyUnavailable):
            await TaxonomyService(b"test").resolve_search(session, {}, allow_long_canonical=False)


@pytest.mark.asyncio
async def test_typed_resolution_is_batched_and_bounds_all_repeated_values(api_client) -> None:
    app, _ = api_client
    await _install_ready(app)
    async with app.state.session_factory() as session:
        document_service = DocumentService(session, app.state.store, app.state.settings)
        await document_service.create("profile", _profile_v2_markdown(), "user_test")
        terms = (
            await session.scalars(
                select(PublicTaxonomyTerm)
                .where(PublicTaxonomyTerm.taxonomy == "skill")
                .order_by(PublicTaxonomyTerm.canonical_id)
            )
        ).all()
        assert len(terms) >= 2
        term_values = [terms[0].filter_value, terms[1].canonical_id]
        term_queries: list[str] = []

        def count_term_queries(_conn, _cursor, statement, _parameters, _context, _executemany):
            if "public_taxonomy_terms" in statement.lower():
                term_queries.append(statement)

        event.listen(app.state.engine.sync_engine, "before_cursor_execute", count_term_queries)
        try:
            resolved = await TaxonomyService(b"test").resolve_search(
                session,
                {"skill_ids": term_values},
                allow_long_canonical=False,
            )
        finally:
            event.remove(app.state.engine.sync_engine, "before_cursor_execute", count_term_queries)
        assert resolved.canonical["skill_ids"] == [
            terms[0].canonical_id,
            terms[1].canonical_id,
        ]
        assert len(term_queries) == 1

        unknown = await TaxonomyService(b"test").resolve_search(
            session,
            {"skill_ids": ["example:unknown"]},
            allow_long_canonical=False,
        )
        assert unknown.empty is True
        occupation = await session.scalar(
            select(PublicTaxonomyTerm).where(PublicTaxonomyTerm.taxonomy == "occupation")
        )
        assert occupation is not None
        wrong_type = await TaxonomyService(b"test").resolve_search(
            session,
            {"skill_ids": [occupation.filter_value]},
            allow_long_canonical=False,
        )
        assert wrong_type.empty is True
        with pytest.raises(TaxonomyInvalidValue):
            await TaxonomyService(b"test").resolve_search(
                session, {"skill_ids": ["malformed"]}, allow_long_canonical=False
            )
        with pytest.raises(TaxonomyInvalidValue):
            await TaxonomyService(b"test").resolve_search(
                session,
                {"skill_ids": [terms[0].filter_value] * 51},
                allow_long_canonical=False,
            )


@pytest.mark.asyncio
async def test_taxonomy_terms_search_asserted_labels_and_signed_revision_cursor(api_client) -> None:
    app, _ = api_client
    await _install_ready(app)
    async with app.state.session_factory() as session:
        document_service = DocumentService(session, app.state.store, app.state.settings)
        document = await document_service.create("profile", _profile_v2_markdown(), "user_test")
        membership = await session.scalar(
            select(PublicTaxonomyMembership)
            .join(PublicTaxonomyTerm, PublicTaxonomyTerm.id == PublicTaxonomyMembership.term_id)
            .where(
                PublicTaxonomyMembership.document_id == document.id,
                PublicTaxonomyTerm.taxonomy == "skill",
            )
        )
        assert membership is not None
        membership.label_assertion = "Asserted search label"
        await session.commit()
        service = TaxonomyService(b"test")
        terms, _, _ = await service.terms(
            session, taxonomy="skill", query="Asserted search label", cursor=None, limit=50
        )
        assert terms
        _, cursor, _ = await service.terms(
            session, taxonomy="skill", query="", cursor=None, limit=1
        )
        assert cursor is not None
        with pytest.raises(TaxonomyCursorMalformed):
            await service.terms(
                session, taxonomy="skill", query="", cursor=cursor[:-1] + "x", limit=1
            )
        cursor_payload = _cursor_decode(cursor, service.cursor_secret)
        cursor_payload["term_id"] = "missing-term"
        missing_term_cursor = _cursor_encode(cursor_payload, service.cursor_secret)
        with pytest.raises(TaxonomyCursorMalformed):
            await service.terms(
                session, taxonomy="skill", query="", cursor=missing_term_cursor, limit=1
            )
        state = await session.get(PublicTaxonomyProjectionState, "skill")
        assert state is not None
        state.revision += 1
        await session.commit()
        with pytest.raises(TaxonomyCursorStale):
            await service.terms(session, taxonomy="skill", query="", cursor=cursor, limit=1)


@pytest.mark.asyncio
async def test_hydration_drops_stale_versions_but_preserves_unfiltered_v1_hits(api_client) -> None:
    app, _ = api_client
    await _install_ready(app)
    async with app.state.session_factory() as session:
        document_service = DocumentService(session, app.state.store, app.state.settings)
        document = await document_service.create("profile", _profile_v2_markdown(), "user_test")
        service = TaxonomyService(b"test")
        hit = {"id": document.id, "version": document.current_version, "skills": ["legacy"]}
        assert await service.hydrate_hits(session, [hit], _resolved())
        document.current_version = 2
        await session.commit()
        assert await service.hydrate_hits(session, [hit], _resolved()) == []
        document = await session.get(Document, document.id)
        assert document is not None
        document.current_version = 1
        document.schema_version = 1
        await session.commit()
        legacy = await service.hydrate_hits(
            session,
            [{"id": document.id, "version": 1, "skills": ["legacy"]}],
            _resolved(),
        )
        assert legacy[0]["skills"] == ["legacy"]
        assert legacy[0]["skill_ids"] == []
        typed = await service.hydrate_hits(
            session,
            [{"id": document.id, "version": 1, "skills": ["legacy"]}],
            _resolved(skill_ids=["connectmd-user-skill:mathematics"]),
        )
        assert typed == []


def test_representative_matching_uses_canonical_identity() -> None:
    data = {
        "representative_ids": [canonical_id("connectmd-agent", "ada")],
        "representative_filter_values": ["tx1_deadbeef"],
    }
    assert TaxonomyService._matches(
        {}, data, {"representative_ids": [canonical_id("connectmd-agent", "ada")]}
    )
    assert not TaxonomyService._matches(
        {}, data, {"representative_ids": [canonical_id("other", "ada")]}
    )


def test_reference_entries_include_source_ordinals() -> None:
    canonical, _ = prepare_client_document(
        "profile",
        _profile_v2_markdown(),
        document_id="00000000-0000-4000-8000-000000000001",
        owner_id="owner-id",
        version=1,
    )
    frontmatter, _ = validate_canonical("profile", canonical)
    entries = _reference_entries(frontmatter)
    assert entries
    assert all("source_ordinal" in entry for entry in entries)
    assert [entry["source_ordinal"] for entry in entries if entry["taxonomy"] == "work_mode"] == [
        0,
        1,
    ]
