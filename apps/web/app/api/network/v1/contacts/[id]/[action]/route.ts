import { ContactError, decideContactRequest } from "@/lib/network/contacts";
import { jsonResponse, currentSession, withNetworkUnavailable } from "@/lib/network/http";
import { database } from "@/lib/network/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ACTIONS = new Set(["accept", "reject", "revoke", "block"]);

export async function POST(_request: Request, context: { params: Promise<{ id: string; action: string }> }): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const session = await currentSession();
    if (session === null) return jsonResponse({ ok: false, reason: "unauthenticated" }, 401);
    const { id, action } = await context.params;
    if (!ACTIONS.has(action) || !/^[0-9a-f-]{36}$/i.test(id)) {
      return jsonResponse({ ok: false, reason: "request-invalid" }, 400);
    }
    try {
      const request = await decideContactRequest(database(), session.account.id, id, action as "accept" | "reject" | "revoke" | "block");
      return jsonResponse({ ok: true, request }, 200);
    } catch (error) {
      if (error instanceof ContactError) {
        const status = error.code === "not-found" ? 404 : error.code === "forbidden" ? 403 : error.code === "blocked" ? 403 : error.code === "conflict" ? 409 : 400;
        return jsonResponse({ ok: false, reason: error.code, message: error.message }, status);
      }
      throw error;
    }
  });
}
