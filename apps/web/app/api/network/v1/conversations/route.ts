import { listConversations } from "@/lib/network/conversations";
import { jsonResponse, currentSession, withNetworkUnavailable } from "@/lib/network/http";
import { database } from "@/lib/network/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const session = await currentSession();
    if (session === null) return jsonResponse({ ok: false, reason: "unauthenticated" }, 401);
    const conversations = await listConversations(database(), session.account.id);
    return jsonResponse({ ok: true, conversations });
  });
}
