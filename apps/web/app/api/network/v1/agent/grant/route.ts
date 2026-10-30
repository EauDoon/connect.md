import { resolveAgentToken } from "@/lib/network/agent-service";
import { jsonResponse, withNetworkUnavailable } from "@/lib/network/http";
import { database } from "@/lib/network/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Agent API: describe the grant behind the bearer token, so an agent can learn
 * its scopes and expiry before acting. Any live grant may call it; no scope is
 * required. Token material (the token, its prefix, or its digest) is never
 * returned.
 */
export async function GET(request: Request): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const agent = await resolveAgentToken(database(), request.headers.get("authorization"));
    if (agent === null) {
      return jsonResponse({ ok: false, reason: "agent-unauthorized" }, 401, { "www-authenticate": "Bearer" });
    }
    return jsonResponse({
      ok: true,
      account: { handle: agent.accountHandle },
      grant: { name: agent.name, scopes: agent.scopes, createdAt: agent.createdAt, expiresAt: agent.expiresAt },
    });
  });
}
