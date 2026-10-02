import { rejectCrossOrigin, jsonResponse, clearSessionCookie, currentSession, revokeCurrentSession, withNetworkUnavailable } from "@/lib/network/http";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  const originError = rejectCrossOrigin(request);
  if (originError !== null) return originError;
  return withNetworkUnavailable(async () => {
    const session = await currentSession();
    if (session !== null) {
      await revokeCurrentSession(session.sessionId);
    }
    await clearSessionCookie();
    return jsonResponse({ ok: true }, 200);
  });
}
