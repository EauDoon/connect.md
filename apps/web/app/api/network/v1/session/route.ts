import { jsonResponse, currentSession, withNetworkUnavailable } from "@/lib/network/http";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const session = await currentSession();
    if (session === null) {
      return jsonResponse({ ok: true, account: null }, 200);
    }
    return jsonResponse(
      { ok: true, account: { handle: session.account.handle, email: session.account.email } },
      200,
    );
  });
}
