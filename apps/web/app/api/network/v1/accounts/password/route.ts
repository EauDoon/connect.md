import { AccountActionError, changePassword } from "@/lib/network/auth-service";
import { database } from "@/lib/network/db";
import { accountErrorStatus, currentSession, jsonResponse, readBoundedJson, rejectCrossOrigin, setSessionCookie, withNetworkUnavailable } from "@/lib/network/http";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Change the signed-in account's password. Every session is revoked and this browser gets a new one. */
export async function POST(request: Request): Promise<Response> {
  const originError = rejectCrossOrigin(request);
  if (originError !== null) return originError;
  return withNetworkUnavailable(async () => {
    const session = await currentSession();
    if (session === null) return jsonResponse({ ok: false, reason: "unauthenticated" }, 401);
    const body = await readBoundedJson(request, ["currentPassword", "newPassword"]);
    if (body === null) return jsonResponse({ ok: false, reason: "request-body-invalid" }, 400);
    try {
      const { sessionToken, revokedSessions } = await changePassword(database(), session, {
        currentPassword: body.currentPassword,
        newPassword: body.newPassword,
      });
      await setSessionCookie(sessionToken);
      return jsonResponse({ ok: true, revokedSessions }, 200);
    } catch (error) {
      if (error instanceof AccountActionError) {
        return jsonResponse({ ok: false, reason: error.code, message: error.message }, accountErrorStatus(error));
      }
      throw error;
    }
  });
}
