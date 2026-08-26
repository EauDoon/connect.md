"""One-off creation of index-scoped Meilisearch runtime keys."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass

import httpx
from pydantic import HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class BootstrapSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CONNECTMD_", extra="ignore")

    meilisearch_url: HttpUrl
    meilisearch_api_key: str
    meilisearch_index: str = "documents"


@dataclass(frozen=True)
class KeyContract:
    uid: str
    environment_key: str
    actions: tuple[str, ...]


KEY_CONTRACTS = {
    "search": KeyContract(
        uid="4dfd60e4-2ea2-4b4e-a889-1956ad3968da",
        environment_key="CONNECTMD_MEILISEARCH_SEARCH_KEY",
        actions=("search", "indexes.get"),
    ),
    "projection": KeyContract(
        uid="c1954642-d6cf-47d4-bb9d-b76a06a6942f",
        environment_key="CONNECTMD_SEARCH_PROJECTION_MEILI_KEY",
        actions=(
            "documents.add",
            "documents.get",
            "documents.delete",
            "tasks.get",
            "indexes.get",
        ),
    ),
    "erasure": KeyContract(
        uid="90501f56-2c42-47e9-8dce-68f7ce9a26d2",
        environment_key="CONNECTMD_ACCOUNT_ERASURE_MEILI_KEY",
        actions=("documents.get", "documents.delete", "tasks.get", "indexes.get"),
    ),
}


async def create_key(settings: BootstrapSettings, purpose: str) -> int:
    contract = KEY_CONTRACTS[purpose]
    base = str(settings.meilisearch_url).rstrip("/")
    headers = {"Authorization": f"Bearer {settings.meilisearch_api_key}"}
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            existing = await client.get(f"{base}/keys/{contract.uid}", headers=headers)
            if existing.status_code != 404:
                existing.raise_for_status()
                print(
                    "The scoped key already exists; use the securely stored value or "
                    "delete that exact key UID before deliberate reprovisioning.",
                    file=sys.stderr,
                )
                return 1
            created = await client.post(
                f"{base}/keys",
                headers=headers,
                json={
                    "uid": contract.uid,
                    "name": f"connect.md {purpose} runtime",
                    "description": "Index-scoped connect.md runtime authority",
                    "actions": list(contract.actions),
                    "indexes": [settings.meilisearch_index],
                    "expiresAt": None,
                },
            )
            created.raise_for_status()
            payload = created.json()
    except (httpx.HTTPError, ValueError):
        print("Meilisearch scoped-key provisioning failed", file=sys.stderr)
        return 2
    key = payload.get("key") if isinstance(payload, dict) else None
    if not isinstance(key, str) or len(key) < 16:
        print("Meilisearch did not return the one-time scoped key", file=sys.stderr)
        return 2
    print(f"{contract.environment_key}={key}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.search_key_bootstrap")
    parser.add_argument("purpose", choices=tuple(KEY_CONTRACTS))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        settings = BootstrapSettings()  # type: ignore[call-arg]
        result = asyncio.run(create_key(settings, args.purpose))
    except Exception:
        print("Meilisearch scoped-key bootstrap configuration failed", file=sys.stderr)
        result = 2
    raise SystemExit(result)


if __name__ == "__main__":
    main()
