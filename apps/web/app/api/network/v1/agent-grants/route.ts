import { AgentGrantError, createAgentGrant, listAgentGrants } from "@/lib/network/agent-service";
import { jsonResponse, currentSession, readBoundedJson, withNetworkUnavailable } from "@/lib/network/http";
import { database } from "@/lib/network/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const session = await currentSession();
    if (session === null) return jsonResponse({ ok: false, reason: "unauthenticated" }, 401);
    const grants = await listAgentGrants(database(), session.account.id);
    // Token values are never returned after creation; only display prefixes.
    return jsonResponse({ ok: true, grants: grants.map(({ id, name, tokenPrefix, scopes, createdAt, expiresAt, revokedAt, lastUsedAt }) => ({ id, name, tokenPrefix, scopes, createdAt, expiresAt, revokedAt, lastUsedAt })) });
  });
}

export async function POST(request: Request): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const session = await currentSession();
    if (session === null) return jsonResponse({ ok: false, reason: "unauthenticated" }, 401);
    const body = await readBoundedJson(request);
    if (body === null) return jsonResponse({ ok: false, reason: "request-body-invalid" }, 400);
    try {
      const { record, token } = await createAgentGrant(database(), session.account.id, {
        name: body.name,
        scopes: body.scopes,
        expiresAt: body.expiresAt,
      });
      return jsonResponse({ ok: true, grant: record, token }, 201);
    } catch (error) {
      if (error instanceof AgentGrantError) {
        const status = error.code === "rate-limited" ? 429 : error.code === "conflict" ? 409 : 400;
        return jsonResponse({ ok: false, reason: error.code, message: error.message }, status);
      }
      throw error;
    }
  });
}
