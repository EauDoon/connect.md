import { ProfileError, getPublishedProfile } from "@/lib/network/profiles";
import { jsonResponse, withNetworkUnavailable } from "@/lib/network/http";
import { database } from "@/lib/network/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(_request: Request, context: { params: Promise<{ handle: string }> }): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const { handle } = await context.params;
    try {
      const profile = await getPublishedProfile(database(), decodeURIComponent(handle));
      return jsonResponse(
        { ok: true, profile: { handle: profile.handle, markdown: profile.markdown, publishedAt: profile.publishedAt, etag: profile.etag } },
        200,
        { etag: profile.etag },
      );
    } catch (error) {
      if (error instanceof ProfileError) {
        return jsonResponse({ ok: false, reason: "not-found" }, 404);
      }
      throw error;
    }
  });
}
