import { ProfileError, setProfileVisibility } from "@/lib/network/profiles";
import { rejectCrossOrigin, jsonResponse, currentSession, withNetworkUnavailable } from "@/lib/network/http";
import { database } from "@/lib/network/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  const originError = rejectCrossOrigin(request);
  if (originError !== null) return originError;
  return withNetworkUnavailable(async () => {
    const session = await currentSession();
    if (session === null) return jsonResponse({ ok: false, reason: "unauthenticated" }, 401);
    try {
      const profile = await setProfileVisibility(database(), session.account, "public", request.headers.get("if-match"));
      return jsonResponse({ ok: true, visibility: profile.visibility, publishedAt: profile.publishedAt }, 200);
    } catch (error) {
      if (error instanceof ProfileError) {
        return jsonResponse({ ok: false, reason: error.code, message: error.message }, error.code === "precondition" ? 412 : 404);
      }
      throw error;
    }
  });
}
