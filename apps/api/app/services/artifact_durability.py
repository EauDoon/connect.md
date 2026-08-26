from __future__ import annotations

import asyncio
import hashlib
import json
import re
import struct
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from hmac import compare_digest
from hmac import new as hmac_new
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.storage import StorageIntegrityError, VersionStore

ARTIFACT_INTENT_DOMAIN = "connect.md/artifact-intent-uuid"
ARTIFACT_INTENT_VERSION = "1"
ARTIFACT_DESCRIPTOR_VERSION = 1
ARTIFACT_STAGE_GRACE_NS = 60 * 60 * 1_000_000_000
ARTIFACT_SCAN_LIMIT = 100
ARTIFACT_RECONCILE_INTERVAL_SECONDS = 60
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")

ArtifactFlow = Literal[
    "application_snapshot",
    "organization_verification_evidence",
    "canonical_document_version",
    "professional_post",
]
ArtifactAuthority = Literal["committed", "absent", "uncertain"]

CANONICAL_DOCUMENT_CREATE_TARGET_IDS = {
    kind: str(uuid5(NAMESPACE_URL, f"https://connect.md/artifact-target/{kind}-create/v1"))
    for kind in ("profile", "resume")
}
PROFESSIONAL_POST_CREATE_TARGET_ID = str(
    uuid5(NAMESPACE_URL, "https://connect.md/artifact-target/professional-post-create/v1")
)
_CANONICAL_DOCUMENT_PATH = re.compile(
    r"^(?P<directory>profiles|resumes)/"
    r"(?P<resource_id>[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/"
    r"versions/(?P<version>[0-9]{6})\.md$"
)
_PROFESSIONAL_POST_PATH = re.compile(
    r"^posts/"
    r"(?P<resource_id>[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/"
    r"versions/000001\.md$"
)


class ArtifactDurabilityUnavailable(RuntimeError):
    pass


def _artifact_max_size(flow: ArtifactFlow) -> int:
    if flow in {"application_snapshot", "canonical_document_version"}:
        return 131_072
    if flow == "professional_post":
        return 10_240
    return 262_144


@dataclass(frozen=True)
class ArtifactDescriptor:
    descriptor_version: int
    flow: ArtifactFlow
    intent_id: str
    resource_id: str
    target_id: str
    owner_binding: str
    request_hash: str
    canonical_path: str
    payload_sha256: str
    payload_size_bytes: int
    staged_payload_path: str
    staged_descriptor_path: str
    created_ns: int


@dataclass
class ArtifactIntentGateLease:
    reconciler: ArtifactReconciler
    intent_id: str
    lock: asyncio.Lock
    released: bool = False

    async def release(self) -> None:
        if self.released:
            return
        self.released = True
        self.lock.release()
        async with self.reconciler._gate_guard:
            current = self.reconciler._gate_locks.get(self.intent_id)
            if current is None:
                return
            lock, references = current
            if lock is not self.lock or references < 1:
                raise RuntimeError("artifact intent gate registry is invalid")
            if references == 1:
                self.reconciler._gate_locks.pop(self.intent_id, None)
            else:
                self.reconciler._gate_locks[self.intent_id] = (lock, references - 1)


def _frame(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack(">I", len(encoded)) + encoded


def derive_artifact_intent_uuid(
    pepper: str,
    *,
    flow: ArtifactFlow,
    owner_id: str,
    target_id: str,
    idempotency_key: str,
) -> str:
    if not pepper or not owner_id or not target_id or not idempotency_key:
        raise ValueError("artifact intent inputs are required")
    framed = b"".join(
        _frame(value)
        for value in (
            ARTIFACT_INTENT_DOMAIN,
            ARTIFACT_INTENT_VERSION,
            flow,
            owner_id,
            target_id,
            idempotency_key,
        )
    )
    digest = bytearray(hmac_new(pepper.encode("utf-8"), framed, hashlib.sha256).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x40
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(UUID(bytes=bytes(digest)))


def _owner_binding(pepper: str, owner_id: str) -> str:
    return hmac_new(
        pepper.encode("utf-8"),
        _frame("connect.md/artifact-owner-binding/v1") + _frame(owner_id),
        hashlib.sha256,
    ).hexdigest()


def descriptor_owner_matches(descriptor: ArtifactDescriptor, pepper: str, owner_id: str) -> bool:
    return compare_digest(descriptor.owner_binding, _owner_binding(pepper, owner_id))


def _descriptor_payload(descriptor: ArtifactDescriptor) -> bytes:
    return json.dumps(
        asdict(descriptor), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _signed_descriptor(descriptor: ArtifactDescriptor, pepper: str) -> bytes:
    payload = _descriptor_payload(descriptor)
    envelope = {
        "descriptor": json.loads(payload),
        "signature": hmac_new(
            pepper.encode("utf-8"),
            _frame("connect.md/artifact-stage-descriptor/v1") + _frame(payload.decode("utf-8")),
            hashlib.sha256,
        ).hexdigest(),
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_signed_descriptor(
    raw: bytes, pepper: str, *, expected_descriptor_path: str
) -> ArtifactDescriptor:
    try:
        envelope = json.loads(raw.decode("utf-8"))
        if not isinstance(envelope, dict) or set(envelope) != {"descriptor", "signature"}:
            raise ValueError
        fields = envelope["descriptor"]
        signature = envelope["signature"]
        expected_fields = set(ArtifactDescriptor.__dataclass_fields__)
        if not isinstance(fields, dict) or set(fields) != expected_fields:
            raise ValueError
        canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        expected_signature = hmac_new(
            pepper.encode("utf-8"),
            _frame("connect.md/artifact-stage-descriptor/v1") + _frame(canonical),
            hashlib.sha256,
        ).hexdigest()
        if not isinstance(signature, str) or not compare_digest(signature, expected_signature):
            raise ValueError
        descriptor = ArtifactDescriptor(**fields)
        for identifier in (
            descriptor.intent_id,
            descriptor.resource_id,
            descriptor.target_id,
        ):
            if str(UUID(identifier)) != identifier:
                raise ValueError
        if (
            descriptor.descriptor_version != ARTIFACT_DESCRIPTOR_VERSION
            or descriptor.flow
            not in {
                "application_snapshot",
                "organization_verification_evidence",
                "canonical_document_version",
                "professional_post",
            }
            or (
                descriptor.flow in {"application_snapshot", "organization_verification_evidence"}
                and descriptor.resource_id != descriptor.intent_id
            )
            or descriptor.staged_descriptor_path != expected_descriptor_path
            or _HEX_SHA256.fullmatch(descriptor.owner_binding) is None
            or _HEX_SHA256.fullmatch(descriptor.request_hash) is None
            or _HEX_SHA256.fullmatch(descriptor.payload_sha256) is None
            or descriptor.payload_size_bytes < 1
            or descriptor.payload_size_bytes > _artifact_max_size(descriptor.flow)
            or descriptor.created_ns < 1
            or not descriptor.staged_payload_path.endswith(".bin")
        ):
            raise ValueError
        expected_stem = descriptor.staged_descriptor_path.removesuffix(".json")
        expected_prefix = f".connectmd-artifact-staging/v1/{descriptor.intent_id}/"
        if descriptor.flow == "application_snapshot":
            expected_canonical = f"applications/{descriptor.resource_id}/snapshot.md"
        elif descriptor.flow == "organization_verification_evidence":
            expected_canonical = (
                f"verification-evidence/{descriptor.target_id}/{descriptor.resource_id}/"
                f"{descriptor.payload_sha256}.bin"
            )
        elif descriptor.flow == "canonical_document_version":
            match = _CANONICAL_DOCUMENT_PATH.fullmatch(descriptor.canonical_path)
            if (
                match is None
                or match.group("resource_id") != descriptor.resource_id
                or int(match.group("version")) < 1
            ):
                raise ValueError
            expected_canonical = descriptor.canonical_path
        else:
            match = _PROFESSIONAL_POST_PATH.fullmatch(descriptor.canonical_path)
            if match is None or match.group("resource_id") != descriptor.resource_id:
                raise ValueError
            expected_canonical = descriptor.canonical_path
        if (
            not expected_stem.startswith(expected_prefix)
            or descriptor.staged_payload_path != f"{expected_stem}.bin"
            or descriptor.canonical_path != expected_canonical
        ):
            raise ValueError
        return descriptor
    except (AttributeError, KeyError, TypeError, UnicodeDecodeError, ValueError) as exc:
        raise ArtifactDurabilityUnavailable("artifact descriptor is unavailable") from exc


def stage_artifact(
    store: VersionStore,
    pepper: str,
    *,
    flow: ArtifactFlow,
    owner_id: str,
    target_id: str,
    idempotency_key: str,
    request_hash: str,
    canonical_path: str,
    payload: bytes,
    max_size_bytes: int,
    resource_id: str | None = None,
) -> ArtifactDescriptor:
    if not payload or len(payload) > max_size_bytes or max_size_bytes != _artifact_max_size(flow):
        raise ArtifactDurabilityUnavailable("artifact payload is unavailable")
    intent_id = derive_artifact_intent_uuid(
        pepper,
        flow=flow,
        owner_id=owner_id,
        target_id=target_id,
        idempotency_key=idempotency_key,
    )
    resolved_resource_id = resource_id or intent_id
    try:
        if str(UUID(resolved_resource_id)) != resolved_resource_id:
            raise ValueError
    except (AttributeError, TypeError, ValueError) as exc:
        raise ArtifactDurabilityUnavailable("artifact resource is unavailable") from exc

    prefix = f".connectmd-artifact-staging/v1/{intent_id}/"
    try:
        scan = store.scan_staged_artifacts(limit=ARTIFACT_SCAN_LIMIT)
        if scan.invalid_entry or scan.overbound:
            raise StorageIntegrityError("artifact staging inventory is unavailable")
        matching_descriptors = [path for path in scan.descriptors if path.startswith(prefix)]
        matching_incomplete = [path for path in scan.incomplete_payloads if path.startswith(prefix)]
        if matching_incomplete or len(matching_descriptors) > 1:
            raise StorageIntegrityError("artifact staging intent is unavailable")
        if matching_descriptors:
            existing = parse_signed_descriptor(
                store.read_staged_descriptor(matching_descriptors[0]),
                pepper,
                expected_descriptor_path=matching_descriptors[0],
            )
            if (
                existing.flow != flow
                or existing.intent_id != intent_id
                or existing.resource_id != resolved_resource_id
                or existing.target_id != target_id
                or not descriptor_owner_matches(existing, pepper, owner_id)
                or existing.request_hash != request_hash
                or existing.canonical_path != canonical_path
            ):
                raise StorageIntegrityError("artifact staging intent is unavailable")
            store.read_verified_bytes(
                existing.staged_payload_path,
                existing.payload_sha256,
                expected_size_bytes=existing.payload_size_bytes,
                max_size_bytes=max_size_bytes,
            )
            store.promote_staged_artifact(
                existing.staged_payload_path,
                existing.canonical_path,
                expected_sha256=existing.payload_sha256,
                expected_size_bytes=existing.payload_size_bytes,
                max_size_bytes=max_size_bytes,
            )
            return existing
    except (ArtifactDurabilityUnavailable, StorageIntegrityError) as exc:
        raise ArtifactDurabilityUnavailable("artifact staging is unavailable") from exc

    created_ns = time.time_ns()
    nonce = str(uuid4())
    stem = f".connectmd-artifact-staging/v1/{intent_id}/{created_ns:019d}-{nonce}"
    descriptor = ArtifactDescriptor(
        descriptor_version=ARTIFACT_DESCRIPTOR_VERSION,
        flow=flow,
        intent_id=intent_id,
        resource_id=resolved_resource_id,
        target_id=target_id,
        owner_binding=_owner_binding(pepper, owner_id),
        request_hash=request_hash,
        canonical_path=canonical_path,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload_size_bytes=len(payload),
        staged_payload_path=f"{stem}.bin",
        staged_descriptor_path=f"{stem}.json",
        created_ns=created_ns,
    )
    try:
        created = store.stage_artifact(
            intent_id,
            payload,
            _signed_descriptor(descriptor, pepper),
            created_ns=created_ns,
            nonce=nonce,
        )
        if (
            created.payload_path != descriptor.staged_payload_path
            or created.descriptor_path != descriptor.staged_descriptor_path
        ):
            raise StorageIntegrityError("artifact staging target is unavailable")
        store.promote_staged_artifact(
            descriptor.staged_payload_path,
            canonical_path,
            expected_sha256=descriptor.payload_sha256,
            expected_size_bytes=descriptor.payload_size_bytes,
            max_size_bytes=max_size_bytes,
        )
    except StorageIntegrityError as exc:
        raise ArtifactDurabilityUnavailable("artifact staging is unavailable") from exc
    return descriptor


async def acquire_artifact_intent_lock(session: AsyncSession, intent_id: str) -> None:
    UUID(intent_id)
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"connect.md:artifact-intent:v1:{intent_id}"},
        )


class ArtifactReconciler:
    def __init__(
        self,
        store: VersionStore,
        pepper: str,
        classify: Callable[[ArtifactDescriptor], Awaitable[ArtifactAuthority]],
        *,
        enabled: bool,
        classify_incomplete: Callable[[str, str], Awaitable[ArtifactAuthority]] | None = None,
    ) -> None:
        self.store = store
        self.pepper = pepper
        self.classify = classify
        self.classify_incomplete = classify_incomplete
        self.enabled = enabled
        self.status: Literal["disabled", "not_attempted", "ready", "unavailable"] = (
            "not_attempted" if enabled else "disabled"
        )
        self._gate_guard = asyncio.Lock()
        self._gate_locks: dict[str, tuple[asyncio.Lock, int]] = {}

    def mark_unavailable(self) -> None:
        if self.enabled:
            self.status = "unavailable"

    async def acquire_intent_gate(self, intent_id: str) -> ArtifactIntentGateLease:
        UUID(intent_id)
        async with self._gate_guard:
            lock, references = self._gate_locks.get(intent_id, (asyncio.Lock(), 0))
            self._gate_locks[intent_id] = (lock, references + 1)
        try:
            await lock.acquire()
        except BaseException:
            async with self._gate_guard:
                current_lock, current_references = self._gate_locks[intent_id]
                if current_references == 1:
                    self._gate_locks.pop(intent_id, None)
                else:
                    self._gate_locks[intent_id] = (current_lock, current_references - 1)
            raise
        return ArtifactIntentGateLease(self, intent_id, lock)

    async def reconcile_descriptor(
        self,
        descriptor: ArtifactDescriptor,
        *,
        respect_grace: bool,
        gate_held: bool = False,
    ) -> ArtifactAuthority:
        if respect_grace and time.time_ns() - descriptor.created_ns < ARTIFACT_STAGE_GRACE_NS:
            return "uncertain"
        lease: ArtifactIntentGateLease | None = None
        try:
            if not gate_held:
                lease = await self.acquire_intent_gate(descriptor.intent_id)
            self.store.read_verified_bytes(
                descriptor.staged_payload_path,
                descriptor.payload_sha256,
                expected_size_bytes=descriptor.payload_size_bytes,
                max_size_bytes=_artifact_max_size(descriptor.flow),
            )
            authority = await self.classify(descriptor)
            if authority == "committed":
                self.store.retire_staged_artifact(
                    descriptor.staged_payload_path, descriptor.staged_descriptor_path
                )
            elif authority == "absent":
                self.store.delete_verified_exact(
                    descriptor.canonical_path,
                    descriptor.payload_sha256,
                    expected_size_bytes=descriptor.payload_size_bytes,
                    max_size_bytes=_artifact_max_size(descriptor.flow),
                )
                self.store.retire_staged_artifact(
                    descriptor.staged_payload_path, descriptor.staged_descriptor_path
                )
            elif not respect_grace:
                self.mark_unavailable()
            return authority
        except asyncio.CancelledError:
            raise
        except (ArtifactDurabilityUnavailable, StorageIntegrityError, OSError, ValueError):
            if not respect_grace:
                self.mark_unavailable()
            return "uncertain"
        finally:
            if lease is not None:
                await lease.release()

    async def run_once(self) -> None:
        if not self.enabled:
            return
        attempted = True
        unavailable = False
        try:
            scan = self.store.scan_staged_artifacts(limit=ARTIFACT_SCAN_LIMIT)
            if scan.invalid_entry or scan.overbound:
                unavailable = True
                return
            parsed_descriptors: list[ArtifactDescriptor] = []
            for descriptor_path in scan.descriptors:
                try:
                    parsed_descriptors.append(
                        parse_signed_descriptor(
                            self.store.read_staged_descriptor(descriptor_path),
                            self.pepper,
                            expected_descriptor_path=descriptor_path,
                        )
                    )
                except (ArtifactDurabilityUnavailable, StorageIntegrityError):
                    unavailable = True
                    return
            incomplete: list[tuple[str, str, int]] = []
            for payload_path in scan.incomplete_payloads:
                try:
                    raw = payload_path.split("/")
                    intent_id = raw[2]
                    created_ns = int(raw[3].split("-", 1)[0])
                    UUID(intent_id)
                    incomplete.append((payload_path, intent_id, created_ns))
                except (IndexError, TypeError, ValueError):
                    unavailable = True
                    return
            for payload_path, intent_id, created_ns in incomplete:
                if time.time_ns() - created_ns < ARTIFACT_STAGE_GRACE_NS:
                    continue
                if self.classify_incomplete is None:
                    unavailable = True
                    continue
                lease = await self.acquire_intent_gate(intent_id)
                try:
                    outcome = await self.classify_incomplete(intent_id, payload_path)
                finally:
                    await lease.release()
                if outcome == "absent":
                    self.store.retire_incomplete_staged_payload(payload_path)
                else:
                    unavailable = True
            for descriptor in parsed_descriptors:
                outcome = await self.reconcile_descriptor(descriptor, respect_grace=True)
                if outcome == "uncertain" and time.time_ns() - descriptor.created_ns >= (
                    ARTIFACT_STAGE_GRACE_NS
                ):
                    unavailable = True
        except asyncio.CancelledError:
            unavailable = True
            raise
        except (ArtifactDurabilityUnavailable, StorageIntegrityError, OSError, ValueError):
            unavailable = True
        except Exception:
            # An unexpected scan/classification failure must not terminate the
            # periodic loop or leave readiness falsely green.  The next cycle
            # is still allowed to retry after the fail-closed state is exposed.
            unavailable = True
        finally:
            if attempted:
                self.status = "unavailable" if unavailable else "ready"

    async def run_forever(self) -> None:
        while True:
            await asyncio.sleep(ARTIFACT_RECONCILE_INTERVAL_SECONDS)
            await self.run_once()
