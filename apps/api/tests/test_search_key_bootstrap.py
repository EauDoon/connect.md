from __future__ import annotations

from app.search_key_bootstrap import KEY_CONTRACTS, BootstrapSettings, create_key


class Response:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self.payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP status {self.status_code}")

    def json(self) -> dict:
        return self.payload


class Client:
    calls: list[tuple[str, str, dict | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def get(self, url: str, **kwargs) -> Response:
        self.calls.append(("GET", url, None))
        return Response(404)

    async def post(self, url: str, **kwargs) -> Response:
        self.calls.append(("POST", url, kwargs["json"]))
        return Response(201, {"key": "restricted-runtime-key-value"})


async def test_bootstrap_keys_are_exact_index_scoped_and_non_admin(monkeypatch, capsys) -> None:
    from app import search_key_bootstrap as bootstrap

    client = Client()
    monkeypatch.setattr(bootstrap.httpx, "AsyncClient", lambda **_: client)
    settings = BootstrapSettings(
        meilisearch_url="http://meilisearch:7700",
        meilisearch_api_key="master-key-for-test",
        meilisearch_index="documents",
    )

    for purpose, contract in KEY_CONTRACTS.items():
        client.calls.clear()
        assert await create_key(settings, purpose) == 0
        body = client.calls[-1][2]
        assert body is not None
        assert body["indexes"] == ["documents"]
        assert body["actions"] == list(contract.actions)
        assert not any(
            action.startswith(("keys.", "settings.", "indexes.create", "indexes.delete"))
            for action in body["actions"]
        )
    output = capsys.readouterr().out
    assert "CONNECTMD_MEILISEARCH_SEARCH_KEY=restricted-runtime-key-value" in output
    assert "CONNECTMD_SEARCH_PROJECTION_MEILI_KEY=restricted-runtime-key-value" in output
    assert "CONNECTMD_ACCOUNT_ERASURE_MEILI_KEY=restricted-runtime-key-value" in output
