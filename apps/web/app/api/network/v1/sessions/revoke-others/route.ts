import { revokeOtherSessions } from "@/lib/network/auth-service";
import { database } from "@/lib/network/db";
import { currentSession, jsonResponse, rejectCrossOrigin, withNetworkUnavailable } from "@/lib/network/http";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Sign out every other browser of the signed-in account. This browser stays signed in. */
export async function POST(request: Request): Promise<Response> {
  const originError = rejectCrossOrigin(request);
  if (originError !== null) return originError;
  return withNetworkUnavailable(async () => {
    const session = await currentSession();
    if (session === null) return jsonResponse({ ok: false, reason: "unauthenticated" }, 401);
    const revoked = await revokeOtherSessions(database(), session.account.id, session.sessionId);
    return jsonResponse({ ok: true, revoked }, 200);
  });
}
