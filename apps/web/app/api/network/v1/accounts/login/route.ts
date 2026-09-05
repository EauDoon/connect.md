import { AccountActionError, clientKeyFromHeaders, loginAccount } from "@/lib/network/auth-service";
import { database } from "@/lib/network/db";
import { accountErrorStatus, jsonResponse, readBoundedJson, setSessionCookie, withNetworkUnavailable } from "@/lib/network/http";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const body = await readBoundedJson(request);
    if (body === null) {
      return jsonResponse({ ok: false, reason: "request-body-invalid" }, 400);
    }
    try {
      const { account, sessionToken } = await loginAccount(database(), {
        email: body.email,
        password: body.password,
        ipKey: clientKeyFromHeaders(request.headers),
      });
      await setSessionCookie(sessionToken);
      return jsonResponse({ ok: true, account: { handle: account.handle, email: account.email } }, 200);
    } catch (error) {
      if (error instanceof AccountActionError) {
        return jsonResponse({ ok: false, reason: error.code, message: error.message }, accountErrorStatus(error));
      }
      throw error;
    }
  });
}
