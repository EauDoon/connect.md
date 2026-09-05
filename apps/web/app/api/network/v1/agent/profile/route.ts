import { ProfileError, getProfile, saveProfile } from "@/lib/network/profiles";
import { resolveAgentToken, type ResolvedAgent } from "@/lib/network/agent-service";
import { scopeAllows } from "@/lib/network/agent-grants";
import { jsonResponse, readBoundedJson, withNetworkUnavailable } from "@/lib/network/http";
import { database } from "@/lib/network/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Agent API: read the owning account's profile (including private state). */
export async function GET(request: Request): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const agent = await resolveAgentToken(database(), request.headers.get("authorization"));
    if (agent === null) {
      return jsonResponse({ ok: false, reason: "agent-unauthorized" }, 401, { "www-authenticate": "Bearer" });
    }
    if (!scopeAllows(agent, "profile:read")) {
      return jsonResponse({ ok: false, reason: "scope-denied", required: "profile:read" }, 403);
    }
    try {
      const profile = await getProfile(database(), agent.accountId);
      return jsonResponse({
        ok: true,
        account: { handle: agent.accountHandle },
        profile: profile === null ? null : {
          markdown: profile.markdown,
          etag: profile.etag,
          visibility: profile.visibility,
          updatedAt: profile.updatedAt,
        },
      });
    } catch (error) {
      if (error instanceof ProfileError && error.code === "not-found") {
        return jsonResponse({ ok: true, account: { handle: agent.accountHandle }, profile: null });
      }
      throw error;
    }
  });
}

export async function PUT(request: Request): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const agent = await resolveAgentToken(database(), request.headers.get("authorization"));
    if (agent === null) {
      return jsonResponse({ ok: false, reason: "agent-unauthorized" }, 401, { "www-authenticate": "Bearer" });
    }
    if (!scopeAllows(agent, "profile:write")) {
      return jsonResponse({ ok: false, reason: "scope-denied", required: "profile:write" }, 403);
    }
    const body = await readBoundedJson(request);
    if (body === null) return jsonResponse({ ok: false, reason: "request-body-invalid" }, 400);
    try {
      const profile = await saveProfile(database(), agentAccountStub(agent), body.markdown, request.headers.get("if-match"));
      return jsonResponse({ ok: true, etag: profile.etag, updatedAt: profile.updatedAt }, 200);
    } catch (error) {
      if (error instanceof ProfileError) {
        const status = error.code === "invalid" ? 400 : error.code === "precondition" ? 412 : 400;
        return jsonResponse({ ok: false, reason: error.code, message: error.message }, status);
      }
      throw error;
    }
  });
}

function agentAccountStub(agent: ResolvedAgent) {
  return { id: agent.accountId, email: "", handle: agent.accountHandle, status: "active", created_at: new Date().toISOString() } as const;
}
