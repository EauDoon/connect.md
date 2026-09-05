import { ContactError, listBlocks, listContactRequests, sendContactRequest } from "@/lib/network/contacts";
import { jsonResponse, currentSession, readBoundedJson, withNetworkUnavailable } from "@/lib/network/http";
import { database } from "@/lib/network/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const session = await currentSession();
    if (session === null) return jsonResponse({ ok: false, reason: "unauthenticated" }, 401);
    const sql = database();
    const [requests, blocked] = await Promise.all([
      listContactRequests(sql, session.account.id),
      listBlocks(sql, session.account.id),
    ]);
    return jsonResponse({ ok: true, ...requests, blockedHandles: blocked });
  });
}

export async function POST(request: Request): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const session = await currentSession();
    if (session === null) return jsonResponse({ ok: false, reason: "unauthenticated" }, 401);
    const body = await readBoundedJson(request);
    if (body === null || typeof body.handle !== "string") {
      return jsonResponse({ ok: false, reason: "request-body-invalid" }, 400);
    }
    try {
      const request = await sendContactRequest(database(), session.account.id, body.handle);
      return jsonResponse({ ok: true, request }, 201);
    } catch (error) {
      if (error instanceof ContactError) {
        const status = error.code === "rate-limited" ? 429 : error.code === "blocked" ? 403 : error.code === "not-found" ? 404 : error.code === "conflict" ? 409 : 400;
        return jsonResponse({ ok: false, reason: error.code, message: error.message }, status);
      }
      throw error;
    }
  });
}
