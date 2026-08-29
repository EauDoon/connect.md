"""Best-effort Meilisearch projection. Canonical Markdown never depends on it."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

import anyio
import httpx
from pydantic import HttpUrl

from app.markdown import validate_canonical
from app.models import Document


class SearchUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchDeleteAttestation:
    """The lifecycle worker's explicit projection-delete outcome."""

    configured: bool
    state: str


MAX_AGENT_SEARCH_RESULTS = 1050


class SearchSettings(Protocol):
    meilisearch_url: HttpUrl | None
    meilisearch_api_key: str | None
    meilisearch_index: str


FILTERABLE_ATTRIBUTES = [
    "visibility",
    "kind",
    "schema_version",
    "skill_ids",
    "skills",
    "occupation_ids",
    "occupations",
    "industry_ids",
    "industries",
    "location_id",
    "location",
    "location_country_code",
    "location_region",
    "location_city",
    "language_ids",
    "languages",
    "language_proficiencies",
    "seniority_id",
    "seniority",
    "work_modes",
    "availability_status",
    "availability_from",
    "open_to_ids",
    "open_to",
    "organization_ids",
    "organizations",
    "representation_status",
    "representative_id",
    "contact_disclosure",
    "taxonomy_versions",
    "updated_at",
]

SEARCHABLE_ATTRIBUTES = [
    "name",
    "headline",
    "title",
    "occupations",
    "occupation_ids",
    "industries",
    "industry_ids",
    "skills",
    "skill_ids",
    "languages",
    "language_ids",
    "seniority",
    "seniority_id",
    "open_to",
    "open_to_ids",
    "organizations",
    "organization_ids",
    "location",
    "location_id",
    "content_untrusted",
]

DISPLAYED_ATTRIBUTES = [
    "id",
    "kind",
    "identifier",
    "schema_version",
    "name",
    "headline",
    "title",
    "occupations",
    "occupation_ids",
    "industries",
    "industry_ids",
    "location",
    "location_id",
    "location_country_code",
    "location_region",
    "location_city",
    "skills",
    "skill_ids",
    "languages",
    "language_ids",
    "language_proficiencies",
    "seniority",
    "seniority_id",
    "work_modes",
    "availability_status",
    "availability_from",
    "open_to",
    "open_to_ids",
    "organizations",
    "organization_ids",
    "representation_status",
    "representative",
    "representative_id",
    "contact_disclosure",
    "taxonomy_versions",
    "updated_at",
    "version",
    "excerpt",
    "html_url",
    "markdown_url",
    "visibility",
]


def _quoted(value: str) -> str:
    if any(ord(character) < 0x20 for character in value):
        raise SearchUnavailable("search filters cannot contain control characters")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _reference_id(reference: dict[str, Any]) -> str:
    return f"{reference['scheme']}:{reference['id']}"


def _reference_version(reference: dict[str, Any]) -> str | None:
    version = reference.get("version")
    return f"{reference['scheme']}@{version}" if version is not None else None


def _project_frontmatter(frontmatter: dict[str, Any]) -> dict[str, Any]:
    """Flatten validated frontmatter into stable, facet-ready search fields."""
    if frontmatter["schema_version"] == 1:
        return {
            "schema_version": 1,
            "occupations": [],
            "occupation_ids": [],
            "industries": [],
            "industry_ids": [],
            "location": frontmatter["location"],
            "location_id": None,
            "location_country_code": None,
            "location_region": None,
            "location_city": None,
            "skills": frontmatter["skills"],
            "skill_ids": [],
            "languages": [],
            "language_ids": [],
            "language_proficiencies": [],
            "seniority": None,
            "seniority_id": None,
            "work_modes": [],
            "availability_status": None,
            "availability_from": None,
            "open_to": [],
            "open_to_ids": [],
            "organizations": [],
            "organization_ids": [],
            "representation_status": None,
            "representative": None,
            "representative_id": None,
            "contact_disclosure": None,
            "taxonomy_versions": [],
        }

    occupations = frontmatter["occupations"]
    industries = frontmatter["industries"]
    location = frontmatter["location"]
    skills = frontmatter["skills"]
    languages = frontmatter["languages"]
    seniority = frontmatter["seniority"]
    open_to = frontmatter["open_to"]
    organizations = frontmatter["organizations"]
    representation = frontmatter["public_representation"]
    representative = representation.get("representative")
    taxonomy_references = [
        *occupations,
        *industries,
        location,
        *skills,
        *languages,
        seniority,
        *open_to,
        *organizations,
    ]
    taxonomy_versions = sorted(
        {
            version
            for reference in taxonomy_references
            if (version := _reference_version(reference)) is not None
        }
    )
    return {
        "schema_version": 2,
        "occupations": [reference["label"] for reference in occupations],
        "occupation_ids": [_reference_id(reference) for reference in occupations],
        "industries": [reference["label"] for reference in industries],
        "industry_ids": [_reference_id(reference) for reference in industries],
        "location": location["label"],
        "location_id": _reference_id(location),
        "location_country_code": location.get("country_code"),
        "location_region": location.get("region"),
        "location_city": location.get("city"),
        "skills": [reference["label"] for reference in skills],
        "skill_ids": [_reference_id(reference) for reference in skills],
        "languages": [reference["label"] for reference in languages],
        "language_ids": [_reference_id(reference) for reference in languages],
        "language_proficiencies": [reference["proficiency"] for reference in languages],
        "seniority": seniority["label"],
        "seniority_id": _reference_id(seniority),
        "work_modes": frontmatter["work_modes"],
        "availability_status": frontmatter["availability"]["status"],
        "availability_from": frontmatter["availability"].get("available_from"),
        "open_to": [reference["label"] for reference in open_to],
        "open_to_ids": [_reference_id(reference) for reference in open_to],
        "organizations": [reference["label"] for reference in organizations],
        "organization_ids": [_reference_id(reference) for reference in organizations],
        "representation_status": representation["status"],
        "representative": representative["label"] if representative is not None else None,
        "representative_id": (
            _reference_id(representative) if representative is not None else None
        ),
        "contact_disclosure": frontmatter["contact"]["disclosure"],
        "taxonomy_versions": taxonomy_versions,
    }


class MeiliSearchProjection:
    def __init__(self, settings: SearchSettings) -> None:
        self.settings = settings
        self._configured = False
        self._setup_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self.settings.meilisearch_url is not None and bool(self.settings.meilisearch_api_key)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.meilisearch_api_key}"}

    def _url(self, suffix: str) -> str:
        assert self.settings.meilisearch_url is not None
        return str(self.settings.meilisearch_url).rstrip("/") + suffix

    async def health(self) -> bool:
        if not self.enabled:
            return False
        try:
            await self.check_ready()
            return True
        except SearchUnavailable:
            return False

    async def check_ready(self) -> None:
        """Authenticate the configured key against the exact existing index."""
        if not self.enabled:
            raise SearchUnavailable("Meilisearch is not configured")
        try:
            async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as client:
                response = await client.get(
                    self._url(f"/indexes/{self.settings.meilisearch_index}"),
                    headers=self._headers(),
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SearchUnavailable("Meilisearch index authentication or readiness failed") from exc

    async def require_index(self) -> None:
        """Read-only index gate used by API search and the projection worker."""
        if not self.enabled or self._configured:
            if not self.enabled:
                raise SearchUnavailable("Meilisearch is not configured")
            return
        async with self._setup_lock:
            if self._configured:
                return
            try:
                async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                    existing = await client.get(
                        self._url(f"/indexes/{self.settings.meilisearch_index}"),
                        headers=self._headers(),
                    )
                    existing.raise_for_status()
                self._configured = True
            except httpx.HTTPError as exc:
                raise SearchUnavailable("Meilisearch index is unavailable") from exc

    async def configure_index(self) -> None:
        """Idempotently create/configure the index for explicit admin operations only."""
        if not self.enabled:
            raise SearchUnavailable("Meilisearch is not configured")
        async with self._setup_lock:
            try:
                async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                    existing = await client.get(
                        self._url(f"/indexes/{self.settings.meilisearch_index}"),
                        headers=self._headers(),
                    )
                    if existing.status_code == 404:
                        created = await client.post(
                            self._url("/indexes"),
                            headers=self._headers(),
                            json={"uid": self.settings.meilisearch_index, "primaryKey": "id"},
                        )
                        created.raise_for_status()
                        await self._wait_for_task(client, created, "index creation")
                    else:
                        existing.raise_for_status()
                    configured = await client.patch(
                        self._url(f"/indexes/{self.settings.meilisearch_index}/settings"),
                        headers=self._headers(),
                        json={
                            "filterableAttributes": FILTERABLE_ATTRIBUTES,
                            "searchableAttributes": SEARCHABLE_ATTRIBUTES,
                            "displayedAttributes": DISPLAYED_ATTRIBUTES,
                            "sortableAttributes": ["updated_at"],
                            "pagination": {"maxTotalHits": MAX_AGENT_SEARCH_RESULTS},
                        },
                    )
                    configured.raise_for_status()
                    await self._wait_for_task(client, configured, "index configuration")
                self._configured = True
            except httpx.HTTPError as exc:
                raise SearchUnavailable("Meilisearch index setup is unavailable") from exc

    async def _wait_for_task(
        self,
        client: httpx.AsyncClient,
        response: httpx.Response,
        operation: str,
        *,
        missing_index_ok: bool = False,
    ) -> None:
        try:
            submission = response.json()
        except ValueError as exc:
            raise SearchUnavailable(f"Meilisearch returned invalid {operation} task JSON") from exc
        task_id = submission.get("taskUid") if isinstance(submission, dict) else None
        if not isinstance(task_id, int):
            raise SearchUnavailable(f"Meilisearch did not confirm {operation} task creation")
        for _ in range(50):
            try:
                task = await client.get(self._url(f"/tasks/{task_id}"), headers=self._headers())
                task.raise_for_status()
                payload = task.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise SearchUnavailable(
                    f"Meilisearch returned an invalid {operation} task response"
                ) from exc
            state = payload.get("status") if isinstance(payload, dict) else None
            if state == "succeeded":
                return
            if (
                state == "failed"
                and missing_index_ok
                and isinstance(payload.get("error"), dict)
                and payload["error"].get("code") == "index_not_found"
            ):
                return
            if state in {"failed", "canceled"}:
                raise SearchUnavailable(f"Meilisearch rejected {operation}")
            if state not in {"enqueued", "processing"}:
                raise SearchUnavailable(f"Meilisearch returned an invalid {operation} task state")
            await anyio.sleep(0.1)
        raise SearchUnavailable(f"Meilisearch {operation} did not finish before timeout")

    async def reset_index(self) -> None:
        """Delete stale projection state, then recreate configured empty index."""
        if not self.enabled:
            raise SearchUnavailable("Meilisearch is not configured")
        self._configured = False
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                response = await client.delete(
                    self._url(f"/indexes/{self.settings.meilisearch_index}"),
                    headers=self._headers(),
                )
                if response.status_code != 404:
                    response.raise_for_status()
                    await self._wait_for_task(
                        client, response, "index reset", missing_index_ok=True
                    )
        except httpx.HTTPError as exc:
            raise SearchUnavailable("Meilisearch index reset is unavailable") from exc
        await self.configure_index()

    async def index(self, document: Document, markdown: str) -> None:
        # Private canonical bytes never enter the projection. Only the durable
        # worker and explicit rebuild path call this writer primitive.
        if document.visibility != "public":
            await self.delete_document(document.id)
            return
        if not self.enabled:
            raise SearchUnavailable("Meilisearch is not configured; projection work is pending")
        await self.require_index()
        frontmatter, body = validate_canonical(document.kind, markdown)
        discovery = _project_frontmatter(frontmatter)
        payload = {
            "id": document.id,
            "kind": document.kind,
            "visibility": document.visibility,
            "version": document.current_version,
            "identifier": document.public_identifier,
            "name": frontmatter["name"],
            "headline": frontmatter["headline"],
            "title": frontmatter.get("title"),
            **discovery,
            "updated_at": frontmatter["updated_at"],
            # A bounded structured summary is returned instead of exposing the
            # searchable free-form body through the result contract.
            "excerpt": str(frontmatter["headline"])[:240],
            "html_url": (
                f"/p/{document.public_identifier}"
                if document.kind == "profile"
                else f"/r/{document.public_identifier}"
            ),
            # User-authored body text may contribute to recall, but is never a
            # displayed/retrievable attribute or a source of structured metadata.
            "content_untrusted": body,
            "markdown_url": (
                f"/v1/profiles/{document.public_identifier}.md"
                if document.kind == "profile"
                else f"/v1/resumes/{document.public_identifier}.md"
            ),
        }
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                response = await client.put(
                    self._url(f"/indexes/{self.settings.meilisearch_index}/documents"),
                    headers=self._headers(),
                    json=[payload],
                )
                response.raise_for_status()
                await self._wait_for_task(client, response, "document indexing")
        except httpx.HTTPError as exc:
            raise SearchUnavailable(
                "Meilisearch indexing failed; canonical document was saved"
            ) from exc

    async def delete_document(self, document_id: str) -> SearchDeleteAttestation:
        """Delete one exact projection document without creating or resetting an index."""
        if not self.enabled:
            return SearchDeleteAttestation(configured=False, state="unconfigured")
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                response = await client.delete(
                    self._url(
                        f"/indexes/{self.settings.meilisearch_index}/documents/{document_id}"
                    ),
                    headers=self._headers(),
                )
                if response.status_code == 404:
                    # An absent index necessarily attests that this document is
                    # absent too. Do not create an index merely to delete from it.
                    self._configured = False
                    return SearchDeleteAttestation(configured=True, state="absent")
                response.raise_for_status()
                await self._wait_for_task(client, response, "document deletion")
                residual = await client.get(
                    self._url(
                        f"/indexes/{self.settings.meilisearch_index}/documents/{document_id}"
                    ),
                    headers=self._headers(),
                )
                if residual.status_code != 404:
                    if residual.is_success:
                        raise SearchUnavailable("Meilisearch retained a deleted document")
                    residual.raise_for_status()
                    raise SearchUnavailable("Meilisearch did not attest document absence")
        except SearchUnavailable:
            raise
        except httpx.HTTPError as exc:
            raise SearchUnavailable("Meilisearch document deletion is unavailable") from exc
        return SearchDeleteAttestation(configured=True, state="deleted")

    async def search(
        self,
        *,
        query: str,
        kind: str | None,
        skills: list[str],
        location: str | None,
        owner_id: str | None,
        skill_ids: list[str] | None = None,
        occupation_ids: list[str] | None = None,
        industry_ids: list[str] | None = None,
        location_id: str | None = None,
        location_country_code: str | None = None,
        location_region: str | None = None,
        location_city: str | None = None,
        language_ids: list[str] | None = None,
        seniority_ids: list[str] | None = None,
        seniority_id: str | None = None,
        work_modes: list[str] | None = None,
        availability_status: str | None = None,
        availability_from: str | None = None,
        open_to_ids: list[str] | None = None,
        organization_ids: list[str] | None = None,
        representative_ids: list[str] | None = None,
        representation_status: str | None = None,
        contact_disclosure: str | None = None,
        updated_after: str | None = None,
        updated_before: str | None = None,
        sort_updated: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        if not self.enabled:
            raise SearchUnavailable("Meilisearch is not configured")
        await self.require_index()
        filters = [f"visibility = {_quoted('public')}"]
        if kind:
            filters.append(f"kind = {_quoted(kind)}")
        if location:
            filters.append(f"location = {_quoted(location)}")
        filters.extend(f"skills = {_quoted(skill)}" for skill in skills)
        rich_multi_filters = {
            "skill_ids": skill_ids or [],
            "occupation_ids": occupation_ids or [],
            "industry_ids": industry_ids or [],
            "language_ids": language_ids or [],
            "work_modes": work_modes or [],
            "open_to_ids": open_to_ids or [],
            "organization_ids": organization_ids or [],
        }
        for field, values in rich_multi_filters.items():
            filters.extend(f"{field} = {_quoted(value)}" for value in values)
        normalized_representative_ids = list(dict.fromkeys(representative_ids or []))
        if normalized_representative_ids:
            filters.append(
                "("
                + " OR ".join(
                    f"representative_id = {_quoted(value)}"
                    for value in normalized_representative_ids
                )
                + ")"
            )
        normalized_seniority_ids = list(
            dict.fromkeys(
                [
                    *(seniority_ids or []),
                    *([seniority_id] if seniority_id is not None else []),
                ]
            )
        )
        if normalized_seniority_ids:
            filters.append(
                "("
                + " OR ".join(
                    f"seniority_id = {_quoted(value)}" for value in normalized_seniority_ids
                )
                + ")"
            )
        rich_single_filters = {
            "location_id": location_id,
            "location_country_code": location_country_code,
            "location_region": location_region,
            "location_city": location_city,
            "availability_status": availability_status,
            "availability_from": availability_from,
            "representation_status": representation_status,
            "contact_disclosure": contact_disclosure,
        }
        for field, value in rich_single_filters.items():
            if value is not None:
                filters.append(f"{field} = {_quoted(value)}")
        if updated_after is not None:
            filters.append(f"updated_at >= {_quoted(updated_after)}")
        if updated_before is not None:
            filters.append(f"updated_at <= {_quoted(updated_before)}")
        if sort_updated not in {None, "asc", "desc"}:
            raise SearchUnavailable("updated sort direction must be 'asc' or 'desc'")
        payload = {
            "q": query,
            "filter": " AND ".join(filters),
            "offset": 0,
            "limit": MAX_AGENT_SEARCH_RESULTS,
            "attributesToRetrieve": DISPLAYED_ATTRIBUTES,
        }
        if sort_updated is not None:
            payload["sort"] = [f"updated_at:{sort_updated}"]
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                response = await client.post(
                    self._url(f"/indexes/{self.settings.meilisearch_index}/search"),
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
                result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SearchUnavailable("Meilisearch search is unavailable") from exc
        if not isinstance(result, dict) or not isinstance(result.get("hits"), list):
            raise SearchUnavailable("Meilisearch returned an invalid search response")
        hits = result["hits"]
        if not all(isinstance(hit, dict) for hit in hits):
            raise SearchUnavailable("Meilisearch returned invalid search hits")
        total = result.get("estimatedTotalHits", len(hits))
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise SearchUnavailable("Meilisearch returned an invalid result count")
        response_fields = [field for field in DISPLAYED_ATTRIBUTES if field != "visibility"]
        safe_hits = [
            {field: hit[field] for field in response_fields if field in hit}
            for hit in hits
            if hit.get("visibility") == "public"
        ]
        return safe_hits, int(total)
