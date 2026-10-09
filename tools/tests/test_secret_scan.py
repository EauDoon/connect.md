"""Coverage for the secret gate's project-specific and newer credential formats.

Every token-shaped fixture is assembled at runtime from fragments. GitHub push
protection and this repository's own scanner both read this file, so no
complete credential may ever appear in it literally.
"""

from __future__ import annotations

import base64
import os
import secrets
import unittest

from tools import secret_scan


def payloads(secret: bytes) -> tuple[tuple[str, bytes], ...]:
    """The raw-bytes shapes the scanner must still inspect."""

    return (
        ("ordinary", secret),
        ("over-five-megabytes", b"x" * (5 * 1024 * 1024 + 1) + b"\n" + secret),
        ("nul-prefixed", b"\0" + secret),
    )


def urlsafe_43() -> bytes:
    """32 random bytes, base64url without padding: the shape every generator uses."""

    return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=")


def connect_token(prefix: bytes, body: bytes) -> bytes:
    return prefix + b"_" + body


class SecretScanTests(unittest.TestCase):
    def assert_labels(self, data: bytes, expected: tuple[str, ...]) -> None:
        for shape, payload in payloads(data):
            with self.subTest(shape=shape):
                self.assertEqual(secret_scan.find_secret_labels(payload), expected)

    def test_detects_connect_md_bearer_tokens_in_every_shape(self) -> None:
        for prefix in (b"cn" + b"ag", b"cn" + b"d", b"cn" + b"g"):
            with self.subTest(prefix=prefix):
                body = urlsafe_43()
                self.assertEqual(len(body), 43)
                self.assert_labels(connect_token(prefix, body), ("connect.md bearer token",))
                self.assert_labels(
                    b"Authorization: Bearer " + connect_token(prefix, body) + b"\n",
                    ("connect.md bearer token",),
                )

    def test_detects_tokens_from_the_api_generator(self) -> None:
        # apps/api mints "cnd_"/"cng_" + secrets.token_urlsafe(32).
        token = (b"cn" + b"d_") + secrets.token_urlsafe(32).encode("ascii")
        self.assertEqual(secret_scan.find_secret_labels(token), ("connect.md bearer token",))

    def test_ignores_near_miss_bearer_shapes(self) -> None:
        prefix = b"cn" + b"ag"
        for data in (
            connect_token(prefix, b"A" * 20),
            connect_token(prefix, b"A" * 44),
            b"x" + connect_token(prefix, b"A" * 43),
            connect_token(b"cn" + b"x", b"A" * 43),
            b"cnag_ prefix mentioned in documentation",
        ):
            with self.subTest(data=data[:12]):
                self.assertEqual(secret_scan.find_secret_labels(data), ())

    def test_detects_fine_grained_github_tokens(self) -> None:
        token = b"github" + b"_pat_" + b"11ABCDEFG0" + b"a1B2c3D4e5_" * 7
        self.assertGreaterEqual(len(token) - len(b"github_pat_"), 60)
        self.assert_labels(token, ("GitHub fine-grained token",))
        short = b"github" + b"_pat_" + b"a" * 59
        self.assertEqual(secret_scan.find_secret_labels(short), ())

    def test_detects_npm_tokens(self) -> None:
        self.assert_labels(b"np" + b"m_" + b"aB3" * 12, ("npm token",))
        for length in (35, 37):
            with self.subTest(length=length):
                self.assertEqual(secret_scan.find_secret_labels(b"np" + b"m_" + b"a" * length), ())

    def test_detects_encrypted_and_pgp_private_key_blocks(self) -> None:
        begin = b"-----" + b"BEGIN "
        for header in (
            begin + b"ENCRYPTED " + b"PRIVATE " + b"KEY-----",
            begin + b"PGP " + b"PRIVATE " + b"KEY " + b"BLOCK-----",
        ):
            with self.subTest(header=header[11:30]):
                self.assert_labels(header + b"\nMIIB\n", ("private key block",))

    def test_unencrypted_pkcs8_keeps_its_single_original_label(self) -> None:
        header = b"-----" + b"BEGIN " + b"PRIVATE " + b"KEY-----"
        self.assertEqual(secret_scan.find_secret_labels(header), ("private key",))

    def test_original_labels_keep_their_order(self) -> None:
        stripe_like = b"sk_" + b"live_" + b"a" * 20
        self.assertEqual(
            secret_scan.find_secret_labels(stripe_like), ("Stripe secret", "Clerk secret")
        )
        self.assertEqual(list(secret_scan.PATTERNS)[:6], [
            "private key", "AWS access key", "GitHub token", "Slack token", "Stripe secret", "Clerk secret",
        ])


if __name__ == "__main__":
    unittest.main()
