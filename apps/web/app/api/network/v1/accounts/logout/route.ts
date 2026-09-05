import { jsonResponse, clearSessionCookie, currentSession, revokeCurrentSession, withNetworkUnavailable } from "@/lib/network/http";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const session = await currentSession();
    if (session !== null) {
      await revokeCurrentSession(session.sessionId);
    }
    await clearSessionCookie();
    return jsonResponse({ ok: true }, 200);
  });
}
