import { ProfileError, getProfile, saveProfile } from "@/lib/network/profiles";
import { rejectCrossOrigin, MAX_PROFILE_JSON_BYTES, jsonResponse, currentSession, readBoundedJson, withNetworkUnavailable } from "@/lib/network/http";
import { database } from "@/lib/network/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const session = await currentSession();
    if (session === null) return jsonResponse({ ok: false, reason: "unauthenticated" }, 401);
    const profile = await getProfile(database(), session.account.id);
    if (profile === null) return jsonResponse({ ok: true, profile: null }, 200);
    return jsonResponse({
      ok: true,
      profile: {
        markdown: profile.markdown,
        etag: profile.etag,
        visibility: profile.visibility,
        publishedAt: profile.publishedAt,
        updatedAt: profile.updatedAt,
      },
    });
  });
}

export async function PUT(request: Request): Promise<Response> {
  const originError = rejectCrossOrigin(request);
  if (originError !== null) return originError;
  return withNetworkUnavailable(async () => {
    const session = await currentSession();
    if (session === null) return jsonResponse({ ok: false, reason: "unauthenticated" }, 401);
    const body = await readBoundedJson(request, ["markdown"], MAX_PROFILE_JSON_BYTES);
    if (body === null) return jsonResponse({ ok: false, reason: "request-body-invalid" }, 400);
    const ifMatch = request.headers.get("if-match");
    try {
      const profile = await saveProfile(database(), session.account, body.markdown, ifMatch);
      return jsonResponse(
        { ok: true, profile },
        200,
      );
    } catch (error) {
      if (error instanceof ProfileError) {
        const status = error.code === "precondition" ? 412 : error.code === "conflict" ? 409 : 400;
        return jsonResponse({ ok: false, reason: error.code, message: error.message }, status);
      }
      throw error;
    }
  });
}
