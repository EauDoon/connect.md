import { AgentGrantError, revokeAgentGrant } from "@/lib/network/agent-service";
import { jsonResponse, currentSession, withNetworkUnavailable } from "@/lib/network/http";
import { database } from "@/lib/network/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function DELETE(_request: Request, context: { params: Promise<{ id: string }> }): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const session = await currentSession();
    if (session === null) return jsonResponse({ ok: false, reason: "unauthenticated" }, 401);
    const { id } = await context.params;
    if (!/^[0-9a-f-]{36}$/i.test(id)) return jsonResponse({ ok: false, reason: "request-invalid" }, 400);
    try {
      await revokeAgentGrant(database(), session.account.id, id);
      return jsonResponse({ ok: true }, 200);
    } catch (error) {
      if (error instanceof AgentGrantError) {
        return jsonResponse({ ok: false, reason: error.code }, error.code === "not-found" ? 404 : 400);
      }
      throw error;
    }
  });
}
