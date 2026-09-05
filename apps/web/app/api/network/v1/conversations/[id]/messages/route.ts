import { ConversationError, listMessages, sendMessage } from "@/lib/network/conversations";
import { jsonResponse, currentSession, readBoundedJson, withNetworkUnavailable } from "@/lib/network/http";
import { database } from "@/lib/network/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UUID_PATTERN = /^[0-9a-f-]{36}$/i;

export async function GET(_request: Request, context: { params: Promise<{ id: string }> }): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const session = await currentSession();
    if (session === null) return jsonResponse({ ok: false, reason: "unauthenticated" }, 401);
    const { id } = await context.params;
    if (!UUID_PATTERN.test(id)) return jsonResponse({ ok: false, reason: "request-invalid" }, 400);
    try {
      const result = await listMessages(database(), session.account.id, id);
      return jsonResponse({ ok: true, ...result });
    } catch (error) {
      if (error instanceof ConversationError) {
        const status = error.code === "not-found" ? 404 : error.code === "forbidden" ? 403 : 400;
        return jsonResponse({ ok: false, reason: error.code, message: error.message }, status);
      }
      throw error;
    }
  });
}

export async function POST(request: Request, context: { params: Promise<{ id: string }> }): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const session = await currentSession();
    if (session === null) return jsonResponse({ ok: false, reason: "unauthenticated" }, 401);
    const { id } = await context.params;
    if (!UUID_PATTERN.test(id)) return jsonResponse({ ok: false, reason: "request-invalid" }, 400);
    const body = await readBoundedJson(request);
    if (body === null) return jsonResponse({ ok: false, reason: "request-body-invalid" }, 400);
    try {
      const message = await sendMessage(database(), session.account.id, id, body.body);
      return jsonResponse({ ok: true, message }, 201);
    } catch (error) {
      if (error instanceof ConversationError) {
        const status = error.code === "not-found" ? 404 : error.code === "blocked" ? 403 : error.code === "rate-limited" ? 429 : 400;
        return jsonResponse({ ok: false, reason: error.code, message: error.message }, status);
      }
      throw error;
    }
  });
}
