import { listPublishedProfiles } from "@/lib/network/profiles";
import { jsonResponse, withNetworkUnavailable } from "@/lib/network/http";
import { database } from "@/lib/network/db";
import { normalizeHandlePrefix } from "@/lib/network/identity";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  return withNetworkUnavailable(async () => {
    const raw = new URL(request.url).searchParams.get("prefix");
    let prefix: string | undefined;
    if (raw !== null && raw.trim() !== "") {
      const normalized = normalizeHandlePrefix(raw);
      if (normalized === null) return jsonResponse({ ok: false, reason: "request-invalid" }, 400);
      prefix = normalized;
    }
    const profiles = await listPublishedProfiles(database(), { prefix });
    return jsonResponse(
      { ok: true, profiles: profiles.map((profile) => ({ handle: profile.handle, publishedAt: profile.publishedAt })) },
      200,
      { "x-connectmd-network": "v1" },
    );
  });
}
