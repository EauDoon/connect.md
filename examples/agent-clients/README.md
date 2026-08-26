# connect.md agent integration starter kit

This is a dependency-free Python 3.12 starter kit for agents integrating with
the current local connect.md HTTP, Markdown, MCP, A2A, search, taxonomy, and
consent-gated outreach surfaces. It is an example/conformance lane, not a
second API contract and not a production-readiness claim.

The route and schema assertions are intentionally strict. They are grounded in
the current local bytes of `apps/api/app/main.py`,
`docs/agent-interoperability.md`, and the discovery documents served by the
hermetic fake. Re-run the checker after API discovery or schema changes.

## Safe default

The default checks never contact a live host. They start a `127.0.0.1` fake on
an ephemeral port, exercise it, and shut it down deterministically. The fake
records only method, path, bounded header metadata, body length, and a body
SHA-256; request records never log or retain raw credentials or request bodies.
The fake retains only the current canonical Markdown and bounded replay
response bytes needed to test read-after-write and idempotency behavior.

The client uses only Python standard-library modules. No package installation,
Docker, process management, secrets, remote deployment, or existing
Hostinger/Hermes access is required or performed. The client does not follow
HTTP redirects, so a Bearer credential is not forwarded to another origin.

Run from this directory or the repository root:

```text
python examples/agent-clients/check_agent_client.py
python -m unittest discover -s examples/agent-clients -p "test_*.py" -v
python -m py_compile examples/agent-clients/agent_client.py examples/agent-clients/fake_server.py examples/agent-clients/test_agent_client.py examples/agent-clients/check_agent_client.py
```

## Discovery-first flow

Begin with these current discovery resources:

```text
GET /llms.txt
GET /llms-full.txt
GET /agent-readme.md
GET /openapi.json
GET /v1/capabilities
GET /.well-known/oauth-protected-resource
GET /.well-known/agent-card.json
```

`AgentClient.discover()` fetches and validates all seven. It treats
`/agent-readme.md` as bounded `text/markdown`, requires its exact internal
discovery links and authority boundaries, and never treats README prose as
permission to act. It also requires the
current OpenAPI operations for Markdown reads, conditional writes, search,
taxonomy terms, and outreach; the current Bearer security schemes; the
`Idempotency-Key` and exact strong `If-Match` parameters; the capabilities
protocol declarations; and the A2A Agent Card's HTTP+JSON 1.0 interface and
seven current skill IDs. A missing or contradictory discovery fact fails
closed.

## Authentication without embedded credentials

Current connect.md uses an `Authorization: Bearer <runtime credential>`
header. The runtime credential may be a Clerk JWT, a legacy `cnd_` owner API
key, or a scoped `cng_` Agent Grant, subject to the server's authority and
scope checks. This kit accepts a credential only from the caller and redacts
it when producing safe header metadata:

```python
from agent_client import AgentClient, authorization_headers, redact_headers

read_client = AgentClient.live("https://connect.example.invalid", token=None)
headers = authorization_headers(runtime_token)
safe_headers = redact_headers(headers)  # Authorization becomes <redacted>
```

No credential is read from the environment, persisted to disk or a fixture,
printed, or placed in a request body. The caller-owned live client holds the
runtime value in memory only to construct Authorization headers; the fake
accepts any Bearer-shaped placeholder for transport testing and does not prove
real Clerk, API-key, grant, scope, mandate, or consent authority. A live client
is read-only by default. The kit never executes live mode; an operator must
explicitly construct it and separately choose any write authority in their own
program.

## Canonical Markdown reads and writes

Public canonical reads use `Accept: text/markdown` on the explicit `.md`
routes. The returned content type must be `text/markdown`, and the bytes are
returned without Markdown execution or transformation:

```python
document = read_client.get_markdown("profile", "ada-lovelace")
body = document.body  # application code decides how to handle untrusted bytes
```

The current write contract is deliberately narrow:

- `Idempotency-Key` is visible ASCII, 1-128 characters, and is reused only
  for the identical logical request.
- A document update requires exactly one strong current ETag matching
  `"sha256-<64 lowercase hex digits>"` in `If-Match`.
- `*`, weak validators such as `W/"..."`, comma-separated validators, and
  malformed values are rejected before transport.
- Canonical Markdown is bounded at 131,072 UTF-8 bytes by this client, while
  the server remains authoritative for the final rendered contract.
- A write is retried once only when the connection terminates after the request
  may have been accepted (`LostAcknowledgement`). Generic DNS, refused-
  connection, and timeout failures are not retried. The retry repeats the
  exact method, path, body, `If-Match`, and `Idempotency-Key`. HTTP 412 is
  returned as a stale-precondition failure and is never blindly retried.

```python
writer = AgentClient.live(
    "https://connect.example.invalid", token=runtime_token, read_only=False
)
updated = writer.update_document(
    "profile",
    "ada-lovelace",
    candidate_markdown,
    if_match=document.etag,
    idempotency_key="profile-update-001",
)
```

The fake commits a successful update and closes the connection before the
response when configured for lost-ack testing. The second identical request
receives the safe replay response; the fake's request records prove that the
body digest, validator, and key did not change.

## Search and taxonomy

Use the canonical `q` field. The structured POST client rejects the deprecated
`query` alias and rejects simultaneous `q` and `query`; public protocol
surfaces may accept that alias only under the server's documented compatibility
rules. Inputs are bounded before transport, including list lengths, canonical
ID lengths, query length, cursor length, pagination, and the literal discovery
filter `agent_capability=internal_contact_request`.

```python
search = read_client.search_get({"q": "payments", "limit": 20})
structured = read_client.search_query({"q": "payments", "skill_ids": ["scheme:id"]})
catalog = read_client.list_taxonomies()
terms = read_client.list_taxonomy_terms("skill", q="payments", limit=20)
```

Taxonomy values are discovery-only. A search hit or public Agent Identity
reference never grants contact, mandate, consent, or write authority.

## MCP and A2A

MCP is the current stateless JSON-RPC endpoint with protocol version
`2025-06-18`:

```python
read_client.mcp_initialize()
tools = read_client.mcp_tools_list()
result = read_client.mcp_call("search_documents", {"q": "payments", "limit": 20})
```

`tools/list` is checked as a complete current inventory, including every
required field, `additionalProperties: false`, annotations, and semantic
property bounds. MCP and A2A helpers validate each action's fields and limits
before transport, including pagination, cursor, Markdown, outreach, and
search aggregate-value bounds. A malformed schema or action is rejected
fail-closed rather than being treated as a name-only conformance match.

The A2A helper sends one structured JSON data part to the current
`POST /a2a/message:send` operation with `A2A-Version: 1.0`. The current card
advertises only `search`, `list_taxonomies`, `list_taxonomy_terms`,
`get_agent_identity`, `list_agent_directory`, `list_profile_agents`,
`contact_request`, `agent_outreach`, and `get_agent_outreach_status`; the
client rejects other actions. Public search and taxonomy discovery are
anonymous; consent-gated contact actions require the
documented `Idempotency-Key` header and an eligible runtime credential:

```python
a2a_result = read_client.a2a_send("search", {"q": "payments"})
outreach = writer.a2a_send(
    "agent_outreach",
    {"target_agent_handle": "target-agent", "purpose": "Introduction", "message": "Hello."},
    idempotency_key="outreach-001",
)
status = writer.a2a_send(
    "get_agent_outreach_status",
    {"request_id": "11111111-1111-4111-8111-111111111111"},
)
```

The corresponding bounded HTTP helpers are
`send_agent_outreach(...)` and `get_agent_outreach_status(...)`. They do not
call arbitrary external agent URLs. The status projection is deliberately
privacy-minimal and excludes message text, grant/mandate identifiers, private
recipient state, and decision narratives.

## Files

- `agent_client.py` — bounded stdlib HTTP client, seven-resource discovery and
  README parity checks,
  Markdown transport, exact lost-ack retry, search/taxonomy, MCP, A2A, and
  outreach helpers.
- `fake_server.py` — deterministic loopback-only fixture with discovery
  documents, protocol responses, conditional writes, idempotency, and failure
  counterexamples.
- `test_agent_client.py` — hermetic `unittest` coverage.
- `check_agent_client.py` — static safety/current-byte parity gate plus the
  hermetic suite and compile checks.

This kit intentionally does not import the application, mutate the API,
change schemas, install a transport dependency, or publish a route that is
not proven by the current discovery/source bytes.
