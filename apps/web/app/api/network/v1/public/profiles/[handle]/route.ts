import { ProfileError, getPublishedProfile } from "@/lib/network/profiles";
import { jsonResponse, withNetworkUnavailable } from "@/lib/network/http";
import { database } from "@/lib/network/db";
import { normalizeHandleLookup } from "@/lib/network/identity";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Decode a route param that Next may or may not have decoded already. The
 * handle alphabet has no "%", so a second decode of a valid handle is a no-op
 * and anything undecodable is simply not a handle.
 */
function decodeHandleParam(raw: string): string | null {
  try {
    return decodeURIComponent(raw);
  } catch {
    return null;
  }
}

export async function GET(_request: Request, context: { params: Promise<{ handle: string }> }): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const { handle: raw } = await context.params;
    const decoded = decodeHandleParam(raw);
    const handle = decoded === null ? null : normalizeHandleLookup(decoded);
    if (handle === null) return jsonResponse({ ok: false, reason: "not-found" }, 404);
    try {
      const profile = await getPublishedProfile(database(), handle);
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
