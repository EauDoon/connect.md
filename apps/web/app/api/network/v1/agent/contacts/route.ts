import { listContactRequests } from "@/lib/network/contacts";
import { resolveAgentToken } from "@/lib/network/agent-service";
import { scopeAllows } from "@/lib/network/agent-grants";
import { jsonResponse, withNetworkUnavailable } from "@/lib/network/http";
import { database } from "@/lib/network/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Agent API: read the owning account's contact-request state (contacts:read). */
export async function GET(request: Request): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const agent = await resolveAgentToken(database(), request.headers.get("authorization"));
    if (agent === null) {
      return jsonResponse({ ok: false, reason: "agent-unauthorized" }, 401, { "www-authenticate": "Bearer" });
    }
    if (!scopeAllows(agent, "contacts:read")) {
      return jsonResponse({ ok: false, reason: "scope-denied", required: "contacts:read" }, 403);
    }
    const requests = await listContactRequests(database(), agent.accountId);
    return jsonResponse({ ok: true, account: { handle: agent.accountHandle }, ...requests });
  });
}
