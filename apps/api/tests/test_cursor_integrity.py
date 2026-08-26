from __future__ import annotations

import base64
import json

import pytest

from app.services.cursors import CursorCodec, CursorError

SECRET = b"cursor-test-secret-0123456789-0123456789"
PAYLOAD = {"v": 1, "scope": "documents", "updated_at": "2026-08-15T00:00:00Z", "id": "d1"}


def _outer_bytes(cursor: str) -> bytes:
    return base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))


def _cursor_from_outer(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_cursor_round_trip_is_versioned_and_bound() -> None:
    codec = CursorCodec(SECRET)
    cursor = codec.encode(PAYLOAD, scope="documents", bindings=("owner-a", "profile"))

    assert isinstance(cursor, str)
    assert cursor
    assert codec.decode(cursor, scope="documents", bindings=("owner-a", "profile")) == PAYLOAD


def test_cursor_round_trip_preserves_unicode_payload() -> None:
    codec = CursorCodec(SECRET)
    payload = {**PAYLOAD, "label": "Café 日本"}
    cursor = codec.encode(payload, scope="documents", bindings=("owner-a",))

    assert codec.decode(cursor, scope="documents", bindings=("owner-a",)) == payload


@pytest.mark.parametrize("variant", ["whitespace", "key_order"])
def test_cursor_rejects_noncanonical_outer_bytes_with_original_valid_signature(
    variant: str,
) -> None:
    codec = CursorCodec(SECRET)
    cursor = codec.encode(PAYLOAD, scope="documents", bindings=("owner-a",))
    raw = _outer_bytes(cursor)
    envelope = json.loads(raw)
    if variant == "whitespace":
        noncanonical = b" " + raw + b"\n"
    else:
        noncanonical = json.dumps(
            dict(reversed(list(envelope.items()))), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")

    assert noncanonical != raw
    with pytest.raises(CursorError):
        codec.decode(_cursor_from_outer(noncanonical), scope="documents", bindings=("owner-a",))


def test_cursor_rejects_unused_base64url_padding_bits_and_explicit_padding() -> None:
    codec = CursorCodec(SECRET)
    cursor = codec.encode({**PAYLOAD, "x": ""}, scope="documents", bindings=("owner-a",))
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    last_index = alphabet.index(cursor[-1])
    unused_mask = 0x0F if len(cursor) % 4 == 2 else 0x03
    assert len(cursor) % 4 in {2, 3}
    assert last_index & unused_mask == 0
    mutated = cursor[:-1] + alphabet[last_index | 1]
    assert _outer_bytes(mutated) == _outer_bytes(cursor)

    with pytest.raises(CursorError):
        codec.decode(mutated, scope="documents", bindings=("owner-a",))
    with pytest.raises(CursorError):
        codec.decode(cursor + "=", scope="documents", bindings=("owner-a",))


@pytest.mark.parametrize("key", ["__cursor_v", "__cursor_sig", "id"])
def test_cursor_rejects_duplicate_reserved_or_payload_keys_with_valid_signature(key: str) -> None:
    codec = CursorCodec(SECRET)
    cursor = codec.encode(PAYLOAD, scope="documents", bindings=("owner-a",))
    envelope = json.loads(_outer_bytes(cursor))
    duplicate = (
        _outer_bytes(cursor)[:-1]
        + b","
        + json.dumps(key).encode("utf-8")
        + b":"
        + json.dumps(envelope[key], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"}"
    )

    with pytest.raises(CursorError):
        codec.decode(_cursor_from_outer(duplicate), scope="documents", bindings=("owner-a",))


@pytest.mark.parametrize(
    ("mutation", "scope", "bindings"),
    [
        ("tamper", "documents", ("owner-a", "profile")),
        ("scope", "public_documents", ("owner-a", "profile")),
        ("binding", "documents", ("owner-b", "profile")),
    ],
)
def test_cursor_rejects_tamper_scope_substitution_and_binding_replay(
    mutation: str, scope: str, bindings: tuple[str, ...]
) -> None:
    codec = CursorCodec(SECRET)
    cursor = codec.encode(PAYLOAD, scope="documents", bindings=("owner-a", "profile"))
    if mutation == "tamper":
        envelope = json.loads(base64.urlsafe_b64decode(cursor + "=="))
        envelope["id"] = "d2"
        cursor = (
            base64.urlsafe_b64encode(
                json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()
            )
            .decode()
            .rstrip("=")
        )
    with pytest.raises(CursorError):
        codec.decode(cursor, scope=scope, bindings=bindings)


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "unsigned-token",
        "not.base64",
        "v2-token",
        "eyJfX2N1cnNvcl92IjoxfQ",
    ],
)
def test_cursor_rejects_malformed_and_legacy_tokens(cursor: str) -> None:
    with pytest.raises(CursorError):
        CursorCodec(SECRET).decode(cursor, scope="documents", bindings=("owner-a",))


def test_cursor_rejects_nonfinite_json_and_overbound_values() -> None:
    codec = CursorCodec(SECRET)
    with pytest.raises(CursorError):
        codec.encode({"v": 1, "value": float("nan")}, scope="documents")
    with pytest.raises(CursorError):
        codec.encode({"v": 1, "value": "x" * 3000}, scope="documents")
    cursor = codec.encode(PAYLOAD, scope="documents")
    raw = base64.urlsafe_b64decode(cursor + "==")
    envelope = json.loads(raw)
    envelope["id"] = "x" * 3_000
    oversized_body = (
        base64.urlsafe_b64encode(
            json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()
        )
        .decode()
        .rstrip("=")
    )
    with pytest.raises(CursorError):
        codec.decode(oversized_body, scope="documents")


def test_cursor_requires_a_real_secret() -> None:
    with pytest.raises(ValueError):
        CursorCodec(b"too-short")
