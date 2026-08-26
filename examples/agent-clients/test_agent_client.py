from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

KIT_DIR = Path(__file__).resolve().parent
if str(KIT_DIR) not in sys.path:
    sys.path.insert(0, str(KIT_DIR))

from agent_client import (  # noqa: E402
    MAX_A2A_MESSAGE_BYTES,
    MAX_MCP_ENVELOPE_BYTES,
    AgentClient,
    DiscoveryError,
    HttpResponse,
    HttpStatusError,
    InputBoundError,
    LiveWritesDisabled,
    LostAcknowledgement,
    ProtocolError,
    TransportError,
    _bounded_json,
    authorization_headers,
    current_byte_parity_errors,
    redact_headers,
)
from fake_server import (  # noqa: E402
    FIXED_OUTREACH_ID,
    FakeConnectServer,
    _fixture_document_response,
)

TEST_TOKEN = "CLERK_JWT_PLACEHOLDER"


class AgentClientHermeticTests(unittest.TestCase):
    def test_discovery_requires_current_routes_schemas_and_protocols(self) -> None:
        with FakeConnectServer() as server:
            client = AgentClient(server.base_url)
            snapshot = client.discover()
            self.assertTrue(
                snapshot.agent_readme.startswith("# connect.md agent onboarding README")
            )
            self.assertEqual(snapshot.capabilities["protocols"]["mcp"], "/mcp")
            self.assertEqual(snapshot.capabilities["protocols"]["a2a_http_json"], "/a2a")
            self.assertIn("/v1/search/query", snapshot.openapi["paths"])

    def test_discovery_rejects_invalid_agent_readme_route(self) -> None:
        for alteration in ("content_type", "missing_link"):
            with self.subTest(alteration=alteration), FakeConnectServer() as server:
                client = AgentClient(server.base_url)
                original = client.transport

                def altered(
                    method: str,
                    url: str,
                    headers: object,
                    body: bytes | None,
                    *,
                    original=original,
                    alteration=alteration,
                ):
                    response = original(method, url, headers, body)
                    if url.endswith("/agent-readme.md"):
                        if alteration == "content_type":
                            response = HttpResponse(
                                response.status,
                                {**response.headers, "content-type": "text/plain"},
                                response.body,
                            )
                        else:
                            response = HttpResponse(
                                response.status,
                                response.headers,
                                response.body.replace(b"/openapi.json", b"/missing.json"),
                            )
                    return response

                client.transport = altered
                with self.assertRaises(DiscoveryError):
                    client.discover()

    def test_discovery_rejects_missing_schema_fact(self) -> None:
        with FakeConnectServer() as server:
            client = AgentClient(server.base_url)
            original = client.transport

            def altered(method: str, url: str, headers: object, body: bytes | None):
                response = original(method, url, headers, body)
                if url.endswith("/openapi.json"):
                    payload = __import__("json").loads(response.body.decode("utf-8"))
                    del payload["paths"]["/v1/search/query"]
                    response = type(response)(
                        response.status,
                        response.headers,
                        __import__("json").dumps(payload).encode(),
                    )
                return response

            client.transport = altered
            with self.assertRaises(DiscoveryError):
                client.discover()

    def test_markdown_accept_header_and_explicit_md_read(self) -> None:
        with FakeConnectServer() as server:
            client = AgentClient(server.base_url)
            document = client.get_markdown("profile", "ada-lovelace")
            self.assertEqual(document.content_type, "text/markdown")
            self.assertIn(b"Ada Lovelace", document.body)
            request = server.state.requests[-1]
            self.assertEqual(request.path, "/v1/profiles/ada-lovelace.md")
            self.assertEqual(request.headers["accept"], "text/markdown")
            self.assertEqual(document.etag, server.state.etag)

    def test_runtime_auth_header_patterns_and_redaction(self) -> None:
        for token in (TEST_TOKEN, "cnd_...", "cng_..."):
            headers = authorization_headers(token)
            self.assertEqual(headers["Authorization"], f"Bearer {token}")
            safe = redact_headers(headers)
            self.assertEqual(safe["Authorization"], "<redacted>")
            self.assertNotIn(token, str(safe))
        with self.assertRaises(InputBoundError):
            authorization_headers("Bearer\nINJECTION")
        with self.assertRaises(InputBoundError):
            authorization_headers("x" * 4_097)

    def test_json_parser_rejects_duplicate_keys_and_excessive_depth(self) -> None:
        with self.assertRaises(ProtocolError):
            _bounded_json(b'{"a":1,"a":2}')
        with self.assertRaises(ProtocolError):
            _bounded_json(("[" * 40 + "0" + "]" * 40).encode("ascii"))

    def test_exact_strong_if_match_and_lost_ack_replay(self) -> None:
        candidate = "---\ntitle: Updated\n---\n\n# Updated\n"
        with FakeConnectServer() as server:
            client = AgentClient(server.base_url, token=TEST_TOKEN, read_only=False)
            current = client.get_markdown("profile", "ada-lovelace")
            self.assertIsNotNone(current.etag)
            server.state.lost_ack_once = True
            result = client.update_document(
                "profile",
                "ada-lovelace",
                candidate,
                if_match=current.etag or "",
                idempotency_key="update-lost-ack-001",
            )
            self.assertEqual(result["version"], 2)
            put_requests = [request for request in server.state.requests if request.method == "PUT"]
            self.assertEqual(len(put_requests), 2)
            self.assertEqual(put_requests[0].path, put_requests[1].path)
            self.assertEqual(put_requests[0].body_sha256, put_requests[1].body_sha256)
            self.assertEqual(
                put_requests[0].headers["if-match"], put_requests[1].headers["if-match"]
            )
            self.assertEqual(
                put_requests[0].headers["idempotency-key"],
                put_requests[1].headers["idempotency-key"],
            )
            self.assertEqual(server.state.markdown, candidate.encode("utf-8"))

    def test_wildcard_weak_and_comma_etags_are_rejected_before_transport(self) -> None:
        with FakeConnectServer() as server:
            client = AgentClient(server.base_url, read_only=False)
            for invalid in (
                "*",
                'W/"sha256-' + "0" * 64 + '"',
                '"sha256-' + "0" * 64 + '","other"',
            ):
                before = len(server.state.requests)
                with self.assertRaises(InputBoundError):
                    client.update_document(
                        "profile",
                        "ada-lovelace",
                        "candidate",
                        if_match=invalid,
                        idempotency_key="etag-negative-001",
                    )
                self.assertEqual(len(server.state.requests), before)

    def test_stale_if_match_is_not_retried(self) -> None:
        with FakeConnectServer() as server:
            client = AgentClient(server.base_url, read_only=False)
            stale = '"sha256-' + "0" * 64 + '"'
            with self.assertRaises(HttpStatusError) as raised:
                client.update_document(
                    "profile",
                    "ada-lovelace",
                    "candidate",
                    if_match=stale,
                    idempotency_key="stale-001",
                )
            self.assertEqual(raised.exception.status, 412)
            self.assertEqual(len([r for r in server.state.requests if r.method == "PUT"]), 1)

    def test_non_412_http_failure_is_not_retried(self) -> None:
        calls: list[tuple[str, str]] = []

        def server_error(
            method: str, url: str, _headers: object, _body: bytes | None
        ) -> HttpResponse:
            calls.append((method, url))
            return HttpResponse(500, {}, b'{"detail":"fixture failure"}')

        client = AgentClient("http://127.0.0.1:1", transport=server_error, read_only=False)
        with self.assertRaises(HttpStatusError) as raised:
            client.update_document(
                "profile",
                "ada-lovelace",
                "candidate",
                if_match='"sha256-' + "0" * 64 + '"',
                idempotency_key="http-500-001",
            )
        self.assertEqual(raised.exception.status, 500)
        self.assertEqual(calls, [("PUT", "http://127.0.0.1:1/v1/profiles/ada-lovelace")])

    def test_same_key_different_body_is_idempotency_collision(self) -> None:
        with FakeConnectServer() as server:
            client = AgentClient(server.base_url, read_only=False)
            client.create_document("profile", "first", idempotency_key="create-collision-001")
            with self.assertRaises(HttpStatusError) as raised:
                client.create_document(
                    "profile", "different", idempotency_key="create-collision-001"
                )
            self.assertEqual(raised.exception.status, 409)

    def test_search_and_taxonomy_bounds_and_canonical_q(self) -> None:
        with FakeConnectServer() as server:
            client = AgentClient(server.base_url)
            get_result = client.search_get({"q": "payments", "limit": 20})
            post_result = client.search_query({"q": "payments", "skill_ids": ["scheme:id"]})
            self.assertEqual(get_result["hits"][0]["excerpt"], "Query: payments")
            self.assertEqual(post_result["hits"][0]["excerpt"], "Query: payments")
            self.assertEqual(client.list_taxonomies()[0]["taxonomy"], "skill")
            self.assertIn("terms", client.list_taxonomy_terms("skill", q="payments"))
            with self.assertRaises(InputBoundError):
                client.search_query({"query": "deprecated"})
            with self.assertRaises(InputBoundError):
                client.search_get({"q": "x" * 81})
            with self.assertRaises(InputBoundError):
                client.search_get({"skill_ids": ["x" * 81]})
            with self.assertRaises(InputBoundError):
                client.search_get({"location_id": "x" * 81})
            with self.assertRaises(InputBoundError):
                client.search_query({"skill_ids": ["x"] * 50, "occupation_ids": ["x"]})
            self.assertEqual(client.search_query({"location_country_code": "SGP"})["total"], 1)
            with self.assertRaises(InputBoundError):
                client.list_taxonomy_terms("skill", cursor="x" * 2049)

    def test_mcp_initialize_tools_and_public_search(self) -> None:
        with FakeConnectServer() as server:
            client = AgentClient(server.base_url)
            initialized = client.mcp_initialize()
            self.assertEqual(initialized["protocolVersion"], "2025-06-18")
            tools = client.mcp_tools_list()
            names = {tool["name"] for tool in tools["tools"]}
            self.assertIn("search_documents", names)
            result = client.mcp_call("search_documents", {"q": "payments"})
            self.assertEqual(result["structuredContent"]["total"], 1)

    def test_mcp_tools_list_validates_complete_schema_not_names_only(self) -> None:
        with FakeConnectServer() as server:
            client = AgentClient(server.base_url)
            tools = client.mcp_tools_list()["tools"]
            self.assertEqual(len(tools), 14)
            self.assertTrue(
                all(tool["inputSchema"]["additionalProperties"] is False for tool in tools)
            )
            self.assertIn(
                "idempotency_key",
                next(tool for tool in tools if tool["name"] == "send_agent_outreach")[
                    "inputSchema"
                ]["required"],
            )

            original = client.transport

            def altered(method: str, url: str, headers: object, body: bytes | None):
                response = original(method, url, headers, body)
                if url.endswith("/mcp") and body and b"tools/list" in body:
                    payload = json.loads(response.body.decode("utf-8"))
                    tool = next(
                        item
                        for item in payload["result"]["tools"]
                        if item["name"] == "list_profile_agents"
                    )
                    del tool["inputSchema"]["properties"]["limit"]["maximum"]
                    response = HttpResponse(
                        response.status,
                        response.headers,
                        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
                    )
                return response

            client.transport = altered
            with self.assertRaises(ProtocolError):
                client.mcp_tools_list()

    def test_mcp_and_a2a_action_bounds_fail_before_transport(self) -> None:
        calls: list[str] = []

        def unexpected(
            method: str, _url: str, _headers: object, _body: bytes | None
        ) -> HttpResponse:
            calls.append(method)
            return HttpResponse(500, {}, b"")

        client = AgentClient("http://127.0.0.1:1", transport=unexpected, read_only=False)
        invalid_calls = (
            lambda: client.mcp_call("list_profile_agents", {"profile_handle": "ada", "limit": 51}),
            lambda: client.mcp_call("read_document", {"kind": "profile"}),
            lambda: client.mcp_call("get_changes", {"after_sequence": -1}),
            lambda: client.mcp_call("create_document", {"kind": "profile", "markdown": "draft"}),
            lambda: client.a2a_send("list_taxonomy_terms", {"taxonomy": "skill", "cursor": "  "}),
            lambda: client.a2a_send(
                "agent_outreach",
                {"target_agent_handle": "target", "purpose": "purpose", "message": "x" * 2_001},
                idempotency_key="a2a-bounds-001",
            ),
            lambda: client.a2a_send("search", {"skills": ["x"] * 51}),
        )
        for invalid_call in invalid_calls:
            with self.assertRaises(InputBoundError):
                invalid_call()
        self.assertEqual(calls, [])

    def test_oversized_create_and_update_return_413_without_state_change(self) -> None:
        oversized = b"x" * (1_048_576 + 1)
        for method, path, extra_headers in (
            ("POST", "/v1/profiles", {"Idempotency-Key": "oversized-create-001"}),
            (
                "PUT",
                "/v1/profiles/ada-lovelace",
                {
                    "Idempotency-Key": "oversized-update-001",
                    "If-Match": '"sha256-' + "0" * 64 + '"',
                },
            ),
        ):
            with self.subTest(method=method), FakeConnectServer() as server:
                before = (
                    server.state.markdown,
                    server.state.version,
                    dict(server.state.idempotency),
                    len(server.state.requests),
                )
                headers = {"Content-Type": "text/markdown", **extra_headers}
                request = Request(
                    server.base_url + path,
                    data=oversized,
                    headers=headers,
                    method=method,
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=5).read()
                self.assertEqual(raised.exception.code, 413)
                after = (
                    server.state.markdown,
                    server.state.version,
                    dict(server.state.idempotency),
                    len(server.state.requests),
                )
                self.assertEqual(after, before)

    def test_mcp_outreach_requires_runtime_bearer_and_returns_safe_shape(self) -> None:
        with FakeConnectServer() as server:
            anonymous = AgentClient(server.base_url, read_only=False)
            with self.assertRaises(ProtocolError):
                anonymous.mcp_call(
                    "send_agent_outreach",
                    {
                        "target_agent_handle": "target-agent",
                        "purpose": "Introduction",
                        "message": "Hello.",
                        "idempotency_key": "mcp-001",
                    },
                )
            authenticated = AgentClient(server.base_url, token=TEST_TOKEN, read_only=False)
            result = authenticated.mcp_call(
                "send_agent_outreach",
                {
                    "target_agent_handle": "target-agent",
                    "purpose": "Introduction",
                    "message": "Hello.",
                    "idempotency_key": "mcp-001",
                },
            )
            self.assertEqual(result["structuredContent"]["status"], "pending")

    def test_a2a_search_and_outreach_status_are_bounded(self) -> None:
        with FakeConnectServer() as server:
            client = AgentClient(server.base_url, token=TEST_TOKEN, read_only=False)
            search = client.a2a_send("search", {"q": "payments"})
            self.assertEqual(search["task"]["status"]["state"], "TASK_STATE_COMPLETED")
            outreach = client.a2a_send(
                "agent_outreach",
                {
                    "target_agent_handle": "target-agent",
                    "purpose": "Introduction",
                    "message": "Hello.",
                },
                idempotency_key="a2a-outreach-001",
            )
            self.assertIn("task", outreach)
            status = client.a2a_send("get_agent_outreach_status", {"request_id": FIXED_OUTREACH_ID})
            self.assertIn("task", status)
            a2a_requests = [r for r in server.state.requests if r.path == "/a2a/message:send"]
            self.assertTrue(a2a_requests)
            self.assertTrue(all(r.headers["a2a-version"] == "1.0" for r in a2a_requests))
            with self.assertRaises(InputBoundError):
                client.a2a_send("search", {"action": "agent_outreach"})

    def test_protocol_envelopes_are_bounded_before_transport(self) -> None:
        calls: list[str] = []

        def unexpected(
            method: str, _url: str, _headers: object, _body: bytes | None
        ) -> HttpResponse:
            calls.append(method)
            return HttpResponse(500, {}, b"")

        client = AgentClient("http://127.0.0.1:1", transport=unexpected)
        with self.assertRaises(InputBoundError):
            client.mcp_call("search_documents", {"blob": "x" * MAX_MCP_ENVELOPE_BYTES})
        with self.assertRaises(InputBoundError):
            client.a2a_send("search", {"blob": "x" * MAX_A2A_MESSAGE_BYTES})
        self.assertEqual(calls, [])

    def test_http_outreach_status_never_contains_message_body(self) -> None:
        with FakeConnectServer() as server:
            client = AgentClient(server.base_url, token=TEST_TOKEN, read_only=False)
            receipt = client.send_agent_outreach(
                "target-agent", "Introduction", "Private message", idempotency_key="http-001"
            )
            self.assertEqual(receipt["status"], "pending")
            replay = client.send_agent_outreach(
                "target-agent", "Introduction", "Private message", idempotency_key="http-001"
            )
            self.assertEqual(replay["id"], receipt["id"])
            with self.assertRaises(HttpStatusError) as raised:
                client.send_agent_outreach(
                    "target-agent", "Introduction", "Different message", idempotency_key="http-001"
                )
            self.assertEqual(raised.exception.status, 409)
            status = client.get_agent_outreach_status(FIXED_OUTREACH_ID)
            self.assertEqual(status["status"], "pending")
            self.assertNotIn("message", status)

    def test_no_raw_body_or_credential_logging_in_fixture_records(self) -> None:
        with FakeConnectServer() as server:
            client = AgentClient(server.base_url, token=TEST_TOKEN)
            client.get_markdown("profile", "ada-lovelace")
            record = server.state.requests[-1]
            self.assertFalse(hasattr(record, "body"))
            self.assertNotIn(TEST_TOKEN, str(record))
            self.assertEqual(len(record.body_sha256), 64)
            source = "\n".join(
                (KIT_DIR / name).read_text(encoding="utf-8")
                for name in ("agent_client.py", "fake_server.py")
            )
            self.assertNotIn("logging.", source)
            self.assertNotIn("logger.", source)
            self.assertNotRegex(source, re.compile(r"\bprint\s*\("))

    def test_fixture_records_digest_request_body_but_keep_canonical_state(self) -> None:
        candidate = "private fixture candidate"
        with FakeConnectServer() as server:
            client = AgentClient(server.base_url, token=TEST_TOKEN, read_only=False)
            current = client.get_markdown("profile", "ada-lovelace")
            client.update_document(
                "profile",
                "ada-lovelace",
                candidate,
                if_match=current.etag or "",
                idempotency_key="record-shape-001",
            )
            self.assertEqual(server.state.markdown, candidate.encode("utf-8"))
            self.assertNotIn(candidate, str(server.state.requests))
            self.assertNotIn(TEST_TOKEN, str(server.state.requests))

    def test_live_client_is_explicit_and_read_only_by_default(self) -> None:
        client = AgentClient.live("http://127.0.0.1:1")
        with self.assertRaises(LiveWritesDisabled):
            client.update_document(
                "profile",
                "ada-lovelace",
                "candidate",
                if_match='"sha256-' + "0" * 64 + '"',
                idempotency_key="live-never-run-001",
            )
        with self.assertRaises(LiveWritesDisabled):
            client.send_agent_outreach(
                "target-agent", "Introduction", "Hello", idempotency_key="live-never-run-002"
            )

    def test_current_byte_parity_checker_is_clean(self) -> None:
        self.assertEqual(current_byte_parity_errors(Path(__file__).resolve().parents[2]), [])

    def test_server_binds_loopback_ephemeral_and_shuts_down(self) -> None:
        server = FakeConnectServer()
        self.assertTrue(server.base_url.startswith("http://127.0.0.1:"))
        with server:
            self.assertTrue(server.is_running)
        self.assertFalse(server.is_running)
        with self.assertRaises(RuntimeError):
            server.__enter__()

    def test_server_close_before_start_is_deterministic(self) -> None:
        server = FakeConnectServer()
        server.close()
        self.assertFalse(server.is_running)
        with self.assertRaises(RuntimeError):
            server.__enter__()

    def test_transport_error_is_distinct_from_http_failure(self) -> None:
        calls: list[str] = []

        def no_response(method: str, _url: str, _headers: object, _body: bytes | None):
            calls.append(method)
            raise TransportError("request did not receive an HTTP response")

        client = AgentClient("http://127.0.0.1:1", transport=no_response, read_only=False)
        with self.assertRaises(TransportError):
            client.update_document(
                "profile",
                "ada-lovelace",
                "candidate",
                if_match='"sha256-' + "0" * 64 + '"',
                idempotency_key="transport-001",
            )
        self.assertEqual(calls, ["PUT"])

    def test_only_lost_acknowledgement_is_retryable(self) -> None:
        calls: list[str] = []

        def lost_ack(method: str, _url: str, _headers: object, _body: bytes | None):
            calls.append(method)
            if len(calls) == 1:
                raise LostAcknowledgement("connection ended after request")
            response_body = (
                __import__("json")
                .dumps(
                    _fixture_document_response(
                        "/v1/profiles/ada-lovelace",
                        b"candidate",
                        version=2,
                        base_url="http://127.0.0.1",
                    ),
                    separators=(",", ":"),
                )
                .encode("utf-8")
            )
            return HttpResponse(
                200,
                {},
                response_body,
            )

        client = AgentClient("http://127.0.0.1:1", transport=lost_ack, read_only=False)
        result = client.update_document(
            "profile",
            "ada-lovelace",
            "candidate",
            if_match='"sha256-' + "0" * 64 + '"',
            idempotency_key="lost-ack-class-001",
        )
        self.assertEqual(result["version"], 2)
        self.assertEqual(calls, ["PUT", "PUT"])


if __name__ == "__main__":
    unittest.main()
