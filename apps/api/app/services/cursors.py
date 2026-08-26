from __future__ import annotations

import json
import re
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Mapping, Sequence
from hashlib import sha256
from hmac import compare_digest
from hmac import new as hmac_new
from typing import Any


class CursorError(ValueError):
    """A generic cursor failed its authenticated envelope checks."""


_CURSOR_VERSION = 1
_CURSOR_MAX_LENGTH = 2048
_SCOPE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,255}\Z")
_BASE64URL_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_META_KEYS = frozenset({"__cursor_v", "__cursor_scope", "__cursor_binding", "__cursor_sig"})


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value} is not allowed")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CursorError("cursor payload is not canonical JSON") from exc


def _binding_digest(bindings: Sequence[str]) -> str:
    if not isinstance(bindings, (tuple, list)):
        raise CursorError("cursor bindings are malformed")
    if len(bindings) > 16:
        raise CursorError("cursor bindings are malformed")
    normalized: list[str] = []
    for value in bindings:
        if not isinstance(value, str) or len(value) > 2048:
            raise CursorError("cursor bindings are malformed")
        normalized.append(value)
    return sha256(_canonical_json(normalized)).hexdigest()


class CursorCodec:
    """Versioned, scope- and binding-bound HMAC cursor codec.

    The secret is supplied by the application. This class never derives a
    fallback secret and never logs or persists cursor material.
    """

    def __init__(self, secret: bytes, *, max_length: int = _CURSOR_MAX_LENGTH) -> None:
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("cursor secret must contain at least 32 bytes")
        if isinstance(max_length, bool) or not isinstance(max_length, int):
            raise ValueError("cursor max length is invalid")
        if max_length < 128 or max_length > _CURSOR_MAX_LENGTH:
            raise ValueError("cursor max length is invalid")
        self._secret = bytes(secret)
        self._max_length = max_length

    def encode(
        self, payload: Mapping[str, Any], *, scope: str, bindings: Sequence[str] = ()
    ) -> str:
        if not isinstance(payload, Mapping) or not isinstance(scope, str):
            raise CursorError("cursor payload is malformed")
        if not _SCOPE_PATTERN.fullmatch(scope):
            raise CursorError("cursor scope is malformed")
        if _META_KEYS.intersection(payload):
            raise CursorError("cursor payload is malformed")
        envelope = {
            **dict(payload),
            "__cursor_binding": _binding_digest(bindings),
            "__cursor_scope": scope,
            "__cursor_v": _CURSOR_VERSION,
        }
        encoded = urlsafe_b64encode(_canonical_json(envelope)).decode("ascii").rstrip("=")
        signature = (
            urlsafe_b64encode(hmac_new(self._secret, encoded.encode("ascii"), sha256).digest())
            .decode("ascii")
            .rstrip("=")
        )
        envelope["__cursor_sig"] = signature
        result = urlsafe_b64encode(_canonical_json(envelope)).decode("ascii").rstrip("=")
        if len(result) > self._max_length:
            raise CursorError("cursor exceeds the maximum length")
        return result

    def decode(self, cursor: str, *, scope: str, bindings: Sequence[str] = ()) -> dict[str, Any]:
        if (
            not isinstance(cursor, str)
            or not cursor
            or len(cursor) > self._max_length
            or not isinstance(scope, str)
            or not _SCOPE_PATTERN.fullmatch(scope)
        ):
            raise CursorError("cursor is malformed")
        if not _BASE64URL_PATTERN.fullmatch(cursor):
            raise CursorError("cursor is malformed")
        try:
            padding = "=" * (-len(cursor) % 4)
            raw = urlsafe_b64decode((cursor + padding).encode("ascii"))
            canonical_cursor = urlsafe_b64encode(raw).decode("ascii").rstrip("=")
            if cursor != canonical_cursor:
                raise ValueError("cursor base64url spelling is not canonical")
            envelope = json.loads(
                raw,
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=_reject_json_constant,
            )
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise CursorError("cursor is malformed") from exc
        if not isinstance(envelope, dict) or not _META_KEYS <= envelope.keys():
            raise CursorError("cursor is malformed")
        try:
            canonical_outer = _canonical_json(envelope)
        except CursorError as exc:
            raise CursorError("cursor is malformed") from exc
        if raw != canonical_outer:
            raise CursorError("cursor is malformed")
        supplied_signature = envelope.pop("__cursor_sig")
        if not isinstance(supplied_signature, str) or not _BASE64URL_PATTERN.fullmatch(
            supplied_signature
        ):
            raise CursorError("cursor is malformed")
        encoded = urlsafe_b64encode(_canonical_json(envelope)).decode("ascii").rstrip("=")
        expected_signature = (
            urlsafe_b64encode(hmac_new(self._secret, encoded.encode("ascii"), sha256).digest())
            .decode("ascii")
            .rstrip("=")
        )
        if not compare_digest(supplied_signature, expected_signature):
            raise CursorError("cursor is malformed")
        try:
            payload = {key: value for key, value in envelope.items() if key not in _META_KEYS}
            if (
                envelope["__cursor_v"] != _CURSOR_VERSION
                or envelope["__cursor_scope"] != scope
                or not isinstance(envelope["__cursor_binding"], str)
                or not compare_digest(envelope["__cursor_binding"], _binding_digest(bindings))
                or not isinstance(payload, dict)
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise CursorError("cursor is malformed") from exc
        return payload


__all__ = ["CursorCodec", "CursorError"]
